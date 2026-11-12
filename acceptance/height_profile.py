#!/usr/bin/env python3
"""3D formations: turn a flown 2D formation into its 3D height profile (pyramid, helix, staircase, ...).

  height_profile.py <base_shape_key>   ->  prints "<profile_name>" then one line per drone "<Name> <dz_cm>"
                                            (nothing if the base shape has no profile)

Each drone is matched to its slot of slots(base, 5) by nearest horizontal distance from its live position
(all five drones participate, so the slot set is arena-centred). Offsets come from
formation_planner.HEIGHT_PROFILES and are rounded to 10 cm (the minimum move) and clamped so the
resulting altitude stays in 1.1-2.7 m. Purely vertical moves: horizontal separation (>= 0.88 m by
construction of the slots) is unchanged. The show flies them one drone at a time through HU and MA'AT.
"""
import json, math, random, sys
from pathlib import Path
from urllib import request

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'festival'))
import formation_planner as fp  # noqa: E402

NAMES = ['Horus', 'Ra', 'Thoth', 'Anubis', 'Osiris']


def profiles_for(base):
    return [k for k, (b, _) in getattr(fp, 'HEIGHT_PROFILES', {}).items() if b == base]


def plan(base, fleet, name=None):
    opts = profiles_for(base)
    if not opts:
        return None, {}
    name = name or random.choice(opts)
    _, offs = fp.HEIGHT_PROFILES[name]
    pts = fp.slots(base, 5)
    free = set(range(len(pts)))
    out = {}
    for n in sorted(NAMES, key=lambda n: min(math.hypot(fleet[n]['x'] - x, fleet[n]['y'] - y) for x, y in pts)):
        i = min(free, key=lambda i: math.hypot(fleet[n]['x'] - pts[i][0], fleet[n]['y'] - pts[i][1]))
        free.discard(i)
        z = fleet[n]['z']
        dz = round(offs.get(i, 0.0) * 10) * 10                    # centimetres, 10 cm steps
        dz = max(int(round((1.1 - z) * 100)), min(int(round((2.7 - z) * 100)), dz))
        dz = int(round(dz / 10.0)) * 10
        out[n] = dz
    return name, out


if __name__ == '__main__':
    with request.urlopen('http://127.0.0.1:8765/api/state', timeout=3) as r:
        fleet = {d['name']: d for d in json.load(r)['drones']}
    name, out = plan(sys.argv[1], fleet)
    if name:
        print(name)
        for n in NAMES:
            print(n, out[n])
