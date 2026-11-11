#!/usr/bin/env python3
"""HU-MA'AT mission coordinator (simulation only; stdlib).

Scientific-honesty contract
---------------------------
* The five "agents" are DETERMINISTIC_AGENT_POLICY functions, one per drone. They are
  hand-written rules over real supervisor telemetry (GET /api/state). They do not
  learn, reason autonomously or use any language model.
* The coordinator is also a deterministic rule set. It turns one objective from a
  small closed grammar into an ordered list of ordinary supervisor commands.
* MA'AT validation is the flight supervisor's own read-only POST /api/plan/check,
  i.e. the same validation code the real command path runs. A plan is VALIDATED only
  if every step passes; it is never executable before that AND an explicit operator
  approval (--approve).
* Execution uses the existing authorised mission gateway (/api/mission/dispatch with
  token, operator_confirmed and planner HU-MISSION-COORD). Each step is re-checked
  against the live state just before dispatch and VERIFIED from telemetry before the
  next one. Deviations stop the mission and hold the fleet.
* Battery figures are a SIMULATED_ESTIMATE: the Gazebo models have no battery.

Arena frame: Gazebo ENU, +x = east, +y = north; |x|,|y| <= 3.35 m validated.

CLI
  python3 mission_coordinator.py plan "inspect north sector"            (never moves)
  python3 mission_coordinator.py plan "..." --snapshot state.json        (offline replay)
  python3 mission_coordinator.py run  "inspect north sector" --approve   (live, operator)
"""
import argparse, itertools, json, math, re, sys, time, uuid
import urllib.error, urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from formation_planner import slots  # noqa: E402  (stdlib-only, deterministic)
from mission_intents import SHAPES   # noqa: E402

NAMES = ('Horus', 'Ra', 'Thoth', 'Anubis', 'Osiris')
POLICY = 'DETERMINISTIC_AGENT_POLICY'
SAFETY = 'SAFETY_SUPERVISOR'
FACT = 'TELEMETRY_FACT'
PLANNER_TAG = 'HU-MISSION-COORD'
SUPERVISOR = 'http://127.0.0.1:8765'
TOKEN_FILE = Path.home() / 'hu-telepresence-gazebo' / 'festival' / 'hu_bridge_token'
LOG_DIR = Path.home() / 'hu-telepresence-gazebo' / 'logs' / 'missions'
CATALOG_FILE = HERE / 'formation_catalog.json'

ARENA_LIMIT = 3.35         # supervisor's validated arena bound
PLAN_LIMIT = 3.10          # coordinator keeps a 0.25 m margin inside it
MAX_MOVE = 2.0             # supervisor's per-command relative move limit
SECTOR_DIST = 1.6          # nominal sector centre distance from the arena centre
TAKEOFF_ALT = 1.7
LOW_BATTERY_PCT = 25.0
SECTORS = {'north': (0., 1.), 'south': (0., -1.), 'east': (1., 0.), 'west': (-1., 0.), 'center': (0., 0.)}
PARKED = ('READY', 'GROUNDED', 'WAITING')
AIRBORNE_OK = ('HOVER', 'FORMATION')
BUSY = ('TAKEOFF', 'MOVE', 'TURN', 'SPIN', 'LANDING', 'QUEUED')
ALT_SHAPES = ('v', 'line', 'wedge', 'arc', 'grid', 'column', 'inverted_v')
NUM = {'two': 2, 'three': 3, 'four': 4, 'five': 5, '2': 2, '3': 3, '4': 4, '5': 5}
VERIFY_TIMEOUT = {'takeoff': 45., 'formation': 45., 'move': 40., 'hover': 10., 'land': 60.}


class CoordinatorError(Exception):
    pass


def utc():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def load_catalog(path=CATALOG_FILE):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


# --------------------------------------------------------------------------- objective
def parse_objective(text):
    """Closed objective grammar. Returns a dict or raises CoordinatorError."""
    raw = str(text or '')
    s = ' '.join(re.sub(r'[,;!?]|\.(?!\d)', ' ', raw.lower()).split())
    if not s or len(s) > 200:
        raise CoordinatorError('Empty or over-long objective')
    cons = {'reserve': [], 'exclude': [], 'count': None, 'shape': None, 'offset_m': None}
    names = '|'.join(n.lower() for n in NAMES)

    def take(pattern, fn):
        nonlocal s
        for m in list(re.finditer(pattern, s)):
            fn(m)
        s = ' '.join(re.sub(pattern, ' ', s).split())
    take(r'\bkeep (' + names + r') (?:in|on|as) reserve\b', lambda m: cons['reserve'].append(m.group(1).title()))
    take(r'\b(?:without|exclude|excluding) (' + names + r')\b', lambda m: cons['exclude'].append(m.group(1).title()))
    take(r'\busing (two|three|four|five|[2-5]) drones\b|\buse (two|three|four|five|[2-5]) drones\b',
         lambda m: cons.__setitem__('count', NUM[m.group(1) or m.group(2)]))
    take(r'\boffset (\d+(?:\.\d+)?) ?(?:m|meters?|metres?)\b', lambda m: cons.__setitem__('offset_m', float(m.group(1))))
    take(r'\bin (?:an? )?([a-z_ ]+?) formation\b', lambda m: cons.__setitem__('shape', m.group(1).strip()))
    s = re.sub(r'^(?:please )', '', s)
    s = re.sub(r'(?: (?:and|with|please))+$', '', s).strip()
    m = re.fullmatch(r'inspect (?:the )?(north|south|east|west|center|centre)(?: sector)?', s)
    if m:
        kind, sector, shape = 'inspect', m.group(1).replace('centre', 'center'), cons['shape'] or 'v'
    elif re.fullmatch(r'survey (?:the )?arena', s):
        kind, sector, shape = 'survey', 'center', cons['shape'] or 'line'
    elif re.fullmatch(r'relay chain to (?:the )?(north|south|east|west)(?: sector)?', s):
        sector = re.fullmatch(r'relay chain to (?:the )?(\w+)(?: sector)?', s).group(1)
        kind, shape = 'relay', cons['shape'] or ('column' if sector in ('north', 'south') else 'relay_chain')
    else:
        m = re.fullmatch(r'form (?:an? )?([a-z_ ]+?)(?: formation| shape)? and hold', s)
        if not m:
            raise CoordinatorError('Objective outside the closed set: "inspect <north|south|east|west|center> sector", '
                                   '"survey the arena", "form <shape> and hold", "relay chain to <sector>" '
                                   '(+ "keep X in reserve", "use N drones", "without X", "in <shape> formation", "offset D m")')
        kind, sector, shape = 'hold', 'center', m.group(1).strip()
    shape = SHAPES.get(shape, shape.replace(' ', '_'))
    bad = [n for n in cons['reserve'] + cons['exclude'] if n not in NAMES]
    if bad:
        raise CoordinatorError('Unknown aircraft: ' + ', '.join(bad))
    if cons['offset_m'] is not None and kind not in ('inspect', 'relay'):
        raise CoordinatorError('An offset applies only to sector objectives')
    return {'kind': kind, 'sector': sector, 'shape': shape, 'constraints': cons, 'text': raw.strip()}


