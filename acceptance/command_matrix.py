#!/usr/bin/env python3
"""HU-MA'AT Phase 1 command-coverage matrix (live, NON-ACTUATING by design).

Sends each phrase to the real HU bridge (port 8100), records how it was
interpreted, then CANCELS any pending confirmation. Every case is expected to
end without motion; Gazebo telemetry before/after is compared to prove it.
Cases whose correct result is EXECUTE are *not* in this file - they are flown
under supervision by flight_test.py.
"""
import json, math, sys, time, urllib.request
from datetime import datetime, timezone

HU = 'http://127.0.0.1:8100'
GZ = 'http://127.0.0.1:8765'
NAMES = ('Horus', 'Ra', 'Thoth', 'Anubis', 'Osiris')

# phrase, route ('typed' | 'voice'), required band(s), required targets or None, note
CASES = [
    ('Land', 'typed', {'REFUSE'}, [], 'bare LAND must ask which aircraft'),
    ('land', 'voice', {'REFUSE'}, [], 'bare LAND by voice'),
    ('Old take off', 'typed', {'REFUSE'}, [], 'typed "old" is not "all"'),
    ('old take off', 'voice', {'VOICE_CLARIFY'}, None, '"old"->"all" needs spoken yes'),
    ('horror take off', 'voice', {'VOICE_CLARIFY'}, None, 'alias needs spoken yes'),
    ('raw take off', 'voice', {'VOICE_CLARIFY'}, None, 'alias needs spoken yes'),
    ('The weather is lovely today', 'typed', {'REFUSE'}, [], 'non-command'),
    ('Horus, fly to the moon', 'typed', {'REFUSE'}, [], 'unsupported destination'),
    ('Horus, do a backflip', 'typed', {'REFUSE'}, [], 'unsupported mission'),
    ('Horus, move forward', 'typed', {'REFUSE'}, [], 'missing distance'),
    ('Horus, move forward 5 meters', 'typed', {'REFUSE'}, [], 'over 2 m limit'),
    ('Horus, move forward 2 centimeters', 'typed', {'REFUSE'}, [], 'under 10 cm limit'),
    ('Horus, move left and right 50 centimeters', 'typed', {'REFUSE'}, [], 'conflicting directions'),
    ('Horus or Ra, take off', 'typed', {'REFUSE'}, [], 'ambiguous target'),
    ('Horus, take off then land', 'typed', {'REFUSE'}, [], 'compound command'),
    ('All except, take off', 'typed', {'REFUSE'}, [], 'broken exclusion'),
    ('Horus, rotate 90 degrees', 'typed', {'REFUSE'}, [], 'rotation without direction'),
    ('Horus, rotate 400 degrees left', 'typed', {'REFUSE'}, [], 'angle out of range'),
    ('All move forward 50 centimeters', 'typed', {'CONFIRM'}, list(NAMES), 'swarm travel: confirm, path-checked'),
    ('All move forward', 'typed', {'REFUSE'}, [], 'swarm move without distance'),
    ('All move forward 3 meters', 'typed', {'REFUSE'}, [], 'swarm move over 2 m limit'),
    ('Horus, move up 20 centimeters', 'typed', {'CONFIRM'}, ['Horus'], 'grounded: confirm only (flown later)'),
    ('Anubis, ascend ten centimeters', 'typed', {'CONFIRM'}, ['Anubis'], 'word number +0.10 m'),
    ('Thoth, turn left 90 degrees', 'typed', {'CONFIRM'}, ['Thoth'], 'yaw +90 (CCW)'),
    ('All form a V shape', 'typed', {'CONFIRM'}, list(NAMES), 'formation, never STOP/TAKEOFF'),
    ('All, land', 'typed', {'CONFIRM'}, list(NAMES), 'fleet land, confirmed'),
    ('All, stop', 'typed', {'CONFIRM', 'EMERGENCY'}, list(NAMES), 'fleet hold'),
    ('Everyone except Ra, take off', 'typed', {'CONFIRM'}, ['Horus', 'Thoth', 'Anubis', 'Osiris'], 'exclusion'),
]


def call(path, data=None):
    req = urllib.request.Request(HU + path if path.startswith('/api/hu') else GZ + path,
                                 data=None if data is None else json.dumps(data).encode(),
                                 headers={'Content-Type': 'application/json'},
                                 method='POST' if data is not None else 'GET')
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b'{}')


def poses():
    _, s = call('/api/state')
    return {d['name']: (d['x'], d['y'], d['z'], d['mode']) for d in s['drones']}


def main():
    results = []
    for phrase, route, bands, targets, note in CASES:
        before = poses()
        t0 = time.monotonic()
        if route == 'voice':
            status, r = call('/api/hu/voice-auto', {'utterance': phrase})
        else:
            status, r = call('/api/hu/command', {'utterance': phrase, 'source': 'typed'})
        ms = round((time.monotonic() - t0) * 1000, 1)
        cid = r.get('cmd_id')
        if cid and r.get('band') in ('CONFIRM', 'VOICE_CLARIFY'):
            call('/api/hu/cancel', {'cmd_id': cid})
        time.sleep(1.2)
        after = poses()
        moved = [n for n in NAMES if math.dist(before[n][:3], after[n][:3]) > 0.05
                 or before[n][3] != after[n][3]]
        band = r.get('band', f'HTTP{status}')
        ok = band in bands and not r.get('dispatched') and not moved
        if ok and targets is not None and band != 'REFUSE':
            ok = sorted(r.get('targets') or []) == sorted(targets)
        results.append({'phrase': phrase, 'route': route, 'band': band,
                        'action': r.get('action'), 'targets': r.get('targets'),
                        'params': r.get('params'), 'confidence': r.get('confidence'),
                        'interpreter': r.get('decision_source') or ('SPEECH NORMALIZER' if band == 'VOICE_CLARIFY' else None),
                        'reason': r.get('reason'), 'dispatched': r.get('dispatched'),
                        'aircraft_moved': moved, 'roundtrip_ms': ms,
                        'expected': sorted(bands), 'note': note,
                        'result': 'PASS' if ok else 'FAIL'})
        print(f"{results[-1]['result']:4} {band:13} {str(r.get('action')):18} {r.get('targets')} <- {phrase!r} [{route}]")
    out = {'generated_utc': datetime.now(timezone.utc).isoformat(), 'cases': results,
           'passed': sum(r['result'] == 'PASS' for r in results), 'total': len(results)}
    path = f"{sys.argv[1] if len(sys.argv) > 1 else '.'}/command_matrix_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    with open(path, 'w') as f:
        json.dump(out, f, indent=2)
    print(f"\n{out['passed']}/{out['total']} PASS  -> {path}")
    sys.exit(0 if out['passed'] == out['total'] else 1)


if __name__ == '__main__':
    main()
