# MQTT publisher for the garden logger (MicroPython ESP32). Configured from the dashboard
# (DATA_DIR/mqtt.json); the module is loaded only while MQTT is enabled there.
#
# Non-blocking: a minimal MQTT 3.1.1 client on asyncio streams (QoS 0, publish only), so a dead or
# slow broker never stalls the sampler or the pump timing (umqtt.simple uses blocking sockets).
# A host *name* is looked up once in a short-lived _thread (getaddrinfo releases the GIL on the ESP32,
# so the asyncio loop keeps running) and the IP is cached until a connection fails.
#
# Topics (<base> = topic prefix from the page, default "garden"):
#   <base>/status      "online" / "offline" (retained; "offline" is the broker's last will)
#   <base>/state       JSON with every value, every `every` seconds and right after pump events;
#                      soil and air are the AVERAGE of all 2-s samples since the previous publish
#                      (same method as the CSV recorder), 'samples' = how many
#   <base>/pump/event  pump log lines (ON / OFF / check OK / DRY RUN ...) as they happen
# What goes into <base>/state is chosen with checkboxes on the page ('fields').
try:
    import asyncio
except ImportError:
    import uasyncio as asyncio
import json
import os
import time
import settings as S

CFG = S.DATA_DIR + '/mqtt.json'
# Template from settings.py (all optional); values saved from the page (mqtt.json) override them.
DEFAULTS = {'on': getattr(S, 'MQTT_ENABLED', False), 'host': getattr(S, 'MQTT_HOST', ''),
            'port': getattr(S, 'MQTT_PORT', 1883), 'user': getattr(S, 'MQTT_USER', ''),
            'pw': getattr(S, 'MQTT_PASSWORD', ''), 'cid': getattr(S, 'MQTT_CLIENT_ID', ''),
            'base': getattr(S, 'MQTT_TOPIC', 'garden'), 'every': getattr(S, 'MQTT_EVERY_S', 60),
            'retain': getattr(S, 'MQTT_RETAIN', True), 'fields': list(getattr(S, 'MQTT_FIELDS', ()))}
FIELDS = ('air', 'pump', 'batt', 'board')       # plus 'sen_<id>' per registry sensor; [] = everything


def load_cfg():
    c = dict(DEFAULTS)
    try:
        with open(CFG) as f:
            d = json.load(f)
        for k in DEFAULTS:
            if k in d:
                c[k] = d[k]
    except (OSError, ValueError):
        pass
    if not c['cid']:
        import machine
        c['cid'] = 'garden-' + ''.join('%02x' % b for b in machine.unique_id()[-3:])
    return c


def snapshot(avg, fields):
    """The selected values as a dict for <base>/state (here, not in app.py: only in RAM while MQTT is on).
    avg = Mqtt.avg(); fields = 'sen_<id>', 'air', 'pump', 'batt', 'board' ([] = everything). An unselected
    soil sensor is sent as null so the others keep their position in the list."""
    import gc
    from app import sens, bands, pump, batt, rec, net, stamp, time_ok, uptime
    from bands import BANDS
    S_ = S
    raws, t, rh, hpa, n, gv = avg
    if raws is None:
        raws, t, rh, hpa, n, gv = list(sens.raw), sens.t, sens.rh, sens.p, 1, list(sens.gval)
    allf = not fields                        # empty selection = everything
    d = {'time': stamp() if time_ok() else None, 'samples': n}
    ss = []
    for i, c in enumerate(sens.ch):
        if not allf and 'sen_' + c[5] not in fields:
            ss.append(None)
            continue
        p = sens.pct(raws[i], c[2], c[3])
        k = bands.band(i, p)
        ss.append({'id': c[5], 'name': c[4], 'gpio': c[0], 'raw': round(raws[i], 1), 'pct': round(p, 1), 'band': k,
                   'band_text': BANDS[k], 'full_F': bands.full[i], 'water_at': bands.full[i] - bands.w(),
                   'dry_rate_pts_day': round(bands.rate[i] * 24, 1), 'rate_measured': bands.rate_ok[i]})
    if any(ss):
        d['soil'] = ss
    gs = {}
    for j, g in enumerate(sens.gch):
        if allf or 'sen_' + g[6] in fields:
            v = gv[j] if j < len(gv) else None
            key = g[6] if len([x for x in sens.gch if x[6] == g[6]]) == 1 else g[6] + '.' + g[2]
            gs[key] = {'name': g[3], 'value': None if v is None else round(v, g[5]), 'unit': g[4]}
    if gs:
        d['sensors'] = gs
    if allf or 'air' in fields:
        d['air'] = {'temp_c': None if t is None else round(t, 2), 'rh_pct': None if rh is None else round(rh, 1),
                    'hpa': None if hpa is None else round(hpa, 1), 'sensor': sens.clim_name if sens.clim else None}
    if pump and (allf or 'pump' in fields):
        c = pump.cfg
        d['pump'] = {'on': pump.running, 'busy': pump.busy(), 'mode': c['mode'], 'fault': c['fault'] or None,
                     'today_s': c['day_s'], 'day_max_s': S_.PUMP_DAY_MAX_S, 'tank_empty': pump.tank_empty(),
                     'runs_today': c['runs_day'], 'today_l': round(c['l_day'], 3), 'total_l': round(c['l_total'], 2),
                     'flow_lpm': None if pump.lpm is None or not pump.running else round(pump.lpm, 2)}
    if batt and (allf or 'batt' in fields):
        d['battery'] = {'v': None if batt.v is None else round(batt.v, 2), 'state': batt.state}
    if allf or 'board' in fields:
        d['uptime_s'] = uptime()
        d['recording'] = rec.fname if rec.active else None
        d['wifi_rssi'] = net.sta.status('rssi') if net.sta.isconnected() else None
        d['heap_free'] = gc.mem_free()
    return d




