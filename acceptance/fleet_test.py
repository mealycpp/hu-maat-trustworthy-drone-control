#!/usr/bin/env python3
"""HU-MA'AT Phase 2.4/3 live fleet test: all takeoff -> all hold -> formation(s) -> all land.

Usage: python3 fleet_test.py [results_dir] [shape ...]   (default shapes: v)
Commands go through the real HU bridge and are operator-confirmed. Telemetry
is sampled every 150 ms for the whole run to measure minimum pairwise
separation; formation RMS error is computed against the supervisor's
assigned targets once every aircraft settles.
"""
import json, math, sys, threading, time, urllib.request
from datetime import datetime, timezone
from itertools import combinations

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


class Sampler(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True); self.stop = False; self.min_sep = (math.inf, None, None); self.samples = 0

    def run(self):
        while not self.stop:
            s = state()
            air = [n for n in NAMES if s[n]['z'] > 0.3]
            for a, b in combinations(air, 2):
                d = math.dist(*[(s[n]['x'], s[n]['y'], s[n]['z']) for n in (a, b)])
                if d < self.min_sep[0]:
                    self.min_sep = (round(d, 3), [a, b], round(time.time(), 2))
            self.samples += 1
            time.sleep(0.15)


def send(phrase):
    _, r = call(HU + '/api/hu/command', {'utterance': phrase, 'source': 'typed'})
    e = {'phrase': phrase, 'band': r.get('band'), 'action': r.get('action'), 'targets': r.get('targets'),
         'interpreter': r.get('decision_source'), 'reason': r.get('reason'), 'dispatched': r.get('dispatched')}
    if r.get('band') == 'CONFIRM':
        st, c = call(HU + '/api/hu/confirm', {'cmd_id': r['cmd_id']})
        e['dispatched'] = st == 200 and c.get('ok') is True
        e['dispatch_reply'] = c
    return e


def wait(pred, timeout):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        s = state()
        if pred(s):
            return s, round(time.monotonic() - t0, 2)
        time.sleep(0.2)
    return state(), None


def settled(s):
    return all(math.dist((d['x'], d['y'], d['z']), tuple(d['target'])) < 0.12 for d in s.values())


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else '.'
    shapes = sys.argv[2:] or ['v']
    s0 = state()
    if not all(d['telemetry'] and d['mode'] in ('READY', 'GROUNDED') for d in s0.values()):
        sys.exit('Fleet must be on the ground with fresh telemetry.')
    sampler = Sampler(); sampler.start()
    steps = []

    def step(name, phrase, pred, timeout, extra=None):
        e = send(phrase)
        s, secs = wait(pred, timeout) if e['dispatched'] else (state(), None)
        rec = {'step': name, 'decision': e, 'settle_s': secs, 'result': 'PASS' if secs is not None else 'FAIL',
               'modes': {n: s[n]['mode'] for n in NAMES}, 'z': {n: s[n]['z'] for n in NAMES}}
        if extra: rec.update(extra(s))
        steps.append(rec)
        print(f"{rec['result']:4} {name:22} band={e['band']} interp={e['interpreter']} settle={secs}s "
              f"{e.get('reason') if not e['dispatched'] else ''} {rec.get('formation_rms_m','')}")
        return rec['result'] == 'PASS'

    ok = step('all take off', 'All, take off',
              lambda s: all(d['mode'] == 'HOVER' and d['z'] > 1.4 for d in s.values()), 30)
    ok = ok and step('all stop (hold)', 'All, stop',
                     lambda s: all(d['mode'] == 'HOVER' and d['z'] > 1.4 for d in s.values()), 8)
    for shape in shapes:
        if not ok: break
        def rms(s):
            errs = [math.dist((d['x'], d['y'], d['z']), tuple(d['target'])) for d in s.values()]
            return {'formation_rms_m': round(math.sqrt(sum(e * e for e in errs) / len(errs)), 3),
                    'positions': {n: [s[n]['x'], s[n]['y'], s[n]['z']] for n in NAMES},
                    'targets': {n: s[n]['target'] for n in NAMES}}
        time.sleep(2)
        ok = step(f'formation {shape}', f'All form a {shape} shape' if len(shape) <= 2 else f'All form a {shape}',
                  lambda s: all(d['mode'] in ('FORMATION', 'HOVER') for d in s.values()) and settled(s), 45, rms)
    landed = step('all land', 'All, land',
                  lambda s: all(d['mode'] == 'GROUNDED' for d in s.values()), 60)
    if not landed:
        call(GZ + '/api/command', {'action': 'emergency_land', 'targets': list(NAMES)})
        wait(lambda s: all(d['mode'] in ('GROUNDED', 'READY') for d in s.values()), 60)
    sampler.stop = True; time.sleep(0.3)
    out = {'generated_utc': datetime.now(timezone.utc).isoformat(), 'shapes': shapes, 'steps': steps,
           'min_observed_separation_m': sampler.min_sep[0], 'min_sep_pair': sampler.min_sep[1],
           'telemetry_samples': sampler.samples,
           'verdict': 'PASS' if all(x['result'] == 'PASS' for x in steps) else 'FAIL'}
    path = f"{outdir}/fleet_{'-'.join(shapes)}_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    json.dump(out, open(path, 'w'), indent=2)
    print(f"min observed separation {sampler.min_sep[0]} m {sampler.min_sep[1]}; samples={sampler.samples}")
    print(f"FLEET: {out['verdict']} -> {path}")


if __name__ == '__main__':
    main()
