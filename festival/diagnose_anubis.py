#!/usr/bin/env python3
"""One-aircraft HU/Gazebo diagnostic, safe by default.

Read-only: python3 diagnose_anubis.py
Explicit simulator-only flight test: python3 diagnose_anubis.py --test-flight
The flight test bypasses HU to isolate the simulator controller and always
attempts to land Anubis afterward. It must not be presented as HU control.
"""
import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE='http://127.0.0.1:8765'
LOG=Path.home()/'hu-telepresence-gazebo'/'logs'/'hu_real_decisions.jsonl'

def request(path, payload=None, timeout=4):
    body=None if payload is None else json.dumps(payload).encode('utf-8')
    req=urllib.request.Request(BASE+path,data=body,
          headers={'Content-Type':'application/json'},
          method='POST' if body is not None else 'GET')
    try:
        with urllib.request.urlopen(req,timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as e:
        try: d=json.loads(e.read().decode());msg=d.get('error',d)
        except Exception:msg=str(e)
        raise RuntimeError('HTTP '+str(e.code)+' '+str(msg)) from e

def get_drone():
    status=request('/api/state')
    data=next((d for d in status.get('drones',[]) if d.get('name')=='Anubis'),None)
    if not data: raise RuntimeError('Anubis absent from Gazebo supervisor roster')
    return data

def describe(d):
    print('Anubis: MODE='+str(d.get('mode'))+
          ' | z='+str(d.get('z'))+' m | target_z='+str((d.get('target') or ['?','?','?'])[2])+' m'+
          ' | telemetry='+str(d.get('telemetry'))+' | fault='+str(d.get('fault')))

def past_decisions():
    if not LOG.exists():
        print('No HU decision log yet:',LOG)
        return
    lines=LOG.read_text(errors='replace').splitlines()
    selected=[]
    for line in reversed(lines[-300:]):
        try:d=json.loads(line)
        except ValueError:continue
        searchable=str(d.get('utterance',''))+' '+str(d.get('targets',''))
        if 'anubis' in searchable.lower():selected.append(d)
        if len(selected)>=5:break
    if not selected:
        print('No recent HU commands referring to Anubis were logged.')
    for d in selected:
        print('HU:', d.get('utterance'), '|',d.get('action'), '|',d.get('band'),
              '| targets:',d.get('targets'),'| sent:',d.get('dispatched'),
              '| reason:',d.get('reason'),'| match scores:',d.get('per_drone'))

def flight_test():
    d=get_drone()
    if not d.get('telemetry'):
        print('NOT TESTED: Anubis odometry is stale; check Gazebo and supervisor.');return
    if d.get('fault'):
        print('NOT TESTED: Anubis is flagged FAULT. Clear that simulated fault in the supervisor dashboard first.');return
    if d.get('z',0)>0.5:
        print('NOT TESTED: Anubis already airborne. Land it before a fresh takeoff test.');return
    first=float(d.get('z',0));command_sent=False
    try:
        print('SIMULATOR-ONLY test (bypasses trained HU): sending Anubis takeoff via dashboard API.')
        print('Result:',request('/api/command',{'action':'takeoff','targets':['Anubis'],
                                                 'params':{'altitude':1.65}}))
        command_sent=True
        measurements=[]
        for i in range(8):
            time.sleep(1)
            d=get_drone(); measurements.append(float(d.get('z',0)))
            print('  t=%ss z=%sm mode=%s target=%s' %(i+1,d.get('z'),d.get('mode'),
                   (d.get('target') or ['?','?','?'])[2]))
        delta=max(measurements)-first
        if delta>=0.45:
            print('PASS: Anubis altitude increased by %.2fm. Simulator controller works; inspect HU match scores.'%delta)
        else:
            print('FAIL: Anubis altitude increased only %.2fm. Investigate motor topics, collisions and simulator physics.'%delta)
    finally:
        if command_sent:
            try:
                print('Returning Anubis to its pad:',request('/api/command',{'action':'land',
                                                        'targets':['Anubis'],'params':{}}))
            except Exception as exc:
                print('WARNING: Landing request failed:',exc)
                print('Use the simulation dashboard to land Anubis manually.')

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--test-flight',action='store_true',help='Operator-approved, simulator-only Anubis takeoff test, then land.')
    args=ap.parse_args()
    try:
        describe(get_drone());past_decisions()
        if args.test_flight:flight_test()
    except (RuntimeError,urllib.error.URLError,TimeoutError) as ex:
        ap.exit(1,'Diagnostics unavailable: '+str(ex)+'\n')
