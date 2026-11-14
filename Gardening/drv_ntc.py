# NTC thermistor driver (e.g. 10k B3950 probe): voltage divider with a series resistor on an ADC1 pin.
#   top = 1: 3V3 - thermistor - pin - series resistor - GND      top = 0: 3V3 - series resistor - pin - thermistor - GND
# Temperature from the Beta equation: 1/T = 1/T25 + ln(R/R25) / B   (T in kelvin)
# Calibration: reading = (Beta temperature) * gain + offset. The calibrate button sets the offset so
# the reading equals the reference temperature typed in 'ref' (blank = the air sensor right now).
# Pin range: keep the divider output below ~2.5 V (11 dB attenuation is accurate up to ~2.45 V).
# Spec: docs/SPEC.md#driver-interface
import math
from machine import ADC, Pin


class Driver:
    """Registry driver: NTC thermistor driver (e.g. 10k B3950 probe). Interface: docs/SPEC.md#driver-interface."""
    def __init__(self, e, ctx=None):
        """Set up from the registry entry e (pins / address / settings); ctx = app.sens (shared I2C bus, free
        UARTs, air readings)."""
        self.e, self.ctx = e, ctx
        self.adc = ADC(Pin(e['pin']))
        self.adc.atten(ADC.ATTN_11DB)
        self.mv = self.r = self.raw_t = None

    def chans(self):
        """Channels this sensor reports: [(key, label, unit, decimals)]."""
        return [('t', self.e['name'], '&deg;C', int(self.e.get('dec', 1)))]

    def read(self):
        """One value per channel, None where there is no valid reading this time."""
        e = self.e
        s = 0
        for _ in range(16):
            s += self.adc.read_uv()
        v = s / 16000
        self.mv = v
        vcc = e.get('vcc_mv', 3300)
        if v < 15 or v > vcc - 15:                       # open or shorted probe
            self.r = self.raw_t = None
            return [None]
        rs = e.get('rs', 10.0)
        self.r = rs * (vcc - v) / v if e.get('top', 0) else rs * v / (vcc - v)    # kOhm
        try:
            k = 1 / (1 / 298.15 + math.log(self.r / e.get('r25', 10.0)) / e.get('beta', 3950))
        except (ValueError, ZeroDivisionError):
            self.raw_t = None
            return [None]
        self.raw_t = k - 273.15
        return [self.raw_t * e.get('gain', 1.0) + e.get('offset', 0.0)]

    def cal(self):
        """Calibrate button of the Sensors panel: returns (key, value) to store in the entry."""
        return cal_offset(self.e, self.raw_t, self.ctx)

    def info(self):
        """Short status text for the dashboard card (pin / address, raw value or error)."""
        return 'GPIO%d &middot; %s mV &middot; %s' % (
            self.e['pin'], '-' if self.mv is None else '%d' % self.mv,
            'probe open / short' if self.r is None else '%.2f k&Omega;' % self.r)


def cal_offset(e, raw, ctx):
    """Offset so that raw * gain + offset = reference ('ref' degC, or the air sensor)."""
    if raw is None:
        raise ValueError('no reading')
    ref = str(e.get('ref', '')).strip()
    if ref:
        ref = float(ref)
    elif ctx is not None and ctx.t is not None:
        ref = ctx.t
    else:
        raise ValueError('type a reference temperature in Ref (or connect the air sensor)')
    return 'offset', round(ref - raw * e.get('gain', 1.0), 2)
