# ESP32 garden logger (MicroPython)

A soil-moisture logger and watering controller for the ESP32, written in MicroPython. It reads
capacitive soil sensors, tells you when to water, runs a pump with dry-run protection, and shows
everything on a web dashboard served by the board itself. Optional parts: MQTT for Node-RED, a
GSM modem for SMS, and OLED or TFT screens. Each optional part can be switched off so it takes no RAM.

Everything lives in [`Gardening/`](Gardening/).

## Features

- **Soil sensors with watering bands.** Each sensor reads 0–100 % from its own dry/wet
  calibration and shows a band: *drought, water now, water soon, good, draining, too wet*. The
  bands follow your "full" reading (F) and learn the drying rate to estimate the next watering.
- **Web dashboard.** Live cards every 2 s, settings panels, CSV recording and download. It's built
  with htmx and Alpine.js, gzipped and served from flash. It works offline, and a setup hotspot
  appears when no WiFi is configured.
- **Watering pump.** One GPIO drives a relay or MOSFET. Modes: manual, schedule, or automatic in
  short pulses with soak time, all with daily limits. The board stops and locks the pump if
  moisture doesn't rise (empty tank). An optional flow sensor does the same check and counts litres.
- **Sensor registry.** Add and remove sensors from the dashboard, without changing code. Each one
  gets a card, a CSV column and an MQTT field. Supported:
  - analog: soil, generic linear, MQ-2/4/6/7/9/135 gas, ACS758 current, GUVA-S12SD UV;
  - temperature: NTC thermistor (Beta formula), MAX6675 K-type thermocouple, DS18B20, DHT11/22,
    each with offset/gain calibration and an "offset = reference now" button;
  - digital: switches, YF-S201/YF-S401 flow meters;
  - UART: MH-Z19 CO2, PMS5003/7003 and SDS011 dust;
  - I2C: AHT10/20, SHT3x, BME280/BMP280, BH1750, CCS811, VL53L0X, VL53L1X, MPU-6050, TCS34725,
    ADS1115, INA219.
- **Air sensor.** Si7020/Si7021 (0x40), AHT10/20 (0x38) and BMP280/BME280 (0x76/0x77) are
  detected on I2C automatically, also as combined modules. It can be removed in the Sensors panel.
- **Status lights.** A 4-LED band indicator, or an RGB LED in the band colour.
- **Battery / supply monitor.** It reads the supply voltage through a resistor divider. The panel
  has presets for 5 V and 12 V supplies and suggests resistor values for a custom voltage.
- **Power saving.** Lower CPU clock, or a deep-sleep cycle that wakes, measures, logs and sleeps.
- **MQTT.** Sends the average since the last message to `<base>/state` (JSON), pump events to
  `<base>/pump/event`, and `online`/`offline` to `<base>/status`. Checkboxes choose which values
  are sent. A [Node-RED Dashboard 2.0 flow](Gardening/node-red/) is included.
- **GSM (SIM800L, e.g. TTGO T-Call V1.4).**
  - SMS alerts: pump locked, soil too dry, battery low.
  - SMS commands from allowed numbers only, e.g. `STATUS`, `PUMP 20`, `STOP`, `LED OFF`, plus your
    own keywords, with an optional secret code.
  - A call from an allowed number can run the pump or ask for a status SMS.
  - MQTT over GPRS when WiFi is down.

  The SIM800L is 2G only.
- **Heater with PID control.** One GPIO drives a MOSFET (PWM, 1 Hz–20 kHz) or an SSR/relay (slow
  time-proportional window). An on/off thermostat mode with hysteresis and a minimum switching time
  covers heat lamps and relays. It follows any temperature sensor: the air sensor, DS18B20, DHT and
  others. The setpoint, Kp/Ki/Kd, output type and limits are set in the Heater panel, with defaults
  in `settings.py`. It switches off when the sensor stops reading or a cut-off temperature is reached.
  Values go to MQTT, and `HEAT 24` / `HEAT OFF` work by SMS.
- **Displays.**
  - OLED over I2C: SSD1306 128×64 and 128×32, SH1106.
  - Colour TFT over SPI: ST7789, ST7735 and ILI9341.
  - Pages with big digits sized to the screen (up to 120 px), band colours and night-off hours.
  - Fonts are read from flash a character at a time, so they cost almost no RAM.
- **Feature switches.** The *Features* panel, or `FEATURES` in `settings.py`, turns parts off.
  A switched-off part is never imported and its panel is hidden.

