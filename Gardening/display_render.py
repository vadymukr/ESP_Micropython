# Display layout: draws one page (display_pages.page()) on any screen size with a driver from
# disp_oled.py / disp_tft.py. Three layouts: list of rows (page 'all'), 128x32 (big number left, text
# right), and the normal page (header bar, biggest number that fits, one or two text lines).
# Colour TFTs are drawn without a frame buffer: every element clears only its own box (no flicker).
# Spec: docs/SPEC.md#displays
import gfont

SHORT = {'dry': 'DRY!', 'now': 'WATER', 'soon': 'Soon', 'good': 'Good', 'drain': 'Drain', 'wet': 'Wet'}
BIG = ('n120', 'n80', 'n64', 'n44', 'n32', 'n24')     # digit fonts, biggest first


def render(d, pg, full):
    """Draw page pg on driver d; full = clear first (page changed). Mono screens always redraw whole."""
    W, colr = d.w, d.color
    bg = d.col('bg')
    if full or not colr:
        d.fill(bg)
    small = gfont.get('t24') if colr and W >= 200 else gfont.get('t16') if colr else None
    if 'rows' in pg:
        _rows(d, pg, small)
    elif d.h <= 32:
        _small32(d, pg)
    else:
        _page(d, pg, small)
    d.show()


def _rows(d, pg, small):
    """Page 'all': one row per soil sensor (colour mark, name, value)."""
    W, H, colr = d.w, d.h, d.color
    bg, fg = d.col('bg'), d.col('fg')
    sh = small.h if small else 8
    y = 0
    if H > 32:
        d.draw(_fit(pg['t'], W, small), 0, 0, small, fg, bg)
        y = sh + 2
    rh = max(sh, (H - y) // max(1, len(pg['rows'])))
    for name, v, k in pg['rows']:
        if y + sh > H:
            break
        c = d.col(k) if colr else fg
        if colr:
            d.fill_rect(0, y, 6, sh, c)
        vw = small.width(v) if small else 8 * len(v)
        d.draw(_fit(name, W - vw - 14, small), 10 if colr else 0, y, small, fg, bg)
        d.draw(v, W - vw, y, small, c, bg)
        if not colr:
            d.draw(SHORT.get(k, '')[:1], W - vw - 10, y, None, fg, bg)
        y += rh


def _small32(d, pg):
    """128x32 OLED: big number on the left, title / band / one line in 8x8 text on the right."""
    W = d.w
    bg, fg = d.col('bg'), d.col('fg')
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


def _value_font(v, avail, uw, W, colr):
    """Biggest digit font for value v within height avail and width W (unit uw px); else a text font / None."""
    for name in BIG:
        try:
            c = gfont.get(name)
        except OSError:
            continue
        if c.h <= avail and c.has(v) and c.width(v) + uw <= W:
            return c
    for name in (('t24', 't16') if colr else ()):         # narrow screen: text fonts, then 8x8
        c = gfont.get(name)
        if c.width(v) + uw <= W:
            return c
    return None


def _page(d, pg, small):
    """Normal page: header bar (band colour on TFT), centred big value + unit, text lines at the bottom."""
    W, H, colr = d.w, d.h, d.color
    bg, fg = d.col('bg'), d.col('fg')
    bc = d.col(pg['b']) if colr and pg.get('b') else fg
    sh = small.h if small else 8
    hh = sh + (4 if colr else 2)
    if colr:
        d.fill_rect(0, 0, W, hh, bc)
        d.draw(_fit(pg['t'], W - 8, small), 4, 2, small, d.col('black'), bc)
    else:
        d.draw(_pad(pg['t'], W // 8), 0, 0, None, fg, bg)
    lines = pg['l'][:2 if H >= 100 else 1]
    fh = (sh + 2) * len(lines)
    avail = H - hh - fh - 2
    _value(d, pg['v'], pg['u'], small, hh, avail, bc if colr else fg)
    y = H - fh
    for k, t in enumerate(lines):
        c = bc if (colr and k == 0) else d.col('muted') if colr else fg
        tw = small.width(_fit(t, W, small)) if small else 8 * min(len(t), W // 8)
        tx = (W - tw) // 2
        d.fill_rect(0, y, tx, sh + 2, bg)
        d.draw(_fit(t, W, small), tx, y, small, c, bg)
        d.fill_rect(tx + tw, y, W - tx - tw, sh + 2, bg)
        y += sh + 2


def _value(d, v, u, uf, hh, avail, col):
    """The big value and its unit, centred in the band below the header; clears around them."""
    W, bg, fg = d.w, d.col('bg'), d.col('fg')
    uw = (uf.width(u) if uf else 8 * len(u)) + 2 if u else 0
    vf = _value_font(v, avail, uw, W, d.color)
    if vf is None and uf and 8 * len(v) + uw > W:
        uf, uw = None, 8 * len(u) + 2 if u else 0          # unit in 8x8 too
    vh = vf.h if vf else 8
    vw = vf.width(v) if vf else 8 * len(v)
    x = (W - vw - uw) // 2
    y = hh + max(0, (avail - vh) // 2)
    d.fill_rect(0, hh, x, avail + 2, bg)                   # left, above and below the number
    d.fill_rect(x, hh, vw + uw, y - hh, bg)
    d.fill_rect(x, y + vh, vw + uw, hh + avail + 2 - y - vh, bg)
    d.draw(v, x, y, vf, col, bg)
    if u:
        d.fill_rect(x + vw, y, 2, vh, bg)
        d.draw(u, x + vw + 2, y, uf, fg, bg)
    d.fill_rect(x + vw + uw, hh, W - x - vw - uw, avail + 2, bg)
    if u:                                                  # below a unit shorter than the number
        uh = uf.h if uf else 8
        d.fill_rect(x + vw, y + uh, uw, max(0, vh - uh), bg)


def _pad(t, n):
    """t cut or space-padded to exactly n characters (8x8 text overwrites the old text)."""
    t = t[:n]
    return t + ' ' * (n - len(t))


def _fit(t, w, font):
    """t shortened until it fits w pixels in font (None = 8x8)."""
    if font is None:
        return t[:max(0, w // 8)]
    while t and font.width(t) > w:
        t = t[:-1]
    return t
