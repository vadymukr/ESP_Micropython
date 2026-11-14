# Colour TFT over SPI: ST7789 (1.3" 240x240 IPS 7-pin, 1.14" 135x240, 2.0" 240x320), ST7735
# (1.8" 128x160, 0.96" 80x160 IPS, 1.44" 128x128) and ILI9341 (2.4"/2.8" 240x320, e.g. the
# ESP32-2432S028 "Cheap Yellow Display"). No frame buffer (240x240 would need 115 KB):
# everything is drawn straight to the screen in small strips (<= 2 KB), text with an opaque
# background, so values update without flicker.
import time
import framebuf
from machine import Pin, SPI, PWM

# (x, y) offset of the visible area per rotation 0 / 90 / 180 / 270
OFFSETS = {'st7789-240': ((0, 0), (0, 0), (0, 80), (80, 0)),
           'st7789-135': ((52, 40), (40, 53), (53, 40), (40, 52)),
           'st7789-320': ((0, 0),) * 4,
           'st7735-160': ((0, 0),) * 4,
           'st7735-80': ((26, 1), (1, 26), (26, 1), (1, 26)),
           'st7735-128': ((2, 1), (1, 2), (2, 3), (3, 2)),
           'ili9341-320': ((0, 0),) * 4}
MADCTL = (0x00, 0x60, 0xC0, 0xA0)
MADCTL_ILI = (0x40, 0x20, 0x80, 0xE0)        # ILI9341: portrait = MX
COLORS = {'bg': 0x0000, 'fg': 0xFFFF, 'muted': 0x8410, 'good': 0x2E8A, 'soon': 0xFE40, 'now': 0xFA20,
          'dry': 0xF800, 'wet': 0x2B3F, 'drain': 0x5D7F, 'black': 0x0000}


def _sw(c):                       # framebuf keeps RGB565 little-endian, the panel wants big-endian
    return ((c & 0xFF) << 8) | (c >> 8)


