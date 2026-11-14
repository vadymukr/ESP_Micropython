# VL53L1X time-of-flight distance sensor, I2C 0x29 (16-bit registers), up to ~4 m.
# Init = ST's ultra-lite driver (VL53L1X_ULD): default configuration block 0x2D..0x87, one
# start/stop cycle, then continuous ranging; a read fetches the newest distance when ready.
# 0x29 is also the address of VL53L0X and TCS34725: only one of them per bus.
# Spec: docs/SPEC.md#driver-interface
import time

CONFIG = bytes((                                              # registers 0x2D .. 0x87 (91 bytes)
    0x00, 0x00, 0x00, 0x01, 0x02, 0x00, 0x02, 0x08, 0x00, 0x08, 0x10, 0x01, 0x01, 0x00, 0x00, 0x00,
    0x00, 0xFF, 0x00, 0x0F, 0x00, 0x00, 0x00, 0x00, 0x00, 0x20, 0x0B, 0x00, 0x00, 0x02, 0x0A, 0x21,
    0x00, 0x00, 0x05, 0x00, 0x00, 0x00, 0x00, 0xC8, 0x00, 0x00, 0x38, 0xFF, 0x01, 0x00, 0x08, 0x00,
    0x00, 0x01, 0xCC, 0x0F, 0x01, 0xF1, 0x0D, 0x01, 0x68, 0x00, 0x80, 0x08, 0xB8, 0x00, 0x00, 0x00,
    0x00, 0x0F, 0x89, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x0F, 0x0D, 0x0E, 0x0E, 0x00,
    0x00, 0x02, 0xC7, 0xFF, 0x9B, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00))


class Driver:
    """Registry driver: VL53L1X time-of-flight distance sensor, I2C 0x29 (16-bit registers), up to ~4 m.
    Interface: docs/SPEC.md#driver-interface."""
    def __init__(self, e, ctx):
        """Set up from the registry entry e (pins / address / settings); ctx = app.sens (shared I2C bus, free
        UARTs, air readings)."""
        self.e, self.ctx = e, ctx
        self.a = int(e.get('addr', '29'), 16)
        if self.rd(0x010F, 2) != b'\xEA\xCC':
            raise OSError('not a VL53L1X')
        t = time.ticks_ms()
        while not self.rd(0x00E5, 1)[0] & 0x01:                 # firmware booted
            if time.ticks_diff(time.ticks_ms(), t) > 500:
                raise OSError('VL53L1X not booted')
            time.sleep_ms(5)
        self.ctx.i2c.writeto_mem(self.a, 0x002D, CONFIG, addrsize=16)
        self.pol = 0 if self.rd(0x0030, 1)[0] & 0x10 else 1   # data-ready level
        self.wr(0x0087, 0x40)                                 # start ranging
        t = time.ticks_ms()
        while not self._ready():
            if time.ticks_diff(time.ticks_ms(), t) > 500:
                raise OSError('VL53L1X timeout')
            time.sleep_ms(5)
        self.wr(0x0086, 0x01)                                 # clear interrupt
        self.wr(0x0087, 0x00)                                 # stop
        self.wr(0x0008, 0x09)                                 # VHV timeout loop bound
        self.wr(0x000B, 0x00)
        self.wr(0x0087, 0x40)                                 # continuous ranging
        self.last = None

    def rd(self, reg, n):
        """Read n bytes from a 16-bit register address."""
        return self.ctx.i2c.readfrom_mem(self.a, reg, n, addrsize=16)

    def wr(self, reg, v):
        """Write one byte to a 16-bit register address."""
        self.ctx.i2c.writeto_mem(self.a, reg, bytes((v,)), addrsize=16)

    def _ready(self):
        """True when a new measurement is ready (interrupt status vs polarity)."""
        return (self.rd(0x0031, 1)[0] & 0x01) == self.pol

    def chans(self):
        """Channels this sensor reports: [(key, label, unit, decimals)]."""
        return [('mm', self.e['name'], 'mm', 0)]

    def read(self):
        """One value per channel, None where there is no valid reading this time."""
        if self._ready():
            st = self.rd(0x0089, 1)[0] & 0x1F                  # range status (9 = valid)
            b = self.rd(0x0096, 2)
            self.wr(0x0086, 0x01)
            self.last = (b[0] << 8) | b[1] if st == 9 else None
        return [self.last]

    def info(self):
        """Short status text for the dashboard card (pin / address, raw value or error)."""
        return 'I2C 0x%02X' % self.a
