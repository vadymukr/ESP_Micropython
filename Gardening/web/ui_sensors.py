# Sensors panel, viewing part (GET /sensors, POST /sensors/edit, /sensors/form) - loaded per request
# by app.py, then released. Changes are saved by ui_sensors_set.py. The add/edit form holds only the
# fields of the chosen type (changing the type fetches its form), so the page stays small.
# Handlers return a list of HTML parts: app.send() streams them without joining (RAM).
import app
from app import esc
import settings as S

HINTS = {
    'soil': 'Capacitive probe to an ADC1 pin, 3.3 V supply. Defaults = the 2 Nov calibration of this probe model; '
            'after adding, press <i>Dry = now</i> with the probe in air and <i>Wet = now</i> in water to the line.',
    'analog': 'Two points mV &rarr; value (straight line). Keep the pin under ~2.45 V (divide higher voltages).',
    'ds18b20': 'Data to the pin with 4.7 k&Omega; to 3.3 V; several probes in parallel on one pin, each becomes a reading. '
               'Probes added later: save this sensor again (rescans).',
    'dht': 'Data to the pin; 10 k&Omega; pull-up to 3.3 V if the module has none. One reading per ~2 s.',
    'digital': 'Switch between the pin and GND with pull-up 1. Rain / float modules: use the DO output (3.3 V).',
    'flow': 'Signal to the pin (internal pull-up), sensor on 5 V. YF-S201 ~450 pulses per litre; litres count from the last restart.',
    'i2c': 'Shared I2C bus with the air sensor: 3.3 V, SDA GPIO%d, SCL GPIO%d. The address is printed on the module or set with '
           'its jumpers and must not clash with another device.' % (S.I2C_SDA, S.I2C_SCL),
    'ina219': ' INA219 boards default to 0x40, the same as the Si7020 air sensor: bridge A0 for 0x41.',
    'mq': 'Module on 5 V (heater ~150 mA, not from the 3.3 V pin). AO swings to 5 V: divider R1 100k / R2 82k to the pin '
          '(ratio 2.22). RL = the module load resistor (SMD 102 = 1, 103 = 10 k&Omega;). Burn in 24 h, then press '
          '<i>R0 = now</i> in clean outdoor air. Values start 3 min after power-on and are approximate - '
          '<b>not a certified gas / CO alarm</b>.',
    'acs758': 'Sensor on 5 V, output 0.5-4.5 V: divider R1 100k / R2 82k (ratio 2.22). 050B = &plusmn;50 A, 40 mV/A. '
              'With no current flowing press <i>Zero = now</i>.',
    'uv': 'Module on 3.3 V, output (&lt; 1.1 V) straight to an ADC1 pin. UV index &asymp; output / 0.1 V.',
    'uart': 'Sensor TX &rarr; RX pin, sensor RX &rarr; TX pin, 5 V supply, common GND (3.3 V logic is fine). '
            'At most 2 UART sensors.',
    'ccs811': ' Tie nWAKE to GND. eCO2 is estimated from VOCs; 48 h burn-in, 20 min warm-up.',
    'x29': ' VL53L0X, VL53L1X and TCS34725 all use 0x29: only one of them on the bus.',
}


def _live(e):
    """Current reading text for the list."""
    sn = app.sens
    for i, c in enumerate(sn.ch):
        if c[5] == e['id']:
            return 'raw %d &middot; %d%%' % (sn.raw[i], round(sn.pct(sn.raw[i], c[2], c[3])))
    for d in sn.gen:
        if d.e['id'] == e['id']:
            vals = ['&ndash;' if sn.gval[j] is None else ('%%.%df' % g[5]) % sn.gval[j] + (' ' + g[4] if g[4] else '')
                    for j, g in enumerate(sn.gch) if g[6] == e['id']]
            return '%s &middot; %s' % (', '.join(vals) or '&ndash;', d.info())
    for name, err in sn.bad:
        if name == e['name']:
            return '<span class="warn">not started: %s</span>' % esc(err)
    return '<span class="muted">starts after restart</span>'


def form(R, typ, cur=None):
    """Add / edit form for one type (list of HTML parts)."""
    lst = app.sens.reg
    kind, fields = R.TYPES[typ][1], R.TYPES[typ][2]
    out = ['<form class="set" hx-post="/sensors/save" hx-target="#smsg">'
           '<input type="hidden" name="id" value="%s"><b style="flex-basis:100%%">%s</b>'
           % (cur['id'] if cur else '', 'Edit %s' % esc(cur['name']) if cur else 'Add a sensor')]
    if cur:
        out.append('<input type="hidden" name="type" value="%s">' % typ)
    else:
        out.append('<label>Type<select name="type" hx-post="/sensors/form" hx-trigger="change" hx-target="#sform">')
        for k, v in R.TYPES.items():
            out.append('<option value="%s"%s>%s</option>' % (k, ' selected' if k == typ else '', esc(v[0])))
        out.append('</select></label>')
    out.append('<label>Name<input name="name" maxlength="20" value="%s" required style="width:10em"></label>'
               % esc(cur['name'] if cur else ''))
    if kind != 'i2c':
        pins = R.free_pins(kind, lst, cur['id'] if cur else None)
        sel = (('pin', 'RX pin (from sensor TX)'), ('pin2', 'TX pin (to sensor RX)')) if kind == 'uart' else \
            (('pin', 'Pin (%s)' % ('ADC1' if kind == 'adc' else 'GPIO')),)
        for n, (key, lbl) in enumerate(sel):
            out.append('<label>%s<select name="%s">' % (lbl, key))
            for k, p in enumerate(pins):
                s_ = cur.get(key) == p if cur else k == n  # new UART sensor: RX/TX preselect different pins
                out.append('<option value="%d"%s>GPIO%d</option>' % (p, ' selected' if s_ else '', p))
            out.append('%s</select></label>' % ('' if pins else '<option value="">no free pin</option>'))
    for key, lbl, t, dflt in fields:
        v = cur.get(key, dflt) if cur else dflt
        out.append('<label>%s<input name="%s" %s value="%s" style="width:7em"></label>'
                   % (esc(lbl), key, 'type="number" step="any"' if t == 'num' else 'maxlength="8"', esc(v)))
    out.append('<button>%s</button>' % ('Save' if cur else 'Add'))
    if cur:
        out.append(' <button class="small" type="button" hx-get="/sensors" hx-target="#sensors">Cancel</button>')
    out.append('<p class="muted" style="flex-basis:100%%;margin:0">%s%s</p></form>'
               % (HINTS[typ] if typ in HINTS and typ not in ('ina219', 'ccs811') else
                  HINTS['mq' if typ[:2] == 'mq' else kind if kind in ('i2c', 'uart') else 'analog'],
                  HINTS[typ] if typ in ('ina219', 'ccs811') else
                  HINTS['x29'] if typ in ('vl53l0x', 'vl53l1x', 'tcs34725') else ''))
    return out