## Hardware

| Board | `BOARD` | Notes |
|---|---|---|
| ESP32 DevKit (WROOM) | `devkit` | default; what the project is developed on |
| TTGO T-Call V1.4 | `ttgo-tcall` | ESP32-WROVER with PSRAM and SIM800L; pump moves to GPIO18 |
| ESP32-2432S028 "Cheap Yellow Display" | `cyd` | built-in 2.8" touch screen, RGB LED; one soil sensor, I2C on CN1 |
| ESP32-S3 / C3 / C6 | detected | own pin sets in `settings.py`; recalibrate the soil sensors |

Default wiring on the `devkit` board:

| What | GPIO |
|---|---|
| Soil sensors (capacitive v1.2/v2.0) | 34, 35 (ADC1) |
| I2C (air sensor, OLED) | SDA 21, SCL 22 |
| Pump relay / MOSFET | 23 |
| 4-LED indicator | 25, 26, 27, 14 |
| BOOT button | 0: hold at power-on to skip the app (REPL only) |

The pins offered in the dashboard come from the chip and board profile, and pins already in use
are never offered.

## Getting started

1. Make a Python environment at the repository root. `deploy.sh` expects it in `.venv`:
   ```sh
   python3 -m venv .venv
   .venv/bin/pip install esptool mpremote "mpy-cross==1.29.*"
   .venv/bin/pip install --target .venv/mpy-cross-1.20 "mpy-cross==1.20.0"   # for 1.20 boards
   ```
2. Optional: copy `Gardening/web/wifi.json.example` to `wifi.json` and enter your network.
   `wifi.json` is git-ignored. Without it, the board opens the setup hotspot **`garden`**
   (password `garden1234`, change it in `settings.py`) with a captive page to pick a network.
3. Flash and upload:
   ```sh
   cd Gardening/web
   ./deploy.sh /dev/ttyUSB0                  # upload the app; offers to flash a board without MicroPython
   ./deploy.sh --flash /dev/ttyUSB0          # detect the chip and install the matching MicroPython first
   ./deploy.sh --board cyd /dev/ttyUSB0      # pick a board profile (kept on later uploads)
   ```
   With `--flash`, the script reads the chip type, PSRAM and flash size and looks up the matching
   firmware on micropython.org. It backs up and verifies the old flash before erasing anything.
   Python files are compiled to `.mpy` for the board's MicroPython version.
4. Open `http://garden.local`, or the IP the board prints, and calibrate each soil sensor in the
   **Sensors** panel: *Dry = now* in air, *Wet = now* in water.

Set `FW_VERSION=1.20.0` at the top of `deploy.sh` for classic ESP32 boards. Newer MicroPython
versions use ESP-IDF 5, which can't join some mixed WPA2/WPA3 networks.

## RAM

A classic ESP32 without PSRAM has about 100 KB for the whole app. With everything on, 20–30 KB stays
free, and these features are switchable:

| Feature | RAM while on |
|---|---|
| Watering pump | ~8 KB |
| MQTT | ~9 KB |
| GSM modem | ~16 KB |
| Displays | ~16 KB |
| Heater (PID) | ~6 KB |
| Power saving | ~6 KB |
| CSV recording | ~5 KB |

To get more room:
- switch features off in the *Features* panel;
- build the custom firmware with the modules frozen into flash (`Gardening/firmware/build.sh`,
  needs Docker), which gives about 35 KB more;
- or use a board with PSRAM, such as an ESP32-S3 N8R8 or a WROVER.

## Repository layout

```
Gardening/
  web/                 app.py (web server, dashboard), settings.py, deploy.sh, ui_*.py panels,
                       www/ (dashboard HTML/JS), fonts/ (display fonts),
                       sensor_types.txt (sensor types: add a line + a drv_*.py to support a new sensor)
  *.py                 modules: pump, mqtt, gsm, bands, sensors + drv_* drivers, display, power ...
  node-red/            Node-RED Dashboard 2.0 flow (make_flow.py builds the JSON)
  firmware/            custom MicroPython build with the app frozen in
  tools/make_fonts.py  rebuilds web/fonts/*.bin (needs Pillow)
  logs/, soil_moisture_sensor_data_csv/   recorded sample data
```

## Plans

- LVGL screens on a PSRAM board (custom firmware). The page content is already kept apart from
  the drawing code for this.
- Optional MQTT over TLS when RAM allows.
