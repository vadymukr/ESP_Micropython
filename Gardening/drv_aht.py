# AHT10 / AHT20 / AHT21 temperature + humidity (I2C 0x38). Non-blocking: measurement triggered on
# one read, result fetched on the next.
import time


class Driver:
    def __init__(self, e, ctx):
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
        n = self.e['name']
        return [('t', n + ' temp', '&deg;C', 1), ('rh', n + ' humidity', '%', 0)]

    def read(self):
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
        return 'I2C 0x%02X' % self.a
