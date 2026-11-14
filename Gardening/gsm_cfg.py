# GSM panel settings (DATA_DIR/gsm.json): kept apart from gsm.py so the panel can read and save them
# without loading the modem code (~15 KB) while the modem is off.
import json
import os
import settings as S

CFG = S.DATA_DIR + '/gsm.json'
DEFAULTS = {'on': False, 'phones': '', 'pin': '', 'max_day': 10, 'a_pump': True, 'a_soil': True, 'a_batt': True,
            'apn': '', 'apn_user': '', 'apn_pw': '', 'gprs': 'off',      # gprs: 'off' | 'fallback' | 'always'
            'code': '',                # SMS must start with this word ('' = not needed)
            'call': 'status',          # call from an allowed number: off | status | pump | toggle
            'call_sms': True,          # confirm call actions by SMS
            'alias': ''}               # own keywords: "WORDS=COMMAND; WORDS=COMMAND"
CALL_ACTIONS = (('off', 'nothing (just hang up)'), ('status', 'reply with the status SMS'),
                ('pump', 'run the pump (manual run time)'), ('toggle', 'pump on / off'))


def load_cfg():
    c = dict(DEFAULTS)
    try:
        with open(CFG) as f:
            d = json.load(f)
        for k in c:
            if k in d:
                c[k] = d[k]
    except (OSError, ValueError):
        pass
    return c


def save_cfg(c):
    tmp = CFG + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(c, f)
    os.rename(tmp, CFG)
