#!/usr/bin/env python3
"""Gazebo Harmonic HU Festival supervisory control + honest demo dashboard.
Uses native Gazebo Transport Python API (apt: python3-gz-transport13).
No learned HU checkpoint is bundled. Existing HU can POST structured dispatch commands.
"""
import copy, itertools, json, math, os, re, signal, threading, time, secrets
from collections import deque
from dataclasses import dataclass, field
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse
from formation_planner import slots, assign, assign_shifted, MIN_SEP, ALT

try:
    from gz.transport13 import Node
    from gz.msgs10.boolean_pb2 import Boolean
    from gz.msgs10.odometry_pb2 import Odometry
    from gz.msgs10.twist_pb2 import Twist
except ImportError as ex:
    raise SystemExit('Gazebo Python bindings missing. Run: sudo apt install -y python3-gz-transport13 python3-gz-msgs10\n'+str(ex))

NAMES=['Horus','Ra','Thoth','Anubis','Osiris']
TOKEN_FILE=Path.home()/'hu-telepresence-gazebo'/'festival'/'hu_bridge_token'
def token():
    return TOKEN_FILE.read_text().strip()
START={'Horus':(-2.2,-1.5),'Ra':(0,-1.5),'Thoth':(2.2,-1.5),'Anubis':(-1.1,1.1),'Osiris':(1.1,1.1)}
COLORS={'Horus':'#EBC35A','Ra':'#3BE2F2','Thoth':'#B39AFF','Anubis':'#5AEBAD','Osiris':'#F18BA5'}

@dataclass
class Drone:
    name:str
    x:float=0.; y:float=0.; z:float=0.; vx:float=0.;vy:float=0.;vz:float=0.;yaw:float=0.
    target_x:float=0.;target_y:float=0.;target_z:float=0.
    last_seen:float=0.
    target_yaw:float=0.
    mode:str='WAITING'
    fault:bool=False
    ever_flew:bool=False
    init:bool=False
    spin_segments:int=0
    spin_direction:int=0
    # Staggered takeoff: a QUEUED aircraft waits on the ground for these
    # (too-close) neighbours to climb before it launches to queue_alt.
    queue_after:tuple=()
    queue_alt:float=0.
    queue_since:float=0.

PARKED=('READY','GROUNDED','WAITING','QUEUED')
STAGGER_RELEASE_Z=1.20   # a waiting aircraft launches once its close neighbours are this high
STAGGER_LAYER_M=.90      # vertical layer between too-close pairs (keeps >=1.0 m 3D at 0.5 m horizontal)

