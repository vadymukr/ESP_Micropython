# Pulse flow meter (YF-S201, YF-S401 and similar: hall sensor; YF-S401 = 5880 pulses/L, F = 98 x Q).
# ppl = pulses per litre (YF-S201 ~450, YF-B1 ~660, check the datasheet). Channels: flow L/min and
# litres since the last restart. 5 V sensors: feed 5 V, but the signal must reach the ESP32 at 3.3 V
# (open-collector output with the internal pull-up is fine; push-pull 5 V output needs a divider).
# Spec: docs/SPEC.md#driver-interface
import time
from machine import Pin


class Driver:
    """Registry driver: Pulse flow meter (YF-S201, YF-S401 and similar. Interface: docs/SPEC.md#driver-interface."""
    def __init__(self, e, ctx=None):
        """Set up from the registry entry e (pins / address / settings); ctx = app.sens (shared I2C bus, free
        UARTs, air readings)."""
        self.e = e
        self.n = 0
        self.p = Pin(e['pin'], Pin.IN, Pin.PULL_UP)
        self.p.irq(trigger=Pin.IRQ_FALLING, handler=self._pulse)
        self.n0 = 0
        self.t0 = time.ticks_ms()

    def _pulse(self, _):
        """Pin interrupt: count one pulse (no allocation in the handler)."""
        self.n += 1

    def chans(self):
        """Channels this sensor reports: [(key, label, unit, decimals)]."""
        n = self.e['name']
        d = 3 if float(self.e.get('ppl', 450)) > 1000 else 2      # small meters (YF-S401): more decimals
        return [('lpm', n + ' flow', 'L/min', d), ('l', n + ' total', 'L', d)]

    def read(self):
        """One value per channel, None where there is no valid reading this time."""
        ppl = float(self.e.get('ppl', 450)) or 450.0
        now, n = time.ticks_ms(), self.n
        dt = time.ticks_diff(now, self.t0) / 60000
        rate = (n - self.n0) / ppl / dt if dt > 0 else 0.0
        self.n0, self.t0 = n, now
        return [rate, n / ppl]

    def info(self):
        """Short status text for the dashboard card (pin / address, raw value or error)."""
        return 'GPIO%d &middot; %d pulses' % (self.e['pin'], self.n)
