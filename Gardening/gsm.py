# SIM800L GSM modem (TTGO T-Call V1.4 or a separate module): SMS alerts + SMS commands, and a TCP
# link over GPRS that the MQTT publisher can use when WiFi is down (or always). Set up from the
# dashboard GSM panel (DATA_DIR/gsm.json); wiring in settings.py (MODEM_*). Not loaded while off.
#
# SIM800L is 2G (GSM 900/1800) only: check that your operator still runs 2G.
#
# Only the allowed numbers (last 9 digits compared) can act; everything else is ignored / hung up.
# SMS commands (case does not matter; optional secret code first, e.g. "4711 PUMP 20"):
#   STATUS, PUMP [seconds], STOP, RESET, MODE MANUAL|SCHEDULE|AUTO, WATER (litres, runs),
#   LED ON|OFF|ECO, RGB ON|OFF|ECO, INFO (signal, network, RAM), HELP
#   + your own keywords from the panel, e.g. "WATER GARDEN=PUMP 30; NIGHT=LED OFF".
# Calls from an allowed number are hung up (no charge for the caller) and then do the chosen
# action: status SMS, run the pump, or toggle the pump. Unknown callers are hung up.
# SMS alerts (each switchable, sent only on a change, at most 'max_day' SMS per day):
#   pump locked / dry run, soil sensor drops to 'water now' / 'drought', battery low / critical.
#
# Non-blocking: one asyncio reader collects lines and unsolicited messages (+CMTI new SMS,
# +CIPRXGET data), commands run one at a time under a lock; TCP data is read in manual mode
# (AT+CIPRXGET=2), so binary MQTT bytes never mix with AT text.
try:
    import asyncio
except ImportError:
    import uasyncio as asyncio
import json
import os
import time
from machine import Pin, UART
import settings as S

from gsm_cfg import CFG, DEFAULTS, CALL_ACTIONS, load_cfg, save_cfg   # noqa: F401 (used by app / SMS)


def _digits(n):
    return ''.join(ch for ch in n if ch.isdigit())[-9:]          # compare the last 9 digits


ICON = {'in': '&#9993;', 'out': '&#8599;', 'call': '&#9742;', 'deny': '&#8856;'}   # envelope, out, phone, blocked


def esc_(t):
    return str(t).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


class Tcp:
    """Stream-like TCP connection over GPRS for mqtt.py (write / drain / readexactly / close)."""

    def __init__(self, g):
        self.g, self.wb, self.rb = g, b'', b''

    def write(self, b):
        self.wb += b

    async def drain(self):
        if self.wb:
            b, self.wb = self.wb, b''
            await self.g.send(b)

    async def readexactly(self, n):
        while len(self.rb) < n:
            d = await self.g.recv(n - len(self.rb))
            if d:
                self.rb += d
            else:
                await asyncio.sleep_ms(200)
        b, self.rb = self.rb[:n], self.rb[n:]
        return b

    def close(self):
        pass

    async def wait_closed(self):
        await self.g.tcp_close()


