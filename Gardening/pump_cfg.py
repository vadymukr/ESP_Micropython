# Pump settings (DATA_DIR/pump.json) and small helpers shared by pump.py and the pump panel.
# Spec: docs/SPEC.md#pump
import json
import os
import time
import settings as S

CFG = S.DATA_DIR + '/pump.json'
LOG = S.DATA_DIR + '/pump.log'
LOG_MAX = 8 * 1024             # pump.log is renamed to pump.old at this size
MODES = ('manual', 'schedule', 'auto')
DEFAULTS = {'enabled': True, 'mode': 'manual', 'man_s': 15, 'times': '06:30', 'sched_s': 20, 'skip_moist': True,
            'pulse_s': 15, 'soak_min': 10, 'max_pulses': 4, 'hours': '05:30-09:00', 'target': 3,
            'sensors': [0], 'fault': '', 'day': '', 'day_s': 0, 'done': '',
            # optional flow sensor protection + water statistics
            'flow': '', 'flow_min': 0.2, 'flow_wait': 8, 'rate_lpm': 0.0,
            'l_day': 0.0, 'l_total': 0.0, 'runs_day': 0, 'hist': []}
FLOW_TYPES = ('flow', 'yfs401')
HIST_DAYS = 14




def _save(path, obj):
    """Write JSON atomically (temp file + rename): a power cut never leaves half a file."""
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(obj, f)
    os.rename(tmp, path)


def parse_times(s):
    """'06:30,18:00' -> [390, 1080] minutes of the day; bad parts ignored."""
    out = []
    for p in s.replace(' ', '').split(','):
        try:
            m = int(p[:2]) * 60 + int(p[3:5])
            if len(p) == 5 and p[2] == ':' and 0 <= m < 1440:
                out.append(m)
        except ValueError:
            pass
    return out


def parse_hours(s):
    """'05:30-09:00' -> (330, 540), '' -> None (any time), bad -> False."""
    s = s.replace(' ', '')
    if not s:
        return None
    a, _, b = s.partition('-')
    t = parse_times(a + ',' + b)
    return (t[0], t[1]) if len(t) == 2 and t[0] != t[1] else False




def _stamp():
    """Local time for log lines."""
    tm = time.localtime(time.time() + S.TZ_OFFSET_H * 3600)
    return '%04d-%02d-%02d %02d:%02d:%02d' % tm[:6]

