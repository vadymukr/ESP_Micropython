# HTTP server: request parsing with hard limits, routing to the dashboard fragments (pages.py) and the
# settings panels (ui_*.py, imported per request and released), static files streamed from flash.
# Spec: docs/SPEC.md#http-routes
try:
    import asyncio
except ImportError:
    import uasyncio as asyncio
import gc
import os
import settings as S
import app
from util import has, time_ok, safe_csv, clean, set_clock, parse_qs, heap_note
from pages import frag_live, frag_status, ui

WWW = 'www/'
TXT = 'text/plain'
HTML = 'text/html; charset=utf-8'
STATIC = {
    '/': ('index.html.gz', HTML, 'no-store'),
    '/htmx.min.js': ('htmx.min.js.gz', 'application/javascript', 'max-age=604800'),
    '/alpine.min.js': ('alpine.min.js.gz', 'application/javascript', 'max-age=604800'),
    '/setup': ('setup.html.gz', HTML, 'no-store'),
}
POLLS = ('/live', '/status', '/tail', '/files', '/wifi/state', '/favicon.ico', '/htmx.min.js', '/alpine.min.js')
KNOWN = ('/features', '/display', '/control', '/live', '/status', '/tail', '/files', '/dl', '/wifi/', '/rec/', '/time',
         '/favicon', '/bands', '/leds', '/power', '/pump', '/mqtt', '/batt', '/sensors', '/rgb', '/gsm')
CAPTIVE = ('/generate_204', '/gen_204', '/hotspot-detect.html', '/library/test/success.html',
           '/ncsi.txt', '/connecttest.txt', '/redirect', '/success.txt', '/canonical.html')
# panel URL segment -> (feature it needs or None, module)
PANELS = {'bands': (None, 'ui_cfg'), 'leds': ('leds', 'ui_cfg'), 'power': ('power', 'ui_cfg'),
          'pump': ('pump', 'ui_pump'), 'mqtt': ('mqtt', 'ui_mqtt'), 'batt': ('battery', 'ui_batt'),
          'sensors': (None, 'ui_sensors'), 'rgb': ('rgb', 'ui_rgb'), 'gsm': ('gsm', 'ui_gsm'),
          'display': ('display', 'ui_display'), 'control': ('control', 'ui_control')}
SUBPAGES = {'/sensors/save': 'ui_sensors_set', '/sensors/del': 'ui_sensors_set', '/sensors/cap': 'ui_sensors_set',
            '/sensors/cal': 'ui_sensors_set', '/sensors/air': 'ui_sensors_set', '/control/form': 'ui_control_form'}
# helper modules a panel may import only for its settings: released again while the feature is off
# (module -> app attribute holding the running object)
HELPERS = (('gsm', 'gsm'), ('gsm_cfg', 'gsm'), ('rgb', 'rgb'), ('mqtt', 'mqtt'), ('battery', 'batt'), ('leds', 'leds'),
           ('display', 'disp'), ('display_cfg', 'disp'), ('gfont', 'disp'), ('control', 'ctrl'), ('control_cfg', 'ctrl'),
           ('pump_cfg', 'pump'), ('mqtt_cfg', 'mqtt'))
conns = 0


async def send(w, body, ctype=HTML, status='200 OK', extra=''):
    """Send a response; a list body is streamed part by part (no big joined copy in RAM)."""
    if isinstance(body, list):
        w.write(('HTTP/1.1 %s\r\nContent-Type: %s\r\nCache-Control: no-store\r\nConnection: close\r\n%s\r\n'
                 % (status, ctype, extra)).encode())
        for i, p in enumerate(body):
            w.write(p.encode())
            if i % 8 == 7:
                await w.drain()
        await w.drain()
        return
    if isinstance(body, str):
        body = body.encode()
    w.write(('HTTP/1.1 %s\r\nContent-Type: %s\r\nContent-Length: %d\r\n'
             'Cache-Control: no-store\r\nConnection: close\r\n%s\r\n' % (status, ctype, len(body), extra)).encode())
    w.write(body)
    await w.drain()


async def send_file(w, path, ctype, extra='', cache='no-store'):
    """Stream a file from flash in 512-byte chunks (404 if missing)."""
    try:
        size = os.stat(path)[6]
    except OSError:
        return await send(w, 'Not found', TXT, '404 Not Found')
    w.write(('HTTP/1.1 200 OK\r\nContent-Type: %s\r\nContent-Length: %d\r\n'
             'Cache-Control: %s\r\nConnection: close\r\n%s\r\n' % (ctype, size, cache, extra)).encode())
    await w.drain()
    buf = bytearray(512)
    with open(path, 'rb') as f:
        while True:
            n = f.readinto(buf)
            if not n:
                break
            w.write(buf if n == 512 else buf[:n])   # stream copies on partial write
            await w.drain()


def panel(method, path, q, f):
    """A settings panel request: HTML (str / list), '' for a hidden sub-panel, or None (not a panel)."""
    import sys
    seg = path.split('/')[1]
    need, name = PANELS.get(seg, (None, None))
    if name is None:
        return None
    if need and not has(need):
        return '' if seg in ('leds', 'rgb') else '<p class="muted">Switched off in <b>Features</b> (not loaded, no RAM used).</p>'
    name = SUBPAGES.get(path, name)
    gc.collect()                                   # loaded per request, then released
    mod = __import__(name)
    try:
        return mod.handle(method, path, f if method == 'POST' else q)
    finally:
        mod = None
        del sys.modules[name]
        for m, o in HELPERS:
            if m in sys.modules and getattr(app, o) is None:
                del sys.modules[m]


