# Frozen-module manifest for the garden logger firmware (MicroPython 1.20.0, ESP32_GENERIC).
# Frozen code runs from flash instead of RAM, which frees ~35 KB of heap.
# settings.py and www/ stay on the filesystem so they can be edited without rebuilding.
include("$(PORT_DIR)/boards/manifest.py")
G = "/w/Gardening"
module("garden_fw.py", base_path="/w/Gardening/firmware")   # marker: deploy.sh skips frozen modules
for m in ("app.py", "ui_cfg.py", "cycle.py"):
    module(m, base_path=G + "/web")
for m in ("aht10.py", "climate.py", "bands.py", "leds.py", "battery.py", "power.py"):
    module(m, base_path=G)
