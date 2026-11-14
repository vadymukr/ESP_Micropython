# Sensirion SHT30 / SHT31 / SHT35 temperature + humidity (I2C 0x44, or 0x45 with ADDR high).
# Non-blocking single shot: started on one read, fetched on the next.
# Spec: docs/SPEC.md#driver-interface
class Driver:
    """Registry driver: Sensirion SHT30 / SHT31 / SHT35 temperature + humidity (I2C 0x44, or 0x45 with A.
    Interface: docs/SPEC.md#driver-interface."""
    def __init__(self, e, ctx):
        """Set up from the registry entry e (pins / address / settings); ctx = app.sens (shared I2C bus, free
        UARTs, air readings)."""
        self.e, self.ctx = e, ctx
        self.a = int(e.get('addr', '44'), 16)
        ctx.i2c.writeto(self.a, b'\x30\xA2')               # soft reset (raises if absent)
        self.last = [None, None]
        self.busy = False

    def chans(self):
        """Channels this sensor reports: [(key, label, unit, decimals)]."""
        n = self.e['name']
        return [('t', n + ' temp', '&deg;C', 1), ('rh', n + ' humidity', '%', 0)]

    def read(self):
        """One value per channel, None where there is no valid reading this time."""
        i2c = self.ctx.i2c
        if self.busy:
            b = i2c.readfrom(self.a, 6)
            self.last = [-45 + 175 * ((b[0] << 8) | b[1]) / 65535, 100 * ((b[3] << 8) | b[4]) / 65535]
        i2c.writeto(self.a, b'\x24\x00')                   # high repeatability, no clock stretching
        self.busy = True
        return list(self.last)

    def info(self):
        """Short status text for the dashboard card (pin / address, raw value or error)."""
        return 'I2C 0x%02X' % self.a
