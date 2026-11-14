# Basic WiFi radio test - run over serial (Thonny or mpremote run), not on boot.
# Starts the 'garden' setup hotspot, scans, tries the saved network, and prints everything,
# with ESP-IDF WiFi driver logs enabled on the serial console.
import network, time, json

try:
    import esp
    esp.osdebug(0, getattr(esp, 'LOG_INFO', 3))   # IDF logs to UART0
except Exception as e:
    print('osdebug n/a:', e)

AUTH = {0: 'open', 1: 'WEP', 2: 'WPA', 3: 'WPA2', 4: 'WPA/WPA2', 5: 'WPA2-ENT', 6: 'WPA3', 7: 'WPA2/WPA3'}
STAT = {getattr(network, k): k for k in dir(network) if k.startswith('STAT_')}

#network.country('AE')
#network.hostname('garden')
sta = network.WLAN(network.STA_IF)
ap = network.WLAN(network.AP_IF)
sta.active(False)
ap.active(False)
time.sleep(0.5)

print('\n=== 1. hotspot "garden" (open, channel 6)')
ap.active(True)
ap.config(essid='garden', authmode=0, channel=6)
time.sleep(1)
print('AP active', ap.active(), 'essid', ap.config('essid'), 'ch', ap.config('channel'),
      'ip', ap.ifconfig()[0], 'txpower', ap.config('txpower'), 'mac', ap.config('mac').hex())

print('\n=== 2. scan (3x)')
sta.active(True)
for i in range(3):
    r = sta.scan()
    print('scan %d: %d networks' % (i + 1, len(r)))
    for x in sorted(r, key=lambda x: -x[3])[:8]:
        print('   %-24s ch%-2d %4d dBm %s' % (x[0].decode('utf-8', 'replace'), x[2], x[3], AUTH.get(x[4], x[4])))
    time.sleep(1)

print('\n=== 3. connect to saved network')
try:
    c = json.load(open('wifi.json'))
    sta.connect(c['ssid'], c.get('password', ''))
    last = None
    for i in range(25):
        st = sta.status()
        if st != last:
            print('  t=%2ds status %s %s' % (i, st, STAT.get(st, '?')))
            last = st
        if sta.isconnected():
            print('  CONNECTED', sta.ifconfig(), 'rssi', sta.status('rssi'))
            break
        time.sleep(1)
except OSError:
    print('  no wifi.json')

print('\n=== 4. hotspot clients for 45 s (join "garden" from your phone now)')
for i in range(15):
    print('  t=%2ds clients %d' % (i * 3, len(ap.status('stations'))))
    time.sleep(3)
print('done')
