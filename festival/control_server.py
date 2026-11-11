#!/usr/bin/env python3
"""Gazebo Harmonic HU Festival supervisory control + honest demo dashboard.
Uses native Gazebo Transport Python API (apt: python3-gz-transport13).
No learned HU checkpoint is bundled. Existing HU can POST structured dispatch commands.
"""
import json, math, os, re, signal, threading, time
from collections import deque
from dataclasses import dataclass, field
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

try:
    from gz.transport13 import Node
    from gz.msgs10.boolean_pb2 import Boolean
    from gz.msgs10.odometry_pb2 import Odometry
    from gz.msgs10.twist_pb2 import Twist
except ImportError as ex:
    raise SystemExit('Gazebo Python bindings missing. Run: sudo apt install -y python3-gz-transport13 python3-gz-msgs10\n'+str(ex))

NAMES=['Horus','Ra','Thoth','Anubis','Osiris']
START={'Horus':(-2.2,-1.5),'Ra':(0,-1.5),'Thoth':(2.2,-1.5),'Anubis':(-1.1,1.1),'Osiris':(1.1,1.1)}
COLORS={'Horus':'#EBC35A','Ra':'#3BE2F2','Thoth':'#B39AFF','Anubis':'#5AEBAD','Osiris':'#F18BA5'}

@dataclass
class Drone:
    name:str
    x:float=0.; y:float=0.; z:float=0.; vx:float=0.;vy:float=0.;vz:float=0.;yaw:float=0.
    target_x:float=0.;target_y:float=0.;target_z:float=0.
    last_seen:float=0.
    mode:str='WAITING'
    fault:bool=False
    ever_flew:bool=False
    init:bool=False

