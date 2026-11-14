# Minimal MicroPython driver for the AHT10 temperature / humidity sensor (I2C, addr 0x38)
import time

AHT10_ADDR = 0x38
_CMD_INIT = b'\xE1\x08\x00'      # calibrate / init
_CMD_MEASURE = b'\xAC\x33\x00'   # trigger measurement
_CMD_RESET = b'\xBA'             # soft reset


class AHT10:
    def __init__(self, i2c, addr=AHT10_ADDR):
        self.i2c = i2c
        self.addr = addr
        self.i2c.writeto(self.addr, _CMD_RESET)
        time.sleep_ms(20)
        self.i2c.writeto(self.addr, _CMD_INIT)
        time.sleep_ms(10)

    # Non-blocking use (asyncio): trigger(), wait >= 80 ms, then result()
    def trigger(self):
        self.i2c.writeto(self.addr, _CMD_MEASURE)

    def result(self):
        """Return (temperature_C, relative_humidity_pct); OSError if still busy."""
        b = self.i2c.readfrom(self.addr, 6)
        if b[0] & 0x80:
            raise OSError("AHT10 busy")
        raw_h = (b[1] << 12) | (b[2] << 4) | (b[3] >> 4)
        raw_t = ((b[3] & 0x0F) << 16) | (b[4] << 8) | b[5]
        return raw_t * 200 / 1048576 - 50, raw_h * 100 / 1048576

    def read(self):
        """Blocking read: return (temperature_C, relative_humidity_pct)."""
        self.trigger()
        time.sleep_ms(80)
        for _ in range(10):
            try:
                return self.result()
            except OSError:
                time.sleep_ms(10)
        raise OSError("AHT10 busy")
