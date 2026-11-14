#!/usr/bin/env bash
# Build and upload the garden logger to an ESP32.
#
#   ./deploy.sh [--flash | --force-flash] [port]        (default port /dev/ttyUSB0)
#
#   (no option)    upload the app to a board that already runs MicroPython
#   --flash        first check the chip; if it does not run MicroPython $FW_VERSION:
#                  back up the whole flash to ESP_BIN/, verify the backup, erase, flash
#                  $FW_VERSION and check it boots - then upload the app
#   --force-flash  same, but reflash even if $FW_VERSION is already installed
#   --flash-garden flash the newest custom firmware from ../firmware/build.sh (modules frozen in
#                  flash: ~35 KB more free RAM), same backup + verify steps
#
# Needs the project venv: esptool, mpremote, mpy-cross 1.29 (pip install esptool mpremote "mpy-cross==1.29.*")
# and for 1.20 boards: pip install --target .venv/mpy-cross-1.20 "mpy-cross==1.20.0"
set -euo pipefail
cd "$(dirname "$0")"

# ---- firmware to install with --flash ----------------------------------------------------
# 1.20.0 = last release on ESP-IDF 4.4; 1.21+ (IDF 5) cannot join the WPA2/WPA3 mixed 'vGuest'.
FW_VERSION=1.13
FW_NAME=ESP32_GENERIC-20230426-v1.13.bin
# -------------------------------------------------------------------------------------------

FW_DIR=../../ESP_BIN
FW_URL=https://micropython.org/resources/firmware/$FW_NAME
VENV=../../.venv/bin
ESPTOOL="$VENV/esptool"
BUILD=build

FLASH=0
GARDEN=0
PORT=/dev/ttyUSB0
for a in "$@"; do
  case "$a" in
    --flash) FLASH=1 ;;
    --force-flash) FLASH=2 ;;
    --flash-garden) FLASH=2; GARDEN=1 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    -*) echo "unknown option $a" >&2; exit 2 ;;
    *) PORT="$a" ;;
  esac
done

die() { echo "ERROR: $*" >&2; exit 1; }

if [[ "$GARDEN" == 1 ]]; then
  FW_NAME=$(cd "$FW_DIR" && ls -t ESP32_GENERIC-v1.20.0-garden-*.bin 2>/dev/null | head -1 || true)
  [[ -n "$FW_NAME" ]] || die "no garden firmware in $FW_DIR - build it first: ../firmware/build.sh"
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
  # ESP32_GENERIC firmware is only for the classic ESP32 (D0WD*, D2WD, S0WD, U4WDH, PICO-*), not S2/S3/C*/H*/P4
  [[ "$chip" =~ ^ESP32-(D0WD|D2WD|S0WD|U4WDH|PICO) ]] || die "chip '$chip' is not a classic ESP32 - $FW_NAME does not fit"
  [[ -n "$size_mb" && "$size_mb" -ge 4 ]] || die "need at least 4 MB flash (detected: ${size_mb:-unknown})"

  local cur
  cur=$(board_version)
  echo "   installed: ${cur:-no MicroPython (or not responding)}"
  if [[ "$cur" == "$FW_VERSION" && "$FLASH" -lt 2 ]]; then
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
  run_esptool --chip esp32 --port "$PORT" --baud 921600 --after no-reset erase-flash || die "erase failed"
  grep -q 'erased successfully' <<<"$ESPLOG" || die "erase not confirmed"
  run_esptool --chip esp32 --port "$PORT" --baud 460800 write-flash 0x1000 "$FW_DIR/$FW_NAME" || die "flashing failed"
  grep -q 'Hash of data verified' <<<"$ESPLOG" || die "flash write not verified"
  sleep 4          # first boot creates the filesystem
  cur=$(board_version)
  [[ "$cur" == "$FW_VERSION" ]] || die "board did not come up with MicroPython $FW_VERSION (got '${cur:-nothing}')"
  echo "   MicroPython $cur running. Restore the old firmware with:"
  echo "   $ESPTOOL --port $PORT write-flash 0 $backup"
}

[[ "$FLASH" -gt 0 ]] && flash_board

# ---- build ------------------------------------------------------------------------------
VER=$(board_version)
[[ -n "$VER" ]] || die "no MicroPython on $PORT (or it does not respond). Use: $0 --flash $PORT"
case "$VER" in
  1.20.*) MPYCROSS=../../.venv/mpy-cross-1.20/mpy_cross/mpy-cross ;;   # bytecode v6.1
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
for m in ui_wifi ui_files captive; do "$MPYCROSS" -O1 -o "$BUILD/$m.mpy" $m.py; done
cp main.py settings.py "$BUILD/"
# Static files gzipped (served as-is with Content-Encoding: gzip, streamed in 512 B chunks)
for f in www/*; do gzip -9 -n -c "$f" > "$BUILD/$f.gz"; done

# ---- upload -----------------------------------------------------------------------------
echo "== uploading"
MP="$VENV/mpremote connect $PORT"
$MP exec "import os
for d in ('www','data'):
    try: os.mkdir(d)
    except OSError: pass
for f in ('aht10.py','app.py','climate.py','bands.py','leds.py','battery.py','power.py','ui_cfg.py','cycle.py','pump.py','mqtt.py','ui_pump.py','ui_mqtt.py','ui_wifi.py','ui_files.py','captive.py'):
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
  $MP cp "$BUILD/pump.mpy" "$BUILD/mqtt.mpy" "$BUILD/ui_pump.mpy" "$BUILD/ui_mqtt.mpy" "$BUILD/ui_wifi.mpy" "$BUILD/ui_files.mpy" "$BUILD/captive.mpy" "$BUILD/settings.py" "$BUILD/main.py" : >/dev/null
else
  $MP cp $(for m in $MODS; do echo "$BUILD/$m.mpy"; done) "$BUILD/pump.mpy" "$BUILD/mqtt.mpy" "$BUILD/ui_pump.mpy" "$BUILD/ui_mqtt.mpy" "$BUILD/ui_wifi.mpy" "$BUILD/ui_files.mpy" "$BUILD/captive.mpy" "$BUILD/settings.py" "$BUILD/main.py" : >/dev/null
fi
$MP cp "$BUILD"/www/*.gz :www/ >/dev/null
if [[ -f wifi.json ]]; then $MP cp wifi.json :wifi.json >/dev/null; echo "   uploaded wifi.json"; fi
$MP exec "import os; print('   board:', os.listdir(), os.listdir('www'))"
$MP reset
echo "Deployed. Watch the console with: $VENV/mpremote connect $PORT repl"
