# Displays (feature 'display'): OLED (SSD1306 / SH1106, I2C) and colour TFT (ST7789 / ST7735, SPI)
# showing the soil bands with a big number, rotating through pages. Set up from the dashboard
# Displays panel (DATA_DIR/display.json); not loaded while the feature is off or no display is set.
#
# Pages are built as plain data (title, value, unit, band, lines) and laid out for any screen size,
# so another renderer (LVGL on a PSRAM board, later) can reuse page() unchanged.
# Spec: docs/SPEC.md#displays
try:
    import asyncio
except ImportError:
    import uasyncio as asyncio
import time
import settings as S
from display_pages import pages_of, page        # noqa: F401
from display_render import render

def _in_hours(spec):
    """'22:00-06:30' -> True while the local time is inside (display off). '' = never."""
    try:
        a, b = spec.split('-')
        a = int(a[:2]) * 60 + int(a[3:5])
        b = int(b[:2]) * 60 + int(b[3:5])
    except (ValueError, IndexError):
        return False
    from app import time_ok
    if not time_ok():
        return False
    t = time.localtime(time.time() + S.TZ_OFFSET_H * 3600)
    m = t[3] * 60 + t[4]
    return a <= m < b if a <= b else m >= a or m < b


# ---------------------------------------------------------------- manager
class Display:
    """All configured screens: drivers, page rotation, night-off hours, touch."""
    def __init__(self, c, sens):
        """Screens from settings c; sens gives the shared I2C bus."""
        self.sens = sens
        self.devs = []                                     # [entry, driver or None, error]
        self.reconfig(c)

    def reconfig(self, c):
        """Switch the old screens off and set up the new list (drivers that fail keep their error)."""
        for d in self.devs:
            if d[1]:
                try:
                    d[1].power(False)
                except Exception:
                    pass
        self.devs = []
        spi_ids = [1] if S.CHIP in ('esp32c3', 'esp32c6') else [2, 1]
        for e in c['list']:
            drv, err = None, ''
            try:
                if e['type'][:2] in ('ss', 'sh'):
                    if not self.sens.i2c:
                        raise OSError('no I2C bus')
                    from disp_oled import Oled
                    drv = Oled(self.sens.i2c, 128, 32 if e['type'].endswith('32') else 64, e.get('addr', 60),
                               e['type'].startswith('sh1106'), e.get('rot', 0))
                    drv.contrast(e.get('bright', 80))
                else:
                    if not spi_ids:
                        raise OSError('no free SPI bus')
                    from disp_tft import Tft
                    drv = Tft(e, spi_ids.pop(0))
            except Exception as ex:
                err = str(ex) or repr(ex)
                if 'ENODEV' in err or 'ETIMEDOUT' in err:
                    err = ('no display answers at I2C 0x%02X - check VCC, GND, SDA, SCL and the address'
                           % e.get('addr', 60) if e['type'][:2] in ('ss', 'sh') else err)
                print('display %s: %s' % (e['type'], err))
            self.devs.append([e, drv, err, 0, -1, None, True, 0, 0])
            # entry, drv, err, page idx, test-until, last page, on, page shift (touch), awake-until (touch)
        self.t0 = time.time()
        from machine import Pin
        self.touch = [(Pin(e['touch'], Pin.IN), d) for e, d in zip(c['list'], self.devs) if e.get('touch') is not None]
        if self.touch and getattr(self, 'running', False):
            asyncio.create_task(self._touch())             # reconfigured: the old touch task has ended

    def off(self):
        """All screens off (before sleep)."""
        for d in self.devs:
            if d[1]:
                try:
                    d[1].power(False)
                except Exception:
                    pass

    def status(self, k):
        """'running WxH' or 'not running: <error>' for the panel."""
        if k >= len(self.devs):
            return 'starting...'
        e, drv, err = self.devs[k][:3]
        return 'running %dx%d' % (drv.w, drv.h) if drv else 'not running: ' + err

    async def run(self):
        """Task: refresh every 2 s (and the touch task); ends when no screen is left."""
        self.running = True
        if self.touch:
            asyncio.create_task(self._touch())
        while self.devs:                                   # all removed: task ends (module unloaded)
            await asyncio.sleep(2)
            for d in self.devs:
                e, drv = d[0], d[1]
                if not drv:
                    continue
                try:
                    self._tick(d, e, drv)
                except Exception as ex:
                    d[2] = str(ex)
                    print('display:', repr(ex))

    async def _touch(self):
        """Touch panel IRQ (XPT2046 T_IRQ goes low while touched): next page; at night: on for 60 s."""
        devs = self.devs
        while self.devs is devs:
            for p, d in self.touch:
                if p.value() == 0:
                    d[8] = time.time() + 60
                    if d[6]:
                        d[7] += 1
                    if d[1]:
                        try:
                            self._tick(d, d[0], d[1])
                        except Exception:
                            pass
                    while p.value() == 0:                  # wait for release
                        await asyncio.sleep_ms(50)
            await asyncio.sleep_ms(80)

    def _tick(self, d, e, drv):
        """One refresh of screen d: night hours, page rotation, redraw if the page changed."""
        now = time.time()
        if d[4] > now:                                     # test pattern showing
            return
        off = _in_hours(e.get('off', '')) and d[8] < now
        if off == d[6]:
            drv.power(not off)
            d[6] = not off
            d[5] = None
        if off:
            return
        pl = pages_of(e)
        idx = (int((now - self.t0) // max(2, e.get('page_s', 5))) + d[7]) % len(pl)
        pg = page(*pl[idx])
        full = idx != d[3] or d[5] is None or d[5] == 'test'
        if full or pg != d[5]:
            render(drv, pg, full)
            d[3], d[5] = idx, pg
