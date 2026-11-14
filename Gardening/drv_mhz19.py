# MH-Z19B / MH-Z19C NDIR CO2 sensor on UART 9600 baud: sensor TX -> RX pin, sensor RX -> TX pin,
# Vin 5 V, GND common (3.3 V logic levels are fine). Warm-up ~3 min.
from machine import UART

CMD = b'\xFF\x01\x86\x00\x00\x00\x00\x00\x79'


class Driver:
    def __init__(self, e, ctx):
        self.e = e
        self.u = UART(ctx.uart_free.pop(0), baudrate=9600, rx=e['pin'], tx=e['pin2'], timeout=0)
        self.last = None
        self.u.write(CMD)

    def chans(self):
        return [('co2', self.e['name'] + ' CO2', 'ppm', 0)]

    def read(self):
        b = self.u.read()                                     # answer to the previous request
        if b:
            i = b.rfind(b'\xFF\x86')
            f = b[i:i + 9] if i >= 0 else b''
            if len(f) == 9 and (0x100 - (sum(f[1:8]) & 0xFF)) & 0xFF == f[8]:
                self.last = f[2] * 256 + f[3]
        self.u.write(CMD)
        return [self.last]

    def info(self):
        return 'UART RX GPIO%d / TX GPIO%d' % (self.e['pin'], self.e['pin2'])
