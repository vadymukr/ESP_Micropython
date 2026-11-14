# Sensor sampling: soil sensors on ADC pins, registry sensors (drv_*.py drivers) and the auto-detected
# air sensor on the shared I2C bus. One instance (app.sens), sampled by app.sample_loop().
# Spec: docs/SPEC.md#sensors
try:
    import asyncio
except ImportError:
    import uasyncio as asyncio
import time
from machine import ADC, Pin, I2C
import settings as S
import app
from util import uptime


class Sensors:
    """All sensor readings, refreshed by sample() every SAMPLE_S seconds (one task; HTTP handlers only
    read the values). Soil sensors in .ch / .raw, registry sensors in .gen / .gch / .gval, the air
    sensor in .t / .rh / .p; .acc_* sum readings between two CSV rows / MQTT messages."""

    def __init__(self):
        """Build the sensors from the registry (sensors.json); a sensor that fails is listed in .bad."""
        import sys
        import sensors as R                 # registry: read the list, then release the module
        self.reg = R.load()
        self.air_on = R.air_on()            # built-in air sensor not removed in the Sensors panel
        R = None
        del sys.modules['sensors']
        self.ch = []                         # soil sensors: (pin, adc, dry, wet, name, id)
        self.gen = []                        # other sensors: driver objects (drv_<type>.py)
        self.gch = []                        # their channels: (driver idx, chan idx, key, label, unit, dec, id)
        self.bad = []                        # (name, error) of sensors that did not start
        for e in self.reg:
            if e['type'] == 'soil':
                try:
                    self.ch.append(self._soil(e))
                except Exception as ex:
                    self.bad.append((e.get('name', '?'), str(ex)))
        if not self.ch:                      # bands / pump need a soil sensor: fall back to the template
            pin, dry, wet, name = S.SENSORS[0]
            self.ch.append(self._soil({'pin': pin, 'dry': dry, 'wet': wet, 'name': name, 'id': 'S1'}))
        self.raw = [0] * len(self.ch)
        self.t = self.rh = self.p = None
        self.updated = 0
        self.i2c = None
        self.clim = None                     # climate.Climate: AHT10 / Si70xx / BMP280, auto-detected
        self.clim_name = 'air sensor'
        self.clim_err = ''
        self._fails = 0                      # consecutive read failures
        self._backoff = S.AIR_RETRY_S[0]
        self._next_try = 0                   # uptime() of the next detect attempt
        self._clim_init()                    # also creates the shared I2C bus (self.i2c)
        self.uart_free = list(getattr(S, 'UART_IDS', (1, 2)))   # UART ids for UART sensors (chip / board)
        for e in self.reg:                   # other sensors: drivers get this object as ctx (ctx.i2c)
            if e['type'] == 'soil':
                continue
            try:
                d = __import__('drv_' + (e.get('drv') or e['type'])).Driver(e, self)
                gi = len(self.gen)
                self.gen.append(d)
                for k, (key, label, unit, dec) in enumerate(d.chans()):
                    self.gch.append((gi, k, key, label, unit, dec, e['id']))
            except Exception as ex:
                print('sensor %s not started: %r' % (e.get('name'), ex))
                self.bad.append((e.get('name', '?'), str(ex)))
        self.gval = [None] * len(self.gch)
        self.acc_reset()

    def air_addrs(self):
        """I2C addresses used by the auto-detected air sensor."""
        c = self.clim
        return [getattr(x, 'addr', None) for x in ((c.hum, c.baro) if c else ()) if x and getattr(x, 'addr', None)]

    @staticmethod
    def _soil(e):
        """A soil sensor tuple (pin, ADC, dry, wet, name, id) from its registry entry."""
        a = ADC(Pin(e['pin']))
        a.atten(ADC.ATTN_11DB)
        try:
            a.width(ADC.WIDTH_12BIT)         # classic ESP32; S3 / C3 / C6 are 12-bit already
        except AttributeError:
            pass
        return (e['pin'], a, e['dry'], e['wet'], e['name'], e['id'])

    def _bus_reset(self):
        """Free a bus held low by a half-reset sensor: clock SCL up to 9 times until SDA is released."""
        scl = Pin(S.I2C_SCL, Pin.OPEN_DRAIN, value=1)
        sda = Pin(S.I2C_SDA, Pin.IN, Pin.PULL_UP)
        for _ in range(9):
            if sda.value():
                break
            scl(0)
            time.sleep_us(5)
            scl(1)
            time.sleep_us(5)
        try:
            self.i2c = I2C(0, scl=Pin(S.I2C_SCL), sda=Pin(S.I2C_SDA), freq=100000, timeout=S.I2C_TIMEOUT_US)
        except TypeError:                    # firmware without the timeout argument
            self.i2c = I2C(0, scl=Pin(S.I2C_SCL), sda=Pin(S.I2C_SDA), freq=100000)

    def _clim_init(self):
        """One quick attempt (4 address probes); on failure schedule the next one with back-off."""
        if not self.air_on:                  # removed: just the bus (OLED, I2C sensors)
            self._bus_reset()
            self.clim, self.clim_err, self._next_try = None, 'removed', 1 << 30
            return
        try:
            self._bus_reset()
            from climate import detect
            self.clim = detect(self.i2c)
            self.clim_name = self.clim.name
            self.clim_err = ''
            self._fails = 0
            self._backoff = S.AIR_RETRY_S[0]
        except Exception as e:
            self.clim = None
            self.clim_err = 'not found (%s)' % e
            self._next_try = uptime() + self._backoff
            self._backoff = min(self._backoff * 2, S.AIR_RETRY_S[1])

    def acc_reset(self):
        """Clear the running sums (after a CSV row / an MQTT message took the averages)."""
        self.acc_raw = [0] * len(self.ch)
        self.acc_g = [0.0] * len(self.gch)
        self.acc_gn = [0] * len(self.gch)
        self.acc_n = 0
        self.acc_t = self.acc_rh = self.acc_p = 0.0
        self.acc_tn = self.acc_hn = self.acc_pn = 0

    @staticmethod
    def pct(raw, dry, wet):
        """Soil moisture % from a raw ADC value and the sensor's dry / wet calibration (0-100)."""
        p = (dry - raw) * 100.0 / (dry - wet)
        return 0.0 if p < 0 else 100.0 if p > 100 else p

    async def sample(self):
        """Read every sensor once (ADC averages, registry drivers, air sensor) and add to the sums."""
        pwr = app.pwr
        sw = pwr is not None and pwr.sensor_switching()
        if sw:                               # power.py: sensors fed from GPIO only while measuring
            pwr.sensors(True)
            await asyncio.sleep_ms(S.SENSOR_SETTLE_MS)
        for i, c in enumerate(self.ch):
            a, s = c[1], 0
            for _ in range(S.ADC_SAMPLES):
                s += a.read()
                time.sleep_us(300)
            self.raw[i] = s // S.ADC_SAMPLES
        if self.gen:                         # other sensors (registry drivers)
            j = 0
            for d in self.gen:
                try:
                    vals = d.read()
                except Exception:
                    vals = None
                for v in (vals or [None] * len(d.chans())):
                    self.gval[j] = v
                    j += 1
        if sw:
            pwr.sensors(False)
        # Moisture is read above regardless; the air sensor must never hold up the loop.
        if self.clim is None and uptime() >= self._next_try:
            self._clim_init()
        if self.clim:
            try:
                self.clim.trigger()
                await asyncio.sleep_ms(85)
                self.t, self.rh, self.p = self.clim.result()
                self._fails = 0
            except Exception as e:
                self.t = self.rh = self.p = None
                self._fails += 1
                self.clim_err = 'read failed (%s)' % e
                if self._fails >= 3:         # 3 bad reads in a row: treat as unplugged, re-detect later
                    self.clim = None
                    self._fails = 0
                    self._next_try = uptime() + self._backoff
                    self._backoff = min(self._backoff * 2, S.AIR_RETRY_S[1])
        for i, r in enumerate(self.raw):
            self.acc_raw[i] += r
        for j, v in enumerate(self.gval):
            if v is not None:
                self.acc_g[j] += v
                self.acc_gn[j] += 1
        self.acc_n += 1
        if self.t is not None:
            self.acc_t += self.t
            self.acc_tn += 1
        if self.rh is not None:
            self.acc_rh += self.rh
            self.acc_hn += 1
        if self.p is not None:
            self.acc_p += self.p
            self.acc_pn += 1
        self.updated = uptime()
