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
import itertools, json, math, os, random, sys, time
from pathlib import Path
from urllib import request

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'festival'))
import formation_planner as fp  # noqa: E402
sys.path.insert(0, str(BASE / 'spaceops_v8'))

NAMES = ['Horus', 'Ra', 'Thoth', 'Anubis', 'Osiris']
PHRASE = {'circle': 'a circle', 'zigzag': 'a zigzag', 'square': 'a square', 'staggered': 'a staggered line',
          'x': 'an X', 'diamond': 'a diamond', 'cross': 'a cross', 'arrow': 'an arrow', 't': 'a T shape',
          'fan': 'a fan', 'arc': 'an arc', 'inverted_v': 'an inverted V', 'triangle': 'a triangle',
          'grid': 'a grid', 'two_rows': 'two rows', 'right_echelon': 'a right echelon',
          'left_echelon': 'a left echelon', 'perimeter': 'a perimeter', 'v': 'a V', 'wedge': 'a wedge',
          'l': 'an L shape', 'line': 'a line', 'column': 'a column',
          # extended catalogue (OFFLINE_CHECKED: planner/geometry checked, not yet flown)
          'letter_c': 'the letter C', 'letter_j': 'the letter J', 'letter_m': 'the letter M',
          'letter_w': 'the letter W', 'letter_n': 'the letter N', 'letter_z': 'the letter Z',
          'letter_s': 'the letter S', 'letter_u': 'the letter U', 'letter_y': 'the letter Y',
          'letter_f': 'the letter F', 'letter_p': 'the letter P',
          'digit_1': 'the number 1', 'digit_2': 'the number 2', 'digit_3': 'the number 3',
          'digit_4': 'the number 4', 'digit_5': 'the number 5', 'digit_7': 'the number 7',
          'cassiopeia': 'Cassiopeia', 'southern_cross': 'the Southern Cross', 'cygnus': 'Cygnus',
          'delphinus': 'Delphinus', 'lyra': 'Lyra', 'corona_borealis': 'Corona Borealis',
          'big_dipper': 'the Big Dipper', 'orion': 'Orion', 'leo_sickle': 'the Sickle of Leo',
          'aquila': 'Aquila', 'cepheus': 'Cepheus', 'scorpius': 'Scorpius',
          'pyramid': 'a pyramid', 'ankh': 'an ankh', 'wedjat_eye': 'the wedjat eye', 'obelisk': 'an obelisk',
          'nile': 'the Nile', 'winged_sun': 'a winged sun',
          'two_one_two': 'a two one two', 'bowtie': 'a bowtie', 'two_three_zone': 'a two three zone',
          'three_two_zone': 'a three two zone', 'box_and_one': 'a box and one', 'finger_four': 'a finger four',
          'echelon_pairs': 'echelon pairs', 'trail_pairs': 'trail pairs', 'diamond_and_one': 'a diamond and one',
          'staggered_column': 'a staggered column', 'combat_spread': 'a combat spread',
          'check_mark': 'a check mark', 'lightning': 'a lightning bolt', 'spiral': 'a spiral', 'steps': 'steps',
          'pentomino_f': 'the F pentomino', 'pentomino_l': 'the L pentomino', 'pentomino_n': 'the N pentomino',
          'pentomino_p': 'the P pentomino', 'pentomino_t': 'the T pentomino', 'pentomino_u': 'the U pentomino',
          'pentomino_v': 'the V pentomino', 'pentomino_w': 'the W pentomino', 'pentomino_y': 'the Y pentomino',
          'pentomino_z': 'the Z pentomino',
          'v_left': 'a V pointing left', 'v_right': 'a V pointing right', 'tight_v': 'a tight V',
          'deep_v': 'a deep V', 'wide_v': 'a wide V', 'arrow_left': 'an arrow pointing left',
          'arrow_right': 'an arrow pointing right', 'arrow_down': 'an arrow pointing down',
          't_inverted': 'an inverted T', 'l_mirrored': 'a mirrored L', 'gamma': 'a gamma',
          'inverted_triangle': 'an inverted triangle', 'inverted_arc': 'an inverted arc',
          'wide_diamond': 'a wide diamond', 'big_square': 'a big square', 'small_square': 'a small square',
          'big_x': 'a big X', 'big_cross': 'a big cross', 'wide_circle': 'a wide circle',
          'small_circle': 'a small circle', 'inverted_pentagon': 'an inverted pentagon'}
