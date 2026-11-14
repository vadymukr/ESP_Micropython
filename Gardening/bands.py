# Watering bands for the garden logger (MicroPython, ~2-3 KB heap).
#
# Per sensor: band from the live reading relative to the soil's "full" value F and the growth stage,
#   wet / drain / good / soon / now / dry
# plus a drying trend (48 half-hour slots = 24 h) for "next watering in ~X h", and a watering
# detector (reading jumps >= WATER_JUMP points within ~10 min) that drives the "draining" state.
#
# Power-loss safe: settings in DATA_DIR/bands.json (changed from the UI), trend + last watering
# times in DATA_DIR/trend.json (every 30 min). Both are written to a .tmp file and renamed, so a
# power cut leaves the old or the new version, never half a file; a bad file -> settings.py defaults.
import json
import os
import time
from array import array
import settings as S

STAGES = (('Germination', 5), ('Seedling / transplant', 10), ('Vine growth', 15),
          ('Flowering & fruiting', 10), ('Late harvest', 10))     # (name, refill offset w below F)
BANDS = {'wet': 'Too wet', 'drain': 'Draining', 'good': 'Good', 'soon': 'Water soon',
         'now': 'Water now', 'dry': 'Drought'}
CFG = S.DATA_DIR + '/bands.json'
TREND = S.DATA_DIR + '/trend.json'
NSLOT = 48                    # 24 h of half-hour slots
SLOT_S = 1800
MISSING = -1.0


