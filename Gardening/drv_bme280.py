# Bosch BME280 (temperature, humidity, pressure) or BMP280 (no humidity), I2C 0x76 / 0x77.
# Normal mode (sensor measures continuously, 1 s standby), so a read is just a register read.
import struct


class Driver:
    def __init__(self, e, ctx):
        self.e, self.ctx = e, ctx
        self.a = int(e.get('addr', '76'), 16)
        i2c = ctx.i2c
        cid = i2c.readfrom_mem(self.a, 0xD0, 1)[0]
        if cid not in (0x58, 0x60):
            raise OSError('chip id 0x%02X is not a BMP280/BME280' % cid)
        self.hum = cid == 0x60
        self.c = struct.unpack('<HhhHhhhhhhhh', i2c.readfrom_mem(self.a, 0x88, 24))
        if self.hum:
            h1 = i2c.readfrom_mem(self.a, 0xA1, 1)[0]
            b = i2c.readfrom_mem(self.a, 0xE1, 7)
            h2 = struct.unpack('<h', b[0:2])[0]
            h4 = (b[3] << 4) | (b[4] & 0x0F)
            h5 = (b[5] << 4) | (b[4] >> 4)
            h6 = b[6] - 256 if b[6] > 127 else b[6]
            self.h = (h1, h2, b[2], h4 - 4096 if h4 > 2047 else h4, h5 - 4096 if h5 > 2047 else h5, h6)
            i2c.writeto_mem(self.a, 0xF2, b'\x01')         # humidity x1 (before ctrl_meas)
        i2c.writeto_mem(self.a, 0xF5, b'\xA8')             # standby 1000 ms, IIR filter x4
        i2c.writeto_mem(self.a, 0xF4, b'\x57')             # temp x2, pressure x16, normal mode

    def chans(self):
        n = self.e['name']
        ch = [('t', n + ' temp', '&deg;C', 1)]
        if self.hum:
            ch.append(('rh', n + ' humidity', '%', 0))
        ch.append(('hpa', n + ' pressure', 'hPa', 1))
        return ch

    def read(self):
        d = self.ctx.i2c.readfrom_mem(self.a, 0xF7, 8)
        adc_p = (d[0] << 12) | (d[1] << 4) | (d[2] >> 4)
        adc_t = (d[3] << 12) | (d[4] << 4) | (d[5] >> 4)
        T1, T2, T3, P1, P2, P3, P4, P5, P6, P7, P8, P9 = self.c
        tf = (adc_t / 16384 - T1 / 1024) * T2 + (adc_t / 131072 - T1 / 8192) ** 2 * T3
        t = tf / 5120
        v1 = tf / 2 - 64000
        v2 = v1 * v1 * P6 / 32768 + v1 * P5 * 2
        v2 = v2 / 4 + P4 * 65536
        v1 = (1 + (P3 * v1 * v1 / 524288 + P2 * v1) / 524288 / 32768) * P1
        p = None
        if v1:
            p = (1048576 - adc_p - v2 / 4096) * 6250 / v1
            p = (p + (P9 * p * p / 2147483648 + p * P8 / 32768 + P7) / 16) / 100
        out = [t]
        if self.hum:
            H1, H2, H3, H4, H5, H6 = self.h
            adc_h = (d[6] << 8) | d[7]
            h = tf - 76800
            h = (adc_h - (H4 * 64 + H5 / 16384 * h)) * (H2 / 65536 * (1 + H6 / 67108864 * h * (1 + H3 / 67108864 * h)))
            h = h * (1 - H1 * h / 524288)
            out.append(0.0 if h < 0 else 100.0 if h > 100 else h)
        out.append(p)
        return out

    def info(self):
        return '%s &middot; I2C 0x%02X' % ('BME280' if self.hum else 'BMP280', self.a)
