#!/usr/bin/env python3
"""Team strategy round: the drones choose the next formation together, then the show flies it.

REAL decision, deterministic policy (no language model):
  * candidates: 4 shapes not used recently, sampled from the validated catalog;
  * every candidate is planned from the drones' CURRENT positions with the same slot planner MA'AT uses
    (formation_planner.assign) -> per-drone leg length and the tightest straight-line pass;
  * each drone proposes the candidate with its own shortest leg (self-interest);
  * Anubis (safety) vetoes candidates the planner cannot solve or whose tightest pass is < VETO_M;
  * Horus (leader) picks the safe candidate with the shortest longest-leg (fastest to form),
    tie-break on the larger clearance.
The round is posted to SwarmChat (source DETERMINISTIC_AGENT_POLICY, numbers attached as evidence)
and the chosen command phrase is printed on stdout for formation_show.sh. MA'AT still checks the move.
"""
import itertools, json, math, random, sys, time
from pathlib import Path
from urllib import request

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'festival'))
import formation_planner as fp  # noqa: E402

NAMES = ['Horus', 'Ra', 'Thoth', 'Anubis', 'Osiris']
PHRASE = {'circle': 'a circle', 'zigzag': 'a zigzag', 'square': 'a square', 'staggered': 'a staggered line',
          'x': 'an X', 'diamond': 'a diamond', 'cross': 'a cross', 'arrow': 'an arrow', 't': 'a T shape',
          'fan': 'a fan', 'arc': 'an arc', 'inverted_v': 'an inverted V', 'triangle': 'a triangle',
          'grid': 'a grid', 'two_rows': 'two rows', 'right_echelon': 'a right echelon',
          'left_echelon': 'a left echelon', 'perimeter': 'a perimeter', 'v': 'a V', 'wedge': 'a wedge',
          'l': 'an L shape', 'line': 'a line', 'column': 'a column'}
LABEL = {k: v.split(' ', 1)[1] if v.startswith(('a ', 'an ')) else v for k, v in PHRASE.items()}
VETO_M = 1.00          # planned straight-line pass must stay >= 1.0 m (watchdog holds below 0.82 m)
HIST = Path('/tmp/hu_strategist_history.json')
SUP = 'http://127.0.0.1:8765/api/state'
OPS = 'http://127.0.0.1:8766'


class D:
    def __init__(self, x, y, z): self.x, self.y, self.z = x, y, z


def get(url):
    with request.urlopen(url, timeout=3) as r:
        return json.load(r)


def plan(shape, fleet):
    drones = {n: D(fleet[n]['x'], fleet[n]['y'], fleet[n]['z']) for n in NAMES}
    perm = fp.assign(NAMES, drones, fp.slots(shape, 5))        # raises ValueError if MA'AT would refuse
    cur = [(drones[n].x, drones[n].y, drones[n].z) for n in NAMES]
    goal = [(x, y, fp.ALT) for x, y in perm]
    legs = {n: math.dist(c, g) for n, c, g in zip(NAMES, cur, goal)}
    clear, pair = 99.0, None
    for k in range(61):
        f = k / 60
        occ = [tuple(a + (b - a) * f for a, b in zip(c, g)) for c, g in zip(cur, goal)]
        for (i, a), (j, b) in itertools.combinations(enumerate(occ), 2):
            d = math.dist(a, b)
            if d < clear:
                clear, pair = d, (NAMES[i], NAMES[j])
    return {'legs': legs, 'longest': max(legs.values()), 'total': sum(legs.values()), 'clear': clear, 'pair': pair}


