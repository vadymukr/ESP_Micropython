# Analog UV sensor module (GUVA-S12SD with amplifier, "UV Ray Module"): UV index ~ Vout / 0.1 V.
# Output stays below ~1.1 V, so it connects straight to an ADC1 pin (module on 3.3 V).
from machine import ADC, Pin


class Driver:
    def __init__(self, e, ctx=None):
        self.e = e
        self.adc = ADC(Pin(e['pin']))
        self.adc.atten(ADC.ATTN_11DB)
        self.mv = None

    def chans(self):
        return [('uvi', self.e['name'] + ' UV index', '', 1), ('mv', self.e['name'] + ' signal', 'mV', 0)]

    def read(self):
        s = 0
        for _ in range(16):
            s += self.adc.read_uv()
        self.mv = s / 16000
        return [self.mv / 100, self.mv]

    def info(self):
        return 'GPIO%d' % self.e['pin']
