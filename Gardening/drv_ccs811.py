# CCS811 eCO2 / TVOC air-quality sensor, I2C 0x5A (0x5B with ADDR high). Tie nWAKE to GND.
# Values are estimates from VOCs (not a true CO2 measurement): 48 h burn-in, ~20 min warm-up.
# The air sensor's temperature / humidity are written to the CCS811 for compensation.
# Spec: docs/SPEC.md#driver-interface
class Driver:
    """Registry driver: CCS811 eCO2 / TVOC air-quality sensor, I2C 0x5A (0x5B with ADDR high). Tie nWAKE.
    Interface: docs/SPEC.md#driver-interface."""
    def __init__(self, e, ctx):
        """Set up from the registry entry e (pins / address / settings); ctx = app.sens (shared I2C bus, free
        UARTs, air readings)."""
        self.e, self.ctx = e, ctx
        self.a = int(e.get('addr', '5a'), 16)
        i2c = ctx.i2c
        if i2c.readfrom_mem(self.a, 0x20, 1)[0] != 0x81:      # HW_ID
            raise OSError('not a CCS811')
        i2c.writeto(self.a, b'\xF4')                          # APP_START
        i2c.writeto_mem(self.a, 0x01, b'\x10')                # MEAS_MODE: every 1 s
        self.last = [None, None]

    def chans(self):
        """Channels this sensor reports: [(key, label, unit, decimals)]."""
        n = self.e['name']
        return [('eco2', n + ' eCO2', 'ppm', 0), ('tvoc', n + ' TVOC', 'ppb', 0)]

    def read(self):
        """One value per channel, None where there is no valid reading this time."""
        i2c, c = self.ctx.i2c, self.ctx
        if c.t is not None and c.rh is not None:              # compensation from the air sensor
            h, t = int(c.rh * 512), int((c.t + 25) * 512)
            i2c.writeto_mem(self.a, 0x05, bytes((h >> 8, h & 0xFF, t >> 8, t & 0xFF)))
        d = i2c.readfrom_mem(self.a, 0x02, 6)
        if d[4] & 0x01:
            raise OSError('CCS811 error 0x%02X' % i2c.readfrom_mem(self.a, 0xE0, 1)[0])
        if d[4] & 0x08:                                       # DATA_READY
            self.last = [(d[0] << 8) | d[1], (d[2] << 8) | d[3]]
        return list(self.last)

    def info(self):
        """Short status text for the dashboard card (pin / address, raw value or error)."""
        return 'I2C 0x%02X' % self.a
