# VL53L0X time-of-flight distance sensor (GY-530, GY-VL53L0XV2), I2C 0x29, up to ~1.2-2 m.
# Init follows Pololu's VL53L0X library (ST API sequence): SPAD setup, default tuning, reference
# calibration, then continuous back-to-back ranging; a read just fetches the newest result.
# 0x29 is also the address of VL53L1X and TCS34725: only one of them per bus.
# Spec: docs/SPEC.md#driver-interface
import time

# default tuning settings (register, value), 0xFF = page select
TUNING = bytes((
    0xFF, 0x01, 0x00, 0x00, 0xFF, 0x00, 0x09, 0x00, 0x10, 0x00, 0x11, 0x00, 0x24, 0x01, 0x25, 0xFF,
    0x75, 0x00, 0xFF, 0x01, 0x4E, 0x2C, 0x48, 0x00, 0x30, 0x20, 0xFF, 0x00, 0x30, 0x09, 0x54, 0x00,
    0x31, 0x04, 0x32, 0x03, 0x40, 0x83, 0x46, 0x25, 0x60, 0x00, 0x27, 0x00, 0x50, 0x06, 0x51, 0x00,
    0x52, 0x96, 0x56, 0x08, 0x57, 0x30, 0x61, 0x00, 0x62, 0x00, 0x64, 0x00, 0x65, 0x00, 0x66, 0xA0,
    0xFF, 0x01, 0x22, 0x32, 0x47, 0x14, 0x49, 0xFF, 0x4A, 0x00, 0xFF, 0x00, 0x7A, 0x0A, 0x7B, 0x00,
    0x78, 0x21, 0xFF, 0x01, 0x23, 0x34, 0x42, 0x00, 0x44, 0xFF, 0x45, 0x26, 0x46, 0x05, 0x40, 0x40,
    0x0E, 0x06, 0x20, 0x1A, 0x43, 0x40, 0xFF, 0x00, 0x34, 0x03, 0x35, 0x44, 0xFF, 0x01, 0x31, 0x04,
    0x4B, 0x09, 0x4C, 0x05, 0x4D, 0x04, 0xFF, 0x00, 0x44, 0x00, 0x45, 0x20, 0x47, 0x08, 0x48, 0x28,
    0x67, 0x00, 0x70, 0x04, 0x71, 0x01, 0x72, 0xFE, 0x76, 0x00, 0x77, 0x00, 0xFF, 0x01, 0x0D, 0x01,
    0xFF, 0x00, 0x80, 0x01, 0x01, 0xF8, 0xFF, 0x01, 0x8E, 0x01, 0x00, 0x01, 0xFF, 0x00, 0x80, 0x00))


