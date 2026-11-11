#!/usr/bin/env python3
"""Live check of every single-aircraft motion primitive, judged from Gazebo telemetry.

Usage: python3 motion_test.py <Name> [results_dir]
take off -> forward/back/left/right 50 cm -> up/down 30 cm -> turn left/right 90 -> spin -> land
"""
import json, math, sys, time, urllib.request
from datetime import datetime, timezone

HU, GZ = 'http://127.0.0.1:8100', 'http://127.0.0.1:8765'


def call(url, data=None):
    req = urllib.request.Request(url, data=None if data is None else json.dumps(data).encode(),
                                 headers={'Content-Type': 'application/json'}, method='POST' if data is not None else 'GET')
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b'{}')


def me_state(n):
    return next(d for d in call(GZ + '/api/state')[1]['drones'] if d['name'] == n)


def send(phrase):
    _, r = call(HU + '/api/hu/command', {'utterance': phrase, 'source': 'typed'})
    if r.get('band') == 'CONFIRM':
        st, c = call(HU + '/api/hu/confirm', {'cmd_id': r['cmd_id']})
        r['dispatched'] = st == 200 and c.get('ok') is True
        r['why'] = c.get('error')
    return r


def angdiff(a, b):
    return math.degrees(math.atan2(math.sin(math.radians(a - b)), math.cos(math.radians(a - b))))


def main():
    n = sys.argv[1].title(); outdir = sys.argv[2] if len(sys.argv) > 2 else '.'
    rows = []

    def run(label, phrase, expect, timeout=20):
        b = me_state(n); r = send(phrase); t0 = time.monotonic(); a = b; ok = False
        while r.get('dispatched') and time.monotonic() - t0 < timeout:
            a = me_state(n)
            if expect(b, a): ok = True; break
            time.sleep(0.15)
        secs = round(time.monotonic() - t0, 2) if ok else None
        yaw = math.radians(b['yaw'])
        dx, dy = a['x'] - b['x'], a['y'] - b['y']
        meas = {'body_fwd_m': round(dx * math.cos(yaw) + dy * math.sin(yaw), 3),
                'body_right_m': round(dx * math.sin(yaw) - dy * math.cos(yaw), 3),
                'dz_m': round(a['z'] - b['z'], 3), 'dyaw_deg': round(angdiff(a['yaw'], b['yaw']), 1)}
        rows.append({'motion': label, 'phrase': phrase, 'interpreter': r.get('decision_source'), 'band': r.get('band'),
                     'result': 'PASS' if ok else 'FAIL', 'settle_s': secs, 'measured': meas,
                     'why': None if ok else (r.get('why') or r.get('reason'))})
        print(f"{rows[-1]['result']:4} {label:16} settle={secs}s {meas} {'' if ok else rows[-1]['why']}")
        time.sleep(1.0)

    def lateral(fwd, right, tol=.08):
        def f(b, a):
            y = math.radians(b['yaw'])
            gx = b['x'] + fwd * math.cos(y) + right * math.sin(y)
            gy = b['y'] + fwd * math.sin(y) - right * math.cos(y)
            return math.dist((a['x'], a['y']), (gx, gy)) < tol and a['mode'] == 'MOVE'
        return f

    run('take off', f'{n}, take off', lambda b, a: a['mode'] == 'HOVER' and a['z'] > 1.4, 25)
    run('forward 50 cm', f'{n}, move forward 50 centimeters', lateral(.5, 0))
    run('back 50 cm', f'{n}, move back 50 centimeters', lateral(-.5, 0))
    run('left 50 cm', f'{n}, move left 50 centimeters', lateral(0, -.5))
    run('right 50 cm', f'{n}, move right 50 centimeters', lateral(0, .5))
    run('up 30 cm', f'{n}, move up 30 centimeters', lambda b, a: abs(a['z'] - b['z'] - .30) < .05)
    run('down 30 cm', f'{n}, move down 30 centimeters', lambda b, a: abs(a['z'] - b['z'] + .30) < .05)
    run('turn left 90', f'{n}, turn left 90 degrees', lambda b, a: abs(angdiff(a['yaw'], b['yaw']) - 90) < 5)
    run('turn right 90', f'{n}, turn right 90 degrees', lambda b, a: abs(angdiff(a['yaw'], b['yaw']) + 90) < 5)
    run('spin 360', f'{n}, spin', lambda b, a: a['mode'] == 'HOVER' and abs(angdiff(a['yaw'], b['yaw'])) < 8, 40)
    run('land', f'{n}, land', lambda b, a: a['mode'] == 'GROUNDED', 40)
    if me_state(n)['mode'] != 'GROUNDED':
        call(GZ + '/api/command', {'action': 'land', 'targets': [n]})
    out = {'aircraft': n, 'generated_utc': datetime.now(timezone.utc).isoformat(), 'motions': rows,
           'verdict': 'PASS' if all(r['result'] == 'PASS' for r in rows) else 'FAIL'}
    p = f"{outdir}/motion_{n}_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    json.dump(out, open(p, 'w'), indent=2); print(f"{n} MOTIONS: {out['verdict']} -> {p}")


if __name__ == '__main__':
    main()
