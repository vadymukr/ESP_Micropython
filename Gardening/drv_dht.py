# DHT11 / DHT22 (AM2302) temperature + humidity on one GPIO (10 kOhm pull-up if the module has none).
# The sensor allows one reading per ~2 s; in between the last values are returned.
import time
from machine import Pin
import dht


class Driver:
    def __init__(self, e, ctx=None):
        self.e = e
        self.s = (dht.DHT11 if int(e.get('model', 22)) == 11 else dht.DHT22)(Pin(e['pin']))
        self.last = [None, None]
        self.t = None

    def chans(self):
        n = self.e['name']
        return [('t', n + ' temp', '&deg;C', 1), ('rh', n + ' humidity', '%', 0)]

    def read(self):
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
        return 'DHT%d &middot; GPIO%d' % (int(self.e.get('model', 22)), self.e['pin'])
