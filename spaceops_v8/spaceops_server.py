#!/usr/bin/env python3
"""RSCL SpaceOps V7: observer/recorder for the existing HU Gazebo Festival.

- No modification to trained HU, inference routes, or Gazebo controllers.
- Observes the existing Gazebo supervisor at http://127.0.0.1:8765/api/state.
- Logs sampled telemetry + simulator events and HU decisions with UTC timestamps.
- Simulator-only fault lab: operator-initiated, disabled unless safe hover + separation.
- Serves only 127.0.0.1 (do not expose a fault-control endpoint publicly).
"""
from __future__ import annotations
import argparse
import csv
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import math
import os
from pathlib import Path
import re
import secrets
import sys
import threading
import time
from urllib import error, request
from urllib.parse import urlparse
import zipfile
from swarmchat import SwarmChat
from swarmchat_hybrid import HybridChat
import tempfile
import spaceops_panels as panels

ROOT = Path(__file__).resolve().parent
HOME_BASE = Path.home() / 'hu-telepresence-gazebo'
NAMES = ('Horus', 'Ra', 'Thoth', 'Anubis', 'Osiris')
COLORS = {'Horus':'#EFC36F','Ra':'#52E2EA','Thoth':'#B69CFF','Anubis':'#61EBAF','Osiris':'#FF91AE'}
GAZEBO = 'http://127.0.0.1:8765'
SAMPLE_INTERVAL = .25
LOG_FIELDS = ('utc','epoch_ms','elapsed_s','vehicle','mode','fault','telemetry_fresh',
              'x_m','y_m','z_m','vx_est_mps','vy_est_mps','vz_est_mps','ground_speed_est_mps',
              'yaw_deg','target_x_m','target_y_m','target_z_m','battery_sim_pct',
              'closest_aircraft','closest_3d_m','min_fleet_3d_m')


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')


def json_req(url, obj=None, timeout=2.5):
    body=None if obj is None else json.dumps(obj).encode('utf-8')
    r=request.Request(url,data=body,headers={'Content-Type':'application/json'},
                      method='POST' if obj is not None else 'GET')
    with request.urlopen(r,timeout=timeout) as response:
        return json.load(response)


def read_tail(path: Path, maximum=16, bytes_limit=200_000):
    try:
        with path.open('rb') as f:
            f.seek(0,os.SEEK_END);size=f.tell();f.seek(max(0,size-bytes_limit))
            data=f.read().decode('utf-8','replace')
        lines=data.splitlines()
        if size>bytes_limit and lines: lines=lines[1:]
        return [json.loads(line) for line in lines[-maximum:] if line.strip()]
    except (OSError,ValueError,json.JSONDecodeError):
        return []


