# Pump checks (pump.Pump inherits Checks): did the water arrive (moisture rise or flow sensor), does
# the soil need water (auto mode), and the auto-watering pulse sequence.
# Spec: docs/SPEC.md#pump (dry-run protection, auto mode)
import settings as S


class Checks:
    """Dry-run protection and auto-watering decisions (mixed into pump.Pump)."""

    def flow_drv(self):
        """The linked flow sensor's driver, or None (not linked / not running)."""
        fid = self.cfg['flow']
        if not fid or not self.sens:
            return None
        for d in self.sens.gen:
            if d.e['id'] == fid and hasattr(d, 'n'):
                return d
        return None

    def _litres(self, d, n):
        """Litres from n pulses of flow driver d (its pulses per litre)."""
        return n / (float(d.e.get('ppl', 450)) or 450.0)


    def _evaluate(self):
        """Soak window over: did the water arrive?"""
        self.check_end = None
        rise = max(p - b for p, b in zip(self.peak, self.base)) if self.base else 0
        self.base = self.peak = None
        if self.flow_used:                        # flow sensor judged the run: moisture is information only
            self.log('moisture after watering: %+.1f points | %s' % (rise, self._vals()))
            return not self.cfg['fault']
        if self.rose:
            if self.cfg['fault']:
                self.save(fault='')
                self.log('moisture rose again - dry-run lock cleared')
            self.log('check OK: +%.1f points | %s' % (rise, self._vals()))
            return True
        if self.check_len < S.PUMP_CHECK_MIN_S:
            self.log('check skipped: run of %d s is too short to judge (+%.1f)' % (self.check_len, rise))
            return True
        f = ('no moisture rise after %d s of pumping (+%.1f points): tank empty, pipe blocked or '
             'pump failed?' % (self.check_len, rise))
        self.save(fault=f)
        self.pulses = 0
        self.log('DRY RUN: ' + f)
        return False

    def _need(self):
        """Auto: (needs water, reached target) from the watched sensors vs their F and w."""
        b = self.bands
        if b is None or self.ema is None:
            return False, True
        w = b.w()
        low = any(self.ema[i] <= b.full[i] - w for i in self.watched())
        full = any(self.ema[i] >= b.full[i] - self.cfg['target'] for i in self.watched())
        return low, full

    def _moist(self):
        """Schedule: skip when every watched sensor is already in the good band or wetter."""
        b = self.bands
        if b is None or self.ema is None:
            return False
        return all(self.ema[i] >= b.full[i] - b.w() for i in self.watched())


    def _flow_check(self, now):
        """Live flow rate over the last ~3 s; no flow after the grace time = stop and lock."""
        d, c = self.fd, self.cfg
        self.fq.append((now, d.n))
        while len(self.fq) > 2 and now - self.fq[0][0] > 3:
            self.fq.pop(0)
        dt = self.fq[-1][0] - self.fq[0][0]
        if dt > 0:
            self.lpm = self._litres(d, self.fq[-1][1] - self.fq[0][1]) * 60 / dt
        if now - self.run_t0 >= c['flow_wait'] and self.lpm is not None and self.lpm < c['flow_min']:
            f = ('no water flow (%.2f L/min) after %d s of pumping: tank empty, pipe blocked or pump failed?'
                 % (self.lpm, now - self.run_t0))
            self.stop('no flow')
            self.save(fault=f)
            self.pulses = 0
            self.log('DRY RUN: ' + f)

    def _auto_next(self):
        """Auto mode, soak window over: next pulse, target reached, or stop and lock after max_pulses."""
        low, full = self._need()
        if full:
            self.log('auto: target reached after %d pulse(s) | %s' % (self.pulses, self._vals()))
            self.pulses = 0
            return
        if self.pulses >= self.cfg['max_pulses']:
            self.log('auto: stopped after %d pulses, target not reached | %s' % (self.pulses, self._vals()))
            self.pulses = 0
            self.save(fault='auto: %d pulses did not bring the soil back to F-%d - check the dripper '
                            'position and the tank' % (self.cfg['max_pulses'], self.cfg['target']))
            return
        self.pulses += 1
        self.msg = self.start(self.cfg['pulse_s'], 'auto')
        if self.msg:
            self.log('auto pulse not run: ' + self.msg)
            self.pulses = 0
            self.hold = self.up() + 1800          # retry in 30 min, not every second

