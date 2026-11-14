# Output controllers (feature 'control'): up to 3, each one GPIO driving a heater, a light (PWM dimmer)
# or a fan / cooler, following any sensor reading. Set up from the dashboard Control panel
# (DATA_DIR/control.json; an old heater.json is taken over). Not loaded while none is set up.
#
# Kinds: 'heater' and 'light' raise the reading (more output when it is below the setpoint), 'fan'
#        lowers it (more output when it is above).
# Modes: 'pid'    output 0-100 %, PID on the error: P = kp * e, I += ki * e * minutes,
#                 D = -kd * change per minute (on the measurement: no kick when the setpoint changes);
#                 the integral is clamped and frozen while the output is saturated (no windup)
#        'onoff'  thermostat / twilight switch: on below setpoint - hyst/2 (fan: above + hyst/2), off past
#                 the other side, at least min_s between switches - lamps, relays, anything without PWM
#        'manual' fixed output % (a light dimmer)          'off'
#        'sun'    daylight cycle (aquarium, terrarium, grow light): night level -> sunrise ramp -> day level
#                 -> sunset ramp -> night level; shape 'smooth' (S-curve), 'linear', or 'arc' (sine over the
#                 whole day, ramp unused); sunset before sunrise = a night-time light; needs the clock
#        'curve'  value -> output on a straight line through (x1, y1) and (x2, y2), clamped, e.g. a fan at
#                 30 % from 30 degC to 100 % at 45 degC (like a CPU cooler); 'stop' = off past x1 (by hyst)
# Fade: the PWM output glides at most 100 % per ramp_s seconds (no sudden jumps; soft start).
# Kick: a fan starting from standstill gets 1 s at 100 % first (fans stall at low duty).
# Output: 'pwm'    hardware PWM at freq Hz (MOSFET, LED driver, 4-pin fan); lights: optional gamma curve
#                  (dimming looks even to the eye) and a minimum level (drivers that flicker when low)
#         'window' on for out % of every window_s s (SSR / relay); pulses < min_on_s skipped
#         'onoff' mode always switches plainly (never PWM)
# Hours: 'HH:MM-HH:MM' = only active then (e.g. a grow light), '' = always.
# Safety (pid / onoff): no reading for 30 s -> output 0; reading past 'cut' (heater / light: at or above,
# fan: at or below) -> output 0 until it is back by 'cut_back'.
# Spec: docs/SPEC.md#controllers
try:
    import asyncio
except ImportError:
    import uasyncio as asyncio
import time
from machine import Pin, PWM

from control_cfg import UP, sources


from control_calc import sun_level, curve_level, _in_hours, _now_min

