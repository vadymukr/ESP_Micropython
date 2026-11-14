# Control panel edit / add form (/control/form) - loaded per request by app.py, then released.
# Built from small parts with helpers (low peak RAM; no import of ui_control).
# Spec: docs/SPEC.md#controllers
import app
from app import esc
import settings as S


def _pins(own):
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


def _num(out, name, label, v, show='', w=5, attrs='step="any"'):
    """Append a number field (show = Alpine condition)."""
    out.append('<label%s>%s<input name="%s" type="number" %s value="%s" style="width:%dem"></label>'
               % (' x-show="%s"' % show if show else '', label, name, attrs, ('%g' % v) if isinstance(v, float) else v, w))


def _chk(out, name, label, on, show=''):
    """Append a checkbox."""
    out.append('<label class="row" style="flex-direction:row;align-items:center;gap:6px"%s><input type="checkbox" '
               'name="%s" value="1"%s> %s</label>' % (' x-show="%s"' % show if show else '', name,
                                                       ' checked' if on else '', label))


def _sel(out, name, label, opts, cur, show='', model=''):
    """Append a select of (value, label) options."""
    out.append('<label%s>%s<select name="%s"%s>' % (' x-show="%s"' % show if show else '', label, name,
                                                     ' x-model="%s"' % model if model else ''))
    for k, lbl in opts:
        out.append('<option value="%s"%s>%s</option>' % (k, ' selected' if k == cur else '', lbl))
    out.append('</select></label>')


def _txt(out, name, label, v, show='', w=5, ph=''):
    """Append a short text field."""
    out.append('<label%s>%s<input name="%s" maxlength="11" value="%s" placeholder="%s" style="width:%dem"></label>'
               % (' x-show="%s"' % show if show else '', label, name, esc(v), ph, w))


