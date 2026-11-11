#!/usr/bin/env python3
"""Build selectable HU-MA'AT Gazebo environments from worlds/hu_festival.sdf.

  python3 scripts/build_environments.py        -> worlds/hu_env_{forest,military,mountains}.sdf

Each variant keeps EVERYTHING the flight stack depends on unchanged: world name
'hu_festival' (reset/control services), the five drone <include>s, physics,
plugins, the arena floor and its landing pads. It only adds a themed ground,
sky/lighting and static VISUAL-ONLY scenery (no collision geometry, so physics
cost and flight behaviour are unchanged).

Safety: the supervisor's collision checks know only about aircraft, so every
scenery object is placed outside a keep-out square of |x|,|y| < 5.0 m (flight
box is |x|,|y| <= 3.35 m) and the default camera's view corridor (south side)
is kept clear of tall objects. Built only from SDF 1.6 primitives (box,
cylinder, sphere): nothing is downloaded.
"""
import math, random, re
from pathlib import Path

BASE = Path.home() / 'hu-telepresence-gazebo' / 'worlds'
SRC = BASE / 'hu_festival.sdf'
KEEP_OUT = 5.0


def mat(r, g, b, emissive=0.0):
    e = f'{r*emissive:.3f} {g*emissive:.3f} {b*emissive:.3f} 1'
    return (f'<material><ambient>{r:.3f} {g:.3f} {b:.3f} 1</ambient><diffuse>{r:.3f} {g:.3f} {b:.3f} 1</diffuse>'
            f'<specular>0.05 0.05 0.05 1</specular><emissive>{e}</emissive></material>')


class Scene:
    def __init__(self, name):
        self.name = name; self.vis = []; self.n = 0

    def _add(self, geom, x, y, z, color, roll=0, pitch=0, yaw=0, emissive=0.0):
        self.n += 1
        self.vis.append(f'<visual name="{self.name}_{self.n}"><pose>{x:.3f} {y:.3f} {z:.3f} {roll:.3f} {pitch:.3f} {yaw:.3f}</pose>'
                        f'<geometry>{geom}</geometry>{mat(*color, emissive=emissive)}<cast_shadows>false</cast_shadows></visual>')

    def box(self, x, y, z, sx, sy, sz, color, yaw=0, roll=0, pitch=0, emissive=0.0):
        self._add(f'<box><size>{sx:.3f} {sy:.3f} {sz:.3f}</size></box>', x, y, z, color, roll, pitch, yaw, emissive)

    def cyl(self, x, y, z, r, h, color, roll=0, pitch=0, yaw=0):
        self._add(f'<cylinder><radius>{r:.3f}</radius><length>{h:.3f}</length></cylinder>', x, y, z, color, roll, pitch, yaw)

    def sph(self, x, y, z, r, color):
        self._add(f'<sphere><radius>{r:.3f}</radius></sphere>', x, y, z, color)

    def model(self):
        return (f'\n    <model name="hu_env_{self.name}"><static>true</static><link name="scenery">\n      '
                + '\n      '.join(self.vis) + '\n    </link></model>\n')


def ring_points(rng, count, rmin, rmax, camera_clear=True):
    """Random points in an annulus outside the keep-out square and camera corridor."""
    pts = []
    while len(pts) < count:
        a = rng.uniform(0, 2 * math.pi); r = rng.uniform(rmin, rmax)
        x, y = r * math.cos(a), r * math.sin(a)
        if max(abs(x), abs(y)) < KEEP_OUT: continue
        # Default camera sits at (0,-5.9,3.7) looking north: keep its corridor open.
        if camera_clear and y < -4.5 and abs(x) < 5.5 + (-y - 4.5) * 0.6: continue
        pts.append((x, y))
    return pts


def ground(s, color, size=160):
    s.box(0, 0, -0.17, size, size, 0.02, color)


