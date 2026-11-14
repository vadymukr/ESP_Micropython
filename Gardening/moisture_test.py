# Basic soil moisture sensor test - ESP32 WROOM, two sensors on GPIO34 and GPIO35
from machine import ADC, Pin, I2C
import time

SENSOR_PINS = (34, 35)
SAMPLES = 10  # average a few reads to smooth ADC noise

# Calibration (raw ADC counts): (dry in air, wet in water up to the edge line)
# New board (MAC 7c:9e:bd:48:2c:c0):
# Dry: 4.5-min average (225 readings) in room air at 24.6 C / 47% RH / 1014.7 hPa (Si7020 + BMP280), 2026-11-02
# Wet: last-60s average in water to the edge line at 24.3 C / 49% RH, 2026-11-02
#      logs/moisture_dry_newboard_20261102_2124.csv, logs/moisture_wet_newboard_20261102_2128.csv
CAL = {34: (3390, 1669), 35: (3443, 1633)}

def make_adc(pin):
    adc = ADC(Pin(pin))
    adc.atten(ADC.ATTN_11DB)     # full ~0-3.3 V input range
    adc.width(ADC.WIDTH_12BIT)   # raw values 0-4095
    return adc

sensors = [(pin, make_adc(pin)) for pin in SENSOR_PINS]

# Air sensor (AHT10 / Si70xx / BMP280, auto-detected), same pins as web/settings.py (SCL=GPIO22, SDA=GPIO21)
air = None
try:
    from climate import detect
    air = detect(I2C(0, scl=Pin(22), sda=Pin(21), freq=100000))
    print("Air sensor:", air.name)
except Exception as e:
    print("Air sensor not available:", e)

def read_avg(adc):
    total = 0
    for _ in range(SAMPLES):
        total += adc.read()
        time.sleep_ms(5)
    return total // SAMPLES

def to_mv(adc, raw):
    try:
        return adc.read_uv() // 1000   # calibrated mV (MicroPython 1.19+)
    except AttributeError:
        return raw * 3300 // 4095      # rough fallback

def moisture_pct(pin, raw):
    dry, wet = CAL[pin]
    return max(0, min(100, (dry - raw) * 100 // (dry - wet)))

print("Soil moisture test on GPIO34 + GPIO35 - Ctrl+C to stop")
while True:
    parts = []
    for pin, adc in sensors:
        raw = read_avg(adc)
        parts.append("GPIO{}: {:3d}% (raw {:4d} / {:4d} mV)".format(
            pin, moisture_pct(pin, raw), raw, to_mv(adc, raw)))
    if air:
        try:
            t, rh, hpa = air.read()
            s = "air {:.1f} C".format(t)
            if rh is not None:
                s += " / {:.0f}% RH".format(rh)
            if hpa is not None:
                s += " / {:.1f} hPa".format(hpa)
            parts.append(s)
        except OSError as e:
            parts.append("air sensor error: {}".format(e))
    print("   |   ".join(parts))
    time.sleep(1)
