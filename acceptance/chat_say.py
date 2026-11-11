#!/usr/bin/env python3
"""Send chat messages to live SwarmChat and print replies with provenance. Usage: chat_say.py [RECIPIENT::]text ..."""
import json, sys, time, urllib.request

SO = 'http://127.0.0.1:8766'
tok = json.load(urllib.request.urlopen(SO + '/api/ops/session'))['token']
for arg in sys.argv[1:]:
    who, text = arg.split('::', 1) if '::' in arg else ('FLEET', arg)
    t0 = time.monotonic()
    req = urllib.request.Request(SO + '/api/ops/chat', data=json.dumps({'text': text, 'recipient': who}).encode(),
                                 headers={'Content-Type': 'application/json', 'X-RSCL-CSRF': tok}, method='POST')
    try:
        r = json.load(urllib.request.urlopen(req, timeout=60))
    except urllib.error.HTTPError as e:
        r = {'messages': [{'sender': 'HTTP', 'text': f'{e.code} {e.read().decode()[:200]}'}]}
    dt = time.monotonic() - t0
    route = next((m.get('evidence', {}).get('route') for m in r.get('messages', []) if m.get('role') == 'HUMAN'), None)
    print(f'\nME -> {who}: {text}   [{dt:.1f}s; understood as {route.get("intent") if route else "?"}'
          + (f' {route.get("confidence")}' if route and route.get('confidence') else '') + ']')
    for m in r.get('messages', []):
        if m.get('role') == 'HUMAN':
            continue
        print(f"   {m.get('sender')} <{m.get('source')}>: {m.get('text')}")
