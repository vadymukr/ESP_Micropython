# Settings panels (bands, LEDs, power) - imported only for /bands, /leds, /power requests and
# released afterwards (app.py removes it from sys.modules), so this code does not sit in RAM.
# Pump and MQTT panels are in ui_pump.py / ui_mqtt.py, loaded the same way (smaller peak heap).
import time
import app
from app import esc, time_ok
import settings as S


def frag_bands(msg=''):
    from bands import STAGES
    b = app.bands
    # Independent F per sensor by default. "Link" keeps sensor B..N at a fixed offset from A, so one
    # number moves all; disabled inputs are not submitted, so the server sees only the active set.
    xd = ''.join(',o%d:%d' % (i, b.off[i]) for i in range(1, len(app.sens.ch)))
    out = ['<form class="set" hx-post="/bands/save" hx-target="#bands" x-data="{link:%s,f0:%d%s}">'
           '<label>Growth stage <select name="stage">' % ('true' if b.link else 'false', b.full[0], xd)]
    for k, (name, w) in enumerate(STAGES):
        out.append('<option value="%d"%s>%s (water at F&minus;%d)</option>' % (k, ' selected' if k == b.stage else '', esc(name), w))
    out.append('</select></label>')
    for i, c in enumerate(app.sens.ch):
        use = ('<button class="small" type="button" hx-post="/bands/save" hx-include="closest form" '
               'hx-vals=\'{"use":"%d"}\' hx-target="#bands">Use current</button>' % i)
        if i == 0:
            out.append('<label>%s full (F), %%<input name="f0" type="number" min="10" max="100" x-model.number="f0" '
                       'style="width:6em"></label>%s' % (esc(c[4]), use))
            continue
        out.append('<label x-show="!link">%s full (F), %%<input name="f%d" type="number" min="10" max="100" value="%d" '
                   ':disabled="link" style="width:6em"></label>'
                   '<label x-show="link" x-cloak>%s offset from %s<input name="o%d" type="number" min="-50" max="50" '
                   'x-model.number="o%d" :disabled="!link" style="width:6em"></label>'
                   '<span x-show="link" x-cloak class="muted" style="align-self:end;padding-bottom:8px">= F <b x-text="f0+o%d"></b>%%</span>%s'
                   % (esc(c[4]), i, b.full[i], esc(c[4]), esc(app.sens.ch[0][4]), i, i, i, use))
    out.append('<label class="row" style="flex-direction:row;align-items:center;gap:6px">'
               '<input type="checkbox" name="link" value="1" x-model="link"%s> Link to %s with an offset</label>'
               '<button>Save</button></form>' % (' checked' if b.link else '', esc(app.sens.ch[0][4])))
    if msg:
        out.append('<p class="ok">%s</p>' % esc(msg))
    out.append('<p class="muted">Each sensor is judged against its own F (soil contact and depth differ). '
               'F = the reading that settles a few hours after a deep watering (pots) or about a day after (garden beds). '
               '&ldquo;Use current&rdquo; takes the live reading. With <i>Link</i>, changing %s moves the others by the '
               'same amount. Saved on the board.</p>' % esc(app.sens.ch[0][4]))
    return ''.join(out)


MODES = ('off', 'on', 'eco')             # same as leds.MODES (leds.py is not loaded while off)


def frag_leds(msg=''):
    if not S.LED_PINS:
        return '<p class="muted">No indicator LEDs configured (LED_PINS in settings.py).</p>'
    mode = app.leds.mode if app.leds else app.leds_mode()
    lbl = {'off': 'Off (energy saving)', 'on': 'On (steady)', 'eco': 'Eco (short flash every 5 s)'}
    out = ['<form class="set" hx-post="/leds/save" hx-target="#leds"><label>Indicator LEDs (%s) <select name="mode">'
           % esc(app.sens.ch[S.LED_SENSOR][4])]
    for m in MODES:
        out.append('<option value="%s"%s>%s</option>' % (m, ' selected' if m == mode else '', lbl[m]))
    out.append('</select></label><button>Save</button></form>')
    if msg:
        out.append('<p class="ok">%s</p>' % esc(msg))
    out.append('<p class="muted">GPIO %s: fully watered &middot; normal &middot; water soon &middot; water now '
               '(blinks on drought). Low battery: all four flash.</p>' % ', '.join(str(p) for p in S.LED_PINS))
    return ''.join(out)


