#!/usr/bin/env python3
"""Offline: every planner shape for 5 aircraft - slot geometry, spacing, and whether the
mission parser can reach it by voice/text. No Gazebo needed."""
import itertools, math, sys
sys.path.insert(0, '/home/hadedy/hu-telepresence-gazebo/festival')
from formation_planner import slots, MIN_SEP, BOUND
from mission_intents import SHAPES, parse

kinds = sorted(set(SHAPES.values()))
phrase_for = {}
for alias, kind in sorted(SHAPES.items(), key=lambda x: len(x[0])):
    phrase_for.setdefault(kind, alias)
print(f'{"shape":15} {"slots":5} {"min_slot_sep":>12} {"extent_m":>9}  parser phrase -> result')
for k in kinds:
    try:
        p = slots(k, 5)
        sep = min(math.dist(a, b) for a, b in itertools.combinations(p, 2))
        ext = max(max(abs(x), abs(y)) for x, y in p)
        geo = f'{len(p):5} {sep:12.2f} {ext:9.2f}'
    except ValueError as e:
        geo = f'ERROR {e}'
    phrase = f'all form a {phrase_for[k]}'
    r = parse(phrase)
    got = r.get('params', {}).get('shape') if r and 'error' not in r else (r or {}).get('error', 'HU route')
    print(f'{k:15} {geo}  "{phrase}" -> {got}')
