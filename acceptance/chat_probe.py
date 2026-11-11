#!/usr/bin/env python3
"""Ask SwarmChat (SpaceOps, port 8766) status and casual questions; print replies verbatim."""
import json, urllib.request

SO = 'http://127.0.0.1:8766'
FLEET = 'FLEET'
tok = json.load(urllib.request.urlopen(SO + '/api/ops/session'))['token']
QUESTIONS = [(FLEET, 'status report everyone'), ('Horus', 'Horus, what is your altitude and battery?'),
             ('Anubis', 'Anubis, are you healthy?'), (FLEET, 'hello team, how is everyone feeling today?'),
             ('Ra', 'Ra, tell me a joke'), (FLEET, 'who is closest to Thoth?'),
             (FLEET, 'can you all take off and form a V?')]
for who, text in QUESTIONS:
    req = urllib.request.Request(SO + '/api/ops/chat', data=json.dumps({'text': text, 'recipient': who}).encode(),
                                 headers={'Content-Type': 'application/json', 'X-RSCL-CSRF': tok}, method='POST')
    try:
        r = json.load(urllib.request.urlopen(req))
    except urllib.error.HTTPError as e:
        r = {'error': e.code, 'body': e.read().decode()[:200]}
    print(f'\nOPERATOR -> {who or "ALL"}: {text}')
    for m in r.get('messages', []) or [r]:
        print(f"   {m.get('sender', m.get('from', '?'))}: {str(m.get('text', m.get('message', m)))[:260]}")
