# Heater with PID control (feature 'heater'): one GPIO drives a MOSFET (PWM) or an SSR / relay
# (time-proportional window), following a chosen temperature sensor. Set up from the dashboard
# Heater panel (DATA_DIR/heater.json); settings.py HEATER_* are the defaults. Not loaded while off.
#
# Modes: 'pid' (output 0-100 %), 'onoff' (thermostat: on below setpoint - hyst/2, off above
# setpoint + hyst/2, at least min_s between switches - lamps, relays, anything without PWM),
# 'manual' (fixed %), 'off'.
# PID output 0-100 %:
#   'pwm'     hardware PWM at HEATER_PWM_HZ - MOSFET + DC heater (pad, cable, film)
#   'window'  on for out % of every window_s seconds - SSR or relay (mains heaters); pulses shorter
#             than min_on_s are skipped (relay life, zero-cross SSRs)
# PID on the error (setpoint - temperature): P = kp * e [%/degC], I += ki * e * minutes
# [%/(degC*min)], D = -kd * rise per minute [%/(degC/min)] (on the measurement: no kick when the
# setpoint changes). The integral is clamped and frozen while the output is saturated (no windup).
# Safety: no reading for 30 s or temperature >= t_max -> output 0 until it is back (t_max - 2 degC).
try:
    import asyncio
except ImportError:
    import uasyncio as asyncio
import json
import os
import time
from machine import Pin, PWM
import settings as S

CFG = S.DATA_DIR + '/heater.json'
DEFAULTS = {'on': False, 'pin': getattr(S, 'HEATER_PIN', None), 'inv': not getattr(S, 'HEATER_ACTIVE_HIGH', True),
            'src': getattr(S, 'HEATER_SOURCE', 'air'), 'mode': getattr(S, 'HEATER_MODE', 'pid'),
            'sp': getattr(S, 'HEATER_SETPOINT', 25.0), 'hyst': getattr(S, 'HEATER_HYST', 0.5),
            'min_s': getattr(S, 'HEATER_MIN_S', 30), 'on_mode': 'pid',
            'kp': getattr(S, 'HEATER_KP', 20.0), 'ki': getattr(S, 'HEATER_KI', 2.0), 'kd': getattr(S, 'HEATER_KD', 0.0),
            'man': 30, 'out': getattr(S, 'HEATER_OUTPUT', 'window'), 'freq': getattr(S, 'HEATER_PWM_HZ', 100),
            'window_s': getattr(S, 'HEATER_WINDOW_S', 10), 'min_on_s': 1.0,
            'max_pct': getattr(S, 'HEATER_MAX_PCT', 100), 't_max': getattr(S, 'HEATER_MAX_C', 40.0)}


def load_cfg():
    c = dict(DEFAULTS)
    try:
        with open(CFG) as f:
            d = json.load(f)
        for k in c:
            if k in d:
                c[k] = d[k]
    except (OSError, ValueError):
        pass
    return c


def save_cfg(c):
    tmp = CFG + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(c, f)
    os.rename(tmp, CFG)


def sources():
    """Temperature sources: [('air', label), ('<id>.<key>', label)...]."""
    import app
    s = app.sens
    out = [('air', 'Air sensor%s' % (' (%s)' % s.clim_name if s.clim else ''))] if s.air_on else []
    for g in s.gch:
        if 'C' in g[4]:                                    # '&deg;C'
            out.append(('%s.%s' % (g[6], g[2]), g[3]))
    return out


