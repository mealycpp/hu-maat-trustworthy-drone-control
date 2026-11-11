"""Offline tests for the MA'AT plan check and the deterministic mission coordinator.
Never connects to Gazebo or any live port; nothing flies."""
import copy
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib import request, error

import test_missions  # noqa: F401  (installs gz stubs and loads control_server_hu)
import control_server_hu as ctrl
from control_server_hu import Drone, Fleet, NAMES
import mission_coordinator as mc

CATALOG = {s: {'status': 'VALIDATED', 'alias_of': None} for s in
           ('v', 'line', 'wedge', 'arc', 'grid', 'column', 'inverted_v', 'circle', 'arrow')}
CATALOG['relay_chain'] = {'status': 'VALIDATED', 'alias_of': 'line'}
CATALOG['fan'] = {'status': 'CANDIDATE', 'alias_of': None}
HOME = [(-2.2, -1.5), (0, -1.5), (2.2, -1.5), (-1.1, 1.1), (1.1, 1.1)]
ROLES = {'DRONE_AGENT', 'COORDINATOR', 'MAAT'}
SOURCES = {'DETERMINISTIC_AGENT_POLICY', 'SAFETY_SUPERVISOR', 'TELEMETRY_FACT'}


def fleet(mode='HOVER', z=1.65):
    """Same fake Fleet pattern as test_v9.fleet(): built via __new__, no Gazebo."""
    f = Fleet.__new__(Fleet)
    f.dr = {n: Drone(n, x=x, y=y, z=z, mode=mode, init=True, last_seen=100, target_x=x, target_y=y, target_z=z)
            for n, (x, y) in zip(NAMES, HOME)}
    f.lock = threading.RLock(); f.fresh = lambda d: True; f.event = lambda *a, **k: None
    return f


def dump(f):
    return {n: dict(vars(d)) for n, d in f.dr.items()}


def state(mode='GROUNDED', z=.05, **over):
    """A /api/state-shaped snapshot. over: {name: {field: value}}."""
    drones = []
    for n, (x, y) in zip(NAMES, HOME):
        d = {'name': n, 'x': x, 'y': y, 'z': z, 'mode': mode, 'fault': False, 'yaw': 0.,
             'target': [x, y, z], 'telemetry': True}
        d.update(over.get(n, {}))
        drones.append(d)
    return {'drones': drones}


def coord(st, simulate=False, log_dir=None):
    client = mc.OfflineSupervisor(st, simulate_dispatch=simulate)
    return mc.Coordinator(client, catalog=CATALOG, log_dir=log_dir or tempfile.mkdtemp(), log_enabled=log_dir is not None,
                          session_id='Stest'), client