def frag_power(msg=''):
    if not app.pwr:
        return '<p class="muted">Power management off (POWER_ENABLED in settings.py).</p>'
    from power import CPUS
    c = app.pwr.cfg
    sel = lambda a, b: ' selected' if a == b else ''
    out = ['<p><b>%s</b></p>' % ' &middot; '.join(app.pwr.status()),
           '<form class="set" hx-post="/power/save" hx-target="#power" x-data="{m:\'%s\'}">' % c['mode'],
           '<label>Mode <select name="mode" x-model="m"><option value="always"%s>Always on</option>'
           '<option value="cycle"%s>Sleep cycle (deep sleep)</option></select></label>' % (sel(c['mode'], 'always'), sel(c['mode'], 'cycle')),
           '<label>CPU <select name="cpu">%s</select></label>'
           % ''.join('<option value="%d"%s>%d MHz</option>' % (m, sel(m, c['cpu']), m) for m in CPUS)]
    if S.SENSOR_PWR_PINS:
        out.append('<label class="row" style="flex-direction:row;align-items:center;gap:6px"><input type="checkbox" '
                   'name="sensor_pwr" value="1"%s> Power sensors only while measuring (GPIO %s)</label>'
                   % (' checked' if c['sensor_pwr'] else '', ', '.join(str(p) for p in S.SENSOR_PWR_PINS)))
    out.append('<span x-show="m==\'cycle\'" x-cloak style="display:contents">'
               '<label>Wake every, min<input name="every" type="number" min="2" max="120" value="%d" style="width:5em"></label>'
               '<label>Stay awake, min<input name="stay" type="number" min="2" max="60" value="%d" style="width:5em"></label>'
               '<label>Low battery: every, min<input name="lowbatt_every" type="number" min="5" max="240" value="%d" style="width:5em"></label>'
               '<label>Awake windows (local)<input name="windows" maxlength="60" value="%s" placeholder="07:00-07:15,19:00-19:15" style="width:15em"></label>'
               '</span><button>Save</button></form>'
               % (c['every'], c['stay'], c['lowbatt_every'], esc(c['windows'])))
    if c['mode'] == 'cycle':
        if app.pwr.maint():
            out.append('<p><button class="small" hx-post="/power/maint" hx-vals=\'{"on":"0"}\' hx-target="#power">'
                       'End maintenance</button></p>')
        else:
            out.append('<p><button class="small" hx-post="/power/maint" hx-vals=\'{"on":"1"}\' hx-target="#power">'
                       'Stay awake %d h (maintenance)</button> '
                       '<button class="small danger" hx-post="/power/sleep" hx-target="#power">Sleep now</button></p>'
                       % S.MAINT_MAX_H)
    if msg:
        out.append('<p class="ok">%s</p>' % esc(msg))
    out.append('<p class="muted">Sleep cycle: while asleep the page is unreachable. Wake it with the BOOT button%s, '
               'in an awake window, or keep it awake with maintenance. Each wake measures, logs a row (if recording) '
               'and flashes the LED.</p>' % (' or the touch pad (GPIO %d)' % S.WAKE_TOUCH if S.WAKE_TOUCH is not None else ''))
    return ''.join(out)


