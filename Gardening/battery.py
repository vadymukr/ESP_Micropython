# Optional battery / supply voltage monitor (MicroPython). Disabled unless settings.BATT_PIN is set.
#
# Wiring: battery + -> R1 -> ADC pin -> R2 -> GND   (pin voltage = Vbat * R2 / (R1 + R2))
#   Use an ADC1 pin (32, 33, 34, 35, 36=VP, 39=VN): ADC2 does not work while WiFi is on.
#   Keep the pin below ~3.1 V at full charge. Add 100 nF from the pin to GND against noise.
#   12 V lead-acid / LiFePO4 4S : R1 100k, R2 22k  -> ratio 5.545 (14.4 V -> 2.60 V)
#   5 V supply / power bank     : R1 10k,  R2 10k  -> ratio 2.0   (5.25 V -> 2.63 V)
#   1S Li-ion (4.2 V)           : R1 47k,  R2 100k -> ratio 1.47  (4.2 V -> 2.86 V)
# Large resistors (100k+) keep the divider drain tiny (12 V / 122k = 0.1 mA).
from machine import ADC, Pin


class Battery:
    def __init__(self, pin, ratio, low_v, crit_v, cal=1.0):
        self.adc = ADC(Pin(pin))
        self.adc.atten(ADC.ATTN_11DB)
        self.pin, self.ratio, self.low_v, self.crit_v, self.cal = pin, ratio, low_v, crit_v, cal
        self.v = None
        self.state = 'ok'

    def read(self):
        s = 0
        for _ in range(16):
            s += self.adc.read_uv()          # factory-calibrated microvolts
        v = s / 16 / 1e6 * self.ratio * self.cal
        self.v = v if self.v is None else self.v * 0.7 + v * 0.3     # smooth load dips
        # hysteresis of 0.1 V so the state does not flicker at the threshold
        if self.v <= self.crit_v or (self.state == 'crit' and self.v < self.crit_v + 0.1):
            self.state = 'crit'
        elif self.v <= self.low_v or (self.state != 'ok' and self.v < self.low_v + 0.1):
            self.state = 'low'
        else:
            self.state = 'ok'
        return self.v
