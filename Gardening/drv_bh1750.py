# BH1750 ambient light, lux (I2C 0x23, or 0x5C with ADDR high). Continuous high-resolution mode.
# Spec: docs/SPEC.md#driver-interface
class Driver:
    """Registry driver: BH1750 ambient light, lux (I2C 0x23, or 0x5C with ADDR high). Continuous high-re.
    Interface: docs/SPEC.md#driver-interface."""
    def __init__(self, e, ctx):
        """Set up from the registry entry e (pins / address / settings); ctx = app.sens (shared I2C bus, free
        UARTs, air readings)."""
        self.e, self.ctx = e, ctx
        self.a = int(e.get('addr', '23'), 16)
        ctx.i2c.writeto(self.a, b'\x01')                   # power on
        ctx.i2c.writeto(self.a, b'\x10')                   # continuous H-resolution (1 lx, 120 ms)

    def chans(self):
        """Channels this sensor reports: [(key, label, unit, decimals)]."""
        return [('lux', self.e['name'], 'lx', 0)]

    def read(self):
        """One value per channel, None where there is no valid reading this time."""
        b = self.ctx.i2c.readfrom(self.a, 2)
        return [((b[0] << 8) | b[1]) / 1.2]

    def info(self):
        """Short status text for the dashboard card (pin / address, raw value or error)."""
        return 'I2C 0x%02X' % self.a
