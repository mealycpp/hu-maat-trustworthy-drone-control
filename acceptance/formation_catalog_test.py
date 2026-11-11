#!/usr/bin/env python3
"""Live formation catalog: fly EVERY planner shape in Gazebo and measure it.

Usage: python3 formation_catalog_test.py [results_dir] [shape ...]   (default: all shapes)

Per formation (all 5 aircraft, chained transitions from the previous shape):
  planned_min_sep_m   - closest approach of the straight-line plan (start -> assigned slot)
  observed_min_sep_m  - closest approach actually observed in Gazebo during the transition (6.7 Hz)
  settle_s            - command dispatch -> every aircraft within 0.12 m of its slot and slow
  rms_error_m         - RMS 3D slot error after a 1.5 s hold
  shape_error_m       - RMS error of the observed shape vs the planner's slot geometry after the
                        best rigid alignment (translation only: tests GEOMETRY, not placement)
  proximity_holds     - supervisor SEPARATION_HOLD interventions during the transition
A shape is VALIDATED only if it was dispatched, settled within 30 s, rms_error <= 0.10 m,
no proximity hold fired and observed separation stayed >= 0.82 m (the hold threshold).
Otherwise EXPERIMENTAL (or REFUSED when the planner declined it - a safe refusal).
Writes results JSON, a Markdown table, and festival/formation_catalog.json (status per shape).
"""
import json, math, sys, threading, time, urllib.request
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path.home() / 'hu-telepresence-gazebo' / 'festival'))
from formation_planner import slots  # geometry reference only
from mission_intents import SHAPES

HU, GZ = 'http://127.0.0.1:8100', 'http://127.0.0.1:8765'
NAMES = ('Horus', 'Ra', 'Thoth', 'Anubis', 'Osiris')
ALIASES = {'pentagon': 'circle', 'relay_chain': 'line'}  # identical slot geometry
ORDER = ['line', 'v', 'circle', 'grid', 'diamond', 'wedge', 'inverted_v', 'arrow', 'triangle', 'cross',
         'x', 'square', 'perimeter', 'two_rows', 'staggered', 'zigzag', 'left_echelon', 'right_echelon',
         'column', 'arc', 'fan', 't', 'l', 'pentagon', 'relay_chain']
PHRASE = {}
for alias, kind in sorted(SHAPES.items(), key=lambda x: len(x[0])):
    PHRASE.setdefault(kind, alias)


def call(url, data=None):
    req = urllib.request.Request(url, data=None if data is None else json.dumps(data).encode(),
                                 headers={'Content-Type': 'application/json'}, method='POST' if data is not None else 'GET')
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b'{}')


def full():
    return call(GZ + '/api/state')[1]


def state():
    return {d['name']: d for d in full()['drones']}


def pos(d):
    return (d['x'], d['y'], d['z'])


def send(phrase):
    _, r = call(HU + '/api/hu/command', {'utterance': phrase, 'source': 'typed'})
    if r.get('band') == 'CONFIRM':
        st, c = call(HU + '/api/hu/confirm', {'cmd_id': r['cmd_id']})
        r['dispatched'] = st == 200 and c.get('ok') is True
        r['why'] = c.get('error')
    return r


class Sep(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True); self.stop = False; self.reset()

    def reset(self):
        self.v = (math.inf, None)

    def run(self):
        while not self.stop:
            s = state(); air = [n for n in NAMES if s[n]['z'] > .3]
            for a, b in combinations(air, 2):
                d = math.dist(pos(s[a]), pos(s[b]))
                if d < self.v[0]: self.v = (round(d, 3), [a, b])
            time.sleep(.15)


def planned_min_sep(start, goal):
    best = math.inf
    for k in range(41):
        f = k / 40
        p = {n: tuple(a + (b - a) * f for a, b in zip(start[n], goal[n])) for n in NAMES}
        best = min(best, min(math.dist(p[a], p[b]) for a, b in combinations(NAMES, 2)))
    return round(best, 3)


def shape_error(s, kind):
    """RMS distance between observed XY shape and the slot geometry, best translation + slot matching."""
    ref = slots(kind, 5)
    obs = [(s[n]['x'], s[n]['y']) for n in NAMES]
    ox = sum(x for x, _ in obs) / 5; oy = sum(y for _, y in obs) / 5
    rx = sum(x for x, _ in ref) / 5; ry = sum(y for _, y in ref) / 5
    obs = [(x - ox, y - oy) for x, y in obs]; ref = [(x - rx, y - ry) for x, y in ref]
    import itertools
    best = min(sum(math.dist(o, r) ** 2 for o, r in zip(obs, perm)) for perm in itertools.permutations(ref))
    return round(math.sqrt(best / 5), 3)


def holds_since(seen):
    ev = [e for e in full().get('events', []) if e.get('kind') == 'SEPARATION_HOLD']
    new = [e for e in ev if json.dumps(e, sort_keys=True) not in seen]
    for e in ev: seen.add(json.dumps(e, sort_keys=True))
    return new


