# Sensor registry for the garden logger (MicroPython). The sensors on the board are a list in
# DATA_DIR/sensors.json, edited from the dashboard Sensors panel; settings.SENSORS is only the
# template used to create the list the first time (migration of the old fixed soil sensors).
#
#   {"list": [{"id": "S1", "type": "soil",   "name": "Sensor A", "pin": 34, "dry": 3390, "wet": 1669},
#             {"id": "S3", "type": "analog", "name": "Light", "pin": 32, "unit": "%",
#              "lo_mv": 0, "lo": 0, "hi_mv": 3000, "hi": 100, "dec": 1}]}
#
# Adding a sensor TYPE later = an entry in TYPES below (name, pin kind, form fields) + a driver
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

# type -> (label, pin kind, form fields [(key, label, 'num'|'text', default)], driver, calibrate button)
#   pin kinds: 'adc' = ADC1 pin, 'io' = free GPIO, 'i2c' = shared I2C bus (address field),
#              'uart' = two free GPIOs (pin = RX from the sensor's TX, pin2 = TX to the sensor's RX)
#   driver: module drv_<driver>.py (None = drv_<type>.py); calibrate: button label, the driver's
#   cal() returns (key, value) to store, e.g. MQ 'R0 = now (clean air)'.
# Soil defaults = the 2026-11-02 calibration of this probe model (average of the two probes:
# A 3390/1669, B 3443/1633); use Dry/Wet = now in the panel for each new probe.
SOIL_DRY, SOIL_WET = 3416, 1651
_I2C = lambda a: [('addr', 'Address hex', 'text', a)]
_MQ = [('rl', 'Load RL, k&Omega;', 'num', 10), ('div', 'Divider ratio', 'num', 2.22), ('vc', 'Supply, V', 'num', 5.0),
       ('r0', 'R0, k&Omega;', 'num', 10)]
