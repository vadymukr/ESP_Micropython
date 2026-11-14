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


def _enable(on):
    """Write pump.json 'enabled' (pump.py not loaded while disabled)."""
    import json
    import os
    path = S.DATA_DIR + '/pump.json'
    try:
        with open(path) as f:
            c = json.load(f)
    except (OSError, ValueError):
        c = {}
    c['enabled'] = on
    with open(path + '.tmp', 'w') as f:
        json.dump(c, f)
    os.rename(path + '.tmp', path)


def frag_pump(msg='', ok=False):
    p = app.pump
    if not p:
        if getattr(S, 'PUMP_PIN', None) is None:
            return '<p class="muted">No pump pin set (PUMP_PIN in settings.py).</p>'
        if not app.pump_enabled():
            return ('%s<p>The pump is <b>disabled</b>: its code is not loaded (about 10 KB more free RAM), its dashboard '
                    'card is hidden and GPIO%d is free for other uses.</p><button hx-post="/pump/enable" '
                    'hx-target="#pump">Enable the pump</button> <span class="muted">(the board restarts, about 20 s)</span>'
                    % ('<p class="%s">%s</p>' % ('ok' if ok else 'warn', esc(msg)) if msg else '', S.PUMP_PIN))
        return '<p class="muted">Pump not running (switched off in Features).</p>'
    from pump import MODES, FLOW_TYPES
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
    flows = [d for d in app.sens.gen if d.e['type'] in FLOW_TYPES]
    out.append('<span style="flex-basis:100%%;font-size:13px;color:var(--muted)">Dry-run protection by a flow sensor (optional):</span>'
               '<label>Flow sensor<select name="flow"><option value="">none &ndash; use the moisture check</option>')
    for d in flows:
        out.append('<option value="%s"%s>%s (%s)</option>' % (d.e['id'], ' selected' if c['flow'] == d.e['id'] else '',
                                                               esc(d.e['name']), d.e['id']))
    out.append('</select></label><label>Min flow, L/min<input name="flow_min" type="number" step="0.01" min="0.01" max="20" '
               'value="%s" style="width:6em"></label><label>Grace time, s<input name="flow_wait" type="number" min="2" max="60" '
               'value="%d" style="width:5em"></label><label>Pump rate estimate, L/min (no flow sensor)<input name="rate_lpm" '
               'type="number" step="0.01" min="0" max="100" value="%s" style="width:6em"></label>'
               % (c['flow_min'], c['flow_wait'], c['rate_lpm']))
    out.append('<button>Save</button></form>')
    out.append('<p class="muted">Every run switches off by itself (max %d s, %d s per day). After each run the ticked '
               'sensors are watched; no rise of %.0f points = tank empty / pipe blocked: timer and auto are locked and an '
               'alert is shown. Tick the sensor(s) the water reaches. Auto waters in pulses when a sensor drops to the '
               '&ldquo;water&rdquo; level (F&minus;w) and stops near F. In sleep-cycle mode it only works while awake '
               '(add awake windows around the times). With a flow sensor linked, the pump stops and locks at once when '
               'the flow stays under the minimum after the grace time or stops during a run (tank ran dry); the moisture '
               'rise is then only logged.%s</p>' % (S.PUMP_MAX_S, S.PUMP_DAY_MAX_S, S.PUMP_RISE,
               '' if flows else ' Add a flow sensor (YF-S401, YF-S201) in the Sensors panel to use it.'))
    out += _stats(p)
    log = _log_tail()
    if log:
        out.append('<p class="muted"><b>Log</b> <button class="small" type="button" hx-get="/pump" hx-target="#pump">Refresh'
                   '</button></p><pre style="font-size:12px;white-space:pre-wrap;margin:4px 0 0">%s</pre>'
                   % esc('\n'.join(reversed(log))))
    return out