# --------------------------------------------------------------------------- telemetry
def normalize_state(state, sample_utc=None):
    """Supervisor /api/state -> {name: drone dict}. Only real telemetry fields are used."""
    drones = {d['name']: dict(d) for d in state.get('drones', []) if d.get('name') in NAMES}
    for n in NAMES:
        drones.setdefault(n, {'name': n, 'telemetry': False, 'mode': 'UNKNOWN', 'fault': False})
    return {'drones': drones, 'sample_utc': sample_utc or utc()}


class SimulatedBattery:
    """SIMULATED_ESTIMATE: Gazebo models have no battery. 100 % minus a fixed drain per
    minute of airborne time that THIS coordinator process has observed in telemetry."""
    LABEL = 'SIMULATED_ESTIMATE (no battery in the Gazebo model)'

    def __init__(self, drain_pct_per_min=2.0):
        self.drain = drain_pct_per_min
        self.airborne_s = {n: 0. for n in NAMES}
        self.last = None

    def observe(self, snap, t=None):
        t = time.monotonic() if t is None else t
        if self.last is not None:
            dt = min(2., max(0., t - self.last))
            for n, d in snap['drones'].items():
                if d.get('telemetry') and d.get('z', 0) > .3:
                    self.airborne_s[n] += dt
        self.last = t

    def pct(self, name, drone):
        if 'sim_battery_pct' in drone:          # injected by tests / a future sim model
            return float(drone['sim_battery_pct'])
        return round(max(0., 100. - self.drain * self.airborne_s[name] / 60.), 1)


class AgentPolicy:
    """DETERMINISTIC_AGENT_POLICY for one drone: reads its own telemetry and the shared
    fleet snapshot, never anything else; returns a structured bid."""
    label = POLICY

    def __init__(self, name, battery):
        self.name = name
        self.battery = battery

    def bid(self, snap, ctx):
        d = snap['drones'][self.name]
        t = snap['sample_utc']
        ev = lambda field: {'field': f'{self.name}.{field}', 'value': d.get(field), 'sample_utc': t,
                            'route': 'GET /api/state'}
        bid = {'agent': self.name, 'policy': POLICY, 'available': 'YES', 'reasons': [], 'position': None,
               'mode': d.get('mode'), 'fault': d.get('fault'), 'telemetry_fresh': bool(d.get('telemetry')),
               'sim_battery_pct': None, 'battery_label': SimulatedBattery.LABEL, 'distance_to_sector_m': None,
               'preferred_slot': None, 'evidence': [ev('telemetry'), ev('mode'), ev('fault')]}
        if not d.get('telemetry'):
            bid['available'] = 'UNKNOWN'
            bid['reasons'].append('telemetry stale: no fresh odometry, so position/mode are UNKNOWN')
            return bid
        bid['position'] = [round(d['x'], 2), round(d['y'], 2), round(d['z'], 2)]
        bid['evidence'] += [ev('x'), ev('y'), ev('z')]
        sx, sy = ctx['sector_point']
        bid['distance_to_sector_m'] = round(math.hypot(d['x'] - sx, d['y'] - sy), 2)
        bid['sim_battery_pct'] = self.battery.pct(self.name, d)
        if d.get('fault') or d.get('mode') == 'FAULT':
            bid['available'] = 'NO'
            bid['reasons'].append('simulated command-link fault active (fault=true)')
        if d.get('mode') in BUSY:
            bid['available'] = 'NO'
            bid['reasons'].append(f'busy: mode {d.get("mode")}')
        elif d.get('mode') not in PARKED + AIRBORNE_OK + ('FAULT',):
            bid['available'] = 'NO'
            bid['reasons'].append(f'mode {d.get("mode")} not plannable')
        if bid['sim_battery_pct'] < LOW_BATTERY_PCT:
            bid['available'] = 'NO'
            bid['reasons'].append(f'low SIMULATED battery estimate {bid["sim_battery_pct"]} %')
        if self.name in ctx['reserve']:
            bid['available'] = 'NO'
            bid['reasons'].append('held in reserve by operator constraint')
        if self.name in ctx['exclude']:
            bid['available'] = 'NO'
            bid['reasons'].append('excluded by operator constraint')
        if bid['available'] == 'YES':
            bid['reasons'].append(f'fresh telemetry, mode {d.get("mode")}, no fault')
        return bid

    def claim_slot(self, snap, centre_slots, taken):
        d = snap['drones'][self.name]
        free = [(round(math.hypot(d['x'] - x, d['y'] - y), 2), k) for k, (x, y) in enumerate(centre_slots) if k not in taken]
        free.sort()
        return free[0] if free else (None, None)


def negotiate(agents, team, snap, centre_slots):
    """Bounded deterministic slot negotiation: each agent claims its nearest free slot;
    on a clash the farther agent yields (ties: fleet order) and re-claims. At most
    len(team) rounds. Returns ({name: slot}, messages)."""
    agreed, msgs = {}, []
    pending = list(team)
    for _ in range(len(team) + 1):
        if not pending:
            break
        claims = {}
        for n in pending:
            dist, k = agents[n].claim_slot(snap, centre_slots, set(agreed.values()))
            claims.setdefault(k, []).append((dist, NAMES.index(n), n))
        pending = []
        for k in sorted(claims):
            group = sorted(claims[k])
            winner = group[0]
            agreed[winner[2]] = k
            msgs.append((winner[2], f'I claim slot {k} ({winner[0]:.2f} m from my position).',
                         [{'field': f'{winner[2]}.x/y', 'value': [snap['drones'][winner[2]]['x'], snap['drones'][winner[2]]['y']],
                           'sample_utc': snap['sample_utc'], 'route': 'GET /api/state'}]))
            for dist, _, loser in group[1:]:
                why = (f'{winner[2]} is {dist - winner[0]:.2f} m closer ({winner[0]:.2f} m vs my {dist:.2f} m)'
                       if dist - winner[0] >= .005 else f'tie at {dist:.2f} m, fixed fleet order decides')
                msgs.append((loser, f'I yield slot {k} to {winner[2]}: {why}.', []))
                pending.append(loser)
    return agreed, msgs


