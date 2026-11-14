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
# Spec: docs/SPEC.md#gsm
try:
    import asyncio
except ImportError:
    import uasyncio as asyncio
import time
from machine import Pin, UART
import settings as S
from gsm_gprs import Gprs
from gsm_view import View
from gsm_sms import Sms


RX = 1024                      # receive buffer size (an SMS listing or a TCP chunk fits)
IGNORE = ('Call Ready', 'SMS Ready', 'NO CARRIER', 'BUSY', 'NO ANSWER')


def _command(text, trusted=False):
    """SMS / call command -> reply (gsm_cmd.py, imported on the first message)."""
    from gsm_cmd import sms_command
    return sms_command(text, trusted)


def _int(b):
    """int of a bytes / str number, None if it is not one."""
    try:
        return int(b)
    except ValueError:
        return None


class Gsm(Sms, Gprs, View):
    """SIM800L driver: AT command engine, start-up, SMS in / out, calls, alerts (see the header)."""

    def __init__(self, c, ctx):
        """Modem state from settings c; ctx = app.sens (I2C bus for the T-Call IP5306)."""
        self.c, self.ctx = c, ctx
        self.u = None
        self.lock = asyncio.Lock()
        self.rx = bytearray(RX)        # receive buffer, filled up to self.n
        self.mv = memoryview(self.rx)
        self.n = 0
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
        """Supply, reset and power-key pins (T-Call: keep the IP5306 boost on); returns the PWKEY pin."""
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
        """Send text or bytes to the modem UART."""
        self.u.write(s if isinstance(s, bytes) else s.encode())

    async def _reader(self):
        """Task: read UART bytes into the fixed receive buffer (no allocation per read) and parse them."""
        while True:
            if self.u.any():
                if self.n >= RX:                                   # no line end in 1 KB: drop it
                    self.n = 0
                k = self.u.readinto(self.mv[self.n:])
                if k:
                    self.n += k
                    self._parse()
            await asyncio.sleep_ms(30)

    def _parse(self):
        """Take complete lines out of the receive buffer: reply lines, raw TCP data, the '> ' prompt and
        unsolicited events (_event). An incomplete line or data block stays for the next read."""
        rx, mv, pos, end = self.rx, self.mv, 0, self.n
        while pos < end:
            if rx[pos] == 62 and pos + 1 < end and rx[pos + 1] == 32:     # '> ' prompt (no line end)
                self.lines.append('>')
                pos += 2
                continue
            i = rx.find(b'\r\n', pos, end)
            if i < 0:
                break
            line = bytes(mv[pos:i])
            if line.startswith(b'+CIPRXGET: 2,'):                 # raw data follows: n bytes
                n = _int(line[13:].split(b',')[0]) or 0
                if end - i - 2 < n:
                    break                                         # wait for the rest
                self.data.append(bytes(mv[i + 2:i + 2 + n]))
                pos = i + 2 + n
                continue
            pos = i + 2
            s = line.decode('utf-8', 'ignore').strip()
            if s and not self._event(s):
                self.lines.append(s)
        rem = end - pos
        if pos and rem:
            rx[:rem] = bytes(mv[pos:end])                         # keep the incomplete tail
        self.n = rem

    def _event(self, s):
        """Handle an unsolicited line (new SMS, RING / caller ID, link status); False = a reply line."""
        if s.startswith('+CMTI:'):                                # new SMS stored: +CMTI: "SM",3
            n = _int(s.split(',')[-1])
            if n is not None:
                self.sms_new.append(n)
        elif s.startswith('+CLIP:'):                              # +CLIP: "+97150...",145,"",0,"",0
            p = s.split('"')
            self.caller = p[1] if len(p) > 1 else ''
            self.ringing = True
        elif s == 'RING':
            self.ringing = True
        elif s in ('CLOSED', '+PDP: DEACT'):
            self.tcp_on = False
            if s == '+PDP: DEACT':
                self.ip = ''
        elif not (s.startswith('+CIPRXGET: 1') or s in IGNORE):
            return False
        return True

    async def cmd(self, c, ok=('OK',), timeout=5000, raw=None):
        """Send an AT command and wait for its FINAL result code (ok); returns all reply lines.
        Raises OSError on ERROR / ... FAIL / timeout. Waiting for the final code (not an info line
        like +CREG) keeps the next command from seeing a late OK."""
        async with self.lock:
            return await self._cmd(c, ok, timeout, raw)

    async def _cmd(self, c, ok, timeout, raw=None):
        """cmd() without the lock (callers that already hold it)."""
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
        """Power on, wait for the modem, SIM PIN, network registration, SMS text mode, read stored SMS."""
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
        """Refresh registration, signal and operator; returns True when registered."""
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

    # ---------------------------------------------------------------- main loop
    async def run(self, command=None):
        """Task: start the modem, then every second handle calls, received and queued SMS, and refresh the
        network status every minute. command(text, trusted) -> reply (default: gsm_cmd.sms_command)."""
        try:
            await self._start()
            self.err = ''
        except Exception as e:
            self.state, self.err = 'failed', str(e)
            print('gsm:', self.err)
            return
        command = command or _command
        n = 0
        while True:
            await asyncio.sleep(1)
            n += 1
            try:
                if self.ringing:
                    await asyncio.sleep(1)                        # let +CLIP arrive
                    await self._call(command)
                await self._inbox(command)
                await self._outbox()
                if n % 60 == 0:
                    await self._status()
                self.err = ''
            except Exception as e:
                self.err = str(e)
                print('gsm:', self.err)
