#!/usr/bin/env bash
# Build and upload the garden logger to an ESP32.
#
#   ./deploy.sh [--flash | --force-flash] [--spiram] [--board NAME] [port]   (default port /dev/ttyUSB0)
#
#   (no option)    upload the app; if no MicroPython answers (new board, Arduino) it offers to
#                  flash first, exactly as with --flash (asks y/N - a busy board can look empty)
#   --flash        detect the chip (esptool) and pick its firmware; if the board does not run that
#                  version: back up the whole flash to ESP_BIN/, verify the backup, erase, flash it
#                  and check it boots - then upload the app
#   --force-flash  same, but reflash even if that version is already installed
#   --flash-garden flash the newest custom firmware from ../firmware/build.sh (modules frozen in
#                  flash: ~35 KB more free RAM), same backup + verify steps
#   --spiram       classic ESP32 with external PSRAM (WROVER) -> SPIRAM firmware. Not needed for
#                  chips with PSRAM inside (detected) or with --board ttgo-tcall (implied)
#   --board NAME   devkit | ttgo-tcall | cyd (ESP32-2432S028): wiring profile written into the uploaded
#                  settings.py.
#                  Without it the profile already on the board is kept.
#
#   Firmware per detected chip (downloaded from micropython.org, file name looked up there):
#     ESP32 (WROOM)          ESP32_GENERIC            $FW_VERSION
#     ESP32 + PSRAM          ESP32_GENERIC-SPIRAM     $FW_STABLE (else $FW_NEW_VERSION)
#     ESP32-C3               ESP32_GENERIC_C3         $FW_STABLE (else $FW_NEW_VERSION)
#     ESP32-S3               ESP32_GENERIC_S3         $FW_STABLE; octal PSRAM -> SPIRAM_OCT,
#                                                     4 MB flash -> FLASH_4M (newest)
#     ESP32-C6               ESP32_GENERIC_C6         $FW_NEW_VERSION (no IDF 4 build exists)
#
# Needs the project venv: esptool, mpremote, mpy-cross 1.29 (pip install esptool mpremote "mpy-cross==1.29.*")
# and for 1.20 boards: pip install --target .venv/mpy-cross-1.20 "mpy-cross==1.20.0"
set -euo pipefail
cd "$(dirname "$0")"

# ---- firmware to install with --flash ----------------------------------------------------
# 1.20.0 = last release on ESP-IDF 4.4; 1.21+ (IDF 5) cannot join the WPA2/WPA3 mixed 'vGuest'.
FW_VERSION=1.13
FW_NAME=ESP32_GENERIC-20230426-v1.13.bin
# ESP32 + PSRAM, C3, S3: last ESP-IDF 4.4 release (joins 'vGuest'); newer builds only if it has none
FW_STABLE=1.20.0
# ESP32-C6 (first build 1.24 = ESP-IDF 5: may not join 'vGuest'), S3 with 4 MB flash
FW_NEW_VERSION=1.29.0
# -------------------------------------------------------------------------------------------

FW_DIR=../../ESP_BIN
FW_URL=https://micropython.org/resources/firmware/$FW_NAME
VENV=../../.venv/bin
ESPTOOL="$VENV/esptool"
BUILD=build

FLASH=0
GARDEN=0
SPIRAM=0
PORT=/dev/ttyUSB0
BOARD_SET=""
NEXT_BOARD=0
for a in "$@"; do
  if [[ "$NEXT_BOARD" == 1 ]]; then BOARD_SET="$a"; NEXT_BOARD=0; continue; fi
  case "$a" in
    --flash) FLASH=1 ;;
    --force-flash) FLASH=2 ;;
    --flash-garden) FLASH=2; GARDEN=1 ;;
    --spiram) SPIRAM=1 ;;
    --board=*) BOARD_SET="${a#--board=}" ;;
    --board) NEXT_BOARD=1; continue ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    -*) echo "unknown option $a" >&2; exit 2 ;;
    *) PORT="$a" ;;
  esac
done

