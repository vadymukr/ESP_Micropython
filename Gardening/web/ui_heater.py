# Heater panel (/heater, /heater/save) - loaded per request by app.py, then released.
import app
from app import esc
import settings as S

ROW = 'class="row" style="flex-direction:row;align-items:center;gap:6px"'


def _pins(c):
    import sys
    import sensors as R
    try:
        t = R.taken_pins(app.sens.reg)
        own = c['pin'] if c['on'] else None
        free = [p for p in R.IO_PINS if p not in t or p == own]
        if app.leds is None:
            free += [p for p in S.LED_PINS if p in R.IO_PINS and p not in free and p not in t]
        return sorted(free)
    finally:
        R = None
        sys.modules.pop('sensors', None)


def frag(msg='', ok=False):
    from heater import load_cfg, sources
    c = load_cfg()
    h = app.heater
    out = []
    if h:
        out.append('<p><b>%s</b> &middot; %s &middot; output %d %% (P %.0f, I %.0f, D %.0f)%s</p>'
                   % (h.state, '&ndash;' if h.t is None else '%.1f &deg;C' % h.t, round(h.outp), h.p, h.i, h.d,
                      ' &middot; <span class="warn">%s</span>' % esc(h.fault) if h.fault else ''))
    if msg:
        out.append('<p class="%s">%s</p>' % ('ok' if ok else 'warn', esc(msg)))
    sel = lambda a, b: ' selected' if a == b else ''
    out.append('<form class="set" hx-post="/heater/save" hx-target="#heater" x-data="{on:%s,m:\'%s\',o:\'%s\'}">'
               '<label %s><input type="checkbox" name="on" value="1" x-model="on"> Use a heater</label>'
               '<span x-show="on" x-cloak style="display:contents"><label>Output pin<select name="pin">'
               % ('true' if c['on'] else 'false', c['mode'], c['out'], ROW))
    for p in _pins(c):
        out.append('<option value="%d"%s>GPIO%d</option>' % (p, sel(c['pin'], p), p))
    out.append('</select></label><label %s><input type="checkbox" name="inv" value="1"%s> On when LOW (relay boards)</label>'
               '<label>Temperature from<select name="src">' % (ROW, ' checked' if c['inv'] else ''))
    for k, lbl in sources():
        out.append('<option value="%s"%s>%s</option>' % (k, sel(c['src'], k), lbl))
    out.append('</select></label><label>Mode<select name="mode" x-model="m"><option value="pid">PID (keep the setpoint)</option>'
               '<option value="onoff">On/off thermostat (lamp, relay, no PWM)</option>'
               '<option value="manual">Manual (fixed output)</option><option value="off">Off</option></select></label>'
               '<label x-show="m!=\'manual\'">Desired temperature, &deg;C<input name="sp" type="number" min="5" max="90" '
               'step="0.1" value="%.1f" style="width:6em"></label>'
               '<label x-show="m==\'manual\'">Manual output, %%<input name="man" type="number" min="0" max="100" value="%d" '
               'style="width:5em"></label>'
               '<label x-show="m==\'onoff\'">Hysteresis, &deg;C<input name="hyst" type="number" min="0" max="10" step="0.1" '
               'value="%g" style="width:5em"></label>'
               '<label x-show="m==\'onoff\'">Min. time between switching, s<input name="min_s" type="number" min="0" '
               'max="3600" value="%d" style="width:5em"></label>'
               '<span x-show="m==\'pid\'" style="display:contents">'
               '<label>Kp, %%/&deg;C<input name="kp" type="number" min="0" max="500" step="0.1" value="%g" style="width:6em"></label>'
               '<label>Ki, %%/(&deg;C&middot;min)<input name="ki" type="number" min="0" max="500" step="0.01" value="%g" '
               'style="width:6em"></label>'
               '<label>Kd, %%/(&deg;C/min)<input name="kd" type="number" min="0" max="500" step="0.1" value="%g" '
               'style="width:6em"></label></span>'
               '<span x-show="m!=\'onoff\'" style="display:contents">'
               '<span style="flex-basis:100%%;font-size:13px;color:var(--muted)">Output:</span>'
               '<label>Drive<select name="out" x-model="o"><option value="window">SSR / relay (slow on/off window)</option>'
               '<option value="pwm">MOSFET (PWM)</option></select></label>'
               '<label x-show="o==\'pwm\'">PWM frequency, Hz<input name="freq" type="number" min="1" max="20000" value="%d" '
               'style="width:6em"></label>'
               '<label x-show="o==\'window\'">Window, s<input name="window_s" type="number" min="2" max="300" value="%d" '
               'style="width:5em"></label>'
               '<label x-show="o==\'window\'">Shortest pulse, s<input name="min_on_s" type="number" min="0" max="30" step="0.1" '
               'value="%g" style="width:5em"></label></span>'
               '<label x-show="m!=\'onoff\'">Output limit, %%<input name="max_pct" type="number" min="1" max="100" value="%d" style="width:5em"></label>'
               '<label>Safety cut-off, &deg;C<input name="t_max" type="number" min="10" max="120" step="0.5" value="%g" '
               'style="width:6em"></label></span><button>Save</button></form>'
               % (c['sp'], c['man'], c['hyst'], c['min_s'], c['kp'], c['ki'], c['kd'], c['freq'], c['window_s'], c['min_on_s'], c['max_pct'],
                  c['t_max']))
    out.append('<p class="muted">On/off thermostat: the plain way for a heat lamp, a relay or anything that cannot '
               'be pulsed - on below the setpoint minus half the hysteresis, off above it plus half. ')
    out.append('MOSFET + DC heater (pad, heating cable, film): PWM, 100-1000 Hz. Mains heater: an SSR '
               '(zero-cross) or relay on the slow window - never PWM; a relay wears out, use a window of 30 s or more. '
               'Tuning: start with Ki = Kd = 0 and raise Kp until the temperature swings around the setpoint, halve it, '
               'then add Ki until it settles on the setpoint; Kd only if it overshoots. The heater switches off when the '
               'sensor gives no reading for 30 s or the temperature reaches the cut-off. SMS (GSM): HEAT 24, HEAT OFF.</p>')
    return out


