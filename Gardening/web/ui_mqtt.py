# MQTT panel (/mqtt, /mqtt/...) - loaded per request by app.py, then released.
# Spec: docs/SPEC.md#mqtt
import app
from app import esc
import settings as S

HOST_OK = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-'


def _cfg():
    """Saved settings; while MQTT is off the mqtt module is released again (no RAM kept)."""
    if app.mqtt:
        return app.mqtt.cfg
    import sys
    from mqtt_cfg import load_cfg
    c = load_cfg()
    del sys.modules['mqtt']
    return c


def _fields(c):
    """'Data to send' checkboxes."""
    sn = app.sens
    opts = [('sen_' + c[5], c[4]) for c in sn.ch] + [('sen_' + d.e['id'], d.e['name']) for d in sn.gen] + [
        ('air', 'Air (temp, RH, pressure)'), ('pump', 'Pump state + events'), ('batt', 'Battery'),
        ('control', 'Controllers (heater, light, fan)'), ('board', 'Board (WiFi, memory, uptime)')]
    out = ['<span style="flex-basis:100%;font-size:13px;color:var(--muted)">Data to send:</span>']
    for k, lbl in opts:
        out.append('<label class="row" style="flex-direction:row;align-items:center;gap:6px"><input type="checkbox" '
                   'name="d_%s" value="1"%s> %s</label>' % (k, ' checked' if not c['fields'] or k in c['fields'] else '',
                                                           esc(lbl)))
    return ''.join(out)


def frag_mqtt(msg='', ok=False):
    """MQTT panel: broker, topics, interval, data selection, status."""
    m = app.mqtt
    c = _cfg()
    # status line kept current by the 5-s /status poll (Alpine: mqs / mqt in index.html)
    out = ['<p><b>MQTT: <span :class="mqs==1?\'ok\':mqs==2?\'warn\':\'\'" x-text="mqt">%s</span></b></p>'
           % esc(m.status() if m else 'off')]
    if msg:
        out.append('<p class="%s">%s</p>' % ('ok' if ok else 'warn', esc(msg)))
    out.append('<form class="set" hx-post="/mqtt/save" hx-target="#mqtt" x-data="{on:%s}">'
               '<label class="row" style="flex-direction:row;align-items:center;gap:6px"><input type="checkbox" name="on" '
               'value="1" x-model="on"> Send data to MQTT</label>'
               '<span x-show="on" x-cloak style="display:contents">'
               '<label>Server (IP or name)<input name="host" maxlength="64" value="%s" placeholder="192.168.1.10" style="width:12em"></label>'
               '<label>Port<input name="port" type="number" min="1" max="65535" value="%d" style="width:6em"></label>'
               '<label>User (optional)<input name="user" maxlength="32" value="%s" autocomplete="off" style="width:9em"></label>'
               '<label>Password<input name="pw" type="password" maxlength="64" placeholder="%s" autocomplete="new-password" style="width:9em"></label>'
               '<label>Client ID<input name="cid" maxlength="23" value="%s" style="width:10em"></label>'
               '<label>Topic prefix<input name="base" maxlength="40" value="%s" style="width:9em"></label>'
               '<label>Send every, s<input name="every" type="number" min="10" max="3600" value="%d" style="width:6em"></label>'
               '<label class="row" style="flex-direction:row;align-items:center;gap:6px"><input type="checkbox" name="retain" '
               'value="1"%s> Retain state</label>%s</span><button>Save</button></form>'
               % ('true' if c['on'] else 'false', esc(c['host']), c['port'], esc(c['user']),
                  'saved - leave empty to keep' if c['pw'] else '', esc(c['cid']), esc(c['base']), c['every'],
                  ' checked' if c['retain'] else '', _fields(c)))
    if m and m.connected:
        out.append('<p><button class="small" hx-post="/mqtt/now" hx-target="#mqtt">Send now</button></p>')
    b = esc(c['base'])
    out.append('<p class="muted">Topics: <code>%s/state</code> (JSON with the ticked items &ndash; every '
               'interval and right after pump events; an unticked soil sensor is sent as null), <code>%s/pump/event</code> (pump log lines), <code>%s/status</code> '
               '(online / offline, retained). Plain MQTT 3.1.1, no TLS: use it on your own network. Server name or IP; '
               'a name is looked up in the background. Values are averages since the previous send. Not sent in sleep-cycle quick wakes.</p>'
               % (b, b, b))
    return ''.join(out)


def mqtt_post(path, f):
    """Save / send-now / test actions of the MQTT panel."""
    if path == '/mqtt/now':
        if app.mqtt:
            app.mqtt.next = 0
        return frag_mqtt('Sending.', True)
    if path != '/mqtt/save':
        return None
    old = _cfg()
    on = f.get('on') == '1'
    kw = {'on': on}
    if on:
        host = f.get('host', '').strip()
        base = f.get('base', '').strip().strip('/')
        cid = f.get('cid', '').strip()
        try:
            port, every = int(f.get('port', 1883)), int(f.get('every', 60))
        except ValueError:
            return frag_mqtt('Invalid number.')
        if not host or len(host) > 64 or any(ch not in HOST_OK for ch in host):
            return frag_mqtt('Server: IP address or host name (letters, digits, dot, dash).')
        if not base or len(base) > 40 or any(ch in '#+ ' for ch in base):
            return frag_mqtt('Topic prefix: no spaces, # or +.')
        if not cid or len(cid) > 23 or any(ch not in HOST_OK + '_' for ch in cid):
            return frag_mqtt('Client ID: up to 23 letters, digits, dot, dash, underscore.')
        if not (1 <= port <= 65535 and 10 <= every <= 3600):
            return frag_mqtt('Out of range.')
        sn = app.sens
        keys = ['sen_' + c[5] for c in sn.ch] + ['sen_' + d.e['id'] for d in sn.gen] + ['air', 'pump', 'batt', 'control', 'board']
        fields = [k for k in keys if f.get('d_' + k) == '1']
        if len(fields) == len(keys):
            fields = []                   # everything ticked = send everything, also sensors added later
        if not fields:
            return frag_mqtt('Tick at least one item under "Data to send".')
        kw.update(host=host, port=port, user=f.get('user', '').strip()[:32], cid=cid, base=base, every=every,
                  retain=f.get('retain') == '1', fields=fields)
        pw = f.get('pw', '')[:64]
        if pw or not kw['user']:
            kw['pw'] = pw                 # empty field keeps the saved password (unless no user)
    if app.mqtt:
        app.mqtt.save(**kw)
    else:
        import json
        import os
        old.update(kw)
        tmp = S.DATA_DIR + '/mqtt.json.tmp'
        with open(tmp, 'w') as fh:
            json.dump(old, fh)
        os.rename(tmp, S.DATA_DIR + '/mqtt.json')
        if not on:
            return frag_mqtt('Saved (off).', True)
        app.mqtt_start()                 # loads mqtt.py and starts the task with the saved file
    return frag_mqtt('Saved.' + (' Connecting...' if on else ' MQTT off.'), True)


def handle(method, path, f):
    """Route entry for app/server.py: GET shows the panel, POST saves; returns HTML (str / list) or None."""
    if method == 'GET':
        return frag_mqtt() if path == '/mqtt' else None
    return mqtt_post(path, f)