# --------------------------------------------------------------------------- supervisor clients
class SupervisorClient:
    """Live supervisor (port 8765). plan_check is read-only; dispatch moves aircraft."""
    route_name = 'LIVE'

    def __init__(self, base=SUPERVISOR, token_file=TOKEN_FILE, timeout=10.):
        self.base, self.token_file, self.timeout = base, Path(token_file), timeout

    def _call(self, path, body=None, headers=None):
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json', **(headers or {})},
                                     method='GET' if body is None else 'POST')
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b'{}')
            except ValueError:
                return e.code, {'ok': False, 'error': f'HTTP {e.code}'}
        except (urllib.error.URLError, OSError) as e:
            raise CoordinatorError(f'Supervisor unreachable at {self.base}: {e}')

    def state(self):
        st, data = self._call('/api/state')
        if st != 200:
            raise CoordinatorError(f'GET /api/state HTTP {st}')
        return data

    def plan_check(self, steps):
        st, data = self._call('/api/plan/check', {'steps': steps})
        if st == 404:
            raise CoordinatorError('Supervisor has no /api/plan/check (restart it with the updated control_server_hu.py)')
        if st != 200:
            raise CoordinatorError(f'plan check HTTP {st}: {data.get("error")}')
        return data

    def dispatch(self, step):
        body = {'action': step['action'], 'targets': step['targets'], 'params': step.get('params', {}),
                'operator_confirmed': True, 'planner': PLANNER_TAG}
        return self._call('/api/mission/dispatch', body, {'X-HU-Token': self.token_file.read_text().strip()})

    def emergency_hover(self):
        body = {'action': 'emergency_hover', 'targets': list(NAMES), 'params': {}, 'symbolic_emergency': True, 'confidence': 1}
        return self._call('/api/hu/dispatch', body, {'X-HU-Token': self.token_file.read_text().strip()})


class OfflineSupervisor:
    """OFFLINE_REPLAY: runs the supervisor's own plan_check code in-process on a recorded
    /api/state snapshot (velocities assumed 0). Optionally simulates dispatch by applying
    the real command() to the copy and settling instantly (tests only)."""
    route_name = 'OFFLINE_REPLAY'

    def __init__(self, state, simulate_dispatch=False):
        import control_server_hu as ctrl
        self.ctrl = ctrl
        now = time.monotonic()
        drones, fresh = {}, set()
        for d in state['drones']:
            t = d.get('target') or [d['x'], d['y'], d['z']]
            drones[d['name']] = ctrl.Drone(d['name'], x=d['x'], y=d['y'], z=d['z'], mode=d['mode'], fault=d['fault'],
                                           init=True, last_seen=now, target_x=t[0], target_y=t[1], target_z=t[2],
                                           yaw=math.radians(d.get('yaw', 0)), target_yaw=math.radians(d.get('yaw', 0)))
            if d.get('telemetry'):
                fresh.add(d['name'])
        self.fleet = ctrl._PlanScratch(drones, fresh)
        self.fleet.event = lambda *a, **k: None
        self.simulate = simulate_dispatch
        self.dispatched = []
        self.extra = {n: dict(d) for n, d in ((d['name'], d) for d in state['drones'])}

    def state(self):
        out = []
        for n in NAMES:
            d = self.fleet.dr[n]
            item = {'name': n, 'x': round(d.x, 2), 'y': round(d.y, 2), 'z': round(d.z, 2), 'mode': d.mode, 'fault': d.fault,
                    'target': [round(d.target_x, 2), round(d.target_y, 2), round(d.target_z, 2)],
                    'yaw': round(math.degrees(d.yaw), 1), 'telemetry': n in self.fleet._fresh}
            if 'sim_battery_pct' in self.extra.get(n, {}):
                item['sim_battery_pct'] = self.extra[n]['sim_battery_pct']
            out.append(item)
        return {'drones': out}

    def plan_check(self, steps):
        return self.ctrl.Fleet.plan_check(self.fleet, steps)

    def dispatch(self, step):
        if not self.simulate:
            raise CoordinatorError('OFFLINE_REPLAY cannot dispatch')
        self.dispatched.append(step)
        try:
            res = self.fleet.command(step['action'], list(step['targets']), dict(step.get('params', {})), 'SIM')
        except ValueError as e:
            return 400, {'ok': False, 'error': str(e)}
        self.fleet.settle()
        return 200, res

    def emergency_hover(self):
        self.dispatched.append({'action': 'emergency_hover'})
        return 200, self.fleet.command('emergency_hover')


# --------------------------------------------------------------------------- mission log
class MissionLog:
    def __init__(self, mission_id, session_id, directory=LOG_DIR, enabled=True):
        self.mission_id, self.session_id = mission_id, session_id
        self.path = Path(directory) / f'{mission_id}.jsonl'
        self.enabled = enabled
        if enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, kind, source, route, targets=None, params=None, safety_result=None,
               dispatch_outcome=None, observed_outcome=None, **extra):
        entry = {'session_id': self.session_id, 'mission_id': self.mission_id, 'utc': utc(),
                 'monotonic': round(time.monotonic(), 3), 'kind': kind, 'source': source, 'route': route,
                 'targets': targets or [], 'params': params or {}, 'safety_result': safety_result,
                 'dispatch_outcome': dispatch_outcome, 'observed_outcome': observed_outcome, **extra}
        if self.enabled:
            with self.path.open('a', encoding='utf-8') as f:
                f.write(json.dumps(entry, default=str) + '\n')
        return entry


