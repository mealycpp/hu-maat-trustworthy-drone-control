#!/usr/bin/env python3
"""Live swarm-travel test: the whole V formation travels as a rigid group.

Usage: python3 swarm_test.py [results_dir]
Each leg passes only if, from Gazebo telemetry: every member moved by the
commanded arena-frame vector (+/-8 cm), the formation shape was kept (every
pairwise distance changed by <6 cm), every member settled back into HOVER, and
non-members did not move. Minimum separation is sampled throughout.
"""
import json, math, sys, threading, time, urllib.request
from datetime import datetime, timezone
from itertools import combinations

HU, GZ = 'http://127.0.0.1:8100', 'http://127.0.0.1:8765'
NAMES = ('Horus', 'Ra', 'Thoth', 'Anubis', 'Osiris')


def call(url, data=None):
    req = urllib.request.Request(url, data=None if data is None else json.dumps(data).encode(),
                                 headers={'Content-Type': 'application/json'}, method='POST' if data is not None else 'GET')
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b'{}')


def state():
    return {d['name']: d for d in call(GZ + '/api/state')[1]['drones']}


def pos(d):
    return (d['x'], d['y'], d['z'])


def send(phrase):
    _, r = call(HU + '/api/hu/command', {'utterance': phrase, 'source': 'typed'})
    if r.get('band') == 'CONFIRM':
        st, c = call(HU + '/api/hu/confirm', {'cmd_id': r['cmd_id']})
        r['dispatched'] = st == 200 and c.get('ok') is True
        r['why'] = c.get('error')
    return r


def wait(pred, timeout):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        s = state()
        if pred(s):
            return s, round(time.monotonic() - t0, 2)
        time.sleep(0.15)
    return state(), None


class MinSep(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True); self.stop = False; self.v = (math.inf, None)

    def run(self):
        while not self.stop:
            s = state(); air = [n for n in NAMES if s[n]['z'] > .3]
            for a, b in combinations(air, 2):
                d = math.dist(pos(s[a]), pos(s[b]))
                if d < self.v[0]: self.v = (round(d, 3), [a, b])
            time.sleep(.15)


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else '.'
    s0 = state()
    if not all(d['telemetry'] and d['mode'] in ('READY', 'GROUNDED') for d in s0.values()):
        sys.exit('Fleet must be parked with fresh telemetry.')
    ms = MinSep(); ms.start(); legs = []

    def setup(label, phrase, pred, timeout):
        r = send(phrase); s, t = wait(pred, timeout) if r.get('dispatched') else (state(), None)
        legs.append({'leg': label, 'result': 'PASS' if t is not None else 'FAIL', 'settle_s': t, 'why': r.get('why')})
        print(f"{legs[-1]['result']:4} {label:34} settle={t}s {r.get('why') or ''}")
        return t is not None

    def leg(label, phrase, members, vec):
        b = state(); r = send(phrase)
        if not r.get('dispatched'):
            legs.append({'leg': label, 'phrase': phrase, 'result': 'FAIL', 'why': r.get('why') or r.get('reason')})
            print(f"FAIL {label:34} not dispatched: {legs[-1]['why']}"); return False
        def arrived(s):
            return all(s[n]['mode'] == 'HOVER' for n in members) and all(
                math.dist(pos(s[n]), tuple(p + v for p, v in zip(pos(b[n]), vec))) < .08 for n in members)
        s, t = wait(arrived, 30)
        moved = {n: [round(a - c, 3) for a, c in zip(pos(s[n]), pos(b[n]))] for n in members}
        shape = max((abs(math.dist(pos(s[x]), pos(s[y])) - math.dist(pos(b[x]), pos(b[y]))) for x, y in combinations(members, 2)), default=0)
        still = [n for n in NAMES if n not in members and math.dist(pos(s[n]), pos(b[n])) > .05]
        ok = t is not None and shape < .06 and not still
        legs.append({'leg': label, 'phrase': phrase, 'interpreter': r.get('decision_source'), 'label': r.get('reason'),
                     'result': 'PASS' if ok else 'FAIL', 'settle_s': t, 'commanded_m': vec, 'moved_m': moved,
                     'max_shape_change_m': round(shape, 3), 'non_members_moved': still})
        print(f"{legs[-1]['result']:4} {label:34} settle={t}s shape_change={shape:.3f} m "
              f"e.g. {members[0]} moved {moved[members[0]]} non_members_moved={still}")
        return ok

    ok = setup('all take off', 'All, take off', lambda s: all(d['mode'] == 'HOVER' and d['z'] > 1.4 for d in s.values()), 30)
    ok = ok and setup('form V', 'All form a V shape', lambda s: all(math.dist(pos(d), tuple(d['target'])) < .06 and d['mode'] == 'FORMATION' for d in s.values()), 45)
    if ok: time.sleep(1.5)
    allm = list(NAMES)
    for label, phrase, vec in [('swarm forward 1 m', 'All move forward 1 meter', (1, 0, 0)),
                               ('swarm right 50 cm', 'All move right 50 centimeters', (0, -.5, 0)),
                               ('swarm back 1 m', 'All move back 1 meter', (-1, 0, 0)),
                               ('swarm left 50 cm', 'All move left 50 centimeters', (0, .5, 0)),
                               ('swarm up 30 cm', 'All move up 30 centimeters', (0, 0, .3)),
                               ('swarm down 30 cm', 'All move down 30 centimeters', (0, 0, -.3))]:
        if not ok: break
        ok = leg(label, phrase, allm, vec); time.sleep(1)
    if ok:
        sub = ['Horus', 'Ra', 'Thoth', 'Osiris']
        # Arena-frame back 0.4 m: a sub-group travels while Anubis holds position.
        ok = leg('sub-group back 40 cm (Anubis holds)', 'All except Anubis move back 40 centimeters', sub, (-.4, 0, 0))
    setup('all land', 'All, land', lambda s: all(d['mode'] == 'GROUNDED' for d in s.values()), 60)
    if not all(d['mode'] in ('GROUNDED', 'READY') for d in state().values()):
        call(GZ + '/api/command', {'action': 'emergency_land', 'targets': allm}); wait(lambda s: all(d['mode'] in ('GROUNDED', 'READY') for d in s.values()), 60)
    ms.stop = True; time.sleep(.3)
    out = {'generated_utc': datetime.now(timezone.utc).isoformat(), 'frame': 'ARENA (forward=+x, right=-y)',
           'legs': legs, 'min_observed_separation_m': ms.v[0], 'min_sep_pair': ms.v[1],
           'verdict': 'PASS' if legs and all(l['result'] == 'PASS' for l in legs) else 'FAIL'}
    p = f"{outdir}/swarm_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"; json.dump(out, open(p, 'w'), indent=2)
    print(f"min observed separation {ms.v[0]} m {ms.v[1]}\nSWARM: {out['verdict']} -> {p}")


if __name__ == '__main__':
    main()
