# Laser PM sensors on UART 9600 baud: Plantower PMS5003 / PMS7003 / PMS9003 and similar (32-byte
# frames 0x42 0x4D) or Nova SDS011 (10-byte frames 0xAA 0xC0), detected from the data.
# Sensor TX -> RX pin, sensor RX -> TX pin, 5 V supply (fan), 3.3 V logic. Values in ug/m3.
# Spec: docs/SPEC.md#driver-interface
from machine import UART


class Driver:
    """Registry driver: Laser PM sensors on UART 9600 baud. Interface: docs/SPEC.md#driver-interface."""
    def __init__(self, e, ctx):
        """Set up from the registry entry e (pins / address / settings); ctx = app.sens (shared I2C bus, free
        UARTs, air readings)."""
        self.e = e
        self.u = UART(ctx.uart_free.pop(0), baudrate=9600, rx=e['pin'], tx=e['pin2'], timeout=0, rxbuf=256)
        self.last = [None, None, None]
        self.kind = '?'

    def chans(self):
        """Channels this sensor reports: [(key, label, unit, decimals)]."""
        n = self.e['name']
        return [('pm1', n + ' PM1.0', '&micro;g/m&sup3;', 0), ('pm25', n + ' PM2.5', '&micro;g/m&sup3;', 0),
                ('pm10', n + ' PM10', '&micro;g/m&sup3;', 0)]

    def read(self):
        """One value per channel, None where there is no valid reading this time."""
        b = self.u.read()
        if b:
            i = b.rfind(b'\x42\x4D')
            while i >= 0:                                     # newest complete Plantower frame
                f = b[i:i + 32]
                if len(f) == 32 and (f[30] << 8 | f[31]) == sum(f[:30]) & 0xFFFF:
                    self.last = [f[10] << 8 | f[11], f[12] << 8 | f[13], f[14] << 8 | f[15]]
                    self.kind = 'Plantower'
                    break
                i = b.rfind(b'\x42\x4D', 0, i)
            j = b.rfind(b'\xAA\xC0')
            if j >= 0 and len(b) >= j + 10:
                f = b[j:j + 10]
                if f[9] == 0xAB and sum(f[2:8]) & 0xFF == f[8]:
                    self.last = [None, (f[3] << 8 | f[2]) / 10, (f[5] << 8 | f[4]) / 10]
                    self.kind = 'SDS011'
        return list(self.last)

    def info(self):
        """Short status text for the dashboard card (pin / address, raw value or error)."""
        return '%s &middot; UART RX GPIO%d / TX GPIO%d' % (self.kind, self.e['pin'], self.e['pin2'])
