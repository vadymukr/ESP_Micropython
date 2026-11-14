# Dashboard card of one controller (output bar, setpoint / sun / curve summary, sensor, fault).
# Spec: docs/SPEC.md#controllers


def _esc(t):
    """HTML-escape names and messages."""
    return str(t).replace('&', '&amp;').replace('<', '&lt;')


def card(k):
    """Dashboard card of controller k (Ctl): state badge, big value or level, output bar, facts."""
    c = k.c
    word = {'heater': 'Heating', 'light': 'On', 'fan': 'Cooling'}[c['kind']]
    band = ('dry' if k.fault else 'now' if k.state == 'on' and c['kind'] == 'heater' else
            'good' if k.state in ('on', 'holding', 'idle', 'manual') else 'wet')
    badge = {'on': word, 'holding': 'Holding', 'idle': 'Past setpoint', 'manual': 'Manual %d %%' % round(k.outp),
             'off': 'Off', 'cut-off': 'CUT-OFF', 'no reading': 'No reading', 'stopped': 'Stopped',
             'outside hours': 'Off (hours)', 'sunrise': 'Sunrise', 'sunset': 'Sunset', 'day': 'Day',
             'night': 'Night', 'clock not set': 'Clock not set'}.get(k.state, k.state)
    lbl, unit = k.src()
    unit = unit.replace('\xb0', '&deg;')
    how = ('switched' if c['mode'] == 'onoff' else 'PWM %d Hz' % c['freq'] if c['out'] == 'pwm'
           else 'window %d s' % c['window_s'])
    if c['mode'] == 'sun':
        setp = 'sun %s-%s &middot; %s %d min &middot; day %d %% night %d %%' % (
            c['rise'], c['set'], c['shape'], c['ramp_min'], c['day'], c['night'])
    elif c['mode'] == 'curve':
        setp = 'curve %g%s &rarr; %d %%, %g%s &rarr; %d %%%s' % (c['x1'], unit, c['y1'], c['x2'], unit, c['y2'],
                                                          ' &middot; stops' if c['stop'] else '')
    else:
        setp = ('manual' if c['mode'] == 'manual' else '%g %s &middot; %s' % (
            c['sp'], unit, 'PID' if c['mode'] == 'pid' else 'on/off &plusmn;%g' % (c['hyst'] / 2)))
    if c['mode'] in ('manual', 'sun') or not c['src']:  # dimmer / daylight: the level is the big number
        big = '%d<small>%%</small>' % round(k.cur if k.pwm else k.outp)
    else:
        big = '%s<small>%s</small>' % ('&ndash;' if k.v is None else '%.1f' % k.v, unit)
    return ('<div class="card band %s"><h3><span>%s <small>GPIO%s</small></span><span class="badge">%s</span></h3>'
            '<div class="big">%s</div><div class="bar"><i style="width:%d%%"></i></div>'
            '<dl class="facts"><dt>Setpoint</dt><dd>%s</dd><dt>Output</dt><dd>%d %% &middot; %s</dd>'
            '<dt>Sensor</dt><dd>%s</dd></dl>%s</div>'
            % (band, _esc(c['name']), c['pin'], badge, big, round(k.outp), setp, round(k.outp), how,
               _esc(lbl), '<p class="warn">%s</p>' % _esc(k.fault) if k.fault else ''))