def _stats(p):
    """Water statistics: today, total, last 14 days."""
    c = p.cfg
    lit = bool(c['flow'] or c['rate_lpm'] or c['l_total'])
    how = 'measured by the flow sensor' if c['flow'] else 'estimated from the pump rate' if c['rate_lpm'] else ''
    out = ['<p><b>Water used</b> <small>%s</small><br>Today: %d run(s), %d s%s%s</p>'
           % (how, c['runs_day'], c['day_s'], ', %.2f L' % c['l_day'] if lit else '',
              ' &middot; total %.1f L' % c['l_total'] if lit else '')]
    if c['hist']:
        out.append('<table><tr><th>Day</th><th>Runs</th><th>Pump s</th>%s</tr>' % ('<th>Litres</th>' if lit else ''))
        for day, l, sec, runs in reversed(c['hist']):
            out.append('<tr><td>%s</td><td>%d</td><td>%d</td>%s</tr>' % (day, runs, sec, '<td>%.2f</td>' % l if lit else ''))
        out.append('</table>')
    out.append('<p><span x-data="{c:0}"><button class="small" x-show="!c" @click="c=1">Reset statistics</button>'
               '<span x-show="c">Clear the history and total? <button class="small danger" hx-post="/pump/statreset" '
               'hx-target="#pump">Yes</button> <button class="small" @click="c=0">No</button></span></span></p>')
    out.append('<p x-data="{c:0}" style="margin-top:12px"><button class="small" x-show="!c" @click="c=1">Disable the pump'
               '</button><span x-show="c">Disable the pump and restart? It stops a running pump; settings are kept. '
               '<button class="small danger" hx-post="/pump/disable" hx-target="#pump">Yes</button> '
               '<button class="small" @click="c=0">No</button></span></p>')
    return out


def pump_post(path, f):
    p = app.pump
    if path == '/pump/enable' and not p:
        import sys
        import sensors as R
        try:
            _enable(True)                      # pin check below sees the pump's pin as taken again
            t = R.taken_pins(app.sens.reg)
        finally:
            R = None
            sys.modules.pop('sensors', None)
        # another feature took the pump pin while the pump was off?
        others = [lbl for pin, lbl in t.items() if pin == S.PUMP_PIN and lbl != 'pump']
        if others:
            _enable(False)
            return frag_pump('GPIO%d is now used by %s - free it first.' % (S.PUMP_PIN, others[0]))
        app.restart_soon()
        return '<p class="ok">Pump enabled. The board restarts - reload the page in about 20 s.</p>'
    if not p:
        return None
    if path == '/pump/disable':
        p.stop('pump disabled')
        p.save(enabled=False)
        app.restart_soon()
        return '<p class="ok">Pump disabled. The board restarts to free its RAM - reload the page in about 20 s.</p>'
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
    if path == '/pump/statreset':
        p.save(hist=[], l_total=0.0, l_day=0.0, runs_day=0)
        p.log('statistics reset by user')
        return frag_pump('Statistics cleared.', True)
    if path == '/pump/save':
        try:
            mode = f.get('mode')
            kw = {'mode': mode, 'soak_min': int(f.get('soak_min', p.cfg['soak_min'])),
                  'sensors': [i for i in range(len(app.sens.ch)) if f.get('s%d' % i) == '1'],
                  'flow': f.get('flow', ''), 'flow_min': float(f.get('flow_min', 0.2)),
                  'flow_wait': int(f.get('flow_wait', 8)), 'rate_lpm': float(f.get('rate_lpm', 0) or 0)}
            if kw['flow'] and kw['flow'] not in [d.e['id'] for d in app.sens.gen if d.e['type'] in ('flow', 'yfs401')]:
                return frag_pump('Choose a running flow sensor.')
            if not (0.01 <= kw['flow_min'] <= 20 and 2 <= kw['flow_wait'] <= 60 and 0 <= kw['rate_lpm'] <= 100):
                return frag_pump('Flow settings out of range.')
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
        p.log('settings: mode %s%s' % (mode, ', flow protection %s' % kw['flow'] if kw['flow'] else ''))
        return frag_pump('Saved.', True)
    return None



def handle(method, path, f):
    if method == 'GET':
        return frag_pump() if path == '/pump' else None
    return pump_post(path, f)
