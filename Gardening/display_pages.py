# Display pages as plain data, independent of any screen: pages_of(entry) lists the pages of a display,
# page(kind, i) builds one: {'t' title, 'v' value, 'u' unit, 'b' band key (colour) or None,
# 'l' small lines} or {'t', 'rows'} for the all-sensors page. An LVGL renderer can reuse this unchanged.
# Spec: docs/SPEC.md#displays


def pages_of(e):
    """The page list of a display: ('soil', i), ('air', 0)... Kinds with one page per item are expanded,
    pages of parts that are not running are left out; at least the clock page."""
    import app
    many = {'soil': len(app.sens.ch), 'gen': len(app.sens.gch), 'ctrl': len(app.ctrl.ctls) if app.ctrl else 0}
    skip = {'air': app.sens.t is None, 'pump': not app.pump, 'batt': not app.batt}
    out = []
    for k in e['pages']:
        if k in many:
            out += [(k, i) for i in range(many[k])]
        elif not skip.get(k):
            out.append((k, 0))
    return out or [('clock', 0)]


def page(kind, i):
    """dict: t title, v value, u unit, b band key (colour) or None, l small lines, rows (page 'all')."""
    import app
    from bands import BANDS
    s = app.sens
    if kind == 'soil' and i < len(s.ch):
        c = s.ch[i]
        p = s.pct(s.raw[i], c[2], c[3])
        k = app.bands.band(i, p)
        return {'t': c[4], 'v': '%d' % round(p), 'u': '%', 'b': k, 'l': [BANDS[k], 'F %d%%' % app.bands.full[i]]}
    if kind == 'all':
        rows = []
        for j, c in enumerate(s.ch):
            p = s.pct(s.raw[j], c[2], c[3])
            rows.append((c[4], '%d%%' % round(p), app.bands.band(j, p)))
        return {'t': 'Soil', 'rows': rows, 'b': None}
    if kind == 'air' and s.t is not None:
        k, lbl = (('now', 'Too hot') if s.t > 32 else ('soon', 'Warm') if s.t > 28 else
                  ('soon', 'Too cold') if s.t < 15 else ('good', 'Good'))
        l = [lbl]
        if s.rh is not None:
            l.append('%d%% RH' % round(s.rh) + (' %d hPa' % round(s.p) if s.p is not None else ''))
        return {'t': 'Air', 'v': '%.1f' % s.t, 'u': '\xb0C', 'b': k, 'l': l}
    if kind == 'gen' and i < len(s.gch):
        g, v = s.gch[i], s.gval[i]
        return {'t': g[3], 'v': '-' if v is None else ('%%.%df' % g[5]) % v, 'u': g[4].replace('&deg;', '\xb0'), 'b': None, 'l': []}
    if kind == 'ctrl' and app.ctrl and i < len(app.ctrl.ctls):
        k = app.ctrl.ctls[i]
        c = k.c
        lbl, u = k.src()
        if c['mode'] in ('manual', 'sun') or k.v is None and not c['src']:
            v, u = '%d' % round(k.outp), '%'
        else:
            v = '-' if k.v is None else '%.1f' % k.v
        return {'t': c['name'], 'v': v, 'u': u, 'b': 'dry' if k.fault else 'good' if k.outp else None,
                'l': ['%s %d%%' % (k.state, round(k.outp)), '' if c['mode'] in ('manual', 'sun', 'curve') else
                      'set %g%s' % (c['sp'], u)]}
    if kind == 'pump' and app.pump:
        c, pm = app.pump.cfg, app.pump
        st = 'LOCKED' if c['fault'] else 'running' if pm.running else 'ready'
        lit = c['flow'] or c['rate_lpm']
        return {'t': 'Water today', 'v': '%.2f' % c['l_day'] if lit else '%d' % c['day_s'], 'u': 'L' if lit else 's',
                'b': 'dry' if c['fault'] else 'wet' if pm.running else 'good',
                'l': ['Pump ' + st, '%d runs%s' % (c['runs_day'], ' %.1f L total' % c['l_total'] if lit else '')]}
    if kind == 'batt' and app.batt and app.batt.v is not None:
        b = app.batt
        return {'t': 'Battery', 'v': '%.2f' % b.v, 'u': 'V', 'b': {'ok': 'good', 'low': 'soon'}.get(b.state, 'dry'),
                'l': [{'ok': 'OK', 'low': 'Low', 'crit': 'CRITICAL'}[b.state]]}
    ok = app.time_ok()
    st = app.stamp() if ok else ''
    l = [app.net.sta.ifconfig()[0] if app.net.sta.isconnected() else 'no WiFi']
    if app.gsm:
        g = app.gsm
        l.append('GSM %s %s' % (g.op or g.state, '%d/31' % g.csq if g.csq not in (None, 99) else ''))
    return {'t': st[:10] if ok else 'Clock not set', 'v': st[11:16] if ok else '--:--', 'u': '', 'b': None, 'l': l}


