# Dashboard HTML fragments polled by the page: /live (sensor cards, every 2 s) and /status (recording
# and system lines, every 5 s). Always loaded; settings panels live in ui_*.py and load per request.
# Spec: docs/SPEC.md#dashboard
import gc
import time
import settings as S
import app
import util
from util import esc, dur, kb, stamp, time_ok, uptime, flash_free, has

BAND_ORDER = (('dry', 'Drought stress: '), ('now', 'Water now: '), ('wet', 'Too wet: '),
              ('soon', 'Water soon: '), ('drain', 'Draining after watering: '))


def band_scale(v, F, w):
    """Zones dry..wet left to right, current-value marker and F tick."""
    cuts = [0] + [max(0, min(100, x)) for x in (F - w - 20, F - w - 10, F - w, F + 5)] + [100]
    z = ''.join('<i class="%s" style="width:%d%%"></i>' % (k, cuts[j + 1] - cuts[j])
                for j, k in enumerate(('dry', 'now', 'soon', 'good', 'wet')))
    return ('<div class="scale">%s<b class="mark" style="left:%d%%"></b><span class="f" style="left:%d%%">F %d</span></div>'
            % (z, max(0, min(100, round(v))), F, F))


def _soil_cards(worst):
    """One card per soil sensor; fills worst = {band: [sensor names]} for the alert line."""
    from bands import BANDS
    s, b = app.sens, app.bands
    w = b.w()
    cards = []
    for i, c in enumerate(s.ch):
        raw = s.raw[i]
        p = s.pct(raw, c[2], c[3])
        k = b.band(i, p)
        worst.setdefault(k, []).append(c[4])
        nxt, est = b.next_text(i, p, k)
        cards.append('<div class="card band %s"><h3><span>%s <small>GPIO%d</small></span><span class="badge">%s</span></h3>'
                     '<div class="big">%d<small>%%</small></div>%s'
                     '<dl class="facts"><dt>Full (F)</dt><dd>%d%% &middot; water at %d%%</dd>'
                     '<dt>Next watering</dt><dd>%s%s</dd><dt>Trend 24 h</dt><dd>%s</dd></dl>'
                     '<p class="muted">raw %d &middot; dry %d &middot; wet %d</p></div>'
                     % (k, esc(c[4]), c[0], BANDS[k], round(p), band_scale(p, b.full[i], w),
                        b.full[i], b.full[i] - w, nxt, ' <small>&middot; estimated</small>' if est else '',
                        b.trend_text(i), raw, c[2], c[3]))
    return cards


def _alert(worst):
    """The coloured line above the cards: worst band, pump lock, low battery or heat."""
    s, pump, batt = app.sens, app.pump, app.batt
    hot = s.t is not None and s.t > 32
    alert = ''
    for k, lead in BAND_ORDER:
        if k in worst:
            alert = '<div class="alert %s">%s%s%s</div>' % (k, lead, ' and '.join(esc(x) for x in worst[k]),
                    ' &middot; hot (%.0f &deg;C): water early morning' % s.t if hot and k in ('soon', 'now', 'dry') else '')
            break
    if pump and pump.cfg['fault']:
        alert = ('<div class="alert dry">Pump stopped: %s Refill / check, then press <b>Reset</b> in Watering pump.</div>'
                 % esc(pump.cfg['fault'])) + alert
    if batt and batt.state != 'ok' and batt.v is not None:
        alert = ('<div class="alert %s">Battery %s: %.2f V &ndash; recharge or replace soon.</div>'
                 % ('now' if batt.state == 'crit' else 'soon', 'critical' if batt.state == 'crit' else 'low', batt.v)) + alert
    if not alert and hot:
        alert = ('<div class="alert now">Hot: %.0f &deg;C &ndash; above 32 &deg;C cucumbers drop flowers. '
                 'Shade 08:00&ndash;12:00.</div>' % s.t)
    return alert