class Fleet:
    def __init__(self):
        self.node=Node(); self.lock=threading.RLock();self.logs=deque(maxlen=120)
        self.dr={name:Drone(name,target_x=START[name][0],target_y=START[name][1],target_z=.55) for name in NAMES}
        self.twist={};self.enables={}
        self.quit=False
        self.last_logfile=Path.home()/'hu-telepresence-gazebo'/'logs'/'hu_festival_events.jsonl'
        self.last_logfile.parent.mkdir(parents=True,exist_ok=True)
        for name in NAMES:
            self.twist[name]=self.node.advertise(f'/{name}/gazebo/command/twist', Twist)
            self.enables[name]=self.node.advertise(f'/{name}/enable', Boolean)
            cb=lambda msg, n=name:self.on_odom(n,msg)
            if not self.node.subscribe(Odometry, f'/model/{name.lower()}/odometry', cb):
                raise RuntimeError('Could not subscribe to odometry for '+name)
        self.event('SYSTEM','Gazebo transport connected. Awaiting odometry.', [])
        self.worker=threading.Thread(target=self.loop,daemon=True);self.worker.start()

    def event(self,kind,message,targets=None):
        entry={'time':time.strftime('%H:%M:%S'),'kind':kind,'message':message,'targets':targets or []}
        with self.lock:
            self.logs.appendleft(entry)
            with self.last_logfile.open('a') as f:f.write(json.dumps(entry)+'\n')

    def on_odom(self,name,msg):
        with self.lock:
            d=self.dr[name]
            d.x=msg.pose.position.x;d.y=msg.pose.position.y;d.z=msg.pose.position.z
            d.vx=msg.twist.linear.x;d.vy=msg.twist.linear.y;d.vz=msg.twist.linear.z
            q=msg.pose.orientation
            d.yaw=math.atan2(2*(q.w*q.z+q.x*q.y),1-2*(q.y*q.y+q.z*q.z))
            d.last_seen=time.monotonic()
            if not d.init:
                d.target_x,d.target_y,d.target_z=d.x,d.y,d.z
                d.init=True
                d.mode='READY'
                self.event('TELEMETRY',f'{name} pose received', [name])

    def fresh(self,d):return d.init and (time.monotonic()-d.last_seen < 2.0)

    def publish(self,name,x=0.,y=0.,z=0.,rz=0.,enable=True):
        if enable:
            m=Boolean();m.data=True;self.enables[name].publish(m)
        t=Twist();t.linear.x=float(x);t.linear.y=float(y);t.linear.z=float(z);t.angular.z=float(rz)
        self.twist[name].publish(t)

    @staticmethod
    def clamp(x,limit):return max(-limit,min(limit,x))

    def loop(self):
        while not self.quit:
            with self.lock:
                for name,d in self.dr.items():
                    if not self.fresh(d):
                        # Do not send new motion when the telemetry watchdog expires.
                        self.publish(name,0,0,0,enable=False)
                        continue
                    if d.mode in ('READY','GROUNDED','WAITING'):
                        continue
                    if d.mode=='FAULT':
                        self.publish(name,0,0,0)
                        continue
                    if d.mode=='LANDING' and d.z<=.20 and abs(d.vz)<.30:
                        self.publish(name,0,0,0)
                        dis=Boolean();dis.data=False;self.enables[name].publish(dis)
                        d.mode='GROUNDED';d.target_z=.10
                        self.event('LANDED',f'{name} landed; motors disarmed',[name]);continue
                    ex=d.target_x-d.x;ey=d.target_y-d.y;ez=d.target_z-d.z
                    # Yaw compensation: motor controller accepts body-frame linear velocity.
                    vx=self.clamp(.80*ex-.16*d.vx,.62)
                    vy=self.clamp(.80*ey-.16*d.vy,.62)
                    vz=self.clamp(1.05*ez-.22*d.vz,.45)
                    if d.mode=='LANDING': vz=max(-.32,min(.10,vz))
                    co=math.cos(d.yaw);si=math.sin(d.yaw)
                    bx=co*vx+si*vy;by=-si*vx+co*vy
                    self.publish(name,bx,by,vz)
                    if d.mode=='TAKEOFF' and abs(ez)<.13 and abs(d.vz)<.18:
                        d.mode='HOVER';self.event('HOVER',f'{name} reached its altitude target',[name])
            time.sleep(.10)

    def command(self,action,targets=None,params=None,source='DASHBOARD'):
        params=params or {}; targets=targets or []
        if isinstance(targets,str):targets=[targets]
        targets=[str(t).title() for t in targets]
        if action not in ('takeoff','hover','land','move','formation','fault','clear_fault','emergency_land'):
            raise ValueError('Unsupported action')
        if action=='emergency_land':targets=list(NAMES);action='land'
        if action=='formation':return self.formation(params.get('shape','v'),source)
        if action=='fault' and len(targets)!=1:raise ValueError('Fault simulation requires one named drone')
        if not targets or any(n not in self.dr for n in targets):raise ValueError('Specify valid drone targets')
        with self.lock:
            if action not in ('clear_fault','land'):
                blocked=[n for n in targets if self.dr[n].fault]
                if blocked:raise ValueError('Simulated command-link fault active: '+','.join(blocked))
            for name in targets:
                d=self.dr[name]
                if not self.fresh(d):raise ValueError('No fresh odometry for '+name)
            for name in targets:
                d=self.dr[name]
                if action=='takeoff':
                    d.target_x,d.target_y=d.x,d.y
                    d.target_z=max(d.z+0.2,min(2.6,float(params.get('altitude',1.7))))
                    d.mode='TAKEOFF';d.ever_flew=True;d.fault=False
                elif action=='hover':
                    d.target_x,d.target_y,d.target_z=d.x,d.y,max(.25,d.z)
                    d.mode='HOVER'
                elif action=='land':
                    # For a simulated failed command path, emergency is a separate safety override.
                    d.fault=False;d.target_x,d.target_y,d.target_z=d.x,d.y,.12
                    d.mode='LANDING'
                elif action=='move':
                    dx=float(params.get('dx',0));dy=float(params.get('dy',0));dz=float(params.get('dz',0))
                    if max(abs(dx),abs(dy),abs(dz))>2.0:raise ValueError('Max per-command displacement is 2m')
                    tx=self.clamp(d.x+dx,3.35);ty=self.clamp(d.y+dy,3.35)
                    d.target_x,d.target_y,d.target_z=tx,ty,max(.65,min(2.8,d.z+dz));d.mode='MOVE'
                elif action=='fault':
                    d.fault=True;d.mode='FAULT';d.target_x,d.target_y,d.target_z=d.x,d.y,d.z
                elif action=='clear_fault':
                    d.fault=False;d.target_x,d.target_y,d.target_z=d.x,d.y,max(.6,d.z)
                    d.mode='HOVER'
        self.event(source.upper(),f'{action.upper()} : {", ".join(targets)}',targets)
        return {'ok':True,'action':action,'targets':targets}

    def formation(self,shape,source='DASHBOARD'):
        shape=str(shape).lower()
        if shape not in ('v','line','circle'):raise ValueError('Formation must be v, line or circle')
        with self.lock:
            active=[n for n in NAMES if not self.dr[n].fault]
            if not all(self.fresh(self.dr[n]) for n in active):raise ValueError('Waiting for fresh odometry')
            if len(active)<2:raise ValueError('At least two healthy drones required')
            if shape=='line':pts=[((i-(len(active)-1)/2)*1.45,.1) for i in range(len(active))]
            elif shape=='circle':pts=[(1.65*math.cos(2*math.pi*i/len(active)),1.65*math.sin(2*math.pi*i/len(active))) for i in range(len(active))]
            else:pts=[((i-(len(active)-1)/2)*1.25,abs(i-(len(active)-1)/2)*.85-.6) for i in range(len(active))]
            for name,(x,y) in zip(active,pts):
                d=self.dr[name];d.target_x=x;d.target_y=y;d.target_z=1.9;d.mode='FORMATION';d.ever_flew=True
        self.event(source.upper(),f'FORMATION {shape.upper()}: {len(active)} drones',active)
        return {'ok':True,'action':'formation','targets':active,'shape':shape}

    def status(self):
        with self.lock:
            return {'world':'hu_festival','time':time.strftime('%H:%M:%S'),
                    'real_hu_connected':False, # set by external HU dispatch route only per-request
                    'drones':[{'name':n,'color':COLORS[n],'x':round(d.x,2),'y':round(d.y,2),'z':round(d.z,2),
                               'target':[round(d.target_x,2),round(d.target_y,2),round(d.target_z,2)],
                               'mode':d.mode,'fault':d.fault,'telemetry':self.fresh(d)}
                              for n,d in self.dr.items()], 'events':list(self.logs)[:32]}