LABEL = {k: v.split(' ', 1)[1] if v.startswith(('a ', 'an ')) else v for k, v in PHRASE.items()}
VETO_M = 1.10          # planned straight-line pass must stay >= 1.1 m: live flights came up to ~0.15 m
                       # closer than the plan (line->diamond 0.96 planned / 0.81 flown), watchdog holds < 0.82 m
CATALOG = BASE / 'festival' / 'formation_catalog.json'


def flown_ok():
    """Shapes whose live Gazebo validation passed (status VALIDATED); aliases resolve to their base."""
    try:
        cat = json.loads(CATALOG.read_text())
    except Exception:
        return set()
    return {k for k, v in cat.items() if v.get('status') == 'VALIDATED' and not v.get('alias_of')}
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
    start = min(math.dist(a, b) for a, b in itertools.combinations(cur, 2))
    return {'legs': legs, 'longest': max(legs.values()), 'total': sum(legs.values()), 'clear': clear, 'pair': pair,
            'start_sep': start, 'goal': {n: g for n, g in zip(NAMES, goal)}}


# ---- constructive deliberation helpers (all computed, nothing scripted) -------------------------------
def family(shape, ok_shapes):
    """Variants of the same idea: the base shape and every validated shape derived from it."""
    try:
        cat = json.loads(CATALOG.read_text())
    except Exception:
        return []
    base = cat.get(shape, {}).get('derived_from') or shape
    return [k for k in ok_shapes if k != shape and (k == base or cat.get(k, {}).get('derived_from') == base)]


def after(p):
    """Fleet state once a candidate has formed (drones at their assigned slots)."""
    return {n: {'name': n, 'x': g[0], 'y': g[1], 'z': g[2]} for n, g in p['goal'].items()}


def lookahead(p, pool, rng, k=12):
    """Cheapest safe NEXT move (longest leg) available after flying candidate p; None if none found."""
    nxt, best = after(p), None
    for c in rng.sample(pool, min(k, len(pool))):
        try:
            q = plan(c, nxt)
        except ValueError:
            continue
        if not too_tight(q) and q['longest'] > 0.05 and (best is None or q['longest'] < best[1]):
            best = (c, q['longest'])
    return best


def history(shape):
    """Last measured flight of this shape by the team: (predicted_s, actual_s) or None."""
    ev = Path.home() / 'hu-telepresence-gazebo' / 'logs' / 'team_merit.jsonl'
    last = None
    try:
        for line in ev.read_text().splitlines():
            e = json.loads(line)
            if e.get('kind') == 'best_prediction' and e.get('shape') == shape:
                last = (e.get('predicted'), e.get('actual'))
    except Exception:
        pass
    return last


def too_tight(p):
    """Veto only a move that brings two drones closer than VETO_M AND closer than they already are
    (a legal but tight starting spacing, e.g. 1.01 m after a staggered takeoff, must not veto everything)."""
    return p['clear'] < VETO_M and p['clear'] < p['start_sep'] - 0.02


