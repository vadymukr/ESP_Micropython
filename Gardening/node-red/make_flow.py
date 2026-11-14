# Generates the Node-RED flow for the garden logger (Dashboard 2.0). Run: python3 make_flow.py OUT.json
import json, sys

ids = {}
def nid(name):
    if name not in ids:
        ids[name] = '%016x' % (0x6a7264656e000000 + len(ids) + 1)
    return ids[name]

TAB = nid('tab')
nodes = []
def add(key, typ, x=None, y=None, wires=None, **kw):
    n = {'id': nid(key), 'type': typ}
    if x is not None:
        n.update(z=TAB, x=x, y=y)
    if wires is not None:
        n['wires'] = [[nid(w) for w in out] for out in wires]
    n.update(kw)
    nodes.append(n)
    return n

# ---------------------------------------------------------------- tab + config nodes
nodes.append({'id': TAB, 'type': 'tab', 'label': 'Garden logger', 'disabled': False,
              'info': 'MQTT data from the ESP32 garden logger (topics garden/state, garden/pump/event, garden/status).'})
add('broker', 'mqtt-broker', name='Mosquitto', broker='localhost', port='1883', clientid='', autoConnect=True,
    usetls=False, protocolVersion='4', keepalive='60', cleansession=True, autoUnsubscribe=True,
    birthTopic='', birthQos='0', birthPayload='', birthMsg={}, closeTopic='', closeQos='0', closePayload='',
    closeMsg={}, willTopic='', willQos='0', willPayload='', willMsg={}, userProps='', sessionExpiry='')
add('base', 'ui-base', name='Garden', path='/dashboard', includeClientData=True,
    acceptsClientConfig=['ui-notification', 'ui-control'], showPathInSidebar=False, navigationStyle='default')
add('theme', 'ui-theme', name='Garden', colors={'surface': '#2f7d4f', 'primary': '#2f7d4f', 'bgPage': '#f4f6f3',
    'groupBg': '#ffffff', 'groupOutline': '#dde3de'},
    sizes={'pagePadding': '12px', 'groupGap': '12px', 'groupBorderRadius': '8px', 'widgetGap': '12px'})
add('page', 'ui-page', name='Garden', ui=nid('base'), path='/garden', icon='sprout', layout='grid',
    theme=nid('theme'), order=1, className='', visible='true', disabled='false')
for k, (name, order, width) in {'g_soil': ('Soil moisture', 1, '6'), 'g_air': ('Air', 2, '6'),
                                'g_pump': ('Pump', 3, '6'), 'g_board': ('Board', 4, '6'),
                                'g_log': ('Pump log', 5, '12'), 'g_all': ('All sensors', 6, '12')}.items():
    add(k, 'ui-group', name=name, page=nid('page'), width=width, height='1', order=order, showTitle=True,
        className='', visible='true', disabled='false')
add('notify', 'ui-notification', ui=nid('base'), position='top right', colorDefault=False, color='#c63c2c',
    displayTime='15', showCountdown=True, outputs=0, allowDismiss=True, dismissText='Close', raw=False,
    className='', name='Alerts', z=TAB, x=1110, y=640, wires=[])

# ---------------------------------------------------------------- inputs
add('c_info', 'comment', 40, 40, name='Setup: read me', wires=None,
    info='1. Palette: install @flowfuse/node-red-dashboard (Dashboard 2.0).\n'
         '2. Edit the "Mosquitto" broker node (host/port/user) - default localhost:1883.\n'
         '3. On the garden board: MQTT panel -> server = this Mosquitto, topic prefix "garden".\n'
         '   If you change the prefix, change the 3 MQTT-in topics here too.\n'
         '4. Dashboard: http://<node-red>:1880/dashboard/garden\n'
         'Mosquitto 2.x needs "listener 1883" + "allow_anonymous true" (or a password file) '
         'to accept the board.')
add('in_state', 'mqtt in', 140, 160, [['f_state', 'f_alerts']], name='garden/state', topic='garden/state', qos='0',
    datatype='json', broker=nid('broker'), nl=False, rap=True, rh=0, inputs=0)
