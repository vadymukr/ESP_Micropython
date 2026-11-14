# Sensor types for the Sensors panel: read from sensor_types.txt (one JSON line per type) line by line,
# so the table takes almost no RAM however many types there are. Edit the .txt to add a type.
import json

FILE = 'sensor_types.txt'


def _lines():
    with open(FILE) as f:
        for ln in f:
            if ln.startswith('['):
                yield ln


def each():
    """(type, label, calibrate button) for every type, in file order."""
    for ln in _lines():
        t = json.loads(ln)
        yield t[0], t[1], t[4]


def get(typ):
    """(label, pin kind, form fields [(key, label, 'num'|'text', default)], driver, calibrate button) or None."""
    key = '["%s"' % typ
    for ln in _lines():
        if ln.startswith(key + ',') or ln.startswith(key + ' '):
            t = json.loads(ln)
            return t[1], t[2], [tuple(x) for x in t[5]], t[3], t[4]
    return None


def label(typ):
    """(label, calibrate button); unknown type: (typ, None)."""
    t = get(typ)
    return (t[0], t[4]) if t else (typ, None)