class Driver:
    """Registry driver: VL53L0X time-of-flight distance sensor (GY-530, GY-VL53L0XV2), I2C 0x29, up to ~.
    Interface: docs/SPEC.md#driver-interface."""
    def __init__(self, e, ctx):
        """Set up from the registry entry e (pins / address / settings); ctx = app.sens (shared I2C bus, free
        UARTs, air readings)."""
        self.e, self.ctx = e, ctx
        self.a = int(e.get('addr', '29'), 16)
        if self.r(0xC0) != 0xEE:
            raise OSError('not a VL53L0X')
        w, r = self.w, self.r
        w(0x89, r(0x89) | 0x01)                               # I/O at 2.8 V
        w(0x88, 0x00)
        self._seq((0x80, 1, 0xFF, 1, 0x00, 0))
        self.stop = r(0x91)
        self._seq((0x00, 1, 0xFF, 0, 0x80, 0))
        w(0x60, r(0x60) | 0x12)                               # disable MSRC / pre-range limit checks
        self.ctx.i2c.writeto_mem(self.a, 0x44, b'\x00\x20')   # signal rate limit 0.25 MCPS
        w(0x01, 0xFF)
        count, aperture = self._spad_info()
        spads = bytearray(self.ctx.i2c.readfrom_mem(self.a, 0xB0, 6))
        self._seq((0xFF, 1, 0x4F, 0x00, 0x4E, 0x2C, 0xFF, 0, 0xB6, 0xB4))
        first, on = (12 if aperture else 0), 0
        for i in range(48):
            if i < first or on == count:
                spads[i // 8] &= ~(1 << (i % 8)) & 0xFF
            elif (spads[i // 8] >> (i % 8)) & 1:
                on += 1
        self.ctx.i2c.writeto_mem(self.a, 0xB0, spads)
        self._seq(TUNING)
        w(0x0A, 0x04)                                         # interrupt on new sample
        w(0x84, r(0x84) & ~0x10 & 0xFF)
        w(0x0B, 0x01)
        w(0x01, 0x01)
        self._ref_cal(0x40)                                   # VHV calibration
        w(0x01, 0x02)
        self._ref_cal(0x00)                                   # phase calibration
        w(0x01, 0xE8)
        self._seq((0x80, 1, 0xFF, 1, 0x00, 0, 0x91, self.stop, 0x00, 1, 0xFF, 0, 0x80, 0))
        w(0x00, 0x02)                                         # continuous back-to-back
        self.last = None

    def w(self, reg, v):
        """Write one 8-bit register."""
        self.ctx.i2c.writeto_mem(self.a, reg, bytes((v,)))

    def r(self, reg):
        """Read one 8-bit register."""
        return self.ctx.i2c.readfrom_mem(self.a, reg, 1)[0]

    def _seq(self, pairs):
        """Write a flat (register, value, register, value, ...) sequence."""
        for i in range(0, len(pairs), 2):
            self.w(pairs[i], pairs[i + 1])

    def _wait(self, fn, ms=200):
        """Poll fn() until true; OSError after ms milliseconds."""
        t = time.ticks_ms()
        while not fn():
            if time.ticks_diff(time.ticks_ms(), t) > ms:
                raise OSError('VL53L0X timeout')
            time.sleep_ms(2)

    def _spad_info(self):
        """Read the SPAD count / type from the sensor NVM (ST reference init)."""
        w, r = self.w, self.r
        self._seq((0x80, 1, 0xFF, 1, 0x00, 0, 0xFF, 6))
        w(0x83, r(0x83) | 0x04)
        self._seq((0xFF, 7, 0x81, 1, 0x80, 1, 0x94, 0x6B, 0x83, 0))
        self._wait(lambda: r(0x83) != 0)
        w(0x83, 0x01)
        t = r(0x92)
        self._seq((0x81, 0, 0xFF, 6))
        w(0x83, r(0x83) & ~0x04 & 0xFF)
        self._seq((0xFF, 1, 0x00, 1, 0xFF, 0, 0x80, 0))
        return t & 0x7F, (t >> 7) & 1

    def _ref_cal(self, vhv):
        """One reference calibration step (VHV or phase), as in the ST API."""
        self.w(0x00, 0x01 | vhv)
        self._wait(lambda: self.r(0x13) & 0x07)
        self.w(0x0B, 0x01)
        self.w(0x00, 0x00)

    def chans(self):
        """Channels this sensor reports: [(key, label, unit, decimals)]."""
        return [('mm', self.e['name'], 'mm', 0)]

    def read(self):
        """One value per channel, None where there is no valid reading this time."""
        if self.r(0x13) & 0x07:                               # new result ready
            b = self.ctx.i2c.readfrom_mem(self.a, 0x14 + 10, 2)
            d = (b[0] << 8) | b[1]
            self.w(0x0B, 0x01)
            self.last = None if d >= 8190 or d == 20 else d   # 8190/8191 = out of range
        return [self.last]

    def info(self):
        """Short status text for the dashboard card (pin / address, raw value or error)."""
        return 'I2C 0x%02X' % self.a
