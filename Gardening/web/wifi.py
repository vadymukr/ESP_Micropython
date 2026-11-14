# WiFi manager: joins the saved network (wifi.json), falls back to a setup access point with a
# captive DNS where the network is chosen from the web page, keeps the clock set by NTP.
# Spec: docs/SPEC.md#wifi
try:
    import asyncio
except ImportError:
    import uasyncio as asyncio
import gc
import json
import os
import network
import settings as S
import app
import util
from util import uptime, stamp


def _stat(name, default):
    """A network.STAT_* constant, or its usual value on firmware that lacks it."""
    return getattr(network, name, default)


FAIL_REASON = {
    _stat('STAT_WRONG_PASSWORD', 202): 'wrong password',
    _stat('STAT_NO_AP_FOUND', 201): 'network not found (2.4 GHz only)',
    _stat('STAT_CONNECT_FAIL', 203): 'connection failed',
    _stat('STAT_HANDSHAKE_TIMEOUT', 204): 'handshake timeout (check password)',
    _stat('STAT_ASSOC_FAIL', 203): 'association failed',
    _stat('STAT_BEACON_TIMEOUT', 200): 'signal lost',
}


_dns_on = False


def _dns_start():
    """Captive DNS task while the setup AP is up (captive.py, unloaded when the AP goes down)."""
    global _dns_on
    if not _dns_on:
        _dns_on = True
        asyncio.create_task(_dns())


async def _dns():
    """Task: answer every DNS query with the AP address while the setup AP is up."""
    global _dns_on
    import sys
    import captive
    try:
        await captive.run(app.net)
    except Exception as e:
        print('dns error:', e)
    finally:
        captive = None
        del sys.modules['captive']
        _dns_on = False


