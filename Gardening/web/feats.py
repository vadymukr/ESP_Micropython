# Optional parts of the app: create, reconfigure or unload them (pump, LEDs, RGB light, battery,
# MQTT, GSM, displays, controllers). A part that is off or not configured is never imported (no RAM).
# The running objects live in app (app.pump, app.mqtt, ...); panels call these functions via app.*.
# Spec: docs/SPEC.md#features
try:
    import asyncio
except ImportError:
    import uasyncio as asyncio
import gc
import sys
import settings as S
import app
from util import has, saved, uptime, time_ok


# ---------------------------------------------------------------- saved switches (no module import)
def pump_enabled():
    """Pump panel Enable / Disable (pump.json 'enabled'); default on."""
    return saved('pump.json', 'enabled', True) is not False


def batt_enabled():
    """Battery monitor switched on (batt.json), else the settings.py template (BATT_PIN set)."""
    return bool(saved('batt.json', 'on', getattr(S, 'BATT_PIN', None) is not None))


def leds_mode():
    """Saved 4-LED indicator mode (leds.json): 'on' | 'eco' | 'off'."""
    return saved('leds.json', 'mode', None) or S.LED_MODE


def _ctrl_enabled():
    """Any enabled controller (control.json, or an old heater.json)."""
    lst = saved('control.json', 'list', None)
    if lst is not None:
        return any(e.get('on') for e in lst)
    return bool(saved('heater.json', 'on', False))


def _displays():
    """Any display set up (display.json), else the board's built-in screen (settings.DISPLAY_BUILTIN)."""
    lst = saved('display.json', 'list', None)
    return bool(lst) if lst is not None else getattr(S, 'DISPLAY_BUILTIN', None) is not None


def _soon(fn, tag):
    """Run fn() 0.3 s from now: after the panel's reply is sent and its module released (RAM)."""
    async def _go():
        """Wait, collect garbage, run fn."""
        await asyncio.sleep_ms(300)
        gc.collect()
        try:
            fn()
        except Exception as e:
            print(tag, repr(e))
    asyncio.create_task(_go())


# ---------------------------------------------------------------- start at boot
def optional():
    """Create the optional parts needed before the tasks start (boot, or a sleep-cycle quick wake)."""
    if app.rgb is None and has('rgb') and saved('rgb.json', 'on', False):
        rgb_apply(False)                     # task started in app._main (or flash_once in a quick wake)
    if has('pump') and getattr(S, 'PUMP_PIN', None) is not None and app.pump is None and pump_enabled():
        from pump import Pump                # drives the pump pin OFF first thing
        app.pump = Pump(uptime, time_ok)
    if has('leds') and S.LED_PINS and leds_mode() != 'off':
        from leds import Leds
        app.leds = Leds(S.LED_PINS, S.LED_ACTIVE_HIGH)
    if has('battery') and batt_enabled():
        batt_apply()


def start_services():
    """Network-side parts started from app._main: MQTT, GSM, displays, controllers."""
    if has('mqtt') and saved('mqtt.json', 'on', bool(getattr(S, 'MQTT_ENABLED', False))):
        mqtt_start()
    if has('gsm') and saved('gsm.json', 'on', False):
        gsm_start()
    if has('display') and _displays():
        display_apply()
    if has('control') and _ctrl_enabled():
        ctrl_apply()


# ---------------------------------------------------------------- one function per part
def batt_apply():
    """(Re)create the battery monitor from batt.json, or remove it when switched off."""
    from battery import Battery, load_cfg
    c = load_cfg()
    app.batt = Battery(c) if c['on'] else None
    if app.batt:
        app.batt.read()
    if app.leds:
        app.leds.batt = app.batt.state if app.batt else 'ok'
    return app.batt


def rgb_apply(start_task=True):
    """RGB status light from rgb.json: create, reconfigure or switch off."""
    from rgb import Rgb, load_cfg
    c = load_cfg()
    if app.rgb:
        app.rgb.reconfig(c)
    elif c['on']:
        app.rgb = Rgb(c)
        if start_task:
            asyncio.create_task(app.rgb.run())
    return app.rgb


def leds_start():
    """4-LED indicator switched on from the page while not loaded."""
    if app.leds is None and S.LED_PINS:
        from leds import Leds
        app.leds = Leds(S.LED_PINS, S.LED_ACTIVE_HIGH)
        if app.batt:
            app.leds.batt = app.batt.state
        asyncio.create_task(app.leds.run())
    return app.leds


def _online():
    """Internet for MQTT: WiFi, or GPRS when the GSM panel allows it."""
    w = app.net.sta.isconnected()
    return w or (app.gsm is not None and app.gsm.use_gprs(w))


def mqtt_start():
    """Start the MQTT publisher task (boot, or switched on from the page)."""
    if app.mqtt is None:
        from mqtt import Mqtt
        m = app.mqtt = Mqtt(uptime)
        m.gprs, m.wifi = app.gsm, app.net.sta.isconnected
        if app.pump:
            app.pump.on_event = m.event
        asyncio.create_task(m.run(None, _online))      # mqtt.snapshot() builds the JSON
    return app.mqtt


def gsm_start():
    """Start the modem task (boot, or switched on from the page) if a modem is wired."""
    if app.gsm is None and getattr(S, 'MODEM_TX', None) is not None:
        from gsm import Gsm
        from gsm_cfg import load_cfg
        app.gsm = Gsm(load_cfg(), app.sens)
        asyncio.create_task(app.gsm.run())
        if app.mqtt:
            app.mqtt.gprs = app.gsm
    return app.gsm


def display_apply():
    """Displays from display.json: create, reconfigure, or unload everything when none is left."""
    from display import Display
    from display_cfg import load_cfg
    c = load_cfg()
    if app.disp:
        app.disp.reconfig(c)                 # an empty list ends its task
        if not c['list']:
            app.disp = None
    elif c['list']:
        app.disp = Display(c, app.sens)
        asyncio.create_task(app.disp.run())
    if app.disp is None:
        for m in ('display', 'display_cfg', 'display_pages', 'display_render', 'disp_oled', 'disp_tft', 'gfont'):
            if m == 'gfont' and m in sys.modules:
                sys.modules[m].close_all()
            sys.modules.pop(m, None)
    return app.disp


def ctrl_apply():
    """Controllers from control.json: create, reconfigure, or unload when none is enabled."""
    from control import Controls
    from control_cfg import load_cfg
    c = load_cfg()
    if app.ctrl:
        app.ctrl.reconfig(c)                 # none enabled: pins released, its task ends
        if not app.ctrl.ctls:
            app.ctrl = None
    elif any(e['on'] for e in c['list']):
        app.ctrl = Controls(c)
        asyncio.create_task(app.ctrl.run())
    if app.ctrl is None:
        sys.modules.pop('control', None)
        sys.modules.pop('control_cfg', None)
    return app.ctrl


def display_apply_soon():
    """display_apply() after the panel reply (see _soon)."""
    _soon(display_apply, 'display:')


def ctrl_apply_soon():
    """ctrl_apply() after the panel reply (see _soon)."""
    _soon(ctrl_apply, 'control:')
