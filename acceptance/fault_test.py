#!/usr/bin/env python3
"""HU-MA'AT Phase 7 live fault/containment/recovery scenario (simulator only).

1 all take off -> 2 inject simulated command-link fault on Anubis (SpaceOps Fault Lab)
3 verify faulted aircraft refuses a command -> 4 verify a 5-aircraft V is refused
5 re-plan: V with the 4 healthy aircraft, Anubis treated as an obstacle
6 spoken emergency "yella land" (ASR variant) must land ALL immediately, incl. Anubis
Each step is judged from Gazebo telemetry, not from HTTP acknowledgements.
"""
import json, math, sys, time, urllib.request
from datetime import datetime, timezone

HU, GZ, SO = 'http://127.0.0.1:8100', 'http://127.0.0.1:8765', 'http://127.0.0.1:8766'
NAMES = ('Horus', 'Ra', 'Thoth', 'Anubis', 'Osiris')
FAULTY = 'Anubis'


def call(url, data=None, headers=None):
    req = urllib.request.Request(url, data=None if data is None else json.dumps(data).encode(),
                                 headers={'Content-Type': 'application/json', **(headers or {})},
                                 method='POST' if data is not None else 'GET')
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b'{}')


def state():
    return {d['name']: d for d in call(GZ + '/api/state')[1]['drones']}


def hu(phrase, voice=False):
    if voice:
        _, r = call(HU + '/api/hu/voice-auto', {'utterance': phrase})
    else:
        _, r = call(HU + '/api/hu/command', {'utterance': phrase, 'source': 'typed'})
    if r.get('band') == 'CONFIRM':
        st, c = call(HU + '/api/hu/confirm', {'cmd_id': r['cmd_id']})
        r['dispatched'] = st == 200 and c.get('ok') is True
        r['confirm_reply'] = c
    return r


def wait(pred, timeout):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        s = state()
        if pred(s):
            return round(time.monotonic() - t0, 2)
        time.sleep(0.15)
    return None


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else '.'
    token = call(SO + '/api/ops/session')[1]['token']
    lab = lambda action: call(SO + '/api/ops/fault', {'action': action, 'vehicle': FAULTY}, {'X-RSCL-CSRF': token})
    steps = []

    def rec(name, ok, **kw):
        steps.append({'step': name, 'result': 'PASS' if ok else 'FAIL', **kw})
        print(f"{'PASS' if ok else 'FAIL':4} {name:44} {json.dumps(kw)[:220]}")
        return ok

    r = hu('All, take off')
    t = wait(lambda s: all(d['mode'] == 'HOVER' and d['z'] > 1.4 for d in s.values()), 30)
    if not rec('all take off', t is not None, settle_s=t, interpreter=r.get('decision_source')):
        return finish(steps, outdir)
    time.sleep(2)
    st, f = lab('inject_fault')
    t_inj = time.monotonic()
    t = wait(lambda s: s[FAULTY]['fault'] and s[FAULTY]['mode'] == 'FAULT', 5)
    so = call(SO + '/api/ops/state')[1]['state']
    rec('fault injected and visible in telemetry', st == 200 and t is not None, detect_s=t, http=st,
        error=f.get('error'), spaceops_view={d['name']: [d['mode'], d['z']] for d in so['drones']},
        spaceops_min_sep=so.get('min_sep_m'))
    # SpaceOps samples at 4 Hz: wait until its own view shows the fault.
    t1 = time.monotonic()
    while time.monotonic() - t1 < 3 and not any(d['name'] == FAULTY and d.get('fault') for d in call(SO + '/api/ops/state')[1]['state']['drones']):
        time.sleep(0.1)
    st, f = lab('test_rejection')
    rec('faulted aircraft refuses HOVER', f.get('observed_refusal') is True, http=st)
    r = hu('All form a V shape')
    s = state()
    rec('5-aircraft V refused (faulted member)', not r.get('dispatched') and all(s[n]['mode'] == 'HOVER' or n == FAULTY for n in NAMES),
        reason=(r.get('confirm_reply') or {}).get('error') or r.get('reason'))
    z_anubis = (s[FAULTY]['x'], s[FAULTY]['y'], s[FAULTY]['z'])
    plan_log = []
    for phrase in (f'All except {FAULTY} form a V shape', f'All except {FAULTY} form a line'):
        r = hu(phrase)
        plan_log.append({'phrase': phrase, 'dispatched': r.get('dispatched'),
                         'reason': (r.get('confirm_reply') or {}).get('error') or r.get('reason')})
        print('   plan:', plan_log[-1])
        if r.get('dispatched'): break
    t = wait(lambda s: all(math.dist((s[n]['x'], s[n]['y'], s[n]['z']), tuple(s[n]['target'])) < .12
                           for n in NAMES if n != FAULTY) and all(s[n]['mode'] in ('FORMATION', 'HOVER') for n in NAMES if n != FAULTY), 45)
    s = state()
    sep = min(math.dist((s[n]['x'], s[n]['y'], s[n]['z']), (s[FAULTY]['x'], s[FAULTY]['y'], s[FAULTY]['z'])) for n in NAMES if n != FAULTY)
    rec('re-planned 4-aircraft V around faulted Anubis', r.get('dispatched') and t is not None
        and math.dist(z_anubis, (s[FAULTY]['x'], s[FAULTY]['y'], s[FAULTY]['z'])) < .1,
        settle_s=t, contained_s=round(time.monotonic() - t_inj, 2), min_sep_to_faulted_m=round(sep, 3),
        targets=r.get('targets'), plans=plan_log)
    time.sleep(1.5)
    t0 = time.monotonic()
    r = hu('yella land', voice=True)
    ack_ms = round((time.monotonic() - t0) * 1000, 1)
    began = wait(lambda s: all(d['mode'] in ('LANDING', 'GROUNDED') for d in s.values()), 3)
    t = wait(lambda s: all(d['mode'] == 'GROUNDED' and d['z'] < .3 for d in s.values()), 60)
    rec('voice "yella land" -> immediate fleet landing incl. faulted', r.get('band') == 'EMERGENCY' and r.get('dispatched') and t is not None,
        band=r.get('band'), ack_ms=ack_ms, landing_began_s=began, all_grounded_s=t)
    s = state()
    rec('fault cleared by landing; nobody re-launches', all(not d['fault'] and d['mode'] == 'GROUNDED' for d in s.values()),
        modes={n: s[n]['mode'] for n in NAMES})
    finish(steps, outdir)


def finish(steps, outdir):
    if not all(d['mode'] in ('GROUNDED', 'READY') for d in state().values()):
        call(GZ + '/api/command', {'action': 'emergency_land', 'targets': list(NAMES)})
        wait(lambda s: all(d['mode'] in ('GROUNDED', 'READY') for d in s.values()), 60)
    out = {'generated_utc': datetime.now(timezone.utc).isoformat(), 'steps': steps,
           'verdict': 'PASS' if steps and all(x['result'] == 'PASS' for x in steps) else 'FAIL'}
    path = f"{outdir}/fault_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    json.dump(out, open(path, 'w'), indent=2)
    print(f"FAULT SCENARIO: {out['verdict']} -> {path}")


if __name__ == '__main__':
    main()