class Fleet:
    def __init__(self):
        self.node=Node(); self.lock=threading.RLock();self.logs=deque(maxlen=120)
        self.dr={name:Drone(name,target_x=START[name][0],target_y=START[name][1],target_z=.55) for name in NAMES}
        self.twist={};self.enables={}
        self.quit=False
        self.hu_connected=False
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
                d.target_yaw=d.yaw
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
                    if d.mode=='QUEUED':
                        ahead=[self.dr[n] for n in d.queue_after]
                        if all(self.fresh(a) and a.z>=STAGGER_RELEASE_Z and not a.fault for a in ahead):
                            d.target_x,d.target_y,d.target_z=d.x,d.y,d.queue_alt;d.target_yaw=d.yaw
                            d.mode='TAKEOFF';d.ever_flew=True;d.queue_after=()
                            self.event('STAGGER_RELEASE',f'{name} launching; {", ".join(a.name for a in ahead)} cleared {STAGGER_RELEASE_Z} m',[name])
                        elif time.monotonic()-d.queue_since>25 or any(a.fault or a.mode in ('LANDING','GROUNDED') for a in ahead):
                            d.mode='GROUNDED';d.queue_after=()
                            self.event('STAGGER_CANCELLED',f'{name} stays parked: the aircraft it waited for did not climb',[name])
                        continue
                    if d.mode in ('READY','GROUNDED','WAITING'):
                        # Parked aircraft are actively disarmed (10 Hz). Gazebo's velocity
                        # controller can otherwise hold a just-spawned drone hovering at
                        # its 0.55 m spawn height while it is reported as parked.
                        dis=Boolean();dis.data=False;self.enables[name].publish(dis)
                        continue
                    # LANDING is deliberately excluded: a proximity hold must never
                    # cancel a landing (an emergency landing of close aircraft would
                    # otherwise freeze them in the air).
                    if d.mode in ('FORMATION','MOVE','TAKEOFF'):
                        # A vertical TAKEOFF climbs away from a PARKED neighbour, so
                        # parked aircraft are not a conflict for it (simultaneous
                        # takeoff of close aircraft is refused in command()).
                        parked=PARKED
                        close=[other for other in self.dr.values()
                               if other.name!=name and self.fresh(other)
                               and not (d.mode=='TAKEOFF' and other.mode in parked)
                               and math.dist((d.x,d.y,d.z),(other.x,other.y,other.z))<.82]
                        if close:
                            d.target_x,d.target_y,d.target_z=d.x,d.y,d.z
                            self.publish(name,0,0,0)
                            if d.z<.30:
                                # Still on the ground: report GROUNDED, never a false HOVER.
                                dis=Boolean();dis.data=False;self.enables[name].publish(dis)
                                d.mode='GROUNDED';d.target_z=.10
                            else:
                                d.mode='HOVER'
                            self.event('SEPARATION_HOLD',f'{name}: observed proximity hold with {", ".join(o.name for o in close)}',[name])
                            continue
                    if d.mode=='FAULT':
                        self.publish(name,0,0,0)
                        continue
                    if d.mode=='LANDING' and d.z<=.20 and abs(d.vz)<.30:
                        self.publish(name,0,0,0)
                        dis=Boolean();dis.data=False;self.enables[name].publish(dis)
                        d.mode='GROUNDED';d.target_z=.10
                        self.event('LANDED',f'{name} landed; motors disarmed',[name]);continue
                    if d.mode=='SPIN':
                        diff=math.atan2(math.sin(d.target_yaw-d.yaw),math.cos(d.target_yaw-d.yaw))
                        if abs(diff)<.12:
                            d.spin_segments-=1
                            if d.spin_segments<=0:
                                d.mode='HOVER';d.target_yaw=d.yaw
                                self.event('SPIN_COMPLETE',f'{name} finished in-place rotation',[name])
                            else:
                                d.target_yaw=math.atan2(math.sin(d.target_yaw+d.spin_direction*math.pi/2),
                                                         math.cos(d.target_yaw+d.spin_direction*math.pi/2))
                    ex=d.target_x-d.x;ey=d.target_y-d.y;ez=d.target_z-d.z
                    # Yaw compensation: motor controller accepts body-frame linear velocity.
                    vx=self.clamp(.80*ex-.16*d.vx,.62)
                    vy=self.clamp(.80*ey-.16*d.vy,.62)
                    vz=self.clamp(1.05*ez-.22*d.vz,.45)
                    if d.mode=='LANDING': vz=max(-.32,min(.10,vz))
                    co=math.cos(d.yaw);si=math.sin(d.yaw)
                    bx=co*vx+si*vy;by=-si*vx+co*vy
                    yaw_error=math.atan2(math.sin(d.target_yaw-d.yaw),math.cos(d.target_yaw-d.yaw))
                    rz=self.clamp(1.25*yaw_error,.55)
                    self.publish(name,bx,by,vz,rz)
                    if d.mode=='TAKEOFF' and abs(ez)<.13 and abs(d.vz)<.18:
                        d.mode='HOVER';self.event('HOVER',f'{name} reached its altitude target',[name])
                    # A completed MOVE/TURN returns to HOVER, so consecutive relative
                    # commands are accepted (previously the aircraft stayed in MOVE
                    # forever and every following move was refused).
                    elif d.mode in ('MOVE','TURN') and math.sqrt(ex*ex+ey*ey+ez*ez)<.06 \
                            and math.sqrt(d.vx*d.vx+d.vy*d.vy+d.vz*d.vz)<.08 and abs(yaw_error)<.05:
                        done=d.mode;d.mode='HOVER'
                        self.event('ARRIVED',f'{name} completed {done} and holds position',[name])
            time.sleep(.10)

    def command(self,action,targets=None,params=None,source='DASHBOARD'):
        emergency_stop=action=='emergency_hover'
        params=params or {}; targets=targets or []
        if isinstance(targets,str):targets=[targets]
        targets=[str(t).title() for t in targets]
        if action not in ('takeoff','hover','land','move','turn','spin','formation','fault','clear_fault','emergency_land','emergency_hover'):
            raise ValueError('Unsupported action')
        if action in ('emergency_land','emergency_hover'):
            targets=list(NAMES)
            action='land' if action=='emergency_land' else 'hover'
        if action=='formation':return self.formation(params.get('shape','v'),source,targets)
        if action=='fault' and len(targets)!=1:raise ValueError('Fault simulation requires one named drone')
        if not targets or any(n not in self.dr for n in targets):raise ValueError('Specify valid drone targets')
        with self.lock:
            if action not in ('clear_fault','land') and not emergency_stop:
                blocked=[n for n in targets if self.dr[n].fault]
                if blocked:raise ValueError('Simulated command-link fault active: '+','.join(blocked))
            # Safe-direction actions (land/hover) must not be blocked fleet-wide by
            # one aircraft with stale odometry: command every fresh aircraft and
            # report the skipped ones. Risk-increasing actions still fail closed.
            stale=[n for n in targets if not self.fresh(self.dr[n])]
            if stale:
                if action not in ('land','hover'):
                    raise ValueError('No fresh odometry for '+', '.join(stale))
                targets=[n for n in targets if n not in stale]
                if not targets:raise ValueError('No fresh odometry for '+', '.join(stale))
                self.event('SAFETY_SKIPPED',f'{action.upper()} not applied (stale odometry): {", ".join(stale)}',stale)
            for name in targets:
                d=self.dr[name]
                if action in ('move','turn','spin') and d.mode not in ('TAKEOFF','HOVER','MOVE','FORMATION','TURN'):
                    raise ValueError(f'{name} must take off before {action}')
            # Before changing *any* setpoint, reject unbounded, mid-flight, or
            # conflict-prone relative moves. Sampled static obstacle check is
            # conservative but is NOT a guarantee of collision-free motion.
            stagger={}
            if action=='takeoff':
                stagger=self._plan_stagger(targets,params)
            move_goal=None
            if action=='move' and len(targets)>1:
                move_goal=self._plan_group_move(targets,params)
            elif action=='move':
                d=self.dr[targets[0]]
                if d.mode!='HOVER':raise ValueError('Establish stable HOVER before a relative move')
                if any(other.mode in ('MOVE','FORMATION','TAKEOFF','LANDING','SPIN')
                       for other in self.dr.values() if other.name!=d.name):
                    raise ValueError('Other aircraft are moving; wait for stable fleet state')
                dx=float(params.get('dx',0));dy=float(params.get('dy',0));dz=float(params.get('dz',0))
                fwd=float(params.get('forward_m',0));right=float(params.get('right_m',0))
                if not all(math.isfinite(v) for v in (dx,dy,dz,fwd,right)):
                    raise ValueError('Non-finite movement parameter')
                if max(abs(dx),abs(dy),abs(dz),abs(fwd),abs(right))>2.0:
                    raise ValueError('Maximum movement 2m')
                # Gazebo world is ENU (x forward at yaw 0, +y is the aircraft's LEFT),
                # so body-right is (sin yaw, -cos yaw). Verified live: the previous
                # signs moved "right 50 cm" 0.43 m to the LEFT.
                dx+=math.cos(d.yaw)*fwd+math.sin(d.yaw)*right
                dy+=math.sin(d.yaw)*fwd-math.cos(d.yaw)*right
                x,y,z=d.x+dx,d.y+dy,d.z+dz
                if abs(x)>3.35 or abs(y)>3.35 or z<.65 or z>2.8:
                    raise ValueError('Movement would leave the validated arena/altitude bounds')
                start=(d.x,d.y,d.z);goal=(x,y,z)
                for other in self.dr.values():
                    if other.name==d.name:continue
                    if not self.fresh(other):raise ValueError('Other aircraft telemetry lost')
                    peer=(other.x,other.y,other.z)
                    for i in range(51):
                        f=i/50
                        pos=tuple(a+(b-a)*f for a,b in zip(start,goal))
                        if math.dist(pos,peer)<1.0:
                            raise ValueError('Movement rejected: predicted separation below 1.0m from '+other.name)
                move_goal={d.name:goal}
            for name in targets:
                d=self.dr[name]
                if action=='takeoff':
                    plan=stagger.get(name)
                    if plan and plan['wait_for']:
                        # Waits on the ground; the loop releases it (see QUEUED).
                        d.mode='QUEUED';d.queue_after=tuple(plan['wait_for']);d.queue_alt=plan['alt']
                        d.queue_since=time.monotonic();d.fault=False
                        continue
                    d.target_x,d.target_y=d.x,d.y
                    d.target_yaw=d.yaw
                    d.target_z=plan['alt'] if plan else max(d.z+0.2,min(2.6,float(params.get('altitude',1.7))))
                    d.mode='TAKEOFF';d.ever_flew=True;d.fault=False
                elif action=='hover':
                    d.target_x,d.target_y=d.x,d.y
                    d.target_yaw=d.yaw
                    if d.mode=='QUEUED':
                        # STOP cancels a waiting staggered launch: it stays parked.
                        d.mode='GROUNDED';d.queue_after=();d.target_z=d.z
                    elif d.mode in ('READY','GROUNDED','WAITING','FAULT'):
                        # STOP is a hold, never a hidden takeoff or fault bypass.
                        d.target_z=d.z
                    else:
                        d.target_z=max(.25,d.z);d.mode='HOVER'
                elif action=='land':
                    # Land does not activate an already parked aircraft.
                    if d.mode=='QUEUED':
                        d.mode='GROUNDED';d.queue_after=()
                    elif d.mode not in ('READY','GROUNDED','WAITING'):
                        d.fault=False;d.target_x,d.target_y,d.target_z=d.x,d.y,.12
                        d.mode='LANDING'
                elif action=='move':
                    d.target_x,d.target_y,d.target_z=move_goal[name];d.mode='MOVE'
                elif action=='turn':
                    degrees=float(params.get('degrees',0))
                    if not math.isfinite(degrees) or abs(degrees)>180:raise ValueError('Turn angle must be within +/-180 degrees')
                    d.target_x,d.target_y,d.target_z=d.x,d.y,d.z
                    d.target_yaw=math.atan2(math.sin(d.yaw+math.radians(degrees)),math.cos(d.yaw+math.radians(degrees)))
                    d.mode='TURN'
                elif action=='spin':
                    direction=int(params.get('direction',1))
                    if direction not in (-1,1):raise ValueError('Invalid spin direction')
                    d.target_x,d.target_y,d.target_z=d.x,d.y,d.z
                    d.spin_direction=direction;d.spin_segments=4
                    d.target_yaw=math.atan2(math.sin(d.yaw+direction*math.pi/2),
                                              math.cos(d.yaw+direction*math.pi/2))
                    d.mode='SPIN'
                elif action=='fault':
                    d.fault=True;d.mode='FAULT';d.target_x,d.target_y,d.target_z=d.x,d.y,d.z
                elif action=='clear_fault':
                    d.fault=False
                    # Clearing a fault on the ground must not become a hidden takeoff.
                    if d.mode in ('READY','GROUNDED','WAITING') or d.z<.3:
                        d.target_x,d.target_y,d.target_z=d.x,d.y,d.z
                        if d.mode=='FAULT':d.mode='GROUNDED'
                    else:
                        d.target_x,d.target_y,d.target_z=d.x,d.y,max(.6,d.z)
                        d.mode='HOVER'
        self.event(source.upper(),f'{action.upper()} : {", ".join(targets)}',targets)
        return {'ok':True,'action':action,'targets':targets,'skipped_stale':stale}

    def _plan_stagger(self,targets,params):
        """Staggered, layered takeoff for parked aircraft that are too close to climb together.

        Aircraft parked closer than MIN_SEP (horizontally) form a conflict graph.
        It is 2-coloured: one side climbs FIRST to base+STAGGER_LAYER_M, the other
        stays QUEUED on the ground until its close neighbours pass
        STAGGER_RELEASE_Z, then climbs to the base altitude, so close pairs end up
        vertically layered. Refused if the graph is not 2-colourable (three or more
        mutually crowded aircraft) or a neighbour is already airborne overhead.
        Returns {name: {'alt': float, 'wait_for': [names]}} for launching aircraft.
        """
        base=max(.25,min(2.6-STAGGER_LAYER_M,float(params.get('altitude',1.7))))
        launching=[n for n in targets if self.dr[n].mode in ('READY','GROUNDED','WAITING')]
        def h(a,b):return math.hypot(self.dr[a].x-self.dr[b].x,self.dr[a].y-self.dr[b].y)
        for n in launching:
            for o in self.dr.values():
                if o.name!=n and o.mode not in PARKED and h(n,o.name)<MIN_SEP and o.z<base+STAGGER_LAYER_M+.5:
                    raise ValueError(f'{n} would climb within {h(n,o.name):.2f} m (horizontal) of airborne {o.name}')
        adj={n:[m for m in launching if m!=n and h(n,m)<MIN_SEP-1e-6] for n in launching}
        if not any(adj.values()):return {}
        colour={}
        for root in launching:
            if root in colour or not adj[root]:continue
            colour[root]=0;stack=[root]
            while stack:
                a=stack.pop()
                for b in adj[a]:
                    if b not in colour:colour[b]=1-colour[a];stack.append(b)
                    elif colour[b]==colour[a]:
                        raise ValueError(f'{a}, {b} and their neighbours are parked too close together to layer; take them off one at a time')
        plan={}
        for n in launching:
            if not adj[n]:continue
            if colour[n]==0:plan[n]={'alt':round(base+STAGGER_LAYER_M,2),'wait_for':[]}
            else:plan[n]={'alt':round(base,2),'wait_for':adj[n]}
        self.event('STAGGERED_TAKEOFF','Layered launch: '+'; '.join(
            f'{n} {"first to" if not p["wait_for"] else "after "+"/".join(p["wait_for"])+" to"} {p["alt"]} m' for n,p in plan.items()),list(plan))
        return plan

    def _plan_group_move(self,targets,params):
        """Swarm travel: rigid translation of several aircraft (caller holds the lock).

        Every member gets the SAME displacement in the ARENA frame (forward = +x,
        right = -y, i.e. the yaw-0 heading), so spacing inside the group is
        preserved and the formation keeps its shape. Refused unless all members
        are settled, every goal is in bounds, the group's own spacing is at
        least MIN_SEP, and every member's straight path keeps 1.0 m from every
        aircraft that is NOT moving. The runtime proximity watchdog stays active.
        """
        dx=float(params.get('dx',0));dy=float(params.get('dy',0));dz=float(params.get('dz',0))
        fwd=float(params.get('forward_m',0));right=float(params.get('right_m',0))
        if not all(math.isfinite(v) for v in (dx,dy,dz,fwd,right)):
            raise ValueError('Non-finite movement parameter')
        if max(abs(dx),abs(dy),abs(dz),abs(fwd),abs(right))>2.0:
            raise ValueError('Maximum movement 2m')
        dx+=fwd;dy-=right
        members=[self.dr[n] for n in targets]
        for d in members:
            if d.fault:raise ValueError(f'{d.name} has an active fault; it cannot join a group move')
            if d.mode not in ('HOVER','FORMATION'):
                raise ValueError(f'{d.name} is {d.mode}; every member must hold HOVER or a settled formation')
            if math.dist((d.x,d.y,d.z),(d.target_x,d.target_y,d.target_z))>.12 or math.sqrt(d.vx**2+d.vy**2+d.vz**2)>.12:
                raise ValueError(f'{d.name} has not settled; wait before moving the group')
        others=[o for o in self.dr.values() if o.name not in targets]
        for o in others:
            if o.mode in ('MOVE','FORMATION','TAKEOFF','LANDING','SPIN','TURN'):
                raise ValueError(f'{o.name} is maneuvering ({o.mode}); wait for a stable fleet')
            if not self.fresh(o):raise ValueError(f'{o.name} telemetry lost')
        for i,a in enumerate(members):
            for b in members[i+1:]:
                s=math.dist((a.x,a.y,a.z),(b.x,b.y,b.z))
                if s<MIN_SEP:raise ValueError(f'{a.name} and {b.name} are only {s:.2f} m apart; spread out before a group move')
        goals={}
        for d in members:
            x,y,z=d.x+dx,d.y+dy,d.z+dz
            if abs(x)>3.35 or abs(y)>3.35 or z<.65 or z>2.8:
                raise ValueError(f'Group move would take {d.name} outside the validated arena/altitude bounds')
            start=(d.x,d.y,d.z);goals[d.name]=(x,y,z)
            for o in others:
                peer=(o.x,o.y,o.z)
                for k in range(51):
                    f=k/50
                    if math.dist(tuple(a+(b-a)*f for a,b in zip(start,(x,y,z))),peer)<1.0:
                        raise ValueError(f'Group move rejected: {d.name} would pass within 1.0 m of {o.name}')
        return goals

    def formation(self,shape,source='DASHBOARD',targets=None):
        shape=str(shape).lower()
        with self.lock:
            names=[n for n in NAMES if (not targets or n in targets)]
            if len(names)<2:raise ValueError('At least two drones required for formation')
            if any(not self.fresh(self.dr[n]) for n in NAMES):
                raise ValueError('Fresh telemetry required from all five aircraft')
            if any(self.dr[n].fault for n in names):
                raise ValueError('Faulted aircraft cannot join a formation')
            if any(self.dr[n].mode not in ('HOVER','MOVE','FORMATION','TURN','SPIN')
                   or self.dr[n].z<.70 for n in names):
                raise ValueError('Take off and establish stable hover before reconfiguration')
            pts=slots(shape,len(names))
            other=[n for n in NAMES if n not in names]
            offset=(0.,0.)
            if other:
                # Non-participating (e.g. faulted) aircraft are fixed obstacles:
                # the formation may be translated away from them, never squeezed.
                assigned,offset=assign_shifted(names,self.dr,pts,other)
            else:
                assigned=assign(names,self.dr,pts,other)
            for name,(x,y) in zip(names,assigned):
                d=self.dr[name];d.target_x=x;d.target_y=y;d.target_z=ALT;d.mode='FORMATION'
        self.event(source.upper(),f'FORMATION {shape.upper()}: {len(names)} drones'+
                   (f' (offset {offset[0]:+.2f},{offset[1]:+.2f} m around {", ".join(other)})' if offset!=(0.,0.) else ''),names)
        return {'ok':True,'action':'formation','targets':names,'shape':shape,'offset_m':list(offset)}

    def plan_check(self,steps):
        """MA'AT read-only plan check: never moves anything, never changes live state.

        The live drone state is copied atomically under the fleet lock; every step is
        then run through the UNCHANGED command()/formation() code path on a scratch
        copy (_PlanScratch), so the first step sees exactly the validation (freshness,
        fault, mode preconditions, stagger/group-move/formation planning, bounds,
        separation) that a real command would see at this instant. Steps after the
        first run on a PREDICTED state (each earlier step assumed to have settled at
        its setpoints) and are marked predicted=True. The (slow) permutation search
        runs on the copy outside the lock, so the 10 Hz control loop never stalls.
        """
        if not isinstance(steps,list) or not steps:raise ValueError('steps must be a non-empty list')
        if len(steps)>12:raise ValueError('At most 12 steps per plan check')
        with self.lock:
            now=time.monotonic()
            fresh_names={n for n,d in self.dr.items() if self.fresh(d)}
            snap={n:copy.deepcopy(d) for n,d in self.dr.items()}
        telemetry={n:{'x':round(d.x,3),'y':round(d.y,3),'z':round(d.z,3),
                      'vx':round(d.vx,3),'vy':round(d.vy,3),'vz':round(d.vz,3),
                      'mode':d.mode,'fault':d.fault,'fresh':n in fresh_names,
                      'age_s':round(now-d.last_seen,3) if d.init else None,
                      'target':[round(d.target_x,3),round(d.target_y,3),round(d.target_z,3)]}
                   for n,d in snap.items()}
        scratch=_PlanScratch(snap,fresh_names)
        results=[];failed=False
        for i,step in enumerate(steps):
            entry={'index':i,'predicted':i>0}
            if not isinstance(step,dict):
                entry.update(ok=False,checked=False,reason='Step must be an object');results.append(entry);failed=True;continue
            action=step.get('action');targets=step.get('targets');params=step.get('params',{})
            entry.update(action=action,targets=targets,params=params)
            if failed:
                entry.update(ok=False,checked=False,reason='Not checked: an earlier step was rejected');results.append(entry);continue
            if action not in PLAN_ACTIONS:
                entry.update(ok=False,checked=True,reason='Unsupported plan action: '+str(action))
            elif not isinstance(targets,list) or not targets or any(n not in NAMES for n in targets):
                entry.update(ok=False,checked=True,reason='Unrecognized plan targets')
            elif not isinstance(params,dict):
                entry.update(ok=False,checked=True,reason='params must be an object')
            else:
                before={n:(d.x,d.y,d.z,d.mode) for n,d in scratch.dr.items()}
                try:
                    res=scratch.command(action,list(targets),dict(params),'PLAN CHECK')
                    entry.update(ok=True,checked=True,reason='Accepted by the supervisor command path'+(' (predicted state)' if i else ''))
                    entry.update(scratch.describe(before,res))
                    scratch.settle()
                except (ValueError,TypeError,OverflowError,KeyError) as e:
                    entry.update(ok=False,checked=True,reason=str(e))
            if not entry['ok']:failed=True
            results.append(entry)
        return {'ok':not failed,'read_only':True,'route':'POST /api/plan/check',
                'checked_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'snapshot_monotonic':round(now,3),
                'telemetry':telemetry,'steps':results,
                'note':'Dry run on a copy of the live state; step 0 is exact, later steps are predicted. Nothing was commanded.'}

    def status(self):
        with self.lock:
            return {'world':'hu_festival','time':time.strftime('%H:%M:%S'),
                    'real_hu_connected':self.hu_connected, # legacy indicator: dispatch seen, NOT model health
                    'hu_dispatch_seen':self.hu_connected,
                    'drones':[{'name':n,'color':COLORS[n],'x':round(d.x,2),'y':round(d.y,2),'z':round(d.z,2),
                               'target':[round(d.target_x,2),round(d.target_y,2),round(d.target_z,2)],
                               'mode':d.mode,'fault':d.fault,'yaw':round(math.degrees(d.yaw),1),'telemetry':self.fresh(d)}
                              for n,d in self.dr.items()], 'events':list(self.logs)[:32]}

