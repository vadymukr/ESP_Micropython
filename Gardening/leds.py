# Optional 4-LED moisture indicator for one sensor (MicroPython).
#   LED 0 fully watered (draining / too wet) | LED 1 normal (good) | LED 2 water soon | LED 3 water now
#   drought = LED 3 blinking
# Modes (set from the web UI, saved in DATA_DIR/leds.json):
#   off  - all dark (energy saving)
#   on   - steady LED for the current band
#   eco  - short 80 ms flash every 5 s (~2% duty: about 1/50 of the LED current)
# Battery (if battery.py is enabled): low = all four flash twice every few seconds on top of the
# normal indication; critical = indication off, only a triple flash every 10 s.
# Spec: docs/SPEC.md#status-lights
try:
    import asyncio
except ImportError:
    import uasyncio as asyncio
import json
import os
from machine import Pin
import settings as S

MODES = ('off', 'on', 'eco')
LEVEL = {'wet': 0, 'drain': 0, 'good': 1, 'soon': 2, 'now': 3, 'dry': 3}
CFG = S.DATA_DIR + '/leds.json'


class Leds:
    """4-LED band indicator (fully watered / normal / water soon / water now)."""
    def __init__(self, pins, active_high=True):
        """Output pins (active high or low); mode from leds.json."""
        self.hi = active_high
        self.p = [Pin(x, Pin.OUT, value=0 if active_high else 1) for x in pins]
        self.mode = S.LED_MODE if S.LED_MODE in MODES else 'on'
        try:
            with open(CFG) as f:
                m = json.load(f).get('mode')
            if m in MODES:
                self.mode = m
        except (OSError, ValueError, AttributeError):
            pass
        self.level = None          # 0..3, None = no reading yet
        self.blink = False         # drought: blink LED 3
        self.batt = 'ok'           # 'ok' | 'low' | 'crit' (set by the app from battery.py)

    def set_mode(self, mode):
        """Mode 'on' | 'eco' | 'off', saved to leds.json."""
        self.mode = mode
        tmp = CFG + '.tmp'
        with open(tmp, 'w') as f:
            json.dump({'mode': mode}, f)
        os.rename(tmp, CFG)

    def show(self, band):
        """Show the band of the followed sensor (applied by run())."""
        self.level = LEVEL.get(band)
        self.blink = band == 'dry'

    def flash_once(self, band, low_batt=False):
        """Sleep-cycle wake: one short flash of the band LED (blocking, ~0.3 s)."""
        import time
        if self.mode == 'off':
            return
        seq = [(-1, 2)] if low_batt else []
        lv = LEVEL.get(band)
        if lv is not None:
            seq.append((lv, 2 if band == 'dry' else 1))
        for idx, times in seq:
            for _ in range(times):
                self._set(idx)
                time.sleep_ms(120)
                self._set(None)
                time.sleep_ms(150)

    def _set(self, idx):
        """idx: None = all off, -1 = all on, 0..3 = that LED only."""
        for k, p in enumerate(self.p):
            p.value((idx == -1 or k == idx) == self.hi)

    async def _flash(self, idx, times, on_ms, off_ms=150):
        """Blink LED idx times (on_ms / off_ms)."""
        for _ in range(times):
            self._set(idx)
            await asyncio.sleep_ms(on_ms)
            self._set(None)
            await asyncio.sleep_ms(off_ms)

    async def run(self):
        """Task: steady light, eco flashes, low-battery blink."""
        for k in range(len(self.p)):                    # lamp test at boot
            self._set(k)
            await asyncio.sleep_ms(250)
        self._set(None)
        n = 0
        while True:
            n += 1
            m, lv = self.mode, self.level
            if m == 'off' or (lv is None and self.batt == 'ok'):
                self._set(None)
                await asyncio.sleep_ms(1000)
                continue
            if self.batt == 'crit':
                await self._flash(-1, 3, 120)
                await asyncio.sleep(10)
                continue
            if self.batt == 'low' and (m == 'eco' or n % 3 == 0):
                await self._flash(-1, 2, 120)
                await asyncio.sleep_ms(300)
            if lv is None:
                await asyncio.sleep(2)
            elif m == 'on':
                if self.blink:
                    await self._flash(lv, 2, 500, 500)  # 2 s
                else:
                    self._set(lv)
                    await asyncio.sleep(2)
            else:                                       # eco
                await self._flash(lv, 2 if self.blink else 1, 80)
                await asyncio.sleep(5)
