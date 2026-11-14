# CSV recorder (feature 'recorder'): records the sensor averages to DATA_DIR/*.csv; started and
# stopped from the dashboard. Not loaded when the feature is off (settings.FEATURES / Features panel).
# Spec: docs/SPEC.md#recording
try:
    import asyncio
except ImportError:
    import uasyncio as asyncio
import json
import os
import time
import settings as S
from app import stamp, time_ok, flash_free, safe_csv

REC_STATE = S.DATA_DIR + '/rec.json'


class Recorder:
    """CSV recording of averaged readings to flash, resumable after a restart."""
    def __init__(self, sens):
        """State from rec.json (a recording that was running continues)."""
        self.sens = sens
        self.active = self.resume = False
        self.fname = ''
        self.interval = 60
        self.end = self.started = self.rows = self.next = 0
        self.msg = ''
        try:
            os.mkdir(S.DATA_DIR)
        except OSError:
            pass
        try:
            with open(REC_STATE) as f:
                st = json.load(f)
            if st.get('active') and safe_csv(st.get('fname')):
                self.fname, self.interval = st['fname'], st['interval']
                self.end, self.started = st['end'], st['started']
                self.resume = True           # picked up by run() once the clock is valid
        except (OSError, ValueError, KeyError):
            pass

    def path(self, name=None):
        """Full path of a recording file (default: the current one)."""
        return S.DATA_DIR + '/' + (name or self.fname)

    def _save(self):
        """Write the recording state (rec.json)."""
        with open(REC_STATE, 'w') as f:
            json.dump({'active': self.active or self.resume, 'fname': self.fname,
                       'interval': self.interval, 'end': self.end, 'started': self.started}, f)

    def _header(self):
        """CSV header: time, air, every soil sensor, every registry channel."""
        cols = ['timestamp', 'elapsed_s', 'air_temp_c', 'air_rh_pct', 'air_hpa']
        for c in self.sens.ch:
            cols.append('gpio%d_raw' % c[0])
            cols.append('gpio%d_pct' % c[0])
        for g in self.sens.gch:
            cols.append('%s_%s' % (g[6], g[2]))      # e.g. S3_v (registry sensor id + channel)
        cols.append('samples')
        return ','.join(cols) + '\n'

    def start(self, interval, hours, label):
        """Start a recording (interval s, hours 0 = until stopped); returns a message."""
        if self.active or self.resume:
            return 'Already recording.'
        if not time_ok():
            return 'Clock not set yet - wait a few seconds or reload the page.'
        if flash_free() < S.MIN_FREE_FLASH:
            return 'Not enough free flash - delete old recordings first.'
        now = time.time()
        name = stamp(now, False).replace('-', '').replace(' ', '_').replace(':', '')
        if label:
            name += '_' + label
        self.fname = name + '.csv'
        self.interval, self.started = interval, now
        self.end = now + int(hours * 3600) if hours > 0 else 0
        with open(self.path(), 'w') as f:
            f.write(self._header())
        self.rows = 0
        self.sens.acc_reset()
        self.next = now + interval
        self.active = True
        self.msg = ''
        self._save()
        print('rec: start', self.fname, interval, 's')
        return ''

    def stop(self, why='Stopped'):
        """Stop recording and note why in the status line."""
        self.active = self.resume = False
        self.msg = '%s at %s.' % (why, stamp())
        self._save()
        print('rec:', self.msg)

    def _row(self, now):
        """Append one row of the averages since the last row, then clear the sums."""
        s = self.sens
        if not s.acc_n:
            return
        p = [stamp(now), str(now - self.started)]
        p.append('%.2f' % (s.acc_t / s.acc_tn) if s.acc_tn else '')
        p.append('%.1f' % (s.acc_rh / s.acc_hn) if s.acc_hn else '')
        p.append('%.1f' % (s.acc_p / s.acc_pn) if s.acc_pn else '')
        for i, c in enumerate(s.ch):
            r = s.acc_raw[i] / s.acc_n
            p.append('%.1f' % r)
            p.append('%.1f' % s.pct(r, c[2], c[3]))
        for j, g in enumerate(s.gch):
            p.append(('%%.%df' % g[5]) % (s.acc_g[j] / s.acc_gn[j]) if s.acc_gn[j] else '')
        p.append(str(s.acc_n))
        with open(self.path(), 'a') as f:
            f.write(','.join(p) + '\n')
        self.rows += 1
        s.acc_reset()

    def _count_rows(self):
        """Rows already in the file (resuming after a restart)."""
        n, buf = 0, bytearray(512)
        try:
            with open(self.path(), 'rb') as f:
                while True:
                    k = f.readinto(buf)
                    if not k:
                        break
                    n += bytes(buf[:k]).count(b'\n')
        except OSError:
            pass
        return max(0, n - 1)

    async def run(self):
        """Task: a row every interval; stops at the end time, on low flash or a full file."""
        while True:
            await asyncio.sleep(1)
            try:
                self._step(time.time())
            except Exception as e:
                self.stop('Stopped: error %s' % e)

    def _resume(self, now):
        """First second with a valid clock after a restart: continue the interrupted recording."""
        self.resume = False
        if self.end and now >= self.end:
            return self.stop('Finished (while powered off)')
        self.rows = self._count_rows()
        self.sens.acc_reset()
        self.next = now + self.interval
        self.active = True
        self.msg = 'Resumed after restart at %s.' % stamp()
        print('rec:', self.msg)

    def _step(self, now):
        """One second of the task: resume, write a row when due, stop on end time / flash / file size."""
        if self.resume and time_ok():
            self._resume(now)
        if not self.active:
            return
        if now >= self.next:
            self._row(now)
            self.next += self.interval
            if self.next <= now:                 # clock jumped forward
                self.next = now + self.interval
            if flash_free() < S.MIN_FREE_FLASH:
                return self.stop('Stopped: flash nearly full')
            if os.stat(self.path())[6] > S.MAX_FILE_BYTES:
                return self.stop('Stopped: file size limit reached')
        if self.end and now >= self.end:
            self.stop('Finished')