class Gsm:
    def __init__(self, c, ctx):
        self.c, self.ctx = c, ctx
        self.u = None
        self.lock = asyncio.Lock()
        self.buf = b''
        self.lines = []                # reply lines of the running command
        self.data = []                 # raw TCP chunks from +CIPRXGET: 2
        self.sms_new = []              # indexes from +CMTI
        self.state = 'starting'
        self.csq = None
        self.op = ''
        self.net = False               # registered
        self.ip = ''                   # GPRS address ('' = no GPRS)
        self.tcp_on = False
        self.err = ''
        self.sent_day, self.day = 0, ''
        self.prev = {}                 # last alerted state per key
        self.sent_at = {}              # (key, state) -> time.time() of the last SMS (3 h repeat guard)
        self.outbox = []               # (number or None = all, text)
        self.last_cmd = ''
        self.caller = None             # number from +CLIP while it rings
        self.ringing = False
        self.call_t = {}               # number -> time of the last call action (one per call)
        self.log = []                  # last events: (time 'MM-DD HH:MM', kind, last 4 digits, text, result)
        self.ok_t = None               # time.time() of the last command the modem answered
        self.calls = self.sms_in = 0   # counters since boot

    # ---------------------------------------------------------------- low level
    def _power_on(self):
        if S.MODEM_IP5306 and self.ctx.i2c:
            try:
                self.ctx.i2c.writeto_mem(0x75, 0x00, b'\x37')   # IP5306: keep boost on at light load
            except OSError:
                pass
        if S.MODEM_POWER is not None:
            Pin(S.MODEM_POWER, Pin.OUT, value=1)
        if S.MODEM_RST is not None:
            Pin(S.MODEM_RST, Pin.OUT, value=1)
        if S.MODEM_PWKEY is not None:
            k = Pin(S.MODEM_PWKEY, Pin.OUT, value=1)
            return k
        return None

    def _write(self, s):
        self.u.write(s if isinstance(s, bytes) else s.encode())

    async def _reader(self):
        while True:
            n = self.u.any()
            if n:
                self.buf += self.u.read(n)
                self._parse()
            await asyncio.sleep_ms(30)

    def _parse(self):
        while True:
            if self.buf.startswith(b'> '):                        # prompt (no line end)
                self.lines.append('>')
                self.buf = self.buf[2:]
                continue
            i = self.buf.find(b'\r\n')
            if i < 0:
                return
            line, rest = self.buf[:i], self.buf[i + 2:]
            if line.startswith(b'+CIPRXGET: 2,'):                 # raw data follows: n bytes
                try:
                    n = int(line[13:].split(b',')[0])
                except ValueError:
                    n = 0
                if len(rest) < n:
                    return                                        # wait for the rest
                self.data.append(rest[:n])
                self.buf = rest[n:]
                continue
            self.buf = rest
            s = line.decode('utf-8', 'ignore').strip()
            if not s:
                continue
            if s.startswith('+CMTI:'):                            # new SMS stored: +CMTI: "SM",3
                try:
                    self.sms_new.append(int(s.split(',')[1]))
                except (IndexError, ValueError):
                    pass
            elif s == 'RING':
                self.ringing = True
            elif s.startswith('+CLIP:'):                          # +CLIP: "+97150...",145,"",0,"",0
                p = s.split('"')
                self.caller = p[1] if len(p) > 1 else ''
                self.ringing = True
            elif s.startswith('+CIPRXGET: 1') or s in ('Call Ready', 'SMS Ready', 'NO CARRIER', 'BUSY',
                                                       'NO ANSWER'):
                pass
            elif s in ('CLOSED', '+PDP: DEACT'):
                self.tcp_on = False
                if s == '+PDP: DEACT':
                    self.ip = ''
            else:
                self.lines.append(s)

    async def cmd(self, c, ok=('OK',), timeout=5000, raw=None):
        """Send an AT command and wait for its FINAL result code (ok); returns all reply lines.
        Raises OSError on ERROR / ... FAIL / timeout. Waiting for the final code (not an info line
        like +CREG) keeps the next command from seeing a late OK."""
        async with self.lock:
            return await self._cmd(c, ok, timeout, raw)

    async def _cmd(self, c, ok, timeout, raw=None):
        self.lines = []
        self.last_cmd = c
        self._write(c + '\r')
        t = time.ticks_ms()
        prompt_done = raw is None
        while True:
            for k, s in enumerate(self.lines):
                if not prompt_done and s == '>':
                    self._write(raw)
                    prompt_done = True
                    del self.lines[k]
                    break
                if any(s.startswith(x) for x in ok):
                    self.ok_t = time.time()
                    out = self.lines
                    self.lines = []
                    return out
                if 'ERROR' in s or s.endswith(' FAIL'):
                    self.lines = []
                    raise OSError('%s -> %s' % (c, s))
            if time.ticks_diff(time.ticks_ms(), t) > timeout:
                self.lines = []
                raise OSError('%s -> timeout' % c)
            await asyncio.sleep_ms(50)

    # ---------------------------------------------------------------- start-up
    async def _start(self):
        k = self._power_on()
        self.u = UART(S.MODEM_UART, baudrate=S.MODEM_BAUD, tx=S.MODEM_TX, rx=S.MODEM_RX, rxbuf=1024, timeout=0)
        asyncio.create_task(self._reader())
        for _ in range(3):
            if k:                                                 # PWKEY low ~1.1 s = power on
                k.value(0)
                await asyncio.sleep_ms(1100)
                k.value(1)
            await asyncio.sleep(4)
            for _ in range(8):
                try:
                    await self.cmd('AT', timeout=1000)
                    break
                except OSError:
                    pass
            else:
                continue
            break
        else:
            raise OSError('modem does not answer (power / wiring / baud?)')
        await self.cmd('ATE0')
        await self.cmd('AT+CMEE=2')
        r = ' '.join(await self.cmd('AT+CPIN?', timeout=10000))
        if 'SIM PIN' in r:
            if not self.c['pin']:
                raise OSError('SIM needs a PIN - enter it in the GSM panel')
            await self.cmd('AT+CPIN=%s' % self.c['pin'], timeout=10000)
        elif 'READY' not in r:
            raise OSError('SIM: %s' % r)
        self.state = 'searching network'
        for _ in range(60):
            if await self._status():
                break
            await asyncio.sleep(2)
        await self.cmd('AT+CMGF=1')                               # SMS text mode
        await self.cmd('AT+CSCS="GSM"')
        await self.cmd('AT+CNMI=2,1,0,0,0')                       # new SMS -> +CMTI
        await self.cmd('AT+CLIP=1')                               # caller number with RING
        r = await self.cmd('AT+CMGL="ALL"', timeout=10000)        # SMS received while off
        for s in r:
            if s.startswith('+CMGL:'):
                try:
                    self.sms_new.append(int(s[6:].split(',')[0]))
                except ValueError:
                    pass

    async def _info(self, c, prefix):
        """The info line of a query, e.g. '+CREG: 0,1' ('' if missing)."""
        for s in await self.cmd(c):
            if s.startswith(prefix):
                return s
        return ''

    async def _status(self):
        r = await self._info('AT+CREG?', '+CREG')
        self.net = r.split(',')[-1].strip() in ('1', '5')         # 1 home, 5 roaming
        try:
            self.csq = int((await self._info('AT+CSQ', '+CSQ'))[6:].split(',')[0])
        except (OSError, ValueError):
            self.csq = None
        if self.net:
            try:
                r = await self._info('AT+COPS?', '+COPS')
                self.op = r.split('"')[1] if '"' in r else ''
            except (OSError, IndexError):
                pass
        self.state = 'registered' if self.net else 'searching network'
        return self.net

    # ---------------------------------------------------------------- SMS
    async def _read_sms(self, i):
        r = await self.cmd('AT+CMGR=%d' % i, timeout=5000)
        await self.cmd('AT+CMGD=%d' % i)
        num, text = '', []
        for k, s in enumerate(r):
            if s.startswith('+CMGR:'):
                p = s.split('"')
                num = p[3] if len(p) > 3 else ''
                text = [x for x in r[k + 1:] if x != 'OK']
                break
        return num, ' '.join(text).strip()

    def allowed(self, num):
        d = _digits(num)
        return bool(d) and any(_digits(p) == d for p in self.c['phones'].split(',') if p.strip())

    async def send_sms(self, num, text):
        self._day()
        if self.sent_day >= self.c['max_day']:
            raise OSError('daily SMS limit reached')
        await self.cmd('AT+CMGS="%s"' % num, timeout=60000, raw=text[:160].encode() + b'\x1a')   # +CMGS: n, OK
        self.sent_day += 1

    def _day(self):
        d = time.localtime(time.time() + S.TZ_OFFSET_H * 3600)[:3]
        if d != self.day:
            self.day, self.sent_day = d, 0

    def queue(self, text, num=None):
        if len(self.outbox) < 8:
            self.outbox.append((num, text))

    def alert(self, key, state, text):
        """Queue an SMS to all allowed numbers when 'key' enters a new alarming 'state'."""
        if self.prev.get(key) != state:
            self.prev[key] = state
            if state:
                t, k = time.time(), (key, state)
                if t - self.sent_at.get(k, -10 ** 9) >= 3 * 3600:   # band flicker: no repeat within 3 h
                    self.sent_at[k] = t
                    self.queue(text)

    def check(self, pcts):
        """SMS alerts on changes (every ~30 s from the sampler)."""
        from bands import BANDS
        from app import sens, bands, pump, batt
        c = self.c
        if c['a_soil']:
            for i, v in enumerate(pcts):
                k = bands.band(i, v)
                nm = sens.ch[i][4]
                self.alert('soil' + nm, k if k in ('now', 'dry') else None, 'Garden: %s %s (%d%%)' % (nm, BANDS[k], round(v)))
        if c['a_pump'] and pump:
            f = pump.cfg['fault']
            self.alert('pump', f or None, 'Garden pump LOCKED: %s Reply RESET after refilling.' % f[:100])
        if c['a_batt'] and batt and batt.v is not None:
            self.alert('batt', None if batt.state == 'ok' else batt.state,
                      'Garden battery %s: %.2f V' % ('CRITICAL' if batt.state == 'crit' else 'low', batt.v))

    # ---------------------------------------------------------------- GPRS / TCP
    def use_gprs(self, wifi_up):
        m = self.c['gprs']
        return self.net and (m == 'always' or (m == 'fallback' and not wifi_up))

    async def _gprs_up(self):
        if self.ip:
            return
        c = self.c
        if not c['apn']:
            raise OSError('no APN set in the GSM panel')
        for a in ('AT+CIPSHUT', 'AT+CIPMUX=0', 'AT+CIPRXGET=1'):
            await self._cmd(a, ('OK', 'SHUT OK'), 10000)
        await self._cmd('AT+CSTT="%s","%s","%s"' % (c['apn'], c['apn_user'], c['apn_pw']), ('OK',), 10000)
        await self._cmd('AT+CIICR', ('OK',), 85000)
        self.lines = []
        self._write('AT+CIFSR\r')                                 # answers with the IP only
        t = time.ticks_ms()
        while not self.lines:
            if time.ticks_diff(time.ticks_ms(), t) > 10000:
                raise OSError('no GPRS address')
            await asyncio.sleep_ms(50)
        ip = self.lines.pop(0)
        if 'ERROR' in ip or '.' not in ip:
            raise OSError('GPRS: %s' % ip)
        self.ip = ip

    async def tcp(self, host, port):
        """Open a TCP connection over GPRS; returns (reader, writer) like asyncio.open_connection."""
        async with self.lock:
            await self._gprs_up()
            self.data = []
            await self._cmd('AT+CIPSTART="TCP","%s",%d' % (host, port), ('CONNECT OK', 'ALREADY CONNECT'), 60000)
            self.tcp_on = True
        t = Tcp(self)
        return t, t

    async def send(self, b):
        if not self.tcp_on:
            raise OSError('GPRS link closed')
        async with self.lock:
            await self._cmd('AT+CIPSEND=%d' % len(b), ('SEND OK',), 20000, raw=b)

    async def recv(self, n):
        if self.data:
            d = self.data.pop(0)
            if len(d) > n:
                self.data.insert(0, d[n:])
                d = d[:n]
            return d
        if not self.tcp_on:
            raise OSError('GPRS link closed')
        async with self.lock:
            await self._cmd('AT+CIPRXGET=2,%d' % min(n, 512), ('OK',), 5000)
        return self.data.pop(0) if self.data else b''

    async def tcp_close(self):
        if self.tcp_on:
            self.tcp_on = False
            try:
                await self.cmd('AT+CIPCLOSE=1', ('CLOSE OK', 'OK'), 5000)
            except OSError:
                pass

    # ---------------------------------------------------------------- calls / actions
    def note(self, kind, num, what, res=''):
        """kind: 'in' SMS received, 'out' SMS sent, 'call', 'deny' (unknown number / wrong code)."""
        from app import stamp, time_ok
        self.log = (self.log + [(stamp()[5:16] if time_ok() else '', kind, num[-4:], what[:40], res[:60])])[-8:]

    async def _call(self, command):
        """Ringing: hang up, then (allowed number, once per call) do the configured action."""
        num = self.caller or ''
        self.caller, self.ringing = None, False
        try:
            await self.cmd('ATH', timeout=5000)
        except OSError:
            pass
        t = time.time()
        d = _digits(num)
        if t - self.call_t.get(d, -100) < 40:                     # later RINGs of the same call
            return
        self.call_t[d] = t
        self.calls += 1
        if not num:
            self.note('deny', '', 'call, no caller ID', 'hung up')    # never acts
            return
        if not self.allowed(num):
            print('gsm: call from unknown number', num, 'hung up')
            self.note('deny', num, 'call, unknown number', 'hung up')
            return
        a = self.c['call']
        if a == 'off':
            self.note('call', num, 'call', 'hung up')
            return
        from app import pump
        if a == 'status':
            cmdtxt = 'STATUS'
        elif a == 'toggle' and pump and pump.running:
            cmdtxt = 'STOP'
        else:
            cmdtxt = 'PUMP'
        r = command(cmdtxt, True)
        self.note('call', num, 'call: ' + cmdtxt, r)
        if a == 'status' or self.c['call_sms']:
            self.queue(r, num)

    # ---------------------------------------------------------------- main loop
    async def run(self, command):
        """command(text) -> reply text, for allowed SMS senders."""
        try:
            await self._start()
            self.err = ''
        except Exception as e:
            self.state, self.err = 'failed', str(e)
            print('gsm:', self.err)
            return
        n = 0
        while True:
            await asyncio.sleep(1)
            n += 1
            try:
                if self.ringing:
                    await asyncio.sleep(1)                        # let +CLIP arrive
                    await self._call(command)
                while self.sms_new:
                    num, text = await self._read_sms(self.sms_new.pop(0))
                    self.sms_in += 1
                    if self.allowed(num):
                        print('gsm: SMS from', num, ':', text)
                        r = command(text)
                        if r:
                            self.note('in', num, text, r)
                            self.queue(r, num)
                        else:
                            self.note('deny', num, 'SMS without the code', 'ignored')
                    else:
                        print('gsm: SMS from unknown number', num, 'ignored')
                        self.note('deny', num, 'SMS, unknown number', 'ignored')
                while self.outbox and self.net:
                    num, text = self.outbox.pop(0)
                    for p in ([num] if num else [x.strip() for x in self.c['phones'].split(',') if x.strip()]):
                        await self.send_sms(p, text)
                    if not num:                                   # replies are shown with their command
                        self.note('out', 'all', text)
                if n % 60 == 0:
                    await self._status()
                self.err = ''
            except Exception as e:
                self.err = str(e)
                print('gsm:', self.err)

    def card(self):
        """Dashboard card (built here so it costs no RAM while the modem is off)."""
        q = self.csq if self.csq is not None and self.csq != 99 else None
        if self.state == 'failed':
            band, badge = 'dry', 'Failed'
        elif self.net:
            band, badge = 'good', 'Online'
        else:
            band, badge = 'soon', self.state[:1].upper() + self.state[1:]
        bars = 0 if q is None else 1 if q < 10 else 2 if q < 15 else 3 if q < 20 else 4
        sig = ('<span class="sig">%s</span> %s' % (''.join('<i class="%s" style="height:%dpx"></i>'
               % ('on' if k < bars else '', 6 + 5 * k) for k in range(4)),
               '%d<small> dBm</small>' % (2 * q - 113) if q is not None else '&ndash;'))
        ans = 'not answering' if self.ok_t is None or time.time() - self.ok_t > 150 else 'answering'
        if self.state == 'failed':
            ans = esc_(self.err)
        o = ['<div class="card band %s"><h3><span>GSM <small>SIM800L</small></span><span class="badge">%s</span></h3>'
             '<div class="big">%s</div><dl class="facts"><dt>Modem</dt><dd>%s</dd><dt>Network</dt><dd>%s</dd>'
             '<dt>GPRS</dt><dd>%s</dd><dt>SMS</dt><dd>sent %d/%d today &middot; in %d &middot; calls %d</dd></dl>'
             % (band, badge, sig, ans, esc_(self.op) or ('searching' if not self.net else 'registered'),
                (esc_(self.ip) + (' &middot; TCP open' if self.tcp_on else '')) if self.ip else 'off',
                self.sent_day, self.c['max_day'], self.sms_in, self.calls)]
        if self.log:
            o.append('<p class="muted" style="font-size:12px;margin:6px 0 0">')
            for t, kind, n, what, res in reversed(self.log[-4:]):
                o.append('%s %s %s %s%s<br>' % (t[6:], ICON.get(kind, ''), '..' + esc_(n) if n and n != 'all' else 'to all'
                         if n else '', esc_(what), ' &rarr; ' + esc_(res) if res else ''))
            o.append('</p>')
        elif self.err and self.state != 'failed':
            o.append('<p class="warn">%s</p>' % esc_(self.err))
        o.append('</div>')
        return ''.join(o)

    def status(self):
        s = self.state
        if self.op:
            s += ' &middot; %s' % self.op
        if self.csq is not None and self.csq != 99:
            s += ' &middot; signal %d/31' % self.csq
        if self.ip:
            s += ' &middot; GPRS %s%s' % (self.ip, ' (TCP open)' if self.tcp_on else '')
        s += ' &middot; SMS today %d/%d' % (self.sent_day, self.c['max_day'])
        if self.err:
            s += ' &middot; <span class="warn">%s</span>' % self.err.replace('<', '&lt;')
        return s