add('in_event', 'mqtt in', 140, 560, [['f_event', 'f_alerts']], name='garden/pump/event', topic='garden/pump/event',
    qos='0', datatype='utf8', broker=nid('broker'), nl=False, rap=True, rh=0, inputs=0)
add('in_status', 'mqtt in', 140, 640, [['f_status', 'f_alerts']], name='garden/status', topic='garden/status',
    qos='0', datatype='utf8', broker=nid('broker'), nl=False, rap=True, rh=0, inputs=0)

# ---------------------------------------------------------------- split the state JSON
OUTS = ['gA', 'gB', 'tA', 'tB', 'chSoil', 'gT', 'gRH', 'chAir', 'tPump', 'tBoard', 'tblAll', 'chAll']
STATE_JS = r'''// garden/state -> one message per widget
// Items can be switched off on the board (MQTT panel "Data to send"): an unticked soil sensor is
// null in its slot, other groups (air, pump, battery, board info) are simply missing.
const d = msg.payload;
if (typeof d !== 'object' || d === null) return null;
const ts = Date.now();
const soil = d.soil || [];
const sA = soil[0] || {}, sB = soil[1] || {};
const txt = s => s.name === undefined ? '-' :
    `${s.band_text} · F ${s.full_F}% · water at ${s.water_at}% · ` +
    `−${s.dry_rate_pts_day} pts/day${s.rate_measured ? '' : ' (est.)'} · raw ${s.raw}`;
const soilChart = soil.filter(s => s).map(s => ({topic: s.name, payload: s.pct, timestamp: ts}));
const a = d.air || {};
const airChart = [];
if (a.temp_c != null) airChart.push({topic: 'Air °C', payload: a.temp_c, timestamp: ts});
if (a.rh_pct != null) airChart.push({topic: 'Air RH %', payload: a.rh_pct, timestamp: ts});
const p = d.pump;
const pump = !p ? 'not sent' :
    (p.fault ? '⚠ LOCKED: ' + p.fault :
     (p.on ? 'RUNNING' : (p.busy ? 'watching moisture' : 'ready')) +
     ` · mode ${p.mode} · today ${p.today_s} / ${p.day_max_s} s` + (p.tank_empty ? ' · TANK EMPTY' : ''));
const board = `${d.time || 'clock not set'} · ${d.samples} samples averaged` +
    (d.wifi_rssi != null ? ` · WiFi ${d.wifi_rssi} dBm` : '') +
    (d.heap_free != null ? ` · heap ${Math.round(d.heap_free / 1024)} KB` : '') +
    (d.battery ? ` · battery ${d.battery.v} V (${d.battery.state})` : '') +
    (d.recording ? ` · recording ${d.recording}` : '');
// every sensor on the board, however many are configured (Sensors panel): table + chart
const gs = d.sensors || {};
const rows = soil.filter(s => s).map(s => ({name: s.name, value: s.pct, unit: '%', info: s.band_text}));
Object.keys(gs).forEach(k => rows.push({name: gs[k].name, value: gs[k].value, unit: gs[k].unit, info: k}));
const allChart = Object.keys(gs).filter(k => gs[k].value != null).map(k =>
    ({topic: gs[k].name + (gs[k].unit ? ' (' + gs[k].unit + ')' : ''), payload: gs[k].value, timestamp: ts}));
return [
    sA.pct == null ? null : {payload: sA.pct},
    sB.pct == null ? null : {payload: sB.pct},
    {payload: txt(sA)},
    {payload: txt(sB)},
    soilChart,          // array = one message per sensor on this output
    a.temp_c == null ? null : {payload: a.temp_c},
    a.rh_pct == null ? null : {payload: a.rh_pct},
    airChart,
    {payload: pump},
    {payload: board},
    rows.length ? {payload: rows} : null,
    allChart
];'''
add('f_state', 'function', 380, 160, [[o] for o in OUTS], name='split state', func=STATE_JS, outputs=len(OUTS),
    timeout=0, noerr=0, initialize='', finalize='', libs=[])