def _sensor_cards():
    """Registry sensors (one card per channel), sensors that did not start, and the air sensor."""
    s, out = app.sens, []
    for j, g in enumerate(s.gch):
        v = s.gval[j]
        out.append('<div class="card"><h3><span>%s <small>%s</small></span></h3><div class="big">%s<small>%s</small></div>'
                   '<p class="muted">%s</p></div>'
                   % (esc(g[3]), g[6], '&ndash;' if v is None else ('%%.%df' % g[5]) % v,
                      g[4],                            # unit: driver text, or sanitised on save; may hold &deg;
                      s.gen[g[0]].info()))
    for name, err in s.bad:
        out.append('<div class="card"><h3>%s</h3><div class="big">&ndash;</div><p class="warn">not started: %s</p></div>'
                   % (esc(name), esc(err)))
    if s.t is not None:
        extra = []
        if s.rh is not None:
            extra.append('%.0f%% RH' % s.rh)
        if s.p is not None:
            extra.append('%.1f hPa' % s.p)
        k, lbl = (('now', 'Too hot') if s.t > 32 else ('soon', 'Warm') if s.t > 28 else
                  ('soon', 'Too cold') if s.t < 15 else ('good', 'Good'))
        out.append('<div class="card band %s"><h3><span>Air <small>%s</small></span><span class="badge">%s</span></h3>'
                   '<div class="big">%.1f<small>&deg;C</small></div><p class="muted">%s</p>'
                   '<dl class="facts"><dt>Cucumbers</dt><dd>22&ndash;28 &deg;C day &middot; 60&ndash;80%% RH</dd></dl></div>'
                   % (k, esc(s.clim_name), lbl, s.t, ' &middot; '.join(extra)))
    elif s.air_on:
        err = s.clim_err or 'starting'
        if s.clim is None and s._next_try > uptime():
            err += ' - retry in %s' % dur(s._next_try - uptime())
        out.append('<div class="card"><h3>Air <small>%s</small></h3><div class="big">&ndash;</div>'
                   '<p class="warn">%s</p></div>' % (esc(s.clim_name), esc(err)))
    return out


def _device_cards():
    """Battery, pump, GSM and controller cards (only for the parts that are running)."""
    batt, pump, out = app.batt, app.pump, []
    if batt:
        if batt.v is None:
            out.append('<div class="card"><h3>Battery <small>GPIO%d</small></h3><div class="big">&ndash;</div></div>' % batt.pin)
        else:
            k, lbl = {'ok': ('good', 'OK'), 'low': ('soon', 'Low'), 'crit': ('now', 'Critical')}[batt.state]
            out.append('<div class="card band %s"><h3><span>Battery <small>GPIO%d</small></span><span class="badge">%s</span></h3>'
                       '<div class="big">%.2f<small>V</small></div><p class="muted">low %.1f V &middot; critical %.1f V'
                       ' &middot; pin %.2f V</p></div>'
                       % (k, batt.pin, lbl, batt.v, batt.low_v, batt.crit_v, batt.pin_v or 0))
    if pump:
        c = pump.cfg
        out.append('<div class="card band %s"><h3><span>Pump <small>GPIO%d</small></span><span class="badge">%s</span></h3>'
                   '<p>%s</p><p class="muted">today %d of %d s%s</p></div>'
                   % ('dry' if c['fault'] else 'wet' if pump.busy() else 'good', S.PUMP_PIN,
                      'Fault' if c['fault'] else 'On' if pump.running else 'Ready',
                      pump.state_text(), c['day_s'], S.PUMP_DAY_MAX_S,
                      ' &middot; %.2f L &middot; total %.1f L' % (c['l_day'], c['l_total'])
                      if c['flow'] or c['rate_lpm'] else ''))
    if app.gsm:
        out.append(app.gsm.card())
    if app.ctrl:
        out.append(app.ctrl.card())
    return out


def frag_live():
    """/live: alert line + all cards + the 'sampled ... ago' footer, as a list of parts (streamed by
    server.send: no joined copy of the whole fragment in RAM)."""
    from bands import STAGES
    worst = {}
    cards = _soil_cards(worst)
    out = [_alert(worst), '<div class="grid">'] + cards + _sensor_cards() + _device_cards()
    b = app.bands
    note = ' &middot; %s' % esc(b.msg) if b.msg and uptime() < 3600 else ''
    out.append('</div><p class="muted">Sampled %s &middot; <span x-text="age"></span> s ago &middot; stage: %s%s</p>'
               '<i hidden x-init="seen()"></i>' % (stamp() if time_ok() else '(clock not set)', esc(STAGES[b.stage][0]), note))
    return out


