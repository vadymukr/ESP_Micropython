# Air sensor auto-detect for the garden logger (MicroPython).
# Uses whatever is on the I2C bus:
#   AHT10            0x38       temperature + humidity   (driver in aht10.py)
#   Si70xx / HTU21D  0x40       temperature + humidity   (e.g. GY-21P board: Si7020 + BMP280)
#   BMP280           0x76/0x77  temperature + pressure
# Temperature comes from the humidity sensor when there is one (BMP280 runs warmer), else from the BMP280.
#
# Non-blocking use (asyncio): trigger(), wait >= 80 ms, then result() -> (temp_C, rh_pct|None, hpa|None).
# result() raises OSError if a sensor is still busy or has gone away.
# Spec: docs/SPEC.md#sensors
import time
import struct


class Si70xx:
    """Si7013/7020/7021 and HTU21D (same commands)."""
    MODELS = {0x0D: 'Si7013', 0x14: 'Si7020', 0x15: 'Si7021', 0x32: 'HTU21D'}

    def __init__(self, i2c, addr=0x40):
        """Si7020 / Si7021 / HTU21 at I2C 0x40 (no-hold measurements)."""
        self.i2c, self.addr = i2c, addr
        i2c.writeto(addr, b'\xFE')               # soft reset
        time.sleep_ms(20)
        try:
            i2c.writeto(addr, b'\xFC\xC9')       # electronic ID, 2nd part: first byte = model
            self.name = self.MODELS.get(i2c.readfrom(addr, 6)[0], 'Si70xx')
        except OSError:
            self.name = 'HTU21D'                 # HTU21D has no electronic-ID command

    def trigger(self):
        """Start the humidity measurement (temperature is read from it afterwards)."""
        self.i2c.writeto(self.addr, b'\xF5')     # measure RH, no hold master (~12-16 ms)

    def result(self):
        """(temperature degC, relative humidity %) of the last trigger()."""
        b = self.i2c.readfrom(self.addr, 3)      # NACK -> OSError while still converting
        rh = ((b[0] << 8 | b[1]) & 0xFFFC) * 125 / 65536 - 6
        try:
            self.i2c.writeto(self.addr, b'\xE0')  # temperature of that RH measurement (Si70xx)
            b = self.i2c.readfrom(self.addr, 2)
        except OSError:                          # HTU21D: separate temperature measurement
            self.i2c.writeto(self.addr, b'\xF3')
            time.sleep_ms(55)
            b = self.i2c.readfrom(self.addr, 3)
        t = ((b[0] << 8 | b[1]) & 0xFFFC) * 175.72 / 65536 - 46.85
        return t, (0.0 if rh < 0 else 100.0 if rh > 100 else rh)


class BMP280:
    """Bosch BMP280 / BME280 pressure + temperature (humidity of a BME280 is not used here)."""
    def __init__(self, i2c, addr=0x76):
        """Read the calibration and start continuous measurement at addr (0x76 / 0x77)."""
        self.i2c, self.addr = i2c, addr
        if i2c.readfrom_mem(addr, 0xD0, 1)[0] != 0x58:
            raise OSError('not a BMP280')
        self.name = 'BMP280'
        i2c.writeto_mem(addr, 0xE0, b'\xB6')     # soft reset
        time.sleep_ms(5)
        self.c = struct.unpack('<HhhHhhhhhhhh', i2c.readfrom_mem(addr, 0x88, 24))
        i2c.writeto_mem(addr, 0xF5, b'\x00')     # no filter

    def trigger(self):
        # forced mode, temperature x2, pressure x16 oversampling (~43 ms)
        """Nothing to start (continuous mode)."""
        self.i2c.writeto_mem(self.addr, 0xF4, b'\x55')

    def result(self):
        """(temperature degC, pressure hPa), compensated with the chip calibration."""
        i2c, a = self.i2c, self.addr
        if i2c.readfrom_mem(a, 0xF3, 1)[0] & 0x08:
            raise OSError('BMP280 busy')
        d = i2c.readfrom_mem(a, 0xF7, 6)
        adc_p = (d[0] << 12) | (d[1] << 4) | (d[2] >> 4)
        adc_t = (d[3] << 12) | (d[4] << 4) | (d[5] >> 4)
        T1, T2, T3, P1, P2, P3, P4, P5, P6, P7, P8, P9 = self.c
        v1 = (adc_t / 16384 - T1 / 1024) * T2
        v2 = (adc_t / 131072 - T1 / 8192) ** 2 * T3
        tf = v1 + v2
        t = tf / 5120
        v1 = tf / 2 - 64000
        v2 = v1 * v1 * P6 / 32768 + v1 * P5 * 2
        v2 = v2 / 4 + P4 * 65536
        v1 = (P3 * v1 * v1 / 524288 + P2 * v1) / 524288
        v1 = (1 + v1 / 32768) * P1
        if not v1:
            raise OSError('BMP280 bad data')
        p = (1048576 - adc_p - v2 / 4096) * 6250 / v1
        p += (P9 * p * p / 2147483648 + p * P8 / 32768 + P7) / 16
        return t, p / 100                        # C, hPa


class Climate:
    """The detected air sensor: a humidity chip and / or a barometer, read together."""
    def __init__(self, hum, baro):
        """hum / baro = sensor objects (either may be None); name for the dashboard."""
        self.hum, self.baro = hum, baro
        self.name = ' + '.join(s.name for s in (hum, baro) if s)

    def trigger(self):
        """Start the measurements of both chips."""
        for s in (self.hum, self.baro):
            if s:
                s.trigger()

    def result(self):
        """(temperature, humidity, pressure); humidity chip temperature preferred."""
        t = rh = p = None
        if self.baro:
            t, p = self.baro.result()
        if self.hum:
            t, rh = self.hum.result()
        return t, rh, p

    def read(self):
        """Blocking read for scripts."""
        self.trigger()
        time.sleep_ms(85)
        for _ in range(10):
            try:
                return self.result()
            except OSError:
                time.sleep_ms(10)
        raise OSError('sensor busy')


ADDRS = (0x38, 0x40, 0x76, 0x77)


def probe(i2c, addrs=ADDRS):
    """Addresses that ACK. Only our 4 addresses, not a 112-address scan(): with a loose wire every
    probe can wait for the bus timeout, so a full scan would freeze the caller for seconds."""
    found = []
    for a in addrs:
        try:
            i2c.writeto(a, b'')
            found.append(a)
        except OSError:
            pass
    return found


def detect(i2c):
    """Return a Climate for the sensors found on the bus; OSError if none."""
    found = probe(i2c)
    hum = baro = None
    if 0x40 in found:
        hum = Si70xx(i2c)
    elif 0x38 in found:
        from aht10 import AHT10
        hum = AHT10(i2c)
        hum.name = 'AHT10'
    for a in (0x76, 0x77):
        if a in found:
            try:
                baro = BMP280(i2c, a)
                break
            except OSError:
                pass
    if not (hum or baro):
        raise OSError('no air sensor at 0x38/0x40/0x76/0x77')
    return Climate(hum, baro)
