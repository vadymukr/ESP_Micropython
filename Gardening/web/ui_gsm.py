# GSM panel (/gsm, /gsm/save, /gsm/test) - loaded per request by app.py, then released.
import app
from app import esc
import settings as S

PHONE_OK = '+0123456789, '


def frag(msg='', ok=False):
    from gsm import load_cfg, CALL_ACTIONS
    c = load_cfg()
    g = app.gsm
    out = []
    if getattr(S, 'MODEM_TX', None) is None:
        out.append('<p class="warn">No modem wired. TTGO T-Call V1.4: set <code>BOARD = \'ttgo-tcall\'</code> in settings.py; '
                   'a separate SIM800L module: set MODEM_TX / MODEM_RX (and PWKEY / RST if wired) there.</p>')
    out.append('<p><b>GSM: %s</b></p>' % (g.status() if g else 'off'))
    if msg:
        out.append('<p class="%s">%s</p>' % ('ok' if ok else 'warn', esc(msg)))
    sel = lambda a, b: ' selected' if a == b else ''
    chk = lambda k: ' checked' if c[k] else ''
    out.append('<form class="set" hx-post="/gsm/save" hx-target="#gsm" x-data="{on:%s,g:\'%s\'}">'
               '<label class="row" style="flex-direction:row;align-items:center;gap:6px"><input type="checkbox" name="on" '
               'value="1" x-model="on"> Use the GSM modem</label><span x-show="on" x-cloak style="display:contents">'
               '<label>Allowed phone numbers (comma separated)<input name="phones" maxlength="80" value="%s" '
               'placeholder="+97150..., +97155..." style="width:18em"></label>'
               '<label>SIM PIN (if any)<input name="pin" type="password" maxlength="8" placeholder="%s" '
               'autocomplete="off" style="width:6em"></label>'
               '<label>Max SMS per day<input name="max_day" type="number" min="1" max="50" value="%d" style="width:5em"></label>'
               '<span style="flex-basis:100%%;font-size:13px;color:var(--muted)">SMS alerts:</span>'
               % ('true' if c['on'] else 'false', c['gprs'], esc(c['phones']), 'saved' if c['pin'] else '', c['max_day']))
    for k, lbl in (('a_pump', 'Pump locked / dry run'), ('a_soil', 'Soil: water now / drought'), ('a_batt', 'Battery low')):
        out.append('<label class="row" style="flex-direction:row;align-items:center;gap:6px"><input type="checkbox" '
                   'name="%s" value="1"%s> %s</label>' % (k, chk(k), lbl))
    out.append('<span style="flex-basis:100%%;font-size:13px;color:var(--muted)">Remote control (allowed numbers only):</span>'
               '<label>A call does<select name="call">')
    for k, lbl in CALL_ACTIONS:
        out.append('<option value="%s"%s>%s</option>' % (k, sel(c['call'], k), lbl))
    out.append('</select></label><label class="row" style="flex-direction:row;align-items:center;gap:6px"><input '
               'type="checkbox" name="call_sms" value="1"%s> Confirm call actions by SMS</label>'
               '<label>SMS code (optional)<input name="code" maxlength="12" value="%s" placeholder="none" autocomplete="off" '
               'style="width:6em"></label>'
               '<label style="flex-basis:100%%">Own keywords: <i>words=command</i>, separated by <b>;</b>'
               '<input name="alias" maxlength="200" value="%s" placeholder="WATER GARDEN=PUMP 30; NIGHT=LED OFF" '
               'style="width:100%%"></label>' % (chk('call_sms'), esc(c['code']), esc(c['alias'])))
    out.append('<span style="flex-basis:100%%;font-size:13px;color:var(--muted)">MQTT over GPRS:</span>'
               '<label>Use GPRS<select name="gprs" x-model="g"><option value="off"%s>off</option>'
               '<option value="fallback"%s>when WiFi is down</option><option value="always"%s>always</option></select></label>'
               '<span x-show="g!=\'off\'" style="display:contents">'
               '<label>APN<input name="apn" maxlength="40" value="%s" style="width:10em"></label>'
               '<label>APN user<input name="apn_user" maxlength="20" value="%s" style="width:7em"></label>'
               '<label>APN password<input name="apn_pw" type="password" maxlength="20" placeholder="%s" style="width:7em"></label>'
               '</span></span><button>Save</button></form>'
               % (sel(c['gprs'], 'off'), sel(c['gprs'], 'fallback'), sel(c['gprs'], 'always'), esc(c['apn']),
                  esc(c['apn_user']), 'saved' if c['apn_pw'] else ''))
    if g and g.net and c['phones']:
        out.append('<p><button class="small" hx-post="/gsm/test" hx-target="#gsm">Send test SMS</button></p>')
    if g and g.log:
        from gsm import ICON
        out.append('<p style="font-size:13px"><b>Last messages and calls</b><br>')
        for t, kind, n, what, res in reversed(g.log):
            out.append('%s %s %s &middot; %s%s<br>' % (t, ICON.get(kind, ''), '..' + esc(n) if n != 'all' else 'to all',
                                                     esc(what), ' &rarr; ' + esc(res) if res else ''))
        out.append('</p>')
    out.append('<p class="muted">SIM800L = 2G GSM only: check that your operator still runs 2G. SMS commands from an '
               'allowed number (code first if set, e.g. <i>4711 PUMP 20</i>): <b>STATUS</b>, <b>PUMP 20</b> (seconds, pump '
               'limits apply), <b>STOP</b>, <b>RESET</b> (clear the pump lock), <b>MODE MANUAL|SCHEDULE|AUTO</b>, '
               '<b>WATER</b> (litres today), <b>LED ON|OFF|ECO</b>, <b>RGB ON|OFF|ECO</b>, <b>INFO</b>. Other numbers are '
               'ignored and their calls hung up. A call is hung up before the action, so it costs the caller nothing; '
               'calls without caller ID never act. Sender numbers can be faked: the SMS code adds a second check. '
               'Alerts are sent once per change (the same alert at most every 3 h). GPRS: the MQTT settings are used; a '
               '60 s interval is about 1 MB of data per day - use a longer one on GPRS. '
               'Switching the modem off restarts the board.</p>')
    return out