SOIL_SEG = [{'from': '0', 'color': '#c63c2c'}, {'from': '45', 'color': '#e0711f'}, {'from': '60', 'color': '#d3a21a'},
            {'from': '70', 'color': '#2f9a58'}, {'from': '90', 'color': '#3d7fc4'}]
def gauge(name, group, order, title, units, mn, mx, seg, y):
    add(name, 'ui-gauge', 700, y, [], group=nid(group), order=order, width=3, height=3, gtype='gauge-half',
        gstyle='needle', title=title, units=units, icon='', prefix='', suffix='', segments=seg, min=mn, max=mx,
        sizeThickness=16, sizeGap=4, sizeKeyThickness=8, styleRounded=True, styleGlow=False, className='')
def text(name, group, order, label, y):
    add(name, 'ui-text', 700, y, [], group=nid(group), order=order, width=0, height=0, name=label, label=label,
        format='{{msg.payload}}', layout='col-center', style=False, font='', fontSize=14, color='#1d2420',
        wrapText=True, className='')
def chart(name, group, order, label, ylabel, ymin, ymax, colors, y):
    add(name, 'ui-chart', 700, y, [], group=nid(group), name=label, label=label, order=order, chartType='line',
        category='topic', categoryType='msg', xAxisLabel='', xAxisProperty='', xAxisPropertyType='timestamp',
        xAxisType='time', xAxisFormat='', xAxisFormatType='auto', yAxisLabel=ylabel, yAxisProperty='payload',
        yAxisPropertyType='msg', ymin=ymin, ymax=ymax, action='append', stackSeries=False, pointShape='false',
        pointRadius=2, showLegend=True, removeOlder='7', removeOlderUnit='86400', removeOlderPoints='',
        colors=colors, textColor=['#666666'], textColorDefault=True, gridColor=['#e5e5e5'],
        gridColorDefault=True, width=12, height=5, className='', interpolation='linear')

gauge('gA', 'g_soil', 1, 'Sensor A', '%', 0, 100, SOIL_SEG, 60)
gauge('gB', 'g_soil', 2, 'Sensor B', '%', 0, 100, SOIL_SEG, 100)
text('tA', 'g_soil', 3, 'Sensor A', 140)
text('tB', 'g_soil', 4, 'Sensor B', 180)
chart('chSoil', 'g_soil', 5, 'Soil moisture, 7 days', '%', '0', '100', ['#2f7d4f', '#3d7fc4'], 220)
gauge('gT', 'g_air', 1, 'Air temperature', '°C', 10, 45,
      [{'from': '10', 'color': '#3d7fc4'}, {'from': '15', 'color': '#2f9a58'}, {'from': '28', 'color': '#d3a21a'},
       {'from': '32', 'color': '#c63c2c'}], 260)
gauge('gRH', 'g_air', 2, 'Air humidity', '%', 0, 100,
      [{'from': '0', 'color': '#e0711f'}, {'from': '40', 'color': '#d3a21a'}, {'from': '60', 'color': '#2f9a58'},
       {'from': '85', 'color': '#3d7fc4'}], 300)
chart('chAir', 'g_air', 3, 'Air, 7 days', '°C / %', '', '', ['#e0711f', '#3d7fc4'], 340)
text('tPump', 'g_pump', 1, 'Pump', 380)
add('tblAll', 'ui-table', 700, 460, [], group=nid('g_all'), name='All sensors', label='', order=1, width=12, height=4,
    maxrows=12, passthru=False, autocols=False, showSearch=False, deselect=True, selectionType='none',
    columns=[{'title': 'Sensor', 'key': 'name', 'keyType': 'key', 'type': 'text', 'width': '', 'align': 'start'},
             {'title': 'Value', 'key': 'value', 'keyType': 'key', 'type': 'text', 'width': '90', 'align': 'end'},
             {'title': 'Unit', 'key': 'unit', 'keyType': 'key', 'type': 'text', 'width': '60', 'align': 'start'},
             {'title': '', 'key': 'info', 'keyType': 'key', 'type': 'text', 'width': '', 'align': 'start'}],
    mobileBreakpoint='sm', mobileBreakpointType='defaults', action='replace', className='')
