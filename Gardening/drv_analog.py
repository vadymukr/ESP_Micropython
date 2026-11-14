# Generic analog sensor driver: ADC1 pin, voltage scaled linearly between two calibration points
# (lo_mv -> lo, hi_mv -> hi), e.g. a light sensor, pressure transducer, level probe, potentiometer.
# Pin range 0-3.1 V (11 dB attenuation, accurate to ~2.45 V): divide higher voltages first.
from machine import ADC, Pin


class Driver:
    def __init__(self, e):
        self.e = e
        self.adc = ADC(Pin(e['pin']))
        self.adc.atten(ADC.ATTN_11DB)
        self.mv = None

    def chans(self):
        e = self.e
        return [('v', e['name'], e.get('unit', ''), int(e.get('dec', 1)))]

    def read(self):
        e = self.e
        s = 0
        for _ in range(16):
            s += self.adc.read_uv()
        self.mv = s / 16000
        span = e['hi_mv'] - e['lo_mv']
        if not span:
            return [None]
        return [e['lo'] + (self.mv - e['lo_mv']) * (e['hi'] - e['lo']) / span]

    def info(self):
        return 'GPIO%d &middot; %s mV' % (self.e['pin'], '-' if self.mv is None else '%d' % self.mv)
