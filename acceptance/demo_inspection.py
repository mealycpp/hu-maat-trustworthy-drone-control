#!/usr/bin/env python3
"""HU-MA'AT conference demo: cooperative inspection (Gazebo simulation only).

  preflight -> plan "inspect north sector" (agent bids + MA'AT verdict)
  -> [--approve] execute, every step verified from telemetry
  -> intentionally unsafe request (operator-forced offset out of the arena) -> MA'AT rejection + safe alternative
  -> [--approve] simulated command-link fault on one drone via SpaceOps Fault Lab
  -> re-plan with the remaining aircraft -> [--approve] execute -> land all -> results summary.

Without --approve this is a DRY RUN: it only reads telemetry and calls the read-only
POST /api/plan/check; nothing is dispatched and nothing moves.
The agents are DETERMINISTIC_AGENT_POLICY rules, not learned/autonomous reasoning.

  python3 demo_inspection.py                     # dry run against the live supervisor
  python3 demo_inspection.py --approve           # live, operator-approved demo
  python3 demo_inspection.py --snapshot s.json   # offline rehearsal on a saved /api/state
"""
import argparse, itertools, json, math, sys, time, urllib.error, urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path.home() / 'hu-telepresence-gazebo'
sys.path.insert(0, str(ROOT / 'festival'))
import mission_coordinator as mc  # noqa: E402

SO = 'http://127.0.0.1:8766'
NAMES = mc.NAMES
OBJECTIVE = 'inspect north sector'
UNSAFE = 'inspect north sector in arrow formation offset 2.0 m'
FALLBACK = 'form line and hold'


def http(url, body=None, headers=None, timeout=10):
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json', **(headers or {})},
                                 method='GET' if body is None else 'POST')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b'{}')
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, OSError) as e:
        return None, {'error': str(e)}


def drones(co):
    return mc.normalize_state(co.client.state())['drones']


def min_sep(D, names=NAMES):
    air = [D[n] for n in names if D[n].get('telemetry') and D[n].get('z', 0) > .3]
    return min((math.dist((a['x'], a['y'], a['z']), (b['x'], b['y'], b['z'])) for a, b in itertools.combinations(air, 2)),
               default=None)


