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
# Spec: docs/SPEC.md#pump
try:
    import asyncio
except ImportError:
    import uasyncio as asyncio
import json
import os
import time
from machine import Pin
import settings as S

from pump_cfg import CFG, LOG, LOG_MAX, DEFAULTS, HIST_DAYS, _save, parse_times, parse_hours, _stamp
from pump_check import Checks

class Pump(Checks):
    """The pump: start / stop with all limits, soak-and-check after each run, schedule and auto mode."""

    def __init__(self, uptime, time_ok):
        """Pin driven OFF first; settings from pump.json; optional tank float switch."""
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
        """Update and save pump.json."""
        self.cfg.update(kw)
        try:
            _save(CFG, self.cfg)
        except OSError as e:
            print('pump: save failed', e)

    def log(self, text):
        """Append a line to pump.log (rotated at LOG_MAX) and pass it to MQTT."""
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
        """Indexes of the soil sensors that decide auto mode and the dry-run check."""
        n = len(self.ema or ())
        w = [i for i in self.cfg['sensors'] if 0 <= i < n]
        return w or list(range(n))

    def tank_empty(self):
        """True if the float switch (TANK_PIN) reads empty."""
        return self.tank is not None and self.tank.value() == S.TANK_EMPTY

    def busy(self):
        """Running or a soak check pending (keeps a sleep-cycle board awake)."""
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

    # ---------------------------------------------------------------- switching
    def _set(self, on):
        """Switch the pump pin (respecting PUMP_ACTIVE_HIGH)."""
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
        """Switch off; counts seconds and litres; starts the soak-and-check window."""
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
        """Clear the dry-run lock (tank refilled)."""
        self.save(fault='')
        self.msg = ''
        self.log('fault reset by user')

    # ---------------------------------------------------------------- per sample (every ~2 s)
    def update(self, pcts):
        """Every sample: smoothed readings and the peak since the last start."""
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
        """Smoothed readings of the watched sensors for log lines."""
        if not self.ema:
            return '-'
        return ' '.join('%.1f%%' % self.ema[i] for i in self.watched())

    # ---------------------------------------------------------------- loop (1 s)
    def _tick(self):
        """Every second: end of run, flow check, soak end, schedule and auto decisions."""
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

    async def run(self):
        """Task: _tick() every second; any error switches the pump off."""
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
        """Short state for the card and panel (running, watching, tank empty, off)."""
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