class Ctl:
    """One controller."""

    def __init__(self, c):
        """Controller from entry c; output pin set up and driven off (PWM unless on/off mode)."""
        self.c = c
        self.pin = self.pwm = None
        self.v = None                  # reading now
        self.v_at = time.ticks_ms()
        self.prev = None               # (ticks, value) for the derivative
        self.i = self.p = self.d = 0.0
        self.outp = 0.0                # output now, %
        self.state, self.fault = 'starting', ''
        self.sw_at = None
        self.cur = 0.0                 # output actually applied (after the fade)
        self.kick = 0                  # ticks of kick-start left
        self.was0 = True               # output was at 0 (a fan is standing still)
        if c['on'] and c['pin'] is not None:
            self.pin = Pin(c['pin'], Pin.OUT, value=1 if c['inv'] else 0)
            if c['out'] == 'pwm' and c['mode'] != 'onoff':
                self.pwm = PWM(self.pin, freq=max(1, min(40000, int(c['freq']))))
                self._duty(0)

    def release(self):
        """Output off and pin released (reconfigure, sleep, restart)."""
        if self.pwm:
            self._duty(0)
            self.pwm.deinit()
            self.pwm = None
        if self.pin:
            self.pin.value(1 if self.c['inv'] else 0)
            self.pin = None
        self.outp = 0.0

    def active(self):
        """Pin set up and not in off mode (keeps a sleep-cycle board awake)."""
        return self.pin is not None and self.c['mode'] != 'off'

    def _duty(self, pct):
        """Apply pct % as PWM duty: minimum level, gamma curve, 16-bit steps where available."""
        c = self.c
        if pct > 0:
            pct = max(pct, c['min_pct'])
            if c['gamma']:
                pct = 100 * (pct / 100) ** 2.2
        pct = max(0.0, min(100.0, pct))
        if hasattr(self.pwm, 'duty_u16'):                  # 16-bit steps: smooth dimming near dark
            d = int(pct * 65535 / 100)
            self.pwm.duty_u16(65535 - d if c['inv'] else d)
        else:
            d = int(pct * 1023 / 100)
            self.pwm.duty(1023 - d if c['inv'] else d)

    def _set(self, on):
        """Switch the output pin on / off (respecting active-low)."""
        self.pin.value((0 if on else 1) if self.c['inv'] else (1 if on else 0))

    def read(self):
        """Current value of the followed sensor ('air', 'air.rh' or '<id>.<channel>'), or None."""
        import app
        s, src = app.sens, self.c['src']
        if src == 'air':
            return s.t
        if src == 'air.rh':
            return s.rh
        sid, _, key = src.partition('.')
        for j, g in enumerate(s.gch):
            if g[6] == sid and g[2] == key:
                return s.gval[j]
        return None

    def src(self):
        """(label, unit) of the source."""
        if not self.c['src']:
            return ('clock (sun cycle)' if self.c['mode'] == 'sun' else 'none (manual)'), ''
        for k, lbl, u in sources():
            if k == self.c['src']:
                return lbl, u
        return (self.c['src'] or 'no sensor') + ' (missing)', ''

    def step(self):
        """Every ~2 s: new reading -> output %."""
        c = self.c
        v = self.read() if c['src'] else None
        now = time.ticks_ms()
        if v is not None:
            self.v, self.v_at = v, now
        elif time.ticks_diff(now, self.v_at) > 30000:
            self.v = None
        up = c['kind'] in UP
        if c['mode'] == 'off':
            self.outp, self.state, self.fault = 0.0, 'off', ''
            return
        if c['mode'] == 'sun':                             # daylight cycle: needs the clock, no sensor
            from app import time_ok
            if not time_ok():
                self.outp, self.state, self.fault = float(c['night']), 'clock not set', ''
                return
            lv, ph = sun_level(c, _now_min())
            self.outp, self.state, self.fault = min(lv, float(c['max_pct'])), ph, ''
            return
        if c['hours'] and not _in_hours(c['hours']):
            self.outp, self.state, self.fault = 0.0, 'outside hours', ''
            self.i, self.prev = 0.0, None
            return
        mx = float(max(0, min(100, c['max_pct'])))
        if c['mode'] == 'manual':                          # needs no reading (dimmer)
            self.outp, self.state, self.fault = min(float(c['man']), mx), 'manual', ''
            return
        if self.v is None:
            self.outp, self.state, self.fault = 0.0, 'no reading', 'no reading from the sensor for 30 s'
            self.prev = None
            return
        v, cut = self.v, c['cut']
        if cut is not None:
            past = v >= cut if up else v <= cut
            back = v > cut - c['cut_back'] if up else v < cut + c['cut_back']
            if past or (self.fault.startswith('cut-off') and back):
                self.outp, self.state, self.i = 0.0, 'cut-off', 0.0
                self.fault = 'cut-off: %g (limit %g)' % (round(v, 1), cut)
                return
        self.fault = ''
        if c['mode'] == 'curve':
            self.outp = min(curve_level(c, v, self.outp), mx)
            self.state = 'on' if self.outp > 0 else 'stopped'
            return
        sp, h = c['sp'], max(0.0, c['hyst']) / 2
        if c['mode'] == 'onoff':
            low, high = v < sp - h, v > sp + h
            want = (100.0 if low else 0.0 if high else self.outp) if up else (100.0 if high else 0.0 if low else self.outp)
            if want != self.outp and (self.sw_at is None or time.ticks_diff(now, self.sw_at) >= c['min_s'] * 1000):
                self.outp, self.sw_at = min(want, mx), now
            self.state = 'on' if self.outp else 'holding'
            return
        e = (sp - v) if up else (v - sp)
        dt = time.ticks_diff(now, self.prev[0]) / 60000 if self.prev else 0     # minutes
        rate = (v - self.prev[1]) / dt if dt > 0 else 0.0
        self.prev = (now, v)
        self.p = c['kp'] * e
        self.d = -c['kd'] * rate if up else c['kd'] * rate
        u = self.p + self.i + self.d
        if (u < mx or e < 0) and (u > 0 or e > 0):          # integrate only when not pushing a limit
            self.i = max(0.0, min(mx, self.i + c['ki'] * e * dt))
        self.outp = max(0.0, min(mx, self.p + self.i + self.d))
        self.state = 'on' if self.outp >= 1 else 'holding' if e > -h - 0.5 else 'idle'

    def tick(self, ms):
        """Output stage, every 0.2 s."""
        c = self.c
        if self.pwm:
            tgt = self.outp
            if c['ramp_s'] > 0:                            # fade: at most 100 % per ramp_s seconds
                stp = 20.0 / c['ramp_s']                   # 0.2 s ticks
                self.cur = min(tgt, self.cur + stp) if tgt > self.cur else max(tgt, self.cur - stp)
            else:
                self.cur = tgt
            if c['kick'] and self.was0 and self.cur > 0:
                self.kick = 5                              # 1 s at full power from standstill
            self.was0 = self.cur <= 0
            if self.kick:
                self.kick -= 1
                self._duty(100)
            else:
                self._duty(self.cur)
        elif self.pin is None:
            return
        elif c['mode'] == 'onoff':
            self._set(self.outp > 0)
        else:
            w = max(2, c['window_s'])
            on_t = w * self.outp / 100
            if on_t < c['min_on_s']:
                on_t = 0
            elif w - on_t < c['min_on_s']:
                on_t = w
            self._set((ms / 1000) % w < on_t)

    # ---------------------------------------------------------------- display
    def card(self):
        """Dashboard card (control_view.py)."""
        from control_view import card
        return card(self)

    def mqtt(self):
        """Values for the MQTT state message."""
        c = self.c
        return {'name': c['name'], 'kind': c['kind'], 'value': None if self.v is None else round(self.v, 2),
                'setpoint': c['sp'], 'out_pct': round(self.outp, 1), 'mode': c['mode'], 'state': self.state,
                'fault': self.fault or None, 'p': round(self.p, 1), 'i': round(self.i, 1), 'd': round(self.d, 1)}


