# MH-Z19B / MH-Z19C NDIR CO2 sensor on UART 9600 baud: sensor TX -> RX pin, sensor RX -> TX pin,
# Vin 5 V, GND common (3.3 V logic levels are fine). Warm-up ~3 min.
# Spec: docs/SPEC.md#driver-interface
from machine import UART

CMD = b'\xFF\x01\x86\x00\x00\x00\x00\x00\x79'


class Driver:
    """Registry driver: MH-Z19B / MH-Z19C NDIR CO2 sensor on UART 9600 baud. Interface:
    docs/SPEC.md#driver-interface."""
    def __init__(self, e, ctx):
        """Set up from the registry entry e (pins / address / settings); ctx = app.sens (shared I2C bus, free
        UARTs, air readings)."""
        self.e = e
        self.u = UART(ctx.uart_free.pop(0), baudrate=9600, rx=e['pin'], tx=e['pin2'], timeout=0)
        self.last = None
        self.u.write(CMD)

    def chans(self):
        """Channels this sensor reports: [(key, label, unit, decimals)]."""
        return [('co2', self.e['name'] + ' CO2', 'ppm', 0)]

    def read(self):
        """One value per channel, None where there is no valid reading this time."""
        b = self.u.read()                                     # answer to the previous request
        if b:
            i = b.rfind(b'\xFF\x86')
            f = b[i:i + 9] if i >= 0 else b''
            if len(f) == 9 and (0x100 - (sum(f[1:8]) & 0xFF)) & 0xFF == f[8]:
                self.last = f[2] * 256 + f[3]
        self.u.write(CMD)
        return [self.last]

    def info(self):
        """Short status text for the dashboard card (pin / address, raw value or error)."""
        return 'UART RX GPIO%d / TX GPIO%d' % (self.e['pin'], self.e['pin2'])
