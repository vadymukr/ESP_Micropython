# Sleep-cycle quick wake (power.py): imported by main.py only after a deep-sleep wake, so this
# code is never in RAM while the web app runs.
import time
try:
    import asyncio
except ImportError:
    import uasyncio as asyncio
import app
from app import Sensors, Recorder, uptime, time_ok
import settings as S


def quick():
    """Sleep-cycle timer wake: measure, log, update bands, flash, sleep - no WiFi/web (NTP once a day).
    Returns only when the full app should run instead (not a quick wake, or any error)."""
    from power import Power
    pwr = app.pwr = Power(uptime, time_ok)
    if not pwr.quick_ok():
        return
    try:
        if pwr.need_ntp():
            pwr.quick_ntp()
        app._optional()
        leds, batt = app.leds, app.batt
        sens = app.sens = Sensors()

        async def burst():
            for _ in range(S.CYCLE_SAMPLES):
                await sens.sample()
                await asyncio.sleep_ms(150)
        asyncio.run(burst())
        pcts = [sens.pct(sens.raw[i], c[2], c[3]) for i, c in enumerate(sens.ch)]
        r = Recorder(sens)
        if r.resume and time_ok():
            now = time.time()
            if r.end and now >= r.end:
                r.stop('Finished')
            else:
                r._row(now)                  # one averaged row per wake
        from bands import Bands
        b = Bands(len(sens.ch), lambda: int(time.time()), time_ok)
        if time_ok():
            b.cycle_step(pcts)
        low = False
        if batt:
            batt.read()
            low = batt.state != 'ok'
        if leds:
            i = min(S.LED_SENSOR, len(pcts) - 1)
            leds.flash_once(b.band(i, pcts[i]), low)
        if app.rgb:
            app.rgb.show([b.band(i, p) for i, p in enumerate(pcts)])
            app.rgb.flash_once(app.rgb.state)
        print('quick: %s | rec %s' % (' '.join('%.0f%%' % p for p in pcts), r.fname if r.resume else '-'))
        pwr.deepsleep(pwr.sleep_secs(low, quick=True))
    except Exception as e:
        print('quick wake failed, starting the full app:', repr(e))