class TestPlanCheck(unittest.TestCase):
    def test_plan_check_never_mutates_live_state(self):
        f = fleet(); before = copy.deepcopy(dump(f))
        r = f.plan_check([{'action': 'formation', 'targets': list(NAMES), 'params': {'shape': 'v'}},
                          {'action': 'move', 'targets': list(NAMES), 'params': {'dy': .5}},
                          {'action': 'land', 'targets': list(NAMES), 'params': {}}])
        self.assertTrue(r['ok'], r); self.assertTrue(r['read_only'])
        self.assertEqual(dump(f), before)
        self.assertEqual([s['predicted'] for s in r['steps']], [False, True, True])
        self.assertIn('planned_min_sep', r['steps'][0])

    def test_first_step_identical_to_command_path(self):
        f = fleet(); live = fleet()
        r = f.plan_check([{'action': 'formation', 'targets': list(NAMES), 'params': {'shape': 'v'}}])
        live.command('formation', list(NAMES), {'shape': 'v'})
        for n in NAMES:
            d = live.dr[n]
            self.assertEqual(r['steps'][0]['planned_goals'][n], [round(d.target_x, 3), round(d.target_y, 3), round(d.target_z, 3)])

    def test_rejection_reason_matches_command_message(self):
        f = fleet()
        r = f.plan_check([{'action': 'move', 'targets': ['Horus'], 'params': {'forward_m': 2.0}}])
        with self.assertRaises(ValueError) as cm:
            fleet().command('move', ['Horus'], {'forward_m': 2.0})
        self.assertFalse(r['ok']); self.assertEqual(r['steps'][0]['reason'], str(cm.exception))

    def test_fault_and_stale_rejected_without_mutation(self):
        f = fleet(); f.dr['Ra'].fault = True; before = copy.deepcopy(dump(f))
        r = f.plan_check([{'action': 'formation', 'targets': list(NAMES), 'params': {'shape': 'line'}}])
        self.assertFalse(r['ok']); self.assertIn('Faulted', r['steps'][0]['reason']); self.assertEqual(dump(f), before)
        f = fleet(); f.fresh = lambda d: d.name != 'Thoth'
        r = f.plan_check([{'action': 'move', 'targets': ['Thoth', 'Ra'], 'params': {'dz': .1}}])
        self.assertFalse(r['ok']); self.assertIn('No fresh odometry', r['steps'][0]['reason'])
        self.assertFalse(r['telemetry']['Thoth']['fresh'])

    def test_later_steps_predicted_and_skipped_after_failure(self):
        f = fleet(mode='READY', z=.05)
        r = f.plan_check([{'action': 'takeoff', 'targets': list(NAMES), 'params': {'altitude': 1.7}},
                          {'action': 'formation', 'targets': list(NAMES), 'params': {'shape': 'v'}},
                          {'action': 'move', 'targets': list(NAMES), 'params': {'dx': 1.99}},
                          {'action': 'land', 'targets': list(NAMES), 'params': {}}])
        self.assertTrue(r['steps'][0]['ok']); self.assertTrue(r['steps'][1]['ok'])
        self.assertFalse(r['steps'][2]['ok']); self.assertIn('arena', r['steps'][2]['reason'])
        self.assertFalse(r['steps'][3]['checked'])
        self.assertTrue(all(d.mode == 'READY' for d in f.dr.values()))

    def test_unsupported_actions_refused(self):
        f = fleet()
        for step in ({'action': 'fault', 'targets': ['Ra']}, {'action': 'emergency_land', 'targets': list(NAMES)},
                     {'action': 'move', 'targets': ['Banana'], 'params': {}}, 'x'):
            self.assertFalse(f.plan_check([step])['ok'])
        with self.assertRaises(ValueError):
            f.plan_check([])

    def test_scratch_cannot_publish(self):
        s = ctrl._PlanScratch({}, set())
        with self.assertRaises(RuntimeError):
            s.publish('Horus')


class TestHTTP(unittest.TestCase):
    def setUp(self):
        self.fleet = fleet(); ctrl.Handler.fleet = self.fleet
        self.calls = []
        real = self.fleet.command
        self.fleet.command = lambda *a, **k: (self.calls.append(a), real(*a, **k))[1]
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), ctrl.Handler)
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'
        self.t = threading.Thread(target=self.server.serve_forever, daemon=True); self.t.start()
        self.tok = patch.object(ctrl, 'token', return_value='tkn'); self.tok.start()

    def tearDown(self):
        self.tok.stop(); self.server.shutdown(); self.server.server_close(); self.t.join(timeout=2)

    def post(self, route, body, headers=None):
        req = request.Request(self.url + route, data=json.dumps(body).encode(),
                              headers={'Content-Type': 'application/json', **(headers or {})})
        try:
            with request.urlopen(req, timeout=5) as r:
                return r.status, json.load(r)
        except error.HTTPError as e:
            return e.code, json.load(e)

    def test_plan_check_endpoint_read_only(self):
        before = copy.deepcopy(dump(self.fleet))
        code, r = self.post('/api/plan/check', {'steps': [{'action': 'formation', 'targets': list(NAMES), 'params': {'shape': 'v'}}]})
        self.assertEqual(code, 200); self.assertTrue(r['ok']); self.assertTrue(r['read_only'])
        self.assertEqual(dump(self.fleet), before); self.assertEqual(self.calls, [])

    def test_plan_check_cross_origin_rejected(self):
        code, _ = self.post('/api/plan/check', {'steps': []}, {'Origin': 'http://evil.example'})
        self.assertEqual(code, 403)

    def test_plan_check_bad_body(self):
        code, r = self.post('/api/plan/check', {'steps': 'nope'})
        self.assertEqual(code, 400); self.assertFalse(r['ok'])

    def test_coordinator_planner_tag_accepted(self):
        body = {'action': 'move', 'targets': ['Horus'], 'params': {'dz': .1}, 'operator_confirmed': True, 'planner': 'HU-MISSION-COORD'}
        code, r = self.post('/api/mission/dispatch', body, {'X-HU-Token': 'tkn'})
        self.assertEqual(code, 200); self.assertEqual(self.calls[0][3], 'MISSION COORD CONFIRMED')
        code, _ = self.post('/api/mission/dispatch', {**body, 'planner': 'SOMETHING-ELSE'}, {'X-HU-Token': 'tkn'})
        self.assertEqual(code, 409)
        code, _ = self.post('/api/mission/dispatch', {**body, 'operator_confirmed': False}, {'X-HU-Token': 'tkn'})
        self.assertEqual(code, 409)
        code, _ = self.post('/api/mission/dispatch', body)
        self.assertEqual(code, 403); self.assertEqual(len(self.calls), 1)