PLAN_ACTIONS=('formation','turn','spin','move','takeoff','land','hover')
MISSION_PLANNERS={'HU-MISSION-V6':'MISSION CONFIRMED','HU-MISSION-COORD':'MISSION COORD CONFIRMED'}

class _PlanScratch(Fleet):
    """Detached copy of the fleet for MA'AT plan checks: inherits the real
    command()/formation()/_plan_stagger()/_plan_group_move() code, but owns copied
    Drone objects, a private lock, no Gazebo node, no event log and no publisher.
    Freshness is frozen at the live snapshot instant."""
    def __init__(self,drones,fresh_names):
        self.dr=drones;self.lock=threading.RLock();self.logs=deque(maxlen=8)
        self._fresh=set(fresh_names);self.hu_connected=False
    def fresh(self,d):return d.name in self._fresh
    def event(self,*args,**kwargs):pass
    def publish(self,*args,**kwargs):raise RuntimeError('plan check must never publish')

    def describe(self,before,result):
        targets=result.get('targets',[])
        goals={};queued={}
        for n in targets:
            d=self.dr[n]
            if d.mode=='QUEUED':
                goals[n]=(d.x,d.y,d.queue_alt);queued[n]=list(d.queue_after)
            else:goals[n]=(d.target_x,d.target_y,d.target_z)
        start={n:before[n][:3] for n in self.dr}
        end={n:goals.get(n,start[n]) for n in self.dr}
        parked=lambda p:p[2]<.30
        pairs={}
        for a,b in itertools.combinations(NAMES,2):
            if parked(start[a]) and parked(end[a]) and parked(start[b]) and parked(end[b]):continue
            best=float('inf')
            # Phase A: non-queued movers travel together while queued aircraft wait on
            # the ground; phase B: queued aircraft climb once the others have arrived.
            for phase in ('A','B'):
                for k in range(41):
                    f=k/40
                    def at(n):
                        if n in queued:
                            g=f if phase=='B' else 0.
                        else:
                            g=f if phase=='A' else 1.
                        return tuple(s+(e-s)*g for s,e in zip(start[n],end[n]))
                    best=min(best,math.dist(at(a),at(b)))
            pairs[f'{a}|{b}']=round(best,3)
        out={'planned_targets':targets,
             'planned_goals':{n:[round(v,3) for v in g] for n,g in goals.items()},
             'pairwise_min_sep':pairs,
             'planned_min_sep':min(pairs.values()) if pairs else None}
        if queued:out['staggered_wait_for']=queued
        if result.get('skipped_stale'):out['skipped_stale']=result['skipped_stale']
        if 'offset_m' in result:out['formation_offset_m']=result['offset_m']
        if 'shape' in result:out['shape']=result['shape']
        return out

    def settle(self):
        """Predict the state after the step has completed (setpoints reached)."""
        for d in self.dr.values():
            if d.mode=='QUEUED':
                d.z=d.target_z=d.queue_alt;d.target_x,d.target_y=d.x,d.y;d.queue_after=();d.mode='HOVER'
            elif d.mode in ('TAKEOFF','MOVE','TURN','SPIN','FORMATION'):
                d.x,d.y,d.z=d.target_x,d.target_y,d.target_z;d.yaw=d.target_yaw
                if d.mode!='FORMATION':d.mode='HOVER'
            elif d.mode=='LANDING':
                d.x,d.y=d.target_x,d.target_y;d.z=.05;d.target_z=.10;d.mode='GROUNDED'
            d.vx=d.vy=d.vz=0.

