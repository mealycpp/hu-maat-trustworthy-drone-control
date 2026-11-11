#!/usr/bin/env python3
"""Send phrases to the live HU bridge and show route/band (EXECUTE / CONFIRM / REFUSE / EMERGENCY).
--confirm: confirm CONFIRM-band proposals (actuates!). Without it, CONFIRM proposals are cancelled."""
import json, sys, time, urllib.request

HU = 'http://127.0.0.1:8100'


def post(path, obj):
    req = urllib.request.Request(HU + path, data=json.dumps(obj).encode(), headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return json.loads(e.read() or b'{}')


confirm = '--confirm' in sys.argv
for phrase in [a for a in sys.argv[1:] if a != '--confirm']:
    r = post('/api/hu/command', {'utterance': phrase, 'source': 'typed'})
    tail = ''
    if r.get('band') == 'CONFIRM':
        if confirm:
            c = post('/api/hu/confirm', {'cmd_id': r['cmd_id']}); tail = ' -> confirmed ' + ('OK' if c.get('ok') else str(c.get('error')))
        else:
            post('/api/hu/cancel', {'cmd_id': r['cmd_id']}); tail = ' -> cancelled'
    print(f"{r.get('band','?'):9} {str(r.get('action')):9} {r.get('targets')} conf={r.get('confidence')} "
          f"[{r.get('decision_source')}] dispatched={r.get('dispatched')}{tail}  <- {phrase!r}"
          + (f"\n          reason: {r.get('reason')}" if r.get('band') == 'REFUSE' else ''))
    time.sleep(0.5)