def frag(R, msg=''):
    lst = app.sens.reg
    out = ['<div id="smsg">']
    if msg:
        out.append('<p class="ok">%s</p>' % esc(msg))
    out.append('</div><table><tr><th>Name</th><th>Type</th><th>Pin</th><th>Reading</th><th></th></tr>')
    for e in lst:
        sid = e['id']
        out.append('<tr><td>%s <small>%s</small></td><td>%s%s</td><td>%s</td><td>%s</td><td>'
                   % (esc(e['name']), sid, esc(R.TYPES.get(e['type'], (e['type'],))[0]),
                      ' <small>dry %d / wet %d</small>' % (e['dry'], e['wet']) if e['type'] == 'soil' else '',
                      ('GPIO%d' % e['pin'] + (' / %d' % e['pin2'] if 'pin2' in e else '')) if 'pin' in e
                      else 'I2C 0x%s' % e['addr'].upper() if 'addr' in e else '-',
                      _live(e)))
        out.append('<button class="small" hx-post="/sensors/edit" hx-vals=\'{"id":"%s"}\' hx-target="#sform">Edit</button> ' % sid)
        cal = R.TYPES.get(e['type'], (0, 0, 0, 0, None))[4]
        if cal:                                 # driver calibration, e.g. MQ R0 in clean air
            out.append('<button class="small" hx-post="/sensors/cal" hx-vals=\'{"id":"%s"}\' hx-target="#smsg">%s</button> '
                       % (sid, cal))
        if e['type'] == 'soil':
            for w in ('dry', 'wet'):
                out.append('<button class="small" hx-post="/sensors/cap" hx-vals=\'{"id":"%s","w":"%s"}\' hx-target="#smsg" '
                           'title="Take the current raw reading as the %s point">%s = now</button> '
                           % (sid, w, w, 'Dry' if w == 'dry' else 'Wet'))
        out.append('<span x-data="{c:0}"><button class="small" x-show="!c" @click="c=1">Remove</button><span x-show="c">Sure? '
                   '<button class="small danger" hx-post="/sensors/del" hx-vals=\'{"id":"%s"}\' hx-target="#smsg">Yes</button> '
                   '<button class="small" @click="c=0">No</button></span></span></td></tr>' % sid)
    sn = app.sens
    if sn.clim:                                # built-in air sensor (climate.py, auto-detected, not editable)
        out.append('<tr><td>Air <small>built in</small></td><td>%s <small>(detected automatically)</small></td><td>I2C %s</td>'
                   '<td>%s</td><td class="muted">always on</td></tr>'
                   % (esc(sn.clim_name), ', '.join('0x%02X' % a for a in sn.air_addrs()),
                      ' &middot; '.join(x for x in ('%.1f &deg;C' % sn.t if sn.t is not None else '',
                                                    '%.0f%% RH' % sn.rh if sn.rh is not None else '',
                                                    '%.1f hPa' % sn.p if sn.p is not None else '') if x) or '&ndash;'))
    out.append('</table><div id="sform">')
    out += form(R, 'soil')
    taken = R.taken_pins(lst)
    out.append('</div><p class="muted">Pins in use: %s. Free ADC1: %s. Free GPIO: %s. I2C in use: %s. '
               'Adding, removing or moving a sensor restarts the board (about 20 s) and stops a running recording '
               '(the CSV columns change); each sensor gets a dashboard card, an MQTT field and a CSV column.</p>'
               % (', '.join('%d %s' % (p, esc(n)) for p, n in sorted(taken.items())),
                  ', '.join(str(p) for p in R.free_pins('adc', lst)) or 'none',
                  ', '.join(str(p) for p in R.free_pins('io', lst)) or 'none',
                  ', '.join('0x%02X %s' % (a, esc(n)) for a, n in sorted(R.i2c_taken(lst, app.sens.air_addrs()).items()))
                  or 'none'))
    return out


def handle(method, path, f):
    import sys
    import sensors as R
    try:
        if path == '/sensors':
            return frag(R, f.get('m', ''))
        if path == '/sensors/edit':
            cur = [e for e in app.sens.reg if e['id'] == f.get('id')]
            return form(R, cur[0]['type'], cur[0]) if cur else None
        if path == '/sensors/form':
            t = f.get('type')
            return form(R, t) if t in R.TYPES else None
        return None
    finally:
        R = None
        sys.modules.pop('sensors', None)      # registry module only needed during the request
