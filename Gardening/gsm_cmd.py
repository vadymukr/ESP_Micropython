# What the modem does for the garden (loaded on the first SMS, call or alert check, not while idle):
# SMS alerts on changes, the status text, and the SMS commands from allowed numbers.
# Spec: docs/SPEC.md#gsm (commands and alerts)
import settings as S
from gsm_cfg import DEFAULTS


def check(g, pcts):
    """SMS alerts on changes (every ~30 s from the sampler)."""
    from bands import BANDS
    from app import sens, bands, pump, batt
    c = g.c
    if c['a_soil']:
        for i, v in enumerate(pcts):
            k = bands.band(i, v)
            nm = sens.ch[i][4]
            g.alert('soil' + nm, k if k in ('now', 'dry') else None, 'Garden: %s %s (%d%%)' % (nm, BANDS[k], round(v)))
    if c['a_pump'] and pump:
        f = pump.cfg['fault']
        g.alert('pump', f or None, 'Garden pump LOCKED: %s Reply RESET after refilling.' % f[:100])
    if c['a_batt'] and batt and batt.v is not None:
        g.alert('batt', None if batt.state == 'ok' else batt.state,
                  'Garden battery %s: %.2f V' % ('CRITICAL' if batt.state == 'crit' else 'low', batt.v))



def status_text():
    """Short plain-text status for an SMS reply (max 160 characters)."""
    from app import sens, bands, pump, batt, stamp, time_ok
    from bands import BANDS
    p = ['Garden %s:' % stamp()[11:16] if time_ok() else 'Garden:']
    for i, c in enumerate(sens.ch):
        v = sens.pct(sens.raw[i], c[2], c[3])
        p.append('%s %d%% %s;' % (c[4], round(v), BANDS[bands.band(i, v)]))
    if sens.t is not None:
        p.append('air %.1fC%s;' % (sens.t, ' %d%%' % sens.rh if sens.rh is not None else ''))
    if pump:
        p.append('pump %s, today %ds%s;' % ('LOCKED' if pump.cfg['fault'] else 'on' if pump.running else 'ready',
                                          pump.cfg['day_s'], ' %.2fL' % pump.cfg['l_day'] if pump.cfg['l_day'] else ''))
    if batt and batt.v is not None:
        p.append('batt %.2fV %s' % (batt.v, batt.state))
    return ' '.join(p)[:160]


HELP = ('STATUS, PUMP [s], STOP, RESET, MODE MANUAL|SCHEDULE|AUTO, WATER, LED ON|OFF|ECO, RGB ON|OFF|ECO, '
        'HEAT|LIGHT|FAN [value|ON|OFF], CTRL <name> [value|ON|OFF], INFO')