def handle(method, path, f):
    from heater import load_cfg, save_cfg, sources
    if method == 'GET':
        return frag() if path == '/heater' else None
    if path != '/heater/save':
        return None
    c = load_cfg()
    on = f.get('on') == '1'
    if on:
        try:
            v = {'pin': int(f.get('pin', '')), 'sp': float(f.get('sp', c['sp'])), 'man': int(f.get('man', c['man'])),
                 'kp': float(f.get('kp', c['kp'])), 'ki': float(f.get('ki', c['ki'])), 'kd': float(f.get('kd', c['kd'])),
                 'freq': int(f.get('freq', c['freq'])), 'window_s': int(f.get('window_s', c['window_s'])),
                 'min_on_s': float(f.get('min_on_s', c['min_on_s'])), 'max_pct': int(f.get('max_pct', c['max_pct'])),
                 't_max': float(f.get('t_max', c['t_max'])), 'hyst': float(f.get('hyst', c['hyst'])),
                 'min_s': int(f.get('min_s', c['min_s']))}
        except ValueError:
            return frag('Invalid number.')
        if v['pin'] not in _pins(c):
            return frag('Choose a free output pin.')
        if f.get('src') not in [k for k, _ in sources()] or f.get('mode') not in ('pid', 'onoff', 'manual', 'off') \
                or f.get('out') not in ('pwm', 'window'):
            return frag('Out of range.')
        if not (5 <= v['sp'] <= 90 and 0 <= v['man'] <= 100 and 1 <= v['freq'] <= 20000 and 2 <= v['window_s'] <= 300
                and 0 <= v['min_on_s'] <= v['window_s'] / 2 and 1 <= v['max_pct'] <= 100 and 10 <= v['t_max'] <= 120
                and all(0 <= v[k] <= 500 for k in ('kp', 'ki', 'kd')) and 0 <= v['hyst'] <= 10 and 0 <= v['min_s'] <= 3600):
            return frag('Out of range.')
        if v['sp'] > v['t_max'] - 1:
            return frag('The desired temperature must be at least 1 degC below the safety cut-off.')
        if app.leds is not None and v['pin'] in S.LED_PINS:
            return frag('That pin belongs to the 4-LED indicator, which is on: switch it off first.')
        c.update(v)
        c.update(on=True, inv=f.get('inv') == '1', src=f.get('src'), mode=f.get('mode'), out=f.get('out'))
    else:
        c['on'] = False
    save_cfg(c)
    app.heater_apply()
    return frag('Saved.' if on else 'Heater off, pin released.', True)
