# Control panel, saving (POST /control/save, /control/del): checks the form, writes control.json and
# applies it after the reply; the reply reloads the list view (ui_control.py) in a separate request,
# so this module and the list code are never in RAM together.
# Spec: docs/SPEC.md#controllers
import app
import settings as S

NUMS = (('sp', -1000, 100000), ('hyst', 0, 10000), ('min_s', 0, 3600), ('kp', 0, 1000), ('ki', 0, 1000),
        ('kd', 0, 1000), ('man', 0, 100), ('freq', 1, 40000), ('window_s', 2, 300), ('min_on_s', 0, 30),
        ('max_pct', 1, 100), ('min_pct', 0, 50), ('cut_back', 0, 10000), ('ramp_min', 1, 720), ('day', 0, 100),
        ('night', 0, 100), ('x1', -1000, 100000), ('y1', 0, 100), ('x2', -1000, 100000), ('y2', 0, 100),
        ('ramp_s', 0, 3600))
INTS = ('min_s', 'man', 'freq', 'window_s', 'max_pct', 'min_pct', 'ramp_min', 'day', 'night', 'y1', 'y2', 'ramp_s')


def _hhmm(t):
    """True for a valid 'HH:MM'."""
    return len(t) == 5 and t[2] == ':' and (t[:2] + t[3:]).isdigit() and int(t[:2]) < 24 and int(t[3:]) < 60


def _pins(own=None):
    """Free output pins (own = the pin the controller already has)."""
    import sys
    import sensors as R
    try:
        t = R.taken_pins(app.sens.reg)
        free = [p for p in R.IO_PINS if p not in t or p == own]
        if app.leds is None:
            free += [p for p in S.LED_PINS if p in R.IO_PINS and p not in free and p not in t]
        return sorted(free)
    finally:
        R = None
        sys.modules.pop('sensors', None)


def done(msg, ok=True):
    """Short reply that reloads the list with msg shown (after the change is applied, ~0.7 s)."""
    q = ''.join('+' if ch == ' ' else ch for ch in msg if ch not in '&%#')
    return ('<p class="%s">%s</p><i hx-get="/control?m=%s&w=%d" hx-trigger="load delay:700ms" hx-target="#control">'
            '</i>' % ('ok' if ok else 'warn', msg, q, 0 if ok else 1))


def bad(msg):
    """Warning: back to the list with the message (nothing saved)."""
    return done(msg, False)


def handle(method, path, f):
    """POST /control/save (add / edit) and /control/del; returns the reply or None."""
    from control_cfg import load_cfg, save_cfg, defaults, sources, KINDS, MAX
    c = load_cfg()
    if method != 'POST':
        return None
    if path == '/control/del':
        c['list'] = [e for e in c['list'] if e['id'] != f.get('id')]
        save_cfg(c)
        app.ctrl_apply_soon()
        return done('Removed.')
    if path != '/control/save':
        return None
    sid, kind = f.get('id', ''), f.get('kind')
    cur = [e for e in c['list'] if e['id'] == sid]
    if not cur and (kind not in dict(KINDS) or len(c['list']) >= MAX):
        return bad('Choose what to add.')
    e = dict(cur[0]) if cur else defaults(kind)
    if not cur:
        ids = [x['id'] for x in c['list']]
        e['id'] = [x for x in ('C1', 'C2', 'C3', 'C4') if x not in ids][0]
    name = f.get('name', '').strip()[:16]
    if not name or any(ch in name for ch in '<>&"\''):
        return bad('Name: letters, digits and spaces.')
    try:
        pin = int(f.get('pin', ''))
        v = dict((k, float(f.get(k, e[k]))) for k, lo, hi in NUMS)
        cut = f.get('cut', '').strip()
        cut = float(cut) if cut else None
    except ValueError:
        return bad('Invalid number.')
    if any(not lo <= v[k] <= hi for k, lo, hi in NUMS):
        return bad('Out of range.')
    if pin not in _pins(e['pin'] if cur else None) or pin in [x['pin'] for x in c['list'] if x['id'] != e['id']]:
        return bad('Choose a free output pin.')
    if app.leds is not None and pin in S.LED_PINS:
        return bad('That pin belongs to the 4-LED indicator, which is on: switch it off first.')
    src = f.get('src', '')
    if src and src not in [k for k, _, _ in sources()]:
        return bad('Unknown sensor.')
    mode = f.get('mode')
    if mode not in ('pid', 'onoff', 'manual', 'sun', 'curve', 'off') or f.get('out') not in ('pwm', 'window') \
            or f.get('shape', 'smooth') not in ('smooth', 'linear', 'arc'):
        return bad('Out of range.')
    if mode in ('pid', 'onoff', 'curve') and not src:
        return bad('PID, on/off and curve need a sensor to follow (or use Manual / Sun cycle).')
    rise, sset = f.get('rise', e['rise']).strip(), f.get('set', e['set']).strip()
    if not (_hhmm(rise) and _hhmm(sset)) or rise == sset:
        return bad('Sunrise and sunset as HH:MM, e.g. 07:00 and 19:00 (different).')
    if mode == 'curve' and v['x1'] == v['x2']:
        return bad('The two curve points need different values.')
    if v['night'] > v['day']:
        return bad('Night level must not be above the day level.')
    hours = f.get('hours', '').strip()
    if hours and not (len(hours) == 11 and hours[2] == ':' and hours[5] == '-' and hours[8] == ':'
                      and (hours[:2] + hours[3:5] + hours[6:8] + hours[9:]).isdigit()):
        return bad('Active hours as HH:MM-HH:MM, e.g. 06:00-20:00.')
    for k in INTS:
        v[k] = int(v[k])
    e.update(v)
    e.update(name=name, pin=pin, on=f.get('on') == '1', inv=f.get('inv') == '1', src=src, mode=mode, out=f.get('out'),
             gamma=f.get('gamma') == '1', hours=hours, cut=cut, rise=rise, set=sset, shape=f.get('shape', 'smooth'),
             stop=f.get('stop') == '1', kick=f.get('kick') == '1')
    if cur:
        c['list'] = [e if x['id'] == e['id'] else x for x in c['list']]
    else:
        c['list'].append(e)
    save_cfg(c)
    app.ctrl_apply_soon()
    return done('Saved %s.' % name)
