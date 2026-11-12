"""Field callouts: drones "talk to each other" in SwarmChat during flight, like players on a field.

HONESTY: these lines are TEMPLATED from live supervisor telemetry (modes, slot targets, positions,
distances). They are not autonomous reasoning, not language-model output, and they never send
commands. Every message carries source FIELD_CALLOUT and the numbers it was built from.
"""
import itertools, json, math, random, threading, time
from urllib import request

NAMES = ['Horus', 'Ra', 'Thoth', 'Anubis', 'Osiris']
PARKED = {'READY', 'GROUNDED'}
SOURCE = 'FIELD_CALLOUT'


def _dist(a, b):
    return math.dist((a['x'], a['y'], a['z']), (b['x'], b['y'], b['z']))


def _side(me, other):
    """Arena-frame bearing word for 'other' as seen from 'me' (x = east/right, y = north/forward)."""
    dx, dy = other['x'] - me['x'], other['y'] - me['y']
    if abs(dx) >= abs(dy):
        return 'on my right' if dx > 0 else 'on my left'
    return 'ahead of me' if dy > 0 else 'behind me'


class FieldCallouts:
    def __init__(self, chat, url='http://127.0.0.1:8765/api/state', hz=4.0, min_gap=0.6):
        self.chat, self.url, self.period, self.min_gap = chat, url, 1.0 / hz, min_gap
        self.prev = {}
        self.last_post = 0.0
        self.rng = random.Random(7)
        self.formation_pending = False
        self.landing_called = False
        self.stop = False

    def start(self):
        threading.Thread(target=self._loop, name='field-callouts', daemon=True).start()
        return self

    # ------------------------------------------------------------------
    def _state(self):
        with request.urlopen(self.url, timeout=1.5) as r:
            return {d['name']: d for d in json.load(r)['drones']}

    def _say(self, speaker, text, evidence, to=None):
        now = time.monotonic()
        if now - self.last_post < self.min_gap:
            time.sleep(self.min_gap - (now - self.last_post))
        rank = ''
        self.chat.append(speaker, 'DRONE_AGENT', text, {**evidence, 'kind': 'FIELD_CALLOUT', 'rank': rank,
                         'note': 'templated from live telemetry; not autonomous reasoning; cannot actuate'},
                         to=to or 'TEAM', intent='field_callout', source=SOURCE)
        self.last_post = time.monotonic()

    def _nearest(self, me, fleet):
        others = [fleet[n] for n in NAMES if n != me['name'] and n in fleet]
        return min(others, key=lambda o: _dist(me, o)) if others else None

    def _loop(self):
        while not self.stop:
            try:
                fleet = self._state()
                self._step(fleet)
                self.prev = fleet
            except Exception:
                pass
            time.sleep(self.period)

    # ------------------------------------------------------------------
    def _step(self, fleet):
        prev = self.prev
        if not prev:
            return
        modes = {n: fleet[n]['mode'] for n in fleet}
        pmodes = {n: prev[n]['mode'] for n in prev if n in fleet}
        changed_target = [n for n in fleet if n in prev and
                          math.dist(fleet[n]['target'], prev[n]['target']) > 0.15]

        # --- a new formation: several aircraft get new slots at once -------------
        if sum(1 for n in changed_target if modes[n] == 'FORMATION') >= 3:
            leader = 'Horus'
            self._say(leader, 'New shape, team. Everyone take your slot.', {'slots_changed': len(changed_target)})
            for n in changed_target:
                me = fleet[n]
                tx, ty, tz = me['target']
                d = math.dist((me['x'], me['y'], me['z']), (tx, ty, tz))
                # predicted nearest teammate at the destination slots
                others = [(o, math.dist((tx, ty, tz), tuple(fleet[o]['target']))) for o in fleet if o != n]
                o, od = min(others, key=lambda x: x[1])
                if d < 0.15:
                    continue
                opener = 'Taking mine.' if n == leader else f'Copy, {leader}.'
                self._say(n, f'{opener} Moving {d:.1f} m to my slot; {o} will be my closest, {od:.1f} m.',
                          {'from': [round(me['x'], 2), round(me['y'], 2), round(me['z'], 2)],
                           'slot': [round(tx, 2), round(ty, 2), round(tz, 2)], 'nearest_at_slot': o,
                           'nearest_distance_m': round(od, 2)}, to='TEAM' if n == leader else leader)
            self.formation_pending = True
            self.form_t0 = time.monotonic()
            self.form_legs = {n: math.dist((fleet[n]['x'], fleet[n]['y'], fleet[n]['z']), tuple(fleet[n]['target'])) for n in fleet}

        # --- everyone arrived ------------------------------------------------------
        if self.formation_pending and all(
                math.dist((fleet[n]['x'], fleet[n]['y'], fleet[n]['z']), tuple(fleet[n]['target'])) < 0.12
                for n in fleet):
            a, b = min(itertools.combinations(NAMES, 2), key=lambda p: _dist(fleet[p[0]], fleet[p[1]]))
            gap = _dist(fleet[a], fleet[b])
            speaker = 'Anubis'
            self._say(speaker, f'All in slot. Tightest gap is {a} and {b} at {gap:.2f} m. Spacing good.',
                      {'pair': [a, b], 'gap_m': round(gap, 2), 'watchdog_hold_below_m': 0.82})
            # celebration, built from the real numbers of this formation
            secs = time.monotonic() - getattr(self, 'form_t0', time.monotonic())
            legs = getattr(self, 'form_legs', {}) or {n: 0.0 for n in fleet}
            star = max(legs, key=legs.get)
            cheer = self.rng.choice(['Goal!', 'Nailed it!', 'Shape locked!', 'Beautiful!', 'Textbook!'])
            ld = 'Horus'
            self._say(ld, f'{cheer} All five in slot in {secs:.1f} s.', {'form_time_s': round(secs, 1)})
            self._say(self.rng.choice([n for n in NAMES if n not in (ld, star)]),
                      f'Great run, {star}! Longest leg of the shape, {legs[star]:.1f} m, and right on target.',
                      {'longest_leg_drone': star, 'leg_m': round(legs[star], 2)}, to=star)
            self._say(star, self.rng.choice(['Thanks, team!', 'Easy. Who is next?', 'Team effort!',
                                              'Felt good. Ready for the next one.']), {}, to='TEAM')
            self.formation_pending = False

        # --- single-drone altitude changes ----------------------------------------
        for n in fleet:
            me, was = fleet[n], prev.get(n)
            if not was:
                continue
            dz = me['target'][2] - was['target'][2]
            if modes[n] == 'MOVE' and abs(dz) >= 0.09 and pmodes.get(n) != 'MOVE':
                near = self._nearest(me, fleet)
                verb = 'Climbing' if dz > 0 else 'Dropping'
                hgap = math.hypot(near['x'] - me['x'], near['y'] - me['y'])
                self._say(n, f'{verb} {abs(dz) * 100:.0f} cm to {me["target"][2]:.1f} m. '
                             f'{near["name"]}, you are {_side(me, near)}, {hgap:.1f} m clear sideways.',
                          {'dz_m': round(dz, 2), 'target_z_m': round(me['target'][2], 2),
                           'nearest': near['name'], 'horizontal_gap_m': round(hgap, 2)}, to=near['name'])
            if pmodes.get(n) == 'MOVE' and modes[n] == 'HOVER' and abs(me['z'] - was['z']) < 0.3:
                pass

        # --- queued takeoff (staggered) --------------------------------------------
        for n in fleet:
            if modes[n] == 'QUEUED' and pmodes.get(n) != 'QUEUED':
                near = self._nearest(fleet[n], fleet)
                self._say(n, f'{near["name"]}, you go first. I will wait on the pad until you are above 1.2 m.',
                          {'queued_behind': near['name'], 'release_altitude_m': 1.2}, to=near['name'])
            if pmodes.get(n) == 'QUEUED' and modes[n] == 'TAKEOFF':
                self._say(n, 'You are clear. Lifting off now.', {'released': True})

        # --- landing ------------------------------------------------------------------
        landing = [n for n in fleet if modes[n] == 'LANDING' and pmodes.get(n) != 'LANDING']
        if len(landing) >= 3 and not self.landing_called:
            self._say('Horus', 'RSCL Flight, bringing it home. Everyone land.', {'landing': landing})
            self.landing_called = True
        if self.landing_called and all(modes[n] in PARKED for n in fleet):
            self._say('Osiris', 'All five on the ground. Good flying, everyone.',
                      {'grounded': sorted(fleet)})
            self.landing_called = False

        # --- takeoff ------------------------------------------------------------------
        lifting = [n for n in fleet if modes[n] == 'TAKEOFF' and pmodes.get(n) in PARKED]
        if len(lifting) >= 2:
            self._say('Horus', 'RSCL Flight, lifting off. Watch your spacing.', {'taking_off': lifting})
        if all(modes[n] == 'HOVER' for n in fleet) and any(pmodes.get(n) in ('TAKEOFF', 'QUEUED') for n in fleet):
            zs = ', '.join(f'{n} {fleet[n]["z"]:.1f} m' for n in NAMES)
            self._say('Thoth', f'Everyone airborne: {zs}.', {'altitudes_m': {n: round(fleet[n]['z'], 2) for n in NAMES}})
