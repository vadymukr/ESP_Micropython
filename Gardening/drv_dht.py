# DHT11 / DHT22 (AM2302) temperature + humidity on one GPIO (10 kOhm pull-up if the module has none).
# The sensor allows one reading per ~2 s; in between the last values are returned.
# Spec: docs/SPEC.md#driver-interface
import time
from machine import Pin
import dht


class Driver:
    """Registry driver: DHT11 / DHT22 (AM2302) temperature + humidity on one GPIO (10 kOhm pull-up if th.
    Interface: docs/SPEC.md#driver-interface."""
    def __init__(self, e, ctx=None):
        """Set up from the registry entry e (pins / address / settings); ctx = app.sens (shared I2C bus, free
        UARTs, air readings)."""
        self.e = e
        self.s = (dht.DHT11 if int(e.get('model', 22)) == 11 else dht.DHT22)(Pin(e['pin']))
        self.last = [None, None]
        self.t = None

    def chans(self):
        """Channels this sensor reports: [(key, label, unit, decimals)]."""
        n = self.e['name']
        return [('t', n + ' temp', '&deg;C', 1), ('rh', n + ' humidity', '%', 0)]

    def read(self):
        """One value per channel, None where there is no valid reading this time."""
        now = time.ticks_ms()
        if self.t is None or time.ticks_diff(now, self.t) >= 2500:
            self.t = now
            try:
                self.s.measure()
                self.last = [self.s.temperature(), self.s.humidity()]
            except OSError:
                self.last = [None, None]
        return list(self.last)

    def info(self):
        """Short status text for the dashboard card (pin / address, raw value or error)."""
        return 'DHT%d &middot; GPIO%d' % (int(self.e.get('model', 22)), self.e['pin'])
