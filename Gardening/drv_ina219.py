# INA219 voltage / current / power monitor (I2C 0x40..0x4F). Default address 0x40 is the same as an
# Si70xx humidity sensor: bridge A0 for 0x41 if both are on the bus. shunt = shunt resistor in ohm
# (0.1 on the common purple boards: up to 3.2 A). Bus voltage up to 26 V.
# Spec: docs/SPEC.md#driver-interface
class Driver:
    """Registry driver: INA219 voltage / current / power monitor (I2C 0x40..0x4F). Default address 0x40 .
    Interface: docs/SPEC.md#driver-interface."""
    def __init__(self, e, ctx):
        """Set up from the registry entry e (pins / address / settings); ctx = app.sens (shared I2C bus, free
        UARTs, air readings)."""
        self.e, self.ctx = e, ctx
        self.a = int(e.get('addr', '40'), 16)
        ctx.i2c.writeto_mem(self.a, 0x00, b'\x39\x9F')     # 32 V, +-320 mV, 12 bit, continuous

    def chans(self):
        """Channels this sensor reports: [(key, label, unit, decimals)]."""
        n = self.e['name']
        return [('v', n + ' voltage', 'V', 2), ('ma', n + ' current', 'mA', 1), ('mw', n + ' power', 'mW', 0)]

    def read(self):
        """One value per channel, None where there is no valid reading this time."""
        i2c = self.ctx.i2c
        b = i2c.readfrom_mem(self.a, 0x02, 2)
        v = (((b[0] << 8) | b[1]) >> 3) * 0.004
        b = i2c.readfrom_mem(self.a, 0x01, 2)
        s = (b[0] << 8) | b[1]
        s = (s - 65536 if s > 32767 else s) * 0.00001                  # shunt volts
        ma = s / (float(self.e.get('shunt', 0.1)) or 0.1) * 1000
        return [v, ma, v * ma]

    def info(self):
        """Short status text for the dashboard card (pin / address, raw value or error)."""
        return 'I2C 0x%02X &middot; shunt %s &Omega;' % (self.a, self.e.get('shunt', 0.1))
