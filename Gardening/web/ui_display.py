# Displays panel (/display, /display/add, /display/del, /display/test) - loaded per request by
# app.py, then released.
import app
from app import esc
import time
import settings as S

MAX = 2
TYPES = (('ssd1306-64', 'OLED 0.96" 128x64 SSD1306 (I2C)', 'i2c'),
         ('ssd1306-32', 'OLED 0.91" 128x32 SSD1306 (I2C)', 'i2c'),
         ('sh1106-64', 'OLED 1.3" 128x64 SH1106 (I2C)', 'i2c'),
         ('st7789-240', 'TFT 1.3" IPS 240x240 ST7789 (SPI, 7 pin)', 'spi'),
         ('st7789-135', 'TFT 1.14" IPS 135x240 ST7789 (SPI)', 'spi'),
         ('st7789-320', 'TFT 2.0" 240x320 ST7789 (SPI)', 'spi'),
         ('st7735-160', 'TFT 1.8" 128x160 ST7735 (SPI)', 'spi'),
         ('st7735-80', 'TFT 0.96" IPS 80x160 ST7735 (SPI)', 'spi'),
         ('st7735-128', 'TFT 1.44" 128x128 ST7735 (SPI)', 'spi'),
         ('ili9341-320', 'TFT 2.4"/2.8" 240x320 ILI9341 (SPI)', 'spi'))
PAGES = (('soil', 'Soil sensors, one page each'), ('all', 'All soil sensors on one page'), ('air', 'Air'),
         ('gen', 'Other sensors, one page each'), ('pump', 'Pump / water today'), ('ctrl', 'Controllers (heater, light, fan)'), ('batt', 'Battery'),
         ('clock', 'Clock and connections'))
DEFAULT = {'type': 'ssd1306-64', 'addr': 60, 'rot': 0, 'pages': ['soil', 'air'], 'page_s': 5, 'bright': 80,
           'off': '', 'xoff': 0, 'yoff': 0}
SPI_PINS = (('sck', 'SCL / SCK (clock)', False), ('mosi', 'SDA / MOSI (data)', False), ('dc', 'DC (data/command)', False),
            ('rst', 'RES / RST (reset)', True), ('cs', 'CS (chip select)', True), ('bl', 'BLK / BL (backlight)', True),
            ('touch', 'T_IRQ (touch: next page)', True))


def _free():
    import sys
    import sensors as R
    try:
        t = R.taken_pins(app.sens.reg)
        free = [p for p in R.IO_PINS if p not in t]
        if app.leds is None:
            free += [p for p in S.LED_PINS if p in R.IO_PINS and p not in free and p not in t]
        return sorted(free)
    finally:
        R = None
        sys.modules.pop('sensors', None)