class Recorder:
    def __init__(self,root:Path, gazebo_url:str=GAZEBO, interval=.25):
        self.root=root;self.root.mkdir(parents=True,exist_ok=True)
        self.gazebo_url=gazebo_url.rstrip('/'); self.interval=max(.2,float(interval))
        self.mutex=threading.RLock();self.created=time.monotonic();self.online=False
        self.state=None;self.error='Waiting for Gazebo supervisor'
        self.recent=deque(maxlen=150);self.history=deque(maxlen=240)
        # Per-aircraft health curves: bounded 2-minute window at the 4 Hz recorder rate.
        self.health={n:deque(maxlen=480) for n in NAMES}
        self.events=deque(maxlen=100);self.event_keys=deque(maxlen=300)
        self.prev={};self.battery={n:100.0 for n in NAMES}
        self.last_seen={};self.last_poll=0.;self.last_record=0.
        self.token=secrets.token_urlsafe(22)
        self.chat=SwarmChat(self.root)
        self.hybrid=HybridChat(self.chat)
        # Drones call out to each other during flight. Templated from live telemetry (source FIELD_CALLOUT);
        # never actuates. Disable with HU_FIELD_CALLOUTS=0.
        if os.environ.get('HU_FIELD_CALLOUTS','1')!='0':
            from field_callouts import FieldCallouts
            self.callouts=FieldCallouts(self.chat,min_gap=.35).start()
        self.root.mkdir(parents=True,exist_ok=True)
        self.telemetry_file=self.root/'flight_telemetry.jsonl'
        self.events_file=self.root/'flight_events.jsonl'
        self.drone_files={n:self.root/(n.lower()+'_flight.csv') for n in NAMES}
        self.decision_file=HOME_BASE/'logs'/'hu_real_decisions.jsonl'
        self.latency_file=HOME_BASE/'logs'/'hu_voice_to_motion.jsonl'
        self._last_log_event=None
        self._decision_offset=self.decision_file.stat().st_size if self.decision_file.exists() else 0
        self._gazebo_events_seeded=False
        # Per-aircraft telemetry-flag transitions (recorded as LINK_STALE / LINK_FRESH events).
        self.link_fresh={}
        self._panel_cache={}
        self.stop=False
        # No historical import. Each SpaceOps session begins with new events only.


    def append_event(self,kind:str,description:str,targets=None,details=None,write=True):
        event={'utc':utcnow(),'session_id':self.root.name,'kind':kind,'message':str(description),
               'targets':targets or [], 'details':details or {}}
        with self.mutex:
            self.events.appendleft(event)
            if write:
                with self.events_file.open('a',encoding='utf8') as f:
                    f.write(json.dumps(event,ensure_ascii=False)+'\n')
        return event

    def _insert_decision(self,item,write=True):
        label=item.get('utterance') or item.get('normalized_utterance') or '(confirmation)'
        band=item.get('band','EVENT')
        evt={'utc':utcnow(),'session_id':self.root.name,'kind':'HU_'+str(band),
             'message':str(label), 'targets':item.get('targets') or [],
             'details':{'action':item.get('action'),'band':band,
                        'confidence':item.get('confidence'), 'dispatched':item.get('dispatched'),
                        'source_utc':item.get('timestamp_utc'),
                        'hu_session_id':item.get('session_id'),
                        'decision_source':item.get('decision_source'),
                        'reason':item.get('reason'),'cmd_id':item.get('cmd_id')}}
        with self.mutex:
            self.events.appendleft(evt)
            if write:
                with self.events_file.open('a',encoding='utf8') as f:
                    f.write(json.dumps(evt,ensure_ascii=False)+'\n')

    def _track_decisions(self):
        # Read new appended bytes ONLY. Historical HU decisions are never
        # imported into a fresh SpaceOps flight session.
        try:
            with self.decision_file.open('rb') as f:
                size=f.seek(0,os.SEEK_END)
                if size<self._decision_offset:self._decision_offset=0  # rotation
                f.seek(self._decision_offset)
                raw=f.read(256_000)
                if not raw:return
                lines=raw.split(b'\n')
                complete=lines[:-1]
                self._decision_offset+=sum(len(line)+1 for line in complete)
        except OSError:
            return
        for line in complete:
            if not line.strip():continue
            try:
                item=json.loads(line)
                if not isinstance(item,dict):continue
            except (ValueError,UnicodeError):continue
            self._insert_decision(item,write=True)

    def _persist_row(self,row):
        with self.telemetry_file.open('a',encoding='utf8') as f:
            f.write(json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n')
        csvpath=self.drone_files[row['vehicle']]
        new=not csvpath.exists() or csvpath.stat().st_size==0
        with csvpath.open('a',newline='',encoding='utf8') as f:
            w=csv.DictWriter(f,fieldnames=LOG_FIELDS)
            if new:w.writeheader()
            w.writerow({k:row.get(k) for k in LOG_FIELDS})

    def poll(self):
        try:
            data=json_req(self.gazebo_url+'/api/state',timeout=1.8)
            if not isinstance(data.get('drones'),list):raise ValueError('Gazebo state lacks drones')
        except (OSError,ValueError,error.URLError) as ex:
            with self.mutex:
                if self.online:self.append_event('SUPERVISOR_LOST','Telemetry endpoint became unavailable')
                self.online=False;self.error=str(ex)
            return
        now=time.monotonic();stamp=utcnow(); epoch_ms=int(time.time()*1000)
        clean=[];rows=[];samples=[]
        for entry in data['drones']:
            n=entry.get('name')
            if n not in NAMES:continue
            d=dict(entry)
            xyz=(float(d.get('x',0)),float(d.get('y',0)),float(d.get('z',0)))
            previous=self.prev.get(n);v=[0.,0.,0.]
            if previous:
                dt=now-previous[0]
                if .01<dt<3.0:v=[(xyz[k]-previous[1][k])/dt for k in range(3)]
            self.prev[n]=(now,xyz)
            sp=math.hypot(v[0],v[1])
            fresh=bool(d.get('telemetry'))
            mode=str(d.get('mode','UNKNOWN'))
            # Synthetic battery accounting only; NOT measured voltage, current or charge.
            if fresh and self.last_poll:
                dt=min(1.0,max(0,now-self.last_poll))
                if mode not in ('GROUNDED','READY','WAITING'):
                    self.battery[n]=max(0,self.battery[n]-dt*((1.15+0.75*sp)/60.0))
            d.update({'battery_sim_pct':round(self.battery[n],1),
                      'vx_est':round(v[0],2),'vy_est':round(v[1],2),'vz_est':round(v[2],2),
                      'ground_speed_est':round(sp,2)})
            clean.append(d)
        closest={}
        mini=float('inf');pair=[]
        for a in clean:
            near=float('inf');other='—'
            for b in clean:
                if a['name']==b['name'] or not a.get('telemetry') or not b.get('telemetry'):continue
                dist=math.dist((a['x'],a['y'],a['z']),(b['x'],b['y'],b['z']))
                if dist<near: near=dist;other=b['name']
                if dist<mini:mini=dist;pair=[a['name'],b['name']]
            closest[a['name']]=(other,None if not math.isfinite(near) else round(near,3))
        min_sep=round(mini,3) if math.isfinite(mini) else None
        safety=('TELEMETRY LOST' if any(not d.get('telemetry') for d in clean)
                else 'SEPARATION ALERT' if min_sep is not None and min_sep<.85
                else 'WATCH' if min_sep is not None and min_sep<1.15
                else 'CLEAR')
        for d in clean:
            tgt=d.get('target') or [None,None,None]
            if len(tgt)<3:tgt=[None,None,None]
            peer,near=closest[d['name']]
            row={'utc':stamp,'epoch_ms':epoch_ms,'elapsed_s':round(now-self.created,3),
                 'vehicle':d['name'],'mode':d.get('mode'),'fault':bool(d.get('fault')),
                 'telemetry_fresh':bool(d.get('telemetry')),
                 'x_m':d.get('x'),'y_m':d.get('y'),'z_m':d.get('z'),
                 'vx_est_mps':d['vx_est'],'vy_est_mps':d['vy_est'],'vz_est_mps':d['vz_est'],
                 'ground_speed_est_mps':d['ground_speed_est'],'yaw_deg':d.get('yaw'),
                 'target_x_m':tgt[0],'target_y_m':tgt[1],'target_z_m':tgt[2],
                 'battery_sim_pct':d['battery_sim_pct'],'closest_aircraft':peer,
                 'closest_3d_m':near,'min_fleet_3d_m':min_sep}
            rows.append(row)
        for d in clean:
            fresh=bool(d.get('telemetry'));was=self.link_fresh.get(d['name'])
            self.link_fresh[d['name']]=fresh
            if was is not None and was!=fresh:
                self.append_event('LINK_FRESH' if fresh else 'LINK_STALE',
                                  d['name']+(' telemetry fresh again' if fresh else ' telemetry flag stale (supervisor watchdog)'),
                                  [d['name']])
        for event in data.get('events',[]):
            sig=json.dumps(event,sort_keys=True)
            with self.mutex:
                if sig in self.event_keys:continue
                self.event_keys.append(sig)
            if self._gazebo_events_seeded:
                self.append_event('GZ_'+str(event.get('kind','EVENT')),
                                  event.get('message','Gazebo event'),event.get('targets',[]))
        self._gazebo_events_seeded=True
        with self.mutex:
            if not self.online:self.append_event('SUPERVISOR_ONLINE','Live Gazebo telemetry connected')
            prev_safety=getattr(self,'safety_status',None)
            self.online=True;self.error='';self.last_poll=now;self.last_record=time.time()
            self.safety_status=safety;self.min_sep=min_sep;self.closest_pair=pair
            self.state={'world':data.get('world','Gazebo'),'drones':clean,
                        'safety_status':safety,'min_sep_m':min_sep,'closest_pair':pair,
                        'max_telemetry_age_s':round(self.interval,2),
                        'timestamp_utc':stamp,'sample_hz_nominal':1/self.interval,
                        'battery_provenance':'SIMULATED ESTIMATE (mode and displacement based)',
                        'velocity_provenance':'ESTIMATED from sampled positions'}
            self.history.append({'t':round(now-self.created,2),'min_sep':min_sep,
                                 'alt_avg':round(sum(float(x.get('z',0)) for x in clean)/max(1,len(clean)),2),
                                 'poses':{d['name']:[round(float(d['x']),3),round(float(d['y']),3),round(float(d['z']),3)]
                                          for d in clean if d.get('telemetry')}})
            self.recent.extend(rows)
            t_rel=round(now-self.created,2)
            for d in clean:
                fresh=bool(d.get('telemetry'))
                tgt=d.get('target') or []
                err=None
                if fresh and len(tgt)>=3 and all(isinstance(v,(int,float)) for v in tgt):
                    err=round(math.dist((float(d['x']),float(d['y']),float(d['z'])),tuple(float(v) for v in tgt)),3)
                # Stale telemetry is recorded as a GAP (None), never as the last known value.
                self.health[d['name']].append({'t':t_rel,'fresh':fresh,'mode':str(d.get('mode')),'fault':bool(d.get('fault')),
                    'z':round(float(d['z']),3) if fresh else None,
                    'speed':d['ground_speed_est'] if fresh else None,
                    'track_err':err,
                    'nearest':closest[d['name']][1] if fresh else None,
                    'nearest_peer':closest[d['name']][0] if fresh else None,
                    'battery_sim':d['battery_sim_pct']})
            if prev_safety!=safety and prev_safety is not None:
                self.append_event('SAFETY_'+safety.replace(' ','_'),
                                  'Minimum observed separation: '+str(min_sep)+' m',pair)
            for row in rows:self._persist_row(row)
        self._track_decisions()

    def loop(self):
        while not self.stop:
            st=time.monotonic()
            try:self.poll()
            except Exception as ex:
                with self.mutex:self.error='Recorder error: '+repr(ex)
            time.sleep(max(.03,self.interval-(time.monotonic()-st)))

    def public_status(self):
        with self.mutex:
            return {'online':self.online, 'error':self.error,'state':self.state,
                    'events':list(self.events)[:28], 'history':list(self.history)[-160:],
                    'session_id':self.root.name,'samples':len(self.recent),
                    'session_elapsed_s':round(time.monotonic()-self.created,1),
                    'csv_count':sum(1 for x in self.drone_files.values() if x.exists()),
                    'logging':True,'read_only_default':True}

    def health_series(self):
        with self.mutex:
            return {'session_id':self.root.name,'window_s':round(480*self.interval,1),
                    'sample_hz_nominal':1/self.interval,
                    'series':{n:list(self.health[n]) for n in NAMES},
                    'provenance':{'z':'Gazebo odometry (supervisor /api/state)',
                                  'speed':'ESTIMATED: finite difference of sampled positions',
                                  'track_err':'3D distance from observed position to the supervisor setpoint',
                                  'nearest':'3D distance to the closest aircraft with fresh telemetry',
                                  'battery_sim':'SIMULATED ESTIMATE - not measured voltage or charge',
                                  'gaps':'None = telemetry stale at that sample'},
                    'limits':{'proximity_hold_m':.82,'separation_alert_m':.85,'advisory_m':1.15}}

    def action(self,action,vehicle):
        if action not in ('inject_fault','test_rejection','recover'):
            raise ValueError('Unsupported fault lab operation')
        if vehicle not in NAMES:raise ValueError('Unknown aircraft')
        with self.mutex:
            if not self.online or self.state is None:raise ValueError('No live Gazebo telemetry')
            st=self.state
            d=next(x for x in st['drones'] if x['name']==vehicle)
            if not d.get('telemetry'):raise ValueError('Aircraft telemetry unavailable')
            if action=='inject_fault':
                if d.get('fault') or d.get('mode')!='HOVER' or float(d.get('z',0))<.8:
                    raise ValueError('Fault injection allowed only in stable, airborne HOVER')
                if st.get('min_sep_m') is None or st['min_sep_m']<1.2:
                    raise ValueError('Separation must be >=1.20 m before injection')
            elif action=='test_rejection' and not d.get('fault'):
                raise ValueError('Inject a command-link fault first')
            elif action=='recover' and not d.get('fault'):
                raise ValueError('Selected aircraft has no active fault')
        payload={'action':{'inject_fault':'fault','test_rejection':'hover','recover':'clear_fault'}[action],
                 'targets':[vehicle]}
        try:
            result=json_req(self.gazebo_url+'/api/command',payload,timeout=2.5)
            if action=='test_rejection':
                self.append_event('REJECTION_TEST_FAILED','Faulted aircraft unexpectedly accepted HOVER',
                                  [vehicle],{'raw':result})
                return {'ok':False,'observed_refusal':False,'detail':'Unexpected acceptance; inspect safety guard'}
            label={'inject_fault':'SIMULATED_LINK_FAULT','recover':'CLEAR_LINK_FAULT'}[action]
            self.append_event(label,label.replace('_',' ').title(),[vehicle],{'raw':result})
            return {'ok':True,'action':action,'vehicle':vehicle,'result':result,
                    'warning':'Controller acknowledgment only; verify telemetry for actual safe state'}
        except error.HTTPError as ex:
            body=ex.read().decode('utf-8','replace')[:400]
            if action=='test_rejection' and ex.code==400:
                self.append_event('REJECTION_VERIFIED','Faulted aircraft refused HOVER command',
                                  [vehicle],{'http':ex.code,'detail':body})
                return {'ok':True,'observed_refusal':True,'status':400,'detail':body}
            raise ValueError('Gazebo command rejected HTTP '+str(ex.code)+': '+body)
        except error.URLError as ex:
            raise ValueError('Gazebo supervisor unavailable: '+str(ex.reason))

    # ---- V10 read-only analysis panels (bounded; files only read, never written) ----
    def _cached(self,key,ttl,fn):
        now=time.monotonic();hit=self._panel_cache.get(key)
        if hit and now-hit[0]<ttl:return hit[1]
        val=fn();self._panel_cache[key]=(now,val);return val

    def formations(self):
        cat=self._cached('catalog',15.0,lambda:panels.load_formation_catalog(
            HOME_BASE/'festival'/'formation_catalog.json',HOME_BASE/'acceptance'/'results'))
        with self.mutex:
            snap=json.loads(json.dumps(self.state)) if self.state else None
            online=self.online
        live=panels.live_formation(snap if online else None)
        live['online']=online
        return {'session_id':self.root.name,'catalog':cat,'live':live,'read_only':True}

    def latency(self):
        return self._cached('latency',1.5,lambda:panels.latency_timeline(self.latency_file,self.decision_file))

    def faults(self):
        def build():
            events=panels.tail_jsonl(self.events_file,maximum=4000,bytes_limit=3_000_000)
            out=panels.fault_timeline(events)
            out['session_id']=self.root.name
            return out
        return self._cached('faults',1.0,build)

    def export_ardupilot(self):
        """Zip of ArduPilot DataFlash-FORMAT .BIN files (SIMULATED Gazebo telemetry) for THIS session."""
        exporter=[ROOT.parent/'scripts'/'export_ardupilot_logs.py',HOME_BASE/'scripts'/'export_ardupilot_logs.py']
        with tempfile.TemporaryDirectory(prefix='spaceops_bin_') as tmp:
            # Copy under the lock so no CSV row is half-written; convert outside the lock.
            with self.mutex:
                snap=panels.snapshot_session([self.events_file,*self.drone_files.values()],Path(tmp),self.root.name)
            raw,summary=panels.ardupilot_zip(self.root.name,snap,exporter)
        return raw,summary

    def export(self):
        # Snapshot under lock prevents zip entries being read while recorder writes them.
        with self.mutex:
            buf=io.BytesIO()
            with zipfile.ZipFile(buf,'w',compression=zipfile.ZIP_DEFLATED) as z:
                for p in [self.telemetry_file,self.events_file,self.chat.path,*self.drone_files.values()]:
                    if p.exists():z.write(p,arcname=p.name)
                # Do not leak/mix earlier flight sessions into this export.
                # HU decisions ingested during this session are in flight_events.jsonl.
                metadata={'format':'HU-SpaceOps-V9-RELIABILITY','recording_type':'SIMULATED GAZEBO GROUND-STATION',
                          'native_ardupilot_bin':False,'session_id':self.root.name,
                          'nominal_sampling_hz':1/self.interval,
                          'battery_provenance':'Synthetic estimate; NOT measured battery',
                          'speed_provenance':'Finite differences of 4Hz position samples',
                          'minimum_separation':'3D Euclidean position distance, sampled; NOT collision-free guarantee',
                          'flight_controller':'Existing Gazebo supervisor, unchanged',
                          'swarmchat':'Evidence-grounded deterministic logical actors (rule/template based, no LLM, no learning); conversational personas change wording only; read-only; no flight dispatch'}
                z.writestr('metadata.json',json.dumps(metadata,indent=2))
            return buf.getvalue()

class Handler(BaseHTTPRequestHandler):
    rec:Recorder=None
    def handle_one_request(self):
        # Browser navigation/aborted fetches can close a socket while a response
        # is being written. This is a transport disconnect, not an app failure.
        # Do not suppress unrelated exceptions (including programmer errors).
        try:
            return super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True

    def log_message(self,*args):pass
    def _headers(self,status=200,ctype='application/json',length=None,disposition=None):
        self.send_response(status)
        self.send_header('Content-Type',ctype)
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','SAMEORIGIN')
        self.send_header('Referrer-Policy','no-referrer')
        if length is not None:self.send_header('Content-Length',str(length))
        if disposition:self.send_header('Content-Disposition',disposition)
        self.end_headers()
    def send_data(self,status,obj):
        raw=json.dumps(obj).encode('utf8');self._headers(status,length=len(raw));self.wfile.write(raw)
    def do_GET(self):
        path=urlparse(self.path).path
        if path=='/api/ops/state':return self.send_data(200,self.rec.public_status())
        if path=='/api/ops/health':return self.send_data(200,self.rec.health_series())
        if path=='/api/ops/session':return self.send_data(200,{'token':self.rec.token,'session_id':self.rec.root.name})
        if path=='/api/ops/chat/history':return self.send_data(200,{'messages':self.rec.chat.history(), 'read_only':True})
        if path=='/api/ops/formations':return self.send_data(200,self.rec.formations())
        if path=='/api/ops/latency':return self.send_data(200,self.rec.latency())
        if path=='/api/ops/faults':return self.send_data(200,self.rec.faults())
        if path=='/api/ops/export/ardupilot':
            try:raw,_=self.rec.export_ardupilot()
            except LookupError as ex:return self.send_data(409,{'ok':False,'error':'No data yet: '+str(ex)})
            except (OSError,ValueError) as ex:return self.send_data(500,{'ok':False,'error':'ArduPilot export failed: '+str(ex)[:200]})
            name=re.sub(r'[^A-Za-z0-9_-]','',self.rec.root.name)+'_ardupilot_SIMULATED.zip'
            self._headers(200,'application/zip',len(raw),'attachment; filename="'+name+'"')
            return self.wfile.write(raw)
        if path=='/api/ops/export':
            raw=self.rec.export();self._headers(200,'application/zip',len(raw))
            return self.wfile.write(raw)
        if path in ('/','/index.html','/rscl_logo.png','/orbital_backdrop.svg'):
            fp=ROOT/('rscl_logo.png' if path=='/rscl_logo.png' else 'orbital_backdrop.svg' if path=='/orbital_backdrop.svg' else 'index.html')
            raw=fp.read_bytes()
            self._headers(200,'image/png' if fp.suffix=='.png' else 'image/svg+xml' if fp.suffix=='.svg' else 'text/html; charset=utf-8',len(raw))
            return self.wfile.write(raw)
        self.send_data(404,{'error':'Not found'})
    def do_POST(self):
        path=urlparse(self.path).path
        if path not in ('/api/ops/fault','/api/ops/chat','/api/ops/chat/agent'):return self.send_data(404,{'error':'Not found'})
        origin=self.headers.get('Origin')
        if origin and origin not in ('http://127.0.0.1:'+str(self.server.server_port),'http://localhost:'+str(self.server.server_port)):
            return self.send_data(403,{'error':'Origin rejected'})
        if not secrets.compare_digest(self.headers.get('X-RSCL-CSRF',''),self.rec.token):
            return self.send_data(403,{'error':'Operator session required'})
        try:
            ln=int(self.headers.get('Content-Length','0'))
            if ln<1 or ln>1024:raise ValueError('Invalid request size')
            data=json.loads(self.rfile.read(ln))
            if path=='/api/ops/chat/agent':
                # Team-strategy messages from acceptance/strategist.py (deterministic policy, local, CSRF-gated).
                # Text only: this route can never actuate.
                sp=str(data.get('speaker',''))
                if sp not in NAMES and sp!='MAAT':raise ValueError('unknown speaker')
                intent=data.get('intent') if data.get('intent') in ('team_strategy','field_callout','team_debrief') else 'team_strategy'
                rank=''
                m=self.rec.chat.append(sp,'DRONE_AGENT',str(data.get('text',''))[:400],
                    {**(data.get('evidence') or {}),'kind':'TEAM_STRATEGY','rank':rank,'note':'deterministic scoring policy on live positions; language templated'},
                    data.get('conversation_id'),to=str(data.get('to') or 'TEAM'),intent=intent,source='DETERMINISTIC_AGENT_POLICY')
                return self.send_data(200,{'ok':True,'id':m['id']})
            if path=='/api/ops/chat':
                with self.rec.mutex:
                    snapshot=json.loads(json.dumps(self.rec.state)) if self.rec.state else None
                    recent=list(self.rec.events)[:35]
                    online=self.rec.online
                    # Copied per-aircraft health history (2 min window) for trend answers; read-only.
                    health={n:[dict(s) for s in self.rec.health[n]] for n in NAMES}
                # Hybrid: learned intent routing + local SmolLM2 wording; falls back to the deterministic
                # engine automatically when the chat service (8767) is unavailable. Never actuates.
                replies=self.rec.hybrid.ask(data.get('text'),data.get('recipient'),snapshot,recent,online,health=health)
                return self.send_data(200,{'ok':True,'messages':replies,'read_only':True})
            result=self.rec.action(data.get('action'),data.get('vehicle'))
            return self.send_data(200,result)
        except (ValueError,KeyError,TypeError,json.JSONDecodeError) as ex:
            return self.send_data(409,{'ok':False,'error':str(ex)})


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--port',type=int,default=8766)
    ap.add_argument('--gazebo',default=GAZEBO)
    ap.add_argument('--sample-ms',type=int,default=250)
    args=ap.parse_args()
    root=HOME_BASE/'logs'/'spaceops_sessions'/datetime.now().strftime('SO-%Y%m%d-%H%M%S')
    rec=Recorder(root,args.gazebo,args.sample_ms/1000)
    Handler.rec=rec
    threading.Thread(target=rec.loop,daemon=True).start()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    print('RSCL SPACEOPS + SWARMCHAT V8 active: http://localhost:'+str(args.port),flush=True)
    print('Observer-only by default. Operates independently of HU voice and Gazebo controllers.',flush=True)
    print('Logs: '+str(root),flush=True)
    try:server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:pass
    finally:rec.stop=True;server.server_close()

if __name__=='__main__':main()
