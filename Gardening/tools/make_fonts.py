#!/usr/bin/env python3
"""Build the bitmap fonts for the displays (web/fonts/*.bin), read glyph by glyph from flash by
gfont.py, so a font costs ~0.5 KB of RAM however big it is.

  nNN  big digits "0123456789.-+:" + space, NN px = digit height (the number on the screen)
  tNN  text (ASCII 32-126 and the degree sign), NN px = line height

File: b'GF', version 1, cell height (u8), glyph count (u16), then per glyph its code (u16 LE),
width (u8), data offset (u32 LE); then the glyphs, MONO_HLSB rows (bytes per row = (w+7)//8).
Needs Pillow:  python3 tools/make_fonts.py
Spec: docs/SPEC.md#displays
"""
import os
import struct
import sys

from PIL import Image, ImageDraw, ImageFont

FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf'
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'web', 'fonts')
DIGITS = ' 0123456789.-+:'
TEXT = ''.join(chr(c) for c in range(32, 127)) + '\xb0'
BIG = (24, 32, 44, 64, 80, 120)
SMALL = (16, 24)


def pack(img):
    """An image as MONO_HLSB rows (bytes per row = (w + 7) // 8)."""
    w, h = img.size
    px = img.load()
    out = bytearray()
    for y in range(h):
        for bx in range(0, w, 8):
            b = 0
            for k in range(8):
                x = bx + k
                if x < w and px[x, y] > 110:
                    b |= 0x80 >> k
            out.append(b)
    return bytes(out)


def build(name, chars, cell, size_for):
    """Render chars into one font file of cell height cell; returns its size in bytes."""
    size, top = size_for(cell)
    f = ImageFont.truetype(FONT, size)
    glyphs = []
    for ch in chars:
        w = max(1, round(f.getlength(ch)))
        img = Image.new('L', (w, cell), 0)
        ImageDraw.Draw(img).text((0, -top), ch, font=f, fill=255)
        glyphs.append((ord(ch), w, pack(img)))
    head = bytearray(b'GF' + bytes([1, cell]) + struct.pack('<H', len(glyphs)))
    off = len(head) + 7 * len(glyphs)
    idx, data = bytearray(), bytearray()
    for code, w, g in glyphs:
        idx += struct.pack('<HBI', code, w, off + len(data))
        data += g
    with open(os.path.join(OUT, name + '.bin'), 'wb') as fh:
        fh.write(head + idx + data)
    return len(head) + len(idx) + len(data)


def digits_size(cell):
    """Font size whose digit '0' is `cell` px tall; top = pixels above the digit top."""
    for size in range(cell, cell * 2):
        b = ImageFont.truetype(FONT, size).getbbox('0123456789')
        if b[3] - b[1] >= cell:
            b = ImageFont.truetype(FONT, size - 1).getbbox('0123456789')
            return size - 1, b[1]
    raise ValueError(cell)


def text_size(cell):
    """Font size whose ascent + descent fits `cell` px."""
    for size in range(cell, 4, -1):
        f = ImageFont.truetype(FONT, size)
        b = f.getbbox('Hgjpq|\xb0')
        if b[3] - min(b[1], 0) <= cell:
            return size, min(b[1], 0) if b[1] < 0 else max(0, b[1] - 1) if b[1] > 2 else 0
    raise ValueError(cell)


def main():
    """Build all digit and text fonts into web/fonts/."""
    os.makedirs(OUT, exist_ok=True)
    total = 0
    for c in BIG:
        n = build('n%d' % c, DIGITS, c, digits_size)
        total += n
        print('n%-3d %6d bytes' % (c, n))
    for c in SMALL:
        n = build('t%d' % c, TEXT, c, text_size)
        total += n
        print('t%-3d %6d bytes' % (c, n))
    print('total %d KB in %s' % (total // 1024, os.path.normpath(OUT)))


if __name__ == '__main__':
    sys.exit(main())