class Tft:
    color = True

    def __init__(self, e, spi_id):
        """e: display entry (type, sck, mosi, dc, rst, cs, bl, rot, xoff, yoff, inv, bgr)."""
        t = e['type']
        self.st7789 = t.startswith('st7789')
        ili = t.startswith('ili')
        w, h = {'ili9341-320': (240, 320), 'st7789-240': (240, 240), 'st7789-135': (135, 240), 'st7789-320': (240, 320),
                'st7735-160': (128, 160), 'st7735-80': (80, 160), 'st7735-128': (128, 128)}[t]
        r = (e.get('rot', 0) // 90) % 4
        self.w, self.h = (h, w) if r % 2 else (w, h)
        xo, yo = OFFSETS[t][r]
        self.xo, self.yo = xo + e.get('xoff', 0), yo + e.get('yoff', 0)
        nocs = e.get('cs') is None
        self.spi = SPI(spi_id, baudrate=16000000 if t.startswith('st7735') else 26000000, polarity=1 if nocs else 0,
                       phase=1 if nocs else 0, sck=Pin(e['sck']), mosi=Pin(e['mosi']))
        self.dc = Pin(e['dc'], Pin.OUT, value=0)
        self.cs = None if nocs else Pin(e['cs'], Pin.OUT, value=1)
        self.bl = PWM(Pin(e['bl']), freq=1000) if e.get('bl') is not None else None
        if e.get('rst') is not None:
            rst = Pin(e['rst'], Pin.OUT, value=1)
            time.sleep_ms(5)
            rst.value(0)
            time.sleep_ms(20)
            rst.value(1)
            time.sleep_ms(120)
        self.cmd(0x01)                                     # software reset
        time.sleep_ms(150)
        self.cmd(0x11)                                     # sleep out
        time.sleep_ms(120)
        if ili:                                            # power / VCOM as the vendor init
            for c, d in ((0xC0, b'\x23'), (0xC1, b'\x10'), (0xC5, b'\x3e\x28'), (0xC7, b'\x86'), (0xB1, b'\x00\x18')):
                self.cmd(c, d)
        elif not self.st7789:
            self.cmd(0xB1, b'\x01\x2c\x2d')                # frame rate
        self.cmd(0x3A, b'\x05' if t.startswith('st7735') else b'\x55')    # 16 bit colour
        mad = MADCTL_ILI[r] if ili else MADCTL[r]
        self.cmd(0x36, bytes((mad | (0x08 if e.get('bgr', ili or t == 'st7735-80') else 0),)))
        self.cmd(0x21 if e.get('inv', self.st7789 or t == 'st7735-80') else 0x20)
        self.cmd(0x13)
        self.fill(0)
        self.cmd(0x29)                                     # display on
        self.pal = framebuf.FrameBuffer(bytearray(4), 2, 1, framebuf.RGB565)
        self.contrast(e.get('bright', 80))

    def cmd(self, c, data=None):
        if self.cs:
            self.cs.value(0)
        self.dc.value(0)
        self.spi.write(bytes((c,)))
        if data:
            self.dc.value(1)
            self.spi.write(data)
        if self.cs:
            self.cs.value(1)

    def _win(self, x, y, w, h):
        x += self.xo
        y += self.yo
        self.cmd(0x2A, bytes((x >> 8, x & 0xFF, (x + w - 1) >> 8, (x + w - 1) & 0xFF)))
        self.cmd(0x2B, bytes((y >> 8, y & 0xFF, (y + h - 1) >> 8, (y + h - 1) & 0xFF)))
        if self.cs:
            self.cs.value(0)
        self.dc.value(0)
        self.spi.write(b'\x2c')
        self.dc.value(1)

    def _end(self):
        if self.cs:
            self.cs.value(1)

    def col(self, name):
        return COLORS.get(name, 0xFFFF)

    def fill_rect(self, x, y, w, h, c):
        x, y = max(0, x), max(0, y)
        w, h = min(w, self.w - x), min(h, self.h - y)
        if w <= 0 or h <= 0:
            return
        self._win(x, y, w, h)
        n = w * h
        chunk = bytes((c >> 8, c & 0xFF)) * min(n, 512)
        while n > 0:
            k = min(n, 512)
            self.spi.write(chunk if k == 512 else chunk[:2 * k])
            n -= k
        self._end()

    def fill(self, c):
        self.fill_rect(0, 0, self.w, self.h, c)

    def draw(self, s, x, y, font=None, fg=0xFFFF, bg=0):
        """Text at x, y (top left), opaque; font = gfont.Font or None (built-in 8x8). Returns the width."""
        self.pal.pixel(0, 0, _sw(bg))
        self.pal.pixel(1, 0, _sw(fg))
        x0 = x
        if font is None:
            for i in range(0, len(s), 8):                  # 8 characters = 64x8 px strip (1 KB)
                part = s[i:i + 8]
                w = 8 * len(part)
                b = bytearray(w * 8 * 2)
                fb = framebuf.FrameBuffer(b, w, 8, framebuf.RGB565)
                fb.fill(_sw(bg))
                fb.text(part, 0, 0, _sw(fg))
                self._blit(b, x, y, w, 8)
                x += w
            return x - x0
        h = font.h
        for ch in s:
            w, g = font.glyph(ch)
            if x >= self.w:
                break
            src = framebuf.FrameBuffer(g, w, h, framebuf.MONO_HLSB)
            rows = max(1, 1024 // w)                       # strip of <= 2 KB
            b = bytearray(w * rows * 2)
            fb = framebuf.FrameBuffer(b, w, rows, framebuf.RGB565)
            for r0 in range(0, h, rows):
                n = min(rows, h - r0)
                fb.blit(src, 0, -r0, -1, self.pal)
                self._blit(b, x, y + r0, w, n)
            x += w
        return x - x0

    def _blit(self, b, x, y, w, h):
        """Send the first h rows of an RGB565 strip w px wide, clipped to the screen."""
        if x >= self.w or y >= self.h or x < 0 or y < 0:
            return
        cw, ch = min(w, self.w - x), min(h, self.h - y)
        mv = memoryview(b)
        self._win(x, y, cw, ch)
        if cw == w:
            self.spi.write(mv[:w * ch * 2])
        else:
            for r in range(ch):
                self.spi.write(mv[r * w * 2:(r * w + cw) * 2])
        self._end()

    def show(self):
        pass

    def power(self, on):
        self.cmd(0x29 if on else 0x28)
        if self.bl:
            self.bl.duty(self._duty if on else 0)

    def contrast(self, pct):
        self._duty = max(5, min(100, pct)) * 1023 // 100
        if self.bl:
            self.bl.duty(self._duty)
