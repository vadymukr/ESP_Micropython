# 'Latest records' and 'Recordings' fragments - loaded per request by app.py, then released.
# Spec: docs/SPEC.md#recording
import os
import app
from app import esc, kb, rec
import settings as S


def _latest_csv():
    """The recording to show in Latest records (running one, else the newest file)."""
    if rec.fname:
        return rec.fname
    names = [x for x in os.listdir(S.DATA_DIR) if x.endswith('.csv')]
    return max(names) if names else ''


def frag_tail():
    """Last rows of the latest recording."""
    name = _latest_csv()
    if not name:
        return '<p class="muted">No recordings yet.</p>'
    path = S.DATA_DIR + '/' + name
    try:
        size = os.stat(path)[6]
        with open(path, 'rb') as f:
            head = f.readline().decode().strip().split(',')
            f.seek(max(0, size - 900))
            data = f.read(900).decode()
    except OSError:
        return '<p class="muted">Cannot read %s</p>' % esc(name)
    lines = data.split('\n')
    if size > 900:
        lines = lines[1:]                 # first line is partial
    rows = [l for l in lines if l and not l.startswith('timestamp')][-8:]
    if not rows:
        return '<p class="muted"><code>%s</code>: no rows yet (first row after one interval).</p>' % esc(name)
    keep = [i for i, h in enumerate(head) if h == 'timestamp' or h.startswith('air') or h.endswith('_pct')
            or (h[:1] == 'S' and '_' in h)]
    names = {'%s_%s' % (g[6], g[2]): '%s %s' % (esc(g[3]), esc(g[4])) for g in app.sens.gch}
    titles = {'timestamp': 'Time', 'air_temp_c': '&deg;C', 'air_rh_pct': 'RH %', 'air_hpa': 'hPa'}
    out = ['<p class="muted"><code>%s</code></p><table><tr>' % esc(name)]
    for i in keep:
        out.append('<th>%s</th>' % (titles.get(head[i]) or names.get(head[i]) or
                                   head[i].replace('_pct', ' %').replace('gpio', 'GPIO')))
    out.append('</tr>')
    for l in reversed(rows):
        v = l.split(',')
        out.append('<tr>')
        for i in keep:
            out.append('<td>%s</td>' % esc(v[i][11:] if i == 0 else v[i]) if i < len(v) else '<td></td>')
        out.append('</tr>')
    out.append('</table>')
    return ''.join(out)


def frag_files():
    """Recording files with download / delete."""
    names = sorted((x for x in os.listdir(S.DATA_DIR) if x.endswith('.csv')), reverse=True)
    if not names:
        return '<p class="muted">No recordings yet.</p>'
    out = ['<table>']
    for x in names[:30]:
        e = esc(x)
        live = rec.active and x == rec.fname
        out.append('<tr><td><a href="/dl?f=%s" download>%s</a></td><td>%s</td><td>' % (e, e, kb(os.stat(S.DATA_DIR + '/' + x)[6])))
        if live:
            out.append('<span class="muted">recording</span>')
        else:
            out.append('<span x-data="{c:0}"><button class="small" x-show="!c" @click="c=1">Delete</button>'
                       '<span x-show="c">Sure? <button class="small danger" hx-post="/files/delete" '
                       'hx-vals=\'{"f":"%s"}\' hx-target="#files">Yes</button> '
                       '<button class="small" @click="c=0">No</button></span></span>' % e)
        out.append('</td></tr>')
    out.append('</table>')
    if len(names) > 30:
        out.append('<p class="muted">%d older files not shown.</p>' % (len(names) - 30))
    return ''.join(out)


def files_oob():
    """Files list as an htmx out-of-band swap (after start / stop)."""
    return '<div id="files" hx-swap-oob="innerHTML">%s</div>' % frag_files()
