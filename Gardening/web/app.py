# Garden logger - asyncio web app for ESP32 MicroPython (htmx + Alpine.js dashboard).
# This module holds the shared state (the running parts below), the sampling loop and start-up;
# the work is done in: util (helpers), sampler (sensors), wifi (network), pages (dashboard fragments),
# server (HTTP), feats (optional parts). Other modules use the names re-exported here (app.esc, ...).
#
# Heap safety: one sampler task (HTTP handlers never touch hardware), small HTML fragments, files
# streamed in 512-byte chunks, capped connections / headers / bodies, gc after every request, a
# free-heap low-water mark in the UI, and optional parts not imported while off.
# Spec: docs/SPEC.md#architecture
try:
    import asyncio            # MicroPython >= 1.21
except ImportError:
    import uasyncio as asyncio   # MicroPython 1.20 (used for vGuest WPA2/WPA3 compatibility)
import gc
import machine
import settings as S

gc.collect()
gc.threshold(gc.mem_free() // 4 + gc.mem_alloc())

# ---------------------------------------------------------------- shared state (set in main() / feats)
sens = rec = net = bands = leds = batt = pwr = pump = mqtt = rgb = gsm = disp = ctrl = None

# ---------------------------------------------------------------- re-exports for the other modules
# (panels and drivers use app.esc, app.rgb_apply(), ... - pyflakes reports them as unused imports)
from util import (uptime, time_ok, stamp, dur, kb, esc, clean, safe_csv, flash_free, has,  # noqa: E402,F401
                  heap_note, FEAT, FEATURES)
from sampler import Sensors                                                                    # noqa: E402,F401
from feats import (pump_enabled, batt_enabled, leds_mode, batt_apply, rgb_apply, leds_start,  # noqa: E402,F401
                   mqtt_start, gsm_start, display_apply, ctrl_apply, display_apply_soon, ctrl_apply_soon)
from feats import optional as _optional                                                        # noqa: E402,F401
from server import send                                                                        # noqa: E402,F401


class NoRec:
    """Stand-in while the recorder feature is off (recorder.py not loaded)."""
    active = resume = False
    fname = msg = ''
    rows = interval = end = started = 0

    def stop(self, msg=''):
        """Nothing to stop."""

    async def run(self):
        """No recording task."""


def restart_soon(secs=2):
    """Restart the board after the current reply was sent (outputs switched off first)."""
    async def _go():
        """Wait, switch outputs off, reset."""
        await asyncio.sleep(secs)
        try:
            _before_sleep()
        except Exception:
            pass
        machine.reset()
    asyncio.create_task(_go())


def _before_sleep():
    """Switch every output off and save the band state (deep sleep, restart)."""
    for part in (rgb, disp, ctrl):
        if part:
            part.off()
    if pump:
        pump.stop('going to sleep')
        pump._set(False)
    bands._persist()
    if leds:
        leds._set(None)


async def sample_loop():
    """Task: sample all sensors every SAMPLE_S and feed the parts that use the readings."""
    batt_next = 0
    while True:
        try:
            await sens.sample()
            pcts = [sens.pct(sens.raw[i], c[2], c[3]) for i, c in enumerate(sens.ch)]
            bands.update(pcts)
            if pump:
                pump.update(pcts)
            if mqtt:
                mqtt.feed(sens.raw, sens.t, sens.rh, sens.p, sens.gval)
            if gsm and gsm.net and uptime() % 30 < S.SAMPLE_S:
                gsm.check(pcts)
            if leds:
                i = min(S.LED_SENSOR, len(pcts) - 1)
                leds.show(bands.band(i, pcts[i]))
            if rgb:
                rgb.show([bands.band(i, p) for i, p in enumerate(pcts)], bool(pump and pump.cfg['fault']))
            if batt and uptime() >= batt_next:
                batt_next = uptime() + S.BATT_EVERY_S
                batt.read()
                if leds:
                    leds.batt = batt.state
        except Exception as e:
            print('sample error:', e)
        gc.collect()
        heap_note()
        await asyncio.sleep(S.SAMPLE_S)


def _busy():
    """Things that keep a sleep-cycle board awake: setup AP, WiFi connect, pump run, active controller."""
    return (net.ap.active() or net.pending is not None or (pump is not None and pump.busy()) or
            (ctrl is not None and ctrl.active()))


async def _main():
    """Start all tasks and the web server, then idle."""
    from feats import start_services
    from server import handle
    asyncio.create_task(net.run())
    asyncio.create_task(rec.run())
    asyncio.create_task(sample_loop())
    for part in (leds, rgb, pump):
        if part:
            asyncio.create_task(part.run())
    if pwr:
        asyncio.create_task(pwr.run(_busy, _before_sleep, lambda: batt is not None and batt.state != 'ok'))
    start_services()
    await asyncio.start_server(handle, '0.0.0.0', S.HTTP_PORT, backlog=5)
    print('http: listening on port', S.HTTP_PORT, '- free heap', gc.mem_free())
    while True:
        await asyncio.sleep(3600)


def main():
    """Boot: power settings, sensors, bands, optional parts, recorder, WiFi - then run the tasks."""
    global sens, rec, net, bands, pwr
    from bands import Bands
    from wifi import Net
    if has('power') and S.POWER_ENABLED and pwr is None:  # sets the CPU clock first
        from power import Power
        pwr = Power(uptime, time_ok)
    sens = Sensors()
    bands = Bands(len(sens.ch), uptime, time_ok)
    if leds is None and batt is None:
        _optional()
    if pump:
        pump.bands = bands
        pump.sens = sens                     # flow sensor protection / water statistics
    if has('recorder'):
        from recorder import Recorder
        rec = Recorder(sens)
    else:
        rec = NoRec()
    net = Net()
    gc.collect()
    asyncio.run(_main())
