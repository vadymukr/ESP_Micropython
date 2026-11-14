# Frozen-module manifest for the garden logger firmware (MicroPython 1.20.0, ESP32_GENERIC).
# Frozen code runs from flash instead of RAM, which frees ~35 KB of heap. The module list is
# firmware/frozen.txt (deploy.sh reads the same list). settings.py and www/ stay on the filesystem.
# Spec: docs/SPEC.md#firmware
include("$(PORT_DIR)/boards/manifest.py")
G = "/w/Gardening"
module("garden_fw.py", base_path=G + "/firmware")   # marker: deploy.sh skips frozen modules
with open(G + "/firmware/frozen.txt") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#"):
            d, _, name = line.rpartition("/")
            module(name, base_path=G + ("/" + d if d else ""))