# --------------------------------------------------------------------------- coordinator
class Coordinator:
    def __init__(self, client, catalog=None, battery=None, log_dir=LOG_DIR, log_enabled=True, session_id=None):
        self.client = client
        self.catalog = load_catalog() if catalog is None else catalog
        self.battery = battery or SimulatedBattery()
        self.agents = {n: AgentPolicy(n, self.battery) for n in NAMES}
        self.log_dir, self.log_enabled = log_dir, log_enabled
        self.session_id = session_id or 'S' + uuid.uuid4().hex[:10]

    # -- telemetry
    def snapshot(self):
        snap = normalize_state(self.client.state())
        self.battery.observe(snap)
        return snap

    def shape_status(self, shape):
        entry = self.catalog.get(shape)
        if not entry:
            return None, f'"{shape}" is not in the formation catalog'
        if entry.get('status') != 'VALIDATED':
            return None, f'"{shape}" is {entry.get("status")} in the catalog, not VALIDATED live'
        return entry.get('alias_of') or shape, None

    # -- planning
    def plan(self, objective, end='land', _alternative_depth=0, mission_id=None):
        obj = parse_objective(objective) if isinstance(objective, str) else objective
        mission_id = mission_id or 'M' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:6]
        log = MissionLog(mission_id, self.session_id, self.log_dir, self.log_enabled and _alternative_depth == 0)
        tr, claims = [], []

        def say(sender, role, text, source, evidence=None):
            tr.append({'sender': sender, 'role': role, 'text': text, 'source': source, 'evidence': evidence or []})

        def claim(text, evidence):
            cid = f'C{len(claims) + 1}'
            claims.append({'id': cid, 'claim': text, 'evidence': evidence})
            return cid
        plan = {'mission_id': mission_id, 'session_id': self.session_id, 'objective': obj, 'created_utc': utc(),
                'policy_label': POLICY, 'route': self.client.route_name, 'end': end, 'status': 'NOT_PLANNABLE',
                'executable': False, 'requires_operator_approval': True, 'transcript': tr, 'steps': [],
                'alternatives': [], 'maat': None}
        say('Coordinator', 'COORDINATOR', f'Objective received: "{obj["text"]}" -> {obj["kind"]} '
            f'(sector {obj["sector"]}, shape {obj["shape"]}). Deterministic rules, not learned reasoning.', POLICY)
        shape, err = self.shape_status(obj['shape'])
        if err:
            say('Coordinator', 'COORDINATOR', 'Refused before MA\'AT: ' + err, POLICY)
            plan['reasons'] = [err]
            return self._finish(plan, log, claims)
        plan['shape'] = shape
        snap = self.snapshot()
        plan['snapshot_utc'] = snap['sample_utc']
        dx, dy = SECTORS[obj['sector']]
        ctx = {'sector_point': (dx * SECTOR_DIST, dy * SECTOR_DIST), 'reserve': set(obj['constraints']['reserve']),
               'exclude': set(obj['constraints']['exclude'])}
        bids = {n: self.agents[n].bid(snap, ctx) for n in NAMES}
        plan['bids'] = bids
        for n in NAMES:
            b = bids[n]
            d = snap['drones'][n]
            if b['telemetry_fresh']:
                say(n, 'DRONE_AGENT', f'Telemetry: mode {d["mode"]}, fault {d["fault"]}, position '
                    f'({d["x"]:.2f}, {d["y"]:.2f}, {d["z"]:.2f}) m.', FACT, b['evidence'])
            dist = f', {b["distance_to_sector_m"]:.2f} m from the {obj["sector"]} sector point' if b['distance_to_sector_m'] is not None else ''
            batt = f', SIMULATED battery estimate {b["sim_battery_pct"]} %' if b['sim_battery_pct'] is not None else ''
            say(n, 'DRONE_AGENT', f'Available: {b["available"]} - {"; ".join(b["reasons"])}{dist}{batt}.', POLICY, b['evidence'])
            claim(f'{n} available={b["available"]}', b['evidence'])
        avail = [n for n in NAMES if bids[n]['available'] == 'YES']
        count = obj['constraints']['count']
        if count is not None and count > len(avail):
            reason = f'{count} drones requested but only {len(avail)} available ({", ".join(avail) or "none"})'
            say('Coordinator', 'COORDINATOR', 'Cannot plan: ' + reason, POLICY)
            plan['reasons'] = [reason]
            return self._finish(plan, log, claims)
        team = sorted(avail, key=lambda n: (bids[n]['distance_to_sector_m'], NAMES.index(n)))[:count or len(avail)]
        team = [n for n in NAMES if n in team]
        if len(team) < 2:
            reason = f'At least two available drones needed; available: {", ".join(avail) or "none"}'
            say('Coordinator', 'COORDINATOR', 'Cannot plan: ' + reason, POLICY)
            plan['reasons'] = [reason]
            return self._finish(plan, log, claims)
        others = [n for n in NAMES if n not in team]
        plan['participants'], plan['non_participants'] = team, others
        if count is not None and count < len(avail):
            say('Coordinator', 'COORDINATOR', f'Selected the {count} available drones closest to the sector point: '
                f'{", ".join(team)}.', POLICY)
        try:
            centre = slots(shape, len(team))
        except ValueError as e:
            plan['reasons'] = [str(e)]
            say('Coordinator', 'COORDINATOR', 'Cannot plan: ' + str(e), POLICY)
            return self._finish(plan, log, claims)
        say('Coordinator', 'COORDINATOR', f'Team {", ".join(team)}; {len(others) and ("non-participants " + ", ".join(others) + " stay where they are as obstacles") or "no reserve"}. '
            f'Formation {shape} with {len(team)} slots is formed at the arena centre first.', POLICY)
        prefs, msgs = negotiate(self.agents, team, snap, centre)
        for sender, text, ev in msgs:
            say(sender, 'DRONE_AGENT', text, POLICY, ev)
        plan['agent_slot_preferences'] = prefs
        # -- steps phase 1 (takeoff + formation), MA'AT check to learn the formation goals
        steps = []
        grounded = [n for n in team if snap['drones'][n]['mode'] in PARKED]
        if grounded:
            steps.append({'action': 'takeoff', 'targets': grounded, 'params': {'altitude': TAKEOFF_ALT},
                          'label': 'take off (supervisor staggers close pairs automatically)'})
        steps.append({'action': 'formation', 'targets': team, 'params': {'shape': shape},
                      'label': f'form {shape} at the arena centre'})
        try:
            check1 = self.client.plan_check(self._wire(steps))
        except CoordinatorError as e:
            return self._maat_unavailable(plan, log, claims, say, str(e))
        offset = (0., 0.)
        if check1['ok'] and obj['kind'] in ('inspect', 'relay', 'survey'):
            goals = check1['steps'][-1].get('planned_goals', {})
            offset = self._sector_offset(obj, goals)
        elif obj['constraints']['offset_m'] is not None:
            offset = (dx * obj['constraints']['offset_m'], dy * obj['constraints']['offset_m'])
        plan['offset_m'] = [round(offset[0], 2), round(offset[1], 2)]
        # -- full step list
        if obj['kind'] == 'survey':
            amp = offset[1]
            steps += [self._move(team, 0, -amp, 'sweep to the south edge'), self._hover(team, 6, 'survey dwell (south)'),
                      self._move(team, 0, 2 * amp, 'sweep to the north edge'), self._hover(team, 6, 'survey dwell (north)'),
                      self._move(team, 0, -amp, 'return to the arena centre')]
        else:
            if offset != (0., 0.):
                steps.append(self._move(team, offset[0], offset[1], f'group move into the {obj["sector"]} sector'))
            steps.append(self._hover(team, 8 if obj['kind'] == 'inspect' else 6,
                                     {'inspect': 'inspect / hold dwell', 'relay': 'relay chain hold', 'hold': 'hold formation'}[obj['kind']]))
            if offset != (0., 0.):
                steps.append(self._move(team, -offset[0], -offset[1], 'return formation to the arena centre'))
        if end == 'land':
            steps.append({'action': 'land', 'targets': team, 'params': {}, 'label': 'land participants'})
        for i, s in enumerate(steps):
            s['id'] = f'S{i + 1}'
        plan['steps'] = steps
        say('Coordinator', 'COORDINATOR', 'Proposed plan: ' + ' -> '.join(f'{s["id"]} {s["label"]}' for s in steps) + '.', POLICY)
        try:
            check = self.client.plan_check(self._wire(steps))
        except CoordinatorError as e:
            return self._maat_unavailable(plan, log, claims, say, str(e))
        plan['maat'] = check
        for s, r in zip(steps, check['steps']):
            s['check'] = {k: r.get(k) for k in ('ok', 'checked', 'predicted', 'reason', 'planned_goals', 'planned_min_sep',
                                                  'staggered_wait_for', 'formation_offset_m')}
        tel = check.get('telemetry', {})
        ages = [{'field': f'{n}.age_s', 'value': tel[n].get('age_s'), 'sample_utc': check.get('checked_utc'),
                 'route': 'POST /api/plan/check'} for n in tel]
        claim('MA\'AT checked against fresh telemetry', ages)
        # assignment (authoritative = supervisor's planned formation goals)
        fgoals = next((r.get('planned_goals') for s, r in zip(steps, check['steps']) if s['action'] == 'formation' and r.get('ok')), None)
        plan['assignment'] = self._assignment(team, others, bids, prefs, centre, fgoals, offset, obj)
        for n, a in plan['assignment'].items():
            if a.get('supervisor_slot') is not None and a['supervisor_slot'] != a.get('agent_preferred_slot'):
                say('Coordinator', 'COORDINATOR', f'{n}: agents agreed slot {a["agent_preferred_slot"]}, the supervisor\'s '
                    f'separation-checked minimum-distance assignment gives slot {a["supervisor_slot"]}; the supervisor is authoritative.', POLICY)
        plan['graphs'] = self._graphs(plan, check, claims)
        if check['ok']:
            plan['status'] = 'VALIDATED'
            plan['executable'] = False   # becomes executable only with explicit operator approval at run time
            mins = [r.get('planned_min_sep') for r in check['steps'] if r.get('planned_min_sep') is not None]
            close = [e for e in plan['graphs']['proximity']['edges'] if e['below_1m']]
            say('MA\'AT', 'MAAT', f'VALIDATED: all {len(steps)} steps accepted by the supervisor command path '
                f'(step 1 exact, later steps on predicted state). Planned minimum separation {min(mins):.2f} m'
                + (f'; {len(close)} pair(s) planned below 1.0 m but above the supervisor limits: '
                   + ', '.join(f'{e["a"]}-{e["b"]} {e["planned_min_sep_m"]:.2f} m at {e["step"]}' for e in close) if close else '')
                + '. Awaiting explicit operator approval; nothing has moved.', SAFETY, ages)
        else:
            bad = next(r for r in check['steps'] if not r['ok'])
            plan['status'] = 'REJECTED'
            plan['reasons'] = [f'S{bad["index"] + 1} {bad.get("action")}: {bad["reason"]}']
            say('MA\'AT', 'MAAT', f'REJECTED at S{bad["index"] + 1} ({bad.get("action")}'
                f'{", predicted state" if bad.get("predicted") else ""}): {bad["reason"]}', SAFETY, ages)
            if _alternative_depth == 0:
                plan['alternatives'] = self._alternatives(obj, end, bad, plan, say)
        return self._finish(plan, log, claims)

    def _maat_unavailable(self, plan, log, claims, say, err):
        plan['status'] = 'REJECTED'
        plan['reasons'] = ['MA\'AT plan check unavailable: ' + err]
        say('MA\'AT', 'MAAT', 'REJECTED (fail closed): plan check unavailable - ' + err, SAFETY)
        return self._finish(plan, log, claims)

    def _finish(self, plan, log, claims):
        plan.setdefault('graphs', self._graphs(plan, plan.get('maat'), claims))
        log.record('PLAN', 'COORDINATOR', (f'{self.client.route_name}: POST /api/plan/check (read-only)'
                                           if plan.get('maat') else 'none'),
                   plan.get('participants', []), {'objective': plan['objective'], 'steps': [
                       {k: s.get(k) for k in ('id', 'action', 'targets', 'params', 'label')} for s in plan['steps']]},
                   safety_result={'status': plan['status'], 'reasons': plan.get('reasons', [])},
                   transcript=plan['transcript'], graphs=plan['graphs'],
                   alternatives=[{'objective': a['objective']['text'], 'status': a['status']} for a in plan['alternatives']])
        plan['log_path'] = str(log.path) if log.enabled else None
        return plan

    @staticmethod
    def _wire(steps):
        return [{'action': s['action'], 'targets': s['targets'], 'params': s.get('params', {})} for s in steps]

    @staticmethod
    def _move(team, dx, dy, label):
        return {'action': 'move', 'targets': list(team), 'params': {'dx': round(dx, 2) + 0., 'dy': round(dy, 2) + 0.}, 'label': label}

    @staticmethod
    def _hover(team, dwell, label):
        return {'action': 'hover', 'targets': list(team), 'params': {}, 'dwell_s': dwell, 'label': label}

    @staticmethod
    def _sector_offset(obj, goals):
        """Largest offset toward the sector (<= nominal, <= MAX_MOVE) keeping every
        planned slot inside PLAN_LIMIT. An explicit operator offset is NOT clamped:
        MA'AT must judge it."""
        dx, dy = SECTORS[obj['sector']]
        if obj['kind'] == 'survey':
            return (0., 1.0)
        if obj['constraints']['offset_m'] is not None:
            o = obj['constraints']['offset_m']
            return (dx * o, dy * o)
        if (dx, dy) == (0., 0.) or not goals:
            return (0., 0.)
        ext = max(g[0] * dx + g[1] * dy for g in goals.values())
        o = max(0., min(SECTOR_DIST, MAX_MOVE, PLAN_LIMIT - ext))
        o = math.floor(o * 20) / 20
        return (dx * o, dy * o)

    def _assignment(self, team, others, bids, prefs, centre, fgoals, offset, obj):
        dx, dy = SECTORS[obj['sector']]
        out = {}
        lead = max(range(len(centre)), key=lambda k: centre[k][0] * dx + centre[k][1] * dy) if (dx, dy) != (0, 0) else None
        shift = fgoals_offset(fgoals, centre) if fgoals else (0., 0.)
        for n in team:
            sup = None
            if fgoals and n in fgoals:
                g = fgoals[n]
                sup = min(range(len(centre)), key=lambda k: math.hypot(centre[k][0] + shift[0] - g[0], centre[k][1] + shift[1] - g[1]))
            k = sup if sup is not None else prefs.get(n)
            role = ('relay-' + str(k)) if obj['kind'] == 'relay' else ('lead-observer' if k == lead else f'slot-{k}')
            out[n] = {'role': role, 'agent_preferred_slot': prefs.get(n), 'supervisor_slot': sup,
                      'formation_goal': fgoals.get(n) if fgoals else None,
                      'sector_goal': [round(fgoals[n][0] + offset[0], 2), round(fgoals[n][1] + offset[1], 2), fgoals[n][2]] if fgoals else None}
        for n in others:
            why = ' '.join(bids[n]['reasons'])
            role = ('not-selected' if bids[n]['available'] == 'YES' else 'reserve' if 'reserve' in why
                    else 'excluded' if 'excluded by operator' in why else 'unavailable')
            out[n] = {'role': role, 'reasons': bids[n]['reasons']}
        return out

    def _graphs(self, plan, check, claims):
        steps = plan.get('steps', [])
        dag = {'nodes': [{'id': s['id'], 'action': s['action'], 'label': s.get('label'), 'targets': s['targets']} for s in steps],
               'edges': [{'from': a['id'], 'to': b['id'], 'type': 'requires_verified_completion'} for a, b in zip(steps, steps[1:])]}
        asg = plan.get('assignment', {})
        assignment = {'nodes': [{'id': n, 'type': 'drone'} for n in NAMES] +
                      [{'id': r, 'type': 'role'} for r in sorted({a['role'] for a in asg.values()})],
                      'edges': [{'from': n, 'to': a['role'], 'slot': a.get('supervisor_slot'),
                                 'sector_goal': a.get('sector_goal')} for n, a in asg.items()]}
        prox = {}
        if check:
            for r in check.get('steps', []):
                for pair, d in (r.get('pairwise_min_sep') or {}).items():
                    if pair not in prox or d < prox[pair]['planned_min_sep_m']:
                        prox[pair] = {'planned_min_sep_m': d, 'step': f'S{r["index"] + 1}'}
        proximity = {'edges': [{'a': p.split('|')[0], 'b': p.split('|')[1], **v, 'below_1m': v['planned_min_sep_m'] < 1.0}
                               for p, v in sorted(prox.items())],
                     'note': 'Straight-line sampled setpoint trajectories (predicted); pairs parked on the ground throughout are omitted.'}
        prov = {'nodes': [{'id': c['id'], 'claim': c['claim']} for c in claims],
                'edges': [{'from': c['id'], 'to': e['field'], 'value': e['value'], 'sample_utc': e['sample_utc'],
                           'route': e['route']} for c in claims for e in c['evidence']]}
        return {'mission_dag': dag, 'assignment': assignment, 'proximity': proximity, 'provenance': prov}

    def _alternatives(self, obj, end, bad, plan, say):
        """Bounded search (<= 6 re-plans, each checked by MA'AT) for a safe variant."""
        reason = bad.get('reason', '')
        cands = []
        c = obj['constraints']
        excl = [({**c, 'exclude': c['exclude'] + [n]}, obj['shape'], f'exclude {n}') for n in NAMES
                if re.search(r'\b' + n + r'\b', reason) and n in plan.get('participants', []) and n not in c['exclude']]
        drone_problem = re.search(r'fault|telemetry|must|settled|maneuvering|is [A-Z]+;', reason)
        if drone_problem:
            cands += excl
        if bad.get('action') == 'move' or re.search(r'arena|bounds|Maximum movement|within 1\.0 m', reason):
            if c['offset_m'] is not None:
                cands.append(({**c, 'offset_m': None}, obj['shape'], 'automatic in-bounds offset'))
            cur = math.hypot(*plan.get('offset_m', [0, 0]))
            for f in (.6, .3):
                if cur * f >= .2 and obj['kind'] in ('inspect', 'relay'):
                    cands.append(({**c, 'offset_m': round(cur * f, 2)}, obj['shape'], f'smaller offset {cur * f:.2f} m'))
        if bad.get('action') in ('formation', 'move'):
            for sh in ALT_SHAPES:
                if sh != plan.get('shape'):
                    cands.append(({**c, 'offset_m': None}, sh, f'shape {sh}'))
        if not drone_problem:
            cands += excl
        tried = []
        for cons, shape, why in cands[:6]:
            alt_obj = {**obj, 'constraints': cons, 'shape': shape,
                       'text': obj['text'] + f' [MA\'AT alternative: {why}]'}
            alt = self.plan(alt_obj, end=end, _alternative_depth=1, mission_id=plan['mission_id'] + '-alt' + str(len(tried) + 1))
            tried.append(alt)
            if alt['status'] == 'VALIDATED':
                say('MA\'AT', 'MAAT', f'Safe alternative found ({why}; shape {alt["shape"]}, offset {alt["offset_m"]} m, '
                    f'team {", ".join(alt["participants"])}): VALIDATED, '
                    f'{" -> ".join(s["id"] + " " + s["label"] for s in alt["steps"])}. It needs its own operator approval.', SAFETY)
                return [alt]
        say('MA\'AT', 'MAAT', f'No safe alternative found in {len(tried)} bounded attempts; hold or land and replan.', SAFETY)
        return tried

    # -- execution
    def execute(self, plan, approve=False, poll=.25, timeouts=None, sleep=time.sleep, clock=time.monotonic):
        log = MissionLog(plan['mission_id'], self.session_id, self.log_dir, self.log_enabled)
        report = {'mission_id': plan['mission_id'], 'status': 'NOT_STARTED', 'steps': [], 'replanning_options': []}
        if approve is not True:
            log.record('EXECUTION_REFUSED', 'COORDINATOR', 'none', safety_result='operator approval missing')
            report['reason'] = 'Explicit operator approval (--approve) is required; nothing was dispatched.'
            return report
        if plan.get('status') != 'VALIDATED':
            log.record('EXECUTION_REFUSED', 'COORDINATOR', 'none', safety_result=plan.get('status'))
            report['reason'] = f'Plan status is {plan.get("status")}; only a MA\'AT-VALIDATED plan can run.'
            return report
        timeouts = {**VERIFY_TIMEOUT, **(timeouts or {})}
        log.record('OPERATOR_APPROVED', 'OPERATOR', 'CLI --approve', plan.get('participants'), safety_result='VALIDATED')
        report['status'] = 'RUNNING'
        steps = plan['steps']
        for i, step in enumerate(steps):
            rec = {'id': step['id'], 'action': step['action'], 'targets': step['targets'], 'params': step.get('params', {})}
            report['steps'].append(rec)
            t_pre = clock()
            while True:
                try:
                    pre = self.client.plan_check(self._wire(steps[i:]))
                except CoordinatorError as e:
                    pre = {'ok': False, 'steps': [{'ok': False, 'reason': str(e)}]}
                first = pre['steps'][0]
                # Only "still settling" refusals are retried (bounded); anything else stops the mission.
                if first['ok'] or not re.search(r'settled|maneuvering|wait for stable', str(first.get('reason'))) \
                        or clock() - t_pre > 8.:
                    break
                sleep(.5)
            rec['precheck'] = {'ok': first['ok'], 'reason': first.get('reason'), 'planned_min_sep': first.get('planned_min_sep')}
            log.record('PRECHECK', 'MAAT', 'POST /api/plan/check', step['targets'], step.get('params'),
                       safety_result=rec['precheck'], step=step['id'])
            if not first['ok']:
                rec['result'] = 'FAIL'
                return self._abort(report, log, plan, step, 'MA\'AT re-check failed before dispatch: ' + str(first.get('reason')), dispatched=False)
            code, res = self.client.dispatch(step)
            rec['dispatch'] = {'http': code, 'ok': res.get('ok'), 'error': res.get('error')}
            log.record('DISPATCH', 'COORDINATOR', f'POST /api/mission/dispatch planner={PLANNER_TAG}', step['targets'],
                       step.get('params'), safety_result=rec['precheck'], dispatch_outcome=rec['dispatch'], step=step['id'])
            if code != 200 or res.get('ok') is not True:
                rec['result'] = 'FAIL'
                return self._abort(report, log, plan, step, f'dispatch refused (HTTP {code}): {res.get("error")}', dispatched=False)
            obs = self._verify(step, first.get('planned_goals') or {}, timeouts[step['action']], poll, sleep, clock)
            rec['observed'] = obs
            rec['result'] = 'PASS' if obs['ok'] else 'FAIL'
            log.record('VERIFY', 'TELEMETRY', 'GET /api/state', step['targets'], step.get('params'),
                       dispatch_outcome=rec['dispatch'], observed_outcome=obs, step=step['id'])
            if not obs['ok']:
                return self._abort(report, log, plan, step, obs['reason'], dispatched=True)
        report['status'] = 'COMPLETED'
        log.record('MISSION_COMPLETED', 'COORDINATOR', 'none', plan.get('participants'))
        return report

    def _verify(self, step, planned, timeout, poll, sleep, clock):
        t0 = clock()
        tg = step['targets']
        min_sep = float('inf')
        last = None
        stable = 0
        while True:
            snap = self.snapshot()
            D = snap['drones']
            last = {n: {k: D[n].get(k) for k in ('x', 'y', 'z', 'mode', 'fault', 'telemetry', 'target')} for n in tg}
            air = [D[n] for n in NAMES if D[n].get('telemetry') and D[n].get('z', 0) > .3]
            for a, b in itertools.combinations(air, 2):
                min_sep = min(min_sep, math.dist((a['x'], a['y'], a['z']), (b['x'], b['y'], b['z'])))
            base = {'samples_until': snap['sample_utc'], 'elapsed_s': round(clock() - t0, 2), 'telemetry': last,
                    'observed_min_sep_m': None if min_sep == float('inf') else round(min_sep, 3)}
            bad = [n for n in tg if not D[n].get('telemetry') or D[n].get('fault') or D[n].get('mode') == 'FAULT']
            if bad:
                return {**base, 'ok': False, 'reason': 'telemetry lost or fault on ' + ', '.join(bad)}
            if min_sep < .82:
                return {**base, 'ok': False, 'reason': f'observed separation {min_sep:.2f} m below the 0.82 m watchdog'}
            dev = [n for n in tg if step['action'] in ('formation', 'move') and n in planned and D[n].get('target')
                   and math.dist(D[n]['target'], planned[n]) > .35]
            if dev:
                return {**base, 'ok': False, 'reason': 'live setpoint deviates from the checked plan (possible SEPARATION_HOLD): ' + ', '.join(dev)}
            stable = stable + 1 if self._done(step, D, planned) else 0
            if stable >= 3:          # three consecutive samples, so the next step sees a settled fleet
                break
            if clock() - t0 > timeout:
                return {**base, 'ok': False, 'reason': f'timeout after {timeout:.0f} s waiting for {step["action"]} completion'}
            sleep(poll)
        dwell = float(step.get('dwell_s', 0) or 0)
        if dwell:
            t1 = clock()
            anchor = {n: (D[n]['x'], D[n]['y'], D[n]['z']) for n in tg}
            drift = 0.
            while clock() - t1 < dwell:
                sleep(poll)
                D = self.snapshot()['drones']
                bad = [n for n in tg if not D[n].get('telemetry') or D[n].get('fault') or D[n].get('mode') != 'HOVER']
                drift = max([drift] + [math.dist(anchor[n], (D[n]['x'], D[n]['y'], D[n]['z'])) for n in tg])
                if bad or drift > .3:
                    return {**base, 'ok': False, 'reason': f'dwell broken: {", ".join(bad) or "drift %.2f m" % drift}'}
            base['dwell_s'] = dwell
            base['dwell_max_drift_m'] = round(drift, 3)
        return {**base, 'ok': True, 'reason': 'verified from telemetry'}

    @staticmethod
    def _done(step, D, planned):
        tg, a = step['targets'], step['action']
        near = lambda n, tol: math.dist((D[n]['x'], D[n]['y'], D[n]['z']), tuple(D[n]['target'])) < tol
        if a == 'takeoff':
            return all(D[n]['mode'] == 'HOVER' and D[n]['z'] > .8 and (n not in planned or abs(D[n]['z'] - planned[n][2]) < .2) for n in tg)
        if a == 'formation':
            return all(D[n]['mode'] == 'FORMATION' and near(n, .10) for n in tg)
        if a == 'move':
            return all(D[n]['mode'] == 'HOVER' and near(n, .10) for n in tg)
        if a == 'hover':
            return all(D[n]['mode'] == 'HOVER' for n in tg)
        if a == 'land':
            return all(D[n]['mode'] in ('GROUNDED', 'READY') and D[n]['z'] < .3 for n in tg)
        return False

    def _abort(self, report, log, plan, step, reason, dispatched):
        report['status'] = 'STOPPED'
        report['reason'] = f'{step["id"]} {step["action"]}: {reason}'
        hold = None
        if dispatched and step['action'] != 'land':
            snap = self.snapshot()
            air = [n for n in NAMES if snap['drones'][n].get('telemetry') and snap['drones'][n].get('mode') not in PARKED + ('FAULT',)]
            if air:
                code, res = self.client.dispatch({'action': 'hover', 'targets': air, 'params': {}})
                hold = {'action': 'hover', 'targets': air, 'http': code, 'ok': res.get('ok'), 'error': res.get('error')}
                if code != 200 or res.get('ok') is not True:
                    code, res = self.client.emergency_hover()
                    hold['escalated'] = {'action': 'emergency_hover', 'http': code, 'ok': res.get('ok')}
        report['contingency'] = hold
        report['replanning_options'] = [
            'Re-run "plan" with the same objective: agents re-bid on current telemetry and MA\'AT re-checks.',
            'Exclude the deviating/faulted aircraft ("without <name>") and re-plan with the remaining drones.',
            'Hold position (already commanded if airborne) or land all through the operator dashboard.']
        log.record('MISSION_STOPPED', 'COORDINATOR', 'POST /api/mission/dispatch' if hold else 'none',
                   plan.get('participants'), safety_result=reason, dispatch_outcome=hold)
        return report


