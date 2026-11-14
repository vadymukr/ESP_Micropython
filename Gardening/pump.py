# Water pump control for the garden logger (MicroPython ESP32). Loaded only if settings.PUMP_PIN is set.
#
# One GPIO drives the pump relay / MOSFET. Every run is a short pulse that always switches off by
# itself; afterwards the soil sensors are watched for SOAK minutes to see the water arrive:
#
#   manual    "Run N s" button on the page (Stop button switches off at once)
#   schedule  fixed local times, e.g. "06:30,18:00", N s each (optionally skipped if the soil is moist)
#   auto      when a watched sensor drops to the "water" level (F - w): pulse, soak, check, repeat
#             until the reading is back near F (target), at most `max_pulses` per session
#
# Dry-run protection: if a run of >= PUMP_CHECK_MIN_S gives no rise of >= PUMP_RISE points on any
# watched sensor within the soak time, the tank is probably empty (or the pipe blocked / pump dead).
# Then schedule + auto are locked and the page shows an alert until "Reset" (after refilling) or a
# manual run that does raise the moisture. The lock survives restarts. Optional float switch
# (TANK_PIN) blocks every run while the tank reads empty. Daily cap PUMP_DAY_MAX_S on all runs.
#
# Safety: the pin is driven "off" before anything else, every loop error switches the pump off, and
# the app never deep-sleeps while the pump runs or a check is pending (busy()).
try:
    import asyncio
except ImportError:
    import uasyncio as asyncio
import json
import os
import time
from machine import Pin
import settings as S

CFG = S.DATA_DIR + '/pump.json'
LOG = S.DATA_DIR + '/pump.log'
LOG_MAX = 8 * 1024             # pump.log is renamed to pump.old at this size
MODES = ('manual', 'schedule', 'auto')
DEFAULTS = {'mode': 'manual', 'man_s': 15, 'times': '06:30', 'sched_s': 20, 'skip_moist': True,
            'pulse_s': 15, 'soak_min': 10, 'max_pulses': 4, 'hours': '05:30-09:00', 'target': 3,
            'sensors': [0], 'fault': '', 'day': '', 'day_s': 0, 'done': '',
            # optional flow sensor protection + water statistics
            'flow': '', 'flow_min': 0.2, 'flow_wait': 8, 'rate_lpm': 0.0,
            'l_day': 0.0, 'l_total': 0.0, 'runs_day': 0, 'hist': []}
FLOW_TYPES = ('flow', 'yfs401')
HIST_DAYS = 14


