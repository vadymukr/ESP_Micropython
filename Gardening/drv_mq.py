# MQ-series gas sensors (MQ-2, MQ-4, MQ-6, MQ-7, MQ-9, MQ-135) on the analog output AO.
# The module runs on 5 V (heater ~150 mA each) and AO goes up to 5 V: use a divider to the ADC1 pin,
# R1 100k / R2 82k (div 2.22: 5 V -> 2.25 V). Rs = RL * (Vc - Vout) / Vout, ppm = a * (Rs/R0)^b from
# the datasheet curve (approximate; MQ-135 CO2 includes +400 ppm outdoor baseline). Calibrate R0 in
# clean outdoor air after >= 24 h burn-in: "R0 = now". Readings start 3 min after power-on (heater).
# Indicative only - NOT a certified gas / CO alarm. MQ-7 is specified with heater cycling; on a
# constant 5 V module its CO value is rough.
# Spec: docs/SPEC.md#driver-interface
import time
from machine import ADC, Pin

#        gas,   a,      b,      Rs/R0 in clean air
MQ = {'mq2': ('LPG', 574.25, -2.222, 9.83), 'mq4': ('CH4', 1012.7, -2.786, 4.4),
      'mq6': ('LPG', 1009.2, -2.35, 10.0), 'mq7': ('CO', 99.042, -1.518, 27.5),
      'mq9': ('CO', 1000.5, -2.186, 9.6), 'mq135': ('CO2', 110.47, -2.862, 3.6)}


class Driver:
    """Registry driver: MQ-series gas sensors (MQ-2, MQ-4, MQ-6, MQ-7, MQ-9, MQ-135) on the analog outpu.
    Interface: docs/SPEC.md#driver-interface."""
    def __init__(self, e, ctx=None):
        """Set up from the registry entry e (pins / address / settings); ctx = app.sens (shared I2C bus, free
        UARTs, air readings)."""
        self.e = e
        self.m = MQ[e['type']]
        self.adc = ADC(Pin(e['pin']))
        self.adc.atten(ADC.ATTN_11DB)
        self.t0 = time.ticks_ms()
        self.mv = self.rs = None

    def chans(self):
        """Channels this sensor reports: [(key, label, unit, decimals)]."""
        n = self.e['name']
        return [('ppm', '%s %s' % (n, self.m[0]), 'ppm', 0), ('ratio', n + ' Rs/R0', '', 2)]

    def read(self):
        """One value per channel, None where there is no valid reading this time."""
        e = self.e
        s = 0
        for _ in range(16):
            s += self.adc.read_uv()
        self.mv = s / 16000
        vout = self.mv * float(e.get('div', 2.22)) / 1000
        vc = float(e.get('vc', 5.0))
        if vout <= 0.01 or vout >= vc:
            self.rs = None
            return [None, None]
        self.rs = float(e.get('rl', 10)) * (vc - vout) / vout          # kOhm
        ratio = self.rs / (float(e.get('r0', 10)) or 10)
        if time.ticks_diff(time.ticks_ms(), self.t0) < 180000:
            return [None, ratio]                                       # heater warming up
        gas, a, b, clean = self.m
        ppm = a * ratio ** b + (400 if e['type'] == 'mq135' else 0)
        return [min(ppm, 100000), ratio]

    def cal(self):
        """'R0 = now' in clean air: R0 = Rs / (Rs/R0 in clean air from the datasheet)."""
        if not self.rs:
            raise ValueError('no reading')
        return 'r0', round(self.rs / self.m[3], 3)

    def info(self):
        """Short status text for the dashboard card (pin / address, raw value or error)."""
        w = time.ticks_diff(time.ticks_ms(), self.t0) < 180000
        return 'GPIO%d &middot; %s mV &middot; Rs %s k&Omega;%s' % (
            self.e['pin'], '-' if self.mv is None else '%d' % self.mv,
            '-' if self.rs is None else '%.1f' % self.rs, ' &middot; warming up' if w else '')