# A *deterministic test parser*, deliberately NOT the author's frozen HU model.
# Use /api/hu/dispatch to connect a real HU inference service.
def preview_command(text):
    s=' '.join(str(text).strip().lower().split())
    if not s:return {'decision':'REFUSE','reason':'Empty command'}
    if s in ('khalas','stop','emergency land','land now','everyone stop'):
        return {'decision':'EXECUTE','action':'emergency_land','targets':NAMES,'reason':'Symbolic emergency channel'}
    if re.search(r'\b(or|maybe|perhaps)\b',s):return {'decision':'CONFIRM','reason':'Ambiguous target expression'}
    names=[n for n in NAMES if re.search(r'\b'+n.lower()+r'\b',s)]
    m=re.search(r'\bexcept\s+(horus|ra|thoth|anubis|osiris)\b',s)
    if 'everyone' in s or 'all drones' in s:
        names=[n for n in NAMES if not m or n.lower()!=m.group(1)]
    elif m:return {'decision':'REFUSE','reason':'Exclusion without a collective'}
    if re.search(r'\b(take off|takeoff|launch)\b',s):act='takeoff'
    elif re.search(r'\b(land|landing)\b',s):act='land'
    elif re.search(r'\b(hover|hold)\b',s):act='hover'
    elif re.search(r'\bform\s+(v|line|circle)\b',s):
        return {'decision':'EXECUTE','action':'formation','targets':NAMES,'params':{'shape':re.search(r'\bform\s+(v|line|circle)\b',s).group(1)}}
    else:return {'decision':'REFUSE','reason':'Unsupported phrase in fallback parser'}
    if not names:
        if act=='land': names=NAMES
        else:return {'decision':'REFUSE','reason':'Unaddressed risk-increasing command'}
    return {'decision':'EXECUTE','action':act,'targets':names,'reason':'Deterministic demo parser (not HU model)'}

class Handler(BaseHTTPRequestHandler):
    fleet=None
    def log_message(self,*a): pass
    def send(self,status,data):
        raw=json.dumps(data).encode();self.send_response(status)
        self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    def do_GET(self):
        path=urlparse(self.path).path
        if path=='/api/state':return self.send(200,self.fleet.status())
        if path in ('/','/index.html'):
            p=Path(__file__).with_name('dashboard.html');raw=p.read_bytes()
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw);return
        self.send(404,{'error':'Not found'})
    def do_POST(self):
        try:
            if int(self.headers.get('Content-Length','0'))>8192:raise ValueError('Command too large')
            data=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))) or '{}')
            path=urlparse(self.path).path
            if path=='/api/preview':
                result=preview_command(data.get('text',''))
                return self.send(200,result)
            if path=='/api/command':
                result=self.fleet.command(data.get('action'),data.get('targets'),data.get('params',{}),'DASHBOARD')
                return self.send(200,result)
            if path=='/api/hu/dispatch':
                # This is an integration boundary for an external HU instance.
                # Refuse uncertain predictions here; HU should handle its confirm band.
                confidence=float(data.get('confidence',0))
                if confidence<.85 and data.get('action')!='emergency_land':
                    self.fleet.event('HU REFUSED',f'confidence={confidence:.2f}',data.get('targets',[]))
                    return self.send(409,{'ok':False,'decision':'CONFIRM' if confidence>=.55 else 'REFUSE'})
                result=self.fleet.command(data.get('action'),data.get('targets'),data.get('params',{}),'HU EXTERNAL')
                return self.send(200,result)
            return self.send(404,{'error':'Not found'})
        except (ValueError,TypeError,OverflowError,KeyError,json.JSONDecodeError) as e:
            return self.send(400,{'ok':False,'error':str(e)})
        except Exception as e:
            self.fleet.event('ERROR',str(e),[])
            return self.send(500,{'ok':False,'error':'Internal error; inspect terminal'})

def main():
    fleet=Fleet();Handler.fleet=fleet
    server=ThreadingHTTPServer(('127.0.0.1',8765),Handler)
    print('HU Festival control server on http://localhost:8765',flush=True)
    print('NOTE: dashboard commands use Gazebo transport; frozen HU model is NOT connected yet.',flush=True)
    try:server.serve_forever(poll_interval=.5)
    except KeyboardInterrupt:pass
    finally:fleet.quit=True;server.server_close()
if __name__=='__main__':main()
