# Sensors panel (/sensors, /sensors/...) - loaded per request by app.py, then released.
# Add / edit / remove sensors of any registered type, offer only suitable free pins, capture soil
# dry/wet calibration. Adding, removing or moving a sensor to another pin restarts the board
# (everything is rebuilt from the saved list); names and calibration apply at once.
import json
import os
import app
from app import esc
import settings as S

NAME_OK = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 -_.#'
MAX_SENSORS = 8


def _live(e):
    """Current reading text for the list."""
    sn = app.sens
    for i, c in enumerate(sn.ch):
        if c[5] == e['id']:
            return 'raw %d &middot; %d%%' % (sn.raw[i], round(sn.pct(sn.raw[i], c[2], c[3])))
    for d in sn.gen:
        if d.e['id'] == e['id']:
            vals = [sn.gval[j] for j, g in enumerate(sn.gch) if g[6] == e['id']]
            return '%s &middot; %s' % (', '.join('&ndash;' if v is None else ('%%.%df' % int(e.get('dec', 1))) % v for v in vals),
                                       d.info())
    for name, err in sn.bad:
        if name == e['name']:
            return '<span class="warn">not started: %s</span>' % esc(err)
    return '<span class="muted">starts after restart</span>'


def frag(msg='', ok=False, edit=None):
    import sensors as R
    lst = app.sens.reg
    out = []
    if msg:
        out.append('<p class="%s">%s</p>' % ('ok' if ok else 'warn', esc(msg)))
    out.append('<table><tr><th>Name</th><th>Type</th><th>Pin</th><th>Reading</th><th></th></tr>')
    for e in lst:
        sid = e['id']
        act = ['<button class="small" hx-post="/sensors/edit" hx-vals=\'{"id":"%s"}\' hx-target="#sensors">Edit</button>' % sid]
        if e['type'] == 'soil':
            for w in ('dry', 'wet'):
                act.append('<button class="small" hx-post="/sensors/cap" hx-vals=\'{"id":"%s","w":"%s"}\' hx-target="#sensors" '
                           'title="Take the current raw reading as the %s point">%s = now</button>' % (sid, w, w, 'Dry' if w == 'dry' else 'Wet'))
        act.append('<span x-data="{c:0}"><button class="small" x-show="!c" @click="c=1">Remove</button><span x-show="c">Sure? '
                   '<button class="small danger" hx-post="/sensors/del" hx-vals=\'{"id":"%s"}\' hx-target="#sensors">Yes</button> '
                   '<button class="small" @click="c=0">No</button></span></span>' % sid)
        extra = ' <small>dry %d / wet %d</small>' % (e['dry'], e['wet']) if e['type'] == 'soil' else ''
        out.append('<tr><td>%s <small>%s</small></td><td>%s%s</td><td>%s</td><td>%s</td><td>%s</td></tr>'
                   % (esc(e['name']), sid, esc(R.TYPES.get(e['type'], (e['type'],))[0]), extra,
                      'GPIO%d' % e['pin'] if 'pin' in e else '-', _live(e), ' '.join(act)))
    out.append('</table>')
    # add / edit form
    cur = None
    if edit:
        cur = [e for e in lst if e['id'] == edit]
        cur = cur[0] if cur else None
    t0 = cur['type'] if cur else 'soil'
    out.append('<form class="set" hx-post="/sensors/save" hx-target="#sensors" x-data="{t:\'%s\'}">'
               '<input type="hidden" name="id" value="%s"><b style="flex-basis:100%%">%s</b>'
               % (t0, cur['id'] if cur else '', 'Edit %s' % esc(cur['name']) if cur else 'Add a sensor'))
    if cur:
        out.append('<input type="hidden" name="type" value="%s">' % t0)
    else:
        out.append('<label>Type<select name="type" x-model="t">%s</select></label>'
                   % ''.join('<option value="%s">%s</option>' % (k, esc(v[0])) for k, v in R.TYPES.items()))
    out.append('<label>Name<input name="name" maxlength="20" value="%s" required style="width:10em"></label>'
               % esc(cur['name'] if cur else ''))
    kinds = []
    for k, v in R.TYPES.items():
        if v[1] not in kinds:
            kinds.append(v[1])
    for kind in kinds:                         # one pin list per pin kind, only the matching one is sent
        types = [k for k, v in R.TYPES.items() if v[1] == kind]
        cond = '||'.join("t=='%s'" % k for k in types)
        pins = R.free_pins(kind, lst, cur['id'] if cur else None)
        opts = ''.join('<option value="%d"%s>GPIO%d</option>' % (p, ' selected' if cur and cur.get('pin') == p else '', p)
                       for p in pins)
        out.append('<label x-show="%s">Pin (%s)<select name="pin" :disabled="!(%s)">%s</select></label>'
                   % (cond, 'ADC1' if kind == 'adc' else kind, cond, opts or '<option value="">no free pin</option>'))
    for k, (label, kind, form) in R.TYPES.items():
        for key, lbl, typ, dflt in form:
            v = cur.get(key, dflt) if cur and cur['type'] == k else dflt
            out.append('<label x-show="t==\'%s\'">%s<input name="%s" %s value="%s" :disabled="t!=\'%s\'" style="width:7em"></label>'
                       % (k, esc(lbl), key, 'type="number" step="any"' if typ == 'num' else 'maxlength="8"', esc(v), k))
    out.append('<button>%s</button>%s</form>' % ('Save' if cur else 'Add',
               ' <button class="small" type="button" hx-get="/sensors" hx-target="#sensors">Cancel</button>' if cur else ''))
    taken = R.taken_pins(lst)
    out.append('<p class="muted">Pins in use: %s. Free ADC1 pins: %s. '
               'Adding, removing or moving a sensor restarts the board (about 20 s) and stops a running recording '
               '(the CSV columns change); a new sensor gets a dashboard card, an MQTT field and a CSV column. '
               'Soil: put the probe in air and press <i>Dry = now</i>, in water to the line and press <i>Wet = now</i>. '
               'Analog: two points mV &rarr; value (straight line), keep the pin under ~2.45 V.</p>'
               % (', '.join('%d %s' % (p, esc(n)) for p, n in sorted(taken.items())),
                  ', '.join(str(p) for p in R.free_pins('adc', lst)) or 'none'))
    return ''.join(out)


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
    if columns and (app.rec.active or app.rec.resume):
        app.rec.stop('Stopped: sensors changed (new CSV columns)')
    app.restart_soon()
    return frag(msg + ' The board restarts to apply it - the page reconnects in about 20 s.', True)


