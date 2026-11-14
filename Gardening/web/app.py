# Garden moisture logger - asyncio web app for ESP32 MicroPython (htmx 4 + Alpine.js UI)
#
# Heap safety: one shared sampler task (HTTP handlers never touch hardware), small HTML
# fragments, files streamed in 512-byte chunks, capped connections / headers / bodies,
# gc after every request, and a free-heap low-water mark shown in the UI.
try:
    import asyncio            # MicroPython >= 1.21
except ImportError:
    import uasyncio as asyncio   # MicroPython 1.20 (used for vGuest WPA2/WPA3 compatibility)
import gc
import json
import os
import time
import machine
import network
from machine import ADC, Pin, I2C
import settings as S

gc.collect()
gc.threshold(gc.mem_free() // 4 + gc.mem_alloc())

WWW = 'www/'
TXT = 'text/plain'
HTML = 'text/html; charset=utf-8'
UNIX_OFF = 946684800 if time.gmtime(0)[0] == 2000 else 0
NAME_OK = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-'

heap_min = gc.mem_free()
clock_src = 'none'
_up_ms = 0
_up_last = time.ticks_ms()


# ---------------------------------------------------------------- helpers
def uptime():
    """Seconds since boot; immune to RTC changes and ticks wrap (called every few s)."""
    global _up_ms, _up_last
    n = time.ticks_ms()
    _up_ms += time.ticks_diff(n, _up_last)
    _up_last = n
    return _up_ms // 1000


def heap_note():
    global heap_min
    f = gc.mem_free()
    if f < heap_min:
        heap_min = f
    return f


def flash_free():
    st = os.statvfs('/')
    return st[0] * st[4]


def time_ok():
    return time.gmtime()[0] >= 2025


def stamp(t=None, secs=True):
    tm = time.localtime((time.time() if t is None else t) + S.TZ_OFFSET_H * 3600)
    if secs:
        return '%04d-%02d-%02d %02d:%02d:%02d' % tm[:6]
    return '%04d-%02d-%02d %02d:%02d' % tm[:5]


def dur(s):
    s = int(s)
    if s < 120:
        return '%d s' % s
    if s < 7200:
        return '%d min' % (s // 60)
    return '%d h %02d min' % (s // 3600, s % 3600 // 60)


def kb(n):
    return '%.1f MB' % (n / 1048576) if n >= 1048576 else '%d KB' % (n // 1024)


def esc(s):
    return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


def clean(s, maxlen):
    return ''.join(c for c in s[:maxlen] if c in NAME_OK)


def safe_csv(n):
    if not n or len(n) > 48 or not n.endswith('.csv'):
        return None
    return n if clean(n[:-4], 44) == n[:-4] else None


def unquote(s):
    s = s.replace('+', ' ')
    if '%' not in s:
        return s
    out = bytearray()
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == '%' and i + 2 < n:
            try:
                out.append(int(s[i + 1:i + 3], 16))
                i += 3
                continue
            except ValueError:
                pass
        out.extend(c.encode())
        i += 1
    return out.decode()


def parse_qs(s):
    d = {}
    for part in s.split('&'):
        if part:
            k, _, v = part.partition('=')
            d[unquote(k)] = unquote(v)
    return d


# ---------------------------------------------------------------- sensors
class Sensors:
    def __init__(self):
        import sys
        import sensors as R                 # registry: read the list, then release the module
        self.reg = R.load()
        R = None
        del sys.modules['sensors']
        self.ch = []                         # soil sensors: (pin, adc, dry, wet, name, id)
        self.gen = []                        # other sensors: driver objects (drv_<type>.py)
        self.gch = []                        # their channels: (driver idx, chan idx, key, label, unit, dec, id)
        self.bad = []                        # (name, error) of sensors that did not start
        for e in self.reg:
            if e['type'] == 'soil':
                try:
                    self.ch.append(self._soil(e))
                except Exception as ex:
                    self.bad.append((e.get('name', '?'), str(ex)))
        if not self.ch:                      # bands / pump need a soil sensor: fall back to the template
            pin, dry, wet, name = S.SENSORS[0]
            self.ch.append(self._soil({'pin': pin, 'dry': dry, 'wet': wet, 'name': name, 'id': 'S1'}))
        self.raw = [0] * len(self.ch)
        self.t = self.rh = self.p = None
        self.updated = 0
        self.i2c = None
        self.clim = None                     # climate.Climate: AHT10 / Si70xx / BMP280, auto-detected
        self.clim_name = 'air sensor'
        self.clim_err = ''
        self._fails = 0                      # consecutive read failures
        self._backoff = S.AIR_RETRY_S[0]
        self._next_try = 0                   # uptime() of the next detect attempt
        self._clim_init()                    # also creates the shared I2C bus (self.i2c)
        self.uart_free = list(getattr(S, 'UART_IDS', (1, 2)))   # UART ids for UART sensors (chip / board)
        for e in self.reg:                   # other sensors: drivers get this object as ctx (ctx.i2c)
            if e['type'] == 'soil':
                continue
            try:
                d = __import__('drv_' + (e.get('drv') or e['type'])).Driver(e, self)
                gi = len(self.gen)
                self.gen.append(d)
                for k, (key, label, unit, dec) in enumerate(d.chans()):
                    self.gch.append((gi, k, key, label, unit, dec, e['id']))
            except Exception as ex:
                print('sensor %s not started: %r' % (e.get('name'), ex))
                self.bad.append((e.get('name', '?'), str(ex)))
        self.gval = [None] * len(self.gch)
        self.acc_reset()

    def air_addrs(self):
        """I2C addresses used by the auto-detected air sensor."""
        c = self.clim
        return [getattr(x, 'addr', None) for x in ((c.hum, c.baro) if c else ()) if x and getattr(x, 'addr', None)]

    @staticmethod
    def _soil(e):
        a = ADC(Pin(e['pin']))
        a.atten(ADC.ATTN_11DB)
        try:
            a.width(ADC.WIDTH_12BIT)         # classic ESP32; S3 / C3 / C6 are 12-bit already
        except AttributeError:
            pass
        return (e['pin'], a, e['dry'], e['wet'], e['name'], e['id'])

    def _bus_reset(self):
        """Free a bus held low by a half-reset sensor: clock SCL up to 9 times until SDA is released."""
        scl = Pin(S.I2C_SCL, Pin.OPEN_DRAIN, value=1)
        sda = Pin(S.I2C_SDA, Pin.IN, Pin.PULL_UP)
        for _ in range(9):
            if sda.value():
                break
            scl(0)
            time.sleep_us(5)
            scl(1)
            time.sleep_us(5)
        try:
            self.i2c = I2C(0, scl=Pin(S.I2C_SCL), sda=Pin(S.I2C_SDA), freq=100000, timeout=S.I2C_TIMEOUT_US)
        except TypeError:                    # firmware without the timeout argument
            self.i2c = I2C(0, scl=Pin(S.I2C_SCL), sda=Pin(S.I2C_SDA), freq=100000)

    def _clim_init(self):
        """One quick attempt (4 address probes); on failure schedule the next one with back-off."""
        try:
            self._bus_reset()
            from climate import detect
            self.clim = detect(self.i2c)
            self.clim_name = self.clim.name
            self.clim_err = ''
            self._fails = 0
            self._backoff = S.AIR_RETRY_S[0]
        except Exception as e:
            self.clim = None
            self.clim_err = 'not found (%s)' % e
            self._next_try = uptime() + self._backoff
            self._backoff = min(self._backoff * 2, S.AIR_RETRY_S[1])

    def acc_reset(self):
        self.acc_raw = [0] * len(self.ch)
        self.acc_g = [0.0] * len(self.gch)
        self.acc_gn = [0] * len(self.gch)
        self.acc_n = 0
        self.acc_t = self.acc_rh = self.acc_p = 0.0
        self.acc_tn = self.acc_hn = self.acc_pn = 0

    @staticmethod
    def pct(raw, dry, wet):
        p = (dry - raw) * 100.0 / (dry - wet)
        return 0.0 if p < 0 else 100.0 if p > 100 else p

    async def sample(self):
        sw = pwr is not None and pwr.sensor_switching()
        if sw:                               # power.py: sensors fed from GPIO only while measuring
            pwr.sensors(True)
            await asyncio.sleep_ms(S.SENSOR_SETTLE_MS)
        for i, c in enumerate(self.ch):
            a, s = c[1], 0
            for _ in range(S.ADC_SAMPLES):
                s += a.read()
                time.sleep_us(300)
            self.raw[i] = s // S.ADC_SAMPLES
        if self.gen:                         # other sensors (registry drivers)
            j = 0
            for d in self.gen:
                try:
                    vals = d.read()
                except Exception:
                    vals = None
                for v in (vals or [None] * len(d.chans())):
                    self.gval[j] = v
                    j += 1
        if sw:
            pwr.sensors(False)
        # Moisture is read above regardless; the air sensor must never hold up the loop.
        if self.clim is None and uptime() >= self._next_try:
            self._clim_init()
        if self.clim:
            try:
                self.clim.trigger()
                await asyncio.sleep_ms(85)
                self.t, self.rh, self.p = self.clim.result()
                self._fails = 0
            except Exception as e:
                self.t = self.rh = self.p = None
                self._fails += 1
                self.clim_err = 'read failed (%s)' % e
                if self._fails >= 3:         # 3 bad reads in a row: treat as unplugged, re-detect later
                    self.clim = None
                    self._fails = 0
                    self._next_try = uptime() + self._backoff
                    self._backoff = min(self._backoff * 2, S.AIR_RETRY_S[1])
        for i, r in enumerate(self.raw):
            self.acc_raw[i] += r
        for j, v in enumerate(self.gval):
            if v is not None:
                self.acc_g[j] += v
                self.acc_gn[j] += 1
        self.acc_n += 1
        if self.t is not None:
            self.acc_t += self.t
            self.acc_tn += 1
        if self.rh is not None:
            self.acc_rh += self.rh
            self.acc_hn += 1
        if self.p is not None:
            self.acc_p += self.p
            self.acc_pn += 1
        self.updated = uptime()


# ---------------------------------------------------------------- recorder
REC_STATE = S.DATA_DIR + '/rec.json'


class Recorder:
    def __init__(self, sens):
        self.sens = sens
        self.active = self.resume = False
        self.fname = ''
        self.interval = 60
        self.end = self.started = self.rows = self.next = 0
        self.msg = ''
        try:
            os.mkdir(S.DATA_DIR)
        except OSError:
            pass
        try:
            with open(REC_STATE) as f:
                st = json.load(f)
            if st.get('active') and safe_csv(st.get('fname')):
                self.fname, self.interval = st['fname'], st['interval']
                self.end, self.started = st['end'], st['started']
                self.resume = True           # picked up by run() once the clock is valid
        except (OSError, ValueError, KeyError):
            pass

    def path(self, name=None):
        return S.DATA_DIR + '/' + (name or self.fname)

    def _save(self):
        with open(REC_STATE, 'w') as f:
            json.dump({'active': self.active or self.resume, 'fname': self.fname,
                       'interval': self.interval, 'end': self.end, 'started': self.started}, f)

    def _header(self):
        cols = ['timestamp', 'elapsed_s', 'air_temp_c', 'air_rh_pct', 'air_hpa']
        for c in self.sens.ch:
            cols.append('gpio%d_raw' % c[0])
            cols.append('gpio%d_pct' % c[0])
        for g in self.sens.gch:
            cols.append('%s_%s' % (g[6], g[2]))      # e.g. S3_v (registry sensor id + channel)
        cols.append('samples')
        return ','.join(cols) + '\n'

    def start(self, interval, hours, label):
        if self.active or self.resume:
            return 'Already recording.'
        if not time_ok():
            return 'Clock not set yet - wait a few seconds or reload the page.'
        if flash_free() < S.MIN_FREE_FLASH:
            return 'Not enough free flash - delete old recordings first.'
        now = time.time()
        name = stamp(now, False).replace('-', '').replace(' ', '_').replace(':', '')
        if label:
            name += '_' + label
        self.fname = name + '.csv'
        self.interval, self.started = interval, now
        self.end = now + int(hours * 3600) if hours > 0 else 0
        with open(self.path(), 'w') as f:
            f.write(self._header())
        self.rows = 0
        self.sens.acc_reset()
        self.next = now + interval
        self.active = True
        self.msg = ''
        self._save()
        print('rec: start', self.fname, interval, 's')
        return ''

    def stop(self, why='Stopped'):
        self.active = self.resume = False
        self.msg = '%s at %s.' % (why, stamp())
        self._save()
        print('rec:', self.msg)

    def _row(self, now):
        s = self.sens
        if not s.acc_n:
            return
        p = [stamp(now), str(now - self.started)]
        p.append('%.2f' % (s.acc_t / s.acc_tn) if s.acc_tn else '')
        p.append('%.1f' % (s.acc_rh / s.acc_hn) if s.acc_hn else '')
        p.append('%.1f' % (s.acc_p / s.acc_pn) if s.acc_pn else '')
        for i, c in enumerate(s.ch):
            r = s.acc_raw[i] / s.acc_n
            p.append('%.1f' % r)
            p.append('%.1f' % s.pct(r, c[2], c[3]))
        for j, g in enumerate(s.gch):
            p.append(('%%.%df' % g[5]) % (s.acc_g[j] / s.acc_gn[j]) if s.acc_gn[j] else '')
        p.append(str(s.acc_n))
        with open(self.path(), 'a') as f:
            f.write(','.join(p) + '\n')
        self.rows += 1
        s.acc_reset()

    def _count_rows(self):
        n, buf = 0, bytearray(512)
        try:
            with open(self.path(), 'rb') as f:
                while True:
                    k = f.readinto(buf)
                    if not k:
                        break
                    n += bytes(buf[:k]).count(b'\n')
        except OSError:
            pass
        return max(0, n - 1)

    async def run(self):
        while True:
            await asyncio.sleep(1)
            try:
                now = time.time()
                if self.resume and time_ok():
                    self.resume = False
                    if self.end and now >= self.end:
                        self.stop('Finished (while powered off)')
                        continue
                    self.rows = self._count_rows()
                    self.sens.acc_reset()
                    self.next = now + self.interval
                    self.active = True
                    self.msg = 'Resumed after restart at %s.' % stamp()
                    print('rec:', self.msg)
                if not self.active:
                    continue
                if now >= self.next:
                    self._row(now)
                    self.next += self.interval
                    if self.next <= now:         # clock jumped forward
                        self.next = now + self.interval
                    if flash_free() < S.MIN_FREE_FLASH:
                        self.stop('Stopped: flash nearly full')
                    elif os.stat(self.path())[6] > S.MAX_FILE_BYTES:
                        self.stop('Stopped: file size limit reached')
                if self.active and self.end and now >= self.end:
                    self.stop('Finished')
            except Exception as e:
                self.stop('Stopped: error %s' % e)


# ---------------------------------------------------------------- network (WiFi manager)
def _stat(name, default):
    return getattr(network, name, default)


FAIL_REASON = {
    _stat('STAT_WRONG_PASSWORD', 202): 'wrong password',
    _stat('STAT_NO_AP_FOUND', 201): 'network not found (2.4 GHz only)',
    _stat('STAT_CONNECT_FAIL', 203): 'connection failed',
    _stat('STAT_HANDSHAKE_TIMEOUT', 204): 'handshake timeout (check password)',
    _stat('STAT_ASSOC_FAIL', 203): 'association failed',
    _stat('STAT_BEACON_TIMEOUT', 200): 'signal lost',
}


_dns_on = False


def _dns_start():
    """Captive DNS task while the setup AP is up (captive.py, unloaded when the AP goes down)."""
    global _dns_on
    if not _dns_on:
        _dns_on = True
        asyncio.create_task(_dns())


async def _dns():
    global _dns_on
    import sys
    import captive
    try:
        await captive.run(net)
    except Exception as e:
        print('dns error:', e)
    finally:
        captive = None
        del sys.modules['captive']
        _dns_on = False


class Net:
    """STA with saved credentials; falls back to a setup AP (+ captive DNS) where the user
    scans and joins a network from the web page. mDNS (garden.local) is answered by the
    MicroPython built-in responder using network.hostname()."""

    def __init__(self):
        try:
            network.country(S.WIFI_COUNTRY)        # before interfaces start (ch 12-13)
            network.hostname(S.HOSTNAME)           # mDNS name -> HOSTNAME.local
        except Exception as e:
            print('net: country/hostname', e)
        self.sta = network.WLAN(network.STA_IF)
        self.ap = network.WLAN(network.AP_IF)
        self.creds = None
        try:
            with open('wifi.json') as f:
                self.creds = json.load(f)
            if not self.creds.get('ssid'):
                self.creds = None
        except (OSError, ValueError):
            pass
        self.pending = None          # (ssid, password, save) while a connect attempt runs
        self.pend_at = 0
        self.msg = ''
        self.down_since = None
        self.retry_at = 0
        self.ap_off_at = None
        self.scan_cache = []
        self.scan_at = None
        self.ntp_at = None
        self.ntp_try = -10 ** 6
        self.ntp_err = ''

    # -- helpers
    def ip(self):
        return self.sta.ifconfig()[0] if self.sta.isconnected() else ''

    def ap_clients(self):
        try:
            return len(self.ap.status('stations'))
        except Exception:
            return 0

    def _ap(self, on):
        if on and not self.ap.active():
            self.ap.active(True)
            self.ap.config(essid=S.AP_SSID, password=S.AP_PASSWORD, authmode=3)
            print('net: setup AP', S.AP_SSID, 'at', self.ap.ifconfig()[0])
            _dns_start()
        elif not on and self.ap.active():
            self.ap.active(False)
            print('net: setup AP off')

    def connect(self, ssid, pw, save):
        self.sta.active(True)
        try:
            self.sta.disconnect()
        except Exception:
            pass
        self.sta.connect(ssid, pw)
        self.pending = (ssid, pw, save)
        self.pend_at = uptime()
        self.msg = ''
        print('net: connecting to', ssid)

    def forget(self):
        try:
            os.remove('wifi.json')
        except OSError:
            pass
        self.creds = self.pending = None
        try:
            self.sta.disconnect()
        except Exception:
            pass
        self._ap(True)
        self.msg = 'Saved network forgotten.'

    def scan(self):
        """Blocking ~2-3 s. Keeps the strongest entry per SSID, max 20."""
        self.sta.active(True)
        best = {}
        for n in self.sta.scan():
            name = n[0].decode('utf-8', 'replace')
            if name and (name not in best or n[3] > best[name][0]):
                best[name] = (n[3], n[2], n[4])
        self.scan_cache = sorted(((k,) + v for k, v in best.items()), key=lambda x: -x[1])[:20]
        self.scan_at = uptime()
        gc.collect()

    def _ntp(self):
        global clock_src
        import ntptime
        self.ntp_try = uptime()
        try:
            ntptime.host = S.NTP_HOST
            ntptime.timeout = 2
            ntptime.settime()
            self.ntp_at, self.ntp_err, clock_src = uptime(), '', 'NTP'
            print('net: NTP ok', stamp())
            if pwr:
                pwr.mark_ntp()
        except Exception as e:
            self.ntp_err = str(e) or 'timeout'

    # -- state machine, 1 s tick
    def _tick(self):
        now = uptime()
        up = self.sta.isconnected()
        if self.pending:
            st = self.sta.status()
            if up:
                ssid, pw, save = self.pending
                self.pending = None
                if save:
                    with open('wifi.json', 'w') as f:
                        json.dump({'ssid': ssid, 'password': pw}, f)
                    self.creds = {'ssid': ssid, 'password': pw}
                self.msg = 'Connected to %s.' % ssid
                # keep the setup AP a little so the phone can read the result, then drop it
                self.ap_off_at = now + 45 if self.ap.active() else None
                print('net: WiFi', ssid, self.ip(), '-> http://%s.local/' % S.HOSTNAME)
            elif now - self.pend_at > 20 or (now - self.pend_at > 4 and st in FAIL_REASON):
                ssid = self.pending[0]
                self.pending = None
                self.msg = 'Could not connect to %s: %s.' % (ssid, FAIL_REASON.get(st, 'timeout'))
                try:
                    self.sta.disconnect()
                except Exception:
                    pass
                self._ap(True)
                self.retry_at = now + 120
                print('net:', self.msg)
            return
        if up:
            self.down_since = None
            if self.ap.active() and self.ap_off_at is not None and now >= self.ap_off_at:
                self._ap(False)
                self.ap_off_at = None
            due = S.NTP_EVERY_S if self.ntp_at is not None else 60
            if now - self.ntp_try >= due:
                self._ntp()
        else:
            if self.down_since is None:
                self.down_since = now
            if not self.creds or now - self.down_since > 30:
                self._ap(True)
            # retry saved network in the background, but not while someone uses the setup AP
            if self.creds and now >= self.retry_at and not self.ap_clients():
                self.retry_at = now + 120
                self.connect(self.creds['ssid'], self.creds.get('password', ''), False)

    async def run(self):
        if self.creds:
            self.connect(self.creds['ssid'], self.creds.get('password', ''), False)
        else:
            self._ap(True)
        while True:
            try:
                self._tick()
            except Exception as e:
                print('net error:', e)
            await asyncio.sleep(1)


# ---------------------------------------------------------------- HTML fragments
BAND_ORDER = (('dry', 'Drought stress: '), ('now', 'Water now: '), ('wet', 'Too wet: '),
              ('soon', 'Water soon: '), ('drain', 'Draining after watering: '))


def band_scale(v, F, w):
    """Zones dry..wet left to right, current-value marker and F tick."""
    cuts = [0] + [max(0, min(100, x)) for x in (F - w - 20, F - w - 10, F - w, F + 5)] + [100]
    z = ''.join('<i class="%s" style="width:%d%%"></i>' % (k, cuts[j + 1] - cuts[j])
                for j, k in enumerate(('dry', 'now', 'soon', 'good', 'wet')))
    return ('<div class="scale">%s<b class="mark" style="left:%d%%"></b><span class="f" style="left:%d%%">F %d</span></div>'
            % (z, max(0, min(100, round(v))), F, F))


def frag_live():
    s, b = sens, bands
    from bands import BANDS
    w = b.w()
    cards, worst = [], {}
    for i, c in enumerate(s.ch):
        raw = s.raw[i]
        p = s.pct(raw, c[2], c[3])
        k = b.band(i, p)
        worst.setdefault(k, []).append(c[4])
        nxt, est = b.next_text(i, p, k)
        cards.append('<div class="card band %s"><h3><span>%s <small>GPIO%d</small></span><span class="badge">%s</span></h3>'
                     '<div class="big">%d<small>%%</small></div>%s'
                     '<dl class="facts"><dt>Full (F)</dt><dd>%d%% &middot; water at %d%%</dd>'
                     '<dt>Next watering</dt><dd>%s%s</dd><dt>Trend 24 h</dt><dd>%s</dd></dl>'
                     '<p class="muted">raw %d &middot; dry %d &middot; wet %d</p></div>'
                     % (k, esc(c[4]), c[0], BANDS[k], round(p), band_scale(p, b.full[i], w),
                        b.full[i], b.full[i] - w, nxt, ' <small>&middot; estimated</small>' if est else '',
                        b.trend_text(i), raw, c[2], c[3]))
    hot = s.t is not None and s.t > 32
    alert = ''
    for k, lead in BAND_ORDER:
        if k in worst:
            alert = '<div class="alert %s">%s%s%s</div>' % (k, lead, ' and '.join(esc(x) for x in worst[k]),
                    ' &middot; hot (%.0f &deg;C): water early morning' % s.t if hot and k in ('soon', 'now', 'dry') else '')
            break
    if pump and pump.cfg['fault']:
        alert = ('<div class="alert dry">Pump stopped: %s Refill / check, then press <b>Reset</b> in Watering pump.</div>'
                 % esc(pump.cfg['fault'])) + alert
    if batt and batt.state != 'ok' and batt.v is not None:
        alert = ('<div class="alert %s">Battery %s: %.2f V &ndash; recharge or replace soon.</div>'
                 % ('now' if batt.state == 'crit' else 'soon', 'critical' if batt.state == 'crit' else 'low', batt.v)) + alert
    if not alert and hot:
        alert = ('<div class="alert now">Hot: %.0f &deg;C &ndash; above 32 &deg;C cucumbers drop flowers. '
                 'Shade 08:00&ndash;12:00.</div>' % s.t)
    out = [alert, '<div class="grid">'] + cards
    for j, g in enumerate(s.gch):
        v = s.gval[j]
        out.append('<div class="card"><h3><span>%s <small>%s</small></span></h3><div class="big">%s<small>%s</small></div>'
                   '<p class="muted">%s</p></div>'
                   % (esc(g[3]), g[6], '&ndash;' if v is None else ('%%.%df' % g[5]) % v, esc(g[4]),
                      s.gen[g[0]].info()))
    for name, err in s.bad:
        out.append('<div class="card"><h3>%s</h3><div class="big">&ndash;</div><p class="warn">not started: %s</p></div>'
                   % (esc(name), esc(err)))
    if s.t is not None:
        extra = []
        if s.rh is not None:
            extra.append('%.0f%% RH' % s.rh)
        if s.p is not None:
            extra.append('%.1f hPa' % s.p)
        k, lbl = (('now', 'Too hot') if s.t > 32 else ('soon', 'Warm') if s.t > 28 else
                  ('soon', 'Too cold') if s.t < 15 else ('good', 'Good'))
        out.append('<div class="card band %s"><h3><span>Air <small>%s</small></span><span class="badge">%s</span></h3>'
                   '<div class="big">%.1f<small>&deg;C</small></div><p class="muted">%s</p>'
                   '<dl class="facts"><dt>Cucumbers</dt><dd>22&ndash;28 &deg;C day &middot; 60&ndash;80%% RH</dd></dl></div>'
                   % (k, esc(s.clim_name), lbl, s.t, ' &middot; '.join(extra)))
    else:
        err = s.clim_err or 'starting'
        if s.clim is None and s._next_try > uptime():
            err += ' - retry in %s' % dur(s._next_try - uptime())
        out.append('<div class="card"><h3>Air <small>%s</small></h3><div class="big">&ndash;</div>'
                   '<p class="warn">%s</p></div>' % (esc(s.clim_name), esc(err)))
    if batt:
        if batt.v is None:
            out.append('<div class="card"><h3>Battery <small>GPIO%d</small></h3><div class="big">&ndash;</div></div>' % batt.pin)
        else:
            k, lbl = {'ok': ('good', 'OK'), 'low': ('soon', 'Low'), 'crit': ('now', 'Critical')}[batt.state]
            out.append('<div class="card band %s"><h3><span>Battery <small>GPIO%d</small></span><span class="badge">%s</span></h3>'
                       '<div class="big">%.2f<small>V</small></div><p class="muted">low %.1f V &middot; critical %.1f V'
                       ' &middot; pin %.2f V</p></div>'
                       % (k, batt.pin, lbl, batt.v, batt.low_v, batt.crit_v, batt.pin_v or 0))
    if pump:
        out.append('<div class="card band %s"><h3><span>Pump <small>GPIO%d</small></span><span class="badge">%s</span></h3>'
                   '<p>%s</p><p class="muted">today %d of %d s%s</p></div>'
                   % ('dry' if pump.cfg['fault'] else 'wet' if pump.busy() else 'good', S.PUMP_PIN,
                      'Fault' if pump.cfg['fault'] else 'On' if pump.running else 'Ready',
                      pump.state_text(), pump.cfg['day_s'], S.PUMP_DAY_MAX_S,
                      ' &middot; %.2f L &middot; total %.1f L' % (pump.cfg['l_day'], pump.cfg['l_total'])
                      if pump.cfg['flow'] or pump.cfg['rate_lpm'] else ''))
    if gsm:
        out.append(gsm.card())
    from bands import STAGES
    note = ' &middot; %s' % esc(b.msg) if b.msg and uptime() < 3600 else ''
    out.append('</div><p class="muted">Sampled %s &middot; <span x-text="age"></span> s ago &middot; stage: %s%s</p>'
               '<i hidden x-init="seen()"></i>' % (stamp() if time_ok() else '(clock not set)', esc(STAGES[b.stage][0]), note))
    return ''.join(out)


def frag_status(msg=''):
    r, n = rec, net
    heap_note()
    rec_on = 1 if (r.active or r.resume) else 0
    out = ['<i hidden x-init="sync(%d,%d,%d,%d,$el.dataset.mqt)" data-mqt="%s"></i>'
           % (rec_on, 1 if time_ok() else 0, flash_free(), mqtt.state() if mqtt else 0,
              esc(mqtt.status()) if mqtt else 'off')]
    if msg:
        out.append('<p class="warn">%s</p>' % esc(msg))
    if r.active:
        line = 'every %s &middot; %d rows &middot; running %s' % (dur(r.interval), r.rows, dur(time.time() - r.started))
        if r.end:
            line += ' &middot; ends %s (in %s)' % (stamp(r.end, False), dur(max(0, r.end - time.time())))
        else:
            line += ' &middot; until stopped'
        out.append('<p><b class="dot on"></b><b>Recording</b> <code>%s</code><br><span class="muted">%s</span></p>'
                   % (esc(r.fname), line))
    elif r.resume:
        out.append('<p><b class="dot wait"></b><b>Waiting for clock</b> to resume <code>%s</code></p>' % esc(r.fname))
    else:
        out.append('<p><b class="dot"></b>Not recording</p>')
    if r.msg:
        out.append('<p class="muted">%s</p>' % esc(r.msg))
    # system line
    if n.sta.isconnected():
        wifi = '%s &middot; %s &middot; %s.local (%d dBm)' % (esc(n.sta.config('essid')), n.ip(), S.HOSTNAME, n.sta.status('rssi'))
    elif n.pending:
        wifi = 'connecting to %s&hellip;' % esc(n.pending[0])
    else:
        wifi = 'not connected - <a href="/setup">set up WiFi</a>'
    if n.ap.active():
        wifi += ' &middot; setup AP %s %s' % (S.AP_SSID, n.ap.ifconfig()[0])
    if n.ntp_at is not None:
        inet = 'OK (NTP %s ago)' % dur(uptime() - n.ntp_at)
    else:
        inet = 'no' + (' (%s)' % esc(n.ntp_err) if n.ntp_err else '')
    out.append('<dl class="sys"><dt>WiFi</dt><dd>%s</dd><dt>Internet</dt><dd>%s</dd>'
               '<dt>Clock</dt><dd>%s %s (%s)</dd><dt>Heap</dt><dd>%s free, lowest %s</dd>'
               '<dt>Flash</dt><dd>%s free</dd><dt>Uptime</dt><dd>%s</dd>%s</dl>'
               % (wifi, inet, stamp() if time_ok() else 'not set', S.TZ_LABEL, clock_src,
                  kb(gc.mem_free()), kb(heap_min), kb(flash_free()), dur(uptime()),
                  ('<dt>Power</dt><dd>%s</dd>' % ' &middot; '.join(pwr.status()) if pwr else '') +
                  '<dt>MQTT</dt><dd>%s</dd>' % (esc(mqtt.status()) if mqtt else 'off') +
                  ('<dt>GSM</dt><dd>%s</dd>' % gsm.status() if gsm else '')))
    return ''.join(out)


def ui(name, fn, *args):
    """Call fn(*args) from module `name`, imported for this request only and then released (RAM)."""
    import sys
    gc.collect()
    m = __import__(name)
    try:
        return getattr(m, fn)(*args)
    finally:
        m = None
        del sys.modules[name]


# ---------------------------------------------------------------- HTTP
conns = 0


async def send(w, body, ctype=HTML, status='200 OK', extra=''):
    if isinstance(body, list):              # parts streamed one by one: no big joined copy in RAM
        w.write(('HTTP/1.1 %s\r\nContent-Type: %s\r\nCache-Control: no-store\r\nConnection: close\r\n%s\r\n'
                 % (status, ctype, extra)).encode())
        for i, p in enumerate(body):
            w.write(p.encode())
            if i % 8 == 7:
                await w.drain()
        await w.drain()
        return
    if isinstance(body, str):
        body = body.encode()
    w.write(('HTTP/1.1 %s\r\nContent-Type: %s\r\nContent-Length: %d\r\n'
             'Cache-Control: no-store\r\nConnection: close\r\n%s\r\n' % (status, ctype, len(body), extra)).encode())
    w.write(body)
    await w.drain()


async def send_file(w, path, ctype, extra='', cache='no-store'):
    try:
        size = os.stat(path)[6]
    except OSError:
        return await send(w, 'Not found', TXT, '404 Not Found')
    w.write(('HTTP/1.1 200 OK\r\nContent-Type: %s\r\nContent-Length: %d\r\n'
             'Cache-Control: %s\r\nConnection: close\r\n%s\r\n' % (ctype, size, cache, extra)).encode())
    await w.drain()
    buf = bytearray(512)
    with open(path, 'rb') as f:
        while True:
            n = f.readinto(buf)
            if not n:
                break
            w.write(buf if n == 512 else buf[:n])   # stream copies on partial write
            await w.drain()


STATIC = {
    '/': ('index.html.gz', HTML, 'no-store'),
    '/htmx.min.js': ('htmx.min.js.gz', 'application/javascript', 'max-age=604800'),
    '/alpine.min.js': ('alpine.min.js.gz', 'application/javascript', 'max-age=604800'),
    '/setup': ('setup.html.gz', HTML, 'no-store'),
}
POLLS = ('/live', '/status', '/tail', '/files', '/wifi/state', '/favicon.ico', '/htmx.min.js', '/alpine.min.js')
KNOWN = ('/live', '/status', '/tail', '/files', '/dl', '/wifi/', '/rec/', '/time', '/favicon', '/bands', '/leds', '/power',
         '/pump', '/mqtt', '/batt', '/sensors', '/rgb', '/gsm')
CAPTIVE = ('/generate_204', '/gen_204', '/hotspot-detect.html', '/library/test/success.html',
           '/ncsi.txt', '/connecttest.txt', '/redirect', '/success.txt', '/canonical.html')


def set_clock(unix):
    global clock_src
    tm = time.gmtime(unix - UNIX_OFF)
    machine.RTC().datetime((tm[0], tm[1], tm[2], tm[6], tm[3], tm[4], tm[5], 0))
    clock_src = 'browser'
    print('clock set from browser:', stamp())


async def route(w, method, path, q, f):
    if method == 'GET':
        if path == '/' and not net.sta.isconnected():
            path = '/setup'                     # no WiFi yet: open the WiFi manager
        elif path in CAPTIVE or (net.ap.active() and path not in STATIC and not any(path.startswith(k) for k in KNOWN)):
            return await send(w, b'', TXT, '302 Found', 'Location: http://%s/setup\r\n' % net.ap.ifconfig()[0])
        if path in ('/wifi/scan', '/wifi/state'):
            return await send(w, ui('ui_wifi', 'handle', method, path, q, f))
    st = STATIC.get(path)
    if st and method == 'GET':
        return await send_file(w, WWW + st[0], st[1], 'Content-Encoding: gzip\r\n', st[2])
    if path == '/live':
        return await send(w, frag_live())
    if path == '/status':
        return await send(w, frag_status())
    if path == '/tail':
        return await send(w, ui('ui_files', 'frag_tail'))
    if path == '/files':
        return await send(w, ui('ui_files', 'frag_files'))
    seg = path.split('/')[1]
    if seg in ('bands', 'leds', 'power', 'pump', 'mqtt', 'batt', 'sensors', 'rgb', 'gsm'):
        import sys
        name = {'pump': 'ui_pump', 'mqtt': 'ui_mqtt', 'batt': 'ui_batt', 'sensors': 'ui_sensors',
                'rgb': 'ui_rgb', 'gsm': 'ui_gsm'}.get(seg, 'ui_cfg')
        if path in ('/sensors/save', '/sensors/del', '/sensors/cap', '/sensors/cal'):
            name = 'ui_sensors_set'
        gc.collect()                           # settings panels: loaded per request, then released
        mod = __import__(name)
        try:
            html = mod.handle(method, path, f if method == 'POST' else q)
        finally:
            mod = None
            del sys.modules[name]
            # helper modules a panel imported only for its settings (gsm.py ~14 KB): released
            # again while their feature is off (module name -> global holding the running object)
            for m, o in (('gsm', 'gsm'), ('rgb', 'rgb'), ('mqtt', 'mqtt'), ('battery', 'batt'), ('leds', 'leds')):
                if m in sys.modules and globals()[o] is None:
                    del sys.modules[m]
        if html is not None:
            return await send(w, html)
    if path == '/dl':
        name = safe_csv(q.get('f'))
        if not name:
            return await send(w, 'Bad name', TXT, '400 Bad Request')
        return await send_file(w, S.DATA_DIR + '/' + name, 'text/csv',
                               'Content-Disposition: attachment; filename="%s"\r\n' % name)
    if path == '/favicon.ico':
        return await send(w, b'', TXT, '204 No Content')
    if method != 'POST':
        return await send(w, 'Not found', TXT, '404 Not Found')

    if path == '/rec/start':
        try:
            interval = int(f.get('interval', 60))
            hours = float(f.get('hours', 0))
        except ValueError:
            return await send(w, frag_status('Invalid interval or duration.'))
        if interval not in S.INTERVALS or not 0 <= hours <= S.MAX_HOURS:
            return await send(w, frag_status('Invalid interval or duration.'))
        msg = rec.start(interval, hours, clean(f.get('label', ''), 20))
        return await send(w, frag_status(msg) + ui('ui_files', 'files_oob'))
    if path == '/rec/stop':
        if rec.active or rec.resume:
            rec.stop()
        return await send(w, frag_status() + ui('ui_files', 'files_oob'))
    if path == '/files/delete':
        name = safe_csv(f.get('f'))
        if name and not (name == rec.fname and (rec.active or rec.resume)):
            try:
                os.remove(S.DATA_DIR + '/' + name)
                if name == rec.fname:
                    rec.fname = ''
            except OSError:
                pass
        return await send(w, ui('ui_files', 'frag_files'))
    if path == '/time':
        try:
            t = int(f.get('t', 0))
        except ValueError:
            t = 0
        if not time_ok() and t > 1735689600:      # only if NTP has not set it (>= 2025)
            set_clock(t)
        return await send(w, b'', TXT, '204 No Content')
    if path in ('/wifi/connect', '/wifi/forget'):
        return await send(w, ui('ui_wifi', 'handle', method, path, q, f))
    return await send(w, 'Not found', TXT, '404 Not Found')


async def handle(r, w):
    global conns
    if conns >= S.MAX_CONN:
        try:
            w.write(b'HTTP/1.1 503 Busy\r\nContent-Length: 0\r\nRetry-After: 1\r\nConnection: close\r\n\r\n')
            await w.drain()
        except Exception:
            pass
        try:
            w.close()
            await w.wait_closed()
        except Exception:
            pass
        return
    conns += 1
    try:
        line = await asyncio.wait_for(r.readline(), 5)
        parts = line.split()
        if len(line) > 512 or len(parts) < 2:
            return
        method, target = parts[0].decode(), parts[1].decode()
        clen = 0
        for _ in range(40):
            h = await asyncio.wait_for(r.readline(), 5)
            if not h or h == b'\r\n':
                break
            if len(h) > 1024:
                return
            if h[:15].lower() == b'content-length:':
                clen = int(h[15:].strip())
        body = ''
        if clen:
            if clen > S.MAX_BODY:
                return await send(w, 'Too large', TXT, '413 Payload Too Large')
            body = (await asyncio.wait_for(r.readexactly(clen), 5)).decode()
        path, _, qs = target.partition('?')
        if pwr and (method == 'POST' or path not in POLLS):
            pwr.activity()                  # real use keeps a sleep-cycle board awake; polling does not
        await route(w, method, path, parse_qs(qs), parse_qs(body))
    except MemoryError:
        gc.collect()
        try:
            await send(w, 'Low memory, retry', TXT, '503 Service Unavailable')
        except Exception:
            pass
    except Exception as e:
        if not isinstance(e, (OSError, asyncio.TimeoutError)):
            print('http error:', repr(e))
    finally:
        conns -= 1
        try:
            w.close()
            await w.wait_closed()
        except Exception:
            pass
        gc.collect()
        heap_note()


# ---------------------------------------------------------------- main
sens = rec = net = bands = leds = batt = pwr = pump = mqtt = rgb = gsm = None


def restart_soon(secs=2):
    """Restart the board after the current reply was sent (sensor list changed: everything is
    rebuilt from the saved settings). The pump is switched off first."""
    async def _go():
        await asyncio.sleep(secs)
        try:
            _before_sleep()
        except Exception:
            pass
        machine.reset()
    asyncio.create_task(_go())


def gsm_start():
    """Start the modem task (boot, or enabled from the page)."""
    global gsm
    if gsm is None and getattr(S, 'MODEM_TX', None) is not None:
        from gsm import Gsm, load_cfg, sms_command
        gsm = Gsm(load_cfg(), sens)
        asyncio.create_task(gsm.run(sms_command))
        if mqtt:
            mqtt.gprs = gsm
    return gsm


def _online():
    """Internet for MQTT: WiFi, or GPRS when the GSM panel allows it."""
    w = net.sta.isconnected()
    return w or (gsm is not None and gsm.use_gprs(w))


def mqtt_start():
    """Start the MQTT task (boot, or enabled from the page). Module not loaded while MQTT is off."""
    global mqtt
    if mqtt is None:
        from mqtt import Mqtt
        mqtt = Mqtt(uptime)
        mqtt.gprs, mqtt.wifi = gsm, net.sta.isconnected
        if pump:
            pump.on_event = mqtt.event
        asyncio.create_task(mqtt.run(None, _online))      # mqtt.snapshot() builds the JSON
    return mqtt


def _gsm_enabled():
    try:
        with open(S.DATA_DIR + '/gsm.json') as f:
            return bool(json.load(f).get('on'))
    except (OSError, ValueError):
        return False


def _mqtt_enabled():
    """Saved page setting wins; without one, the settings.py template decides."""
    try:
        with open(S.DATA_DIR + '/mqtt.json') as f:
            return bool(json.load(f).get('on'))
    except (OSError, ValueError):
        return bool(getattr(S, 'MQTT_ENABLED', False))


async def sample_loop():
    batt_next = 0
    while True:
        try:
            await sens.sample()
            pcts = [sens.pct(sens.raw[i], c[2], c[3]) for i, c in enumerate(sens.ch)]
            bands.update(pcts)
            if pump:
                pump.update(pcts)
            if mqtt:
                mqtt.feed(sens.raw, sens.t, sens.rh, sens.p, sens.gval)
            if gsm and gsm.net and uptime() % 30 < S.SAMPLE_S:
                gsm.check(pcts)
            if leds:
                i = min(S.LED_SENSOR, len(pcts) - 1)
                leds.show(bands.band(i, pcts[i]))
            if rgb:
                rgb.show([bands.band(i, p) for i, p in enumerate(pcts)], bool(pump and pump.cfg['fault']))
            if batt and uptime() >= batt_next:
                batt_next = uptime() + S.BATT_EVERY_S
                batt.read()
                if leds:
                    leds.batt = batt.state
        except Exception as e:
            print('sample error:', e)
        gc.collect()
        heap_note()
        await asyncio.sleep(S.SAMPLE_S)


async def _main():
    asyncio.create_task(net.run())
    asyncio.create_task(rec.run())
    asyncio.create_task(sample_loop())
    if leds:
        asyncio.create_task(leds.run())
    if rgb:
        asyncio.create_task(rgb.run())
    if pump:
        asyncio.create_task(pump.run())
    if pwr:
        asyncio.create_task(pwr.run(lambda: net.ap.active() or net.pending is not None or (pump is not None and pump.busy()),
                                    _before_sleep, lambda: batt is not None and batt.state != 'ok'))
    if _mqtt_enabled():
        mqtt_start()
    if _gsm_enabled():
        gsm_start()
    await asyncio.start_server(handle, '0.0.0.0', S.HTTP_PORT, backlog=5)
    print('http: listening on port', S.HTTP_PORT, '- free heap', gc.mem_free())
    while True:
        await asyncio.sleep(3600)


def _before_sleep():
    if rgb:
        rgb.off()
    if pump:
        pump.stop('going to sleep')
        pump._set(False)
    bands._persist()
    if leds:
        leds._set(None)


def batt_enabled():
    """Battery panel setting (batt.json) without importing battery.py; else the settings.py template."""
    try:
        with open(S.DATA_DIR + '/batt.json') as f:
            return bool(json.load(f).get('on'))
    except (OSError, ValueError):
        return getattr(S, 'BATT_PIN', None) is not None


def batt_apply():
    """(Re)create the monitor from the saved settings, or remove it when switched off."""
    global batt
    from battery import Battery, load_cfg
    c = load_cfg()
    batt = Battery(c) if c['on'] else None
    if batt:
        batt.read()
    if leds:
        leds.batt = batt.state if batt else 'ok'
    return batt


def rgb_apply(start_task=True):
    """RGB status light from rgb.json: create, reconfigure or switch off (module loaded only once used)."""
    global rgb
    from rgb import Rgb, load_cfg
    c = load_cfg()
    if rgb:
        rgb.reconfig(c)
    elif c['on']:
        rgb = Rgb(c)
        if start_task:
            asyncio.create_task(rgb.run())
    return rgb


def _rgb_enabled():
    try:
        with open(S.DATA_DIR + '/rgb.json') as f:
            return bool(json.load(f).get('on'))
    except (OSError, ValueError):
        return False


def leds_mode():
    """Saved LED mode (leds.json) without importing leds.py."""
    try:
        with open(S.DATA_DIR + '/leds.json') as f:
            return json.load(f).get('mode') or S.LED_MODE
    except (OSError, ValueError, AttributeError):
        return S.LED_MODE


def leds_start():
    """LEDs switched on from the page while not loaded."""
    global leds
    if leds is None and S.LED_PINS:
        from leds import Leds
        leds = Leds(S.LED_PINS, S.LED_ACTIVE_HIGH)
        if batt:
            leds.batt = batt.state
        asyncio.create_task(leds.run())
    return leds


def _optional():
    """Optional modules: not imported (no RAM) when not configured."""
    global leds, batt, pump, rgb
    if rgb is None and _rgb_enabled():
        rgb_apply(False)                     # task started in _main (or flash_once in a quick wake)
    if getattr(S, 'PUMP_PIN', None) is not None and pump is None:
        from pump import Pump                # drives the pump pin OFF first thing
        pump = Pump(uptime, time_ok)
    if S.LED_PINS and leds_mode() != 'off':     # LEDs off: module not loaded (RAM)
        from leds import Leds
        leds = Leds(S.LED_PINS, S.LED_ACTIVE_HIGH)
    if batt_enabled():                         # off: battery.py not loaded
        batt_apply()


def main():
    global sens, rec, net, bands, leds, batt, pwr, pump
    from bands import Bands
    if S.POWER_ENABLED and pwr is None:  # sets the CPU clock first
        from power import Power
        pwr = Power(uptime, time_ok)
    sens = Sensors()
    bands = Bands(len(sens.ch), uptime, time_ok)
    if leds is None and batt is None:
        _optional()
    if pump:
        pump.bands = bands
        pump.sens = sens                     # flow sensor protection / water statistics
    rec = Recorder(sens)
    net = Net()
    gc.collect()
    asyncio.run(_main())