def main():
    outdir = Path(sys.argv[1] if len(sys.argv) > 1 else '.')
    order = sys.argv[2:] or ORDER
    s0 = state()
    if not all(d['telemetry'] and d['mode'] in ('READY', 'GROUNDED') for d in s0.values()):
        sys.exit('Fleet must be parked with fresh telemetry.')
    seen = set(); holds_since(seen)
    sep = Sep(); sep.start(); rows = []
    r = send('All, take off')
    t0 = time.monotonic()
    while time.monotonic() - t0 < 40 and not all(d['mode'] == 'HOVER' and d['z'] > 1.4 for d in state().values()):
        time.sleep(.2)
    if not all(d['mode'] == 'HOVER' for d in state().values()):
        print('takeoff failed', r.get('why')); order = []
    for kind in order:
        time.sleep(1.0)
        before = state(); sep.reset(); holds_since(seen)
        phrase = f'All form a {PHRASE[kind]}' if len(PHRASE[kind]) > 1 else f'All form a {PHRASE[kind]} shape'
        r = send(phrase)
        row = {'shape': kind, 'phrase': phrase, 'alias_of': ALIASES.get(kind), 'interpreter': r.get('decision_source')}
        if not r.get('dispatched'):
            row.update(status='REFUSED', why=r.get('why') or r.get('reason'))
            rows.append(row); print(f"REFUSED  {kind:14} {row['why']}"); continue
        after_cmd = state()
        goal = {n: tuple(after_cmd[n]['target']) for n in NAMES}
        row['planned_min_sep_m'] = planned_min_sep({n: pos(before[n]) for n in NAMES}, goal)
        t0 = time.monotonic(); settle = None
        while time.monotonic() - t0 < 30:
            s = state()
            if all(math.dist(pos(s[n]), tuple(s[n]['target'])) < .12 for n in NAMES) and \
               all(s[n]['mode'] in ('FORMATION', 'HOVER') for n in NAMES):
                settle = round(time.monotonic() - t0, 2); break
            time.sleep(.15)
        time.sleep(1.5)
        s = state()
        errs = [math.dist(pos(s[n]), tuple(s[n]['target'])) for n in NAMES]
        new_holds = holds_since(seen)
        row.update(settle_s=settle, rms_error_m=round(math.sqrt(sum(e * e for e in errs) / 5), 3),
                   max_error_m=round(max(errs), 3), shape_error_m=shape_error(s, kind),
                   observed_min_sep_m=sep.v[0], closest_pair=sep.v[1], proximity_holds=len(new_holds),
                   positions={n: [round(v, 3) for v in pos(s[n])] for n in NAMES}, targets={n: s[n]['target'] for n in NAMES})
        ok = settle is not None and row['rms_error_m'] <= .10 and not new_holds and sep.v[0] >= .82
        row['status'] = 'VALIDATED' if ok else 'EXPERIMENTAL'
        rows.append(row)
        print(f"{row['status']:12} {kind:14} settle={settle}s rms={row['rms_error_m']:.3f} shape={row['shape_error_m']:.3f} "
              f"min_sep obs={sep.v[0]} plan={row['planned_min_sep_m']} holds={len(new_holds)}")
        if not all(d['mode'] in ('FORMATION', 'HOVER') for d in state().values()):
            send('All, stop'); time.sleep(2)
    send('All, land')
    t0 = time.monotonic()
    while time.monotonic() - t0 < 60 and not all(d['mode'] == 'GROUNDED' for d in state().values()):
        time.sleep(.3)
    if not all(d['mode'] in ('GROUNDED', 'READY') for d in state().values()):
        call(GZ + '/api/command', {'action': 'emergency_land', 'targets': list(NAMES)})
    sep.stop = True
    stamp = f'{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}'
    out = {'generated_utc': datetime.now(timezone.utc).isoformat(), 'world_env': (Path.home() / 'hu-telepresence-gazebo/logs/current_env').read_text().split()[0] if (Path.home() / 'hu-telepresence-gazebo/logs/current_env').exists() else None,
           'criteria': 'VALIDATED = dispatched, settled <30 s, RMS slot error <=0.10 m, no proximity hold, observed separation >=0.82 m',
           'formations': rows}
    (outdir / f'formation_catalog_{stamp}.json').write_text(json.dumps(out, indent=2))
    lines = ['| Formation | Status | Settle (s) | RMS slot error (m) | Shape error (m) | Min sep observed / planned (m) | Holds |', '|---|---|---|---|---|---|---|']
    for r in rows:
        name = r['shape'] + (f" (alias of {r['alias_of']})" if r.get('alias_of') else '')
        if r['status'] == 'REFUSED':
            lines.append(f"| {name} | REFUSED (safe) | - | - | - | - | - |"); continue
        lines.append(f"| {name} | {r['status']} | {r['settle_s']} | {r['rms_error_m']:.3f} | {r['shape_error_m']:.3f} | {r['observed_min_sep_m']} / {r['planned_min_sep_m']} | {r['proximity_holds']} |")
    (outdir / f'formation_catalog_{stamp}.md').write_text('\n'.join(lines) + '\n')
    catalog = Path.home() / 'hu-telepresence-gazebo/festival/formation_catalog.json'
    prev = json.loads(catalog.read_text()) if catalog.exists() else {}
    for r in rows:
        prev[r['shape']] = {'status': r['status'], 'alias_of': r.get('alias_of'), 'evidence': f'formation_catalog_{stamp}.json',
                            'rms_error_m': r.get('rms_error_m'), 'observed_min_sep_m': r.get('observed_min_sep_m')}
    catalog.write_text(json.dumps(prev, indent=2))
    v = sum(r['status'] == 'VALIDATED' for r in rows)
    distinct = sum(r['status'] == 'VALIDATED' and not r.get('alias_of') for r in rows)
    print('\n'.join(lines))
    print(f'\nCATALOG: {v}/{len(rows)} VALIDATED ({distinct} distinct geometries) -> {outdir}/formation_catalog_{stamp}.md')


if __name__ == '__main__':
    main()
