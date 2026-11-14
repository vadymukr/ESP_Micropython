# Sensors panel, saving part (POST /sensors/save, /sensors/del, /sensors/cap) - loaded per request by
# app.py, then released. Returns a short message for #smsg; on success the panel reloads itself.
# Adding, removing or moving a sensor restarts the board (everything is rebuilt from the saved list);
# names and calibration apply at once.
# Spec: docs/SPEC.md#sensors
import json
import os
import app
from app import esc
import settings as S

NAME_OK = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 -_.#'
MAX_SENSORS = 8


def bad(msg):
    """Warning message for the panel."""
    return '<p class="warn">%s</p>' % esc(msg)


def done(msg, delay=0):
    """Success: message, then reload the panel (after a restart: once the board is back)."""
    q = msg.replace('%', '').replace('&', '').replace('#', '%23').replace(' ', '+')
    return ('<p class="ok">%s</p><i hx-get="/sensors?m=%s" hx-trigger="load%s" hx-target="#sensors"></i>'
            % (esc(msg), q, ' delay:%ds' % delay if delay else ''))


def _num(f, k, lo, hi):
    """Number field k of form f within lo..hi (ValueError otherwise)."""
    v = float(f.get(k, ''))
    if not lo <= v <= hi:
        raise ValueError(k)
    return v


def _soil_changed(old, new):
    """Soil list changed (add/remove/reorder): remap pump sensors, bands F, MQTT fields; reset the trend."""
    oi = [e['id'] for e in old if e['type'] == 'soil']
    ni = [e['id'] for e in new if e['type'] == 'soil']
    if oi == ni:
        return
    d = S.DATA_DIR + '/'
    full = list(app.bands.full)
    nf = [full[oi.index(x)] if x in oi and oi.index(x) < len(full) else 70 for x in ni]
    _jupdate(d + 'bands.json', lambda c: c.update(full=nf, link=False))
    if app.pump:
        ps = [ni.index(oi[i]) for i in app.pump.cfg['sensors'] if i < len(oi) and oi[i] in ni]
        app.pump.save(sensors=ps or [0])
    try:
        os.remove(d + 'trend.json')               # per-sensor history no longer lines up
    except OSError:
        pass


def _mqtt_fields(add=None, drop=None):
    """Keep an explicit MQTT selection in step: new sensors are sent, removed ones dropped."""
    def fix(c):
        """Adjust one MQTT settings dict."""
        f = c.get('fields') or []
        if not f:
            return                              # empty = everything, nothing to do
        if drop:
            f = [x for x in f if x != 'sen_' + drop]
        if add:
            f.append('sen_' + add)
        c['fields'] = f
    _jupdate(S.DATA_DIR + '/mqtt.json', fix)
    if app.mqtt:
        fix(app.mqtt.cfg)


def _jupdate(path, fn):
    """Read-modify-write a JSON settings file with fn."""
    try:
        with open(path) as f:
            c = json.load(f)
    except (OSError, ValueError):
        return
    fn(c)
    with open(path + '.tmp', 'w') as f:
        json.dump(c, f)
    os.rename(path + '.tmp', path)


def _restart(lst, msg, columns):
    """Save the list, stop a recording if the CSV columns change, restart the board."""
    import sensors as R
    R.save(lst)
    app.sens.reg = lst                        # further edits before the restart build on this list
    if columns and (app.rec.active or app.rec.resume):
        app.rec.stop('Stopped: sensors changed (new CSV columns)')
    app.restart_soon()
    return done(msg + ' The board restarts to apply it - this panel reloads in about 25 s.', 25)


def handle(method, path, f):
    """POST /sensors/save | del | cap | cal | air (dispatched through ACTIONS)."""
    import sys
    import sensors as R
    try:
        fn = ACTIONS.get(path)
        return fn(R, app.sens, f) if fn else None
    finally:
        R = None
        for m in ('sensors', 'sensor_types'):
            sys.modules.pop(m, None)


def _first(it):
    """First item of an iterable, None if empty (MicroPython's next() has no default argument)."""
    for x in it:
        return x
    return None


def _entry(lst, sid):
    """The registry entry with id sid, or None."""
    return _first(x for x in lst if x['id'] == sid)