class Controls:
    """All controllers, one task."""

    def __init__(self, cfg):
        """All enabled controllers of cfg."""
        self.ctls = []
        self.reconfig(cfg)

    def reconfig(self, cfg):
        """Release every output and rebuild from cfg (only enabled controllers run)."""
        for k in self.ctls:
            k.release()
        self.ctls = [Ctl(e) for e in cfg['list'] if e['on']]

    def off(self):
        """All outputs off."""
        for k in self.ctls:
            k.release()

    def active(self):
        """True if any controller is active."""
        return any(k.active() for k in self.ctls)

    def get(self, key):
        """By id, name (any case) or kind (first of that kind)."""
        key = key.lower()
        for k in self.ctls:
            if k.c['id'].lower() == key or k.c['name'].lower() == key:
                return k
        for k in self.ctls:
            if k.c['kind'] == key:
                return k
        return None

    def card(self):
        """Dashboard cards of all controllers."""
        return ''.join(k.card() for k in self.ctls)

    def mqtt(self):
        """MQTT values keyed by controller id."""
        return dict((k.c['id'], k.mqtt()) for k in self.ctls)

    async def run(self):
        """Task: output stage every 0.2 s, control step every 2 s; ends when none is left."""
        n = 0
        while self.ctls:                                   # all removed: task ends (module unloaded)
            ms = time.ticks_ms()
            for k in self.ctls:
                if n % 10 == 0:
                    try:
                        k.step()
                    except Exception as ex:
                        k.outp, k.fault = 0.0, repr(ex)
                try:
                    k.tick(ms)
                except Exception:
                    pass
            n += 1
            await asyncio.sleep_ms(200)