def _rec_lines(r):
    """Recording state lines for /status."""
    if not has('recorder'):
        return []
    if r.active:
        line = 'every %s &middot; %d rows &middot; running %s' % (dur(r.interval), r.rows, dur(time.time() - r.started))
        if r.end:
            line += ' &middot; ends %s (in %s)' % (stamp(r.end, False), dur(max(0, r.end - time.time())))
        else:
            line += ' &middot; until stopped'
        return ['<p><b class="dot on"></b><b>Recording</b> <code>%s</code><br><span class="muted">%s</span></p>'
                % (esc(r.fname), line)]
    if r.resume:
        return ['<p><b class="dot wait"></b><b>Waiting for clock</b> to resume <code>%s</code></p>' % esc(r.fname)]
    return ['<p><b class="dot"></b>Not recording</p>']


def _sys_line():
    """The system facts: WiFi, internet, clock, heap, flash, uptime, power, MQTT, GSM."""
    n, mqtt, pwr, gsm = app.net, app.mqtt, app.pwr, app.gsm
    if n.sta.isconnected():
        wifi = '%s &middot; %s &middot; %s.local (%d dBm)' % (esc(n.sta.config('essid')), n.ip(), S.HOSTNAME, n.sta.status('rssi'))
    elif n.pending:
        wifi = 'connecting to %s&hellip;' % esc(n.pending[0])
    else:
        wifi = 'not connected - <a href="/setup">set up WiFi</a>'
    if n.ap.active():
        wifi += ' &middot; setup AP %s %s' % (S.AP_SSID, n.ap.ifconfig()[0])
    if n.ntp_at is not None:
        inet = 'OK (NTP %s ago)' % dur(uptime() - n.ntp_at)
    else:
        inet = 'no' + (' (%s)' % esc(n.ntp_err) if n.ntp_err else '')
    return ('<dl class="sys"><dt>WiFi</dt><dd>%s</dd><dt>Internet</dt><dd>%s</dd>'
            '<dt>Clock</dt><dd>%s %s (%s)</dd><dt>Heap</dt><dd>%s free, lowest %s</dd>'
            '<dt>Flash</dt><dd>%s free</dd><dt>Uptime</dt><dd>%s</dd>%s</dl>'
            % (wifi, inet, stamp() if time_ok() else 'not set', S.TZ_LABEL, util.clock_src,
               kb(gc.mem_free()), kb(util.heap_min), kb(flash_free()), dur(uptime()),
               ('<dt>Power</dt><dd>%s</dd>' % ' &middot; '.join(pwr.status()) if pwr else '') +
               '<dt>MQTT</dt><dd>%s</dd>' % (esc(mqtt.status()) if mqtt else 'off') +
               ('<dt>GSM</dt><dd>%s</dd>' % gsm.status() if gsm else '')))


def frag_status(msg=''):
    """/status: Alpine sync tag (recording, clock, flash, MQTT, hidden panels) + recording + system lines
    (list of parts, streamed)."""
    r, mqtt = app.rec, app.mqtt
    util.heap_note()
    out = ['<i hidden x-init="sync(%d,%d,%d,%d,$el.dataset.mqt,$el.dataset.off)" data-mqt="%s" data-off="%s"></i>'
           % (1 if (r.active or r.resume) else 0, 1 if time_ok() else 0, flash_free(), mqtt.state() if mqtt else 0,
              esc(mqtt.status()) if mqtt else 'off', ' '.join(k for k in util.FEATURES if k not in util.FEAT))]
    if msg:
        out.append('<p class="warn">%s</p>' % esc(msg))
    out += _rec_lines(r)
    if r.msg:
        out.append('<p class="muted">%s</p>' % esc(r.msg))
    out.append(_sys_line())
    return out


def ui(name, fn, *args):
    """Call fn(*args) from module `name`, imported for this request only and then released (RAM)."""
    import sys
    gc.collect()
    m = __import__(name)
    try:
        return getattr(m, fn)(*args)
    finally:
        m = None
        del sys.modules[name]
