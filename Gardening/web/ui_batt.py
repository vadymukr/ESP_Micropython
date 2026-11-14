# Battery panel (/batt, /batt/...) - loaded per request by app.py, then released.
# Spec: docs/SPEC.md#battery-monitor
import app
from app import esc
import settings as S


def frag_batt(msg='', ok=False):
    """Battery panel: preset, divider suggestion, thresholds, live reading."""
    from battery import load_cfg, PINS, PRESETS, PIN_OK_V, PIN_MAX_V, E12
    c = load_cfg()
    b = app.batt
    used = ([x[0] for x in app.sens.ch] + [d.e['pin'] for d in app.sens.gen if 'pin' in d.e] +
            list(S.SENSOR_PWR_PINS) + [getattr(S, 'PUMP_PIN', None), S.WAKE_TOUCH])
    out = []
    if b and b.v is not None:
        out.append('<p><b>Battery: %.2f V (%s)</b> &middot; pin %.3f V &middot; ratio %.3f &middot; calibration &times;%.3f</p>'
                   % (b.v, {'ok': 'OK', 'low': 'LOW', 'crit': 'CRITICAL'}[b.state], b.pin_v, b.ratio, b.cal))
    else:
        out.append('<p><b>Battery monitor: off</b></p>')
    if msg:
        out.append('<p class="%s">%s</p>' % ('ok' if ok else 'warn', esc(msg)))
    p5, p12 = PRESETS['5'], PRESETS['12']
    # Alpine: presets fill R1/R2/alarms; custom suggests R2 for the entered max voltage; live pin check
    out.append('<form class="set" hx-post="/batt/save" hx-target="#batt" x-data="{on:%s,p:\'%s\',vmax:%s,r1:%s,r2:%s,low:%s,crit:%s,'
               'E:[%s],'
               'pre(){if(this.p==\'5\'){this.vmax=%s;this.r1=%s;this.r2=%s;this.low=%s;this.crit=%s}'
               'else if(this.p==\'12\'){this.vmax=%s;this.r1=%s;this.r2=%s;this.low=%s;this.crit=%s}},'
               'get sug(){let b=0;for(const d of [0.1,1,10])for(const e of this.E){const r=e*d;'
               'if(this.vmax*r/(this.r1+r)<=2.3&&r>b)b=r}return b},'
               'get pv(){return this.vmax*this.r2/(this.r1+this.r2)}}">'
               % ('true' if c['on'] else 'false', c['preset'], c['vmax'], c['r1'], c['r2'], c['low'], c['crit'],
                  ','.join(str(e) for e in E12), p5[0], p5[1], p5[2], p5[3], p5[4], p12[0], p12[1], p12[2], p12[3], p12[4]))
    out.append('<label class="row" style="flex-direction:row;align-items:center;gap:6px"><input type="checkbox" name="on" '
               'value="1" x-model="on"> Monitor battery / supply</label>'
               '<span x-show="on" x-cloak style="display:contents"><label>Pin (ADC1)<select name="pin">')
    for p in PINS:
        if p not in used or p == c['pin']:
            out.append('<option value="%d"%s>GPIO%d%s</option>' % (p, ' selected' if p == c['pin'] else '', p,
                       {36: ' (VP)', 39: ' (VN)'}.get(p, '')))
    out.append('</select></label>'
               '<label>Input<select name="preset" x-model="p" @change="pre()"><option value="5">5 V supply / USB</option>'
               '<option value="12">12 V battery</option><option value="custom">Custom</option></select></label>'
               '<label>Max input, V<input name="vmax" type="number" step="0.1" min="1" max="60" x-model.number="vmax" '
               ':readonly="p!=\'custom\'" style="width:6em"></label>'
               '<label>R1 (top), k&Omega;<input name="r1" type="number" step="0.1" min="1" max="1000" x-model.number="r1" style="width:6em"></label>'
               '<label>R2 (bottom), k&Omega;<input name="r2" type="number" step="0.1" min="0.1" max="1000" x-model.number="r2" style="width:6em"></label>'
               '<label>Low alarm, V<input name="low" type="number" step="0.1" min="0.5" max="60" x-model.number="low" style="width:6em"></label>'
               '<label>Critical, V<input name="crit" type="number" step="0.1" min="0.5" max="60" x-model.number="crit" style="width:6em"></label>'
               '</span><button>Save</button>'
               '<p x-show="on" x-cloak class="muted" style="flex-basis:100%%;margin:0">'
               'Pin at <span x-text="vmax"></span> V: <b x-text="pv.toFixed(2)+\' V\'" '
               ':class="pv>%s?\'warn\':pv>%s?\'warn\':\'ok\'"></b>'
               '<span x-show="pv>%s" class="warn"> &ndash; DANGEROUS for the ESP32, use a larger R1 or smaller R2</span>'
               '<span x-show="pv>%s&&pv<=%s" class="warn"> &ndash; above the accurate ADC range (%.2f V), readings near max will be off</span>'
               '<span x-show="p==\'custom\'"> &middot; suggested: R1 <b x-text="r1"></b> k&Omega;, R2 <b x-text="sug"></b> k&Omega; '
               '<button class="small" type="button" @click="r2=sug">use</button></span></p></form>'
               % (PIN_MAX_V, PIN_OK_V, PIN_MAX_V, PIN_OK_V, PIN_MAX_V, PIN_OK_V))
    if b and b.v is not None:
        out.append('<form class="set" hx-post="/batt/cal" hx-target="#batt"><label>Multimeter reads, V'
                   '<input name="meas" type="number" step="0.01" min="0.5" max="60" style="width:7em"></label>'
                   '<button>Calibrate</button><button class="small" type="button" hx-post="/batt/cal" '
                   'hx-vals=\'{"reset":"1"}\' hx-target="#batt">Reset calibration</button></form>')
    out.append('<p class="muted"><b>Wiring:</b> V+ &rarr; R1 &rarr; pin &rarr; R2 &rarr; GND, plus 100 nF from the pin to GND; '
               'common GND with the ESP32. Optional spike clamp: 3.3 V zener (or BAT54S) from the pin to GND. '
               'Recommended: <b>5 V</b> (up to 5.5 V): R1 100k / R2 68k &rarr; 2.23 V at the pin, 30 &micro;A. '
               '<b>12 V lead-acid</b> (up to 15 V while charging): R1 100k / R2 15k &rarr; 1.96 V, 0.1 mA. '
               '<b>Custom</b>: keep R1 100k and use the suggested R2 (pin &le; 2.3 V at the max input). '
               'Resistors 1% metal film. After wiring, enter the multimeter voltage to calibrate.</p>')
    return ''.join(out)


