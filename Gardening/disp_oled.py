# Monochrome OLED over I2C: SSD1306 (0.96" 128x64, 0.91" 128x32) and SH1106 (1.3" 128x64).
# Frame buffer in RAM (128x64 = 1 KB), sent with show(). Rotation 0 or 180 (done by the chip).
import framebuf


class Oled:
    color = False

    def __init__(self, i2c, w, h, addr=0x3C, sh1106=False, rot=0):
        self.i2c, self.addr, self.w, self.h, self.sh = i2c, addr, w, h, sh1106
        self.pages = h // 8
        self.buf = bytearray(w * self.pages)
        self.fb = framebuf.FrameBuffer(self.buf, w, h, framebuf.MONO_VLSB)
        self.pal = framebuf.FrameBuffer(bytearray(1), 2, 1, framebuf.MONO_HLSB)
        flip = rot == 180
        seq = [0xAE, 0xD5, 0x80, 0xA8, h - 1, 0xD3, 0x00, 0x40,
               0xA0 if flip else 0xA1, 0xC0 if flip else 0xC8, 0xDA, 0x12 if h == 64 else 0x02,
               0x81, 0xCF, 0xD9, 0xF1, 0xDB, 0x40, 0xA4, 0xA6]
        seq += [0xAD, 0x8B] if sh1106 else [0x20, 0x00, 0x8D, 0x14]    # charge pump
        for c in seq:
            self.cmd(c)
        self.fill(0)
        self.show()
        self.cmd(0xAF)

    def cmd(self, c):
        self.i2c.writeto(self.addr, bytes((0x80, c)))

    def col(self, name):
        return 0 if name == 'bg' else 1

    def fill(self, c):
        self.fb.fill(c)

    def fill_rect(self, x, y, w, h, c):
        self.fb.fill_rect(x, y, w, h, c)

    def draw(self, s, x, y, font=None, fg=1, bg=0):
        """Text at x, y (top left); font = gfont.Font or None for the built-in 8x8. Returns the width."""
        if font is None:
            self.fb.fill_rect(x, y, 8 * len(s), 8, bg)
            self.fb.text(s, x, y, fg)
            return 8 * len(s)
        self.pal.pixel(0, 0, bg)
        self.pal.pixel(1, 0, fg)
        x0 = x
        for ch in s:
            w, g = font.glyph(ch)
            self.fb.blit(framebuf.FrameBuffer(g, w, font.h, framebuf.MONO_HLSB), x, y, -1, self.pal)
            x += w
        return x - x0

    def show(self):
        if self.sh:                                   # SH1106: 132 columns, page by page, offset 2
            w = self.w
            for p in range(self.pages):
                for c in (0xB0 | p, 0x02, 0x10):
                    self.cmd(c)
                self.i2c.writevto(self.addr, (b'\x40', memoryview(self.buf)[p * w:(p + 1) * w]))
            return
        for c in (0x21, 0, self.w - 1, 0x22, 0, self.pages - 1):
            self.cmd(c)
        self.i2c.writevto(self.addr, (b'\x40', self.buf))

    def power(self, on):
        self.cmd(0xAF if on else 0xAE)

    def contrast(self, pct):
        self.cmd(0x81)
        self.cmd(max(1, min(255, pct * 255 // 100)))
