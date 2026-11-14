# MQTT settings (DATA_DIR/mqtt.json, template in settings.py MQTT_*), shared by mqtt.py and the panel.
# Spec: docs/SPEC.md#mqtt
import json
import os
import settings as S

CFG = S.DATA_DIR + '/mqtt.json'
# Template from settings.py (all optional); values saved from the page (mqtt.json) override them.
DEFAULTS = {'on': getattr(S, 'MQTT_ENABLED', False), 'host': getattr(S, 'MQTT_HOST', ''),
            'port': getattr(S, 'MQTT_PORT', 1883), 'user': getattr(S, 'MQTT_USER', ''),
            'pw': getattr(S, 'MQTT_PASSWORD', ''), 'cid': getattr(S, 'MQTT_CLIENT_ID', ''),
            'base': getattr(S, 'MQTT_TOPIC', 'garden'), 'every': getattr(S, 'MQTT_EVERY_S', 60),
            'retain': getattr(S, 'MQTT_RETAIN', True), 'fields': list(getattr(S, 'MQTT_FIELDS', ()))}
FIELDS = ('air', 'pump', 'batt', 'board')       # plus 'sen_<id>' per registry sensor; [] = everything


def load_cfg():
    """MQTT settings (mqtt.json over the settings.py template)."""
    c = dict(DEFAULTS)
    try:
        with open(CFG) as f:
            d = json.load(f)
        for k in DEFAULTS:
            if k in d:
                c[k] = d[k]
    except (OSError, ValueError):
        pass
    if not c['cid']:
        import machine
        c['cid'] = 'garden-' + ''.join('%02x' % b for b in machine.unique_id()[-3:])
    return c


def save_cfg(c):
    """Write mqtt.json atomically."""
    tmp = CFG + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(c, f)
    os.rename(tmp, CFG)
