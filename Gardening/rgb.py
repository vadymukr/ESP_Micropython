# RGB LED status light for the soil bands (MicroPython), e.g. KY-016 / "RGB three colour" module.
# Set up from the dashboard (Watering bands - settings -> RGB status light, DATA_DIR/rgb.json);
# not loaded while off.
#
# Colours: good = green, soon = yellow, now = red, dry = red blinking, wet = blue,
#          drain = light blue, pump locked (dry run) = magenta blinking, no reading = dark.
# Follows the worst band of all soil sensors, or one chosen sensor ('sensor' = index).
# Modes: 'on' steady, 'eco' short flash every 5 s (~2 % of the current), 'off'.
# Wiring: R, G, B pins each through ~220 Ohm to the LED (the KY-016 has resistors on board),
#         common cathode -> GND (active high) or common anode -> 3V3 (set 'anode').
try:
    import asyncio
except ImportError:
    import uasyncio as asyncio
import json
import os
from machine import Pin, PWM
import settings as S

CFG = S.DATA_DIR + '/rgb.json'
DEFAULTS = {'on': False, 'pins': list(getattr(S, 'RGB_PINS', (25, 26, 27))), 'anode': getattr(S, 'RGB_ANODE', False), 'mode': 'on', 'bright': 60, 'sensor': -1}
COLOR = {'good': (0, 1, 0), 'soon': (1, 0.6, 0), 'now': (1, 0, 0), 'dry': (1, 0, 0), 'wet': (0, 0, 1),
         'drain': (0, 0.6, 1), 'fault': (1, 0, 1)}
BLINK = ('dry', 'fault')
WORST = ('fault', 'dry', 'now', 'wet', 'soon', 'drain', 'good')     # first match wins


def load_cfg():
    c = dict(DEFAULTS)
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


class Rgb:
    def __init__(self, c):
        self.c, self.pwm = c, []
        self.state = None              # band key shown
        self.reconfig(c)

    def reconfig(self, c):
        """New settings from the page; switched off = pins released."""
        for p in self.pwm:
            p.deinit()
        self.c = c
        self.pwm = [PWM(Pin(p), freq=1000) for p in c['pins']] if c['on'] else []
        self._set(None)

    def show(self, bands, pump_fault=False):
        """bands: list of band keys of the soil sensors."""
        if pump_fault:
            self.state = 'fault'
            return
        i = self.c['sensor']
        if 0 <= i < len(bands):
            self.state = bands[i]
            return
        self.state = None
        for k in WORST:
            if k in bands:
                self.state = k
                break

    def _set(self, key):
        col = COLOR.get(key, (0, 0, 0))
        b = max(0, min(100, self.c['bright'])) / 100
        for p, v in zip(self.pwm, col):
            d = int(v * b * 1023)
            p.duty(1023 - d if self.c['anode'] else d)

    def off(self):
        self._set(None)

    def flash_once(self, key):
        """Sleep-cycle wake: one short flash (blocking ~0.3 s)."""
        import time
        if self.c['mode'] == 'off' or key not in COLOR:
            return
        for _ in range(2 if key in BLINK else 1):
            self._set(key)
            time.sleep_ms(150)
            self._set(None)
            time.sleep_ms(150)

    async def run(self):
        for k in ('now', 'good', 'wet'):              # lamp test: red, green, blue
            self._set(k)
            await asyncio.sleep_ms(300)
        self._set(None)
        while True:
            m, k = self.c['mode'], self.state
            if m == 'off' or k is None or not self.pwm:
                self._set(None)
                await asyncio.sleep(1)
            elif m == 'eco':
                self._set(k)
                await asyncio.sleep_ms(80)
                self._set(None)
                await asyncio.sleep(5)
            elif k in BLINK:
                self._set(k)
                await asyncio.sleep_ms(500)
                self._set(None)
                await asyncio.sleep_ms(500)
            else:
                self._set(k)
                await asyncio.sleep(1)
