# Watering pump panel (/pump, /pump/...) - loaded per request by app.py, then released.
import app
from app import esc
import settings as S

def _log_tail(n=8):
    from pump import LOG
    try:
        with open(LOG) as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 700))
            lines = f.read().split('\n')
    except OSError:
        return []
    if size > 700:
        lines = lines[1:]
    return [x for x in lines if x][-n:]


def frag_pump(msg='', ok=False):
    p = app.pump
    if not p:
        return '<p class="muted">No pump configured (PUMP_PIN in settings.py).</p>'
    from pump import MODES
    c = p.cfg
    sel = lambda a, b: ' selected' if a == b else ''
    out = ['<p><b>Pump (GPIO %d): %s</b> &middot; today %d of %d s</p>' % (S.PUMP_PIN, p.state_text(), c['day_s'], S.PUMP_DAY_MAX_S)]
    if c['fault']:
        out.append('<p class="warn"><b>Locked:</b> %s</p><p><button class="small danger" hx-post="/pump/reset" '
                   'hx-target="#pump">Reset (tank refilled / fixed)</button></p>' % esc(c['fault']))
    if msg:
        out.append('<p class="%s">%s</p>' % ('ok' if ok else 'warn', esc(msg)))
    # manual run: always switches off by itself
    out.append('<form class="set" hx-post="/pump/run" hx-target="#pump"><label>Run now, seconds'
               '<input name="s" type="number" min="1" max="%d" value="%d" style="width:6em"></label>'
               '<button>Run</button>%s</form>'
               % (S.PUMP_MAX_S, c['man_s'], ' <button class="danger" type="button" hx-post="/pump/stop" hx-target="#pump">'
                  'Stop</button>' if p.running else ''))
    # automatic modes
    out.append('<form class="set" hx-post="/pump/save" hx-target="#pump" x-data="{m:\'%s\'}">'
               '<label>Mode <select name="mode" x-model="m">' % c['mode'])
    lbl = {'manual': 'Manual only', 'schedule': 'Timer (fixed times)', 'auto': 'Auto (by moisture)'}
    for m in MODES:
        out.append('<option value="%s"%s>%s</option>' % (m, sel(m, c['mode']), lbl[m]))
    out.append('</select></label>')
    out.append('<span x-show="m==\'schedule\'" x-cloak style="display:contents">'
               '<label>Times (local)<input name="times" maxlength="60" value="%s" placeholder="06:30,18:00" style="width:11em"></label>'
               '<label>Run, seconds<input name="sched_s" type="number" min="1" max="%d" value="%d" style="width:6em"></label>'
               '<label class="row" style="flex-direction:row;align-items:center;gap:6px"><input type="checkbox" name="skip_moist" '
               'value="1"%s> Skip if soil already good</label></span>'
               % (esc(c['times']), S.PUMP_MAX_S, c['sched_s'], ' checked' if c['skip_moist'] else ''))
    out.append('<span x-show="m==\'auto\'" x-cloak style="display:contents">'
               '<label>Pulse, seconds<input name="pulse_s" type="number" min="1" max="%d" value="%d" style="width:6em"></label>'
               '<label>Max pulses<input name="max_pulses" type="number" min="1" max="10" value="%d" style="width:5em"></label>'
               '<label>Stop at F &minus;<input name="target" type="number" min="0" max="20" value="%d" style="width:5em"></label>'
               '<label>Allowed hours (local, empty = any)<input name="hours" maxlength="11" value="%s" placeholder="05:30-09:00" style="width:9em"></label></span>'
               % (S.PUMP_MAX_S, c['pulse_s'], c['max_pulses'], c['target'], esc(c['hours'])))
    out.append('<label>Watch moisture, min<input name="soak_min" type="number" min="2" max="60" value="%d" style="width:5em"></label>'
               % c['soak_min'])
    for i, ch in enumerate(app.sens.ch):
        out.append('<label class="row" style="flex-direction:row;align-items:center;gap:6px"><input type="checkbox" '
                   'name="s%d" value="1"%s> %s</label>' % (i, ' checked' if i in c['sensors'] else '', esc(ch[4])))
    out.append('<button>Save</button></form>')
    out.append('<p class="muted">Every run switches off by itself (max %d s, %d s per day). After each run the ticked '
               'sensors are watched; no rise of %.0f points = tank empty / pipe blocked: timer and auto are locked and an '
               'alert is shown. Tick the sensor(s) the water reaches. Auto waters in pulses when a sensor drops to the '
               '&ldquo;water&rdquo; level (F&minus;w) and stops near F. In sleep-cycle mode it only works while awake '
               '(add awake windows around the times).</p>' % (S.PUMP_MAX_S, S.PUMP_DAY_MAX_S, S.PUMP_RISE))
    log = _log_tail()
    if log:
        out.append('<p class="muted"><b>Log</b> <button class="small" type="button" hx-get="/pump" hx-target="#pump">Refresh'
                   '</button></p><pre style="font-size:12px;white-space:pre-wrap;margin:4px 0 0">%s</pre>'
                   % esc('\n'.join(reversed(log))))
    return ''.join(out)


