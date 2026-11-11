#!/usr/bin/env python3
"""Diagnose all five Gazebo drone landings against HU decisions and measured altitude.

Default read-only. --recover issues a simulator-only landing request (NOT HU)
and observes telemetry. Never labels a command as landed without telemetry.
"""
import argparse
import json
from pathlib import Path
import time
import urllib.error
import urllib.request

URL = 'http://127.0.0.1:8765'
LOG = Path.home() / 'hu-telepresence-gazebo' / 'logs' / 'hu_real_decisions.jsonl'
NAMES = ('Horus','Ra','Thoth','Anubis','Osiris')

def req(path, payload=None):
    data = None if payload is None else json.dumps(payload).encode('utf-8')
    request = urllib.request.Request(URL + path, data=data,
                  headers={'Content-Type':'application/json'},
                  method='POST' if data is not None else 'GET')
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)

def last_hu():
    if not LOG.exists():
        print('HU DECISIONS: log not found; may be a supervisor-only test.')
        return
    lines = LOG.read_text(errors='replace').splitlines()
    hits = []
    for line in reversed(lines[-350:]):
        try: entry = json.loads(line)
        except ValueError: continue
        action = str(entry.get('action', '')).upper()
        utterance = str(entry.get('utterance', '')).lower()
        if action == 'LAND' or 'land' in utterance or 'khalas' in utterance:
            hits.append(entry)
        if len(hits) >= 5: break
    print('RECENT HU LANDING DECISIONS:')
    if not hits: print('  None. A landing command may not have reached HU.')
    for d in hits:
        print(' ',d.get('timestamp_utc', '?'),
              repr(d.get('utterance','')), 'band=',d.get('band'),
              'targets=',d.get('targets'), 'sent=',d.get('dispatched'),
              'reason=',d.get('reason',''))

def show(state, elapsed):
    print('\nMEASURED GAZEBO ALTITUDE after %ds:' % elapsed)
    print('%-9s %-12s %8s %9s  %s' % ('Name','Mode','Z (m)','Target','Telemetry'))
    for n in NAMES:
        drone = next((d for d in state.get('drones',[]) if d.get('name')==n), None)
        if drone is None:
            print('%-9s NOT FOUND' % n)
            continue
        tg = drone.get('target') or ['?','?','?']
        print('%-9s %-12s %8s %9s  %s' % (n,drone.get('mode','?'),drone.get('z','?'),
            tg[2],drone.get('telemetry')))

def report(before, after):
    pre={d['name']:d for d in before.get('drones',[])}
    post={d['name']:d for d in after.get('drones',[])}
    ground=[]; stalled=[]; no_odom=[]
    for n in NAMES:
        if n not in post or not post[n].get('telemetry'):
            no_odom.append(n)
        elif post[n].get('mode')=='GROUNDED' and float(post[n].get('z',100))<=.32:
            ground.append(n)
        else:
            height0=float(pre.get(n,{}).get('z',0)); height1=float(post[n].get('z',0))
            stalled.append((n,round(height0-height1,2),post[n].get('mode'),height1))
    print('\nVERDICT:')
    print(' VERIFIED GROUNDED:', ', '.join(ground) if ground else '(none)')
    if stalled:
        for n,delta,mode,z in stalled:
            print(' NOT VERIFIED:',n, 'altitude drop=%.2fm'%delta,'current z=%.2fm'%z,'mode=',mode)
    if no_odom: print(' NO FRESH ODOMETRY:', ', '.join(no_odom))
    print('DISPATCH IS NOT PROOF OF LANDING. Analyze actual telemetry before modifying physics.')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recover',action='store_true',help='Send simulator-only land to all five; bypasses HU for diagnosis.')
    args=parser.parse_args()
    try:
        last_hu()
        initial = req('/api/state')
        show(initial,0)
        if args.recover:
            print('\nSIMULATOR-ONLY recovery (not HU inference):')
            print(req('/api/command',{'action':'land','targets':list(NAMES),'params':{}}))
            for seconds in (4,8,13):
                time.sleep(seconds - (0 if seconds==4 else previous))
                state = req('/api/state')
                show(state,seconds)
                previous = seconds
            report(initial,state)
        else:
            print('\nRead-only check complete. To observe recovery: add --recover')
    except (OSError, ValueError, urllib.error.URLError) as exc:
        parser.exit(1,'Cannot read Gazebo supervisor: %s\n'%exc)