def _cap(R, sn, f):
    """Soil sensor: take the current raw reading as its dry or wet point."""
    sid, w = f.get('id'), f.get('w')
    i = _first(i for i, c in enumerate(sn.ch) if c[5] == sid)
    if i is None or w not in ('dry', 'wet'):
        return bad('Sensor not active yet.')
    c, e, raw = sn.ch[i], _entry(sn.reg, sid), sn.raw[i]
    dry, wet = (raw, e['wet']) if w == 'dry' else (e['dry'], raw)
    if dry < wet + 200:                        # e.g. "Wet = now" pressed with the probe in air
        return bad('Not saved: %s %s = %d would leave dry %d / wet %d (dry must be well above wet). '
                   'Wet = probe in water to the line, Dry = probe in air.' % (e['name'], w, raw, dry, wet))
    e[w] = raw
    R.save(sn.reg)
    sn.ch[i] = (c[0], c[1], e['dry'], e['wet'], c[4], sid)
    return done('%s: %s = %d.' % (e['name'], w, raw))


def _cal(R, sn, f):
    """Driver calibration button (MQ R0, ACS758 zero, NTC / MAX6675 offset): store the driver's value."""
    sid = f.get('id')
    d = _first(d for d in sn.gen if d.e['id'] == sid and hasattr(d, 'cal'))
    if d is None:
        return bad('Sensor not running.')
    try:
        key, val = d.cal()
    except Exception as ex:
        return bad('Not calibrated: %s' % ex)
    e = _entry(sn.reg, sid)
    e[key] = val
    d.e = e
    R.save(sn.reg)
    return done('%s: %s = %s.' % (e['name'], key, val))


def _air(R, sn, f):
    """Built-in air sensor: remove / add back (restart)."""
    on = f.get('on') == '1'
    R.save(sn.reg, on)
    return _restart(sn.reg, 'Air sensor %s.' % ('added back' if on else 'removed'), True)


def _del(R, sn, f):
    """Remove a sensor (at least one soil sensor stays); restart."""
    sid, lst = f.get('id'), sn.reg
    new = [x for x in lst if x['id'] != sid]
    if len(new) == len(lst):
        return bad('Unknown sensor.')
    if not any(x['type'] == 'soil' for x in new):
        return bad('At least one soil sensor is needed (watering bands, pump).')
    _soil_changed(lst, new)
    _mqtt_fields(drop=sid)
    return _restart(new, 'Removed.', True)


# form field -> (min, max, integer); fields not listed: any number; text fields are cleaned instead
RANGES = {'dry': (0, 4095, 1), 'wet': (0, 4095, 1), 'dec': (0, 3, 1), 'pull': (0, 1, 1), 'invert': (0, 1, 1),
          'top': (0, 1, 1), 'model': (11, 22, 1)}
POSITIVE = ('ppl', 'shunt', 'rl', 'div', 'vc', 'r0', 'mv_a', 'r25', 'rs', 'beta', 'vcc_mv', 'gain')
PIN_KEYS = {'uart': ('pin', 'pin2'), 'spi': ('pin', 'pin2', 'pin3')}


def _field(f, key, t):
    """Value of one form field, checked against RANGES / POSITIVE (ValueError if out of range)."""
    if t == 'text':
        return ''.join(ch for ch in f.get(key, '')[:8] if ch not in '<>&"\'')
    lo, hi, whole = RANGES.get(key) or ((0.001, 100000, 0) if key in POSITIVE else (-1e6, 1e6, 0))
    v = _num(f, key, lo, hi)
    if key == 'model' and v not in (11, 22):
        raise ValueError('model must be 11 or 22')
    return int(v) if whole else v


def _pins(R, T, e, kind, f, lst, cur):
    """Read and check the pin fields of a non-I2C sensor into e; returns an error text or None."""
    free = R.free_pins(kind, lst, cur and cur['id'])
    for key in PIN_KEYS.get(kind, ('pin',)):
        try:
            e[key] = int(f.get(key, ''))
        except ValueError:
            return 'Choose a pin.'
        if e[key] not in free:
            return 'GPIO%d is not free or not suitable for this type.' % e[key]
    pins = [e[k] for k in PIN_KEYS.get(kind, ())]
    if len(set(pins)) < len(pins):
        return 'The pins must be different.'
    if kind == 'uart' and not cur and sum(1 for x in lst if (T.get(x['type']) or ('', ''))[1] == 'uart') >= 2:
        return 'At most 2 UART sensors (the ESP32 has 2 free UARTs).'
    return None