def _save(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(obj, f)
    os.rename(tmp, path)          # atomic on LittleFS


def _load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


class Bands:
    def __init__(self, n, uptime, time_ok):
        self.n, self.up, self.time_ok = n, uptime, time_ok
        self.stage = S.BANDS_STAGE
        self.full = list(S.BANDS_FULL[:n])     # F per sensor: each sensor is judged against its own F
        self.link = False                      # optional: F[i] = F[0] + off[i] (edit one number)
        self.off = [x - self.full[0] for x in self.full]
        c = _load(CFG)
        if c:
            try:
                st, fl = int(c['stage']), [int(x) for x in c['full']]
                if 0 <= st < len(STAGES) and len(fl) == n and all(10 <= x <= 100 for x in fl):
                    self.stage, self.full = st, fl
                    self.link = bool(c.get('link', False))
                    self.off = [x - fl[0] for x in fl]
            except (KeyError, TypeError, ValueError):
                pass
        default = S.DRY_RATE_PER_DAY / 24
        self.ring = [array('f', [MISSING] * NSLOT) for _ in range(n)]   # % at the end of each slot
        self.head = 0                  # next slot to write
        self.slots = 0                 # slots written since boot
        self.rate = [default] * n      # points per hour (positive = drying), last good value
        self.rate_ok = [False] * n     # computed from ~a day of usable data
        self.have = [0] * n            # usable half-hour slots behind the current rate
        self.watered = [None] * n      # uptime() of the last detected watering
        self.acc = [0.0] * n
        self.acc_n = 0
        self.slot_end = uptime() + SLOT_S
        self.lo = [[None, None] for _ in range(n)]    # min reading of this / previous 5-min bucket
        self.lo_end = uptime() + 300
        self.loaded = False            # trend.json merged (needs a valid clock)
        self.tp = 0                    # unix time of the last slot push (0 = unknown)
        self.last = None               # latest readings, saved for the next deep-sleep wake
        self.msg = ''

    # ---------------------------------------------------------------- settings from the UI
    def set(self, stage, full, link=False):
        self.stage, self.full, self.link = stage, full, link
        self.off = [x - full[0] for x in full]
        _save(CFG, {'stage': stage, 'full': full, 'link': link})

    def w(self):
        return STAGES[self.stage][1]

    # ---------------------------------------------------------------- per sample (every ~2 s)
    def update(self, pcts):
        now = self.up()
        self.last = pcts
        if not self.loaded and self.slots == 0 and self.time_ok():
            self._restore(now)
        for i, v in enumerate(pcts):
            self.acc[i] += v
            lo = self.lo[i]
            base = min(x for x in lo if x is not None) if lo[0] is not None or lo[1] is not None else v
            wt = self.watered[i]
            if v - base >= S.WATER_JUMP and (wt is None or now - wt > 1800):
                self.watered[i] = now             # watering: big rise within ~5-10 min (once per 30 min)
                lo[0] = lo[1] = v                 # no re-trigger on the same watering
                print('bands: watering detected on sensor', i, '%.0f -> %.0f' % (base, v))
            elif lo[0] is None or v < lo[0]:
                lo[0] = v
        self.acc_n += 1
        if now >= self.lo_end:
            self.lo_end = now + 300
            for lo in self.lo:
                lo[1], lo[0] = lo[0], None
        if now >= self.slot_end:
            self.slot_end += SLOT_S
            if self.slot_end <= now:
                self.slot_end = now + SLOT_S
            self._push([a / self.acc_n for a in self.acc])
            self.acc = [0.0] * self.n
            self.acc_n = 0
            self._persist()

    def _push(self, vals):
        for i, v in enumerate(vals):
            self.ring[i][self.head] = v
        self.head = (self.head + 1) % NSLOT
        self.slots += 1
        if self.time_ok():
            self.tp = time.time()
        self._rates()

    def _rates(self):
        """Least-squares slope over valid slots, excluding the draining window after a watering.
        Needs >= 40 of 48 slots (~a full day): soil dries mostly in the morning, so a slope over part
        of a day (e.g. a flat night) is badly biased. Until then the last good / default rate is used."""
        now = self.up()
        for i in range(self.n):
            r, ys = self.ring[i], []
            w = self.watered[i]
            for k in range(NSLOT):                # oldest -> newest
                age = (NSLOT - k) * SLOT_S        # seconds before now (approx.)
                y = r[(self.head + k) % NSLOT]
                if y == MISSING or (w is not None and now - age < w + S.DRAIN_H * 3600):
                    continue                      # skip gaps, and before/while draining after watering
                ys.append((k, y))
            self.have[i] = len(ys)
            if len(ys) < 40:
                self.rate_ok[i] = False
                continue
            mx = sum(k for k, _ in ys) / len(ys)
            my = sum(y for _, y in ys) / len(ys)
            sxx = sum((k - mx) ** 2 for k, _ in ys)
            slope = sum((k - mx) * (y - my) for k, y in ys) / sxx     # points per slot
            per_h = -slope * 3600 / SLOT_S
            if per_h > 0.02:                      # drying measurably (> ~0.5 point/day)
                self.rate[i], self.rate_ok[i] = per_h, True
            else:
                self.rate_ok[i] = False

    # ---------------------------------------------------------------- persistence
    def _persist(self):
        if not self.time_ok():
            return                                # no real time yet: cannot align after a restart
        t = time.time()
        u = self.up()
        try:
            _save(TREND, {
                't': t, 'head': self.head,
                'v': [[round(x, 1) for x in r] for r in self.ring],
                'rate': [round(x, 4) for x in self.rate],
                'wat': [None if x is None else t - (u - x) for x in self.watered],
                'tp': self.tp, 'last': [round(x, 1) for x in self.last] if self.last else None})
        except OSError as e:
            print('bands: save failed', e)

    def _restore(self, now):
        self.loaded = True
        d = _load(TREND)
        if not d:
            self.msg = 'no saved trend - collecting data'
            return
        try:
            t = time.time()
            gap = t - d['t']
            self.tp = d.get('tp') or 0
            for i in range(self.n):
                self.rate[i] = float(d['rate'][i])
                wt = d['wat'][i]
                if wt is not None and t - wt < 86400:
                    self.watered[i] = now - (t - wt)
            if 0 <= gap < 6 * 3600:               # short outage: keep the history, mark the gap
                head = int(d['head'])
                for i in range(self.n):
                    for k, y in enumerate(d['v'][i][:NSLOT]):
                        self.ring[i][k] = MISSING if y is None else float(y)
                self.head = head % NSLOT
                for _ in range(int(gap // SLOT_S)):
                    for i in range(self.n):
                        self.ring[i][self.head] = MISSING
                    self.head = (self.head + 1) % NSLOT
                self._rates()
                self.msg = 'trend restored after %d min outage' % (gap // 60)
            else:
                self.msg = 'outage %d h - using last drying rate until new data' % (gap // 3600)
        except (KeyError, TypeError, ValueError, IndexError):
            self.msg = 'saved trend unreadable - collecting data'
        print('bands:', self.msg)

    def cycle_step(self, pcts):
        """One reading per deep-sleep wake (sleep-cycle mode). Here up() must be unix time, so
        watering times and the draining window stay valid across sleeps (the RTC keeps running)."""
        now = self.up()
        d = _load(TREND)
        last = lt = None
        self.tp = now
        if d:
            try:
                for i in range(self.n):
                    for k, y in enumerate(d['v'][i][:NSLOT]):
                        self.ring[i][k] = float(y)
                    self.rate[i] = float(d['rate'][i])
                    self.watered[i] = d['wat'][i]
                self.head = int(d['head']) % NSLOT
                self.tp = d.get('tp') or now
                last, lt = d.get('last'), d['t']
            except (KeyError, TypeError, ValueError, IndexError):
                last = None
        if last and now - lt < 3 * 3600:          # watering = rise since the previous wake
            for i, v in enumerate(pcts):
                wt = self.watered[i]
                if v - last[i] >= S.WATER_JUMP and (wt is None or now - wt > 1800):
                    self.watered[i] = now
                    print('bands: watering detected on sensor', i)
        k = int((now - self.tp) // SLOT_S)
        if k >= 1:                                # one trend point per 30 min; mark longer gaps
            for _ in range(min(k - 1, NSLOT)):
                for i in range(self.n):
                    self.ring[i][self.head] = MISSING
                self.head = (self.head + 1) % NSLOT
            self._push(pcts)
            self.tp = now
        self.last = list(pcts)
        self._persist()

    # ---------------------------------------------------------------- evaluation
    def band(self, i, v):
        F, w = self.full[i], self.w()
        if v >= F + 5:
            wt = self.watered[i]
            if wt is not None and self.up() - wt < S.DRAIN_H * 3600:
                return 'drain'
            return 'wet'
        if v >= F - w:
            return 'good'
        if v >= F - w - 10:
            return 'soon'
        if v >= F - w - 20:
            return 'now'
        return 'dry'

    def next_text(self, i, v, b):
        """Human text for 'next watering' and whether it is an estimate."""
        if b == 'drain':
            return 'draining - watered %s ago' % _ago(self.up() - self.watered[i]), False
        if b == 'wet':
            return 'let it drain, check drainage', False
        if b == 'soon':
            return '<b>today</b>, early morning', False
        if b in ('now', 'dry'):
            return '<b>water now</b>, deeply', False
        hrs = (v - (self.full[i] - self.w())) / self.rate[i]
        txt = 'in about <b>%d h</b>' % max(1, round(hrs)) if hrs < 36 else 'in about <b>%d days</b>' % round(hrs / 24)
        return txt, not self.rate_ok[i]

    def trend_text(self, i):
        d = self.rate[i] * 24
        if self.rate_ok[i]:
            return '&minus;%.1f pts/day' % d
        return '&minus;%.1f pts/day &middot; estimated (%d of 20 h data)' % (d, self.have[i] // 2)


def _ago(s):
    s = int(s)
    return '%d min' % (s // 60) if s < 7200 else '%d h' % (s // 3600)
