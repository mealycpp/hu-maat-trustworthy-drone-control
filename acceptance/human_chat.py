#!/usr/bin/env python3
"""Unscripted, human-style conversation with live SwarmChat (SpaceOps :8766). Read-only."""
import json, urllib.request

SO = 'http://127.0.0.1:8766'
tok = json.load(urllib.request.urlopen(SO + '/api/ops/session'))['token']
CONVO = [
    ('FLEET', 'hey guys!! whats up'),
    ('Horus', 'yo horus u good?'),
    ('Horus', 'nice. you looked a bit wobbly earlier tho'),
    ('Thoth', 'thoth whats ur deal today, you seem quiet'),
    ('Anubis', 'anubis you always hang around horus, you two besties or what'),
    ('FLEET', 'great job on those formations everyone, honestly impressive'),
    ('Ra', 'ra whats your favorite formation'),
    ('Osiris', 'osiris are you tired? you have been flying all day'),
    ('FLEET', 'who is the best drone here, be honest'),
    ('FLEET', "i'm kinda nervous about presenting you all at the conference tomorrow"),
    ('Thoth', 'thoth give me one number that proves we are safe right now'),
    ('Anubis', 'if something went wrong would you tell me?'),
    ('FLEET', 'can you guys do a backflip for the audience lol'),
    ('Ra', 'tell me another joke'),
    ('Horus', 'do you actually understand me or are you just pattern matching'),
    ('FLEET', 'ok thanks team, good night'),
]
for who, text in CONVO:
    req = urllib.request.Request(SO + '/api/ops/chat', data=json.dumps({'text': text, 'recipient': who}).encode(),
                                 headers={'Content-Type': 'application/json', 'X-RSCL-CSRF': tok}, method='POST')
    try:
        r = json.load(urllib.request.urlopen(req))
    except urllib.error.HTTPError as e:
        r = {'messages': [{'sender': 'HTTP', 'text': f'{e.code} {e.read().decode()[:150]}'}]}
    print(f'\nME -> {who}: {text}')
    for m in r.get('messages', []):
        print(f"   {m.get('sender', '?')}{(' -> ' + m['to']) if m.get('to') not in (None, '', 'OPERATOR', 'FLEET') else ''}: {m.get('text', '')}")