class Chat:
    def __init__(self):
        try:
            self.tok = get(OPS + '/api/ops/session')['token']
        except Exception:
            self.tok = None
        self.cid = 'strategy-' + time.strftime('%H%M%S')

    def say(self, speaker, text, evidence=None, to='TEAM'):
        if os.environ.get('STRATEGY_ECHO') == '1':      # operator-side transcript on stderr (testing only)
            print(f'{speaker} -> {to}: {text}', file=sys.stderr)
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
    ok_shapes = flown_ok()   # only shapes that passed a live flight test (set STRATEGY_ALL=1 to include unflown)
    pool = [s for s in PHRASE if s not in hist[-8:] and (s in ok_shapes or os.environ.get('STRATEGY_ALL') == '1')]
    rng = random.Random()
    cands = rng.sample(pool, 4)
    chat = Chat()
    # fixed roles: Horus calls, Anubis vetoes, Thoth reads the numbers
    LEAD, SAFETY, ANALYST = 'Horus', 'Anubis', 'Thoth'
    chat.say(LEAD, 'Next move, RSCL Flight. On the table: ' + ', '.join(LABEL[c] for c in cands) + '. Proposals?',
             {'candidates': cands})
    plans = {}
    for c in cands:
        try:
            plans[c] = plan(c, fleet)
        except ValueError as e:
            plans[c] = {'error': str(e)}
    ok = {c: p for c, p in plans.items() if 'error' not in p}
    # every non-Lead drone proposes its own shortest leg (self-interest); three speak, random order
    proposals = {}
    for n in NAMES:
        if n != LEAD and ok:
            proposals[n] = min(ok, key=lambda c: ok[c]['legs'][n])
    order = [n for n in proposals]
    rng.shuffle(order)
    for n in order[:3]:
        mine = proposals[n]
        other = max(ok, key=lambda c: ok[c]['legs'][n])
        chat.say(n, f"Propose {LABEL[mine]}. My leg is only {ok[mine]['legs'][n]:.1f} m"
                    + (f", versus {ok[other]['legs'][n]:.1f} for {LABEL[other]}." if other != mine else '.'),
                 {'proposal': mine, 'my_leg_m': round(ok[mine]['legs'][n], 2)}, to=LEAD)
    # Debate: a drone challenges another's proposal with team numbers; the challenged drone defends or concedes.
    spoke = order[:3]
    pairs = [(a, b) for a in spoke for b in spoke if a != b and proposals[a] != proposals[b]]
    if pairs:
        x, y = rng.choice(pairs)
        sx, sy = proposals[x], proposals[y]
        cost_x = ok[sy]['legs'][x] - ok[sx]['legs'][x]
        chat.say(x, f"{y}, your {LABEL[sy]} saves you {ok[sx]['legs'][y] - ok[sy]['legs'][y]:.1f} m, but it costs me "
                    f"{cost_x:.1f} m extra. Fleet-wise, {LABEL[sy]} has a {ok[sy]['longest']:.1f} m longest leg and "
                    f"{LABEL[sx]} {ok[sx]['longest']:.1f}.", {'challenge': [sy, sx]}, to=y)
        if ok[sy]['longest'] <= ok[sx]['longest']:
            chat.say(y, f"Disagree, {x}. The longest leg decides the time, and {LABEL[sy]} still finishes first: "
                        f"{ok[sy]['longest']:.1f} against {ok[sx]['longest']:.1f}.", {'defends': sy}, to=x)
        else:
            chat.say(y, f"Fair point, {x}. {LABEL[sx]} is faster for the team. I'll back it.", {'concedes_to': sx}, to=x)
            proposals[y] = sx
    # Lead asks the Safety Officer before the vetoes
    chat.say(LEAD, f"{SAFETY}, any safety concerns before I call it?", {}, to=SAFETY)
    # Safety Officer vetoes
    safe = {}
    for c, p in plans.items():
        if 'error' in p:
            chat.say(SAFETY, f"Veto {LABEL[c]}: MA'AT's planner finds no separation-safe paths from here.",
                     {'shape': c, 'planner': p['error']})
        elif too_tight(p):
            a, b = p['pair']
            chat.say(SAFETY, f"Veto {LABEL[c]}. {a} and {b} would pass at {p['clear']:.2f} m. "
                             f"I want at least {VETO_M:.1f}.", {'shape': c, 'tightest_pass_m': round(p['clear'], 2),
                                                                'pair': [a, b], 'veto_below_m': VETO_M})
        else:
            safe[c] = p
    if safe and len(safe) == len(plans):
        tight = min(safe, key=lambda c: safe[c]['clear'])
        line = (f"everything clears my {VETO_M:.1f} m line." if safe[tight]['clear'] >= VETO_M else
                f"that is under my {VETO_M:.1f} m line only because we start that close; no option makes it tighter.")
        chat.say(SAFETY, f"No concerns, {LEAD}. Tightest pass on the table is {LABEL[tight]} at "
                         f"{safe[tight]['clear']:.2f} m; {line}",
                 {'tightest': tight, 'tightest_pass_m': round(safe[tight]['clear'], 2)}, to=LEAD)
    elif safe:
        chat.say(SAFETY, f"The rest is clear, {LEAD}. {len(safe)} option{'s' if len(safe) > 1 else ''} left.",
                 {'safe': sorted(safe)}, to=LEAD)
    if not safe:
        chat.say(LEAD, 'Nothing safe on the table. Holding this shape. Next round.', {})
        print('')
        return
    chat.say(ANALYST, 'Numbers. ' + '; '.join(f"{LABEL[c]}: {p['longest']:.1f} m longest leg, {p['clear']:.1f} m tightest"
                                             for c, p in sorted(safe.items(), key=lambda kv: kv[1]['longest'])) + '.',
             {c: {'longest_leg_m': round(p['longest'], 2), 'tightest_pass_m': round(p['clear'], 2),
                  'total_m': round(p['total'], 2)} for c, p in safe.items()})
    # ---- constructive deliberation: the team improves on the Lead's first instinct -----------------
    # support = proposals after the debate (a conceding drone's vote moved); ties on time go to the team's backing
    votes = {c: sum(1 for n in proposals if proposals[n] == c) for c in safe}
    first = min(safe, key=lambda c: (round(safe[c]['longest'], 1), -votes.get(c, 0), -safe[c]['clear']))
    pick, reasons = first, []
    tied = [c for c in safe if c != first and round(safe[c]['longest'], 1) == round(safe[first]['longest'], 1)]
    backing = (f" Equal on time with {LABEL[tied[0]]}, but {votes[first]} of you backed it." if tied and votes.get(first, 0) > votes.get(tied[0], 0)
               else f" {votes[first]} of you proposed it." if votes.get(first, 0) >= 2 else '')
    if pairs and first not in (sx, sy):
        debated = [c for c in (sx, sy) if c in safe]
        if debated:
            backing += (f" You argued {LABEL[sx]} against {LABEL[sy]}, but this beats both: "
                        f"{safe[first]['longest']:.1f} against {min(safe[c]['longest'] for c in debated):.1f}.")
    chat.say(LEAD, f"First instinct: {LABEL[first]}, {safe[first]['longest']:.1f} m longest leg.{backing} Can anyone improve it?",
             {'tentative': first, 'votes': votes}, to='TEAM')
    # (1) Analyst: a variant of the same idea that forms faster
    best_var = None
    for v in family(first, ok_shapes):
        try:
            q = plan(v, fleet)
        except ValueError:
            continue
        if not too_tight(q) and q['longest'] <= safe[first]['longest'] - 0.2 and (best_var is None or q['longest'] < best_var[1]['longest']):
            best_var = (v, q)
    if best_var:
        v, q = best_var
        chat.say(ANALYST, f"Same idea, faster: {LABEL[v]} instead of {LABEL[first]}. Longest leg {q['longest']:.1f} "
                          f"against {safe[first]['longest']:.1f}, tightest pass {q['clear']:.1f}.",
                 {'variant': v, 'longest_leg_m': round(q['longest'], 2)}, to=LEAD)
        safe[v] = q; pick = v; reasons.append(f"{ANALYST}'s variant")
        chat.say(LEAD, f"Good catch, {ANALYST}. Taking {LABEL[v]}.", {}, to=ANALYST)
    elif family(first, ok_shapes):
        chat.say(ANALYST, f"I checked the variants of {LABEL[first]}; none is faster from here.", {}, to=LEAD)
    # (2) Look-ahead: what does each option leave for the NEXT move? (score = this move + half the next)
    thinker = 'Osiris' if LEAD != 'Osiris' else 'Ra'
    pool = sorted(ok_shapes)
    rival = min((c for c in safe if c != pick), key=lambda c: safe[c]['longest'], default=None)
    if rival:
        la_p, la_r = lookahead(safe[pick], pool, rng), lookahead(safe[rival], pool, rng)
        if la_p and la_r:
            sp = safe[pick]['longest'] + 0.5 * la_p[1]
            sr = safe[rival]['longest'] + 0.5 * la_r[1]
            if sr + 0.3 < sp:
                chat.say(thinker, f"Think one move ahead. After {LABEL[pick]}, our cheapest next change is a "
                                  f"{la_p[1]:.1f} m leg; after {LABEL[rival]} it's only {la_r[1]:.1f}. "
                                  f"Two-move cost {sr:.1f} against {sp:.1f}. I'd take {LABEL[rival]}.",
                         {'lookahead': {pick: round(sp, 2), rival: round(sr, 2)}}, to=LEAD)
                chat.say(LEAD, f"Agreed, {thinker}. {LABEL[rival]} sets us up better.", {}, to=thinker)
                pick = rival; reasons.append(f"{thinker}'s look-ahead")
            else:
                chat.say(thinker, f"Looked one move ahead: {LABEL[pick]} still leaves us a {la_p[1]:.1f} m next move. "
                                  f"Keep it.", {'lookahead_next_leg_m': round(la_p[1], 2)}, to=LEAD)
    # (3) Memory: measured history of the chosen shape
    h = history(pick)
    hr = history(rival) if rival and rival != pick else None
    rival_clean = rival and rival != pick and not (hr and hr[0] and hr[1] is not None and hr[1] - hr[0] >= 1.0)
    if h and h[0] and h[1] is not None and h[1] - h[0] >= 1.0 and rival_clean:
        chat.say(SAFETY if SAFETY != LEAD else ANALYST,
                 f"Careful: last time we flew {LABEL[pick]} it took {h[1]:.1f} seconds against {h[0]:.1f} predicted. "
                 + (f"{LABEL[rival]} flew to plan last time ({hr[1]:.1f} s)." if hr and hr[1] is not None else
                  f"We have no record of {LABEL[rival]} going badly."),
                 {'history': {'shape': pick, 'predicted_s': h[0], 'actual_s': h[1]}, 'rival_history': hr},
                 to=LEAD)
        pick = rival; reasons.append('flight history')
    elif h and h[1] is not None:
        chat.say(ANALYST, f"For the record, we flew {LABEL[pick]} before: {h[1]:.1f} seconds.", {'history': h}, to='TEAM')
    p = safe[pick]
    far = max(p['legs'], key=p['legs'].get)
    if pick != first:
        chat.say(LEAD, f"Change of plan: {LABEL[first]} becomes {LABEL[pick]}, thanks to {' and '.join(reasons)}. "
                       f"Longest leg {safe[first]['longest']:.1f} becomes {p['longest']:.1f}.",
                 {'first': first, 'final': pick, 'improved_by': reasons}, to='TEAM')
    preds = {n: round(1.5 + 1.9 * p['longest'], 2) for n in NAMES}   # simple time estimate (overhead + speed)
    chat.say(LEAD, f"Call {LABEL[pick]}. {far} has the longest run, {p['longest']:.1f} m; tightest pass "
                   f"{p['clear']:.1f}. I predict {preds[LEAD]:.1f} seconds. Push!",
             {'decision': pick, 'rule': 'min longest leg (ties: team support) among candidates whose tightest pass >= 1.1 m or no tighter than the start',
              'longest_leg_m': round(p['longest'], 2), 'tightest_pass_m': round(p['clear'], 2),
              'predicted_s': preds})
    # read-backs: the long-run drone and one more teammate acknowledge; a doubter says so with its own prediction
    chat.say(far, f"Wilco. That's my long run, {p['legs'][far]:.1f} m. I'll keep it smooth.", {'leg_m': round(p['legs'][far], 2)}, to=LEAD)
    doubter = max((n for n in NAMES if n != LEAD), key=lambda n: preds[n])
    if preds[doubter] - preds[LEAD] >= 0.5:
        chat.say(doubter, f"Copy. My model says {preds[doubter]:.1f} seconds, not {preds[LEAD]:.1f}. We'll see who's right.",
                 {'my_prediction_s': preds[doubter]}, to=LEAD)
    else:
        other = rng.choice([n for n in NAMES if n not in (LEAD, far)])
        chat.say(other, f"Copy, {LEAD}. Moving on your mark.", {}, to=LEAD)
    HIST.write_text(json.dumps((hist + [pick])[-20:]))
    print(PHRASE[pick] + '|' + pick)


if __name__ == '__main__':
    main()
