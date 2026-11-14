# Displays (feature 'display'): OLED (SSD1306 / SH1106, I2C) and colour TFT (ST7789 / ST7735, SPI)
# showing the soil bands with a big number, rotating through pages. Set up from the dashboard
# Displays panel (DATA_DIR/display.json); not loaded while the feature is off or no display is set.
#
# Pages are built as plain data (title, value, unit, band, lines) and laid out for any screen size,
# so another renderer (LVGL on a PSRAM board, later) can reuse page() unchanged.
try:
    import asyncio
except ImportError:
    import uasyncio as asyncio
import json
import os
import time
import settings as S

CFG = S.DATA_DIR + '/display.json'
SHORT = {'dry': 'DRY!', 'now': 'WATER', 'soon': 'Soon', 'good': 'Good', 'drain': 'Drain', 'wet': 'Wet'}
BIG = ('n120', 'n80', 'n64', 'n44', 'n32', 'n24')



def load_cfg():
    """display.json; without one, the board's built-in screen (settings.DISPLAY_BUILTIN), if any."""
    try:
        with open(CFG) as f:
            c = json.load(f)
        if isinstance(c.get('list'), list):
            return c
    except (OSError, ValueError):
        pass
    b = getattr(S, 'DISPLAY_BUILTIN', None)
    return {'list': [dict(b)] if b else []}


def save_cfg(c):
    tmp = CFG + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(c, f)
    os.rename(tmp, CFG)


def _in_hours(spec):
    """'22:00-06:30' -> True while the local time is inside (display off). '' = never."""
    try:
        a, b = spec.split('-')
        a = int(a[:2]) * 60 + int(a[3:5])
        b = int(b[:2]) * 60 + int(b[3:5])
    except (ValueError, IndexError):
        return False
    from app import time_ok
    if not time_ok():
        return False
    t = time.localtime(time.time() + S.TZ_OFFSET_H * 3600)
    m = t[3] * 60 + t[4]
    return a <= m < b if a <= b else m >= a or m < b


# ---------------------------------------------------------------- page data
def pages_of(e):
    """The page list of a display: ('soil', i), ('air', 0)..."""
    import app
    out = []
    for k in e['pages']:
        if k == 'soil':
            out += [('soil', i) for i in range(len(app.sens.ch))]
        elif k == 'gen':
            out += [('gen', j) for j in range(len(app.sens.gch))]
        elif k == 'air' and app.sens.t is None:
            continue
        elif k == 'pump' and not app.pump:
            continue
        elif k == 'batt' and not app.batt:
            continue
        else:
            out.append((k, 0))
    return out or [('clock', 0)]


def page(kind, i):
    """dict: t title, v value, u unit, b band key (colour) or None, l small lines, rows (page 'all')."""
    import app
    from bands import BANDS
    s = app.sens
    if kind == 'soil' and i < len(s.ch):
        c = s.ch[i]
        p = s.pct(s.raw[i], c[2], c[3])
        k = app.bands.band(i, p)
        return {'t': c[4], 'v': '%d' % round(p), 'u': '%', 'b': k, 'l': [BANDS[k], 'F %d%%' % app.bands.full[i]]}
    if kind == 'all':
        rows = []
        for j, c in enumerate(s.ch):
            p = s.pct(s.raw[j], c[2], c[3])
            rows.append((c[4], '%d%%' % round(p), app.bands.band(j, p)))
        return {'t': 'Soil', 'rows': rows, 'b': None}
    if kind == 'air' and s.t is not None:
        k, lbl = (('now', 'Too hot') if s.t > 32 else ('soon', 'Warm') if s.t > 28 else
                  ('soon', 'Too cold') if s.t < 15 else ('good', 'Good'))
        l = [lbl]
        if s.rh is not None:
            l.append('%d%% RH' % round(s.rh) + (' %d hPa' % round(s.p) if s.p is not None else ''))
        return {'t': 'Air', 'v': '%.1f' % s.t, 'u': '\xb0C', 'b': k, 'l': l}
    if kind == 'gen' and i < len(s.gch):
        g, v = s.gch[i], s.gval[i]
        return {'t': g[3], 'v': '-' if v is None else ('%%.%df' % g[5]) % v, 'u': g[4].replace('&deg;', '\xb0'), 'b': None, 'l': []}
    if kind == 'pump' and app.pump:
        c, pm = app.pump.cfg, app.pump
        st = 'LOCKED' if c['fault'] else 'running' if pm.running else 'ready'
        lit = c['flow'] or c['rate_lpm']
        return {'t': 'Water today', 'v': '%.2f' % c['l_day'] if lit else '%d' % c['day_s'], 'u': 'L' if lit else 's',
                'b': 'dry' if c['fault'] else 'wet' if pm.running else 'good',
                'l': ['Pump ' + st, '%d runs%s' % (c['runs_day'], ' %.1f L total' % c['l_total'] if lit else '')]}
    if kind == 'batt' and app.batt and app.batt.v is not None:
        b = app.batt
        return {'t': 'Battery', 'v': '%.2f' % b.v, 'u': 'V', 'b': {'ok': 'good', 'low': 'soon'}.get(b.state, 'dry'),
                'l': [{'ok': 'OK', 'low': 'Low', 'crit': 'CRITICAL'}[b.state]]}
    ok = app.time_ok()
    st = app.stamp() if ok else ''
    l = [app.net.sta.ifconfig()[0] if app.net.sta.isconnected() else 'no WiFi']
    if app.gsm:
        g = app.gsm
        l.append('GSM %s %s' % (g.op or g.state, '%d/31' % g.csq if g.csq not in (None, 99) else ''))
    return {'t': st[:10] if ok else 'Clock not set', 'v': st[11:16] if ok else '--:--', 'u': '', 'b': None, 'l': l}


