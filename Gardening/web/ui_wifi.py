# WiFi setup fragments (/wifi/...) - loaded per request by app.py, then released.
import app
from app import esc, uptime, net, dur
import settings as S


def frag_wifi_state():
    n = net
    out = []
    if n.pending:
        out.append('<p><b class="dot wait"></b>Connecting to <b>%s</b>&hellip; (%d s)</p>'
                   '<p class="muted">If this page stops responding, the board has probably connected and moved '
                   'channel: join your home WiFi again and open <a href="http://%s.local/">http://%s.local/</a>.</p>'
                   % (esc(n.pending[0]), uptime() - n.pend_at, S.HOSTNAME, S.HOSTNAME))
    elif n.sta.isconnected():
        ip = n.ip()
        out.append('<p><b class="dot ok"></b>Connected to <b>%s</b> (%d dBm)</p>'
                   '<p>Open <a href="http://%s.local/"><b>http://%s.local/</b></a> or <a href="http://%s/">http://%s/</a> '
                   'from a device on that network.</p>'
                   % (esc(n.sta.config('essid')), n.sta.status('rssi'), S.HOSTNAME, S.HOSTNAME, ip, ip))
        if n.ap.active():
            out.append('<p class="muted">The setup hotspot <b>%s</b> turns off in about %d s - switch this device back to your WiFi.</p>'
                       % (S.AP_SSID, max(0, (n.ap_off_at or uptime()) - uptime())))
    else:
        out.append('<p><b class="dot"></b>Not connected to a WiFi network.</p>')
        if n.creds:
            out.append('<p class="muted">Saved network <b>%s</b> is retried every 2 min while nobody is on the setup hotspot.</p>'
                       % esc(n.creds['ssid']))
    if n.msg:
        out.append('<p class="%s">%s</p>' % ('ok' if n.msg.startswith('Connected') else 'warn', esc(n.msg)))
    return ''.join(out)


def frag_scan():
    n = net
    if not n.scan_cache:
        return ('<p class="warn">No networks found.</p><p class="muted">The ESP32 only sees 2.4 GHz networks. '
                'You can still type a hidden network name below.</p>')
    out = ['<div class="nets">']
    for ssid, rssi, ch, auth in n.scan_cache:
        e = esc(ssid)
        bars = 4 if rssi > -55 else 3 if rssi > -67 else 2 if rssi > -78 else 1
        out.append('<button type="button" class="net" data-s="%s" @click="pick($el.dataset.s)" '
                   ':class="ssid===$el.dataset.s&&\'sel\'"><span>%s%s</span><small>%s ch%d %d dBm</small></button>'
                   % (e, e, ' &#128274;' if auth else '', '&#9602;&#9604;&#9606;&#9608;'[:bars * 7], ch, rssi))
    out.append('</div><p class="muted">Scanned %s ago.</p>' % dur(uptime() - n.scan_at))
    return ''.join(out)


def handle(method, path, q, f):
    if path == '/wifi/scan':
        if q.get('fresh') or net.scan_at is None or uptime() - net.scan_at > 30:
            net.scan()
        return frag_scan()
    if path == '/wifi/state':
        return frag_wifi_state()
    if method != 'POST':
        return None
    if path == '/wifi/connect':
        ssid = f.get('ssid', '').strip()[:32]
        if not ssid:
            net.msg = 'Choose or type a network name.'
        else:
            net.connect(ssid, f.get('password', '')[:64], True)
        return frag_wifi_state()
    if path == '/wifi/forget':
        net.forget()
        return frag_wifi_state()
    return None
