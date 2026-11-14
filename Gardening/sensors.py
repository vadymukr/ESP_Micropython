# Sensor registry for the garden logger (MicroPython). The sensors on the board are a list in
# DATA_DIR/sensors.json, edited from the dashboard Sensors panel; settings.SENSORS is only the
# template used to create the list the first time (migration of the old fixed soil sensors).
#
#   {"list": [{"id": "S1", "type": "soil",   "name": "Sensor A", "pin": 34, "dry": 3390, "wet": 1669},
#             {"id": "S3", "type": "analog", "name": "Light", "pin": 32, "unit": "%",
#              "lo_mv": 0, "lo": 0, "hi_mv": 3000, "hi": 100, "dec": 1}]}
#
# Adding a sensor TYPE later = a line in web/sensor_types.txt + a driver
# module drv_<type>.py with a class Driver(entry, ctx) (ctx.i2c = the shared I2C bus, ctx.uart_free =
# free UART ids, ctx.t / ctx.rh = air temperature / humidity), optional cal() for a calibrate button:
# chans() -> [(key, label, unit, decimals)],
# read() -> [value or None per channel], and a short 'info' text. Nothing else in the app changes:
# every channel automatically gets a dashboard card, an MQTT field and a CSV column.
# 'soil' is built into app.Sensors (bands, pump and LEDs work with soil sensors).
import json
import os
import settings as S

CFG = S.DATA_DIR + '/sensors.json'
# Pin tables come from the chip profile in settings.py (classic ESP32 values as fallback)
ADC_PINS = getattr(S, 'ADC_PINS', (32, 33, 34, 35, 36, 39))     # ADC1: works with WiFi on (ADC2 does not)
IO_PINS = getattr(S, 'IO_PINS', (4, 13, 14, 16, 17, 18, 19, 21, 22, 23, 25, 26, 27, 32, 33))

# The sensor types (names, pin kinds, form fields, drivers) are in web/sensor_types.txt, read line by
# line by sensor_types.py in the Sensors panel only, so the pin helpers below stay small.

def _default():
    out = []
    for k, (pin, dry, wet, name) in enumerate(S.SENSORS):
        out.append({'id': 'S%d' % (k + 1), 'type': 'soil', 'name': name, 'pin': pin, 'dry': dry, 'wet': wet})
    return out


def load():
    """The sensor list; created from settings.SENSORS the first time (and the old MQTT field
    names 's0', 's1' are converted to the new sensor ids)."""
    try:
        with open(CFG) as f:
            lst = json.load(f)['list']
        if isinstance(lst, list) and lst:
            return lst
    except (OSError, ValueError, KeyError):
        pass
    lst = _default()
    save(lst)
    try:                                       # migrate MQTT field selection by position
        p = S.DATA_DIR + '/mqtt.json'
        with open(p) as f:
            m = json.load(f)
        soil = [e['id'] for e in lst]
        m['fields'] = ['sen_' + soil[int(x[1:])] if x[:1] == 's' and x[1:].isdigit() and int(x[1:]) < len(soil)
                       else x for x in m.get('fields', [])]
        with open(p, 'w') as f:
            json.dump(m, f)
    except (OSError, ValueError, KeyError):
        pass
    return lst


def air_on():
    """Built-in air sensor (auto-detected on I2C) wanted? Removable in the Sensors panel."""
    try:
        with open(CFG) as f:
            return json.load(f).get('air', True) is not False
    except (OSError, ValueError):
        return True


def save(lst, air=None):
    """Write the list; air = True / False switches the built-in air sensor (None = keep)."""
    if air is None:
        air = air_on()
    tmp = CFG + '.tmp'
    with open(tmp, 'w') as f:
        json.dump({'list': lst, 'air': air}, f)
    os.rename(tmp, CFG)


def new_id(lst):
    n = 1
    ids = [e['id'] for e in lst]
    while 'S%d' % n in ids:
        n += 1
    return 'S%d' % n


def taken_pins(lst, skip_id=None):
    """Pins used by other features and by the other sensors (pin -> what uses it)."""
    t = {S.I2C_SCL: 'I2C SCL', S.I2C_SDA: 'I2C SDA'}
    for p in S.LED_PINS:
        t[p] = 'LED'
    for p, lbl in getattr(S, 'BOARD_PINS', {}).items():       # wired on the board (screen, touch...)
        t[p] = lbl
    for p in S.SENSOR_PWR_PINS:
        t[p] = 'sensor power'
    try:                                       # pump disabled in its panel: its GPIO is free
        with open(S.DATA_DIR + '/pump.json') as f:
            pump_off = json.load(f).get('enabled', True) is False
    except (OSError, ValueError):
        pump_off = False
    for k, lbl in (('PUMP_PIN', 'pump'), ('TANK_PIN', 'tank switch'), ('WAKE_TOUCH', 'touch wake'),
                   ('WAKE_BUTTON', 'wake button'), ('MODEM_TX', 'modem TX'), ('MODEM_RX', 'modem RX'),
                   ('MODEM_PWKEY', 'modem PWKEY'), ('MODEM_RST', 'modem RST'), ('MODEM_POWER', 'modem power')):
        p = getattr(S, k, None)
        if p is not None and not (pump_off and k in ('PUMP_PIN', 'TANK_PIN')):
            t[p] = lbl
    try:
        with open(S.DATA_DIR + '/batt.json') as f:
            b = json.load(f)
        if b.get('on'):
            t[b['pin']] = 'battery'
    except (OSError, ValueError, KeyError):
        pass
    try:
        with open(S.DATA_DIR + '/rgb.json') as f:
            r = json.load(f)
        if r.get('on'):
            for p, c in zip(r['pins'], 'RGB'):
                t[p] = 'RGB light ' + c
    except (OSError, ValueError, KeyError):
        pass
    try:                                       # controllers (heater / light / fan); old heater.json until migrated
        with open(S.DATA_DIR + '/control.json') as f:
            for e in json.load(f)['list']:
                if e.get('pin') is not None:
                    t[e['pin']] = e.get('name', 'controller')
    except (OSError, ValueError, KeyError):
        try:
            with open(S.DATA_DIR + '/heater.json') as f:
                h = json.load(f)
            if h.get('on') and h.get('pin') is not None:
                t[h['pin']] = 'heater'
        except (OSError, ValueError, KeyError):
            pass
    try:
        with open(S.DATA_DIR + '/display.json') as f:
            for k, d in enumerate(json.load(f)['list']):
                for p in ('sck', 'mosi', 'dc', 'rst', 'cs', 'bl', 'touch'):
                    if d.get(p) is not None:
                        t[d[p]] = 'display %d %s' % (k + 1, p.upper())
    except (OSError, ValueError, KeyError):
        pass
    for e in lst:
        if e['id'] != skip_id:
            for k in ('pin', 'pin2', 'pin3'):
                if k in e:
                    t[e[k]] = e['name']
    return t


def free_pins(kind, lst, skip_id=None):
    if kind == 'i2c':
        return []
    t = taken_pins(lst, skip_id)
    return [p for p in (ADC_PINS if kind == 'adc' else IO_PINS) if p not in t]      # 'io', 'uart', 'spi'


def i2c_taken(lst, air, skip_id=None):
    """I2C addresses in use: the auto-detected air sensor (air = list of addresses) and other sensors."""
    t = {}
    for a in air:
        t[a] = 'air sensor'
    for e in lst:
        if e['id'] != skip_id and isinstance(e.get('addr'), str):             # only I2C sensors have 'addr'
            t[int(e['addr'], 16)] = e['name']
    return t
