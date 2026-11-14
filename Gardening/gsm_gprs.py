# GPRS TCP link of the SIM800L for MQTT (gsm.Gsm inherits Gprs): one connection, data read in manual
# mode (AT+CIPRXGET=2) so binary MQTT bytes never mix with AT text. Tcp is the stream mqtt.py writes to.
# Spec: docs/SPEC.md#gsm
try:
    import asyncio
except ImportError:
    import uasyncio as asyncio
import time


class Tcp:
    """Stream-like TCP connection over GPRS for mqtt.py (write / drain / readexactly / close)."""

    def __init__(self, g):
        """Stream over the GPRS link of modem g."""
        self.g, self.wb, self.rb = g, [], b''

    def write(self, b):
        """Buffer bytes (sent by drain() as one TCP send)."""
        self.wb.append(b)

    async def drain(self):
        """Send the buffered parts (one join: AT+CIPSEND needs the total length)."""
        if self.wb:
            b, self.wb = b''.join(self.wb), []
            await self.g.send(b)

    async def readexactly(self, n):
        """Exactly n bytes from the link (waits)."""
        while len(self.rb) < n:
            d = await self.g.recv(n - len(self.rb))
            if d:
                self.rb += d
            else:
                await asyncio.sleep_ms(200)
        b, self.rb = self.rb[:n], self.rb[n:]
        return b

    def close(self):
        """Nothing to do (wait_closed() closes)."""
        pass

    async def wait_closed(self):
        """Close the TCP connection."""
        await self.g.tcp_close()



class Gprs:
    """GPRS / TCP methods of the modem (mixed into gsm.Gsm; uses its lock, _cmd and parsed data)."""

    def use_gprs(self, wifi_up):
        """True if MQTT should go over GPRS now ('always', or 'fallback' while WiFi is down)."""
        m = self.c['gprs']
        return self.net and (m == 'always' or (m == 'fallback' and not wifi_up))

    async def _gprs_up(self):
        """Attach GPRS with the APN and get an IP (once)."""
        if self.ip:
            return
        c = self.c
        if not c['apn']:
            raise OSError('no APN set in the GSM panel')
        for a in ('AT+CIPSHUT', 'AT+CIPMUX=0', 'AT+CIPRXGET=1'):
            await self._cmd(a, ('OK', 'SHUT OK'), 10000)
        await self._cmd('AT+CSTT="%s","%s","%s"' % (c['apn'], c['apn_user'], c['apn_pw']), ('OK',), 10000)
        await self._cmd('AT+CIICR', ('OK',), 85000)
        self.lines = []
        self._write('AT+CIFSR\r')                                 # answers with the IP only
        t = time.ticks_ms()
        while not self.lines:
            if time.ticks_diff(time.ticks_ms(), t) > 10000:
                raise OSError('no GPRS address')
            await asyncio.sleep_ms(50)
        ip = self.lines.pop(0)
        if 'ERROR' in ip or '.' not in ip:
            raise OSError('GPRS: %s' % ip)
        self.ip = ip

    async def tcp(self, host, port):
        """Open a TCP connection over GPRS; returns (reader, writer) like asyncio.open_connection."""
        async with self.lock:
            await self._gprs_up()
            self.data = []
            await self._cmd('AT+CIPSTART="TCP","%s",%d' % (host, port), ('CONNECT OK', 'ALREADY CONNECT'), 60000)
            self.tcp_on = True
        t = Tcp(self)
        return t, t

    async def send(self, b):
        """Send bytes on the open TCP link."""
        if not self.tcp_on:
            raise OSError('GPRS link closed')
        async with self.lock:
            await self._cmd('AT+CIPSEND=%d' % len(b), ('SEND OK',), 20000, raw=b)

    async def recv(self, n):
        """Up to n received bytes (b'' if none yet)."""
        if self.data:
            d = self.data.pop(0)
            if len(d) > n:
                self.data.insert(0, d[n:])
                d = d[:n]
            return d
        if not self.tcp_on:
            raise OSError('GPRS link closed')
        async with self.lock:
            await self._cmd('AT+CIPRXGET=2,%d' % min(n, 512), ('OK',), 5000)
        return self.data.pop(0) if self.data else b''

    async def tcp_close(self):
        """Close the TCP link (ignore errors)."""
        if self.tcp_on:
            self.tcp_on = False
            try:
                await self.cmd('AT+CIPCLOSE=1', ('CLOSE OK', 'OK'), 5000)
            except OSError:
                pass

