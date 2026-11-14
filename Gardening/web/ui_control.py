# Control panel, list view (GET /control): controllers with Edit / Remove, Add buttons, help. The add /
# edit form is ui_control_form.py, saving is ui_control_save.py (each loaded alone, per request).
# Spec: docs/SPEC.md#controllers
import app
from app import esc


def frag(msg='', ok=False):
    """Control panel: list of controllers with Edit / Remove, Add buttons, help."""
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
    """GET /control: the list (msg = a message shown above it, e.g. after a save)."""
    return frag(f.get('m', ''), f.get('w') != '1') if path == '/control' and method == 'GET' else None
