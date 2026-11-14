# Battery / supply voltage monitor (MicroPython). Set up from the dashboard Battery panel
# (DATA_DIR/batt.json); settings.py BATT_* are the template defaults. Not loaded while off.
#
# Wiring:  V+ ──R1──┬── ADC pin            pin voltage = V * R2 / (R1 + R2)
#                   ├── R2 ── GND
#                   ├── 100 nF ── GND     (noise filter, next to the pin)
#                   └── 3.3 V zener or BAT54S clamp to GND/3V3 (spike protection, optional)
#   GND of the battery and the ESP32 must be common.
#   Use a free ADC1 pin: 32, 33, 36 (VP) or 39 (VN). ADC2 pins do not work while WiFi is on;
#   34/35 are the soil sensors here.
#   The divider is sized so the pin stays <= ~2.45 V at the highest input voltage: the ESP32 ADC
#   (11 dB) is accurate only up to ~2.45 V, and the absolute pin limit is 3.3 V. R1 of 100 kOhm also
#   limits the current into the pin's protection diodes if the input goes far too high.
#
#   Preset            max input  R1     R2     ratio  pin at max  drain   alarms (low / critical)
#   5 V supply/USB    5.5 V      100k   68k    2.47   2.23 V      30 uA   4.6 / 4.4 V
#   12 V lead-acid    15 V       100k   15k    7.67   1.96 V      0.1 mA  11.8 / 11.4 V
#   custom            Vmax       100k   largest E12 value with pin <= 2.3 V at Vmax (see suggest())
#
# Calibration: measure the real voltage with a multimeter and enter it in the panel; the factor
# (resistor tolerance, ADC error) is saved as 'cal'.
import json
import os
from machine import ADC, Pin
import settings as S

CFG = S.DATA_DIR + '/batt.json'
PINS = tuple(p for p in getattr(S, 'ADC_PINS', (32, 33, 36, 39)) if p not in (34, 35) or getattr(S, 'CHIP', 'esp32') != 'esp32')
PRESETS = {'5': (5.5, 100, 68, 4.6, 4.4), '12': (15.0, 100, 15, 11.8, 11.4)}   # vmax, R1 k, R2 k, low, crit
E12 = (10, 12, 15, 18, 22, 27, 33, 39, 47, 56, 68, 82, 100)
PIN_OK_V = 2.45                                 # accurate ADC range (11 dB)
PIN_MAX_V = 3.1                                 # never above this


def defaults():
    """settings.py template; R1/R2/max input come from the preset (5 V unless BATT_PRESET says otherwise)."""
    pin = getattr(S, 'BATT_PIN', None)
    pre = getattr(S, 'BATT_PRESET', '5')
    vmax, r1, r2, low, crit = PRESETS.get(pre, PRESETS['5'])
    return {'on': pin is not None, 'pin': pin if pin in PINS else 36, 'preset': pre if pre in PRESETS else 'custom',
            'vmax': vmax, 'r1': r1, 'r2': r2, 'low': getattr(S, 'BATT_LOW_V', low),
            'crit': getattr(S, 'BATT_CRIT_V', crit), 'cal': getattr(S, 'BATT_CAL', 1.0)}


def load_cfg():
    c = defaults()
    try:
        with open(CFG) as f:
            d = json.load(f)
        for k in c:
            if k in d:
                c[k] = d[k]
    except (OSError, ValueError):
        pass
    return c


def save_cfg(c):
    tmp = CFG + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(c, f)
    os.rename(tmp, CFG)


def suggest(vmax, r1=100):
    """Largest E12 R2 (k) with the pin <= 2.3 V at vmax, for R1 = r1 k."""
    best = None
    for dec in (1, 10, 100):
        for e in E12:
            r2 = e * dec / 10
            if vmax * r2 / (r1 + r2) <= 2.3 and (best is None or r2 > best):
                best = r2
    return best


class Battery:
    def __init__(self, c):
        self.cfg = c
        self.pin = c['pin']
        self.adc = ADC(Pin(self.pin))
        self.adc.atten(ADC.ATTN_11DB)
        self.ratio = (c['r1'] + c['r2']) / c['r2']
        self.low_v, self.crit_v, self.cal = c['low'], c['crit'], c['cal']
        self.v = None                  # calibrated, smoothed input voltage
        self.pin_v = None              # voltage at the pin (smoothed)
        self.state = 'ok'

    def read(self):
        s = 0
        for _ in range(16):
            s += self.adc.read_uv()          # factory-calibrated microvolts
        pv = s / 16 / 1e6
        self.pin_v = pv if self.pin_v is None else self.pin_v * 0.7 + pv * 0.3   # smooth load dips
        self.v = self.pin_v * self.ratio * self.cal
        # hysteresis of 0.1 V so the state does not flicker at the threshold
        if self.v <= self.crit_v or (self.state == 'crit' and self.v < self.crit_v + 0.1):
            self.state = 'crit'
        elif self.v <= self.low_v or (self.state != 'ok' and self.v < self.low_v + 0.1):
            self.state = 'low'
        else:
            self.state = 'ok'
        return self.v