# A *deterministic test parser*, deliberately NOT the author's frozen HU model.
# Use /api/hu/dispatch to connect a real HU inference service.
def preview_command(text):
    s=' '.join(str(text).strip().lower().split())
    if not s:return {'decision':'REFUSE','reason':'Empty command'}
    if s in ('khalas','stop','everyone stop'):
        return {'decision':'EXECUTE','action':'emergency_hover','targets':NAMES,'reason':'Symbolic emergency hover channel'}
    if s in ('emergency land','land now'):
        return {'decision':'EXECUTE','action':'emergency_land','targets':NAMES,'reason':'Symbolic emergency landing channel'}
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
        # Block cross-site requests: any web page could otherwise POST a
        # text/plain body here. Local tools (urllib, curl) send no Origin.
        origin=self.headers.get('Origin')
        if origin and origin not in ('http://127.0.0.1:8765','http://localhost:8765'):
            return self.send(403,{'ok':False,'error':'Cross-origin request rejected'})
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
            if path=='/api/mission/dispatch':
                # New deterministic mission planner, never portrayed as HU inference.
                if not secrets.compare_digest(self.headers.get('X-HU-Token',''),token()):
                    return self.send(403,{'ok':False,'error':'Authorized mission gateway required'})
                if data.get('operator_confirmed') is not True or data.get('planner') not in MISSION_PLANNERS:
                    return self.send(409,{'ok':False,'error':'Explicit operator confirmation required'})
                action=data.get('action')
                if action not in ('formation','turn','spin','move','takeoff','land','hover'):
                    return self.send(400,{'ok':False,'error':'Unsupported mission action'})
                targets=data.get('targets')
                if not isinstance(targets,list) or not targets or any(n not in NAMES for n in targets):
                    return self.send(400,{'ok':False,'error':'Unrecognized mission targets'})
                return self.send(200,self.fleet.command(action,targets,data.get('params',{}),MISSION_PLANNERS[data['planner']]))
            if path=='/api/plan/check':
                # MA'AT read-only plan check: a dry run on a copy; nothing is commanded.
                return self.send(200,self.fleet.plan_check(data.get('steps')))
            if path=='/api/hu/dispatch':
                # This is an integration boundary for an external HU instance.
                # Refuse uncertain predictions here; HU should handle its confirm band.
                if not secrets.compare_digest(self.headers.get('X-HU-Token',''),token()):
                    return self.send(403,{'ok':False,'error':'Authorized HU bridge required'})
                action=data.get('action')
                confidence=float(data.get('confidence',0))
                confirmed=data.get('operator_confirmed') is True
                emergency=data.get('symbolic_emergency') is True and action in ('emergency_land','emergency_hover')
                if not math.isfinite(confidence) or confidence<0 or confidence>1:
                    return self.send(400,{'ok':False,'error':'Invalid confidence'})
                if not emergency and (confidence<.55 or (confidence<.85 and not confirmed)):
                    self.fleet.event('HU REFUSED',f'confidence={confidence:.2f}',data.get('targets',[]))
                    return self.send(409,{'ok':False,'decision':'CONFIRM' if confidence>=.55 else 'REFUSE'})
                result=self.fleet.command(action,data.get('targets'),data.get('params',{}),'HU CONFIRMED' if confirmed else 'HU ACTUAL')
                if result.get('ok') is True:self.fleet.hu_connected=True
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
