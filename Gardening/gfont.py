# Bitmap fonts for the displays (fonts/*.bin from tools/make_fonts.py). Glyphs are read from flash
# when drawn: an open font costs ~0.5 KB of RAM (its index), whatever its size.
# Spec: docs/SPEC.md#displays
import struct

DIR = 'fonts/'
_GBUF = bytearray(1800)        # one glyph (the biggest: n120, 15 bytes x 120 rows)


class Font:
    """One bitmap font file (index in RAM, glyphs read from flash when drawn)."""
    def __init__(self, name):
        """Open fonts/<name>.bin and read its index."""
        self.name = name
        self.f = open(DIR + name + '.bin', 'rb')
        hd = self.f.read(6)
        self.h, n = hd[3], struct.unpack('<H', hd[4:6])[0]
        idx = self.f.read(7 * n)
        self.chars = ''.join(chr(struct.unpack_from('<H', idx, 7 * i)[0]) for i in range(n))
        self.idx = idx

    def _ent(self, ch):
        """(width, file offset) of a character (unknown characters draw as a space)."""
        i = self.chars.find(ch)
        if i < 0:
            i = self.chars.find(' ')
        _, w, off = struct.unpack_from('<HBI', self.idx, 7 * i)
        return w, off

    def has(self, s):
        """True if every character of s is in the font."""
        return all(self.chars.find(c) >= 0 for c in s)

    def width(self, s):
        """Width of s in pixels."""
        return sum(self._ent(c)[0] for c in s)

    def glyph(self, ch):
        """(width, MONO_HLSB rows) of a character, read into the shared glyph buffer: valid until the next
        glyph() call (the drivers blit it at once). No allocation per character."""
        w, off = self._ent(ch)
        n = ((w + 7) // 8) * self.h
        self.f.seek(off)
        mv = memoryview(_GBUF if n <= len(_GBUF) else bytearray(n))[:n]
        self.f.readinto(mv)
        return w, mv

    def close(self):
        """Close the font file."""
        self.f.close()


_open = {}


def get(name):
    """The open font name (opened once, cached)."""
    f = _open.get(name)
    if f is None:
        f = _open[name] = Font(name)
    return f


def close_all():
    """Close every cached font (displays removed)."""
    for f in _open.values():
        f.close()
    _open.clear()
