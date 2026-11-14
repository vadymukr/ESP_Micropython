# Display settings (DATA_DIR/display.json; without one the board's built-in screen, settings.DISPLAY_BUILTIN),
# shared by display.py and the Displays panel.
# Spec: docs/SPEC.md#displays
import json
import os
import settings as S

CFG = S.DATA_DIR + '/display.json'



def load_cfg():
    """display.json; without one, the board's built-in screen (settings.DISPLAY_BUILTIN), if any."""
    try:
        with open(CFG) as f:
            c = json.load(f)
        if isinstance(c.get('list'), list):
            return c
    except (OSError, ValueError):
        pass
    b = getattr(S, 'DISPLAY_BUILTIN', None)
    return {'list': [dict(b)] if b else []}


def save_cfg(c):
    """Write display.json atomically."""
    tmp = CFG + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(c, f)
    os.rename(tmp, CFG)
