# Captive-portal DNS for the setup access point - loaded only while the AP is up (app.py), then released.
try:
    import asyncio
except ImportError:
    import uasyncio as asyncio


def dns_reply(q, ip):
    """Captive-portal DNS: answer every A query with our AP address."""
    if len(q) < 17 or q[2] & 0x80:
        return None
    i = 12
    while i < len(q) and q[i]:
        i += q[i] + 1
    if i + 5 > len(q):
        return None
    qtype = (q[i + 1] << 8) | q[i + 2]
    end = i + 5
    a = qtype == 1
    r = q[:2] + b'\x81\x80\x00\x01' + (b'\x00\x01' if a else b'\x00\x00') + b'\x00\x00\x00\x00' + q[12:end]
    if a:
        r += b'\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x3c\x00\x04' + ip
    return r


async def run(net):
    """Answers DNS while the setup AP is up; returns (and is unloaded by app.py) when it goes down."""
    import socket
    sock = None
    while True:
        if not net.ap.active():
            if sock:
                sock.close()
            print('dns: captive DNS off')
            return
        if sock is None:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(('0.0.0.0', 53))
                sock.setblocking(False)
                ip = bytes(int(x) for x in net.ap.ifconfig()[0].split('.'))
                print('dns: captive DNS on', net.ap.ifconfig()[0])
            except OSError as e:
                print('dns error:', e)
                sock = None
                await asyncio.sleep(5)
                continue
        try:
            data, addr = sock.recvfrom(512)
        except OSError:
            await asyncio.sleep_ms(50)
            continue
        try:
            r = dns_reply(data, ip)
            if r:
                sock.sendto(r, addr)
        except Exception:
            pass
