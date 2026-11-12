#!/usr/bin/env python3
"""A/B flight experiment: does the team's coordination make the fleet fly better?

Each round draws 4 random flight-validated candidate shapes from the CURRENT position, then:
  arm A (team)   - the team rule: drop candidates the Safety Officer vetoes (acceptance/strategist.too_tight),
                   pick the shortest longest-leg (= the strategy round's decision rule without the talk);
  arm B (random) - an uncoordinated fleet: any candidate MA'AT's planner accepts, chosen at random.
Arms alternate A,B,A,B... so both see the same drift of starting positions. Every transition is flown for real
through HU (CONFIRM band, confirmed by this script) and MA'AT. Measured per transition (from Gazebo telemetry):
  form_s        dispatch -> every drone within 0.12 m of its slot
  min_sep_m     closest approach between any two drones during the transition (polled ~8 Hz)
  holds         SEPARATION_HOLD events raised by MA'AT during the transition
  planned_longest_m / planned_clear_m   the plan's numbers
Writes acceptance/results/ab_coordination_<stamp>.json and prints a summary. Nothing here bypasses MA'AT.
  python3 acceptance/ab_coordination.py [rounds_per_arm=12] [seed]
"""
import itertools, json, math, random, statistics, subprocess, sys, time
from pathlib import Path
from urllib import request

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'acceptance'))
import strategist as st  # noqa: E402

SUP = 'http://127.0.0.1:8765/api/state'
PROBE = [str(BASE / 'hu-venv/bin/python'), str(BASE / 'acceptance/hu_band_probe.py'), '--confirm']


def state():
    with request.urlopen(SUP, timeout=3) as r:
        return json.load(r)


def fleet_of(s):
    return {d['name']: d for d in s['drones']}


def send(phrase):
    out = subprocess.run(PROBE + [phrase], capture_output=True, text=True, timeout=90, cwd=BASE).stdout
    return out.splitlines()[0] if out else ''


def fly(phrase):
    seen = {(e.get('time'), e.get('message')) for e in state().get('events', [])}
    t0 = time.monotonic()
    r = send(phrase)
    if 'rejected' in r or r.startswith('REFUSE'):
        return {'refused': r[-160:]}
    min_sep, form = 99.0, None
    while time.monotonic() - t0 < 30:
        s = state(); f = fleet_of(s)
        pos = [(d['x'], d['y'], d['z']) for d in f.values()]
        min_sep = min(min_sep, min(math.dist(a, b) for a, b in itertools.combinations(pos, 2)))
        err = max(math.dist((d['x'], d['y'], d['z']), tuple(d['target'])) for d in f.values())
        if time.monotonic() - t0 > 1.0 and err < 0.12:
            form = time.monotonic() - t0
            break
        time.sleep(0.12)
    holds = sum(1 for e in state().get('events', []) if e.get('kind') == 'SEPARATION_HOLD'
                and (e.get('time'), e.get('message')) not in seen)
    return {'form_s': round(form, 2) if form else None, 'min_sep_m': round(min_sep, 3), 'holds': holds}


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    rng = random.Random(int(sys.argv[2]) if len(sys.argv) > 2 else 2026)
    ok = sorted(st.flown_ok())
    s = state()
    if not all(d['mode'] in ('HOVER', 'FORMATION') for d in s['drones']):
        print('fleet must be airborne and steady (HOVER/FORMATION)'); sys.exit(1)
    rows, last = [], None
    for i in range(2 * n):
        arm = 'team' if i % 2 == 0 else 'random'
        f = fleet_of(state())
        cands = rng.sample([c for c in ok if c != last], 4)
        plans = {}
        for c in cands:
            try:
                plans[c] = st.plan(c, f)
            except ValueError:
                pass
        if not plans:
            continue
        if arm == 'team':
            safe = {c: p for c, p in plans.items() if not st.too_tight(p)} or plans
            pick = min(safe, key=lambda c: (round(safe[c]['longest'], 1), -safe[c]['clear']))
        else:
            pick = rng.choice(sorted(plans))
        p = plans[pick]
        res = fly('All form ' + st.PHRASE[pick])
        row = {'i': i, 'arm': arm, 'shape': pick, 'candidates': cands, 'planned_longest_m': round(p['longest'], 3),
               'planned_clear_m': round(p['clear'], 3), **res}
        rows.append(row); last = pick
        print(f"{i:2} {arm:6} {pick:18} planned longest {p['longest']:.2f} clear {p['clear']:.2f} -> "
              + (f"form {res.get('form_s')} s, min sep {res.get('min_sep_m')} m, holds {res.get('holds')}" if 'refused' not in res
                 else 'REFUSED ' + res['refused']), flush=True)
        if res.get('holds'):
            time.sleep(4)
        time.sleep(1.0)
    summ = {}
    for arm in ('team', 'random'):
        r = [x for x in rows if x['arm'] == arm and x.get('form_s')]
        if not r:
            continue
        summ[arm] = {'n': len(r), 'form_s_mean': round(statistics.mean(x['form_s'] for x in r), 2),
                     'form_s_median': round(statistics.median(x['form_s'] for x in r), 2),
                     'planned_longest_mean': round(statistics.mean(x['planned_longest_m'] for x in r), 2),
                     'min_sep_worst': round(min(x['min_sep_m'] for x in r), 3),
                     'min_sep_mean': round(statistics.mean(x['min_sep_m'] for x in r), 3),
                     'holds_total': sum(x['holds'] for x in r),
                     'refused': sum(1 for x in rows if x['arm'] == arm and 'refused' in x)}
    out = BASE / 'acceptance/results' / f"ab_coordination_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
    out.write_text(json.dumps({'rows': rows, 'summary': summ}, indent=1))
    print(json.dumps(summ, indent=1)); print('->', out)


if __name__ == '__main__':
    main()
