# AHT10 / AHT20 / AHT21 temperature + humidity (I2C 0x38). Non-blocking: measurement triggered on
# one read, result fetched on the next.
# Spec: docs/SPEC.md#driver-interface
import time


class Driver:
    """Registry driver: AHT10 / AHT20 / AHT21 temperature + humidity (I2C 0x38). Non-blocking. Interface:
    docs/SPEC.md#driver-interface."""
    def __init__(self, e, ctx):
        """Set up from the registry entry e (pins / address / settings); ctx = app.sens (shared I2C bus, free
        UARTs, air readings)."""
        self.e, self.ctx = e, ctx
        self.a = int(e.get('addr', '38'), 16)
        i2c = ctx.i2c
        st = i2c.readfrom(self.a, 1)[0]
        if not st & 0x08:                                  # not calibrated yet
            try:
                i2c.writeto(self.a, b'\xBE\x08\x00')       # AHT20 init
            except OSError:
                i2c.writeto(self.a, b'\xE1\x08\x00')       # AHT10 init
            time.sleep_ms(10)
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
            if not b[0] & 0x80:                            # not busy: data valid
                h = (b[1] << 12) | (b[2] << 4) | (b[3] >> 4)
                t = ((b[3] & 0x0F) << 16) | (b[4] << 8) | b[5]
                self.last = [t * 200 / 1048576 - 50, h * 100 / 1048576]
        i2c.writeto(self.a, b'\xAC\x33\x00')
        self.busy = True
        return list(self.last)

    def info(self):
        """Short status text for the dashboard card (pin / address, raw value or error)."""
        return 'I2C 0x%02X' % self.a
