# BH1750 ambient light, lux (I2C 0x23, or 0x5C with ADDR high). Continuous high-resolution mode.
class Driver:
    def __init__(self, e, ctx):
        self.e, self.ctx = e, ctx
        self.a = int(e.get('addr', '23'), 16)
        ctx.i2c.writeto(self.a, b'\x01')                   # power on
        ctx.i2c.writeto(self.a, b'\x10')                   # continuous H-resolution (1 lx, 120 ms)

    def chans(self):
        return [('lux', self.e['name'], 'lx', 0)]

    def read(self):
        b = self.ctx.i2c.readfrom(self.a, 2)
        return [((b[0] << 8) | b[1]) / 1.2]

    def info(self):
        return 'I2C 0x%02X' % self.a