class Net:
    """STA with saved credentials; falls back to a setup AP (+ captive DNS) where the user
    scans and joins a network from the web page. mDNS (garden.local) is answered by the
    MicroPython built-in responder using network.hostname()."""

    def __init__(self):
        """Interfaces and saved credentials (wifi.json); nothing connects until run()."""
        try:
            network.country(S.WIFI_COUNTRY)        # before interfaces start (ch 12-13)
            network.hostname(S.HOSTNAME)           # mDNS name -> HOSTNAME.local
        except Exception as e:
            print('net: country/hostname', e)
        self.sta = network.WLAN(network.STA_IF)
        self.ap = network.WLAN(network.AP_IF)
        self.creds = None
        try:
            with open('wifi.json') as f:
                self.creds = json.load(f)
            if not self.creds.get('ssid'):
                self.creds = None
        except (OSError, ValueError):
            pass
        self.pending = None          # (ssid, password, save) while a connect attempt runs
        self.pend_at = 0
        self.msg = ''
        self.down_since = None
        self.retry_at = 0
        self.ap_off_at = None
        self.scan_cache = []
        self.scan_at = None
        self.ntp_at = None
        self.ntp_try = -10 ** 6
        self.ntp_err = ''

    # -- helpers
    def ip(self):
        """The station IP address, '' while not connected."""
        return self.sta.ifconfig()[0] if self.sta.isconnected() else ''

    def ap_clients(self):
        """Number of phones / laptops on the setup access point."""
        try:
            return len(self.ap.status('stations'))
        except Exception:
            return 0

    def _ap(self, on):
        """Switch the setup access point (and its captive DNS) on or off."""
        if on and not self.ap.active():
            self.ap.active(True)
            self.ap.config(essid=S.AP_SSID, password=S.AP_PASSWORD, authmode=3)
            print('net: setup AP', S.AP_SSID, 'at', self.ap.ifconfig()[0])
            _dns_start()
        elif not on and self.ap.active():
            self.ap.active(False)
            print('net: setup AP off')

    def connect(self, ssid, pw, save):
        """Start joining a network; _tick() reports the result (save = store it in wifi.json)."""
        self.sta.active(True)
        try:
            self.sta.disconnect()
        except Exception:
            pass
        self.sta.connect(ssid, pw)
        self.pending = (ssid, pw, save)
        self.pend_at = uptime()
        self.msg = ''
        print('net: connecting to', ssid)

    def forget(self):
        """Delete the saved network and open the setup access point."""
        try:
            os.remove('wifi.json')
        except OSError:
            pass
        self.creds = self.pending = None
        try:
            self.sta.disconnect()
        except Exception:
            pass
        self._ap(True)
        self.msg = 'Saved network forgotten.'

    def scan(self):
        """Blocking ~2-3 s. Keeps the strongest entry per SSID, max 20."""
        self.sta.active(True)
        best = {}
        for n in self.sta.scan():
            name = n[0].decode('utf-8', 'replace')
            if name and (name not in best or n[3] > best[name][0]):
                best[name] = (n[3], n[2], n[4])
        self.scan_cache = sorted(((k,) + v for k, v in best.items()), key=lambda x: -x[1])[:20]
        self.scan_at = uptime()
        gc.collect()

    def _ntp(self):
        """Set the clock by NTP (blocking up to ~2 s); remembers the result for the status line."""
        import ntptime
        self.ntp_try = uptime()
        try:
            ntptime.host = S.NTP_HOST
            ntptime.timeout = 2
            ntptime.settime()
            self.ntp_at, self.ntp_err = uptime(), ''
            util.clock_src = 'NTP'
            print('net: NTP ok', stamp())
            if app.pwr:
                app.pwr.mark_ntp()
        except Exception as e:
            self.ntp_err = str(e) or 'timeout'

    # -- state machine, 1 s tick
    def _tick(self):
        """Connection state machine, once a second: connect result, NTP, setup AP, retries."""
        now = uptime()
        up = self.sta.isconnected()
        if self.pending:
            st = self.sta.status()
            if up:
                ssid, pw, save = self.pending
                self.pending = None
                if save:
                    with open('wifi.json', 'w') as f:
                        json.dump({'ssid': ssid, 'password': pw}, f)
                    self.creds = {'ssid': ssid, 'password': pw}
                self.msg = 'Connected to %s.' % ssid
                # keep the setup AP a little so the phone can read the result, then drop it
                self.ap_off_at = now + 45 if self.ap.active() else None
                print('net: WiFi', ssid, self.ip(), '-> http://%s.local/' % S.HOSTNAME)
            elif now - self.pend_at > 20 or (now - self.pend_at > 4 and st in FAIL_REASON):
                ssid = self.pending[0]
                self.pending = None
                self.msg = 'Could not connect to %s: %s.' % (ssid, FAIL_REASON.get(st, 'timeout'))
                try:
                    self.sta.disconnect()
                except Exception:
                    pass
                self._ap(True)
                self.retry_at = now + 120
                print('net:', self.msg)
            return
        if up:
            self.down_since = None
            if self.ap.active() and self.ap_off_at is not None and now >= self.ap_off_at:
                self._ap(False)
                self.ap_off_at = None
            due = S.NTP_EVERY_S if self.ntp_at is not None else 60
            if now - self.ntp_try >= due:
                self._ntp()
        else:
            if self.down_since is None:
                self.down_since = now
            if not self.creds or now - self.down_since > 30:
                self._ap(True)
            # retry saved network in the background, but not while someone uses the setup AP
            if self.creds and now >= self.retry_at and not self.ap_clients():
                self.retry_at = now + 120
                self.connect(self.creds['ssid'], self.creds.get('password', ''), False)

    async def run(self):
        """Task: connect with the saved network (or open the setup AP), then tick forever."""
        if self.creds:
            self.connect(self.creds['ssid'], self.creds.get('password', ''), False)
        else:
            self._ap(True)
        while True:
            try:
                self._tick()
            except Exception as e:
                print('net error:', e)
            await asyncio.sleep(1)
