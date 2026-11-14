# ACS758 Hall current sensor (e.g. ACS758LCB-050B: +-50 A, 40 mV/A, zero at Vcc/2 = 2.5 V on 5 V).
# Output 0.5-4.5 V: divider to the ADC1 pin, R1 100k / R2 82k (div 2.22: 4.5 V -> 2.03 V).
# "Zero = now" with no current flowing stores the real zero point.
from machine import ADC, Pin


class Driver:
    def __init__(self, e, ctx=None):
        self.e = e
        self.adc = ADC(Pin(e['pin']))
        self.adc.atten(ADC.ATTN_11DB)
        self.out = None                    # sensor output, mV (before the divider)

    def chans(self):
        return [('a', self.e['name'], 'A', 2)]

    def read(self):
        e = self.e
        s = 0
        for _ in range(32):
            s += self.adc.read_uv()
        self.out = s / 32000 * float(e.get('div', 2.22))
        return [(self.out - float(e.get('zero_mv', 2500))) / (float(e.get('mv_a', 40)) or 40)]

    def cal(self):
        if self.out is None:
            raise ValueError('no reading')
        return 'zero_mv', round(self.out, 1)

    def info(self):
        return 'GPIO%d &middot; sensor %s mV' % (self.e['pin'], '-' if self.out is None else '%d' % self.out)