def sms_command(text, trusted=False):
    """Text from an allowed number -> reply text ('' = ignore: secret code missing / wrong).
    trusted: a call action (no code needed)."""
    import app
    from app import pump
    c = app.gsm.c if app.gsm else DEFAULTS
    t = text.strip().upper().split()
    code = c['code'].strip().upper()
    if code and not trusted:
        if not t or t[0] != code:
            return ''
        t = t[1:]
    words = ' '.join(t)
    for a in c['alias'].split(';'):                            # own keywords: WORDS=COMMAND
        k, _, v = a.partition('=')
        if k.strip() and k.strip().upper() == words:
            t = v.strip().upper().split()
            break
    w = t[0] if t else ''
    arg = t[1] if len(t) > 1 else ''
    if w in ('STATUS', 'S'):
        return status_text()
    if w in ('PUMP', 'STOP', 'RESET', 'MODE', 'WATER') and not pump:
        return 'No pump configured.'
    if w == 'PUMP':
        try:
            secs = int(arg) if arg else pump.cfg['man_s']
        except ValueError:
            return 'Use: PUMP <seconds>'
        return pump.start(secs, 'sms') or 'Pump on for %d s (max %d).' % (min(secs, S.PUMP_MAX_S), S.PUMP_MAX_S)
    if w == 'STOP':
        pump.stop('stopped by SMS')
        return 'Pump stopped.'
    if w == 'RESET':
        pump.reset_fault()
        return 'Pump lock cleared.'
    if w == 'MODE':
        m = {'MANUAL': 'manual', 'SCHEDULE': 'schedule', 'SCHED': 'schedule', 'AUTO': 'auto'}.get(arg)
        if not m:
            return 'Use: MODE MANUAL|SCHEDULE|AUTO (now %s)' % pump.cfg['mode']
        pump.save(mode=m)
        return 'Pump mode: %s.' % m
    if w == 'WATER':
        p = pump.cfg
        return 'Water today %.2f L, %d runs, %d s; total %.1f L.' % (p['l_day'], p['runs_day'], p['day_s'], p['l_total'])
    if w in ('LED', 'RGB'):
        m = arg.lower()
        if m not in ('on', 'off', 'eco'):
            return 'Use: %s ON|OFF|ECO' % w
        if w == 'LED':
            if not S.LED_PINS:
                return 'No LED indicator on this board.'
            if app.leds:
                app.leds.set_mode(m)
            elif m != 'off':
                app.leds_start().set_mode(m)
            return 'LED indicator: %s.' % m
        from rgb import load_cfg, save_cfg
        r = load_cfg()
        if not r['on']:
            return 'RGB light is not set up (Watering bands - settings).'
        r['mode'] = m
        save_cfg(r)
        app.rgb_apply()
        return 'RGB light: %s.' % m
    if w in ('HEAT', 'LIGHT', 'FAN', 'CTRL'):
        return _ctrl_cmd(app, w, t[1:])
    if w == 'INFO':
        import gc
        g = app.gsm
        ip = app.net.sta.ifconfig()[0] if app.net and app.net.sta.isconnected() else 'no WiFi'
        return 'GSM %s signal %s/31%s; WiFi %s; RAM %d KB free; up %d h.' % (
            g.op if g else '-', g.csq if g else '-', ', GPRS ' + g.ip if g and g.ip else '', ip,
            gc.mem_free() // 1024, app.uptime() // 3600)
    return 'Commands: ' + HELP


def _ctrl_cmd(app, w, args):
    """HEAT / LIGHT / FAN [value|ON|OFF] (first controller of that kind), CTRL <name> [value|ON|OFF].
    A value sets the level in manual mode, the day level in sun mode, otherwise the setpoint."""
    if w == 'CTRL' and not args:
        return 'Use: CTRL <name> [value|ON|OFF]'
    key, args = (args[0], args[1:]) if w == 'CTRL' else ({'HEAT': 'heater', 'LIGHT': 'light', 'FAN': 'fan'}[w], args)
    k = app.ctrl.get(key) if app.ctrl else None
    if not k:
        return 'No %s set up (Control panel).' % key.lower()
    arg = args[0] if args else ''
    err = _ctrl_set(k.c, arg, w) if arg else None
    if err:
        return err
    if arg:
        from control_cfg import load_cfg, save_cfg
        cfg = load_cfg()
        cfg['list'] = [dict(k.c) if e['id'] == k.c['id'] else e for e in cfg['list']]
        save_cfg(cfg)
        try:
            k.step()                                       # reply with the new state
        except Exception:
            pass
    return _ctrl_reply(k)


def _ctrl_set(c, arg, w):
    """Apply ON / OFF / a value to controller settings c; returns an error text or None."""
    mode = c['mode'] if c['mode'] != 'off' else c.get('on_mode', 'pid')     # the mode a value applies to
    if arg == 'OFF':
        if c['mode'] != 'off':
            c['on_mode'] = c['mode']                       # ON brings this mode back
        c['mode'] = 'off'
        return None
    if arg == 'ON':
        c['mode'] = mode if mode != 'off' else 'manual'
        return None
    try:
        v = float(arg)
    except ValueError:
        return 'Use: %s [value|ON|OFF]' % w
    if mode == 'curve':
        return '%s follows its curve - change it in the Control panel.' % c['name']
    if mode in ('sun', 'manual'):
        if not 0 <= v <= 100:
            return 'Level 0-100 %.'
        c['mode'] = mode
        if mode == 'sun':
            c['day'] = int(v)
            c['night'] = min(c['night'], c['day'])
        else:
            c['man'] = int(v)
        return None
    if c['cut'] is not None and (v >= c['cut'] if c['kind'] != 'fan' else v <= c['cut']):
        return 'Setpoint must stay inside the cut-off (%g).' % c['cut']
    c['sp'], c['mode'] = v, mode
    return None


def _ctrl_reply(k):
    """'<name> <state>: <setting>, now <value>, output N%.' for an SMS reply."""
    c = k.c
    unit = k.src()[1]
    if unit and unit[0] != '\xb0':
        unit = ' ' + unit
    what = {'manual': 'level %d%%' % c['man'], 'sun': 'sun %s-%s day %d%%' % (c['rise'], c['set'], c['day']),
            'curve': 'curve', 'off': 'off'}.get(c['mode']) or 'set %g%s' % (c['sp'], unit)
    now = '' if k.v is None else ', now %.1f%s' % (k.v, unit)
    return '%s %s: %s%s, output %d%%.' % (c['name'], k.state, what, now, round(k.outp))