chart('chAll', 'g_all', 2, 'Other sensors, 7 days', '', '', '', ['#2f7d4f', '#3d7fc4', '#e0711f', '#c63c2c', '#d3a21a', '#7a5cc6'], 500)
text('tBoard', 'g_board', 2, 'Last data', 420)

# ---------------------------------------------------------------- pump log + board status
EVENT_JS = r'''// keep the last 20 pump log lines for the table (newest first)
const log = flow.get('pumpLog') || [];
const line = String(msg.payload);
const m = line.match(/^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d|up \d+s) (.*)$/);
log.unshift({time: m ? m[1] : '', event: m ? m[2] : line});
log.splice(20);
flow.set('pumpLog', log);
return {payload: log};'''
add('f_event', 'function', 380, 560, [['tbl']], name='pump log', func=EVENT_JS, outputs=1, timeout=0, noerr=0,
    initialize='', finalize='', libs=[])
add('tbl', 'ui-table', 700, 560, [], group=nid('g_log'), name='Pump log', label='', order=1, width=12, height=8,
    maxrows=20, passthru=False, autocols=False, showSearch=False, deselect=True, selectionType='none',
    columns=[{'title': 'Time', 'key': 'time', 'keyType': 'key', 'type': 'text', 'width': '170', 'align': 'start'},
             {'title': 'Event', 'key': 'event', 'keyType': 'key', 'type': 'text', 'width': '', 'align': 'start'}],
    mobileBreakpoint='sm', mobileBreakpointType='defaults', action='replace', className='')
STATUS_JS = r'''const on = String(msg.payload) === 'online';
return {payload: on ? '🟢 online' : '🔴 offline (board lost power or WiFi)'};'''
add('f_status', 'function', 380, 640, [['tStatus']], name='board status', func=STATUS_JS, outputs=1, timeout=0,
    noerr=0, initialize='', finalize='', libs=[])
text('tStatus', 'g_board', 1, 'Board', 640)
nodes[-1].update(x=700, y=640)

# ---------------------------------------------------------------- alerts (only on changes)
ALERT_JS = r'''// Pop-up alerts, sent once when a state changes (not on every message).
const prev = context.get('prev') || {};
const out = [];
const say = t => out.push({payload: t});
if (msg.topic.endsWith('/state') && typeof msg.payload === 'object') {
    const d = msg.payload;
    (d.soil || []).forEach(s => {
        if (!s) return;                     // sensor not sent
        const k = 'band_' + s.name;
        if (prev[k] !== undefined && prev[k] !== s.band && ['now', 'dry'].includes(s.band))
            say(`${s.name}: ${s.band_text} (${s.pct}%)`);
        prev[k] = s.band;
    });
    const f = d.pump && d.pump.fault;
    if (f && f !== prev.fault) say('Pump locked: ' + f);
    prev.fault = f || null;
    const t = d.air && d.air.temp_c;
    const hot = t != null && t > 32;
    if (hot && !prev.hot) say(`Hot: ${t} °C - above 32 °C cucumbers drop flowers`);
    prev.hot = hot;
} else if (msg.topic.endsWith('/status')) {
    const s = String(msg.payload);
    if (s === 'offline' && prev.status === 'online') say('Garden board went offline');
    prev.status = s;
} else if (msg.topic.endsWith('/pump/event') && /DRY RUN/.test(String(msg.payload))) {
    say(String(msg.payload));
}
context.set('prev', prev);
return [out];'''
add('f_alerts', 'function', 860, 640, [['notify']], name='alerts on change', func=ALERT_JS, outputs=1, timeout=0,
    noerr=0, initialize='', finalize='', libs=[])

json.dump(nodes, open(sys.argv[1], 'w'), indent=1, ensure_ascii=False)
print(len(nodes), 'nodes ->', sys.argv[1])