def _save(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(obj, f)
    os.rename(tmp, path)


def parse_times(s):
    """'06:30,18:00' -> [390, 1080] minutes of the day; bad parts ignored."""
    out = []
    for p in s.replace(' ', '').split(','):
        try:
            m = int(p[:2]) * 60 + int(p[3:5])
            if len(p) == 5 and p[2] == ':' and 0 <= m < 1440:
                out.append(m)
        except ValueError:
            pass
    return out


def parse_hours(s):
    """'05:30-09:00' -> (330, 540), '' -> None (any time), bad -> False."""
    s = s.replace(' ', '')
    if not s:
        return None
    a, _, b = s.partition('-')
    t = parse_times(a + ',' + b)
    return (t[0], t[1]) if len(t) == 2 and t[0] != t[1] else False


class Pump:
    def __init__(self, uptime, time_ok):
        self.on_lvl = 1 if S.PUMP_ACTIVE_HIGH else 0
        self.pin = Pin(S.PUMP_PIN, Pin.OUT, value=1 - self.on_lvl)     # off first, before anything else
        self.tank = None
        if S.TANK_PIN is not None:
            self.tank = Pin(S.TANK_PIN, Pin.IN, Pin.PULL_UP)
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
        self.running = False
        self.run_end = 0               # uptime() when the current run stops
        self.run_t0 = 0
        self.why = ''                  # 'manual' | 'schedule' | 'auto'
        self.check_end = None          # uptime() when the soak / check window ends
        self.check_len = 0             # seconds the pump ran in the run being checked
        self.base = None               # smoothed readings at pump start (per watched sensor)
        self.peak = None
        self.rose = False
        self.pulses = 0                # pulses in the current auto session
        self.ema = None                # smoothed readings (~10 s), all sensors
        self.pcts = None
        self.bands = None              # set by the app: auto mode needs F and w per sensor
        self.msg = ''
        self.last_min = -1
        self.on_event = None           # callback(text) for every log line (MQTT)
        self.hold = 0                  # auto: no new attempt before this uptime() (after a refusal)
        self.sens = None               # set by the app: registry drivers (flow sensor)
        self.fd = None                 # flow driver watched during the current run
        self.n0 = 0                    # its pulse count at pump start
        self.fq = []                   # recent (uptime, pulses) for the live flow rate
        self.lpm = None                # live flow during the run, L/min
        self.flow_used = False         # last run was judged by flow (not by moisture)

    # ---------------------------------------------------------------- config / state
    def save(self, **kw):
        self.cfg.update(kw)
        try:
            _save(CFG, self.cfg)
        except OSError as e:
            print('pump: save failed', e)

    def log(self, text):
        line = '%s %s' % (_stamp() if self.time_ok() else 'up %ds' % self.up(), text)
        print('pump:', line)
        if self.on_event:
            try:
                self.on_event(line)
            except Exception:
                pass
        try:
            if os.stat(LOG)[6] > LOG_MAX:
                os.rename(LOG, S.DATA_DIR + '/pump.old')
        except OSError:
            pass
        try:
            with open(LOG, 'a') as f:
                f.write(line + '\n')
        except OSError:
            pass

    def watched(self):
        n = len(self.ema or ())
        w = [i for i in self.cfg['sensors'] if 0 <= i < n]
        return w or list(range(n))

    def tank_empty(self):
        return self.tank is not None and self.tank.value() == S.TANK_EMPTY

    def busy(self):
        return self.running or self.check_end is not None

    def _day(self):
        """New local day: yesterday goes into the history, daily totals restart (needs the clock)."""
        c = self.cfg
        if self.time_ok():
            d = _stamp()[:10]
            if d != c['day']:
                if c['day'] and (c['day_s'] or c['l_day']):
                    c['hist'] = (c['hist'] + [[c['day'], round(c['l_day'], 3), c['day_s'], c['runs_day']]])[-HIST_DAYS:]
                c['day'], c['day_s'], c['done'], c['l_day'], c['runs_day'] = d, 0, '', 0.0, 0
        return S.PUMP_DAY_MAX_S - c['day_s']

    def flow_drv(self):
        """The linked flow sensor's driver, or None (not linked / not running)."""
        fid = self.cfg['flow']
        if not fid or not self.sens:
            return None
        for d in self.sens.gen:
            if d.e['id'] == fid and hasattr(d, 'n'):
                return d
        return None

    def _litres(self, d, n):
        return n / (float(d.e.get('ppl', 450)) or 450.0)

    # ---------------------------------------------------------------- switching
    def _set(self, on):
        self.pin.value(self.on_lvl if on else 1 - self.on_lvl)

    def start(self, secs, why):
        """Returns '' or the reason it did not start."""
        if self.running:
            return 'Pump is already running.'
        if self.ema is None:
            return 'No sensor reading yet - try again in a few seconds.'
        if self.tank_empty():
            return 'Tank float switch reads EMPTY - refill the tank first.'
        if self.cfg['fault'] and why != 'manual':
            return 'Locked after a dry run - check the tank, then press Reset.'
        left = self._day()
        secs = min(int(secs), S.PUMP_MAX_S, left)
        if secs < 1:
            return 'Daily limit of %d s reached.' % S.PUMP_DAY_MAX_S
        w = self.watched()
        self.base = [self.ema[i] for i in w]
        self.peak = list(self.base)
        self.rose = False
        self.running, self.why = True, why
        self.run_t0 = self.up()
        self.run_end = self.run_t0 + secs
        self.check_end = None
        self.fd = self.flow_drv()
        if self.cfg['flow'] and not self.fd:
            self.log('flow sensor %s not running - using the moisture check' % self.cfg['flow'])
        if self.fd:
            self.n0 = self.fd.n
            self.fq = [(self.run_t0, self.n0)]
        self.lpm = None
        self.cfg['runs_day'] += 1
        self._set(True)
        self.log('ON %d s (%s) | %s' % (secs, why, self._vals()))
        return ''

    def stop(self, note='done'):
        if not self.running:
            self._set(False)
            return
        self._set(False)
        self.running = False
        ran = max(0, self.up() - self.run_t0)
        c = self.cfg
        c['day_s'] += ran
        d, lit, how = self.fd, None, ''
        if d:                                     # measured
            lit, how = self._litres(d, d.n - self.n0), 'measured'
        elif c['rate_lpm'] > 0:                   # estimated from the pump rate
            lit, how = ran * c['rate_lpm'] / 60, 'estimated'
        if lit is not None:
            c['l_day'] += lit
            c['l_total'] += lit
        self.flow_used = d is not None
        if d and c['fault'] and note != 'no flow' and lit >= 0.02:
            c['fault'] = ''                       # water flows again: lock cleared
            self.log('water flowed - dry-run lock cleared')
        self.fd = None
        self.save()
        self.check_len = ran
        self.check_end = self.up() + max(1, c['soak_min']) * 60
        self.log('OFF after %d s (%s)%s - watching moisture for %d min'
                 % (ran, note, '' if lit is None else ', %.3f L %s' % (lit, how), c['soak_min']))

    def reset_fault(self):
        self.save(fault='')
        self.msg = ''
        self.log('fault reset by user')

    # ---------------------------------------------------------------- per sample (every ~2 s)
    def update(self, pcts):
        self.pcts = pcts
        if self.ema is None:
            self.ema = list(pcts)
        else:
            self.ema = [e + 0.2 * (p - e) for e, p in zip(self.ema, pcts)]
        if self.base is not None and self.peak is not None:
            for k, i in enumerate(self.watched()):
                if k < len(self.peak) and self.ema[i] > self.peak[k]:
                    self.peak[k] = self.ema[i]
            if not self.rose and any(p - b >= S.PUMP_RISE for p, b in zip(self.peak, self.base)):
                self.rose = True

    def _vals(self):
        if not self.ema:
            return '-'
        return ' '.join('%.1f%%' % self.ema[i] for i in self.watched())

    def _evaluate(self):
        """Soak window over: did the water arrive?"""
        self.check_end = None
        rise = max(p - b for p, b in zip(self.peak, self.base)) if self.base else 0
        self.base = self.peak = None
        if self.flow_used:                        # flow sensor judged the run: moisture is information only
            self.log('moisture after watering: %+.1f points | %s' % (rise, self._vals()))
            return not self.cfg['fault']
        if self.rose:
            if self.cfg['fault']:
                self.save(fault='')
                self.log('moisture rose again - dry-run lock cleared')
            self.log('check OK: +%.1f points | %s' % (rise, self._vals()))
            return True
        if self.check_len < S.PUMP_CHECK_MIN_S:
            self.log('check skipped: run of %d s is too short to judge (+%.1f)' % (self.check_len, rise))
            return True
        f = ('no moisture rise after %d s of pumping (+%.1f points): tank empty, pipe blocked or '
             'pump failed?' % (self.check_len, rise))
        self.save(fault=f)
        self.pulses = 0
        self.log('DRY RUN: ' + f)
        return False

    def _need(self):
        """Auto: (needs water, reached target) from the watched sensors vs their F and w."""
        b = self.bands
        if b is None or self.ema is None:
            return False, True
        w = b.w()
        low = any(self.ema[i] <= b.full[i] - w for i in self.watched())
        full = any(self.ema[i] >= b.full[i] - self.cfg['target'] for i in self.watched())
        return low, full

    def _moist(self):
        """Schedule: skip when every watched sensor is already in the good band or wetter."""
        b = self.bands
        if b is None or self.ema is None:
            return False
        return all(self.ema[i] >= b.full[i] - b.w() for i in self.watched())

    # ---------------------------------------------------------------- loop (1 s)
    def _tick(self):
        now = self.up()
        if now % 60 == 0:
            self._day()                           # midnight rollover also on days without a run
        if self.running:
            if self.tank_empty():
                self.stop('tank float switch EMPTY')
                self.save(fault='tank float switch reads empty')
            elif now >= self.run_end:
                self.stop()
            elif self.fd:
                self._flow_check(now)
            return
        if self.check_end is not None:
            if self.rose and self.why != 'auto':
                self._evaluate()                  # water arrived: done early (lets the board sleep)
            elif now >= self.check_end:
                ok = self._evaluate()
                if ok and self.why == 'auto':
                    self._auto_next()
            return
        c = self.cfg
        if c['fault'] or not self.time_ok():
            return
        lt = time.localtime(time.time() + S.TZ_OFFSET_H * 3600)
        m = lt[3] * 60 + lt[4]
        if c['mode'] == 'schedule' and m != self.last_min and m in parse_times(c['times']):
            self.last_min = m
            key = '%02d:%02d' % (lt[3], lt[4])
            self._day()
            if key in c['done'].split(','):
                return                            # already ran at this time today (restart)
            self.save(done=(c['done'] + ',' + key).strip(','))
            if c['skip_moist'] and self._moist():
                self.log('schedule %s skipped: soil already moist | %s' % (key, self._vals()))
            else:
                self.msg = self.start(c['sched_s'], 'schedule')
                if self.msg:
                    self.log('schedule %s not run: %s' % (key, self.msg))
        elif c['mode'] == 'auto':
            h = parse_hours(c['hours'])
            if h is False or (h and not ((h[0] <= m < h[1]) if h[0] < h[1] else (m >= h[0] or m < h[1]))):
                return
            low, _ = self._need()
            if low and now >= self.hold:
                self.pulses = 0
                self._auto_next()

    def _flow_check(self, now):
        """Live flow rate over the last ~3 s; no flow after the grace time = stop and lock."""
        d, c = self.fd, self.cfg
        self.fq.append((now, d.n))
        while len(self.fq) > 2 and now - self.fq[0][0] > 3:
            self.fq.pop(0)
        dt = self.fq[-1][0] - self.fq[0][0]
        if dt > 0:
            self.lpm = self._litres(d, self.fq[-1][1] - self.fq[0][1]) * 60 / dt
        if now - self.run_t0 >= c['flow_wait'] and self.lpm is not None and self.lpm < c['flow_min']:
            f = ('no water flow (%.2f L/min) after %d s of pumping: tank empty, pipe blocked or pump failed?'
                 % (self.lpm, now - self.run_t0))
            self.stop('no flow')
            self.save(fault=f)
            self.pulses = 0
            self.log('DRY RUN: ' + f)

    def _auto_next(self):
        low, full = self._need()
        if full:
            self.log('auto: target reached after %d pulse(s) | %s' % (self.pulses, self._vals()))
            self.pulses = 0
            return
        if self.pulses >= self.cfg['max_pulses']:
            self.log('auto: stopped after %d pulses, target not reached | %s' % (self.pulses, self._vals()))
            self.pulses = 0
            self.save(fault='auto: %d pulses did not bring the soil back to F-%d - check the dripper '
                            'position and the tank' % (self.cfg['max_pulses'], self.cfg['target']))
            return
        self.pulses += 1
        self.msg = self.start(self.cfg['pulse_s'], 'auto')
        if self.msg:
            self.log('auto pulse not run: ' + self.msg)
            self.pulses = 0
            self.hold = self.up() + 1800          # retry in 30 min, not every second

    async def run(self):
        while True:
            try:
                self._tick()
            except Exception as e:
                self._set(False)
                self.running = False
                print('pump error, switched off:', repr(e))
            await asyncio.sleep(1)

    # ---------------------------------------------------------------- status for the page
    def state_text(self):
        if self.running:
            fl = ''
            if self.fd:
                fl = ' &middot; %s L/min &middot; %.3f L' % ('&ndash;' if self.lpm is None else '%.2f' % self.lpm,
                                                            self._litres(self.fd, self.fd.n - self.n0))
            return 'running (%s) &middot; off in %d s%s' % (self.why, max(0, self.run_end - self.up()), fl)
        if self.check_end is not None:
            rise = max(p - b for p, b in zip(self.peak, self.base)) if self.base else 0
            return 'watching moisture &middot; +%.1f points &middot; %d s left' % (rise, max(0, self.check_end - self.up()))
        if self.tank_empty():
            return 'tank EMPTY (float switch)'
        return 'off'


def _stamp():
    tm = time.localtime(time.time() + S.TZ_OFFSET_H * 3600)
    return '%04d-%02d-%02d %02d:%02d:%02d' % tm[:6]