def _handle(method, path, f):
    """Save the battery settings and apply them."""
    from battery import load_cfg, save_cfg, PINS, PRESETS, PIN_MAX_V
    if method == 'GET':
        return frag_batt() if path == '/batt' else None
    c = load_cfg()
    if path == '/batt/save':
        on = f.get('on') == '1'
        if on:
            try:
                pin = int(f.get('pin', c['pin']))
                preset = f.get('preset', '12')
                r1, r2 = float(f.get('r1', 100)), float(f.get('r2', 15))
                low, crit = float(f.get('low', 0)), float(f.get('crit', 0))
                vmax = PRESETS[preset][0] if preset in PRESETS else float(f.get('vmax', 0))
            except (ValueError, KeyError):
                return frag_batt('Invalid number.')
            if pin not in PINS or preset not in ('5', '12', 'custom'):
                return frag_batt('Invalid pin or input type.')
            if not (1 <= r1 <= 1000 and 0.1 <= r2 <= 1000 and 1 <= vmax <= 60):
                return frag_batt('Out of range.')
            pv = vmax * r2 / (r1 + r2)
            if pv > PIN_MAX_V:
                return frag_batt('Not saved: the pin would see %.2f V at %.1f V input - dangerous. '
                                 'Use a larger R1 or a smaller R2.' % (pv, vmax))
            if not 0 < crit < low <= vmax:
                return frag_batt('Alarms: critical must be below low, and low at most the max input.')
            if (r1, r2) != (c['r1'], c['r2']):
                c['cal'] = 1.0                    # new resistors: old calibration no longer applies
            c.update(on=True, pin=pin, preset=preset, vmax=vmax, r1=r1, r2=r2, low=low, crit=crit)
        else:
            c['on'] = False
        save_cfg(c)
        app.batt_apply()
        return frag_batt('Saved.' + ('' if on else ' Monitor off.'), True)
    if path == '/batt/cal':
        b = app.batt
        if f.get('reset') == '1':
            c['cal'] = 1.0
        else:
            if not b or not b.pin_v:
                return frag_batt('No reading yet.')
            try:
                meas = float(f.get('meas', 0))
            except ValueError:
                return frag_batt('Invalid number.')
            k = meas / (b.pin_v * b.ratio)
            if not 0.8 <= k <= 1.2:
                return frag_batt('Calibration factor %.2f is outside 0.8-1.2: check R1/R2 and the wiring first.' % k)
            c['cal'] = round(k, 4)
        save_cfg(c)
        app.batt_apply()
        return frag_batt('Calibration saved (×%.3f).' % c['cal'], True)
    return None


def handle(method, path, f):
    """Route entry for app/server.py: GET shows the panel, POST saves; returns HTML (str / list) or None."""
    try:
        return _handle(method, path, f)
    finally:
        if app.batt is None:              # monitor off: do not keep battery.py in RAM
            import sys
            sys.modules.pop('battery', None)
