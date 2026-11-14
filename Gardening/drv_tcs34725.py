# TCS34725 RGB colour sensor, I2C 0x29 (same address as VL53L0X/VL53L1X: only one of them per bus).
# R, G, B as % of the clear channel, plus the clear (brightness) count.
# Spec: docs/SPEC.md#driver-interface
class Driver:
    """Registry driver: TCS34725 RGB colour sensor, I2C 0x29 (same address as VL53L0X/VL53L1X. Interface:
    docs/SPEC.md#driver-interface."""
    def __init__(self, e, ctx):
        """Set up from the registry entry e (pins / address / settings); ctx = app.sens (shared I2C bus, free
        UARTs, air readings)."""
        self.e, self.ctx = e, ctx
        self.a = int(e.get('addr', '29'), 16)
        i2c = ctx.i2c
        if i2c.readfrom_mem(self.a, 0x92 | 0x80, 1)[0] not in (0x44, 0x4D, 0x10):
            raise OSError('not a TCS3472x')
        i2c.writeto_mem(self.a, 0x81 | 0x80, b'\xD5')        # integration 101 ms
        i2c.writeto_mem(self.a, 0x8F | 0x80, b'\x01')        # gain 4x
        i2c.writeto_mem(self.a, 0x80 | 0x80, b'\x03')        # power on + RGBC enable

    def chans(self):
        """Channels this sensor reports: [(key, label, unit, decimals)]."""
        n = self.e['name']
        return [('r', n + ' red', '%', 0), ('g', n + ' green', '%', 0), ('b', n + ' blue', '%', 0),
                ('c', n + ' clear', '', 0)]

    def read(self):
        """One value per channel, None where there is no valid reading this time."""
        d = self.ctx.i2c.readfrom_mem(self.a, 0x94 | 0xA0, 8)   # auto-increment: C, R, G, B
        c, r, g, b = [(d[i + 1] << 8) | d[i] for i in range(0, 8, 2)]
        if not c:
            return [0, 0, 0, 0]
        return [r * 100 / c, g * 100 / c, b * 100 / c, c]

    def info(self):
        """Short status text for the dashboard card (pin / address, raw value or error)."""
        return 'I2C 0x%02X' % self.a