def form(e, new):
    """The add / edit form of controller entry e (new = adding)."""
    from control_cfg import sources
    out = ['<form class="set" hx-post="/control/save" hx-target="#control" x-data="{m:\'%s\',o:\'%s\'}">'
           '<input type="hidden" name="id" value="%s"><input type="hidden" name="kind" value="%s">'
           '<b style="flex-basis:100%%">%s</b><label>Name<input name="name" maxlength="16" value="%s" required '
           'style="width:9em"></label>'
           % (e['mode'], e['out'], e['id'], e['kind'], 'Add a ' + e['kind'] if new else 'Edit ' + esc(e['name']),
              esc(e['name']))]
    _chk(out, 'on', 'Enabled', e['on'])
    _sel(out, 'pin', 'Output pin', [(p, 'GPIO%d' % p) for p in _pins(None if new else e['pin'])], e['pin'])
    _chk(out, 'inv', 'On when LOW', e['inv'])
    _sel(out, 'src', 'Follows', [('', 'no sensor')] + [(k, esc(lbl) + (' (%s)' % esc(u) if u else ''))
                                                       for k, lbl, u in sources()], e['src'])
    _sel(out, 'mode', 'Mode', (('pid', 'PID (keep the setpoint)'), ('onoff', 'On/off (relay, lamp, no PWM)'),
                               ('manual', 'Manual level'), ('sun', 'Sun cycle (sunrise / sunset)'),
                               ('curve', 'Curve (value &rarr; level, e.g. fan)'), ('off', 'Off')), e['mode'], model='m')
    # sun cycle
    s = "m=='sun'"
    _txt(out, 'rise', 'Sunrise', e['rise'], s)
    _txt(out, 'set', 'Sunset', e['set'], s)
    _num(out, 'ramp_min', 'Ramp, min', e['ramp_min'], s, attrs='min="1" max="720"')
    _num(out, 'day', 'Day level, %', e['day'], s, 4, 'min="0" max="100"')
    _num(out, 'night', 'Night level, %', e['night'], s, 4, 'min="0" max="100"')
    _sel(out, 'shape', 'Shape', (('smooth', 'smooth (S-curve)'), ('linear', 'linear'),
                                 ('arc', 'sun arc over the whole day')), e['shape'], s)
    # curve
    s = "m=='curve'"
    _num(out, 'x1', 'At', e['x1'], s)
    _num(out, 'y1', '&rarr; level, %', e['y1'], s, 4, 'min="0" max="100"')
    _num(out, 'x2', 'At', e['x2'], s)
    _num(out, 'y2', '&rarr; level, %', e['y2'], s, 4, 'min="0" max="100"')
    _chk(out, 'stop', 'Off past the first point', e['stop'], s)
    # setpoint modes
    _num(out, 'sp', 'Setpoint', e['sp'], "m=='pid'||m=='onoff'", 7)
    _num(out, 'man', 'Level, %', e['man'], "m=='manual'", 4, 'min="0" max="100"')
    _num(out, 'hyst', 'Hysteresis', e['hyst'], "m=='onoff'||m=='curve'", 5, 'step="any" min="0"')
    _num(out, 'min_s', 'Min. s between switching', e['min_s'], "m=='onoff'", 5, 'min="0" max="3600"')
    s = "m=='pid'"
    _num(out, 'kp', 'Kp, %/unit', e['kp'], s, 6)
    _num(out, 'ki', 'Ki, %/(unit&middot;min)', e['ki'], s, 6)
    _num(out, 'kd', 'Kd, %/(unit/min)', e['kd'], s, 6)
    # output stage
    out.append('<span x-show="m!=\'onoff\'" style="display:contents">'
               '<span style="flex-basis:100%;font-size:13px;color:var(--muted)">Output:</span>')
    _sel(out, 'out', 'Drive', (('pwm', 'PWM (MOSFET / dimmer / fan)'), ('window', 'SSR / relay (slow window)')),
         e['out'], model='o')
    p = "o=='pwm'"
    _num(out, 'freq', 'PWM, Hz', e['freq'], p, 6, 'min="1" max="40000"')
    _chk(out, 'gamma', 'Gamma (even dimming for lights)', e['gamma'], p)
    _num(out, 'min_pct', 'Min. level when on, %', e['min_pct'], p, 4, 'min="0" max="50"')
    _num(out, 'ramp_s', 'Fade 0&rarr;100 %, s', e['ramp_s'], p, 5, 'min="0" max="3600"')
    _chk(out, 'kick', 'Kick-start (fans)', e['kick'], p)
    _num(out, 'window_s', 'Window, s', e['window_s'], "o=='window'", 5, 'min="2" max="300"')
    _num(out, 'min_on_s', 'Shortest pulse, s', e['min_on_s'], "o=='window'", 5, 'min="0" max="30" step="0.1"')
    _num(out, 'max_pct', 'Output limit, %', e['max_pct'], '', 4, 'min="1" max="100"')
    out.append('</span>')
    _txt(out, 'hours', 'Active hours', e['hours'], "m!='sun'", 8, 'always')
    out.append('<label>Cut-off (%s)<input name="cut" type="number" step="any" value="%s" placeholder="none" '
               'style="width:6em"></label>' % ('output off at or below' if e['kind'] == 'fan' else 'output off at or above',
                                               '' if e['cut'] is None else '%g' % e['cut']))
    _num(out, 'cut_back', 'back by', e['cut_back'], '', 5, 'step="any" min="0"')
    out.append('<button>Save</button> <button class="small" type="button" hx-get="/control" hx-target="#control">'
               'Cancel</button></form>')
    return out


def handle(method, path, f):
    """GET /control/form?id=<id> (edit) or ?kind=<kind> (add)."""
    from control_cfg import load_cfg, defaults, KINDS, MAX
    c = load_cfg()
    if f.get('id'):
        cur = [e for e in c['list'] if e['id'] == f.get('id')]
        return form(cur[0], False) if cur else None
    if f.get('kind') not in dict(KINDS) or len(c['list']) >= MAX:
        return None
    return form(defaults(f.get('kind')), True)
