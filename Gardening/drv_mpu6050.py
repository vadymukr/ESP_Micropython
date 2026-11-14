# MPU-6050 accelerometer / gyroscope (GY-521), I2C 0x68 (0x69 with AD0 high): tilt and motion,
# e.g. for a tank float arm, a door or a gate. Ranges +-2 g, +-250 deg/s.
# Spec: docs/SPEC.md#driver-interface
import math


class Driver:
    """Registry driver: MPU-6050 accelerometer / gyroscope (GY-521), I2C 0x68 (0x69 with AD0 high). Interface:
    docs/SPEC.md#driver-interface."""
    def __init__(self, e, ctx):
        """Set up from the registry entry e (pins / address / settings); ctx = app.sens (shared I2C bus, free
        UARTs, air readings)."""
        self.e, self.ctx = e, ctx
        self.a = int(e.get('addr', '68'), 16)
        ctx.i2c.readfrom_mem(self.a, 0x75, 1)                # WHO_AM_I (raises if absent)
        ctx.i2c.writeto_mem(self.a, 0x6B, b'\x00')           # wake up

    def chans(self):
        """Channels this sensor reports: [(key, label, unit, decimals)]."""
        n = self.e['name']
        return [('pitch', n + ' pitch', '&deg;', 1), ('roll', n + ' roll', '&deg;', 1),
                ('rot', n + ' rotation', '&deg;/s', 1), ('t', n + ' chip temp', '&deg;C', 1)]

    def read(self):
        """One value per channel, None where there is no valid reading this time."""
        b = self.ctx.i2c.readfrom_mem(self.a, 0x3B, 14)
        v = [(b[i] << 8) | b[i + 1] for i in range(0, 14, 2)]
        v = [x - 65536 if x > 32767 else x for x in v]
        ax, ay, az = v[0] / 16384, v[1] / 16384, v[2] / 16384
        gx, gy, gz = v[4] / 131, v[5] / 131, v[6] / 131
        pitch = math.atan2(ax, math.sqrt(ay * ay + az * az)) * 57.2958
        roll = math.atan2(ay, az) * 57.2958
        return [pitch, roll, math.sqrt(gx * gx + gy * gy + gz * gz), v[3] / 340 + 36.53]

    def info(self):
        """Short status text for the dashboard card (pin / address, raw value or error)."""
        return 'I2C 0x%02X' % self.a