def fgoals_offset(fgoals, centre):
    """Translation between the supervisor's formation goals and the centred slot set
    (non-zero when the supervisor shifted the formation around a stationary aircraft)."""
    gx = sum(g[0] for g in fgoals.values()) / len(fgoals)
    gy = sum(g[1] for g in fgoals.values()) / len(fgoals)
    cx = sum(p[0] for p in centre) / len(centre)
    cy = sum(p[1] for p in centre) / len(centre)
    return (gx - cx, gy - cy)


# --------------------------------------------------------------------------- presentation
def format_transcript(tr):
    return '\n'.join(f'[{t["role"]:<11} {t["sender"]:<11}| {t["source"]}] {t["text"]}' for t in tr)


def summarize(plan):
    lines = [f'Mission {plan["mission_id"]}  route={plan["route"]}  status={plan["status"]}'
             f'  executable_now=False (operator approval required)']
    for s in plan.get('steps', []):
        c = s.get('check') or {}
        lines.append(f'  {s["id"]:<3} {s["action"]:<9} {",".join(s["targets"]):<34} {json.dumps(s.get("params", {})):<26} '
                     f'{"OK " if c.get("ok") else "NO "}{"(pred) " if c.get("predicted") else "(exact)"} '
                     f'min_sep={c.get("planned_min_sep")}  {"" if c.get("ok") else c.get("reason")}')
    for n, a in plan.get('assignment', {}).items():
        lines.append(f'  assign {n:<7} -> {a["role"]:<14} sector_goal={a.get("sector_goal")}')
    g = plan.get('graphs', {})
    prox = g.get('proximity', {}).get('edges', [])
    lines.append(f'  graphs: DAG {len(g.get("mission_dag", {}).get("nodes", []))} steps; assignment '
                 f'{len(g.get("assignment", {}).get("edges", []))} edges; proximity {len(prox)} pairs '
                 f'({sum(e["below_1m"] for e in prox)} below 1.0 m); provenance {len(g.get("provenance", {}).get("edges", []))} evidence links')
    for e in prox:
        if e['below_1m']:
            lines.append(f'    proximity < 1.0 m: {e["a"]}-{e["b"]} {e["planned_min_sep_m"]} m at {e["step"]}')
    for r in plan.get('reasons', []):
        lines.append('  reason: ' + r)
    for alt in plan.get('alternatives', []):
        lines.append(f'  alternative: {alt["objective"]["text"]} -> {alt["status"]}')
    if plan.get('log_path'):
        lines.append('  log: ' + plan['log_path'])
    return '\n'.join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('plan', 'run'))
    ap.add_argument('objective')
    ap.add_argument('--approve', action='store_true', help='explicit operator approval (run only)')
    ap.add_argument('--snapshot', help='plan offline against a saved /api/state JSON (OFFLINE_REPLAY)')
    ap.add_argument('--end', choices=('land', 'hold'), default='land')
    ap.add_argument('--json', action='store_true', help='print the full plan JSON')
    a = ap.parse_args(argv)
    if a.snapshot:
        if a.command == 'run':
            print('run is live-only; --snapshot is for offline planning'); return 2
        client = OfflineSupervisor(json.loads(Path(a.snapshot).read_text()))
    else:
        client = SupervisorClient()
    co = Coordinator(client)
    try:
        plan = co.plan(a.objective, end=a.end)
    except CoordinatorError as e:
        print('REFUSED:', e); return 2
    print(format_transcript(plan['transcript']))
    print()
    print(summarize(plan))
    if a.json:
        print(json.dumps(plan, indent=1, default=str))
    if a.command == 'plan':
        print('\nPLAN ONLY: nothing was dispatched.')
        return 0 if plan['status'] == 'VALIDATED' else 1
    if not a.approve:
        print('\nNot executed: add --approve to give explicit operator approval.'); return 3
    if plan['status'] != 'VALIDATED':
        print('\nNot executed: plan is not VALIDATED by MA\'AT.'); return 1
    rep = co.execute(plan, approve=True)
    for s in rep['steps']:
        print(f'{s.get("result", "----"):4} {s["id"]} {s["action"]:<9} {s.get("observed", {}).get("reason") or s.get("precheck", {}).get("reason")}')
    print(f'EXECUTION {rep["status"]}' + (f': {rep.get("reason")}' if rep.get('reason') else ''))
    for o in rep.get('replanning_options', []):
        print('  option:', o)
    return 0 if rep['status'] == 'COMPLETED' else 1


if __name__ == '__main__':
    sys.exit(main())