class TestAgents(unittest.TestCase):
    def bids(self, st, reserve=(), exclude=()):
        snap = mc.normalize_state(st, 'T0')
        battery = mc.SimulatedBattery()
        ctx = {'sector_point': (0, 1.6), 'reserve': set(reserve), 'exclude': set(exclude)}
        return {n: mc.AgentPolicy(n, battery).bid(snap, ctx) for n in NAMES}

    def test_faulted_stale_lowbattery_reserve_busy(self):
        b = self.bids(state(mode='HOVER', z=1.7, Horus={'fault': True, 'mode': 'FAULT'}, Ra={'telemetry': False},
                            Thoth={'sim_battery_pct': 12}, Osiris={'mode': 'MOVE'}), reserve=['Anubis'])
        self.assertEqual(b['Horus']['available'], 'NO'); self.assertIn('fault', ' '.join(b['Horus']['reasons']))
        self.assertEqual(b['Ra']['available'], 'UNKNOWN'); self.assertIsNone(b['Ra']['position'])
        self.assertIsNone(b['Ra']['distance_to_sector_m']); self.assertIn('UNKNOWN', b['Ra']['reasons'][0])
        self.assertEqual(b['Thoth']['available'], 'NO'); self.assertIn('SIMULATED battery', ' '.join(b['Thoth']['reasons']))
        self.assertEqual(b['Anubis']['available'], 'NO'); self.assertIn('reserve', ' '.join(b['Anubis']['reasons']))
        self.assertEqual(b['Osiris']['available'], 'NO'); self.assertIn('busy', ' '.join(b['Osiris']['reasons']))
        for bid in b.values():
            self.assertEqual(bid['policy'], 'DETERMINISTIC_AGENT_POLICY')
            self.assertTrue(all(e['route'] == 'GET /api/state' for e in bid['evidence']))

    def test_healthy_bid(self):
        b = self.bids(state())['Anubis']
        self.assertEqual(b['available'], 'YES'); self.assertEqual(b['position'], [-1.1, 1.1, .05])
        self.assertAlmostEqual(b['distance_to_sector_m'], 1.21, places=2)

    def test_negotiation_yield_is_deterministic(self):
        snap = mc.normalize_state(state(Ra={'x': -2.0, 'y': -1.5}), 'T0')
        battery = mc.SimulatedBattery()
        agents = {n: mc.AgentPolicy(n, battery) for n in NAMES}
        pts = [(-2.2, -1.5), (2.2, -1.5)]
        a1, m1 = mc.negotiate(agents, ['Horus', 'Ra'], snap, pts)
        a2, m2 = mc.negotiate(agents, ['Ra', 'Horus'], snap, pts)
        self.assertEqual(a1, a2); self.assertEqual(a1, {'Horus': 0, 'Ra': 1})
        self.assertTrue(any('Ra' == s and 'yield slot 0 to Horus' in t for s, t, _ in m1))


