# SMS and calls of the SIM800L (gsm.Gsm inherits Sms): read and send SMS (daily limit), the allowed
# numbers, change-only alerts with a 3 h repeat guard, and calls (hang up, then the chosen action).
# Spec: docs/SPEC.md#gsm
import time
import settings as S


def _digits(n):
    """Last 9 digits of a phone number (+97150..., 050... compare equal)."""
    return ''.join(ch for ch in n if ch.isdigit())[-9:]



class Sms:
    """SMS / call methods of the modem (mixed into gsm.Gsm; uses its cmd() and settings self.c)."""

    async def _read_sms(self, i):
        """Read and delete stored SMS i: (sender number, text)."""
        r = await self.cmd('AT+CMGR=%d' % i, timeout=5000)
        await self.cmd('AT+CMGD=%d' % i)
        num, text = '', []
        for k, s in enumerate(r):
            if s.startswith('+CMGR:'):
                p = s.split('"')
                num = p[3] if len(p) > 3 else ''
                text = [x for x in r[k + 1:] if x != 'OK']
                break
        return num, ' '.join(text).strip()

    def allowed(self, num):
        """True if num is one of the allowed phone numbers (last 9 digits)."""
        d = _digits(num)
        return bool(d) and any(_digits(p) == d for p in self.c['phones'].split(',') if p.strip())

    async def send_sms(self, num, text):
        """Send one SMS (OSError when the daily limit is reached)."""
        self._day()
        if self.sent_day >= self.c['max_day']:
            raise OSError('daily SMS limit reached')
        await self.cmd('AT+CMGS="%s"' % num, timeout=60000, raw=text[:160].encode() + b'\x1a')   # +CMGS: n, OK
        self.sent_day += 1

    def _day(self):
        """Reset the daily SMS counter at local midnight."""
        d = time.localtime(time.time() + S.TZ_OFFSET_H * 3600)[:3]
        if d != self.day:
            self.day, self.sent_day = d, 0

    def queue(self, text, num=None):
        """Queue an SMS for num (None = all allowed numbers); at most 8 waiting."""
        if len(self.outbox) < 8:
            self.outbox.append((num, text))

    def alert(self, key, state, text):
        """Queue an SMS to all allowed numbers when 'key' enters a new alarming 'state'."""
        if self.prev.get(key) != state:
            self.prev[key] = state
            if state:
                t, k = time.time(), (key, state)
                if t - self.sent_at.get(k, -10 ** 9) >= 3 * 3600:   # band flicker: no repeat within 3 h
                    self.sent_at[k] = t
                    self.queue(text)

    def check(self, pcts):
        """SMS alerts on changes (every ~30 s from the sampler; gsm_cmd.check)."""
        from gsm_cmd import check
        check(self, pcts)

    async def _call(self, command):
        """Ringing: hang up, then (allowed number, once per call) do the configured action."""
        num = self.caller or ''
        self.caller, self.ringing = None, False
        try:
            await self.cmd('ATH', timeout=5000)
        except OSError:
            pass
        t = time.time()
        d = _digits(num)
        if t - self.call_t.get(d, -100) < 40:                     # later RINGs of the same call
            return
        self.call_t[d] = t
        self.calls += 1
        if not num:
            self.note('deny', '', 'call, no caller ID', 'hung up')    # never acts
            return
        if not self.allowed(num):
            print('gsm: call from unknown number', num, 'hung up')
            self.note('deny', num, 'call, unknown number', 'hung up')
            return
        a = self.c['call']
        if a == 'off':
            self.note('call', num, 'call', 'hung up')
            return
        from app import pump
        if a == 'status':
            cmdtxt = 'STATUS'
        elif a == 'toggle' and pump and pump.running:
            cmdtxt = 'STOP'
        else:
            cmdtxt = 'PUMP'
        r = command(cmdtxt, True)
        self.note('call', num, 'call: ' + cmdtxt, r)
        if a == 'status' or self.c['call_sms']:
            self.queue(r, num)


