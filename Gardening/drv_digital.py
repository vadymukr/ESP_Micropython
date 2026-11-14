# Digital input: float switch, rain sensor (DO pin), door contact, button. Value 1 / 0.
# pull = 1: internal pull-up (switch to GND); invert = 1: report 1 when the pin is LOW.
from machine import Pin


class Driver:
    def __init__(self, e, ctx=None):
        self.e = e
        self.p = Pin(e['pin'], Pin.IN, Pin.PULL_UP if int(e.get('pull', 1)) else None)

    def chans(self):
        return [('d', self.e['name'], self.e.get('unit', ''), 0)]

    def read(self):
        v = self.p.value()
        return [1 - v if int(self.e.get('invert', 0)) else v]

    def info(self):
        return 'GPIO%d &middot; %s%s' % (self.e['pin'], 'pull-up' if int(self.e.get('pull', 1)) else 'no pull',
                                         ', inverted' if int(self.e.get('invert', 0)) else '')
