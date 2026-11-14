# Sensirion SHT30 / SHT31 / SHT35 temperature + humidity (I2C 0x44, or 0x45 with ADDR high).
# Non-blocking single shot: started on one read, fetched on the next.
class Driver:
    def __init__(self, e, ctx):
        self.e, self.ctx = e, ctx
        self.a = int(e.get('addr', '44'), 16)
        ctx.i2c.writeto(self.a, b'\x30\xA2')               # soft reset (raises if absent)
        self.last = [None, None]
        self.busy = False

    def chans(self):
        n = self.e['name']
        return [('t', n + ' temp', '&deg;C', 1), ('rh', n + ' humidity', '%', 0)]

    def read(self):
        i2c = self.ctx.i2c
        if self.busy:
            b = i2c.readfrom(self.a, 6)
            self.last = [-45 + 175 * ((b[0] << 8) | b[1]) / 65535, 100 * ((b[3] << 8) | b[4]) / 65535]
        i2c.writeto(self.a, b'\x24\x00')                   # high repeatability, no clock stretching
        self.busy = True
        return list(self.last)

    def info(self):
        return 'I2C 0x%02X' % self.a