class Heater:
    def __init__(self, c):
        self.pin = self.pwm = None
        self.c = None
        self.reconfig(c)

    def reconfig(self, c):
        """New settings; the output pin is switched off first."""
        self._release()
        self.c = c
        self.t = None                  # temperature now
        self.t_at = time.ticks_ms()    # last good reading
        self.prev = None               # (ticks, temp) for the derivative
        self.i = 0.0                   # integral term, %
        self.p = self.d = 0.0
        self.outp = 0.0                # output now, %
        self.state = 'starting'
        self.fault = ''
        self.sw_at = None              # ticks of the last on/off switch (thermostat mode)
        if c['on'] and c['pin'] is not None:
            self.pin = Pin(c['pin'], Pin.OUT, value=1 if c['inv'] else 0)
            if c['out'] == 'pwm' and c['mode'] != 'onoff':          # thermostat: plain on/off, never PWM
                self.pwm = PWM(self.pin, freq=max(1, min(20000, int(c['freq']))))
                self._duty(0)

    def _release(self):
        if self.pwm:
            self._duty(0)
            self.pwm.deinit()
            self.pwm = None
        if self.pin:
            self.pin.value(1 if self.c['inv'] else 0)
            self.pin = None

    def off(self):
        """Before sleep / restart: output off."""
        self.outp = 0.0
        self._release()

    def active(self):
        return self.pin is not None and self.c['mode'] != 'off'

    def _duty(self, pct):
        d = int(max(0.0, min(100.0, pct)) * 1023 / 100)
        self.pwm.duty(1023 - d if self.c['inv'] else d)

    def _set(self, on):
        self.pin.value((0 if on else 1) if self.c['inv'] else (1 if on else 0))

    def read(self):
        import app
        s, src = app.sens, self.c['src']
        if src == 'air':
            return s.t
        sid, _, key = src.partition('.')
        for j, g in enumerate(s.gch):
            if g[6] == sid and g[2] == key:
                return s.gval[j]
        return None

    def src_name(self):
        for k, lbl in sources():
            if k == self.c['src']:
                return lbl
        return self.c['src'] + ' (missing)'

    def step(self):
        """Every ~2 s: new reading -> output %."""
        c = self.c
        t = self.read()
        now = time.ticks_ms()
        if t is not None:
            self.t, self.t_at = t, now
        elif time.ticks_diff(now, self.t_at) > 30000:
            self.t = None
        if c['mode'] == 'off':
            self.outp, self.state = 0.0, 'off'
            return
        if self.t is None:
            self.outp, self.state, self.fault = 0.0, 'no reading', 'no temperature reading for 30 s'
            self.prev = None
            return
        t = self.t
        if t >= c['t_max'] or (self.fault.startswith('over') and t > c['t_max'] - 2):
            self.outp, self.state = 0.0, 'too hot'
            self.fault = 'over temperature: %.1f C (max %.1f C)' % (t, c['t_max'])
            self.i = 0.0
            return
        self.fault = ''
        mx = max(0, min(100, c['max_pct']))
        if c['mode'] == 'manual':
            self.outp, self.state = float(min(c['man'], mx)), 'manual'
            return
        if c['mode'] == 'onoff':
            h = max(0.0, c['hyst']) / 2
            want = 100.0 if t < c['sp'] - h else 0.0 if t > c['sp'] + h else self.outp
            if want != self.outp and (self.sw_at is None or
                                      time.ticks_diff(now, self.sw_at) >= c['min_s'] * 1000):
                self.outp, self.sw_at = want, now
            self.state = 'heating' if self.outp else 'holding' if t <= c['sp'] + h else 'idle'
            return
        e = c['sp'] - t
        dt = time.ticks_diff(now, self.prev[0]) / 60000 if self.prev else 0     # minutes
        rate = (t - self.prev[1]) / dt if dt > 0 else 0.0                       # degC per minute
        self.prev = (now, t)
        self.p = c['kp'] * e
        self.d = -c['kd'] * rate
        u = self.p + self.i + self.d
        if (u < mx or e < 0) and (u > 0 or e > 0):          # integrate only when not pushing a limit
            self.i = max(0.0, min(float(mx), self.i + c['ki'] * e * dt))
        self.outp = max(0.0, min(float(mx), self.p + self.i + self.d))
        self.state = 'heating' if self.outp >= 1 else 'holding' if e > -0.5 else 'idle'

    async def run(self):
        k = 0
        while self.pin is not None or self.c['on']:
            if self.pin is None:                           # switched off from the panel: idle until on again
                await asyncio.sleep(1)
                continue
            if k % 10 == 0:
                try:
                    self.step()
                except Exception as ex:
                    self.outp, self.fault = 0.0, repr(ex)
            k += 1
            c = self.c
            if self.pwm:
                self._duty(self.outp)
            elif c['mode'] == 'onoff':
                self._set(self.outp > 0)
            elif self.pin is not None:
                w = max(2, c['window_s'])
                on_t = w * self.outp / 100
                if on_t < c['min_on_s']:
                    on_t = 0
                elif w - on_t < c['min_on_s']:
                    on_t = w
                pos = (time.ticks_ms() / 1000) % w
                on = pos < on_t
                self._set(on)
            await asyncio.sleep_ms(200)

    # ---------------------------------------------------------------- display
    def card(self):
        c = self.c
        band = 'dry' if self.fault else 'now' if self.state == 'heating' else 'good' if self.state in (
            'holding', 'idle') else 'wet'
        badge = {'heating': 'Heating', 'holding': 'Holding', 'idle': 'Above setpoint', 'manual': 'Manual',
                 'off': 'Off', 'too hot': 'TOO HOT', 'no reading': 'No reading'}.get(self.state, self.state)
        out = 'PWM %d Hz' % c['freq'] if c['out'] == 'pwm' else 'window %d s' % c['window_s']
        return ('<div class="card band %s"><h3><span>Heater <small>GPIO%s</small></span><span class="badge">%s</span>'
                '</h3><div class="big">%s<small>&deg;C</small></div><div class="bar"><i style="width:%d%%"></i></div>'
                '<dl class="facts"><dt>Setpoint</dt><dd>%.1f &deg;C &middot; %s</dd><dt>Output</dt><dd>%d %% &middot; %s</dd>'
                '<dt>Sensor</dt><dd>%s</dd></dl>%s</div>'
                % (band, c['pin'], badge, '&ndash;' if self.t is None else '%.1f' % self.t, round(self.outp),
                   c['sp'], {'pid': 'PID', 'onoff': 'on/off &plusmn;%g' % (c['hyst'] / 2)}.get(c['mode'], c['mode']),
                   round(self.outp), 'switched' if c['mode'] == 'onoff' else out,
                   self.src_name().replace('<', '&lt;'),
                   '<p class="warn">%s</p>' % self.fault if self.fault else ''))

    def mqtt(self):
        c = self.c
        return {'temp_c': None if self.t is None else round(self.t, 2), 'setpoint_c': c['sp'], 'out_pct': round(self.outp, 1),
                'mode': c['mode'], 'state': self.state, 'fault': self.fault or None,
                'p': round(self.p, 1), 'i': round(self.i, 1), 'd': round(self.d, 1)}