async def action(w, path, f):
    """POST actions outside the panels: recording, file delete, browser clock, WiFi."""
    rec = app.rec
    if path == '/rec/start':
        try:
            interval, hours = int(f.get('interval', 60)), float(f.get('hours', 0))
        except ValueError:
            interval, hours = 0, -1
        if interval not in S.INTERVALS or not 0 <= hours <= S.MAX_HOURS:
            return await send(w, frag_status('Invalid interval or duration.'))
        msg = rec.start(interval, hours, clean(f.get('label', ''), 20))
        return await send(w, frag_status(msg) + ui('ui_files', 'files_oob'))
    if path == '/rec/stop':
        if rec.active or rec.resume:
            rec.stop()
        return await send(w, frag_status() + ui('ui_files', 'files_oob'))
    if path == '/files/delete':
        name = safe_csv(f.get('f'))
        if name and not (name == rec.fname and (rec.active or rec.resume)):
            try:
                os.remove(S.DATA_DIR + '/' + name)
                if name == rec.fname:
                    rec.fname = ''
            except OSError:
                pass
        return await send(w, ui('ui_files', 'frag_files'))
    if path == '/time':
        try:
            t = int(f.get('t', 0))
        except ValueError:
            t = 0
        if not time_ok() and t > 1735689600:      # only if NTP has not set it (>= 2025)
            set_clock(t)
        return await send(w, b'', TXT, '204 No Content')
    if path in ('/wifi/connect', '/wifi/forget'):
        return await send(w, ui('ui_wifi', 'handle', 'POST', path, {}, f))
    return await send(w, 'Not found', TXT, '404 Not Found')


async def route(w, method, path, q, f):
    """Dispatch one request (q = query, f = form body)."""
    net = app.net
    if method == 'GET':
        if path == '/' and not net.sta.isconnected():
            path = '/setup'                     # no WiFi yet: open the WiFi manager
        elif path in CAPTIVE or (net.ap.active() and path not in STATIC and not any(path.startswith(k) for k in KNOWN)):
            return await send(w, b'', TXT, '302 Found', 'Location: http://%s/setup\r\n' % net.ap.ifconfig()[0])
        if path in ('/wifi/scan', '/wifi/state'):
            return await send(w, ui('ui_wifi', 'handle', method, path, q, f))
        st = STATIC.get(path)
        if st:
            return await send_file(w, WWW + st[0], st[1], 'Content-Encoding: gzip\r\n', st[2])
    if path == '/live':
        return await send(w, frag_live())
    if path == '/status':
        return await send(w, frag_status())
    if path in ('/tail', '/files'):
        return await send(w, ui('ui_files', 'frag_' + path[1:]))
    if path.startswith('/features'):
        return await send(w, ui('ui_feat', 'handle', method, path, f))
    html = panel(method, path, q, f)
    if html is not None:
        return await send(w, html)
    if path == '/dl':
        name = safe_csv(q.get('f'))
        if not name:
            return await send(w, 'Bad name', TXT, '400 Bad Request')
        return await send_file(w, S.DATA_DIR + '/' + name, 'text/csv',
                               'Content-Disposition: attachment; filename="%s"\r\n' % name)
    if path == '/favicon.ico':
        return await send(w, b'', TXT, '204 No Content')
    if method != 'POST':
        return await send(w, 'Not found', TXT, '404 Not Found')
    return await action(w, path, f)


async def _busy(w):
    """Too many open connections: a short 503 and close."""
    try:
        w.write(b'HTTP/1.1 503 Busy\r\nContent-Length: 0\r\nRetry-After: 1\r\nConnection: close\r\n\r\n')
        await w.drain()
        w.close()
        await w.wait_closed()
    except Exception:
        pass


async def _read_request(r):
    """(method, target, body) of one request, or None if it breaks the limits (line, headers, body)."""
    line = await asyncio.wait_for(r.readline(), 5)
    parts = line.split()
    if len(line) > 512 or len(parts) < 2:
        return None
    clen = 0
    for _ in range(40):
        h = await asyncio.wait_for(r.readline(), 5)
        if not h or h == b'\r\n':
            break
        if len(h) > 1024:
            return None
        if h[:15].lower() == b'content-length:':
            clen = int(h[15:].strip())
    if clen > S.MAX_BODY:
        return parts[0].decode(), parts[1].decode(), None
    body = (await asyncio.wait_for(r.readexactly(clen), 5)).decode() if clen else ''
    return parts[0].decode(), parts[1].decode(), body


async def handle(r, w):
    """asyncio.start_server callback: one request per connection, memory errors answered with 503."""
    global conns
    if conns >= S.MAX_CONN:
        return await _busy(w)
    conns += 1
    try:
        req = await _read_request(r)
        if req:
            method, target, body = req
            if body is None:
                return await send(w, 'Too large', TXT, '413 Payload Too Large')
            path, _, qs = target.partition('?')
            if app.pwr and (method == 'POST' or path not in POLLS):
                app.pwr.activity()          # real use keeps a sleep-cycle board awake; polling does not
            await route(w, method, path, parse_qs(qs), parse_qs(body))
    except MemoryError:
        gc.collect()
        try:
            await send(w, 'Low memory, retry', TXT, '503 Service Unavailable')
        except Exception:
            pass
    except Exception as e:
        if not isinstance(e, (OSError, asyncio.TimeoutError)):
            print('http error:', repr(e))
    finally:
        conns -= 1
        try:
            w.close()
            await w.wait_closed()
        except Exception:
            pass
        gc.collect()
        heap_note()
