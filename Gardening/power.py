# Optional power management for the garden logger (MicroPython ESP32). Loaded only if
# settings.POWER_ENABLED. Every part can be switched on/off from the dashboard (DATA_DIR/power.json):
#
#   cpu         80 / 160 / 240 MHz (80 keeps WiFi working, ~10-15 mA less than 160)
#   sensor_pwr  feed the moisture sensors from SENSOR_PWR_PINS only while measuring
#   mode        'always' - normal, web always reachable
#               'cycle'  - deep sleep between measurements: wake every `every` min, measure, log a
#                          CSV row (if recording), update bands/trend, flash the LED, sleep again.
#                          The web page is only reachable while awake:
#                            * power-on / EN reset  -> awake `stay` min (at least 10)
#                            * BOOT button (WAKE_BUTTON) or touch pad (WAKE_TOUCH) -> awake `stay` min
#                            * awake windows, e.g. "07:00-07:15,19:00-19:15" (local time)
#                            * maintenance: "stay awake" switch on the page, up to MAINT_MAX_H hours
#                          Page requests extend the awake time by `stay` min (capped at MAX_AWAKE_MIN).
#                          On low battery the interval becomes `lowbatt_every`.
# Safety: any error in the quick (timer-wake) path starts the full app instead of sleeping, so the
# board stays reachable. Holding BOOT during an EN reset still skips the app (REPL only).
# Spec: docs/SPEC.md#power-saving
try:
    import asyncio
except ImportError:
    import uasyncio as asyncio
import json
import os
import time
import machine
from machine import Pin
import settings as S

CFG = S.DATA_DIR + '/power.json'
DEFAULTS = {'cpu': 160, 'sensor_pwr': False, 'mode': 'always', 'every': 10, 'stay': 10,
            'windows': '', 'lowbatt_every': 30, 'maint_until': 0, 'ntp_at': 0}
CPUS = getattr(S, 'CPUS', (80, 160, 240))       # C3 / C6: 80, 160 only
WAKE_NAMES = {}
for _k, _v in (('PIN_WAKE', 'button'), ('EXT0_WAKE', 'button'), ('TIMER_WAKE', 'timer'), ('TOUCHPAD_WAKE', 'touch')):
    if hasattr(machine, _k):                      # not every chip has every wake source
        WAKE_NAMES[getattr(machine, _k)] = _v


def local_min(t):
    """Local minute of the day of epoch time t."""
    tm = time.localtime(t + S.TZ_OFFSET_H * 3600)
    return tm[3] * 60 + tm[4]


def parse_windows(s):
    """'07:00-07:15,19:00-19:15' -> [(420, 435), (1140, 1155)] minutes of the day; bad parts ignored."""
    out = []
    for part in s.replace(' ', '').split(','):
        try:
            a, b = part.split('-')
            a = int(a[:2]) * 60 + int(a[3:5])
            b = int(b[:2]) * 60 + int(b[3:5])
            if 0 <= a < 1440 and 0 <= b <= 1440 and a != b:
                out.append((a, b))
        except (ValueError, IndexError):
            pass
    return out