# ---------------------------------------------------------------- FOREST
def forest(rng):
    s = Scene('forest')
    ground(s, (0.16, 0.24, 0.11))
    for x, y in ring_points(rng, 70, 7, 60, camera_clear=False):  # darker undergrowth patches
        s.cyl(x, y, -0.15, rng.uniform(1.5, 4), 0.02, (0.11, 0.18, 0.08))
    # dirt track from the arena edge into the woods (east)
    s.box(12, 0, -0.145, 14, 1.6, 0.02, (0.33, 0.26, 0.17))
    for x, y in ring_points(rng, 150, 6, 45):
        kind = rng.random(); h = rng.uniform(2.2, 5.5)
        if kind < 0.6:  # conifer: trunk + stacked shrinking spheres
            s.cyl(x, y, h * 0.25, 0.12, h * 0.5, (0.30, 0.20, 0.12))
            green = (rng.uniform(0.07, 0.12), rng.uniform(0.22, 0.32), rng.uniform(0.08, 0.13))
            for k in range(4):
                r = h * (0.30 - 0.06 * k)
                s.sph(x, y, h * (0.40 + 0.17 * k), max(0.18, r), green)
        else:  # broadleaf: trunk + clustered canopy
            s.cyl(x, y, h * 0.3, 0.16, h * 0.6, (0.36, 0.25, 0.15))
            green = (rng.uniform(0.15, 0.25), rng.uniform(0.32, 0.45), rng.uniform(0.10, 0.16))
            for _ in range(3):
                s.sph(x + rng.uniform(-.5, .5), y + rng.uniform(-.5, .5), h * rng.uniform(.65, .85), h * rng.uniform(.18, .26), green)
    for x, y in ring_points(rng, 40, 5.4, 30):  # bushes
        s.sph(x, y, 0.15, rng.uniform(.3, .6), (0.14, 0.30, 0.10))
    for x, y in ring_points(rng, 25, 5.4, 35):  # boulders and fallen logs
        if rng.random() < .5: s.box(x, y, .2, rng.uniform(.5, 1.2), rng.uniform(.4, 1), rng.uniform(.3, .7), (0.40, 0.40, 0.37), yaw=rng.uniform(0, 3), roll=rng.uniform(-.2, .2))
        else: s.cyl(x, y, .15, .15, rng.uniform(1.5, 3), (0.32, 0.22, 0.13), pitch=math.pi / 2, yaw=rng.uniform(0, 3))
    return s, {'sky': True, 'ambient': '0.50 0.56 0.50 1', 'background': '0.55 0.70 0.85 1', 'gui_bg': '0.55 0.70 0.85'}