die() { echo "ERROR: $*" >&2; exit 1; }
case "$BOARD_SET" in ""|devkit|ttgo-tcall|cyd) ;; *) die "unknown board '$BOARD_SET' (devkit | ttgo-tcall | cyd)" ;; esac
[[ "$BOARD_SET" == ttgo-tcall ]] && SPIRAM=1          # T-Call V1.4 = ESP32-WROVER-B (8 MB PSRAM)

norm() { [[ "$1" =~ ^[0-9]+\.[0-9]+$ ]] && echo "$1.0" || echo "$1"; }   # 1.13 -> 1.13.0 (as the board reports)

resolve_fw() {      # resolve_fw BOARD[-VARIANT] VERSION|latest...: file name of the first version that exists
  local base=$1 v f page; shift
  for v in "$@"; do
    f=$(cd "$FW_DIR" && ls -t "$base"-20[0-9]*-v"$v".bin 2>/dev/null | head -1 || true)   # downloaded before
    if [[ -z "$f" ]]; then
      [[ -n "${PAGE[$base]:-}" ]] || PAGE[$base]=$(curl -sSfL --max-time 30 \
        "https://micropython.org/download/${base%%-*}/" 2>/dev/null || echo none)
      if [[ "$v" == latest ]]; then                      # newest build of this variant
        f=$(grep -o "$base-20[0-9]*-v[0-9.]*\.bin" <<<"${PAGE[$base]}" | sort -u | tail -1 || true)
      else
        f=$(grep -o "$base-20[0-9]*-v$v\.bin" <<<"${PAGE[$base]}" | head -1 || true)
      fi
    fi
    [[ -n "$f" ]] && { echo "$f"; return; }
  done
}
declare -A PAGE

if [[ "$GARDEN" == 1 ]]; then
  FW_NAME=$(cd "$FW_DIR" && ls -t ESP32_GENERIC-v1.20.0-garden-*.bin 2>/dev/null | head -1 || true)
  [[ -n "$FW_NAME" ]] || die "no garden firmware in $FW_DIR - build it first: ../firmware/build.sh"
  FW_VERSION=1.20.0
  echo "== using custom firmware $FW_NAME"
fi

board_version() {   # prints e.g. 1.20.0, or nothing if the board does not run MicroPython
  local out i
  for i in 1 2 3; do   # retry: a board that is still booting cannot enter the raw REPL yet
    out=$(timeout 20 "$VENV/mpremote" connect "$PORT" exec \
      "import sys; print('MPYVER', '.'.join(str(x) for x in sys.implementation.version[:3]))" 2>/dev/null || true)
    out=$(sed -n 's/^MPYVER \([0-9.]*\).*/\1/p' <<<"$out" | tr -d '\r')
    [[ -n "$out" ]] && { echo "$out"; return; }
    sleep 3
  done
}

cur_machine() {     # os.uname().machine of the running MicroPython, e.g. 'Generic ESP32 module with ESP32'
  timeout 20 "$VENV/mpremote" connect "$PORT" exec "import os; print('MACH', os.uname().machine)" 2>/dev/null \
    | sed -n 's/^MACH //p' | tr -d '\r' || true
}

run_esptool() {     # run esptool, keep the full log (no pipes: avoids SIGPIPE + pipefail false failures)
  ESPLOG=$("$ESPTOOL" "$@" 2>&1) || { echo "$ESPLOG" | tail -5; return 1; }
}

