# ADS1115 16-bit 4-channel ADC (I2C 0x48..0x4B): A0..A3 single-ended, range +-4.096 V, in mV.
# Inputs must stay between GND and the ADS1115 supply (use 3.3 V supply with the ESP32).
# Spec: docs/SPEC.md#driver-interface
import time


class Driver:
    """Registry driver: ADS1115 16-bit 4-channel ADC (I2C 0x48..0x4B). Interface: docs/SPEC.md#driver-interface."""
    def __init__(self, e, ctx):
        """Set up from the registry entry e (pins / address / settings); ctx = app.sens (shared I2C bus, free
        UARTs, air readings)."""
        self.e, self.ctx = e, ctx
        self.a = int(e.get('addr', '48'), 16)
        ctx.i2c.readfrom_mem(self.a, 0x01, 2)              # config register (raises if absent)

    def chans(self):
        """Channels this sensor reports: [(key, label, unit, decimals)]."""
        n = self.e['name']
        return [('a%d' % k, '%s A%d' % (n, k), 'mV', 1) for k in range(4)]

    def read(self):
        """One value per channel, None where there is no valid reading this time."""
        i2c, out = self.ctx.i2c, []
        for ch in range(4):
            cfg = 0x8000 | ((4 + ch) << 12) | (1 << 9) | (1 << 8) | (7 << 5) | 3   # single shot, 860 SPS
            i2c.writeto_mem(self.a, 0x01, bytes((cfg >> 8, cfg & 0xFF)))
            time.sleep_ms(2)
            b = i2c.readfrom_mem(self.a, 0x00, 2)
            raw = (b[0] << 8) | b[1]
            out.append((raw - 65536 if raw > 32767 else raw) * 0.125)
        return out

    def info(self):
        """Short status text for the dashboard card (pin / address, raw value or error)."""
        return 'I2C 0x%02X' % self.a
