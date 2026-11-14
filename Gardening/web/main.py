# Garden logger entry point.
#   - Hold the BOOT button (GPIO0) during an EN reset / power-on to skip the app (REPL only).
#   - A wake from deep sleep (power.py sleep cycle) never skips: BOOT is also the wake button.
#     Timer wakes run the quick measure-log-sleep path; anything else starts the full web app.
from machine import Pin, reset_cause, DEEPSLEEP_RESET
import settings as S

deep = reset_cause() == DEEPSLEEP_RESET
if deep or Pin(0, Pin.IN, Pin.PULL_UP).value():
    import app
    if deep and S.POWER_ENABLED:
        import sys
        import cycle
        cycle.quick()        # returns only if the full app should run (button wake, window, error)
        del sys.modules['cycle']
        cycle = None
    app.main()
else:
    print('BOOT held: app skipped, REPL only')