def _resolve(host, port, box):
    """Runs in a _thread: blocking DNS lookup, result (or the exception) into box."""
    import socket
    try:
        box.append(socket.getaddrinfo(host, port)[0][-1])
    except Exception as e:
        box.append(e)


def save_cfg(c):
    tmp = CFG + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(c, f)
    os.rename(tmp, CFG)


def _vlen(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | 0x80 if n else b)
        if not n:
            return out


def _str(s):
    b = s.encode() if isinstance(s, str) else s
    return bytes((len(b) >> 8, len(b) & 0xFF)) + b


class Mqtt:
    def __init__(self, uptime):
        self.up = uptime
        self.cfg = load_cfg()
        self.r = self.w = None
        self.connected = False
        self.err = ''
        self.sent = 0
        self.last = None               # uptime() of the last state publish
        self.next = 0                  # uptime() of the next state publish
        self.retry = 0                 # uptime() of the next connect attempt
        self.backoff = 10
        self.events = []               # pump log lines waiting to be sent (max 10)
        self.kick = False              # reconnect with new settings
        self.gprs = None               # gsm.Gsm when a modem is set up (MQTT over GPRS)
        self.wifi = lambda: True       # WiFi connected? (set by the app)
        self.via = ''                  # 'WiFi' or 'GPRS' of the open connection
        self.ip = None                 # cached (host name, 'ip') of the broker
        self.dns = None                # box of a lookup thread still running
        self._acc_reset(0, 0)

    # ---------------------------------------------------------------- averaging since the last publish
    def _acc_reset(self, n, m):
        self.acc_raw = [0] * n
        self.acc_g = [0.0] * m               # registry sensor channels (sum, count)
        self.acc_gn = [0] * m
        self.acc_n = 0
        self.acc_t = self.acc_h = self.acc_p = 0.0
        self.acc_tn = self.acc_hn = self.acc_pn = 0

    def feed(self, raw, t, rh, p, g=()):
        """Called by the sampler after every reading (~2 s)."""
        if not self.cfg['on']:
            return
        if len(self.acc_raw) != len(raw) or len(self.acc_g) != len(g):
            self._acc_reset(len(raw), len(g))
        for i, r in enumerate(raw):
            self.acc_raw[i] += r
        for j, v in enumerate(g):
            if v is not None:
                self.acc_g[j] += v
                self.acc_gn[j] += 1
        self.acc_n += 1
        if t is not None:
            self.acc_t += t
            self.acc_tn += 1
        if rh is not None:
            self.acc_h += rh
            self.acc_hn += 1
        if p is not None:
            self.acc_p += p
            self.acc_pn += 1

    def avg(self):
        """(raw averages, temp, rh, hpa, samples, channel averages) since the last publish."""
        n = self.acc_n
        return ([r / n for r in self.acc_raw] if n else None,
                self.acc_t / self.acc_tn if self.acc_tn else None,
                self.acc_h / self.acc_hn if self.acc_hn else None,
                self.acc_p / self.acc_pn if self.acc_pn else None, n,
                [a / c if c else None for a, c in zip(self.acc_g, self.acc_gn)])

    def save(self, **kw):
        self.cfg.update(kw)
        save_cfg(self.cfg)
        self.kick = True
        self.retry = self.next = 0
        self.backoff = 10

    def event(self, text):
        """Hook for pump.log(): queue the line and publish the state right after it."""
        f = self.cfg['fields']
        if self.cfg['on'] and (not f or 'pump' in f):
            if len(self.events) >= 10:
                self.events.pop(0)
            self.events.append(text)
            self.next = 0

    def topic(self, t):
        return self.cfg['base'] + '/' + t

    async def _addr(self):
        """Broker address without blocking the loop: IP as-is, a name via a lookup thread."""
        h, port = self.cfg['host'], self.cfg['port']
        if all(ch in '0123456789.' for ch in h):
            return h, port
        if self.ip and self.ip[0] == h:
            return self.ip[1], port
        try:
            import _thread
        except ImportError:
            return h, port                      # no threads in this firmware: blocking lookup
        if self.dns is None:                    # never more than one lookup thread
            self.dns = []
            _thread.start_new_thread(_resolve, (h, port, self.dns))
        t0 = self.up()
        while not self.dns:
            if self.up() - t0 > 30:
                raise OSError('DNS lookup of %s timed out' % h)   # thread finishes later on its own
            await asyncio.sleep_ms(100)
        r, self.dns = self.dns[0], None
        if isinstance(r, Exception):
            raise OSError('cannot resolve %s (%s)' % (h, r))
        ip = r[0]                               # sockaddr ('192.168.1.10', 1883)
        self.ip = (h, ip)
        return ip, port

    # ---------------------------------------------------------------- protocol
    async def _send(self, pkt):
        self.w.write(pkt)
        await asyncio.wait_for(self.w.drain(), 10)

    async def _publish(self, topic, payload, retain=False):
        body = _str(topic) + (payload.encode() if isinstance(payload, str) else payload)
        await self._send(bytes((0x31 if retain else 0x30,)) + _vlen(len(body)) + body)

    async def _connect(self):
        c = self.cfg
        keep = min(65535, c['every'] * 2 + 60)       # every publish counts as keep-alive traffic
        flags = 0x02 | 0x04 | 0x20                    # clean session, will (retained "offline")
        pay = _str(c['cid']) + _str(self.topic('status')) + _str('offline')
        if c['user']:
            flags |= 0x80
            pay += _str(c['user'])
            if c['pw']:
                flags |= 0x40
                pay += _str(c['pw'])
        var = _str('MQTT') + bytes((4, flags, keep >> 8, keep & 0xFF))
        if self.gprs and self.gprs.use_gprs(self.wifi()):
            self.r, self.w = await asyncio.wait_for(self.gprs.tcp(c['host'], c['port']), 150)
            self.via = 'GPRS'                                 # the modem resolves host names itself
        else:
            host, port = await self._addr()
            self.r, self.w = await asyncio.wait_for(asyncio.open_connection(host, port), 10)
            self.via = 'WiFi'
        await self._send(b'\x10' + _vlen(len(var) + len(pay)) + var + pay)
        ack = await asyncio.wait_for(self.r.readexactly(4), 10)
        if len(ack) < 4 or ack[0] != 0x20:
            raise OSError('no CONNACK')
        if ack[3]:
            raise OSError({1: 'bad protocol', 2: 'client id rejected', 3: 'server unavailable',
                           4: 'bad user name or password', 5: 'not authorised'}.get(ack[3], 'refused %d' % ack[3]))
        self.connected = True
        self.err = ''
        self.backoff = 10
        await self._publish(self.topic('status'), 'online', True)
        print('mqtt: connected to %s:%d' % (c['host'], c['port']))

    async def _close(self, polite=True):
        if self.w:
            try:
                if polite and self.connected:
                    await self._publish(self.topic('status'), 'offline', True)
                    await self._send(b'\xe0\x00')          # DISCONNECT
            except Exception:
                pass
            try:
                self.w.close()
                await self.w.wait_closed()
            except Exception:
                pass
        self.r = self.w = None
        self.connected = False

    # ---------------------------------------------------------------- loop
    async def run(self, snapshot_fn, online):
        while True:
            await asyncio.sleep(1)
            c = self.cfg
            try:
                if self.kick or not c['on']:
                    self.kick = False
                    await self._close()
                    if not c['on']:
                        continue
                if not c['host'] or not online():
                    continue
                now = self.up()
                if not self.connected:
                    if now < self.retry:
                        continue
                    self.retry = now + self.backoff
                    self.backoff = min(self.backoff * 2, 300)
                    await self._connect()
                while self.events:
                    await self._publish(self.topic('pump/event'), self.events[0])
                    self.events.pop(0)
                if now >= self.next:
                    self.next = now + c['every']
                    await self._publish(self.topic('state'), json.dumps((snapshot_fn or snapshot)(self.avg(), c['fields'])), c['retain'])
                    self._acc_reset(len(self.acc_raw), len(self.acc_g))
                    self.sent += 1
                    self.last = now
                    await self._send(b'\xc0\x00')             # PINGREQ: is the broker really there?
                    if await asyncio.wait_for(self.r.readexactly(2), 10) != b'\xd0\x00':
                        raise OSError('broker did not answer the ping')
            except Exception as e:
                self.err = ('no answer from the broker (timeout)' if isinstance(e, asyncio.TimeoutError)
                            else str(e) or e.__class__.__name__)
                print('mqtt:', self.err)
                self.ip = None                  # look the name up again next time
                await self._close(False)

    def state(self):
        """0 off, 1 connected (broker answered the last ping), 2 enabled but not connected."""
        if not self.cfg['on']:
            return 0
        return 1 if self.connected else 2

    def status(self):
        """Plain text (callers escape it)."""
        c = self.cfg
        if not c['on']:
            return 'off'
        if not c['host']:
            return 'enabled, no server set'
        s = ('connected to %s:%d via %s' % (c['host'], c['port'], self.via)) if self.connected else \
            ('not connected (%s:%d)' % (c['host'], c['port']))
        if self.last is not None:
            s += ' \u00b7 last publish %d s ago, %d sent' % (self.up() - self.last, self.sent)
        if self.err and not self.connected:
            s += ' \u00b7 error: %s \u00b7 retry in %d s' % (self.err, max(0, self.retry - self.up()))
        elif not self.connected and c['host']:
            s += ' \u00b7 connecting'
        return s
