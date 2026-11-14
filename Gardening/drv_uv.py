# Analog UV sensor module (GUVA-S12SD with amplifier, "UV Ray Module"): UV index ~ Vout / 0.1 V.
# Output stays below ~1.1 V, so it connects straight to an ADC1 pin (module on 3.3 V).
# Spec: docs/SPEC.md#driver-interface
from machine import ADC, Pin


class Driver:
    """Registry driver: Analog UV sensor module (GUVA-S12SD with amplifier, "UV Ray Module"). Interface:
    docs/SPEC.md#driver-interface."""
    def __init__(self, e, ctx=None):
        """Set up from the registry entry e (pins / address / settings); ctx = app.sens (shared I2C bus, free
        UARTs, air readings)."""
        self.e = e
        self.adc = ADC(Pin(e['pin']))
        self.adc.atten(ADC.ATTN_11DB)
        self.mv = None

    def chans(self):
        """Channels this sensor reports: [(key, label, unit, decimals)]."""
        return [('uvi', self.e['name'] + ' UV index', '', 1), ('mv', self.e['name'] + ' signal', 'mV', 0)]

    def read(self):
        """One value per channel, None where there is no valid reading this time."""
        s = 0
        for _ in range(16):
            s += self.adc.read_uv()
        self.mv = s / 16000
        return [self.mv / 100, self.mv]

    def info(self):
        """Short status text for the dashboard card (pin / address, raw value or error)."""
        return 'GPIO%d' % self.e['pin']