class Power:
    """Power saving: CPU clock, sensor supply switching, deep-sleep cycle with awake windows."""
    def __init__(self, uptime, time_ok):
        """Settings from power.json; applies the CPU clock and notes the wake cause."""
        self.up, self.time_ok = uptime, time_ok
        self.cfg = dict(DEFAULTS)
        try:
            with open(CFG) as f:
                c = json.load(f)
            for k in DEFAULTS:
                if k in c:
                    self.cfg[k] = c[k]
        except (OSError, ValueError):
            pass
        self.wake = machine.wake_reason()
        self.deep = machine.reset_cause() == machine.DEEPSLEEP_RESET
        self.sp = [Pin(p, Pin.OUT, value=0) for p in S.SENSOR_PWR_PINS]
        self.apply_cpu()
        # awake budget for the full app (uptime seconds)
        first = 10 * 60 if not self.deep else 0           # power-on / reset: at least 10 min
        self.deadline = self.up() + max(first, self.cfg['stay'] * 60)
        self.cap = self.up() + S.MAX_AWAKE_MIN * 60
        self.going = False

    # ---------------------------------------------------------------- config
    def save(self, **kw):
        """Update and save power.json."""
        self.cfg.update(kw)
        tmp = CFG + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(self.cfg, f)
        os.rename(tmp, CFG)

    def apply_cpu(self):
        """Set the CPU frequency from the settings (if the chip supports it)."""
        mhz = self.cfg['cpu'] if self.cfg['cpu'] in CPUS else 160
        if machine.freq() != mhz * 1000000:
            try:
                machine.freq(mhz * 1000000)
            except ValueError:
                pass

    # ---------------------------------------------------------------- sensor power
    def sensor_switching(self):
        """True if the soil sensors are powered from GPIOs only while measuring."""
        return bool(self.sp) and self.cfg['sensor_pwr']

    def sensors(self, on):
        """Switch the sensor supply pins."""
        for p in self.sp:
            p.value(1 if on else 0)

    # ---------------------------------------------------------------- awake / sleep decisions
    def maint(self):
        """Maintenance mode active (stay awake until maint_until)."""
        return self.time_ok() and self.cfg['maint_until'] > time.time()

    def in_window(self):
        """Inside one of the daily awake windows."""
        if not self.time_ok():
            return False
        m = local_min(time.time())
        for a, b in parse_windows(self.cfg['windows']):
            if (a <= m < b) if a < b else (m >= a or m < b):
                return True
        return False

    def secs_to_window(self):
        """Seconds until the next awake window starts (None if none / clock not set)."""
        if not self.time_ok():
            return None
        t = time.time()
        m, s = local_min(t), time.localtime(t)[5]
        best = None
        for a, _ in parse_windows(self.cfg['windows']):
            d = ((a - m) % 1440) * 60 - s
            if d <= 0:
                d += 86400
            best = d if best is None or d < best else best
        return best

    def quick_ok(self):
        """Timer wake in cycle mode, outside windows/maintenance: measure-log-sleep without the web app."""
        return (self.cfg['mode'] == 'cycle' and self.deep and self.wake == machine.TIMER_WAKE
                and not self.maint() and not self.in_window())

    def activity(self):
        """Called for each HTTP request: stay awake a while longer (capped)."""
        if self.cfg['mode'] == 'cycle':
            self.deadline = min(max(self.deadline, self.up() + self.cfg['stay'] * 60), self.cap)

    def awake_left(self):
        """Seconds until the board may sleep again."""
        return max(0, self.deadline - self.up())

    def want_sleep(self):
        """Cycle mode, no window / maintenance, deadline passed: time to sleep."""
        return (self.cfg['mode'] == 'cycle' and not self.maint() and not self.in_window()
                and self.up() >= self.deadline)

    # ---------------------------------------------------------------- time sync
    def need_ntp(self):
        """Clock not set or the last NTP sync is older than NTP_EVERY_S."""
        return not self.time_ok() or time.time() - self.cfg['ntp_at'] > S.NTP_EVERY_S

    def mark_ntp(self):
        """Remember the time of a successful NTP sync."""
        if self.time_ok():
            self.cfg['ntp_at'] = time.time()
            self.save()

    def quick_ntp(self):
        """Join WiFi just long enough to set the clock (cycle mode, ~3-8 s)."""
        import network
        try:
            with open('wifi.json') as f:
                c = json.load(f)
        except (OSError, ValueError):
            return False
        sta = network.WLAN(network.STA_IF)
        sta.active(True)
        try:
            sta.connect(c['ssid'], c.get('password', ''))
            t0 = time.ticks_ms()
            while not sta.isconnected() and time.ticks_diff(time.ticks_ms(), t0) < 15000:
                time.sleep_ms(200)
            if sta.isconnected():
                import ntptime
                ntptime.host = S.NTP_HOST
                ntptime.settime()
                self.mark_ntp()
                return True
        except Exception as e:
            print('power: ntp failed', e)
        finally:
            sta.active(False)
        return False

    # ---------------------------------------------------------------- deep sleep
    def sleep_secs(self, low_batt=False, quick=False):
        """Seconds to sleep (low-battery interval if low_batt; steady cadence after a quick wake)."""
        every = (self.cfg['lowbatt_every'] if low_batt else self.cfg['every']) * 60
        s = max(30, every - self.up()) if quick else every       # quick wake: keep a steady cadence
        w = self.secs_to_window()
        return min(s, w) if w is not None and w > 0 else s

    def deepsleep(self, secs):
        """Outputs off, wake sources set (timer, button, touch), deep sleep for secs."""
        import esp32
        self.sensors(False)
        if S.WAKE_BUTTON is not None:
            try:
                esp32.wake_on_ext0(pin=Pin(S.WAKE_BUTTON, Pin.IN, Pin.PULL_UP), level=esp32.WAKEUP_ALL_LOW)
            except (AttributeError, ValueError, OSError):
                print('power: button wake n/a on this chip (timer wake only)')
        if S.WAKE_TOUCH is not None:
            try:
                t = machine.TouchPad(Pin(S.WAKE_TOUCH))
                t.config(int(t.read() * 2 // 3))           # touch drops the reading by > 1/3
                esp32.wake_on_touch(True)
            except Exception as e:
                print('power: touch wake n/a', e)
        print('power: deep sleep %d s' % secs)
        time.sleep_ms(50)                                  # let the UART flush
        machine.deepsleep(int(secs * 1000))

    async def run(self, busy, before_sleep, low_batt):
        """Full app: go to sleep when the awake time is over (cycle mode)."""
        while True:
            await asyncio.sleep(5)
            if self.want_sleep() and not busy():
                self.going = True
                await asyncio.sleep(2)                     # last page update shows "sleeping"
                try:
                    before_sleep()
                except Exception as e:
                    print('power: before_sleep', e)
                self.deepsleep(self.sleep_secs(low_batt()))

    def status(self):
        """Status parts for the System facts (MHz, mode, next sleep / window)."""
        c = self.cfg
        parts = ['%d MHz' % (machine.freq() // 1000000)]
        if self.sensor_switching():
            parts.append('sensors powered only while measuring')
        if c['mode'] != 'cycle':
            parts.insert(0, 'Always on')
            return parts
        parts.insert(0, 'Sleep cycle: every %d min' % c['every'])
        if self.deep:
            parts.append('woke by %s' % WAKE_NAMES.get(self.wake, 'reset'))
        if self.maint():
            parts.append('maintenance until %s' % _hm(c['maint_until']))
        elif self.in_window():
            parts.append('in awake window')
        elif self.going:
            parts.append('going to sleep now')
        else:
            parts.append('sleeps in %s unless the page is used' % _dur(self.awake_left()))
        w = self.secs_to_window()
        if w is not None and not self.in_window():
            parts.append('next window in %s' % _dur(w))
        return parts


def _hm(t):
    """Epoch time as local HH:MM."""
    tm = time.localtime(t + S.TZ_OFFSET_H * 3600)
    return '%02d:%02d' % (tm[3], tm[4])


def _dur(s):
    """Human duration."""
    s = int(s)
    return '%d s' % s if s < 120 else '%d min' % (s // 60) if s < 7200 else '%d h %02d min' % (s // 3600, s % 3600 // 60)