# ---------------------------------------------------------------- MILITARY
def military(rng):
    s = Scene('military')
    ground(s, (0.55, 0.48, 0.34))
    tan, olive, concrete, dark = (0.62, 0.55, 0.38), (0.29, 0.32, 0.20), (0.58, 0.58, 0.55), (0.15, 0.14, 0.12)
    # perimeter fence (posts + wire band) around the base at 14 m, gap for the camera side
    for i in range(-14, 15, 2):
        for (x, y) in ((i, 14), (14, i), (-14, i)):
            s.cyl(x, y, .9, .05, 1.8, (0.35, 0.35, 0.35))
    for (x, y, sx, sy) in ((0, 14, 28, .03), (14, 0, .03, 28), (-14, 0, .03, 28)):
        s.box(x, y, 1.2, sx, sy, .9, (0.45, 0.45, 0.45))
    # concrete jersey barriers and sandbag walls just outside the arena
    for i in range(-4, 5):
        s.box(i * 1.1, 5.6, .4, 1.0, .5, .8, concrete)
        s.box(5.6, i * 1.1, .4, .5, 1.0, .8, concrete)
        s.box(-5.6, i * 1.1, .4, .5, 1.0, .8, concrete)
    for (x0, y0, yaw) in ((-8, 8, .6), (8, 8, -.6), (9, -3, 1.57), (-9, -3, 1.57)):
        for row in range(3):
            for k in range(6):
                dx = (k - 2.5) * .55 + (row % 2) * .27
                s.box(x0 + dx * math.cos(yaw), y0 + dx * math.sin(yaw), .12 + row * .22, .52, .3, .2, tan, yaw=yaw)
    # watchtowers
    for (x, y) in ((-12, 12), (12, 12), (12, -10), (-12, -10)):
        for dx, dy in ((-.8, -.8), (.8, -.8), (-.8, .8), (.8, .8)):
            s.cyl(x + dx, y + dy, 2.2, .08, 4.4, (0.33, 0.27, 0.20))
        s.box(x, y, 4.5, 2.2, 2.2, .15, (0.33, 0.27, 0.20)); s.box(x, y, 5.6, 2.6, 2.6, .12, olive)
        s.box(x, y, 4.9, 2.2, .06, .7, (0.33, 0.27, 0.20))
    # tents, containers, vehicles
    for (x, y, yaw) in ((-9, 18, 0), (-4, 19, 0), (1, 18, 0), (18, 6, 1.57)):
        s.box(x, y, .8, 4, 3, 1.6, olive, yaw=yaw)
        s.box(x, y, 1.9, 4.1, 2.3, .15, olive, yaw=yaw, roll=.55); s.box(x, y, 1.9, 4.1, 2.3, .15, olive, yaw=yaw, roll=-.55)
    for i, (x, y, c) in enumerate(((-19, 3, (0.25, 0.30, 0.22)), (-19, 6, (0.45, 0.28, 0.18)), (-19, 9, (0.25, 0.30, 0.22)), (20, -6, (0.30, 0.33, 0.24)))):
        s.box(x, y, 1.3, 6, 2.4, 2.6, c)
    for (x, y, yaw) in ((7, 18, .3), (11, 17, .2)):  # armoured vehicles: hull, turret, barrel, tracks
        s.box(x, y, .7, 4.2, 2.2, 1.0, olive, yaw=yaw); s.box(x, y, 1.45, 2.0, 1.6, .6, olive, yaw=yaw)
        s.cyl(x + 2.0 * math.cos(yaw), y + 2.0 * math.sin(yaw), 1.5, .08, 2.6, dark, pitch=math.pi / 2, yaw=yaw)
        for side in (-1, 1):
            s.box(x - side * 1.15 * math.sin(yaw), y + side * 1.15 * math.cos(yaw), .4, 4.4, .45, .8, dark, yaw=yaw)
    # radar dome on a mast and a helipad outside the arena (west)
    s.cyl(-18, -14, 3, .3, 6, concrete); s.sph(-18, -14, 6.5, 1.6, (0.85, 0.85, 0.82))
    s.cyl(-10, -16, -.13, 4, .04, (0.30, 0.30, 0.30)); s.box(-10, -16, -.1, .5, 3, .02, (0.9, 0.9, 0.9)); s.box(-11, -16, -.1, .5, 3, .02, (0.9, 0.9, 0.9)); s.box(-10.5, -16, -.1, 1.5, .5, .02, (0.9, 0.9, 0.9))
    # war-zone edge: craters, rubble, ruined walls beyond the fence
    for x, y in ring_points(rng, 30, 17, 45, camera_clear=False):
        s.cyl(x, y, -.15, rng.uniform(1, 2.5), .03, (0.22, 0.19, 0.15))
    for x, y in ring_points(rng, 60, 17, 50):
        s.box(x, y, .15, rng.uniform(.3, 1), rng.uniform(.3, 1), rng.uniform(.2, .6), (0.5, 0.48, 0.44), yaw=rng.uniform(0, 3), roll=rng.uniform(-.4, .4))
    for (x, y, yaw) in ((-26, 20, .4), (25, 22, -.3), (28, 0, 1.4), (-28, -8, 1.2)):
        for k in range(5):
            s.box(x + k * 1.2 * math.cos(yaw), y + k * 1.2 * math.sin(yaw), rng.uniform(.8, 2.2) / 2, 1.2, .35, rng.uniform(.8, 2.6), (0.62, 0.58, 0.50), yaw=yaw)
    return s, {'sky': True, 'ambient': '0.62 0.58 0.50 1', 'background': '0.78 0.74 0.62 1', 'gui_bg': '0.78 0.74 0.62'}


