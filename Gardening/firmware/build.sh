#!/usr/bin/env bash
# Build MicroPython 1.20.0 (ESP-IDF 4.4.4, same base as the stock firmware: joins vGuest) with the
# garden logger modules frozen in. Output: ESP_BIN/ESP32_GENERIC-v1.20.0-garden-<date>.bin
#   ./build.sh            needs docker; first run downloads the IDF image (~2.5 GB)
set -euo pipefail
cd "$(dirname "$0")"
ROOT=$(cd ../.. && pwd)                       # ESP_Micropython
MP=$PWD/micropython                           # git-ignored checkout
IMG=espressif/idf:v4.4.4
[[ -d $MP ]] || git clone -q --depth 1 -b v1.20.0 https://github.com/micropython/micropython.git "$MP"
echo "VERSION = '$(date +%Y%m%d)'" > garden_fw.py
docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$ROOT":/w -w /w/Gardening/firmware/micropython "$IMG" bash -c '
  set -e
  git config --global --add safe.directory "*"
  make -s -C mpy-cross
  cd ports/esp32
  make -s submodules BOARD=GENERIC
  make BOARD=GENERIC FROZEN_MANIFEST=/w/Gardening/firmware/manifest.py -j8 2>&1 | tail -15
'
OUT=$ROOT/ESP_BIN/ESP32_GENERIC-v1.20.0-garden-$(date +%Y%m%d).bin
cp "$MP/ports/esp32/build-GENERIC/firmware.bin" "$OUT"
echo "firmware: $OUT ($(stat -c %s "$OUT") bytes)"