class TestCoordinator(unittest.TestCase):
    def test_objective_grammar(self):
        o = mc.parse_objective('Inspect the north sector, keep Anubis in reserve, use three drones')
        self.assertEqual((o['kind'], o['sector'], o['constraints']['reserve'], o['constraints']['count']),
                         ('inspect', 'north', ['Anubis'], 3))
        self.assertEqual(mc.parse_objective('relay chain to east')['shape'], 'relay_chain')
        self.assertEqual(mc.parse_objective('form a v shape and hold')['shape'], 'v')
        self.assertEqual(mc.parse_objective('inspect north sector offset 2.0 m')['constraints']['offset_m'], 2.0)
        for bad in ('patrol the arena', 'inspect the moon', 'tell me a joke', '', 'survey the arena offset 1 m'):
            with self.assertRaises(mc.CoordinatorError):
                mc.parse_objective(bad)

    def test_inspect_north_plan_structure_and_graphs(self):
        co, client = coord(state())
        p = co.plan('inspect north sector')
        self.assertEqual(p['status'], 'VALIDATED', p.get('reasons'))
        self.assertFalse(p['executable']); self.assertTrue(p['requires_operator_approval'])
        self.assertEqual([s['action'] for s in p['steps']], ['takeoff', 'formation', 'move', 'hover', 'move', 'land'])
        mv = p['steps'][2]['params']; self.assertEqual(mv['dx'], 0.); self.assertGreater(mv['dy'], 0.)
        self.assertEqual(p['steps'][4]['params']['dy'], -mv['dy'])
        for goal in (a['sector_goal'] for a in p['assignment'].values()):
            self.assertLessEqual(max(abs(goal[0]), abs(goal[1])), 3.35)
        g = p['graphs']
        self.assertEqual(len(g['mission_dag']['edges']), len(p['steps']) - 1)
        self.assertEqual(len(g['proximity']['edges']), 10)
        self.assertTrue(all('below_1m' in e for e in g['proximity']['edges']))
        self.assertEqual({e['from'] for e in g['assignment']['edges']}, set(NAMES))
        self.assertTrue(g['provenance']['edges'])
        self.assertTrue(all(t['role'] in ROLES and t['source'] in SOURCES for t in p['transcript']))
        self.assertEqual(p['transcript'][-1]['role'], 'MAAT'); self.assertIn('VALIDATED', p['transcript'][-1]['text'])
        self.assertEqual(client.dispatched, [])

    def test_plan_is_deterministic(self):
        a = coord(state())[0].plan('inspect east sector')
        b = coord(state())[0].plan('inspect east sector')
        strip = lambda p: (json.dumps([{k: s[k] for k in ('action', 'targets', 'params')} for s in p['steps']]),
                           json.dumps(p['assignment'], sort_keys=True), [t['text'] for t in p['transcript']])
        self.assertEqual(strip(a), strip(b))

    def test_faulted_drone_excluded_and_count_infeasible(self):
        st = state(mode='HOVER', z=1.7, Thoth={'fault': True, 'mode': 'FAULT'})
        p = coord(st)[0].plan('form line and hold', end='hold')
        self.assertNotIn('Thoth', p['participants']); self.assertEqual(p['assignment']['Thoth']['role'], 'unavailable')
        p = coord(st)[0].plan('form line and hold using five drones')
        self.assertEqual(p['status'], 'NOT_PLANNABLE'); self.assertIn('only 4 available', p['reasons'][0])
        self.assertEqual(p['steps'], [])

    def test_stale_drone_unknown_not_used(self):
        p = coord(state(Osiris={'telemetry': False}))[0].plan('inspect south sector')
        self.assertEqual(p['bids']['Osiris']['available'], 'UNKNOWN')
        self.assertNotIn('Osiris', p.get('participants', []))
        # the supervisor itself refuses a formation while any aircraft is stale
        self.assertEqual(p['status'], 'REJECTED'); self.assertIn('Fresh telemetry', p['reasons'][0])

    def test_unsafe_offset_rejected_with_safe_alternative(self):
        p = coord(state())[0].plan('inspect north sector in arrow formation offset 2.0 m')
        self.assertEqual(p['status'], 'REJECTED'); self.assertIn('arena', p['reasons'][0])
        self.assertTrue(p['alternatives']); alt = p['alternatives'][0]
        self.assertEqual(alt['status'], 'VALIDATED'); self.assertLess(alt['offset_m'][1], 2.0)
        self.assertFalse(alt['executable'])
        self.assertTrue(any(t['role'] == 'MAAT' and 'Safe alternative' in t['text'] for t in p['transcript']))

    def test_unvalidated_shape_refused_before_maat(self):
        p = coord(state())[0].plan('form fan and hold')
        self.assertEqual(p['status'], 'NOT_PLANNABLE'); self.assertIn('CANDIDATE', p['reasons'][0]); self.assertIsNone(p['maat'])

    def test_maat_unavailable_fails_closed(self):
        co, client = coord(state())
        client.plan_check = lambda steps: (_ for _ in ()).throw(mc.CoordinatorError('no route'))
        p = co.plan('inspect north sector')
        self.assertEqual(p['status'], 'REJECTED'); self.assertIn('unavailable', p['reasons'][0])