flash_board() {
  echo "== checking chip on $PORT"
  local info chip mac size_mb
  run_esptool --port "$PORT" flash-id || die "esptool cannot talk to $PORT"
  info=$ESPLOG
  chip=$(sed -n 's/^Chip type: *//p; s/^Chip is //p' <<<"$info" | head -1)
  mac=$(sed -n 's/^MAC: *//p' <<<"$info" | head -1)
  size_mb=$(sed -n 's/^Detected flash size: *\([0-9]*\)MB.*/\1/p' <<<"$info" | head -1)
  echo "   chip: $chip   MAC: $mac   flash: ${size_mb:-?} MB"
  local psram
  psram=$(grep -o 'Embedded PSRAM [0-9]*MB' <<<"$info" | grep -o '[0-9]*' | head -1 || true)
  [[ -n "$psram" ]] && echo "   PSRAM inside the chip: ${psram} MB"
  # firmware + flash offset per chip family (C3 / S3 / C6 start at 0x0, the classic ESP32 at 0x1000)
  FWCHIP=esp32; FWOFF=0x1000
  local base vers
  case "$chip" in
    ESP32-C3*) base=ESP32_GENERIC_C3; FWCHIP=esp32c3; FWOFF=0x0; vers="$FW_STABLE $FW_NEW_VERSION" ;;
    ESP32-C6*) base=ESP32_GENERIC_C6; FWCHIP=esp32c6; FWOFF=0x0; vers="$FW_NEW_VERSION" ;;
    ESP32-S3*) base=ESP32_GENERIC_S3; FWCHIP=esp32s3; FWOFF=0x0; vers="$FW_STABLE $FW_NEW_VERSION"
      # in-package 8 MB PSRAM (N8R8, N16R8...) is octal; quad PSRAM (R2) runs on the plain build
      if [[ "${psram:-0}" -ge 8 ]]; then base=$base-SPIRAM_OCT
      elif [[ "$size_mb" == 4 ]]; then base=$base-FLASH_4M; vers="$FW_NEW_VERSION latest"; fi ;;
    ESP32-D2WD*) die "ESP32-D2WD has 2 MB flash: too small for this app" ;;
    ESP32-D0WD*|ESP32-S0WD*|ESP32-U4WDH*|ESP32-PICO*)
      base=ESP32_GENERIC; vers="$FW_VERSION"
      if [[ "$SPIRAM" == 1 || -n "$psram" ]]; then base=ESP32_GENERIC-SPIRAM; vers="$FW_STABLE $FW_NEW_VERSION"; fi ;;
    *) die "chip '$chip' is not supported (ESP32, ESP32-C3, ESP32-S3, ESP32-C6)" ;;
  esac
  if [[ "$GARDEN" == 0 ]]; then
    FW_NAME=$(resolve_fw "$base" $vers)
    [[ -n "$FW_NAME" ]] || die "no $base firmware ($vers) in $FW_DIR or on micropython.org (offline?)"
    FW_VERSION=$(norm "$(sed 's/.*-v\([0-9.]*\)\.bin/\1/' <<<"$FW_NAME")")
    case "$FW_VERSION" in 1.1[0-9].*|1.[0-9].*)       # checked before anything is erased
      die "MicroPython $FW_VERSION is too old for this app (needs 1.20+): set FW_VERSION=1.20.0 at the top of $0" ;;
    esac
  elif [[ "$FWCHIP" != esp32 || "$base" != ESP32_GENERIC ]]; then
    die "the garden firmware is built for the plain classic ESP32 only"
  fi
  FW_URL=https://micropython.org/resources/firmware/$FW_NAME
  echo "   firmware: $FW_NAME at $FWOFF"
  [[ -n "$size_mb" && "$size_mb" -ge 4 ]] || die "need at least 4 MB flash (detected: ${size_mb:-unknown})"

  local cur
  cur=$(board_version)
  echo "   installed: ${cur:-no MicroPython (or not responding)}"
  local want_spiram=0
  [[ "$base" == *SPIRAM* ]] && want_spiram=1
  if [[ "$cur" == "$FW_VERSION" && "$FLASH" -lt 2 && "$(cur_machine | grep -ci spiram || true)" == "$want_spiram" ]]; then
    echo "   MicroPython $FW_VERSION already installed - not reflashing (use --force-flash to reflash)"
    return
  fi

  if [[ ! -f "$FW_DIR/$FW_NAME" ]]; then
    echo "== downloading $FW_NAME"
    curl -sSfL -o "$FW_DIR/$FW_NAME" "$FW_URL" || die "download failed: $FW_URL"
  fi
  [[ "$(head -c1 "$FW_DIR/$FW_NAME" | od -An -tx1 | tr -d ' ')" == e9 ]] || die "$FW_NAME is not an ESP32 image"

  # Backup + verify + erase run with '--after no-reset': each following command resets straight
  # into the bootloader, so the old firmware never runs in between and the backup stays exact.
  local backup="$FW_DIR/backup_${mac//:/}_$(date +%Y%m%d_%H%M%S).bin"
  echo "== backing up ${size_mb} MB flash to $backup"
  run_esptool --port "$PORT" --baud 921600 --after no-reset read-flash 0 $((size_mb * 1024 * 1024)) "$backup" \
    || die "backup failed"
  grep -m1 '^Read ' <<<"$ESPLOG"
  echo "== verifying backup"
  run_esptool --port "$PORT" --baud 921600 --after no-reset verify-flash 0 "$backup" || true
  grep -q 'Verification successful' <<<"$ESPLOG" \
    || die "backup does not match the chip - NOT erasing. Backup kept: $backup"
  echo "   backup verified"
  echo "== erasing and flashing MicroPython $FW_VERSION"
  run_esptool --chip "$FWCHIP" --port "$PORT" --baud 921600 --after no-reset erase-flash || die "erase failed"
  grep -q 'erased successfully' <<<"$ESPLOG" || die "erase not confirmed"
  run_esptool --chip "$FWCHIP" --port "$PORT" --baud 460800 write-flash "$FWOFF" "$FW_DIR/$FW_NAME" || die "flashing failed"
  grep -q 'Hash of data verified' <<<"$ESPLOG" || die "flash write not verified"
  sleep 4          # first boot creates the filesystem
  cur=$(board_version)
  [[ "$cur" == "$FW_VERSION" ]] || die "board did not come up with MicroPython $FW_VERSION (got '${cur:-nothing}')"
  echo "   MicroPython $cur running. Restore the old firmware with:"
  echo "   $ESPTOOL --port $PORT write-flash 0 $backup"
}