# ---------------------------------------------------------------- app hooks (only in RAM while GSM is on)
def status_text():
    import app
    from app import sens, bands, pump, batt, stamp, time_ok
    """Short plain-text status for an SMS reply (max 160 characters)."""
    from bands import BANDS
    p = ['Garden %s:' % stamp()[11:16] if time_ok() else 'Garden:']
    for i, c in enumerate(sens.ch):
        v = sens.pct(sens.raw[i], c[2], c[3])
        p.append('%s %d%% %s;' % (c[4], round(v), BANDS[bands.band(i, v)]))
    if sens.t is not None:
        p.append('air %.1fC%s;' % (sens.t, ' %d%%' % sens.rh if sens.rh is not None else ''))
    if pump:
        p.append('pump %s, today %ds%s;' % ('LOCKED' if pump.cfg['fault'] else 'on' if pump.running else 'ready',
                                          pump.cfg['day_s'], ' %.2fL' % pump.cfg['l_day'] if pump.cfg['l_day'] else ''))
    if batt and batt.v is not None:
        p.append('batt %.2fV %s' % (batt.v, batt.state))
    return ' '.join(p)[:160]


HELP = ('STATUS, PUMP [s], STOP, RESET, MODE MANUAL|SCHEDULE|AUTO, WATER, LED ON|OFF|ECO, RGB ON|OFF|ECO, '
        'HEAT|LIGHT|FAN [value|ON|OFF], CTRL <name> [value|ON|OFF], INFO')