# ---------------------------------------------------------------- MOUNTAINS
def mountains(rng):
    s = Scene('mountains')
    ground(s, (0.30, 0.36, 0.22), size=400)
    rock, rock2, snow = (0.42, 0.40, 0.38), (0.34, 0.33, 0.32), (0.93, 0.94, 0.96)
    # far ranges: buried spheres make massifs; smaller white spheres cap the summits
    for k in range(26):
        a = k / 26 * 2 * math.pi + rng.uniform(-.08, .08)
        if math.sin(a) < -0.86: continue  # keep a valley open behind the camera's view
        dist = rng.uniform(70, 120); r = rng.uniform(25, 45); h = rng.uniform(14, 34)
        x, y = dist * math.cos(a), dist * math.sin(a)
        s.sph(x, y, h - r, r, rock if k % 2 else rock2)
        if h > 20: s.sph(x, y, h - 4.5, 6.5, snow)
    # foothills
    for x, y in ring_points(rng, 26, 25, 55):
        r = rng.uniform(6, 14); s.sph(x, y, -r * .55, r, (0.27, 0.34, 0.19))
    # alpine pines and boulders near the arena
    for x, y in ring_points(rng, 70, 6, 24):
        h = rng.uniform(2.5, 5.0)
        s.cyl(x, y, h * .25, .11, h * .5, (0.28, 0.19, 0.12))
        for k in range(4): s.sph(x, y, h * (.42 + .16 * k), max(.17, h * (.27 - .055 * k)), (0.08, 0.22, 0.12))
    for x, y in ring_points(rng, 45, 5.5, 30):
        s.box(x, y, .25, rng.uniform(.6, 2.2), rng.uniform(.5, 1.8), rng.uniform(.4, 1.4), rock, yaw=rng.uniform(0, 3), roll=rng.uniform(-.3, .3), pitch=rng.uniform(-.2, .2))
    # a mountain lake to the north-east
    s.cyl(20, 18, -.14, 7, .02, (0.20, 0.38, 0.52))
    return s, {'sky': True, 'ambient': '0.55 0.60 0.66 1', 'background': '0.62 0.75 0.90 1', 'gui_bg': '0.62 0.75 0.90'}


ENVIRONMENTS = {'forest': forest, 'military': military, 'mountains': mountains}


def build(name, fn, src):
    s, look = fn(random.Random(f'hu-maat-{name}'))  # deterministic layout per environment
    w = src
    w = re.sub(r'<ambient_light>[^<]*</ambient_light>', f'<ambient_light>{look["ambient"].rsplit(" ",1)[0]}</ambient_light>', w, count=1)
    w = re.sub(r'<background_color>[^<]*</background_color>', f'<background_color>{look["gui_bg"]}</background_color>', w, count=1)
    w = re.sub(r'<camera_clip>\s*<near>[^<]*</near>\s*<far>[^<]*</far>',
               '<camera_clip>\n          <near>.08</near>\n          <far>600</far>', w, count=1)
    scene = (f'<scene>\n      <ambient>{look["ambient"]}</ambient>\n      <background>{look["background"]}</background>\n'
             + ('      <sky></sky>\n' if look['sky'] else '') + '      <shadows>false</shadows>\n    </scene>')
    w = re.sub(r'<scene>\s*<ambient>[^<]*</ambient>\s*<background>[^<]*</background>\s*</scene>', scene, w, count=1)
    if 'hu_env_' in w: raise SystemExit('source already contains environment scenery')
    w = w.replace('  </world>', s.model() + '  </world>', 1)
    out = BASE / f'hu_env_{name}.sdf'; out.write_text(w)
    return out, s.n


if __name__ == '__main__':
    src = SRC.read_text()
    for name, fn in ENVIRONMENTS.items():
        out, n = build(name, fn, src)
        print(f'{name:10} -> {out} ({n} scenery visuals)')