def _handle(method, path, f):
    import sensors as R
    sn = app.sens
    lst = sn.reg
    if method == 'GET':
        return frag() if path == '/sensors' else None
    if path == '/sensors/edit':
        return frag(edit=f.get('id'))
    if path == '/sensors/cap':                  # soil: current raw reading -> dry or wet
        sid, w = f.get('id'), f.get('w')
        for i, c in enumerate(sn.ch):
            if c[5] == sid and w in ('dry', 'wet'):
                e = [x for x in lst if x['id'] == sid][0]
                e[w] = sn.raw[i]
                R.save(lst)
                sn.ch[i] = (c[0], c[1], e['dry'], e['wet'], c[4], sid)
                warn = '' if e['dry'] > e['wet'] + 200 else ' Dry should be well above wet - check the probe.'
                return frag('%s: %s = %d.%s' % (e['name'], w, e[w], warn), not warn)
        return frag('Sensor not active yet.')
    if path == '/sensors/del':
        sid = f.get('id')
        old = [dict(x) for x in lst]
        new = [x for x in lst if x['id'] != sid]
        if len(new) == len(lst):
            return frag('Unknown sensor.')
        if not any(x['type'] == 'soil' for x in new):
            return frag('At least one soil sensor is needed (watering bands, pump).')
        _soil_changed(old, new)
        _mqtt_fields(drop=sid)
        return _restart(new, 'Removed.', True)
    if path == '/sensors/save':
        sid, typ = f.get('id', ''), f.get('type', '')
        if typ not in R.TYPES:
            return frag('Unknown type.')
        label, kind, form = R.TYPES[typ]
        name = f.get('name', '').strip()[:20]
        if not name or any(ch not in NAME_OK for ch in name):
            return frag('Name: letters, digits, space, - _ . #')
        cur = [x for x in lst if x['id'] == sid]
        cur = cur[0] if cur else None
        if not cur and len(lst) >= MAX_SENSORS:
            return frag('At most %d sensors (RAM).' % MAX_SENSORS)
        try:
            pin = int(f.get('pin', ''))
        except ValueError:
            return frag('Choose a pin.')
        if pin not in R.free_pins(kind, lst, sid or None):
            return frag('GPIO%d is not free or not suitable for this type.' % pin)
        e = {'id': cur['id'] if cur else R.new_id(lst), 'type': typ, 'name': name, 'pin': pin}
        try:
            for key, lbl, t, dflt in form:
                if t == 'text':
                    e[key] = ''.join(ch for ch in f.get(key, '')[:8] if ch not in '<>&"\'')
                elif key in ('dry', 'wet'):
                    e[key] = int(_num(f, key, 0, 4095))
                elif key == 'dec':
                    e[key] = int(_num(f, key, 0, 3))
                else:
                    e[key] = _num(f, key, -1e6, 1e6)
        except ValueError as ex:
            return frag('Invalid value: %s' % ex)
        if typ == 'soil' and e['dry'] <= e['wet']:
            return frag('Dry raw must be higher than wet raw.')
        if typ == 'analog' and e['hi_mv'] == e['lo_mv']:
            return frag('Input low and high mV must differ.')
        if not cur:                               # new sensor
            old = [dict(x) for x in lst]
            new = lst + [e]
            _soil_changed(old, new)
            _mqtt_fields(add=e['id'])
            return _restart(new, 'Added %s.' % name, True)
        if cur['pin'] != pin:                     # moved: hardware changes
            new = [e if x['id'] == sid else x for x in lst]
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
        return frag('Saved.', True)
    return None


def handle(method, path, f):
    try:
        return _handle(method, path, f)
    finally:
        import sys
        sys.modules.pop('sensors', None)      # registry module only needed during the request