# ---------------------------------------------------------------- layout
def render(d, pg, full):
    """Draw a page on driver d. full: clear first (page changed)."""
    import gfont
    W, H, colr = d.w, d.h, d.color
    bg, fg = d.col('bg'), d.col('fg')
    bc = d.col(pg['b']) if colr and pg.get('b') else fg
    if full or not colr:                                   # mono: frame buffer, always redrawn whole
        d.fill(bg)
    small = gfont.get('t24') if colr and W >= 200 else gfont.get('t16') if colr else None
    sh = small.h if small else 8
    if 'rows' in pg:                                       # all soil sensors
        y = 0
        if H > 32:
            d.draw(_fit(pg['t'], W, small), 0, 0, small, fg, bg)
            y = sh + 2
        rh = max(sh, (H - y) // max(1, len(pg['rows'])))
        font = small
        for name, v, k in pg['rows']:
            if y + sh > H:
                break
            c = d.col(k) if colr else fg
            if colr:
                d.fill_rect(0, y, 6, sh, c)
            vw = font.width(v) if font else 8 * len(v)
            d.draw(_fit(name, W - vw - 14, font), 10 if colr else 0, y, font, fg, bg)
            d.draw(v, W - vw, y, font, c, bg)
            if not colr:
                d.draw(SHORT.get(k, '')[:1], W - vw - 10, y, None, fg, bg)
            y += rh
        d.show()
        return
    if H <= 32:                                            # 128x32: big number left, text right
        v = pg['v']
        f = gfont.get('n32')
        if f.width(v) + 8 * len(pg['u']) > W - 44:         # keep 5 characters for the text column
            f = gfont.get('n24')
        vw = d.draw(v, 0, (32 - f.h) // 2, f, fg, bg) if f.has(v) else d.draw(v[:6], 0, 12, None, fg, bg)
        uw = d.draw(pg['u'][:3], vw + 1, 0, None, fg, bg) if pg['u'] else 0
        x = vw + max(uw, 0) + 4
        n = (W - x) // 8
        d.fill_rect(vw, 8, x - vw, 24, bg)
        d.draw(_pad(pg['t'], n), x, 0, None, fg, bg)
        d.draw(_pad(SHORT.get(pg['b'], pg['l'][0] if pg['l'] else ''), n), x, 12, None, fg, bg)
        d.draw(_pad(pg['l'][1] if len(pg['l']) > 1 else '', n), x, 24, None, fg, bg)
        d.show()
        return
    hh = sh + (4 if colr else 2)                           # header bar
    if colr:
        d.fill_rect(0, 0, W, hh, bc)
        d.draw(_fit(pg['t'], W - 8, small), 4, 2, small, d.col('black'), bc)
    else:
        d.draw(_pad(pg['t'], W // 8), 0, 0, None, fg, bg)
    lines = pg['l'][:2 if H >= 100 else 1]
    fh = (sh + 2) * len(lines)
    v, u = pg['v'], pg['u']
    uf = small
    uw = (uf.width(u) if uf else 8 * len(u)) + 2 if u else 0
    avail = H - hh - fh - 2
    f = None
    for name in BIG:                                       # biggest digits that fit
        try:
            c = gfont.get(name)
        except OSError:
            continue
        if c.h <= avail and c.has(v) and c.width(v) + uw <= W:
            f = c
            break
    vf = f
    if vf is None:                                         # narrow screen: text fonts, then 8x8
        vf = None
        for name in (('t24', 't16') if colr else ()):
            c = gfont.get(name)
            if c.width(v) + uw <= W:
                vf = c
                break
    if vf is None and uf and 8 * len(v) + uw > W:
        uf, uw = None, 8 * len(u) + 2 if u else 0          # unit in 8x8 too
    vh = vf.h if vf else 8
    vw = vf.width(v) if vf else 8 * len(v)
    x = (W - vw - uw) // 2
    y = hh + max(0, (avail - vh) // 2)
    d.fill_rect(0, hh, x, avail + 2, bg)                   # clear around the number (no full clear)
    d.fill_rect(x, hh, vw + uw, y - hh, bg)                # above and below the number
    d.fill_rect(x, y + vh, vw + uw, hh + avail + 2 - y - vh, bg)
    d.draw(v, x, y, vf, bc if colr else fg, bg)
    if u:
        d.fill_rect(x + vw, y, 2, vh, bg)
        d.draw(u, x + vw + 2, y, uf, fg, bg)
    d.fill_rect(x + vw + uw, hh, W - x - vw - uw, avail + 2, bg)
    if u and uf:
        d.fill_rect(x + vw, y + uf.h, uw, max(0, vh - uf.h), bg)
    elif u:
        d.fill_rect(x + vw, y + 8, uw, max(0, vh - 8), bg)
    y = H - fh
    for k, t in enumerate(lines):
        c = bc if (colr and k == 0) else d.col('muted') if colr else fg
        tw = small.width(_fit(t, W, small)) if small else 8 * min(len(t), W // 8)
        tx = (W - tw) // 2
        d.fill_rect(0, y, tx, sh + 2, bg)
        d.draw(_fit(t, W, small), tx, y, small, c, bg)
        d.fill_rect(tx + tw, y, W - tx - tw, sh + 2, bg)
        y += sh + 2
    d.show()


def _pad(t, n):
    t = t[:n]
    return t + ' ' * (n - len(t))


def _fit(t, w, font):
    if font is None:
        return t[:max(0, w // 8)]
    while t and font.width(t) > w:
        t = t[:-1]
    return t


# ---------------------------------------------------------------- manager
class Display:
    def __init__(self, c, sens):
        self.sens = sens
        self.devs = []                                     # [entry, driver or None, error]
        self.reconfig(c)

    def reconfig(self, c):
        for d in self.devs:
            if d[1]:
                try:
                    d[1].power(False)
                except Exception:
                    pass
        self.devs = []
        spi_ids = [1] if S.CHIP in ('esp32c3', 'esp32c6') else [2, 1]
        for e in c['list']:
            drv, err = None, ''
            try:
                if e['type'][:2] in ('ss', 'sh'):
                    if not self.sens.i2c:
                        raise OSError('no I2C bus')
                    from disp_oled import Oled
                    drv = Oled(self.sens.i2c, 128, 32 if e['type'].endswith('32') else 64, e.get('addr', 60),
                               e['type'].startswith('sh1106'), e.get('rot', 0))
                    drv.contrast(e.get('bright', 80))
                else:
                    if not spi_ids:
                        raise OSError('no free SPI bus')
                    from disp_tft import Tft
                    drv = Tft(e, spi_ids.pop(0))
            except Exception as ex:
                err = str(ex) or repr(ex)
                if 'ENODEV' in err or 'ETIMEDOUT' in err:
                    err = ('no display answers at I2C 0x%02X - check VCC, GND, SDA, SCL and the address'
                           % e.get('addr', 60) if e['type'][:2] in ('ss', 'sh') else err)
                print('display %s: %s' % (e['type'], err))
            self.devs.append([e, drv, err, 0, -1, None, True, 0, 0])
            # entry, drv, err, page idx, test-until, last page, on, page shift (touch), awake-until (touch)
        self.t0 = time.time()
        from machine import Pin
        self.touch = [(Pin(e['touch'], Pin.IN), d) for e, d in zip(c['list'], self.devs) if e.get('touch') is not None]
        if self.touch and getattr(self, 'running', False):
            asyncio.create_task(self._touch())             # reconfigured: the old touch task has ended

    def off(self):
        for d in self.devs:
            if d[1]:
                try:
                    d[1].power(False)
                except Exception:
                    pass

    def status(self, k):
        if k >= len(self.devs):
            return 'starting...'
        e, drv, err = self.devs[k][:3]
        return 'running %dx%d' % (drv.w, drv.h) if drv else 'not running: ' + err

    async def run(self):
        self.running = True
        if self.touch:
            asyncio.create_task(self._touch())
        while self.devs:                                   # all removed: task ends (module unloaded)
            await asyncio.sleep(2)
            for d in self.devs:
                e, drv = d[0], d[1]
                if not drv:
                    continue
                try:
                    self._tick(d, e, drv)
                except Exception as ex:
                    d[2] = str(ex)
                    print('display:', repr(ex))

    async def _touch(self):
        """Touch panel IRQ (XPT2046 T_IRQ goes low while touched): next page; at night: on for 60 s."""
        devs = self.devs
        while self.devs is devs:
            for p, d in self.touch:
                if p.value() == 0:
                    d[8] = time.time() + 60
                    if d[6]:
                        d[7] += 1
                    if d[1]:
                        try:
                            self._tick(d, d[0], d[1])
                        except Exception:
                            pass
                    while p.value() == 0:                  # wait for release
                        await asyncio.sleep_ms(50)
            await asyncio.sleep_ms(80)

    def _tick(self, d, e, drv):
        now = time.time()
        if d[4] > now:                                     # test pattern showing
            return
        off = _in_hours(e.get('off', '')) and d[8] < now
        if off == d[6]:
            drv.power(not off)
            d[6] = not off
            d[5] = None
        if off:
            return
        pl = pages_of(e)
        idx = (int((now - self.t0) // max(2, e.get('page_s', 5))) + d[7]) % len(pl)
        pg = page(*pl[idx])
        full = idx != d[3] or d[5] is None or d[5] == 'test'
        if full or pg != d[5]:
            render(drv, pg, full)
            d[3], d[5] = idx, pg