def _addr(R, sn, e, lst, cur):
    """Check the I2C address field of e (hex, free on the bus) and normalise it; error text or None."""
    try:
        a = int(e['addr'].lower().replace('0x', ''), 16)
    except ValueError:
        return 'Address: hex, e.g. 38 or 0x38.'
    if not 0x08 <= a <= 0x77:
        return 'I2C address must be 0x08-0x77.'
    used = R.i2c_taken(lst, sn.air_addrs(), cur and cur['id'])
    if a in used:
        return 'Address 0x%02X is already used by %s.' % (a, used[a])
    e['addr'] = '%02x' % a
    return None


def _check(e):
    """Type-specific sanity checks of a complete entry; error text or None."""
    if e['type'] == 'soil' and e['dry'] < e['wet'] + 200:
        return 'Dry raw must be well above wet raw (at least 200): dry = probe in air, wet = in water.'
    if e['type'] == 'analog' and e['hi_mv'] == e['lo_mv']:
        return 'Input low and high mV must differ.'
    return None


def _save(R, sn, f):
    """Add or edit a sensor: new / moved sensors restart the board, names and calibration apply at once."""
    import sensor_types as T
    lst, typ = sn.reg, f.get('type', '')
    spec = T.get(typ) if typ and all(c.isalpha() or c.isdigit() for c in typ) else None
    if spec is None:
        return bad('Unknown type.')
    kind, form, drv = spec[1:4]
    name = f.get('name', '').strip()[:20]
    if not name or any(ch not in NAME_OK for ch in name):
        return bad('Name: letters, digits, space, - _ . #')
    cur = _entry(lst, f.get('id', ''))
    if not cur and len(lst) >= MAX_SENSORS:
        return bad('At most %d sensors (RAM).' % MAX_SENSORS)
    e = {'id': cur['id'] if cur else R.new_id(lst), 'type': typ, 'name': name}
    if drv:
        e['drv'] = drv                        # shared driver module, e.g. all MQ types -> drv_mq
    err = None if kind == 'i2c' else _pins(R, T, e, kind, f, lst, cur)
    try:
        for key, _, t, _ in form:
            e[key] = _field(f, key, t)
    except ValueError as ex:
        return bad('Invalid value: %s' % ex)
    err = err or (_addr(R, sn, e, lst, cur) if kind == 'i2c' else None) or _check(e)
    if err:
        return bad(err)
    if not cur:                               # new sensor
        _soil_changed(lst, lst + [e])
        _mqtt_fields(add=e['id'])
        return _restart(lst + [e], 'Added %s.' % name, True)
    if any(cur.get(k) != e.get(k) for k in ('pin', 'pin2', 'pin3', 'addr')) or typ == 'ds18b20':
        new = [e if x['id'] == cur['id'] else x for x in lst]   # moved (or 1-Wire rescan): hardware changes
        return _restart(new, 'Saved.', typ == 'soil')           # soil CSV columns are named after the pin
    cur.update(e)                             # name / calibration: apply now
    R.save(lst)
    _apply_now(sn, cur)
    return done('Saved.')


def _apply_now(sn, e):
    """Name / calibration change of a running sensor: update the sampler's tuples in place."""
    sid = e['id']
    sn.ch[:] = [(c[0], c[1], e['dry'], e['wet'], e['name'], sid) if c[5] == sid else c for c in sn.ch]
    for gi, d in enumerate(sn.gen):
        if d.e['id'] != sid:
            continue
        d.e = e
        chans = d.chans()
        js = [j for j, g in enumerate(sn.gch) if g[0] == gi]
        for k, j in enumerate(js):
            sn.gch[j] = (gi, k) + tuple(chans[k]) + (sid,)


ACTIONS = {'/sensors/cap': _cap, '/sensors/cal': _cal, '/sensors/air': _air, '/sensors/del': _del,
           '/sensors/save': _save}