class TestExecution(unittest.TestCase):
    def clock(self):
        t = [0.]
        def tick():
            t[0] += .5
            return t[0]
        return tick

    def test_requires_approval(self):
        co, client = coord(state(), simulate=True)
        p = co.plan('inspect north sector')
        r = co.execute(p, approve=False)
        self.assertEqual(r['status'], 'NOT_STARTED'); self.assertEqual(client.dispatched, [])
        p['status'] = 'REJECTED'
        self.assertEqual(co.execute(p, approve=True)['status'], 'NOT_STARTED'); self.assertEqual(client.dispatched, [])

    def test_simulated_execution_verified_and_logged(self):
        d = tempfile.mkdtemp()
        co, client = coord(state(), simulate=True, log_dir=d)
        p = co.plan('inspect north sector')
        r = co.execute(p, approve=True, sleep=lambda s: None, clock=self.clock())
        self.assertEqual(r['status'], 'COMPLETED', r.get('reason'))
        self.assertEqual([s['result'] for s in r['steps']], ['PASS'] * len(p['steps']))
        self.assertEqual([s['action'] for s in client.dispatched], [s['action'] for s in p['steps']])
        rows = [json.loads(x) for x in Path(p['log_path']).read_text().splitlines()]
        kinds = [x['kind'] for x in rows]
        for k in ('PLAN', 'OPERATOR_APPROVED', 'PRECHECK', 'DISPATCH', 'VERIFY', 'MISSION_COMPLETED'):
            self.assertIn(k, kinds)
        for x in rows:
            for field in ('session_id', 'mission_id', 'utc', 'monotonic', 'source', 'route', 'targets', 'params',
                          'safety_result', 'dispatch_outcome', 'observed_outcome'):
                self.assertIn(field, x)
        self.assertTrue(all(x['mission_id'] == p['mission_id'] for x in rows))

    def test_stops_when_state_changes_after_planning(self):
        co, client = coord(state(mode='HOVER', z=1.7), simulate=True)
        p = co.plan('inspect north sector')
        self.assertEqual(p['status'], 'VALIDATED', p.get('reasons'))
        client.fleet.dr['Ra'].fault = True; client.fleet.dr['Ra'].mode = 'FAULT'
        r = co.execute(p, approve=True, sleep=lambda s: None, clock=self.clock())
        self.assertEqual(r['status'], 'STOPPED'); self.assertIn('re-check failed', r['reason'])
        self.assertEqual(client.dispatched, []); self.assertTrue(r['replanning_options'])

    def test_deviation_holds_fleet(self):
        co, client = coord(state(mode='HOVER', z=1.7), simulate=True)
        p = co.plan('inspect north sector')
        real = client.dispatch
        def dispatch(step):
            code, res = real(step)
            if step['action'] == 'formation':        # simulate a proximity hold: Horus stops short
                d = client.fleet.dr['Horus']; d.mode = 'HOVER'; d.target_x, d.target_y = d.x, d.y = (d.x + .6, d.y)
            return code, res
        client.dispatch = dispatch
        r = co.execute(p, approve=True, sleep=lambda s: None, clock=self.clock())
        self.assertEqual(r['status'], 'STOPPED'); self.assertIn('S1', r['reason'])
        self.assertEqual(r['contingency']['action'], 'hover')
        self.assertEqual(client.dispatched[-1]['action'], 'hover')


if __name__ == '__main__':
    unittest.main(verbosity=2)