def sms_command(text, trusted=False):
    """Text from an allowed number -> reply text ('' = ignore: secret code missing / wrong).
    trusted: a call action (no code needed)."""
    import app
    from app import pump
    c = app.gsm.c if app.gsm else DEFAULTS
    t = text.strip().upper().split()
    code = c['code'].strip().upper()
    if code and not trusted:
        if not t or t[0] != code:
            return ''
        t = t[1:]
    words = ' '.join(t)
    for a in c['alias'].split(';'):                            # own keywords: WORDS=COMMAND
        k, _, v = a.partition('=')
        if k.strip() and k.strip().upper() == words:
            t = v.strip().upper().split()
            break
    w = t[0] if t else ''
    arg = t[1] if len(t) > 1 else ''
    if w in ('STATUS', 'S'):
        return status_text()
    if w in ('PUMP', 'STOP', 'RESET', 'MODE', 'WATER') and not pump:
        return 'No pump configured.'
    if w == 'PUMP':
        try:
            secs = int(arg) if arg else pump.cfg['man_s']
        except ValueError:
            return 'Use: PUMP <seconds>'
        return pump.start(secs, 'sms') or 'Pump on for %d s (max %d).' % (min(secs, S.PUMP_MAX_S), S.PUMP_MAX_S)
    if w == 'STOP':
        pump.stop('stopped by SMS')
        return 'Pump stopped.'
    if w == 'RESET':
        pump.reset_fault()
        return 'Pump lock cleared.'
    if w == 'MODE':
        m = {'MANUAL': 'manual', 'SCHEDULE': 'schedule', 'SCHED': 'schedule', 'AUTO': 'auto'}.get(arg)
        if not m:
            return 'Use: MODE MANUAL|SCHEDULE|AUTO (now %s)' % pump.cfg['mode']
        pump.save(mode=m)
        return 'Pump mode: %s.' % m
    if w == 'WATER':
        p = pump.cfg
        return 'Water today %.2f L, %d runs, %d s; total %.1f L.' % (p['l_day'], p['runs_day'], p['day_s'], p['l_total'])
    if w in ('LED', 'RGB'):
        m = arg.lower()
        if m not in ('on', 'off', 'eco'):
            return 'Use: %s ON|OFF|ECO' % w
        if w == 'LED':
            if not S.LED_PINS:
                return 'No LED indicator on this board.'
            if app.leds:
                app.leds.set_mode(m)
            elif m != 'off':
                app.leds_start().set_mode(m)
            return 'LED indicator: %s.' % m
        from rgb import load_cfg, save_cfg
        r = load_cfg()
        if not r['on']:
            return 'RGB light is not set up (Watering bands - settings).'
        r['mode'] = m
        save_cfg(r)
        app.rgb_apply()
        return 'RGB light: %s.' % m
    if w in ('HEAT', 'LIGHT', 'FAN', 'CTRL'):
        return _ctrl_cmd(app, w, t[1:])
    if w == 'INFO':
        import gc
        g = app.gsm
        ip = app.net.sta.ifconfig()[0] if app.net and app.net.sta.isconnected() else 'no WiFi'
        return 'GSM %s signal %s/31%s; WiFi %s; RAM %d KB free; up %d h.' % (
            g.op if g else '-', g.csq if g else '-', ', GPRS ' + g.ip if g and g.ip else '', ip,
            gc.mem_free() // 1024, app.uptime() // 3600)
    return 'Commands: ' + HELP


def _ctrl_cmd(app, w, args):
    """HEAT / LIGHT / FAN [value|ON|OFF] (first controller of that kind), CTRL <name> [value|ON|OFF].
    A value sets the level in manual mode, otherwise the setpoint."""
    cs = app.ctrl
    if w == 'CTRL':
        if not args:
            return 'Use: CTRL <name> [value|ON|OFF]'
        key, args = args[0], args[1:]
    else:
        key = {'HEAT': 'heater', 'LIGHT': 'light', 'FAN': 'fan'}[w]
    k = cs.get(key) if cs else None
    if not k:
        return 'No %s set up (Control panel).' % key.lower()
    c, arg = k.c, args[0] if args else ''
    if arg == 'OFF':
        if c['mode'] != 'off':
            c['on_mode'] = c['mode']                       # ON brings this mode back
        c['mode'] = 'off'
    elif arg == 'ON':
        c['mode'] = c.get('on_mode', 'pid') if c.get('on_mode') != 'off' else 'manual'
    elif arg:
        try:
            v = float(arg)
        except ValueError:
            return 'Use: %s [value|ON|OFF]' % w
        if c['mode'] == 'curve':
            return '%s follows its curve - change it in the Control panel.' % c['name']
        if c['mode'] == 'sun' or (c['mode'] == 'off' and c.get('on_mode') == 'sun'):
            if not 0 <= v <= 100:
                return 'Day level 0-100 %.'
            c['day'], c['mode'] = int(v), 'sun'
            c['night'] = min(c['night'], c['day'])
        elif c['mode'] == 'manual' or (c['mode'] == 'off' and c.get('on_mode') == 'manual'):
            if not 0 <= v <= 100:
                return 'Level 0-100 %.'
            c['man'], c['mode'] = int(v), 'manual'
        else:
            if c['cut'] is not None and (v >= c['cut'] if c['kind'] != 'fan' else v <= c['cut']):
                return 'Setpoint must stay inside the cut-off (%g).' % c['cut']
            c['sp'] = v
            if c['mode'] == 'off':
                c['mode'] = c.get('on_mode', 'pid')
    if arg:
        from control_cfg import load_cfg, save_cfg
        cfg = load_cfg()
        cfg['list'] = [dict(c) if e['id'] == c['id'] else e for e in cfg['list']]
        save_cfg(cfg)
        try:
            k.step()                                       # reply with the new state
        except Exception:
            pass
    lbl, unit = k.src()
    if unit and unit[0] != '\xb0':
        unit = ' ' + unit
    return '%s %s: %s%s, output %d%%.' % (c['name'], k.state,
                                          'level %d%%' % c['man'] if c['mode'] == 'manual' else
                                          'sun %s-%s day %d%%' % (c['rise'], c['set'], c['day']) if c['mode'] == 'sun' else
                                          'curve' if c['mode'] == 'curve' else
                                          'off' if c['mode'] == 'off' else 'set %g%s' % (c['sp'], unit),
                                          '' if k.v is None else ', now %.1f%s' % (k.v, unit), round(k.outp))
