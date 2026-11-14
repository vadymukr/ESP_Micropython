# The JSON message for <base>/state: the selected values (averages since the last message).
# Spec: docs/SPEC.md#mqtt (state message)
import settings as S


def snapshot(avg, fields):
    """The selected values as a dict for <base>/state (here, not in app.py: only in RAM while MQTT is on).
    avg = Mqtt.avg(); fields = 'sen_<id>', 'air', 'pump', 'batt', 'board' ([] = everything). An unselected
    soil sensor is sent as null so the others keep their position in the list."""
    import gc
    from app import sens, bands, pump, batt, rec, net, stamp, time_ok, uptime, ctrl
    from bands import BANDS
    S_ = S
    raws, t, rh, hpa, n, gv = avg
    if raws is None:
        raws, t, rh, hpa, n, gv = list(sens.raw), sens.t, sens.rh, sens.p, 1, list(sens.gval)
    allf = not fields                        # empty selection = everything
    d = {'time': stamp() if time_ok() else None, 'samples': n}
    ss = []
    for i, c in enumerate(sens.ch):
        if not allf and 'sen_' + c[5] not in fields:
            ss.append(None)
            continue
        p = sens.pct(raws[i], c[2], c[3])
        k = bands.band(i, p)
        ss.append({'id': c[5], 'name': c[4], 'gpio': c[0], 'raw': round(raws[i], 1), 'pct': round(p, 1), 'band': k,
                   'band_text': BANDS[k], 'full_F': bands.full[i], 'water_at': bands.full[i] - bands.w(),
                   'dry_rate_pts_day': round(bands.rate[i] * 24, 1), 'rate_measured': bands.rate_ok[i]})
    if any(ss):
        d['soil'] = ss
    gs = {}
    for j, g in enumerate(sens.gch):
        if allf or 'sen_' + g[6] in fields:
            v = gv[j] if j < len(gv) else None
            key = g[6] if len([x for x in sens.gch if x[6] == g[6]]) == 1 else g[6] + '.' + g[2]
            gs[key] = {'name': g[3], 'value': None if v is None else round(v, g[5]), 'unit': g[4].replace('&deg;', '\xb0')}
    if gs:
        d['sensors'] = gs
    if allf or 'air' in fields:
        d['air'] = {'temp_c': None if t is None else round(t, 2), 'rh_pct': None if rh is None else round(rh, 1),
                    'hpa': None if hpa is None else round(hpa, 1), 'sensor': sens.clim_name if sens.clim else None}
    if pump and (allf or 'pump' in fields):
        c = pump.cfg
        d['pump'] = {'on': pump.running, 'busy': pump.busy(), 'mode': c['mode'], 'fault': c['fault'] or None,
                     'today_s': c['day_s'], 'day_max_s': S_.PUMP_DAY_MAX_S, 'tank_empty': pump.tank_empty(),
                     'runs_today': c['runs_day'], 'today_l': round(c['l_day'], 3), 'total_l': round(c['l_total'], 2),
                     'flow_lpm': None if pump.lpm is None or not pump.running else round(pump.lpm, 2)}
    if ctrl and (allf or 'control' in fields or 'heater' in fields):
        d['control'] = ctrl.mqtt()
    if batt and (allf or 'batt' in fields):
        d['battery'] = {'v': None if batt.v is None else round(batt.v, 2), 'state': batt.state}
    if allf or 'board' in fields:
        d['uptime_s'] = uptime()
        d['recording'] = rec.fname if rec.active else None
        d['wifi_rssi'] = net.sta.status('rssi') if net.sta.isconnected() else None
        d['heap_free'] = gc.mem_free()
    return d