class Demo:
    def __init__(self, co, approve, live):
        self.co, self.approve, self.live = co, approve, live
        self.results = []

    def rec(self, name, ok, **evidence):
        res = 'PASS' if ok else 'FAIL'
        self.results.append({'step': name, 'result': res, 'utc': mc.utc(), **evidence})
        print(f'{res:4} {name:58} {json.dumps(evidence, default=str)[:240]}')
        return ok

    def banner(self, text):
        print('\n' + '=' * 100 + '\n' + text + '\n' + '=' * 100)

    def show(self, plan):
        print(mc.format_transcript(plan['transcript']))
        print(mc.summarize(plan))

    def execute(self, plan, label):
        rep = self.co.execute(plan, approve=True)
        for s in rep['steps']:
            obs = s.get('observed') or {}
            self.rec(f'{label} {s["id"]} {s["action"]} {",".join(s["targets"])}', s.get('result') == 'PASS',
                     reason=obs.get('reason') or (s.get('precheck') or {}).get('reason'), elapsed_s=obs.get('elapsed_s'),
                     observed_min_sep_m=obs.get('observed_min_sep_m'), dwell_max_drift_m=obs.get('dwell_max_drift_m'))
        self.rec(f'{label} mission {plan["mission_id"]}', rep['status'] == 'COMPLETED', status=rep['status'],
                 reason=rep.get('reason'), contingency=rep.get('contingency'))
        return rep

    def preflight(self):
        self.banner('1. PREFLIGHT (telemetry only)')
        D = drones(self.co)
        fresh = [n for n in NAMES if D[n].get('telemetry')]
        faults = [n for n in NAMES if D[n].get('fault')]
        ok = self.rec('supervisor telemetry fresh for all five aircraft', len(fresh) == 5, fresh=fresh,
                      modes={n: D[n].get('mode') for n in NAMES})
        ok &= self.rec('no active simulated faults', not faults, faults=faults)
        if self.live:
            ok &= self.rec('mission gateway token present', mc.TOKEN_FILE.exists(), path=str(mc.TOKEN_FILE))
            st, s = http(SO + '/api/ops/session')
            self.rec('SpaceOps fault lab reachable (needed only for the fault stage)', st == 200, http=st,
                     session_id=s.get('session_id'))
        return ok

    def land_all(self):
        self.banner('LAND ALL (mission gateway, verified from telemetry)')
        code, res = self.co.client.dispatch({'action': 'land', 'targets': list(NAMES), 'params': {}})
        t0 = time.monotonic()
        while time.monotonic() - t0 < 75:
            D = drones(self.co)
            if all(D[n].get('mode') in ('GROUNDED', 'READY') and D[n].get('z', 1) < .3 for n in NAMES if D[n].get('telemetry')):
                break
            time.sleep(.3)
        D = drones(self.co)
        return self.rec('all aircraft landed and disarmed', code == 200 and all(
            D[n].get('mode') in ('GROUNDED', 'READY') and D[n].get('z', 1) < .3 for n in NAMES),
            http=code, error=res.get('error'), modes={n: [D[n].get('mode'), D[n].get('z')] for n in NAMES},
            faults={n: D[n].get('fault') for n in NAMES}, settle_s=round(time.monotonic() - t0, 1))

    def run(self, fault_drone, do_fault):
        if not self.preflight() and self.approve:
            print('Preflight failed: live execution skipped (dry-run stages still shown).')
            self.approve = False
        self.banner(f'2. PLAN "{OBJECTIVE}" (deterministic agent policies + MA\'AT read-only check)')
        plan = self.co.plan(OBJECTIVE, end='hold')
        self.show(plan)
        self.rec(f'MA\'AT verdict for "{OBJECTIVE}"', plan['status'] == 'VALIDATED', status=plan['status'],
                 reasons=plan.get('reasons'), team=plan.get('participants'), offset_m=plan.get('offset_m'))
        airborne = False
        try:
            if self.approve and plan['status'] == 'VALIDATED':
                self.banner('3. OPERATOR APPROVED (--approve): EXECUTE, VERIFY EACH STEP FROM TELEMETRY')
                airborne = True   # from here on, always finish with "land all"
                rep = self.execute(plan, 'inspect')
                ran_ok = rep['status'] == 'COMPLETED'
            else:
                print('\n3. EXECUTION SKIPPED: dry run (no --approve) or plan not VALIDATED. Nothing moved.')
                ran_ok = False
            self.banner(f'4. INTENTIONALLY UNSAFE REQUEST: "{UNSAFE}"')
            bad = self.co.plan(UNSAFE, end='hold')
            self.show(bad)
            alt = next((a for a in bad['alternatives'] if a['status'] == 'VALIDATED'), None)
            self.rec('MA\'AT rejects the out-of-bounds request', bad['status'] == 'REJECTED', reasons=bad.get('reasons'))
            self.rec('MA\'AT proposes a VALIDATED safe alternative (not executed)', alt is not None,
                     alternative=alt and alt['objective']['text'], offset_m=alt and alt['offset_m'])
            if not (self.approve and do_fault and ran_ok):
                print('\n5-6. FAULT + REPLAN SKIPPED (needs --approve, a completed inspection, and no --no-fault).')
                if self.approve and do_fault:
                    self.rec('fault stage preconditions', False, reason='inspection did not complete')
                return
            self.fault_and_replan(fault_drone)
        finally:
            if self.approve and airborne:
                self.land_all()

    def fault_and_replan(self, fd):
        self.banner(f'5. SIMULATED COMMAND-LINK FAULT ON {fd} (SpaceOps Fault Lab)')
        D = drones(self.co)
        sep = min_sep(D)
        pre = D[fd].get('mode') == 'HOVER' and D[fd].get('z', 0) >= .8 and sep is not None and sep >= 1.2
        if not self.rec('fault-lab preconditions from telemetry (HOVER >= 0.8 m, fleet min sep >= 1.2 m)', pre,
                        mode=D[fd].get('mode'), z=D[fd].get('z'), fleet_min_sep_m=sep and round(sep, 3)):
            return
        st, s = http(SO + '/api/ops/session')
        if not self.rec('SpaceOps session token', st == 200 and bool(s.get('token')), http=st):
            return
        st, r = http(SO + '/api/ops/fault', {'action': 'inject_fault', 'vehicle': fd}, {'X-RSCL-CSRF': s['token']})
        t0 = time.monotonic()
        seen = None
        while time.monotonic() - t0 < 6:
            D = drones(self.co)
            if D[fd].get('fault') and D[fd].get('mode') == 'FAULT':
                seen = round(time.monotonic() - t0, 2)
                break
            time.sleep(.15)
        if not self.rec(f'{fd} fault visible in supervisor telemetry', st == 200 and seen is not None, http=st,
                        error=r.get('error'), detect_s=seen):
            return
        self.banner('6. RE-PLAN WITH THE REMAINING AIRCRAFT')
        plan = self.co.plan(OBJECTIVE, end='hold')
        self.show(plan)
        self.rec(f'{fd} agent reports unavailable (fault)', plan['bids'][fd]['available'] == 'NO',
                 reasons=plan['bids'][fd]['reasons'])
        chosen = plan if plan['status'] == 'VALIDATED' else next(
            (a for a in plan['alternatives'] if a['status'] == 'VALIDATED'), None)
        if chosen is None:
            print(f'No VALIDATED inspection re-plan; trying the fallback objective "{FALLBACK}".')
            chosen = self.co.plan(FALLBACK, end='hold')
            self.show(chosen)
        ok = self.rec('VALIDATED re-plan without the faulted aircraft', chosen['status'] == 'VALIDATED'
                      and fd not in chosen.get('participants', []), objective=chosen['objective']['text'],
                      team=chosen.get('participants'), offset_m=chosen.get('offset_m'))
        if ok:
            print('Operator approval for the re-plan: given by the same --approve flag for this scripted demo.')
            self.execute(chosen, 'replan')
            D = drones(self.co)
            self.rec(f'{fd} stayed put while the others flew (contained)', D[fd].get('mode') == 'FAULT',
                     mode=D[fd].get('mode'), position=[D[fd].get('x'), D[fd].get('y'), D[fd].get('z')])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--approve', action='store_true', help='explicit operator approval: fly the demo')
    ap.add_argument('--fault-drone', default='Anubis', choices=NAMES)
    ap.add_argument('--no-fault', action='store_true', help='skip the fault-injection stage')
    ap.add_argument('--snapshot', help='offline rehearsal against a saved /api/state JSON (never live)')
    a = ap.parse_args()
    if a.snapshot and a.approve:
        print('--approve is live-only'); return 2
    client = mc.OfflineSupervisor(json.loads(Path(a.snapshot).read_text())) if a.snapshot else mc.SupervisorClient()
    co = mc.Coordinator(client)
    print(f'HU-MA\'AT cooperative inspection demo  mode={"LIVE APPROVED" if a.approve else "DRY RUN (nothing moves)"}  '
          f'route={client.route_name}  session={co.session_id}')
    demo = Demo(co, a.approve, live=not a.snapshot)
    try:
        demo.run(a.fault_drone, not a.no_fault)
    except mc.CoordinatorError as e:
        demo.rec('coordinator error', False, error=str(e))
    verdict = 'PASS' if demo.results and all(r['result'] == 'PASS' for r in demo.results) else 'FAIL'
    out = {'generated_utc': mc.utc(), 'mode': 'LIVE_APPROVED' if a.approve else 'DRY_RUN', 'route': client.route_name,
           'session_id': co.session_id, 'agent_policy': mc.POLICY, 'battery_model': mc.SimulatedBattery.LABEL,
           'mission_logs': str(mc.LOG_DIR), 'steps': demo.results, 'verdict': verdict}
    rd = ROOT / 'acceptance' / 'results'
    rd.mkdir(parents=True, exist_ok=True)
    path = rd / f'demo_inspection_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json'
    path.write_text(json.dumps(out, indent=2, default=str))
    print('\n' + '=' * 100)
    print(f'RESULTS: {sum(r["result"] == "PASS" for r in demo.results)}/{len(demo.results)} PASS  verdict={verdict}  -> {path}')
    for r in demo.results:
        print(f'  {r["result"]:4} {r["step"]}')
    return 0 if verdict == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
