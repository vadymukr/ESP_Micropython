# Garden logger – specification

What the ESP32 garden logger does and how the code is organised. Every source module starts with a
`# Spec: docs/SPEC.md#<section>` line pointing to its section here. Change this document when
behaviour changes.

## Contents
[Architecture](#architecture) · [Features](#features) · [RAM rules](#ram-rules) ·
[Sensors](#sensors) · [Driver interface](#driver-interface) · [Watering bands](#watering-bands) ·
[Pump](#pump) · [Dashboard](#dashboard) · [HTTP routes](#http-routes) · [WiFi](#wifi) ·
[MQTT](#mqtt) · [GSM](#gsm) · [Displays](#displays) · [Controllers](#controllers) ·
[Status lights](#status-lights) · [Battery monitor](#battery-monitor) ·
[Power saving](#power-saving) · [Recording](#recording) · [Data files](#data-files) ·
[Deploy](#deploy) · [Firmware](#firmware) · [Coding standards](#coding-standards)

## Architecture
MicroPython 1.20+ on an ESP32 (classic, S3, C3, C6). One asyncio event loop runs everything.

| Module (web/) | Role |
|---|---|
| `main.py` | Entry point: quick sleep-cycle wake (`cycle.py`) or the full app (`app.main()`) |
| `app.py` | Shared state (`app.sens`, `app.pump`, `app.mqtt`, ...), sampling loop, start-up; re-exports helpers |
| `util.py` | Time, text, HTML escaping, query parsing, heap / flash, feature switches |
| `sampler.py` | `Sensors`: reads every sensor every `SAMPLE_S` (2 s) |
| `wifi.py` | WiFi manager, setup access point, captive DNS, NTP |
| `pages.py` | Dashboard fragments `/live` and `/status` |
| `server.py` | HTTP parsing with limits, routing, static files |
| `feats.py` | Create / reconfigure / unload optional parts |
| `ui_*.py` | Settings panels, imported per request and released |
| `settings.py` | Board wiring and defaults (template; panels save their own JSON in `/data`) |

Modules next to `web/` (Gardening/) hold the features: `bands`, `pump*`, `mqtt*`, `gsm*`,
`display*`, `control*`, `rgb`, `leds`, `battery`, `power`, `recorder`, `climate`, `aht10`,
`sensors`, `sensor_types` and the sensor drivers `drv_*.py`.

**Rules:** only the sampler task touches sensor hardware; HTTP handlers read its values. The shared
state lives in `app`; other modules read it as `app.<name>` at call time (never `from app import x`
at import time, except per-request panels). Imports between `app` and its helper modules are
circular by design and safe because no module uses `app` attributes while it is being imported.

## Features
Optional parts, switched in the **Features** panel (`/data/features.json`) or by `FEATURES` in
`settings.py`: `pump`, `mqtt`, `gsm`, `battery`, `leds`, `rgb`, `power`, `recorder`, `display`,
`control` (`heater` is accepted as the old name of `control`). A feature that is off is never
imported and its panel is hidden (the dashboard gets the off list from `/status`). Changing the list
restarts the board. Inside a feature, a part that is not configured is not loaded either (e.g. no
display set up, pump disabled in its panel). Soil sensors, the air sensor and the sensor registry are
always on.

## RAM rules
A classic ESP32 without PSRAM has about 100 KB for the app; with everything on 20–30 KB stays free.
- Optional modules are imported only when used and removed from `sys.modules` when switched off.
- Settings panels (`ui_*.py`) are imported for one request and released (`server.panel`); helper
  modules they import only for their settings are released too (`server.HELPERS`).
- Each feature keeps its settings code in a small `*_cfg.py` so panels need not load the runtime.
- Big tables live in flash files, read line by line (`sensor_types.txt`, display fonts).
- HTML is built as a list of small parts and streamed (`server.send`); no big joined strings.
- Work that needs the panel module released first runs 0.3 s after the reply (`feats._soon`).
- The low-water mark is shown on the dashboard (System: Heap ... lowest).
Docstrings and comments cost nothing on the board (MicroPython does not keep them).

## Sensors
`sampler.Sensors` (one instance, `app.sens`):
- **Soil sensors** (capacitive, ADC1 pins): `.ch` = (pin, ADC, dry, wet, name, id), `.raw`, moisture
  % = (dry − raw) / (dry − wet), clamped 0–100. Calibrate with Dry / Wet = now.
- **Registry sensors** (`/data/sensors.json`, Sensors panel): drivers `drv_<type>.py`, channels in
  `.gch` = (driver index, channel index, key, label, unit, decimals, sensor id), values in `.gval`.
- **Air sensor**: auto-detected on I2C (Si70xx 0x40, AHT 0x38, BMP/BME280 0x76/0x77), `.t .rh .p`;
  removable in the Sensors panel; lost sensors are re-detected with back-off (`AIR_RETRY_S`).
- `.acc_*` sum readings for averages (CSV rows, MQTT messages).
Sensor types are listed in `web/sensor_types.txt`: one JSON line per type
`[type, label, pin kind, driver, calibrate button, form fields]`; pin kinds `adc`, `io`, `i2c`,
`uart` (RX + TX), `spi` (SO + SCK + CS). Adding a type = a line there + a driver.

## Driver interface
`class Driver(entry, ctx)` in `drv_<type>.py` (or the shared driver named in the type line):
- `chans()` → `[(key, label, unit, decimals)]`
- `read()` → one value per channel, `None` = no valid reading
- `info()` → short status text for the card
- `cal()` (optional) → `(key, value)` stored in the entry by the calibrate button
`ctx` = `app.sens`: `ctx.i2c` (shared bus), `ctx.uart_free` (free UART ids), `ctx.t` / `ctx.rh`.
Temperature drivers (NTC, MAX6675) calibrate by gain / offset and "Offset = reference now".

## Watering bands
`bands.Bands`: per soil sensor a "full" reading F and a growth-stage band width w. Bands (v = moisture
%): **too wet** v ≥ F+5, or **draining** if watered within `DRAIN_H` hours; **good** v ≥ F−w;
**water soon** v ≥ F−w−10; **water now** v ≥ F−w−20; **drought** below. A watering is detected as a rise ≥ `WATER_JUMP` within ~10 min. The drying
rate is a least-squares slope over 24 h of half-hour averages (`trend.json`), used for "next
watering". Settings: Watering bands panel (`bands.json`).

## Pump
`pump.Pump` (+ `pump_check.Checks`, settings `pump_cfg`): one GPIO, every run is a short pulse that
switches off by itself (`PUMP_MAX_S`, daily `PUMP_DAY_MAX_S`). Modes: manual, schedule (local
times), auto (pulse–soak–check until near F, at most `max_pulses`). **Dry-run protection:** no
moisture rise ≥ `PUMP_RISE` within the soak time after a run ≥ `PUMP_CHECK_MIN_S` (or no flow from
a linked flow sensor) locks schedule and auto until Reset. Optional float switch (`TANK_PIN`).
Water statistics from a flow sensor or a set pump rate. The pump can be disabled in its panel (not
loaded, GPIO freed). Log: `pump.log`.

## Dashboard
`www/index.html` (htmx + Alpine.js, gzipped on deploy). `/live` every 2 s: alert line, soil cards,
registry sensor cards, air, battery, pump, GSM and controller cards. `/status` every 5 s: Alpine sync
tag (recording, clock, flash, MQTT state, hidden panels), recording lines, System facts. Settings
panels open on demand (`hx-trigger="toggle once"`).

## HTTP routes
GET `/` (dashboard, `/setup` while no WiFi), `/setup`, `/live`, `/status`, `/tail`, `/files`,
`/dl?f=<csv>`, `/wifi/scan`, `/wifi/state`, panels `/bands /leds /power /pump /mqtt /batt /sensors
/rgb /gsm /display /control /features` (+ sub-paths such as `/sensors/form`, `/control/form`).
POST `/rec/start`, `/rec/stop`, `/files/delete`, `/time` (browser clock), `/wifi/connect`,
`/wifi/forget` and the panel saves (`/<panel>/save`, ...). Limits: `MAX_CONN` connections (503
"Busy" beyond), request line 512 B, 40 headers of 1 KB, body `MAX_BODY`; a `MemoryError` answers
503 "Low memory, retry". While the setup AP is up, unknown paths redirect to `/setup` (captive portal).

## WiFi
`wifi.Net`: joins the network in `wifi.json`; without one, or 30 s after losing it, opens the setup
access point `AP_SSID` with captive DNS; retries the saved network every 2 min while nobody uses the
AP. NTP every `NTP_EVERY_S`; the browser sets the clock if NTP has not. mDNS name `HOSTNAME.local`.

## MQTT
`mqtt.Mqtt` (settings `mqtt_cfg`, message `mqtt_snapshot`): minimal non-blocking MQTT 3.1.1, QoS 0.
Topics: `<base>/status` (online / offline, retained, last will), `<base>/state` (JSON, every
interval and after pump events; averages since the previous message), `<base>/pump/event`.
State fields: `soil`, `sensors`, `air`, `pump`, `battery`, `control`, board facts — selectable in the
panel. Over GPRS when the GSM panel allows it. Node-RED flow: `node-red/`.

## GSM
SIM800L (TTGO T-Call or separate module), 2G only. `gsm.Gsm` = AT engine and start-up, mixed with
`gsm_sms.Sms` (SMS, calls, alerts), `gsm_gprs.Gprs` (TCP for MQTT), `gsm_view.View` (card, log);
commands and alerts in `gsm_cmd` (loaded on first use); settings `gsm_cfg`.
- Only allowed numbers (last 9 digits) act; optional SMS code; own keywords `WORDS=COMMAND`.
- Commands: `STATUS`, `PUMP [s]`, `STOP`, `RESET`, `MODE MANUAL|SCHEDULE|AUTO`, `WATER`,
  `LED/RGB ON|OFF|ECO`, `HEAT|LIGHT|FAN [value|ON|OFF]`, `CTRL <name> [value|ON|OFF]`, `INFO`.
- Calls are hung up, then: status SMS, pump run or toggle. Unknown callers are hung up.
- Alerts on changes only (pump lock, soil now / drought, battery), same alert at most every 3 h,
  daily SMS limit.

## Displays
`display.Display` manages up to two screens (one SPI TFT): OLED SSD1306 / SH1106 (I2C), TFT ST7789,
ST7735, ILI9341 (SPI). `display_pages` builds pages as data (`t`, `v`, `u`, `b`, `l` / `rows`),
`display_render` lays them out (rows, 128×32, normal page with the biggest digits that fit),
`disp_oled` / `disp_tft` are the drivers, `gfont` reads the bitmap fonts (`web/fonts/*.bin`, built by
`tools/make_fonts.py`) from flash. Night-off hours, touch IRQ to change page, board profile `cyd`
(ESP32-2432S028) with a built-in screen. LVGL (PSRAM boards) can reuse `display_pages`.

## Controllers
`control.Controls` (up to 3 `Ctl`, settings `control_cfg`, maths `control_calc`, card
`control_view`): heater, light (dimmer) or fan on one GPIO each, following any sensor value.
- Modes: PID (P on error, I with anti-windup, D on the measurement), on/off (hysteresis, minimum
  switching time), manual level, sun cycle (sunrise / sunset ramps, day / night level, smooth /
  linear / arc, needs the clock), curve (value → level through two points, optional stop), off.
- Output: PWM (16-bit duty where available, gamma, minimum level, fade, fan kick-start) or a
  time-proportional window for SSR / relay. Active hours. Safety: no reading for 30 s or a cut-off
  value → output 0.

## Status lights
`leds.Leds`: four LEDs (fully watered / normal / water soon / water now), modes on / eco / off.
`rgb.Rgb`: one RGB LED in the band colour (green good, yellow soon, red now, blinking drought, blue
wet, light blue draining, blinking magenta pump locked).

## Battery monitor
`battery.Battery`: supply voltage through a divider on an ADC1 pin; presets 5 V (100k/68k) and 12 V
(100k/15k) or custom with a suggested divider; calibration factor; low / critical alarms.

## Power saving
`power.Power`: CPU clock, soil sensors powered from GPIOs only while measuring, and a deep-sleep
cycle: wake on a timer, measure / log / sleep without WiFi (`cycle.py`), awake windows and a
maintenance mode for the dashboard, wake by button or touch. Never sleeps while the pump runs, a
controller is active or the setup AP is in use.

## Recording
`recorder.Recorder`: CSV rows of averages every interval to `/data/*.csv`, resumed after a restart
(`rec.json`), stopped at the end time, below `MIN_FREE_FLASH` or at `MAX_FILE_BYTES`.

## Data files
In `/data` on the board: `sensors.json`, `bands.json`, `trend.json`, `pump.json`, `pump.log`,
`mqtt.json`, `gsm.json`, `batt.json`, `leds.json`, `rgb.json`, `power.json`, `display.json`,
`control.json`, `features.json`, `rec.json`, recordings `*.csv`. All JSON writes are atomic (temp file
+ rename). In the root: `wifi.json` (never committed), `deployed.txt` (written by deploy).

## Deploy
`web/deploy.sh [--flash|--force-flash|--flash-garden] [--spiram] [--board NAME] [port]`: detects the
chip, PSRAM and flash size and installs the matching MicroPython (backup and verify first); compiles
every module to `.mpy` for the board's MicroPython; uploads them with `settings.py`, `main.py`,
`sensor_types.txt`, the gzipped web files and fonts; removes files of modules that no longer exist
(only ones a previous deploy put there, `deployed.txt`); keeps the board's wiring profile.

## Firmware
`firmware/build.sh` builds MicroPython 1.20.0 with the core modules frozen in flash (about 35 KB more
free RAM). The frozen list is `firmware/frozen.txt`, read by both `manifest.py` and `deploy.sh`.

## Coding standards
- Source files about 300 lines at most; one responsibility per module.
- Every module starts with a comment saying what it does and a `Spec:` link to this document.
- Every function and class has a docstring (one line if it says enough).
- Lines up to 120 characters; long HTML string literals in the panels may exceed it.
- MicroPython compatibility: no `str.isalnum` / `str.capitalize`, no tuple in `startswith`, no default
  in `next()`, no `del` of a `bytearray` slice; `asyncio` with a `uasyncio` fallback, `time.ticks_*`
  for intervals.
- Memory in loops that run often: no allocation when nothing changes (floats are heap objects on the
  ESP32), fixed `bytearray` buffers with `memoryview` slices for I/O (`readinto`), parts written one by
  one instead of joined, generators inside `sum` / `any` / `all`, `array('f')` for stored floats.
- Flat code: guard clauses and early returns instead of nested `if`; dispatch dicts for actions; a
  function per step when a loop body grows.
- Check: `pyflakes *.py web/*.py` (only the re-exports in `web/app.py` may be reported).
