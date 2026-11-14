# Dashboard view of the modem (gsm.Gsm inherits View): the GSM card with signal bars and the last
# messages / calls, the one-line status for the System facts and the panel, and the event log.
# Spec: docs/SPEC.md#gsm
import time

ICON = {'in': '&#9993;', 'out': '&#8599;', 'call': '&#9742;', 'deny': '&#8856;'}   # envelope, out, phone, blocked


def esc_(t):
    """HTML-escape (the app's esc is not imported here: the card is drawn every 2 s)."""
    return str(t).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


class View:
    """Card, status line and event log of the modem (mixed into gsm.Gsm)."""

    def note(self, kind, num, what, res=''):
        """kind: 'in' SMS received, 'out' SMS sent, 'call', 'deny' (unknown number / wrong code)."""
        from app import stamp, time_ok
        self.log = (self.log + [(stamp()[5:16] if time_ok() else '', kind, num[-4:], what[:40], res[:60])])[-8:]


    def card(self):
        """Dashboard card (built here so it costs no RAM while the modem is off)."""
        q = self.csq if self.csq is not None and self.csq != 99 else None
        if self.state == 'failed':
            band, badge = 'dry', 'Failed'
        elif self.net:
            band, badge = 'good', 'Online'
        else:
            band, badge = 'soon', self.state[:1].upper() + self.state[1:]
        bars = 0 if q is None else 1 if q < 10 else 2 if q < 15 else 3 if q < 20 else 4
        sig = ('<span class="sig">%s</span> %s' % (''.join('<i class="%s" style="height:%dpx"></i>'
               % ('on' if k < bars else '', 6 + 5 * k) for k in range(4)),
               '%d<small> dBm</small>' % (2 * q - 113) if q is not None else '&ndash;'))
        ans = 'not answering' if self.ok_t is None or time.time() - self.ok_t > 150 else 'answering'
        if self.state == 'failed':
            ans = esc_(self.err)
        o = ['<div class="card band %s"><h3><span>GSM <small>SIM800L</small></span><span class="badge">%s</span></h3>'
             '<div class="big">%s</div><dl class="facts"><dt>Modem</dt><dd>%s</dd><dt>Network</dt><dd>%s</dd>'
             '<dt>GPRS</dt><dd>%s</dd><dt>SMS</dt><dd>sent %d/%d today &middot; in %d &middot; calls %d</dd></dl>'
             % (band, badge, sig, ans, esc_(self.op) or ('searching' if not self.net else 'registered'),
                (esc_(self.ip) + (' &middot; TCP open' if self.tcp_on else '')) if self.ip else 'off',
                self.sent_day, self.c['max_day'], self.sms_in, self.calls)]
        if self.log:
            o.append('<p class="muted" style="font-size:12px;margin:6px 0 0">')
            for t, kind, n, what, res in reversed(self.log[-4:]):
                o.append('%s %s %s %s%s<br>' % (t[6:], ICON.get(kind, ''), '..' + esc_(n) if n and n != 'all' else 'to all'
                         if n else '', esc_(what), ' &rarr; ' + esc_(res) if res else ''))
            o.append('</p>')
        elif self.err and self.state != 'failed':
            o.append('<p class="warn">%s</p>' % esc_(self.err))
        o.append('</div>')
        return ''.join(o)

    def status(self):
        """One-line modem status (state, operator, signal, GPRS, SMS today, error)."""
        s = self.state
        if self.op:
            s += ' &middot; %s' % self.op
        if self.csq is not None and self.csq != 99:
            s += ' &middot; signal %d/31' % self.csq
        if self.ip:
            s += ' &middot; GPRS %s%s' % (self.ip, ' (TCP open)' if self.tcp_on else '')
        s += ' &middot; SMS today %d/%d' % (self.sent_day, self.c['max_day'])
        if self.err:
            s += ' &middot; <span class="warn">%s</span>' % self.err.replace('<', '&lt;')
        return s


