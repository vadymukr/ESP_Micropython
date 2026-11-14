# Digital input: float switch, rain sensor (DO pin), door contact, button. Value 1 / 0.
# pull = 1: internal pull-up (switch to GND); invert = 1: report 1 when the pin is LOW.
# Spec: docs/SPEC.md#driver-interface
from machine import Pin


class Driver:
    """Registry driver: Digital input. Interface: docs/SPEC.md#driver-interface."""
    def __init__(self, e, ctx=None):
        """Set up from the registry entry e (pins / address / settings); ctx = app.sens (shared I2C bus, free
        UARTs, air readings)."""
        self.e = e
        self.p = Pin(e['pin'], Pin.IN, Pin.PULL_UP if int(e.get('pull', 1)) else None)

    def chans(self):
        """Channels this sensor reports: [(key, label, unit, decimals)]."""
        return [('d', self.e['name'], self.e.get('unit', ''), 0)]

    def read(self):
        """One value per channel, None where there is no valid reading this time."""
        v = self.p.value()
        return [1 - v if int(self.e.get('invert', 0)) else v]

    def info(self):
        """Short status text for the dashboard card (pin / address, raw value or error)."""
        return 'GPIO%d &middot; %s%s' % (self.e['pin'], 'pull-up' if int(self.e.get('pull', 1)) else 'no pull',
                                         ', inverted' if int(self.e.get('invert', 0)) else '')