_R0 = 'R0 = now (clean air)'
TYPES = {
    'soil': ('Soil moisture (capacitive)', 'adc',
             [('dry', 'Dry raw (in air)', 'num', SOIL_DRY), ('wet', 'Wet raw (in water)', 'num', SOIL_WET)], None, None),
    'analog': ('Analog sensor (linear scale)', 'adc',
               [('unit', 'Unit', 'text', ''), ('lo_mv', 'Input low, mV', 'num', 0), ('lo', '= value', 'num', 0),
                ('hi_mv', 'Input high, mV', 'num', 3000), ('hi', '= value', 'num', 100), ('dec', 'Decimals', 'num', 1)],
               None, None),
    'mq2': ('MQ-2 gas: LPG / smoke (analog)', 'adc', _MQ, 'mq', _R0),
    'mq4': ('MQ-4 gas: methane (analog)', 'adc', _MQ, 'mq', _R0),
    'mq6': ('MQ-6 gas: LPG / butane (analog)', 'adc', _MQ, 'mq', _R0),
    'mq7': ('MQ-7 gas: carbon monoxide (analog)', 'adc', _MQ, 'mq', _R0),
    'mq9': ('MQ-9 gas: CO / flammable (analog)', 'adc', _MQ, 'mq', _R0),
    'mq135': ('MQ-135 air quality / CO2 (analog)', 'adc', _MQ, 'mq', _R0),
    'acs758': ('ACS758 Hall current sensor (analog)', 'adc',
               [('mv_a', 'mV per A', 'num', 40), ('zero_mv', 'Zero, mV', 'num', 2500), ('div', 'Divider ratio', 'num', 2.22)],
               None, 'Zero = now (no current)'),
    'uv': ('UV sensor GUVA-S12SD (analog)', 'adc', [], None, None),
    'ds18b20': ('DS18B20 temperature, 1-Wire (several per pin)', 'io', [], None, None),
    'dht': ('DHT11 / DHT22 temperature + humidity', 'io', [('model', 'Model (11 or 22)', 'num', 22)], None, None),
    'digital': ('Digital input (float / rain / door switch)', 'io',
                [('pull', 'Pull-up (1/0)', 'num', 1), ('invert', 'Invert (1/0)', 'num', 0), ('unit', 'Label', 'text', '')],
                None, None),
    'flow': ('Flow meter YF-S201 / other, pulses', 'io', [('ppl', 'Pulses per litre', 'num', 450)], None, None),
    'yfs401': ('Flow meter YF-S401, 0.3-6 L/min', 'io', [('ppl', 'Pulses per litre', 'num', 5880)], 'flow', None),
    'mhz19': ('MH-Z19 CO2, NDIR (UART)', 'uart', [], None, None),
    'pms': ('PM2.5 laser dust sensor: PMS5003/7003, SDS011 (UART)', 'uart', [], None, None),
    'aht': ('AHT10 / AHT20 temperature + humidity (I2C)', 'i2c', _I2C('38'), None, None),
    'sht3x': ('SHT30 / SHT31 temperature + humidity (I2C)', 'i2c', _I2C('44'), None, None),
    'bme280': ('BME280 / BMP280 temp, humidity, pressure (I2C)', 'i2c', _I2C('76'), None, None),
    'bh1750': ('BH1750 light, lux (I2C)', 'i2c', _I2C('23'), None, None),
    'ccs811': ('CCS811 eCO2 / TVOC air quality (I2C)', 'i2c', _I2C('5a'), None, None),
    'vl53l0x': ('VL53L0X distance, GY-530 / GY-VL53L0XV2 (I2C)', 'i2c', _I2C('29'), None, None),
    'vl53l1x': ('VL53L1X distance up to 4 m (I2C)', 'i2c', _I2C('29'), None, None),
    'mpu6050': ('MPU-6050 tilt / motion, GY-521 (I2C)', 'i2c', _I2C('68'), None, None),
    'tcs34725': ('TCS34725 RGB colour sensor (I2C)', 'i2c', _I2C('29'), None, None),
    'ads1115': ('ADS1115 4-channel ADC, mV (I2C)', 'i2c', _I2C('48'), None, None),
    'ina219': ('INA219 voltage / current (I2C)', 'i2c', _I2C('41') + [('shunt', 'Shunt, ohm', 'num', 0.1)], None, None),
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
    for p, lbl in getattr(S, 'BOARD_PINS', {}).items():       # wired on the board (screen, touch...)
        t[p] = lbl
    for p in S.SENSOR_PWR_PINS:
        t[p] = 'sensor power'
    for k, lbl in (('PUMP_PIN', 'pump'), ('TANK_PIN', 'tank switch'), ('WAKE_TOUCH', 'touch wake'),
                   ('WAKE_BUTTON', 'wake button'), ('MODEM_TX', 'modem TX'), ('MODEM_RX', 'modem RX'),
                   ('MODEM_PWKEY', 'modem PWKEY'), ('MODEM_RST', 'modem RST'), ('MODEM_POWER', 'modem power')):
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
    try:
        with open(S.DATA_DIR + '/rgb.json') as f:
            r = json.load(f)
        if r.get('on'):
            for p, c in zip(r['pins'], 'RGB'):
                t[p] = 'RGB light ' + c
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
            for k in ('pin', 'pin2'):
                if k in e:
                    t[e[k]] = e['name']
    return t


def free_pins(kind, lst, skip_id=None):
    if kind == 'i2c':
        return []
    t = taken_pins(lst, skip_id)
    return [p for p in (ADC_PINS if kind == 'adc' else IO_PINS) if p not in t]      # 'io' and 'uart'


def i2c_taken(lst, air, skip_id=None):
    """I2C addresses in use: the auto-detected air sensor (air = list of addresses) and other sensors."""
    t = {}
    for a in air:
        t[a] = 'air sensor'
    for e in lst:
        if e['id'] != skip_id and 'addr' in e and TYPES.get(e['type'], ('', ''))[1] == 'i2c':
            t[int(e['addr'], 16)] = e['name']
    return t