def frag(msg='', ok=False):
    from display import load_cfg
    c = load_cfg()
    d = app.disp
    names = dict((k, lbl) for k, lbl, _ in TYPES)
    out = []
    if msg:
        out.append('<p class="%s">%s</p>' % ('ok' if ok else 'warn', esc(msg)))
    for k, e in enumerate(c['list']):
        conn = ('I2C 0x%02X (SDA %d, SCL %d)' % (e.get('addr', 60), S.I2C_SDA, S.I2C_SCL) if e['type'][:2] in ('ss', 'sh')
                else 'SPI ' + ', '.join('%s %d' % (p.upper(), e[p]) for p, _, _ in SPI_PINS if e.get(p) is not None))
        out.append('<div class="card" style="margin:8px 0"><b>%d. %s</b><br><span class="muted">%s &middot; turned %d&deg; '
                   '&middot; pages: %s every %d s%s</span><br>%s '
                   '<button class="small" hx-post="/display/test" hx-vals=\'{"k":"%d"}\' hx-target="#display">Test pattern</button> '
                   '<button class="small danger" hx-post="/display/del" hx-vals=\'{"k":"%d"}\' hx-target="#display">Remove</button></div>'
                   % (k + 1, esc(names.get(e['type'], e['type'])), conn, e.get('rot', 0), ', '.join(e['pages']),
                      e.get('page_s', 5), ' &middot; off %s' % esc(e['off']) if e.get('off') else '',
                      esc(d.status(k)) if d else 'starting...', k, k))
    if len(c['list']) >= MAX:
        out.append('<p class="muted">Two displays at most.</p>')
        return out + _help()
    free = _free()
    opt = lambda none: ('<option value="">none</option>' if none else '') + ''.join(
        '<option value="%d">GPIO%d</option>' % (p, p) for p in free)
    out.append('<form class="set" hx-post="/display/add" hx-target="#display" x-data="{t:\'ssd1306-64\'}">'
               '<b style="flex-basis:100%">Add a display</b><label>Type<select name="type" x-model="t">')
    for k, lbl, _ in TYPES:
        out.append('<option value="%s">%s</option>' % (k, lbl))
    out.append('</select></label><label x-show="t.startsWith(\'ss\')||t.startsWith(\'sh\')">I2C address<select name="addr">'
               '<option value="60">0x3C</option><option value="61">0x3D</option></select></label>'
               '<span x-show="t.startsWith(\'st\')" x-cloak style="display:contents">')
    for k, (p, lbl, none) in enumerate(SPI_PINS):
        out.append('<label>%s<select name="%s">%s</select></label>' % (lbl, p, opt(none)))
    out.append('</span><label>Turned<select name="rot"><option value="0">0&deg;</option>'
               '<option value="90" x-show="t.startsWith(\'st\')">90&deg;</option><option value="180">180&deg;</option>'
               '<option value="270" x-show="t.startsWith(\'st\')">270&deg;</option></select></label>'
               '<label>Next page every, s<input name="page_s" type="number" min="2" max="120" value="5" style="width:5em"></label>'
               '<label>Brightness, %<input name="bright" type="number" min="5" max="100" value="80" style="width:5em"></label>'
               '<label>Off at night<input name="off" maxlength="11" placeholder="22:00-06:00" style="width:8em"></label>'
               '<span style="flex-basis:100%;font-size:13px;color:var(--muted)">Pages:</span>')
    for k, lbl in PAGES:
        out.append('<label class="row" style="flex-direction:row;align-items:center;gap:6px"><input type="checkbox" '
                   'name="pg_%s" value="1"%s> %s</label>' % (k, ' checked' if k in ('soil', 'air') else '', lbl))
    out.append('<details x-show="t.startsWith(\'st\')" style="flex-basis:100%"><summary class="muted">Picture shifted or '
               'colours wrong?</summary><label>X offset<input name="xoff" type="number" min="-80" max="80" value="0" '
               'style="width:5em"></label> <label>Y offset<input name="yoff" type="number" min="-80" max="80" value="0" '
               'style="width:5em"></label> <label class="row" style="flex-direction:row;align-items:center;gap:6px">'
               '<input type="checkbox" name="swap_inv" value="1"> Invert colours the other way</label>'
               '<label class="row" style="flex-direction:row;align-items:center;gap:6px"><input type="checkbox" '
               'name="swap_bgr" value="1"> Red and blue swapped</label></details><button>Add</button></form>')
    return out + _help()


def test(self, k):
    """Test pattern on display k: colour bars / frame and the big font."""
    d = self.devs[k][1] if k < len(self.devs) else None
    if not d:
        return
    import gfont
    if d.color:
        w = d.w // 6
        for i, n in enumerate(('dry', 'now', 'soon', 'good', 'drain', 'wet')):
            d.fill_rect(i * w, 0, w if i < 5 else d.w - 5 * w, d.h, d.col(n))
    else:
        d.fill(0)
        d.fill_rect(0, 0, d.w, 1, 1)
        d.fill_rect(0, d.h - 1, d.w, 1, 1)
        d.fill_rect(0, 0, 1, d.h, 1)
        d.fill_rect(d.w - 1, 0, 1, d.h, 1)
    d.draw('88', 4, 4, gfont.get('n24'), d.col('fg'), d.col('bg'))
    d.draw('%dx%d' % (d.w, d.h), 4, d.h - 12, None, d.col('fg'), d.col('bg'))
    d.show()
    self.devs[k][5] = 'test'                          # redraw fully after 5 s
    self.devs[k][4] = time.time() + 5


