# Small helpers shared by the whole app: uptime, clock and text formatting, HTML escaping, query
# parsing, free heap / flash, and the feature switches. No hardware access; imports nothing of the app.
# Spec: docs/SPEC.md#architecture (helpers) and docs/SPEC.md#features (feature switches)
import gc
import json
import os
import time
import machine
import settings as S

UNIX_OFF = 946684800 if time.gmtime(0)[0] == 2000 else 0     # MicroPython epoch 2000 -> Unix
NAME_OK = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-'

heap_min = gc.mem_free()       # lowest free heap seen (shown on the dashboard)
clock_src = 'none'             # where the clock came from: 'none' | 'NTP' | 'browser'
_up_ms = 0
_up_last = time.ticks_ms()

# ---------------------------------------------------------------- feature switches
# settings.FEATURES, or the Features panel (DATA_DIR/features.json, applied after a restart).
# A feature that is off is never imported, so its code takes no RAM. 'heater' = old name of 'control'.
FEATURES = ('pump', 'mqtt', 'gsm', 'battery', 'leds', 'rgb', 'power', 'recorder', 'display', 'control')


def _features():
    """The enabled features as a tuple (saved panel choice, else the settings.py list)."""
    try:
        with open(S.DATA_DIR + '/features.json') as f:
            on = json.load(f)['on']
    except (OSError, ValueError, KeyError):
        on = getattr(S, 'FEATURES', FEATURES)
    return tuple('control' if k == 'heater' else k for k in on)


FEAT = _features()


def has(f):
    """True if feature `f` (e.g. 'pump', 'gsm') is switched on."""
    return f in FEAT


def saved(name, key, default):
    """One value from a DATA_DIR JSON settings file, without importing the module that owns it."""
    try:
        with open(S.DATA_DIR + '/' + name) as f:
            return json.load(f).get(key, default)
    except (OSError, ValueError, AttributeError):
        return default


# ---------------------------------------------------------------- time
def uptime():
    """Seconds since boot; immune to RTC changes and ticks wrap (called every few s)."""
    global _up_ms, _up_last
    n = time.ticks_ms()
    _up_ms += time.ticks_diff(n, _up_last)
    _up_last = n
    return _up_ms // 1000


def time_ok():
    """True once the clock was set (NTP or the browser): year >= 2025."""
    return time.gmtime()[0] >= 2025


def stamp(t=None, secs=True):
    """Local time as 'YYYY-MM-DD HH:MM[:SS]' (t = epoch seconds, default now)."""
    tm = time.localtime((time.time() if t is None else t) + S.TZ_OFFSET_H * 3600)
    if secs:
        return '%04d-%02d-%02d %02d:%02d:%02d' % tm[:6]
    return '%04d-%02d-%02d %02d:%02d' % tm[:5]


def set_clock(unix):
    """Set the RTC from a Unix timestamp sent by the browser (only used while NTP has not set it)."""
    global clock_src
    tm = time.gmtime(unix - UNIX_OFF)
    machine.RTC().datetime((tm[0], tm[1], tm[2], tm[6], tm[3], tm[4], tm[5], 0))
    clock_src = 'browser'
    print('clock set from browser:', stamp())


def dur(s):
    """Human duration: '45 s', '12 min', '3 h 05 min'."""
    s = int(s)
    if s < 120:
        return '%d s' % s
    if s < 7200:
        return '%d min' % (s // 60)
    return '%d h %02d min' % (s // 3600, s % 3600 // 60)


# ---------------------------------------------------------------- memory
def heap_note():
    """Free heap now; updates the low-water mark heap_min."""
    global heap_min
    f = gc.mem_free()
    if f < heap_min:
        heap_min = f
    return f


def flash_free():
    """Free bytes on the flash file system."""
    st = os.statvfs('/')
    return st[0] * st[4]


def kb(n):
    """Bytes as '12 KB' or '1.5 MB'."""
    return '%.1f MB' % (n / 1048576) if n >= 1048576 else '%d KB' % (n // 1024)


# ---------------------------------------------------------------- text
def esc(s):
    """HTML-escape any value for text and attribute use."""
    return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


def clean(s, maxlen):
    """Keep only letters, digits, '_' and '-' (file name parts), at most maxlen characters."""
    return ''.join(c for c in s[:maxlen] if c in NAME_OK)


def safe_csv(n):
    """n if it is a safe recording file name ('name.csv'), else None (no paths, no odd characters)."""
    if not n or len(n) > 48 or not n.endswith('.csv'):
        return None
    return n if clean(n[:-4], 44) == n[:-4] else None


def unquote(s):
    """URL-decode a form / query value ('+' = space, %XX = byte; UTF-8)."""
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
    """'a=1&b=x%20y' -> {'a': '1', 'b': 'x y'} (last value wins)."""
    d = {}
    for part in s.split('&'):
        if part:
            k, _, v = part.partition('=')
            d[unquote(k)] = unquote(v)
    return d