def handle(method, path, f):
    from gsm import load_cfg, save_cfg
    if method == 'GET':
        return frag() if path == '/gsm' else None
    g = app.gsm
    if path == '/gsm/test':
        if not g:
            return frag('Modem not running.')
        from gsm import status_text
        g.queue('Garden logger test SMS. ' + status_text()[:100])
        return frag('Test SMS queued to all allowed numbers.', True)
    if path != '/gsm/save':
        return None
    c = load_cfg()
    was = c['on']
    on = f.get('on') == '1'
    if on:
        phones = f.get('phones', '').strip()[:80]
        if any(ch not in PHONE_OK for ch in phones):
            return frag('Phone numbers: digits, +, commas only.')
        try:
            mx = int(f.get('max_day', 10))
        except ValueError:
            return frag('Invalid number.')
        if not 1 <= mx <= 50 or f.get('gprs') not in ('off', 'fallback', 'always'):
            return frag('Out of range.')
        apn = f.get('apn', '').strip()[:40]
        if f.get('gprs') != 'off' and not apn:
            return frag('GPRS needs the APN of your operator.')
        code = f.get('code', '').strip()
        alias = f.get('alias', '').strip()[:200]
        if ' ' in code or any(ch in code for ch in '=;'):
            return frag('SMS code: one word, no spaces.')
        for a in alias.split(';'):
            if a.strip() and (a.count('=') != 1 or not a.split('=')[0].strip() or not a.split('=')[1].strip()):
                return frag('Own keywords: words=command; words=command')
        if f.get('call') not in ('off', 'status', 'pump', 'toggle'):
            return frag('Out of range.')
        c.update(on=True, phones=phones, max_day=mx, gprs=f.get('gprs'), apn=apn, code=code, alias=alias,
                 call=f.get('call'), call_sms=f.get('call_sms') == '1',
                 apn_user=f.get('apn_user', '').strip()[:20],
                 a_pump=f.get('a_pump') == '1', a_soil=f.get('a_soil') == '1', a_batt=f.get('a_batt') == '1')
        for k in ('pin', 'apn_pw'):                 # empty field keeps the saved secret
            if f.get(k):
                c[k] = f.get(k)[:20]
    else:
        c['on'] = False
    save_cfg(c)
    if g:
        g.c = c                                     # phones, alerts, limits, GPRS mode apply at once
    if on and not g:
        app.gsm_start()
        return frag('Saved. Starting the modem (about 20-60 s until it is on the network).', True)
    if not on and was:
        app.restart_soon()
        return frag('Saved. The board restarts to switch the modem off.', True)
    return frag('Saved.', True)
