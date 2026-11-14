# Controller settings (DATA_DIR/control.json) for the Control panel and control.py: kept apart so the
# panel can read and save them without loading the controller code.
import json
import os
import settings as S

CFG = S.DATA_DIR + '/control.json'
OLD = S.DATA_DIR + '/heater.json'
MAX = 3
KINDS = (('heater', 'Heater'), ('light', 'Light (dimmer)'), ('fan', 'Fan / cooler'))
UP = ('heater', 'light')                   # these raise the reading


def defaults(kind):
    g = lambda k, d: getattr(S, k, d)
    c = {'id': '', 'name': dict(KINDS)[kind].split(' ')[0], 'kind': kind, 'on': True, 'pin': None, 'inv': False,
         'src': 'air', 'mode': 'pid', 'on_mode': 'pid', 'sp': 25.0, 'hyst': 1.0, 'min_s': 30, 'kp': 20.0, 'ki': 2.0,
         'kd': 0.0, 'man': 50, 'out': 'pwm', 'freq': 1000, 'window_s': 10, 'min_on_s': 1.0, 'max_pct': 100,
         'min_pct': 0, 'gamma': False, 'hours': '', 'cut': None, 'cut_back': 2.0,
         'rise': '07:00', 'set': '19:00', 'ramp_min': 60, 'day': 100, 'night': 0, 'shape': 'smooth',
         'x1': 30.0, 'y1': 30, 'x2': 45.0, 'y2': 100, 'stop': True, 'ramp_s': 0, 'kick': False}
    if kind == 'heater':
        c.update(pin=g('HEATER_PIN', None), inv=not g('HEATER_ACTIVE_HIGH', True), src=g('HEATER_SOURCE', 'air'),
                 mode=g('HEATER_MODE', 'pid'), sp=g('HEATER_SETPOINT', 25.0), hyst=g('HEATER_HYST', 0.5),
                 min_s=g('HEATER_MIN_S', 30), kp=g('HEATER_KP', 20.0), ki=g('HEATER_KI', 2.0), kd=g('HEATER_KD', 0.0),
                 out=g('HEATER_OUTPUT', 'window'), freq=g('HEATER_PWM_HZ', 100), window_s=g('HEATER_WINDOW_S', 10),
                 max_pct=g('HEATER_MAX_PCT', 100), cut=g('HEATER_MAX_C', 40.0))
    elif kind == 'light':                  # lux from a BH1750 / LDR; manual dimming by default
        c.update(mode='manual', man=g('LIGHT_LEVEL', 60), freq=g('LIGHT_PWM_HZ', 1000), gamma=g('LIGHT_GAMMA', True),
                 sp=300.0, hyst=50.0, min_s=60, kp=0.05, ki=0.5, src='', cut_back=50.0, ramp_s=10)
    else:                                  # fan: speed follows the temperature (curve)
        c.update(mode='curve', sp=28.0, hyst=1.0, freq=25000, kp=20.0, ki=2.0, ramp_s=3, kick=True)
    return c


def load_cfg():
    """{'list': [entry, ...]}; the first time, an existing heater.json becomes controller C1."""
    try:
        with open(CFG) as f:
            lst = json.load(f)['list']
        out = []
        for e in lst:
            c = defaults(e.get('kind', 'heater'))
            c.update(e)
            out.append(c)
        return {'list': out}
    except (OSError, ValueError, KeyError):
        pass
    try:
        with open(OLD) as f:
            h = json.load(f)
        c = defaults('heater')
        for k in c:
            if k in h:
                c[k] = h[k]
        c.update(id='C1', name='Heater', cut=h.get('t_max', c['cut']))
        return {'list': [c]}
    except (OSError, ValueError):
        return {'list': []}


def save_cfg(c):
    tmp = CFG + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(c, f)
    os.rename(tmp, CFG)


def sources():
    """[(key, label, unit)]: air temperature / humidity and every registry channel."""
    import app
    s = app.sens
    out = [('air', 'Air temperature', '\xb0C'), ('air.rh', 'Air humidity', '%')] if s.air_on else []
    for g in s.gch:
        out.append(('%s.%s' % (g[6], g[2]), g[3], g[4].replace('&deg;', '\xb0')))
    return out
