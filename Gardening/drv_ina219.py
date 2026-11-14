# INA219 voltage / current / power monitor (I2C 0x40..0x4F). Default address 0x40 is the same as an
# Si70xx humidity sensor: bridge A0 for 0x41 if both are on the bus. shunt = shunt resistor in ohm
# (0.1 on the common purple boards: up to 3.2 A). Bus voltage up to 26 V.
class Driver:
    def __init__(self, e, ctx):
        self.e, self.ctx = e, ctx
        self.a = int(e.get('addr', '40'), 16)
        ctx.i2c.writeto_mem(self.a, 0x00, b'\x39\x9F')     # 32 V, +-320 mV, 12 bit, continuous

    def chans(self):
        n = self.e['name']
        return [('v', n + ' voltage', 'V', 2), ('ma', n + ' current', 'mA', 1), ('mw', n + ' power', 'mW', 0)]

    def read(self):
        i2c = self.ctx.i2c
        b = i2c.readfrom_mem(self.a, 0x02, 2)
        v = (((b[0] << 8) | b[1]) >> 3) * 0.004
        b = i2c.readfrom_mem(self.a, 0x01, 2)
        s = (b[0] << 8) | b[1]
        s = (s - 65536 if s > 32767 else s) * 0.00001                  # shunt volts
        ma = s / (float(self.e.get('shunt', 0.1)) or 0.1) * 1000
        return [v, ma, v * ma]

    def info(self):
        return 'I2C 0x%02X &middot; shunt %s &Omega;' % (self.a, self.e.get('shunt', 0.1))
