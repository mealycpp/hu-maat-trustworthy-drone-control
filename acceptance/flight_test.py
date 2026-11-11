#!/usr/bin/env python3
"""HU-MA'AT Phase 2 supervised single-aircraft live flight test.

Usage: python3 flight_test.py <Name> [results_dir]
Every step goes through the real HU bridge (port 8100) exactly as the
dashboard does; CONFIRM-band proposals are confirmed (operator-authorized run).
A step PASSES only when Gazebo telemetry shows the expected physical result,
and every other aircraft stayed put. Any failure triggers an immediate
'Name, land' attempt and aborts the sequence.
"""
import json, math, sys, time, urllib.request
from datetime import datetime, timezone

HU = 'http://127.0.0.1:8100'
GZ = 'http://127.0.0.1:8765'
NAMES = ('Horus', 'Ra', 'Thoth', 'Anubis', 'Osiris')


def call(url, data=None, timeout=25):
    req = urllib.request.Request(url, data=None if data is None else json.dumps(data).encode(),
                                 headers={'Content-Type': 'application/json'},
                                 method='POST' if data is not None else 'GET')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b'{}')


def state():
    return {d['name']: d for d in call(GZ + '/api/state')[1]['drones']}


def send(phrase, log):
    t0 = time.monotonic()
    _, r = call(HU + '/api/hu/command', {'utterance': phrase, 'source': 'typed'})
    entry = {'phrase': phrase, 'band': r.get('band'), 'action': r.get('action'),
             'targets': r.get('targets'), 'params': r.get('params'),
             'confidence': r.get('confidence'), 'interpreter': r.get('decision_source'),
             'cmd_id': r.get('cmd_id'), 'reason': r.get('reason'), 'dispatched': r.get('dispatched')}
    if r.get('band') == 'CONFIRM':
        status, c = call(HU + '/api/hu/confirm', {'cmd_id': r['cmd_id']})
        entry['confirmed'] = True
        entry['dispatched'] = status == 200 and c.get('ok') is True
        entry['dispatch_reply'] = c
    entry['decision_to_ack_ms'] = round((time.monotonic() - t0) * 1000, 1)
    log.append(entry)
    return entry


def wait(pred, timeout):
    end = time.monotonic() + timeout
    t0 = time.monotonic()
    while time.monotonic() < end:
        s = state()
        if pred(s):
            return s, round(time.monotonic() - t0, 2)
        time.sleep(0.15)
    return state(), None


def main():
    me = sys.argv[1].title()
    outdir = sys.argv[2] if len(sys.argv) > 2 else '.'
    others = [n for n in NAMES if n != me]
    s0 = state()
    if not all(d['telemetry'] for d in s0.values()):
        sys.exit('Not all aircraft have fresh telemetry; refusing to fly.')
    home = {n: (s0[n]['x'], s0[n]['y'], s0[n]['z']) for n in NAMES}
    steps, log = [], []

    def others_still(s):
        # Horizontal drift >5 cm, a climb above 0.30 m, or leaving the parked
        # state counts as movement (post-landing settling below 0.30 m does not).
        return [n for n in others if math.dist(home[n][:2], (s[n]['x'], s[n]['y'])) > 0.05
                or s[n]['z'] > 0.30 or s[n]['mode'] not in ('READY', 'GROUNDED')]

    def step(name, phrase, pred, timeout, measure):
        before = state()[me]
        e = send(phrase, log)
        if not e['dispatched']:
            steps.append({'step': name, 'result': 'FAIL', 'why': 'not dispatched: ' + str(e.get('reason') or e.get('dispatch_reply')), 'decision': e})
            return False
        s, secs = wait(pred, timeout)
        moved = others_still(s)
        ok = secs is not None and not moved
        d = s[me]
        steps.append({'step': name, 'result': 'PASS' if ok else 'FAIL', 'decision': e,
                      'settle_s': secs, 'before': {k: before[k] for k in ('x', 'y', 'z', 'yaw', 'mode')},
                      'after': {k: d[k] for k in ('x', 'y', 'z', 'yaw', 'mode')},
                      'measured': measure(before, d), 'other_aircraft_moved': moved})
        print(f"{steps[-1]['result']:4} {name:24} {e['interpreter']!s:30} band={e['band']} "
              f"settle={secs}s {steps[-1]['measured']} others_moved={moved}")
        return ok

    z0 = s0[me]['z']
    ok = step('takeoff', f'{me}, take off',
              lambda s: s[me]['mode'] == 'HOVER' and s[me]['z'] > 1.4, 25,
              lambda b, a: {'dz_m': round(a['z'] - b['z'], 3)})
    hz = state()[me]['z']
    ok = ok and step('move up 20 cm', f'{me}, move up 20 centimeters',
                     lambda s: abs(s[me]['z'] - (hz + 0.20)) < 0.05 and abs(s[me]['z'] - s[me]['target'][2]) < 0.05, 20,
                     lambda b, a: {'dz_m': round(a['z'] - b['z'], 3), 'requested_dz_m': 0.20})
    if ok:
        time.sleep(1.5)
    ok = ok and step('stop -> hover (not land)', f'{me}, stop',
                     lambda s: s[me]['mode'] == 'HOVER' and s[me]['z'] > 1.0, 6,
                     lambda b, a: {'z_m': a['z'], 'mode': a['mode']})
    if ok:
        time.sleep(2.0)  # let 'stop' hold settle before the lateral test
        # Hover must still hold after 2 s (STOP must not decay into LAND).
        sz = state()[me]
        if sz['mode'] != 'HOVER' or sz['z'] < 1.0:
            steps.append({'step': 'stop holds 2 s', 'result': 'FAIL', 'after': sz}); ok = False
    if ok:
        b = state()[me]
        yaw = math.radians(b['yaw'])
        # Drone-body right in Gazebo ENU world frame (x fwd, y LEFT) is (sin yaw, -cos yaw).
        rx, ry = math.sin(yaw), -math.cos(yaw)
        # Only the true body-right destination satisfies this step.
        ok = step('move right 50 cm', f'{me}, move right 50 centimeters',
                  lambda s: math.dist((s[me]['x'], s[me]['y']), (b['x'] + .5 * rx, b['y'] + .5 * ry)) < .08, 20,
                  lambda bb, a: {'dx_m': round(a['x'] - bb['x'], 3), 'dy_m': round(a['y'] - bb['y'], 3),
                                 'went': 'BODY RIGHT' if ((a['x'] - bb['x']) * rx + (a['y'] - bb['y']) * ry) > 0 else 'BODY LEFT (MIRRORED)'})
    land = step('land', f'{me}, land',
                lambda s: s[me]['mode'] == 'GROUNDED' and s[me]['z'] < .3, 40,
                lambda b, a: {'z_m': a['z'], 'mode': a['mode']})
    if not land:
        call(GZ + '/api/command', {'action': 'land', 'targets': [me]})  # dashboard-authority safety landing
        wait(lambda s: s[me]['mode'] == 'GROUNDED', 40)
    out = {'aircraft': me, 'generated_utc': datetime.now(timezone.utc).isoformat(),
           'steps': steps, 'decisions': log,
           'verdict': 'PASS' if all(x['result'] == 'PASS' for x in steps) and len(steps) >= 5 else 'FAIL'}
    path = f"{outdir}/flight_{me}_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    json.dump(out, open(path, 'w'), indent=2)
    print(f"{me}: {out['verdict']} -> {path}")


if __name__ == '__main__':
    main()