class Chat:
    def __init__(self):
        try:
            self.tok = get(OPS + '/api/ops/session')['token']
        except Exception:
            self.tok = None
        self.cid = 'strategy-' + time.strftime('%H%M%S')

    def say(self, speaker, text, evidence=None, to='TEAM'):
        if not self.tok:
            return
        body = json.dumps({'speaker': speaker, 'text': text, 'to': to, 'conversation_id': self.cid,
                           'evidence': evidence or {}}).encode()
        req = request.Request(OPS + '/api/ops/chat/agent', data=body, method='POST',
                              headers={'Content-Type': 'application/json', 'X-RSCL-CSRF': self.tok})
        try:
            request.urlopen(req, timeout=3).read()
        except Exception:
            pass
        time.sleep(0.45)   # readable pace in the chat panel


def main():
    fleet = {d['name']: d for d in get(SUP)['drones']}
    hist = json.loads(HIST.read_text()) if HIST.exists() else []
    pool = [s for s in PHRASE if s not in hist[-8:]]
    rng = random.Random()
    cands = rng.sample(pool, 4)
    chat = Chat()
    chat.say('Horus', 'Next move, team. On the table: ' + ', '.join(LABEL[c] for c in cands) + '. Proposals?',
             {'candidates': cands})
    plans = {}
    for c in cands:
        try:
            plans[c] = plan(c, fleet)
        except ValueError as e:
            plans[c] = {'error': str(e)}
    ok = {c: p for c, p in plans.items() if 'error' not in p}
    # each drone proposes its own shortest leg (self-interest), speaking in a random order
    order = NAMES[1:]
    rng.shuffle(order)
    for n in order[:3]:
        if not ok:
            break
        mine = min(ok, key=lambda c: ok[c]['legs'][n])
        other = max(ok, key=lambda c: ok[c]['legs'][n])
        chat.say(n, f"I'd take {LABEL[mine]}: my leg is only {ok[mine]['legs'][n]:.1f} m"
                    + (f" (vs {ok[other]['legs'][n]:.1f} m for {LABEL[other]})." if other != mine else '.'),
                 {'proposal': mine, 'my_leg_m': round(ok[mine]['legs'][n], 2)}, to='Horus')
    # safety vetoes
    safe = {}
    for c, p in plans.items():
        if 'error' in p:
            chat.say('Anubis', f"Veto {LABEL[c]}: MA'AT's planner finds no separation-safe paths from here.",
                     {'shape': c, 'planner': p['error']})
        elif p['clear'] < VETO_M:
            a, b = p['pair']
            chat.say('Anubis', f"Veto {LABEL[c]}: {a} and {b} would pass at {p['clear']:.2f} m. "
                               f"I want at least {VETO_M:.1f} m.", {'shape': c, 'tightest_pass_m': round(p['clear'], 2),
                                                                     'pair': [a, b], 'veto_below_m': VETO_M})
        else:
            safe[c] = p
    if not safe:
        chat.say('Horus', 'Nothing safe on the table. Holding this shape; next round.', {})
        print('')
        return
    chat.say('Thoth', 'Numbers: ' + '; '.join(f"{LABEL[c]} {p['longest']:.1f} m longest leg, {p['clear']:.1f} m tightest"
                                             for c, p in sorted(safe.items(), key=lambda kv: kv[1]['longest'])) + '.',
             {c: {'longest_leg_m': round(p['longest'], 2), 'tightest_pass_m': round(p['clear'], 2),
                  'total_m': round(p['total'], 2)} for c, p in safe.items()})
    pick = min(safe, key=lambda c: (round(safe[c]['longest'], 1), -safe[c]['clear']))
    p = safe[pick]
    far = max(p['legs'], key=p['legs'].get)
    chat.say('Horus', f"Call: {LABEL[pick]}. Fastest safe option: {far} has the longest run at {p['longest']:.1f} m, "
                      f"tightest pass {p['clear']:.1f} m. Go!",
             {'decision': pick, 'rule': 'min longest leg among candidates with tightest pass >= 1.0 m',
              'longest_leg_m': round(p['longest'], 2), 'tightest_pass_m': round(p['clear'], 2)})
    HIST.write_text(json.dumps((hist + [pick])[-20:]))
    print(PHRASE[pick])


if __name__ == '__main__':
    main()
