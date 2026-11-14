# Sensor registry for the garden logger (MicroPython). The sensors on the board are a list in
# DATA_DIR/sensors.json, edited from the dashboard Sensors panel; settings.SENSORS is only the
# template used to create the list the first time (migration of the old fixed soil sensors).
#
#   {"list": [{"id": "S1", "type": "soil",   "name": "Sensor A", "pin": 34, "dry": 3390, "wet": 1669},
#             {"id": "S3", "type": "analog", "name": "Light", "pin": 32, "unit": "%",
#              "lo_mv": 0, "lo": 0, "hi_mv": 3000, "hi": 100, "dec": 1}]}
#
# Adding a sensor TYPE later = an entry in TYPES below (name, pin kind, form fields) + a driver
# module drv_<type>.py with a class Driver(entry): chans() -> [(key, label, unit, decimals)],
# read() -> [value or None per channel], and a short 'info' text. Nothing else in the app changes:
# every channel automatically gets a dashboard card, an MQTT field and a CSV column.
# 'soil' is built into app.Sensors (bands, pump and LEDs work with soil sensors).
import json
import os
import settings as S

CFG = S.DATA_DIR + '/sensors.json'
ADC_PINS = (32, 33, 34, 35, 36, 39)            # ADC1: works with WiFi on (ADC2 does not)
IO_PINS = (4, 13, 14, 16, 17, 18, 19, 21, 22, 23, 25, 26, 27, 32, 33)   # free of boot straps / flash

# type -> (label, pin kind, form fields [(key, label, 'num'|'text', default)])
TYPES = {
    'soil': ('Soil moisture (capacitive)', 'adc',
             [('dry', 'Dry raw (in air)', 'num', 3400), ('wet', 'Wet raw (in water)', 'num', 1650)]),
    'analog': ('Analog sensor (linear scale)', 'adc',
               [('unit', 'Unit', 'text', ''), ('lo_mv', 'Input low, mV', 'num', 0), ('lo', '= value', 'num', 0),
                ('hi_mv', 'Input high, mV', 'num', 3000), ('hi', '= value', 'num', 100), ('dec', 'Decimals', 'num', 1)]),
}


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


def save(lst):
    tmp = CFG + '.tmp'
    with open(tmp, 'w') as f:
        json.dump({'list': lst}, f)
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
    for p in S.SENSOR_PWR_PINS:
        t[p] = 'sensor power'
    for k, lbl in (('PUMP_PIN', 'pump'), ('TANK_PIN', 'tank switch'), ('WAKE_TOUCH', 'touch wake'),
                   ('WAKE_BUTTON', 'wake button')):
        p = getattr(S, k, None)
        if p is not None:
            t[p] = lbl
    try:
        with open(S.DATA_DIR + '/batt.json') as f:
            b = json.load(f)
        if b.get('on'):
            t[b['pin']] = 'battery'
    except (OSError, ValueError, KeyError):
        pass
    for e in lst:
        if e['id'] != skip_id and 'pin' in e:
            t[e['pin']] = e['name']
    return t


def free_pins(kind, lst, skip_id=None):
    t = taken_pins(lst, skip_id)
    return [p for p in (ADC_PINS if kind == 'adc' else IO_PINS) if p not in t]
