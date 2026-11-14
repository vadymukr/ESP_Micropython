# Bitmap fonts for the displays (fonts/*.bin from tools/make_fonts.py). Glyphs are read from flash
# when drawn: an open font costs ~0.5 KB of RAM (its index), whatever its size.
import struct

DIR = 'fonts/'


class Font:
    def __init__(self, name):
        self.name = name
        self.f = open(DIR + name + '.bin', 'rb')
        hd = self.f.read(6)
        self.h, n = hd[3], struct.unpack('<H', hd[4:6])[0]
        idx = self.f.read(7 * n)
        self.chars = ''.join(chr(struct.unpack_from('<H', idx, 7 * i)[0]) for i in range(n))
        self.idx = idx

    def _ent(self, ch):
        i = self.chars.find(ch)
        if i < 0:
            i = self.chars.find(' ')
        _, w, off = struct.unpack_from('<HBI', self.idx, 7 * i)
        return w, off

    def has(self, s):
        return all(self.chars.find(c) >= 0 for c in s)

    def width(self, s):
        return sum(self._ent(c)[0] for c in s)

    def glyph(self, ch):
        """(width, bytearray of MONO_HLSB rows)."""
        w, off = self._ent(ch)
        self.f.seek(off)
        return w, bytearray(self.f.read(((w + 7) // 8) * self.h))

    def close(self):
        self.f.close()


_open = {}


def get(name):
    f = _open.get(name)
    if f is None:
        f = _open[name] = Font(name)
    return f


def close_all():
    for f in _open.values():
        f.close()
    _open.clear()
