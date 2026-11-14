# RGB status light panel (/rgb, /rgb/save) - loaded per request by app.py, then released.
import app
from app import esc
import settings as S

MODES = (('on', 'On (steady)'), ('eco', 'Eco (short flash every 5 s)'), ('off', 'Off'))


def _pins(c):
    """Free GPIOs, plus the 4-LED pins while that indicator is off, plus the light's own pins."""
    import sys
    import sensors as R
    try:
        lst = app.sens.reg
        own = c['pins'] if c['on'] else []
        t = R.taken_pins(lst)
        free = [p for p in R.IO_PINS if p not in t or p in own]
        if app.leds is None:
            free += [p for p in S.LED_PINS if p in R.IO_PINS and p not in free]
        return sorted(free)
    finally:
        R = None
        sys.modules.pop('sensors', None)


def frag(msg='', ok=False):
    from rgb import load_cfg
    c = load_cfg()
    r = app.rgb
    out = ['<form class="set" hx-post="/rgb/save" hx-target="#rgb" x-data="{on:%s}"><b style="flex-basis:100%%">RGB status light</b>'
           '<label class="row" style="flex-direction:row;align-items:center;gap:6px"><input type="checkbox" name="on" value="1" '
           'x-model="on"> Show the soil band in colour</label><span x-show="on" x-cloak style="display:contents">'
           % ('true' if c['on'] else 'false')]
    pins = _pins(c)
    for k, col in enumerate(('Red', 'Green', 'Blue')):
        out.append('<label>%s pin<select name="p%d">' % (col, k))
        for p in pins:
            out.append('<option value="%d"%s>GPIO%d</option>' % (p, ' selected' if c['pins'][k] == p else '', p))
        out.append('</select></label>')
    out.append('<label>Mode<select name="mode">')
    for m, lbl in MODES:
        out.append('<option value="%s"%s>%s</option>' % (m, ' selected' if c['mode'] == m else '', lbl))
    out.append('</select></label><label>Follows<select name="sensor"><option value="-1"%s>worst soil sensor</option>'
               % (' selected' if c['sensor'] < 0 else ''))
    for i, ch in enumerate(app.sens.ch):
        out.append('<option value="%d"%s>%s</option>' % (i, ' selected' if c['sensor'] == i else '', esc(ch[4])))
    out.append('</select></label><label>Brightness, %%<input name="bright" type="number" min="5" max="100" value="%d" '
               'style="width:5em"></label><label class="row" style="flex-direction:row;align-items:center;gap:6px">'
               '<input type="checkbox" name="anode" value="1"%s> Common anode (+ to 3V3)</label></span><button>Save</button></form>'
               % (c['bright'], ' checked' if c['anode'] else ''))
    if msg:
        out.append('<p class="%s">%s</p>' % ('ok' if ok else 'warn', esc(msg)))
    out.append('<p class="muted">%sGreen good &middot; yellow water soon &middot; red water now &middot; red blinking drought '
               '&middot; blue too wet &middot; light blue draining &middot; magenta blinking pump locked. KY-016 / "RGB three colour" '
               'module: R, G, B to the pins, &minus; to GND (common cathode). Bare LED: 220 &Omega; per colour. '
               'Red-green-blue lamp test when switched on.</p>'
               % ('Showing: <b>%s</b>. ' % esc(r.state) if r and r.pwm and r.state else ''))
    return out


def handle(method, path, f):
    from rgb import load_cfg, save_cfg
    if method == 'GET':
        return frag() if path == '/rgb' else None
    if path != '/rgb/save':
        return None
    c = load_cfg()
    on = f.get('on') == '1'
    if on:
        try:
            pins = [int(f.get('p%d' % k, '')) for k in range(3)]
            bright = int(f.get('bright', 60))
            sensor = int(f.get('sensor', -1))
        except ValueError:
            return frag('Invalid value.')
        allowed = _pins(c)
        if len(set(pins)) != 3 or any(p not in allowed for p in pins):
            return frag('Choose three different free pins.')
        if f.get('mode') not in ('on', 'eco', 'off') or not 5 <= bright <= 100 or not -1 <= sensor < len(app.sens.ch):
            return frag('Out of range.')
        if app.leds is not None and any(p in S.LED_PINS for p in pins):
            return frag('Those pins belong to the 4-LED indicator, which is on: switch it off first.')
        c.update(on=True, pins=pins, mode=f.get('mode'), bright=bright, sensor=sensor, anode=f.get('anode') == '1')
    else:
        c['on'] = False
    save_cfg(c)
    first = app.rgb is None
    app.rgb_apply()
    return frag('Saved.' + (' Lamp test: red, green, blue.' if on and first else ''), True)
