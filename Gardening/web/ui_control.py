# Control panel (/control, /control/save, /control/del; the form: ui_control_form.py) - loaded per request,
# then released. Heaters, lights (PWM dimmers) and fans with PID, on/off or manual control.
import app
from app import esc
import settings as S

ROW = 'class="row" style="flex-direction:row;align-items:center;gap:6px"'
NUMS = (('sp', -1000, 100000), ('hyst', 0, 10000), ('min_s', 0, 3600), ('kp', 0, 1000), ('ki', 0, 1000),
        ('kd', 0, 1000), ('man', 0, 100), ('freq', 1, 40000), ('window_s', 2, 300), ('min_on_s', 0, 30),
        ('max_pct', 1, 100), ('min_pct', 0, 50), ('cut_back', 0, 10000), ('ramp_min', 1, 720), ('day', 0, 100),
        ('night', 0, 100), ('x1', -1000, 100000), ('y1', 0, 100), ('x2', -1000, 100000), ('y2', 0, 100),
        ('ramp_s', 0, 3600))
INTS = ('min_s', 'man', 'freq', 'window_s', 'max_pct', 'min_pct', 'ramp_min', 'day', 'night', 'y1', 'y2', 'ramp_s')


def _hhmm(t):
    return len(t) == 5 and t[2] == ':' and (t[:2] + t[3:]).isdigit() and int(t[:2]) < 24 and int(t[3:]) < 60


def _pins(own=None):
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


def frag(msg='', ok=False):
    from control_cfg import load_cfg, KINDS, MAX
    c = load_cfg()
    run = dict((k.c['id'], k) for k in app.ctrl.ctls) if app.ctrl else {}
    out = []
    if msg:
        out.append('<p class="%s">%s</p>' % ('ok' if ok else 'warn', esc(msg)))
    names = dict(KINDS)
    for e in c['list']:
        k = run.get(e['id'])
        st = ('%s &middot; output %d %%%s' % (k.state, round(k.outp), ' &middot; <span class="warn">%s</span>' % esc(k.fault)
                                                if k.fault else '')) if k else 'disabled' if not e['on'] else 'starting'
        out.append('<div class="card" style="margin:8px 0"><b>%s</b> <small>%s &middot; GPIO%s</small><br>'
                   '<span class="muted">%s</span><br><button class="small" hx-get="/control/form?id=%s" '
                   'hx-target="#cform">Edit</button> <span x-data="{c:0}"><button class="small" x-show="!c" @click="c=1">'
                   'Remove</button><span x-show="c">Sure? <button class="small danger" hx-post="/control/del" '
                   'hx-vals=\'{"id":"%s"}\' hx-target="#control">Yes</button> <button class="small" @click="c=0">No'
                   '</button></span></span></div>'
                   % (esc(e['name']), names.get(e['kind'], e['kind']), e['pin'], st, e['id'], e['id']))
    out.append('<div id="cform">')
    if len(c['list']) < MAX:
        for k, lbl in KINDS:
            out.append('<button class="small" hx-get="/control/form?kind=%s" hx-target="#cform">Add %s</button> '
                       % (k, lbl.split(' (')[0].lower()))
    else:
        out.append('<p class="muted">%d controllers at most.</p>' % MAX)
    out.append('</div><p class="muted">Heater: MOSFET + DC heater on PWM (100-1000 Hz), a mains heater on an SSR / relay '
               'window - never PWM. Light: an LED strip through a logic-level MOSFET, or a PWM-dimmable LED driver '
               '(1 kHz, gamma on); on/off for a lamp on a relay; the setpoint is in the sensor unit (lux from a '
               'BH1750). Sun cycle: aquarium / terrarium light rising and setting slowly every day (needs the clock; '
               'night level = moonlight). Curve: fan speed from the temperature like a CPU cooler, e.g. 30 &deg;C '
               '&rarr; 30 %, 45 &deg;C &rarr; 100 %. Fade makes every change glide. Fan: 4-pin PC fan PWM at 25 kHz, or a relay. PID tuning: Ki = Kd = 0, raise Kp until the value '
               'swings around the setpoint, halve it, then add Ki until it settles. SMS: HEAT 24, LIGHT 60, FAN OFF.</p>')
    return out


def handle(method, path, f):
    from control_cfg import load_cfg, save_cfg, defaults, sources, KINDS, MAX
    c = load_cfg()
    if path == '/control':
        return frag()
    if method != 'POST':
        return None
    if path == '/control/del':
        c['list'] = [e for e in c['list'] if e['id'] != f.get('id')]
        save_cfg(c)
        app.ctrl_apply_soon()
        return frag('Removed.', True)
    if path != '/control/save':
        return None
    sid, kind = f.get('id', ''), f.get('kind')
    cur = [e for e in c['list'] if e['id'] == sid]
    if not cur and (kind not in dict(KINDS) or len(c['list']) >= MAX):
        return frag('Choose what to add.')
    e = dict(cur[0]) if cur else defaults(kind)
    if not cur:
        ids = [x['id'] for x in c['list']]
        e['id'] = [x for x in ('C1', 'C2', 'C3', 'C4') if x not in ids][0]
    name = f.get('name', '').strip()[:16]
    if not name or any(ch in name for ch in '<>&"\''):
        return frag('Name: letters, digits and spaces.')
    try:
        pin = int(f.get('pin', ''))
        v = dict((k, float(f.get(k, e[k]))) for k, lo, hi in NUMS)
        cut = f.get('cut', '').strip()
        cut = float(cut) if cut else None
    except ValueError:
        return frag('Invalid number.')
    if any(not lo <= v[k] <= hi for k, lo, hi in NUMS):
        return frag('Out of range.')
    if pin not in _pins(e['pin'] if cur else None) or pin in [x['pin'] for x in c['list'] if x['id'] != e['id']]:
        return frag('Choose a free output pin.')
    if app.leds is not None and pin in S.LED_PINS:
        return frag('That pin belongs to the 4-LED indicator, which is on: switch it off first.')
    src = f.get('src', '')
    if src and src not in [k for k, _, _ in sources()]:
        return frag('Unknown sensor.')
    mode = f.get('mode')
    if mode not in ('pid', 'onoff', 'manual', 'sun', 'curve', 'off') or f.get('out') not in ('pwm', 'window') \
            or f.get('shape', 'smooth') not in ('smooth', 'linear', 'arc'):
        return frag('Out of range.')
    if mode in ('pid', 'onoff', 'curve') and not src:
        return frag('PID, on/off and curve need a sensor to follow (or use Manual / Sun cycle).')
    rise, sset = f.get('rise', e['rise']).strip(), f.get('set', e['set']).strip()
    if not (_hhmm(rise) and _hhmm(sset)) or rise == sset:
        return frag('Sunrise and sunset as HH:MM, e.g. 07:00 and 19:00 (different).')
    if mode == 'curve' and v['x1'] == v['x2']:
        return frag('The two curve points need different values.')
    if v['night'] > v['day']:
        return frag('Night level must not be above the day level.')
    hours = f.get('hours', '').strip()
    if hours and not (len(hours) == 11 and hours[2] == ':' and hours[5] == '-' and hours[8] == ':'
                      and (hours[:2] + hours[3:5] + hours[6:8] + hours[9:]).isdigit()):
        return frag('Active hours as HH:MM-HH:MM, e.g. 06:00-20:00.')
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
    return frag('Saved %s - applied in a moment.' % name, True)