if [[ "$FLASH" == 0 ]]; then
  VER=$(board_version)
  if [[ -z "$VER" ]]; then
    echo "== no MicroPython answering on $PORT (new board, Arduino sketch - or just busy/booting)"
    [[ -t 0 ]] || die "not flashing without asking. Retry, or flash with: $0 --flash $PORT"
    read -r -p "   Detect the chip and flash MicroPython (old flash is backed up first)? [y/N] " yn
    [[ "$yn" == [yY]* ]] || die "nothing changed. Retry when the board has booted, or use --flash"
    FLASH=1
  fi
fi
[[ "$FLASH" -gt 0 ]] && flash_board

# ---- build ------------------------------------------------------------------------------
VER=$(board_version)
[[ -n "$VER" ]] || die "no MicroPython on $PORT (or it does not respond). Try: $0 --force-flash $PORT"
echo "== board: $(cur_machine)"
case "$VER" in
  1.1[0-9].*|1.[0-9].*) die "MicroPython $VER is too old for this app (needs 1.20+): set FW_VERSION=1.20.0 and run $0 --force-flash" ;;
  1.20.*|1.21.*) MPYCROSS=../../.venv/mpy-cross-1.20/mpy_cross/mpy-cross ;;   # bytecode v6.1
  *)      MPYCROSS="$VENV/mpy-cross" ;;                                # bytecode v6.3 (1.22+)
esac
[[ -x "$MPYCROSS" ]] || die "missing $MPYCROSS"
echo "== board MicroPython $VER -> $("$MPYCROSS" --version)"
[[ -f wifi.json ]] || echo "   note: no wifi.json - the board will start the 'garden' setup hotspot"

