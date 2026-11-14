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
            'retain': getattr(S, 'MQTT_RETAIN', True)}


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
        self.ip = None                 # cached (host name, 'ip') of the broker
        self.dns = None                # box of a lookup thread still running
        self._acc_reset(0)

    # ---------------------------------------------------------------- averaging since the last publish
    def _acc_reset(self, n):
        self.acc_raw = [0] * n
        self.acc_n = 0
        self.acc_t = self.acc_h = self.acc_p = 0.0
        self.acc_tn = self.acc_hn = self.acc_pn = 0

    def feed(self, raw, t, rh, p):
        """Called by the sampler after every reading (~2 s)."""
        if not self.cfg['on']:
            return
        if len(self.acc_raw) != len(raw):
            self._acc_reset(len(raw))
        for i, r in enumerate(raw):
            self.acc_raw[i] += r
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
        """(raw averages, temp, rh, hpa, samples) since the last publish; None parts = no data."""
        n = self.acc_n
        return ([r / n for r in self.acc_raw] if n else None,
                self.acc_t / self.acc_tn if self.acc_tn else None,
                self.acc_h / self.acc_hn if self.acc_hn else None,
                self.acc_p / self.acc_pn if self.acc_pn else None, n)

    def save(self, **kw):
        self.cfg.update(kw)
        save_cfg(self.cfg)
        self.kick = True
        self.retry = self.next = 0
        self.backoff = 10

    def event(self, text):
        """Hook for pump.log(): queue the line and publish the state right after it."""
        if self.cfg['on']:
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
        host, port = await self._addr()
        self.r, self.w = await asyncio.wait_for(asyncio.open_connection(host, port), 10)
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
    async def run(self, snapshot, online):
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
                    await self._publish(self.topic('state'), json.dumps(snapshot(self.avg())), c['retain'])
                    self._acc_reset(len(self.acc_raw))
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
        s = ('connected to %s:%d' % (c['host'], c['port'])) if self.connected else \
            ('not connected (%s:%d)' % (c['host'], c['port']))
        if self.last is not None:
            s += ' \u00b7 last publish %d s ago, %d sent' % (self.up() - self.last, self.sent)
        if self.err and not self.connected:
            s += ' \u00b7 error: %s \u00b7 retry in %d s' % (self.err, max(0, self.retry - self.up()))
        elif not self.connected and c['host']:
            s += ' \u00b7 connecting'
        return s