def pump_post(path, f):
    p = app.pump
    if not p:
        return None
    from pump import MODES, parse_times, parse_hours
    if path == '/pump/run':
        try:
            secs = int(f.get('s', p.cfg['man_s']))
        except ValueError:
            return frag_pump('Invalid value.')
        if not 1 <= secs <= S.PUMP_MAX_S:
            return frag_pump('Out of range (1-%d s).' % S.PUMP_MAX_S)
        p.save(man_s=secs)
        err = p.start(secs, 'manual')
        return frag_pump(err or 'Pump on for %d s - watch the reading rise.' % secs, not err)
    if path == '/pump/stop':
        p.stop('stopped by user')
        return frag_pump('Pump stopped.', True)
    if path == '/pump/reset':
        p.reset_fault()
        return frag_pump('Lock cleared.', True)
    if path == '/pump/save':
        try:
            mode = f.get('mode')
            kw = {'mode': mode, 'soak_min': int(f.get('soak_min', p.cfg['soak_min'])),
                  'sensors': [i for i in range(len(app.sens.ch)) if f.get('s%d' % i) == '1']}
            if mode == 'schedule':
                kw['times'] = f.get('times', '').strip()[:60]
                kw['sched_s'] = int(f.get('sched_s', p.cfg['sched_s']))
                kw['skip_moist'] = f.get('skip_moist') == '1'
                if not parse_times(kw['times']):
                    return frag_pump('Times: use HH:MM, separated by commas.')
                if not 1 <= kw['sched_s'] <= S.PUMP_MAX_S:
                    return frag_pump('Out of range.')
            if mode == 'auto':
                kw['pulse_s'] = int(f.get('pulse_s', p.cfg['pulse_s']))
                kw['max_pulses'] = int(f.get('max_pulses', p.cfg['max_pulses']))
                kw['target'] = int(f.get('target', p.cfg['target']))
                kw['hours'] = f.get('hours', '').strip()[:11]
                if parse_hours(kw['hours']) is False:
                    return frag_pump('Hours: use HH:MM-HH:MM or leave empty.')
                if not (1 <= kw['pulse_s'] <= S.PUMP_MAX_S and 1 <= kw['max_pulses'] <= 10 and 0 <= kw['target'] <= 20):
                    return frag_pump('Out of range.')
        except ValueError:
            return frag_pump('Invalid value.')
        if mode not in MODES or not 2 <= kw['soak_min'] <= 60:
            return frag_pump('Out of range.')
        if not kw['sensors']:
            return frag_pump('Tick at least one sensor to watch.')
        if mode != 'schedule':
            kw['done'] = ''
        p.save(**kw)
        p.log('settings: mode %s' % mode)
        return frag_pump('Saved.', True)
    return None



def handle(method, path, f):
    if method == 'GET':
        return frag_pump() if path == '/pump' else None
    return pump_post(path, f)
