#!/usr/bin/env python3
"""Smoke client for chat_llm_service.py. Talks ONLY to the port given (default 8797)."""
import json
import sys
import time
import urllib.error
import urllib.request

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8797
assert PORT not in (8100, 8765, 8766, 8767), 'smoke test must not use a production port'
BASE = f'http://127.0.0.1:{PORT}'


def call(path, body=None, timeout=60):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, headers={'Content-Type': 'application/json'})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            code, out = r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        code, out = e.code, json.loads(e.read() or b'{}')
    return code, out, round((time.monotonic() - t0) * 1000, 1)


def wait_loaded(limit_s=180):
    t0 = time.monotonic()
    while time.monotonic() - t0 < limit_s:
        try:
            code, h, _ = call('/health', timeout=3)
            if h.get('loaded') or (not h.get('loading') and not h.get('loaded')):
                return h
        except Exception:
            pass
        time.sleep(1)
    return None


RA_EV = {'vehicle': 'Ra', 'mode': 'FORMATION', 'formation_shape': 'v', 'fault': False, 'telemetry': True,
         'airborne': True, 'alt_m': 2.1, 'xy_m': [0.0, -1.5], 'battery_sim_pct': 87.3,
         'nearest': {'name': 'Horus', 'dist_m': 2.2}, 'snapshot_age_s': 0.4}
THOTH_EV = {'vehicle': 'Thoth', 'mode': 'FORMATION', 'formation_shape': 'v', 'fault': False, 'telemetry': True,
            'airborne': True, 'alt_m': 1.5, 'xy_m': [2.2, -1.5], 'battery_sim_pct': 91.0,
            'nearest': {'name': 'Osiris', 'dist_m': 1.0}, 'snapshot_age_s': 0.3}
ANUBIS_EV = {'vehicle': 'Anubis', 'mode': 'FORMATION', 'fault': False, 'telemetry': True, 'airborne': True,
             'alt_m': 1.4, 'nearest': {'name': 'Horus', 'dist_m': 2.86}, 'snapshot_age_s': 0.5}

CASES = [
    ('social', {'speaker': 'Ra', 'evidence': RA_EV, 'allowed_facts': ['2.10 m', 'Horus', '87.3%', 'FORMATION', 'v'],
                'draft': None, 'history': [], 'user_text': "hey Ra, how's it going?", 'style': 'social',
                'max_new_tokens': 50}),
    ('factual+draft', {'speaker': 'Thoth', 'evidence': THOTH_EV,
                       'allowed_facts': ['1.50 m', 'Osiris', '1.00 m', '91.0%'],
                       'draft': 'My altitude is 1.50 m (airborne). My nearest aircraft is Osiris at 1.00 m (3D).',
                       'history': [{'role': 'user', 'speaker': 'OPERATOR', 'text': 'Thoth, status?'},
                                   {'role': 'assistant', 'speaker': 'Thoth', 'text': 'By the numbers: FORMATION at 1.50 m.'}],
                       'user_text': 'Thoth, what is your altitude and who is closest to you?', 'style': 'factual',
                       'max_new_tokens': 60}),
    ('unverifiable', {'speaker': 'Anubis', 'evidence': ANUBIS_EV, 'allowed_facts': ['1.40 m', 'Horus', '2.86 m'],
                      'draft': None, 'history': [],
                      'user_text': 'Anubis, did Osiris just lose a propeller and how windy is it out there?',
                      'style': 'explain', 'max_new_tokens': 60}),
]


def main():
    h = wait_loaded()
    print('== /health after load ==')
    print(json.dumps(h, indent=1))
    if not h:
        print('service did not come up'); return 1
    for name, body in CASES:
        code, out, wall = call('/generate', body)
        print(f'\n== {name}  speaker={body["speaker"]}  HTTP {code}  wall={wall} ms ==')
        print('user_text :', body['user_text'])
        if body.get('draft'):
            print('draft     :', body['draft'])
        print('raw       :', repr(out.get('raw')))
        print('text      :', out.get('text'))
        print('accepted  :', out.get('accepted'), ' fallback:', out.get('fallback'), out.get('reason') or '')
        print('violations:', out.get('violations'))
        print('latency_ms:', out.get('latency_ms'), ' gen_ms:', out.get('gen_ms'), ' new_tokens:',
              out.get('new_tokens'), ' model:', out.get('model'))
    # timeout path: a 0.3 s budget cannot fit a generation
    body = dict(CASES[0][1], timeout_s=0.3)
    code, out, wall = call('/generate', body)
    print(f'\n== timeout_s=0.3 -> HTTP {code} fallback={out.get("fallback")} reason={out.get("reason")} '
          f'wall={wall} ms')
    # busy path: 8 concurrent requests against queue_max=4 and a single worker
    import threading
    res = []

    def burst(i):
        b = dict(CASES[1][1], user_text=f'Thoth, altitude check {i}?', timeout_s=30)
        res.append(call('/generate', b, timeout=90)[:2])
    ts = [threading.Thread(target=burst, args=(i,)) for i in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    busy = sum(1 for c, o in res if c == 503 and o.get('reason') == 'busy')
    done = sum(1 for c, o in res if c == 200 and not o.get('fallback'))
    print(f'== burst of 8 -> {done} generated, {busy} immediate 503 busy, '
          f'accepted={sum(1 for c, o in res if o.get("accepted"))}')
    # bad request and health counters
    code, out, _ = call('/generate', {'speaker': 'Zeus'})
    print(f'\n== bad speaker -> HTTP {code}: {out}')
    code, h, _ = call('/health')
    print('\n== /health final ==')
    print(json.dumps(h, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