rm -rf "$BUILD"; mkdir -p "$BUILD/www"
# Python -> .mpy (precompiled bytecode: no on-device compile, much less heap at boot)
"$MPYCROSS" -O1 -o "$BUILD/app.mpy" app.py
"$MPYCROSS" -O1 -o "$BUILD/aht10.mpy" ../aht10.py
"$MPYCROSS" -O1 -o "$BUILD/climate.mpy" ../climate.py
"$MPYCROSS" -O1 -o "$BUILD/bands.mpy" ../bands.py
"$MPYCROSS" -O1 -o "$BUILD/leds.mpy" ../leds.py
"$MPYCROSS" -O1 -o "$BUILD/battery.mpy" ../battery.py
"$MPYCROSS" -O1 -o "$BUILD/power.mpy" ../power.py
"$MPYCROSS" -O1 -o "$BUILD/ui_cfg.mpy" ui_cfg.py
"$MPYCROSS" -O1 -o "$BUILD/cycle.mpy" cycle.py
"$MPYCROSS" -O1 -o "$BUILD/pump.mpy" ../pump.py      # pump + mqtt: not frozen in the garden firmware, always uploaded
"$MPYCROSS" -O1 -o "$BUILD/mqtt.mpy" ../mqtt.py
"$MPYCROSS" -O1 -o "$BUILD/ui_pump.mpy" ui_pump.py
"$MPYCROSS" -O1 -o "$BUILD/ui_mqtt.mpy" ui_mqtt.py
for m in ui_wifi ui_files captive ui_batt ui_sensors ui_sensors_set ui_rgb ui_gsm ui_feat ui_display ui_control ui_control_form; do "$MPYCROSS" -O1 -o "$BUILD/$m.mpy" $m.py; done
"$MPYCROSS" -O1 -o "$BUILD/sensors.mpy" ../sensors.py
"$MPYCROSS" -O1 -o "$BUILD/sensor_types.mpy" ../sensor_types.py
"$MPYCROSS" -O1 -o "$BUILD/gsm.mpy" ../gsm.py
"$MPYCROSS" -O1 -o "$BUILD/gsm_cfg.mpy" ../gsm_cfg.py
"$MPYCROSS" -O1 -o "$BUILD/recorder.mpy" ../recorder.py
for m in display disp_oled disp_tft gfont control control_cfg; do "$MPYCROSS" -O1 -o "$BUILD/$m.mpy" ../$m.py; done
"$MPYCROSS" -O1 -o "$BUILD/rgb.mpy" ../rgb.py           # sensor registry + one file per driver type
for f in ../drv_*.py; do "$MPYCROSS" -O1 -o "$BUILD/$(basename "${f%.py}").mpy" "$f"; done
cp main.py settings.py sensor_types.txt "$BUILD/"
# wiring profile: --board, else the one in the settings.py already on the board, else the template's
if [[ -z "$BOARD_SET" ]]; then
  BOARD_SET=$(timeout 20 "$VENV/mpremote" connect "$PORT" exec "
try:
    for l in open('settings.py'):
        if l.startswith('BOARD'): print('BOARDIS', l.split(\"'\")[1]); break
except OSError: pass" 2>/dev/null | sed -n 's/^BOARDIS //p' | tr -d '\r' || true)
fi
if [[ -n "$BOARD_SET" ]]; then
  sed -i "s/^BOARD = '[a-z0-9-]*'/BOARD = '$BOARD_SET'/" "$BUILD/settings.py"
  grep -q "^BOARD = '$BOARD_SET'" "$BUILD/settings.py" || die "could not set BOARD in settings.py"
  echo "   wiring profile: $BOARD_SET"
fi
# Static files gzipped (served as-is with Content-Encoding: gzip, streamed in 512 B chunks)
for f in www/*; do gzip -9 -n -c "$f" > "$BUILD/$f.gz"; done

# ---- upload -----------------------------------------------------------------------------
echo "== uploading"
MP="$VENV/mpremote connect $PORT"
$MP exec "import os
for d in ('www','data','fonts'):
    try: os.mkdir(d)
    except OSError: pass
for f in ('heater.mpy','ui_heater.mpy','aht10.py','app.py','climate.py','bands.py','leds.py','battery.py','power.py','ui_cfg.py','cycle.py','pump.py','mqtt.py','ui_pump.py','ui_mqtt.py','ui_wifi.py','ui_files.py','captive.py','ui_batt.py','ui_sensors.py','ui_sensors_set.py','ui_rgb.py','rgb.py','ui_gsm.py','gsm.py','ui_feat.py','recorder.py','sensors.py','drv_analog.py'):
    try: os.remove(f)
    except OSError: pass" >/dev/null
MODS="app aht10 climate bands leds battery power ui_cfg cycle"
FROZEN=$($MP exec "try:
    import garden_fw; print('FROZEN', garden_fw.VERSION)
except ImportError:
    pass" 2>/dev/null | sed -n 's/^FROZEN \([0-9]*\).*/\1/p' | tr -d '\r')
if [[ -n "$FROZEN" ]]; then
  # custom firmware with the modules built in: remove filesystem copies (they would shadow the frozen ones)
  echo "   garden firmware $FROZEN: modules are frozen in flash - uploading settings and web files only"
  $MP exec "import os
for m in '$MODS'.split():
    try: os.remove(m + '.mpy')
    except OSError: pass" >/dev/null
  $MP cp "$BUILD/pump.mpy" "$BUILD/mqtt.mpy" "$BUILD/ui_pump.mpy" "$BUILD/ui_mqtt.mpy" "$BUILD/ui_wifi.mpy" "$BUILD/ui_files.mpy" "$BUILD/captive.mpy" "$BUILD/ui_batt.mpy" "$BUILD/ui_sensors.mpy" "$BUILD/ui_sensors_set.mpy" "$BUILD/ui_rgb.mpy" "$BUILD/rgb.mpy" "$BUILD/ui_gsm.mpy" "$BUILD/gsm.mpy" "$BUILD/ui_feat.mpy" "$BUILD/recorder.mpy" "$BUILD/ui_display.mpy" "$BUILD/display.mpy" "$BUILD/disp_oled.mpy" "$BUILD/disp_tft.mpy" "$BUILD/gfont.mpy" "$BUILD/control.mpy" "$BUILD/control_cfg.mpy" "$BUILD/ui_control.mpy" "$BUILD/ui_control_form.mpy" "$BUILD/sensors.mpy" "$BUILD/sensor_types.mpy" "$BUILD/gsm_cfg.mpy" "$BUILD"/drv_*.mpy "$BUILD/settings.py" "$BUILD/main.py" "$BUILD/sensor_types.txt" : >/dev/null
else
  $MP cp $(for m in $MODS; do echo "$BUILD/$m.mpy"; done) "$BUILD/pump.mpy" "$BUILD/mqtt.mpy" "$BUILD/ui_pump.mpy" "$BUILD/ui_mqtt.mpy" "$BUILD/ui_wifi.mpy" "$BUILD/ui_files.mpy" "$BUILD/captive.mpy" "$BUILD/ui_batt.mpy" "$BUILD/ui_sensors.mpy" "$BUILD/ui_sensors_set.mpy" "$BUILD/ui_rgb.mpy" "$BUILD/rgb.mpy" "$BUILD/ui_gsm.mpy" "$BUILD/gsm.mpy" "$BUILD/ui_feat.mpy" "$BUILD/recorder.mpy" "$BUILD/ui_display.mpy" "$BUILD/display.mpy" "$BUILD/disp_oled.mpy" "$BUILD/disp_tft.mpy" "$BUILD/gfont.mpy" "$BUILD/control.mpy" "$BUILD/control_cfg.mpy" "$BUILD/ui_control.mpy" "$BUILD/ui_control_form.mpy" "$BUILD/sensors.mpy" "$BUILD/sensor_types.mpy" "$BUILD/gsm_cfg.mpy" "$BUILD"/drv_*.mpy "$BUILD/settings.py" "$BUILD/main.py" "$BUILD/sensor_types.txt" : >/dev/null
fi
$MP cp "$BUILD"/www/*.gz :www/ >/dev/null
$MP cp fonts/*.bin :fonts/ >/dev/null          # display fonts (read from flash glyph by glyph)
if [[ -f wifi.json ]]; then $MP cp wifi.json :wifi.json >/dev/null; echo "   uploaded wifi.json"; fi
$MP exec "import os; print('   board:', os.listdir(), os.listdir('www'))"
$MP reset
echo "Deployed. Watch the console with: $VENV/mpremote connect $PORT repl"