def _help():
    return ['<p class="muted">OLED (I2C): VCC 3.3 V, GND, SDA to GPIO%d, SCL to GPIO%d - the bus the air sensor uses. '
            'TFT (SPI): VCC 3.3 V, GND, then the pins chosen above; BLK can go to 3.3 V (always on) instead. '
            '1.3&quot; IPS boards with 7 pins and no CS are ST7789 240x240. The big number is the soil moisture; the '
            'colour follows the watering band. Night hours switch the screen off (OLEDs last longer).</p>'
            % (S.I2C_SDA, S.I2C_SCL)]


def handle(method, path, f):
    from display import load_cfg, save_cfg
    if method == 'GET':
        return frag() if path == '/display' else None
    c = load_cfg()
    if path == '/display/test':
        try:
            k = int(f.get('k', -1))
        except ValueError:
            k = -1
        if not app.disp or not 0 <= k < len(app.disp.devs) or not app.disp.devs[k][1]:
            return frag('That display is not running.')
        test(app.disp, k)
        return frag('Test pattern on display %d for 5 s: colour bars (TFT) or a frame (OLED), "88" and the size.'
                    % (k + 1), True)
    if path == '/display/del':
        try:
            k = int(f.get('k', -1))
            c['list'].pop(k)
        except (ValueError, IndexError):
            return frag('No such display.')
        save_cfg(c)
        app.display_apply_soon()
        return frag('Removed.', True)
    if path != '/display/add':
        return None
    t = f.get('type')
    kind = dict((k, kd) for k, _, kd in TYPES).get(t)
    if not kind or len(c['list']) >= MAX:
        return frag('Choose a display type.')
    e = dict(DEFAULT)
    e['type'] = t
    try:
        e['rot'] = int(f.get('rot', 0))
        e['page_s'] = int(f.get('page_s', 5))
        e['bright'] = int(f.get('bright', 80))
        e['xoff'] = int(f.get('xoff', 0) or 0)
        e['yoff'] = int(f.get('yoff', 0) or 0)
    except ValueError:
        return frag('Invalid number.')
    if e['rot'] not in (0, 90, 180, 270) or (kind == 'i2c' and e['rot'] not in (0, 180)) \
            or not 2 <= e['page_s'] <= 120 or not 5 <= e['bright'] <= 100:
        return frag('Out of range.')
    off = f.get('off', '').strip()
    if off and not (len(off) == 11 and off[2] == ':' and off[5] == '-' and off[8] == ':'
                    and (off[:2] + off[3:5] + off[6:8] + off[9:]).isdigit()):
        return frag('Night hours as HH:MM-HH:MM, e.g. 22:00-06:00.')
    e['off'] = off
    e['pages'] = [k for k, _ in PAGES if f.get('pg_' + k) == '1']
    if not e['pages']:
        return frag('Choose at least one page.')
    if kind == 'i2c':
        e['addr'] = 61 if f.get('addr') == '61' else 60
        if any(x.get('addr', 60) == e['addr'] and x['type'][:2] in ('ss', 'sh') for x in c['list']):
            return frag('Another OLED already uses that address: set the other one to 0x3D (solder jumper on the back).')
    else:
        if any(x['type'].startswith('st') for x in c['list']):
            return frag('One colour TFT at most (one SPI bus).')
        free = _free()
        used = []
        for p, lbl, none in SPI_PINS:
            v = f.get(p, '')
            if v == '' and none:
                e[p] = None
                continue
            try:
                v = int(v)
            except ValueError:
                return frag('Choose a pin for %s.' % lbl)
            if v not in free or v in used:
                return frag('Pins must be free and different.')
            used.append(v)
            e[p] = v
        if f.get('swap_inv') == '1':
            e['inv'] = not (t.startswith('st7789') or t == 'st7735-80')
        if f.get('swap_bgr') == '1':
            e['bgr'] = not (t == 'st7735-80')
    c['list'].append(e)
    save_cfg(c)
    app.display_apply_soon()
    return frag('Added. It starts in a moment - open this panel again to see whether it runs.', True)
