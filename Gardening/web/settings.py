# Settings for the garden moisture logger web app (ESP32 WROOM, MicroPython 1.29)

# Soil sensors TEMPLATE: (gpio, dry_raw, wet_raw, name). Used only to create the sensor list the first
# time; after that sensors are added / calibrated / removed from the dashboard Sensors panel
# (/data/sensors.json). Raw = 12-bit ADC average, 11 dB attenuation.
# Calibrated 2026-11-01: dry = room air 24.0 C / 43% RH, wet = water to the edge line.
SENSORS = (
    (34, 3390, 1669, 'Sensor A'),   # dry / wet calibrated 2026-11-02 on board 7c:9e:bd:48:2c:c0
    (35, 3443, 1633, 'Sensor B'),
)
ADC_SAMPLES = 16          # ADC reads averaged per sample
SAMPLE_S = 2              # seconds between live samples

# AHT10 air temperature / humidity (I2C). 30-pin DevKit: sensors D34/D35 on the left header,
# AHT10 on the ESP32 default I2C pins SCL=D22 / SDA=D21 (right header). Avoid D2: a pull-up there blocks flashing.
I2C_SCL = 22
I2C_SDA = 21
I2C_TIMEOUT_US = 10000    # per transfer; keeps a loose wire from stalling the app
AIR_RETRY_S = (30, 300)   # re-detect a lost air sensor after 30 s, doubling up to 5 min

# Time
TZ_OFFSET_H = 4           # Asia/Dubai
TZ_LABEL = '+04:00'
NTP_HOST = 'pool.ntp.org'
NTP_EVERY_S = 6 * 3600

# Network. Station credentials live in wifi.json on the board (set from the web page).
WIFI_COUNTRY = 'AE'      # UAE: channels 1-13 (default 'XX' only scans 1-11; vGuest is on ch 13)
HOSTNAME = 'garden'       # reachable as http://garden.local where mDNS works
AP_SSID = 'garden'       # fallback access point when WiFi is not configured / unreachable
AP_PASSWORD = 'garden1234'
HTTP_PORT = 80
MAX_CONN = 4              # concurrent HTTP connections; extra ones get 503 (heap guard)
MAX_BODY = 1024           # max POST body bytes

# Recording
DATA_DIR = '/data'
INTERVALS = (10, 30, 60, 300, 600, 900)   # allowed record intervals, seconds
MAX_HOURS = 720
MIN_FREE_FLASH = 96 * 1024                # stop recording below this much free flash
MAX_FILE_BYTES = 1024 * 1024              # stop recording when one CSV reaches this size

# Watering bands (defaults; the dashboard saves changes to /data/bands.json)
BANDS_FULL = (82, 88)     # soil "full" reading F per sensor, % (night plateau after watering, 10-11 Nov 2026)
BANDS_STAGE = 3           # 0 germination, 1 seedling, 2 vine growth, 3 flowering & fruiting, 4 late harvest
DRY_RATE_PER_DAY = 6.0    # fallback drying rate, points/day, until ~20 h (40 half-hour slots) of own trend data exist
WATER_JUMP = 5            # rise of >= this many points within ~10 min = watering
DRAIN_H = 6               # above F+5 within this many hours after watering = "draining", not "too wet"

# Indicator LEDs for one sensor (leds.py). () = no LEDs, module not loaded.
LED_PINS = (25, 26, 27, 14)   # fully watered, normal, water soon, water now (GPIO -> resistor -> LED -> GND)
LED_ACTIVE_HIGH = True
LED_SENSOR = 0                # which soil sensor the LEDs show: 0 = the first soil sensor in the list
LED_MODE = 'on'               # default 'off' | 'on' | 'eco'; the dashboard saves changes to /data/leds.json

# Water pump (pump.py). None = no pump, module not loaded. Mode, run times, schedule and the
# auto settings are set from the dashboard (saved to /data/pump.json); these are hardware limits.
PUMP_PIN = 23                 # GPIO -> relay module IN / MOSFET gate (23: free, not a boot strapping pin)
PUMP_ACTIVE_HIGH = True       # True: HIGH = pump on (MOSFET, "high level trigger" relay); False for low-trigger relays
PUMP_MAX_S = 120              # longest single run (manual, schedule or one auto pulse), seconds
PUMP_DAY_MAX_S = 600          # all runs together per day; protects against flooding if a sensor fails
PUMP_RISE = 2.0               # moisture rise (points) that proves the water arrived; less = dry-run alarm
PUMP_CHECK_MIN_S = 10         # runs shorter than this are not judged (too little water to show up)
TANK_PIN = None               # optional tank float switch to GND, e.g. 19 (internal pull-up); None = none
TANK_EMPTY = 0                # pin level that means "tank empty"

# MQTT (mqtt.py) - template defaults only. Whatever is saved from the dashboard MQTT panel
# (/data/mqtt.json on the board) overrides these. Leave MQTT_HOST blank and set it from the page.
MQTT_ENABLED = False          # default for "Send data to MQTT"
MQTT_HOST = ''                # broker IP or domain name, e.g. '192.168.1.10' or 'broker.lan'
MQTT_PORT = 1883
MQTT_USER = ''
MQTT_PASSWORD = ''
MQTT_CLIENT_ID = ''           # '' = garden-<last 3 bytes of the chip id>
MQTT_TOPIC = 'garden'         # topic prefix: <prefix>/state, /pump/event, /status
MQTT_EVERY_S = 60             # publish interval (average of the samples since the previous publish)
MQTT_RETAIN = True
MQTT_FIELDS = ()              # what to send: () = everything; else e.g. ('s0', 'batt') - 's0','s1' soil
                              # sensors, 'air', 'pump' (state + events), 'batt', 'board' (WiFi, heap, uptime)

# Battery / supply monitor (battery.py) - template defaults only; the dashboard Battery panel
# (/data/batt.json) overrides them: on/off, pin, input 5 V / 12 V / custom, R1/R2, alarms, calibration.
BATT_PIN = None               # None = off by default; ADC1 pin with the divider: 32, 33, 36 (VP) or 39 (VN)
BATT_PRESET = '5'             # default input: '5' V supply (100k/68k); '12' V battery (100k/15k); 'custom'
BATT_CAL = 1.0                # multimeter voltage / shown voltage (set by "Calibrate" on the page)
BATT_LOW_V = 4.6              # alarms for the default preset; 12 V lead-acid: 11.8 / 11.4
BATT_CRIT_V = 4.4
BATT_EVERY_S = 30             # read interval

# Power management (power.py). False = module not loaded: always on, 160 MHz, as before.
# Mode, CPU MHz, sensor power, wake interval and awake windows are set from the dashboard
# (saved to /data/power.json); these are the hardware facts and limits.
POWER_ENABLED = True
SENSOR_PWR_PINS = ()          # GPIOs feeding the moisture sensors' VCC, e.g. (32, 33); () = sensors on 3V3
SENSOR_SETTLE_MS = 400        # sensor warm-up after power-on before reading
WAKE_BUTTON = 0               # GPIO that wakes from deep sleep when pulled LOW (0 = BOOT button); None = off
WAKE_TOUCH = None             # touch-capable GPIO (e.g. 13 or 32) to wake by touching a wire/pad; None = off
MAX_AWAKE_MIN = 60            # page use keeps it awake at most this long after a wake (then sleeps)
MAINT_MAX_H = 2               # "stay awake (maintenance)" button duration
CYCLE_SAMPLES = 8             # readings averaged per sleep-cycle wake (~2 s)
