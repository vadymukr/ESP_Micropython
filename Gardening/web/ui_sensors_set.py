# Sensors panel, saving part (POST /sensors/save, /sensors/del, /sensors/cap) - loaded per request by
# app.py, then released. Returns a short message for #smsg; on success the panel reloads itself.
# Adding, removing or moving a sensor restarts the board (everything is rebuilt from the saved list);
# names and calibration apply at once.
import json
import os
import app
from app import esc
import settings as S

NAME_OK = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 -_.#'
MAX_SENSORS = 8


def bad(msg):
    return '<p class="warn">%s</p>' % esc(msg)


def done(msg, delay=0):
    """Success: message, then reload the panel (after a restart: once the board is back)."""
    q = msg.replace('%', '').replace('&', '').replace('#', '%23').replace(' ', '+')
    return ('<p class="ok">%s</p><i hx-get="/sensors?m=%s" hx-trigger="load%s" hx-target="#sensors"></i>'
            % (esc(msg), q, ' delay:%ds' % delay if delay else ''))


def _num(f, k, lo, hi):
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
    import sensors as R
    R.save(lst)
    app.sens.reg = lst                        # further edits before the restart build on this list
    if columns and (app.rec.active or app.rec.resume):
        app.rec.stop('Stopped: sensors changed (new CSV columns)')
    app.restart_soon()
    return done(msg + ' The board restarts to apply it - this panel reloads in about 25 s.', 25)


def handle(method, path, f):
    import sys
    import sensors as R
    try:
        return _do(R, path, f)
    finally:
        R = None
        for m in ('sensors', 'sensor_types'):
            sys.modules.pop(m, None)


