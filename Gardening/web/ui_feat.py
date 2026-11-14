# Features panel (/features, /features/save) - loaded per request by app.py, then released.
# Saves DATA_DIR/features.json and restarts: a feature that is off is never imported (no RAM).
import json
import os
import app
import settings as S

# key, label, approximate RAM while on (measured on ESP32 / MicroPython 1.20)
INFO = (('pump', 'Watering pump', 8), ('mqtt', 'MQTT publisher', 9), ('gsm', 'GSM modem (SIM800L)', 16),
        ('battery', 'Battery / supply monitor', 2), ('leds', '4-LED band indicator', 1),
        ('rgb', 'RGB status light', 2), ('power', 'Power saving / sleep cycle', 6),
        ('recorder', 'CSV recording', 5), ('display', 'Displays (OLED / TFT)', 16))
CFG = S.DATA_DIR + '/features.json'


def frag(msg='', ok=False):
    import gc
    on = app.FEAT
    out = ['<form class="set" hx-post="/features/save" hx-target="#features">']
    for k, lbl, kb in INFO:
        out.append('<label class="row" style="flex-direction:row;align-items:center;gap:6px;flex-basis:100%%">'
                   '<input type="checkbox" name="%s" value="1"%s> %s <small class="muted">~%d KB</small></label>'
                   % (k, ' checked' if k in on else '', lbl, kb))
    out.append('<button>Save and restart</button>')
    if _exists():
        out.append('<button class="small" name="reset" value="1">Back to settings.py</button>')
    out.append('</form>')
    if msg:
        out.append('<p class="%s">%s</p>' % ('ok' if ok else 'warn', app.esc(msg)))
    out.append('<p class="muted">Free RAM now %d KB. A feature that is off is not loaded at all: its code uses no RAM '
               'and its panel is hidden; its saved settings are kept for when you switch it back on. Soil sensors, '
               'the air sensor and the sensor list are always on. Default list: <code>FEATURES</code> in '
               'settings.py%s.</p>' % (gc.mem_free() // 1024, ' (overridden here)' if _exists() else ''))
    return out


def _exists():
    try:
        os.stat(CFG)
        return True
    except OSError:
        return False


def handle(method, path, f):
    if method == 'GET':
        return frag() if path == '/features' else None
    if path != '/features/save':
        return None
    if f.get('reset') == '1':
        try:
            os.remove(CFG)
        except OSError:
            pass
        app.restart_soon()
        return frag('Back to the settings.py list. Restarting...', True)
    on = [k for k, _, _ in INFO if f.get(k) == '1']
    tmp = CFG + '.tmp'
    with open(tmp, 'w') as fh:
        json.dump({'on': on}, fh)
    os.rename(tmp, CFG)
    app.restart_soon()
    return frag('Saved. Restarting - reload the page in about 20 s.', True)
