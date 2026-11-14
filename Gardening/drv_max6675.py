# MAX6675 + K-type thermocouple (0..1024 degC, 0.25 degC steps). Read-only SPI on three free GPIOs,
# bit-banged (any pins): pin = SO (data), pin2 = SCK, pin3 = CS. A conversion takes ~220 ms.
# Calibration: reading = MAX6675 value * gain + offset; the calibrate button sets the offset so the
# reading equals the reference typed in 'ref' (blank = the air sensor right now). Power 3.3-5 V.
# Spec: docs/SPEC.md#driver-interface
import time
from machine import Pin
from drv_ntc import cal_offset


class Driver:
    """Registry driver: MAX6675 + K-type thermocouple (0..1024 degC, 0.25 degC steps). Read-only SPI on .
    Interface: docs/SPEC.md#driver-interface."""
    def __init__(self, e, ctx=None):
        """Set up from the registry entry e (pins / address / settings); ctx = app.sens (shared I2C bus, free
        UARTs, air readings)."""
        self.e, self.ctx = e, ctx
        self.so = Pin(e['pin'], Pin.IN)
        self.sck = Pin(e['pin2'], Pin.OUT, value=0)
        self.cs = Pin(e['pin3'], Pin.OUT, value=1)
        self.raw_t = None
        self.err = ''
        self.t0 = time.ticks_ms()

    def chans(self):
        """Channels this sensor reports: [(key, label, unit, decimals)]."""
        return [('t', self.e['name'], '&deg;C', int(self.e.get('dec', 1)))]

    def _word(self):
        """Clock in the 16-bit conversion result (CS low, 16 x SCK, CS high starts the next)."""
        self.cs.value(0)
        time.sleep_us(2)
        v = 0
        for _ in range(16):
            self.sck.value(1)
            time.sleep_us(1)
            v = (v << 1) | self.so.value()
            self.sck.value(0)
            time.sleep_us(1)
        self.cs.value(1)                              # starts the next conversion
        return v

    def read(self):
        """One value per channel, None where there is no valid reading this time."""
        if time.ticks_diff(time.ticks_ms(), self.t0) < 250:     # still converting
            return [None if self.raw_t is None else self.raw_t * self.e.get('gain', 1.0) + self.e.get('offset', 0.0)]
        self.t0 = time.ticks_ms()
        v = self._word()
        if v & 0x8000 or v == 0:                      # bit 15 is always 0; all-zero = SO not connected
            self.raw_t, self.err = None, 'no answer (wiring)'
        elif v & 0x4:
            self.raw_t, self.err = None, 'thermocouple open'
        else:
            self.raw_t, self.err = (v >> 3) * 0.25, ''
        if self.raw_t is None:
            return [None]
        return [self.raw_t * self.e.get('gain', 1.0) + self.e.get('offset', 0.0)]

    def cal(self):
        """Calibrate button of the Sensors panel: returns (key, value) to store in the entry."""
        return cal_offset(self.e, self.raw_t, self.ctx)

    def info(self):
        """Short status text for the dashboard card (pin / address, raw value or error)."""
        e = self.e
        return 'SO %d, SCK %d, CS %d &middot; %s' % (e['pin'], e['pin2'], e['pin3'],
                                                   self.err or ('%.2f &deg;C raw' % self.raw_t if self.raw_t is not None else '-'))