def _do(R, path, f):
    sn = app.sens
    lst = sn.reg
    if path == '/sensors/cap':                  # soil: current raw reading -> dry or wet
        sid, w = f.get('id'), f.get('w')
        for i, c in enumerate(sn.ch):
            if c[5] == sid and w in ('dry', 'wet'):
                e = [x for x in lst if x['id'] == sid][0]
                raw = sn.raw[i]
                dry, wet = (raw, e['wet']) if w == 'dry' else (e['dry'], raw)
                if dry < wet + 200:                    # e.g. "Wet = now" pressed with the probe in air
                    return bad('Not saved: %s %s = %d would leave dry %d / wet %d (dry must be well above wet). '
                               'Wet = probe in water to the line, Dry = probe in air.' % (e['name'], w, raw, dry, wet))
                e[w] = raw
                R.save(lst)
                sn.ch[i] = (c[0], c[1], e['dry'], e['wet'], c[4], sid)
                return done('%s: %s = %d.' % (e['name'], w, raw))
        return bad('Sensor not active yet.')
    if path == '/sensors/cal':                  # driver calibration button (MQ R0, ACS758 zero, ...)
        sid = f.get('id')
        for d in sn.gen:
            if d.e['id'] == sid and hasattr(d, 'cal'):
                try:
                    key, val = d.cal()
                except Exception as ex:
                    return bad('Not calibrated: %s' % ex)
                e = [x for x in lst if x['id'] == sid][0]
                e[key] = val
                d.e = e
                R.save(lst)
                return done('%s: %s = %s.' % (e['name'], key, val))
        return bad('Sensor not running.')
    if path == '/sensors/air':                  # built-in air sensor: remove / add back
        on = f.get('on') == '1'
        R.save(lst, on)
        return _restart(lst, 'Air sensor %s.' % ('added back' if on else 'removed'), True)
    if path == '/sensors/del':
        sid = f.get('id')
        old = [dict(x) for x in lst]
        new = [x for x in lst if x['id'] != sid]
        if len(new) == len(lst):
            return bad('Unknown sensor.')
        if not any(x['type'] == 'soil' for x in new):
            return bad('At least one soil sensor is needed (watering bands, pump).')
        _soil_changed(old, new)
        _mqtt_fields(drop=sid)
        return _restart(new, 'Removed.', True)
    if path == '/sensors/save':
        sid, typ = f.get('id', ''), f.get('type', '')
        import sensor_types as T
        spec = T.get(typ) if typ and all(c.isalpha() or c.isdigit() for c in typ) else None
        if spec is None:
            return bad('Unknown type.')
        kind, form, drv = spec[1:4]
        name = f.get('name', '').strip()[:20]
        if not name or any(ch not in NAME_OK for ch in name):
            return bad('Name: letters, digits, space, - _ . #')
        cur = [x for x in lst if x['id'] == sid]
        cur = cur[0] if cur else None
        if not cur and len(lst) >= MAX_SENSORS:
            return bad('At most %d sensors (RAM).' % MAX_SENSORS)
        e = {'id': cur['id'] if cur else R.new_id(lst), 'type': typ, 'name': name}
        if drv:
            e['drv'] = drv                        # shared driver module, e.g. all MQ types -> drv_mq
        if kind != 'i2c':
            free = R.free_pins(kind, lst, sid or None)
            for key in (('pin', 'pin2') if kind == 'uart' else ('pin', 'pin2', 'pin3') if kind == 'spi' else ('pin',)):
                try:
                    p = int(f.get(key, ''))
                except ValueError:
                    return bad('Choose a pin.')
                if p not in free:
                    return bad('GPIO%d is not free or not suitable for this type.' % p)
                e[key] = p
            if kind == 'spi' and len(set((e['pin'], e['pin2'], e['pin3']))) < 3:
                return bad('SO, SCK and CS must be three different pins.')
            if kind == 'uart':
                if e['pin'] == e['pin2']:
                    return bad('RX and TX must be different pins.')
                if not cur and len([x for x in lst if (T.get(x['type']) or ('', ''))[1] == 'uart']) >= 2:
                    return bad('At most 2 UART sensors (the ESP32 has 2 free UARTs).')
        try:
            for key, lbl, t, dflt in form:
                if t == 'text':
                    e[key] = ''.join(ch for ch in f.get(key, '')[:8] if ch not in '<>&"\'')
                elif key in ('dry', 'wet'):
                    e[key] = int(_num(f, key, 0, 4095))
                elif key == 'dec':
                    e[key] = int(_num(f, key, 0, 3))
                elif key in ('pull', 'invert', 'top'):
                    e[key] = int(_num(f, key, 0, 1))
                elif key == 'model':
                    e[key] = int(_num(f, key, 11, 22))
                    if e[key] not in (11, 22):
                        raise ValueError('model must be 11 or 22')
                elif key in ('ppl', 'shunt', 'rl', 'div', 'vc', 'r0', 'mv_a', 'r25', 'rs', 'beta', 'vcc_mv', 'gain'):
                    e[key] = _num(f, key, 0.001, 100000)
                else:
                    e[key] = _num(f, key, -1e6, 1e6)
        except ValueError as ex:
            return bad('Invalid value: %s' % ex)
        if kind == 'i2c':
            try:
                a = int(e['addr'].lower().replace('0x', ''), 16)
            except ValueError:
                return bad('Address: hex, e.g. 38 or 0x38.')
            if not 0x08 <= a <= 0x77:
                return bad('I2C address must be 0x08-0x77.')
            used = R.i2c_taken(lst, sn.air_addrs(), sid or None)
            if a in used:
                return bad('Address 0x%02X is already used by %s.' % (a, used[a]))
            e['addr'] = '%02x' % a
        if typ == 'soil' and e['dry'] < e['wet'] + 200:
            return bad('Dry raw must be well above wet raw (at least 200): dry = probe in air, wet = in water.')
        if typ == 'analog' and e['hi_mv'] == e['lo_mv']:
            return bad('Input low and high mV must differ.')
        if not cur:                               # new sensor
            old = [dict(x) for x in lst]
            new = lst + [e]
            _soil_changed(old, new)
            _mqtt_fields(add=e['id'])
            return _restart(new, 'Added %s.' % name, True)
        if (cur.get('pin') != e.get('pin') or cur.get('pin2') != e.get('pin2') or cur.get('addr') != e.get('addr')
                or typ == 'ds18b20'):
            new = [e if x['id'] == sid else x for x in lst]   # moved (or 1-Wire rescan): hardware changes
            return _restart(new, 'Saved.', typ == 'soil')     # soil CSV columns are named after the pin
        cur.update(e)                             # name / calibration: apply now
        R.save(lst)
        for i, c in enumerate(sn.ch):
            if c[5] == sid:
                sn.ch[i] = (c[0], c[1], e['dry'], e['wet'], name, sid)
        for gi, d in enumerate(sn.gen):
            if d.e['id'] == sid:
                d.e = cur
                k = 0
                for j, g in enumerate(sn.gch):
                    if g[0] == gi:
                        key, lb, unit, dec = d.chans()[k]
                        sn.gch[j] = (gi, k, key, lb, unit, dec, sid)
                        k += 1
        return done('Saved.')
    return None
    return None
