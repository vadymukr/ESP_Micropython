# Controller maths (no hardware): the daylight curve of the sun cycle, the value -> level curve, and
# the active-hours window. Used by control.py.
# Spec: docs/SPEC.md#controllers
import math
import time
import settings as S


def _hm(s):
    """'HH:MM' -> minutes of the day."""
    return int(s[:2]) * 60 + int(s[3:5])


def _now_min():
    """Local time now as minutes of the day (with seconds as a fraction)."""
    t = time.localtime(time.time() + S.TZ_OFFSET_H * 3600)
    return t[3] * 60 + t[4] + t[5] / 60


def sun_level(c, m):
    """Output % of the daylight cycle at minute-of-day m, and the phase name."""
    rise, L = _hm(c['rise']), (_hm(c['set']) - _hm(c['rise'])) % 1440 or 1440
    d = (m - rise) % 1440                                  # minutes since sunrise
    if d >= L:
        return float(c['night']), 'night'
    if c['shape'] == 'arc':
        f, ph = math.sin(math.pi * d / L), 'day'
    else:
        r = max(1, min(c['ramp_min'], L / 2))
        if d < r:
            f, ph = d / r, 'sunrise'
        elif d > L - r:
            f, ph = (L - d) / r, 'sunset'
        else:
            f, ph = 1.0, 'day'
        if c['shape'] == 'smooth':
            f = (1 - math.cos(math.pi * f)) / 2           # S-curve: slow start, slow finish
    return c['night'] + (c['day'] - c['night']) * f, ph


def curve_level(c, v, prev):
    """Output % on the line (x1, y1)-(x2, y2); with 'stop', off past x1 (restarts at x1 - hysteresis)."""
    x1, x2 = c['x1'], c['x2']
    if x1 == x2:
        return float(c['y2'])
    f = (v - x1) / (x2 - x1)
    if c['stop']:
        beyond = f * abs(x2 - x1)                         # >0 towards x2, <0 past x1, in sensor units
        if beyond < -max(0.0, c['hyst']) or (prev == 0 and beyond < 0):
            return 0.0
    f = max(0.0, min(1.0, f))
    return c['y1'] + (c['y2'] - c['y1']) * f


def _in_hours(spec):
    """True inside 'HH:MM-HH:MM' (also across midnight), or if spec is bad / the clock is not set."""
    try:
        a, b = spec.split('-')
        a = int(a[:2]) * 60 + int(a[3:5])
        b = int(b[:2]) * 60 + int(b[3:5])
    except (ValueError, IndexError):
        return True
    from app import time_ok
    if not time_ok():
        return True                        # clock not set yet: do not keep things off
    t = time.localtime(time.time() + S.TZ_OFFSET_H * 3600)
    m = t[3] * 60 + t[4]
    return a <= m < b if a <= b else m >= a or m < b