def handle(method, path, f):
    bands = app.bands
    if method == 'GET':
        return {'/bands': frag_bands, '/leds': frag_leds, '/power': frag_power}.get(path, lambda: None)()
    if path == '/power/save' and app.pwr:
        from power import CPUS, parse_windows
        try:
            mode = f.get('mode')
            cpu = int(f.get('cpu', 160))
            kw = {'mode': mode, 'cpu': cpu, 'sensor_pwr': f.get('sensor_pwr') == '1'}
            if mode == 'cycle':
                kw['every'] = int(f.get('every', app.pwr.cfg['every']))
                kw['stay'] = int(f.get('stay', app.pwr.cfg['stay']))
                kw['lowbatt_every'] = int(f.get('lowbatt_every', app.pwr.cfg['lowbatt_every']))
                win = f.get('windows', '').strip()[:60]
                if win and not parse_windows(win):
                    return (frag_power('Windows: use HH:MM-HH:MM, separated by commas.'))
                kw['windows'] = win
        except ValueError:
            return (frag_power('Invalid value.'))
        if mode not in ('always', 'cycle') or cpu not in CPUS or not (
                2 <= kw.get('every', 10) <= 120 and 2 <= kw.get('stay', 10) <= 60 and 5 <= kw.get('lowbatt_every', 30) <= 240):
            return (frag_power('Out of range.'))
        app.pwr.save(**kw)
        app.pwr.apply_cpu()
        app.pwr.activity()
        return (frag_power('Saved.' + (' The board sleeps after %d min without page use.' % app.pwr.cfg['stay']
                                                     if mode == 'cycle' else '')))
    if path == '/power/sleep' and app.pwr:
        app.pwr.save(maint_until=0)
        app.pwr.deadline = app.pwr.up()         # power.run() sleeps within ~5 s
        return frag_power('Going to sleep now. Press BOOT to wake it.')
    if path == '/power/maint' and app.pwr:
        if f.get('on') == '1' and time_ok():
            app.pwr.save(maint_until=time.time() + S.MAINT_MAX_H * 3600)
            msg = 'Maintenance on: stays awake for %d h.' % S.MAINT_MAX_H
        else:
            app.pwr.save(maint_until=0)
            app.pwr.activity()
            msg = 'Maintenance off.' if f.get('on') != '1' else 'Clock not set yet - try again in a moment.'
        return (frag_power(msg))
    if path == '/leds/save':
        m = f.get('mode')
        if not S.LED_PINS or m not in MODES:
            return (frag_leds('Invalid mode.'))
        r = app.rgb
        if m != 'off' and r and r.pwm and any(p in S.LED_PINS for p in r.c['pins']):
            return (frag_leds('The RGB status light uses these pins - switch it off or move it first.'))
        if app.leds:
            app.leds.set_mode(m)                  # off: stays loaded until the next restart
        elif m != 'off':
            app.leds_start().set_mode(m)
        else:
            import json
            with open(S.DATA_DIR + '/leds.json', 'w') as fh:
                json.dump({'mode': m}, fh)
        return (frag_leds('Saved.'))
    if path == '/bands/save':
        from bands import STAGES
        try:
            stage = int(f.get('stage', bands.stage))
            link = f.get('link') == '1'
            n = len(app.sens.ch)
            f0 = int(f.get('f0', bands.full[0]))
            if link:                              # others = A + offset
                full = [f0] + [f0 + int(f.get('o%d' % i, bands.off[i])) for i in range(1, n)]
            else:
                full = [f0] + [int(f.get('f%d' % i, bands.full[i])) for i in range(1, n)]
            use = f.get('use')
            if use is not None:
                i = int(use)
                c = app.sens.ch[i]
                cur = round(app.sens.pct(app.sens.raw[i], c[2], c[3]))
                if link and i == 0:               # move A, keep the offsets
                    full = [x + cur - full[0] for x in full]
                else:
                    full[i] = cur
        except (ValueError, IndexError):
            return (frag_bands('Invalid value.'))
        if not 0 <= stage < len(STAGES) or not all(10 <= x <= 100 for x in full):
            return (frag_bands('F must be 10-100 %.'))
        bands.set(stage, full, link)
        return (frag_bands('Saved.'))
    return None
