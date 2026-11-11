#!/usr/bin/env python3
"""Real HU / Gazebo bridge for the user's HU repository.
Runs the original frozen-model HU parser; no fallback parser or simulated prediction.
Run from ~/rscl-hu-latest, where the original `hu` package and checkpoints live.
"""
import argparse
import html
import json
import math
import os
from pathlib import Path
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.request
import urllib.error
from mission_intents import parse as parse_mission

FESTIVAL = 'http://127.0.0.1:8765'
NAMES = ('Horus','Ra','Thoth','Anubis','Osiris')
ROSTER = Path.home()/'hu-telepresence-gazebo'/'festival'/'hu_sim_roster.yaml'
TOKEN = Path.home()/'hu-telepresence-gazebo'/'festival'/'hu_bridge_token'
EMERGENCY_LAND={'land now','land land land','everybody down','everyone down','emergency land','yalla land'}
EMERGENCY_HOVER={'stop','stop stop stop','all stop','everyone stop','everybody stop','khalas','abort'}
# Bare 'land' is NOT a fleet-wide emergency: require an explicit target or ALL.
EMERGENCY=EMERGENCY_LAND | EMERGENCY_HOVER

def emergency_key(text):
    """Canonical emergency phrase or None. Tolerates a fleet address and politeness around
    an emergency phrase ("All, khalas!", "everyone, stop now", "drones: abort please"), which
    was previously refused because only exact phrases matched. Bare 'land' stays non-emergency."""
    s=' '.join(re.sub(r'[^\w\s]',' ',str(text).lower()).split())
    pre=lambda x:re.sub(r'^(?:(?:hey|ok|okay)\s+)?(?:all|everyone|everybody|all drones|drones|team|guys|fleet)\s+','',x)
    suf=lambda x:re.sub(r'\s+(?:please|right now|immediately)$','',x)
    # Try the phrase as said, then without the fleet address, then without trailing politeness.
    # "now" is kept because "land now" is itself an emergency phrase; bare "land" never is.
    now=lambda x:re.sub(r'\s+now$','',x)
    for cand in (s,pre(s),suf(s),pre(suf(s)),suf(pre(s)),now(pre(suf(s))),now(suf(s))):
        if cand in EMERGENCY:return cand
    return None
VOICE_ALIASES={'rock':'Ra','prop':'Ra','roland':'Ra','raw':'Ra','rah':'Ra',
               'taurus':'Horus','horace':'Horus','chorus':'Horus','horis':'Horus',
               'horror':'Horus','horrors':'Horus','anubus':'Anubis','a nubis':'Anubis',
               'toth':'Thoth','thought':'Thoth','thoss':'Thoth',
               'yella':'yalla','yallow':'yalla','yellow':'yalla','yala':'yalla'}

class DispatchError(RuntimeError):pass

def get_json(url, data=None, timeout=3.0, headers=None):
    request=urllib.request.Request(url,
         data=None if data is None else json.dumps(data).encode('utf-8'),
         headers={'Content-Type':'application/json', **(headers or {})},
         method='POST' if data is not None else 'GET')
    try:
        with urllib.request.urlopen(request,timeout=timeout) as res:
            return json.load(res)
    except urllib.error.HTTPError as e:
        raise DispatchError(f'Gazebo rejected HTTP {e.code}: {e.read().decode()[:300]}') from e
    except urllib.error.URLError as e:
        raise DispatchError('Gazebo control service unavailable: '+str(e.reason)) from e

def translate(action, params):
    """Translate the genuine HU paper's nine action labels to Gazebo commands."""
    action=str(action).upper()
    params=params or {}
    if params.get('range_violation'):
        raise ValueError('HU refused unsafe parameter: '+str(params['range_violation']))
    if action=='TAKEOFF':return 'takeoff',{'altitude':1.65}
    if action=='LAND':return 'land',{}
    if action=='STOP' or action=='HOVER':return 'hover',{}
    if action=='MOVE':
        cm=float(params.get('dist',50))
        if not 10<=cm<=200:raise ValueError('Unsafe move distance')
        direction=params.get('dir','forward')
        offsets={'forward':(1,0),'back':(-1,0),'left':(0,-1),'right':(0,1)}
        if direction not in offsets:raise ValueError('Unsupported HU move direction')
        f,r=offsets[direction]
        return 'move',{'forward_m':f*cm/100,'right_m':r*cm/100}
    if action in ('UP','DOWN'):
        cm=float(params.get('dist',30))
        if not 10<=cm<=200:raise ValueError('Unsafe vertical distance')
        return 'move',{'dz':(1 if action=='UP' else -1)*cm/100}
    if action=='TURN':
        a=float(params.get('angle',90))
        if not 5<=a<=180:raise ValueError('Unsafe turn angle')
        rot=params.get('rot','clockwise')
        if rot not in ('clockwise','counterclockwise'):raise ValueError('Unknown turn direction')
        return 'turn',{'degrees':a*(1 if rot=='counterclockwise' else -1)}
    raise ValueError('HU does not authorize this action: '+action)

def normalize_utterance(raw,source):
    u=raw
    if source=='voice':
        for wrong,right in VOICE_ALIASES.items():
            u=re.sub(r'\b'+re.escape(wrong)+r'\b',right,u,flags=re.IGNORECASE)
    if source == 'voice':
        # Accent-aware, context-limited repair: Chrome sometimes hears
        # "old take off" when the operator says "all take off".
        # Never rewrite unrelated uses of the word "old".
        u = re.sub(
            r'\bold(?=\s+(?:(?:the\s+)?drones?\s+)?(?:take\s*off|takeoff|land|stop|hover|form|perform|turn|rotate|spin)\b)',
            'all', u, flags=re.IGNORECASE)
    return u

def stated_targets(utterance):
    """Conservative, exact-name guard; never invent a drone based on model output.

    Returns None if the utterance does not explicitly state an address. For a
    collective, exact set membership includes exclusion phrases. Rejecting a
    mismatch is safer than silently steering another named drone.
    """
    u=str(utterance).lower()
    named={n for n in NAMES if re.search(r'\b'+re.escape(n.lower())+r'\b',u)}
    collective=bool(re.search(r'\b(?:everyone|everybody|all\s+(?:the\s+)?drones?|all\s+(?=take\s*off|land|stop|hover|move|rise|ascend|descend))\b',u))
    ex=re.search(r'\b(?:except|excluding|but\s+not)\s+(horus|ra|thoth|anubis|osiris)\b',u)
    if collective:
        if re.search(r'\b(?:except|excluding|but\s+not)\b',u) and ex is None:
            return None  # Unsupported exclusion forms remain subject to HU refusal.
        return [n for n in NAMES if not ex or n.lower()!=ex.group(1)]
    if named:
        return [n for n in NAMES if n in named]
    return None

def explicit_primitive_intent(text):
    """Lexical consistency guard, not an inference engine."""
    s=str(text).lower()
    candidates=set()
    if re.search(r'\b(?:take\s*off|launch|lift\s*off)\b',s):candidates.add('TAKEOFF')
    if re.search(r'\b(?:land|landing|touch\s*down)\b',s):candidates.add('LAND')
    if re.search(r'\b(?:stop|hover|hold)\b',s):candidates.update(('STOP','HOVER'))
    if re.search(r'\b(?:forward|backward|backwards|back|move|left|right)\b',s):
        candidates.add('MOVE')
    if re.search(r'\b(?:go up|rise|ascend|up|climb)\b',s):candidates.add('UP')
    if re.search(r'\b(?:go down|descend|down|lower)\b',s):candidates.add('DOWN')
    return candidates or None

def preview_voice(utterance):
    if not isinstance(utterance,str) or not utterance.strip() or len(utterance)>300:
        raise ValueError('Invalid voice transcript')
    normalized=normalize_utterance(utterance.strip(),'voice')
    return {'heard':utterance.strip(), 'suggested':normalized,
            'changed':normalized != utterance.strip(),
            'review_required':True, 'dispatched':False}

class Service:
    def __init__(self, hu, endpoint=FESTIVAL, token_path=TOKEN, now=time.monotonic):
        self.hu=hu;self.endpoint=endpoint;self.token_path=token_path;self.now=now
        self.lock=threading.RLock();self.pending={};self.voice_pending={};self.events=[]
        self.session_id='HU-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+uuid.uuid4().hex[:5]
        self.log_path=Path.home()/'hu-telepresence-gazebo'/'logs'/'hu_real_decisions.jsonl'
        self.log_path.parent.mkdir(parents=True,exist_ok=True)

    def publish(self,action,targets,params,confidence,confirmed=False,symbolic=False):
        payload={'action':action,'targets':targets,'params':params,'confidence':confidence,
                 'operator_confirmed':confirmed,'symbolic_emergency':symbolic}
        return get_json(self.endpoint+'/api/hu/dispatch',payload,timeout=5.0,
                        headers={'X-HU-Token':self.token_path.read_text().strip()})

    def publish_mission(self, action, targets, params):
        # New deterministic planner, separate from the original HU neural bridge.
        payload={'action':action,'targets':targets,'params':params,
                 'operator_confirmed':True,'planner':'HU-MISSION-V6'}
        return get_json(self.endpoint+'/api/mission/dispatch',payload,timeout=5.0,
                        headers={'X-HU-Token':self.token_path.read_text().strip()})

    def record(self,entry):
        with self.lock:
            self.events.insert(0,entry)
            del self.events[60:]
            with self.log_path.open('a',encoding='utf-8') as f:
                f.write(json.dumps({'timestamp_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
                                    'event_epoch_ms':int(time.time()*1000),'session_id':self.session_id,
                                    **entry})+'\n')
        return entry

    def command(self, utterance, source='typed', voice_reviewed=False, original_transcript=None):
        if not isinstance(utterance,str) or not utterance.strip() or len(utterance)>300:
            raise ValueError('Utterance must have 1 to 300 characters')
        if source not in ('typed','voice'):raise ValueError('Unknown input source')
        raw=utterance.strip();normalized=normalize_utterance(raw,source)
        if source=='voice' and not voice_reviewed:
            return self.record({'cmd_id':uuid.uuid4().hex[:8],
                 'utterance':raw,'normalized_utterance':normalized,
                 'source':source,'band':'VOICE_REVIEW','dispatched':False,
                 'reason':'Review recognized text and press SEND before HU inference'})
        stripped=emergency_key(normalized) or normalized.lower().strip(' .!?')
        if stripped in EMERGENCY:
            safety_action='emergency_hover' if stripped in EMERGENCY_HOVER else 'emergency_land'
            reply={'cmd_id':uuid.uuid4().hex[:8], 'utterance':raw,'action':('HOVER' if safety_action=='emergency_hover' else 'LAND'),
                   'targets':list(NAMES),'params':{},'confidence':1.0,
                   'band':'EMERGENCY','reason':'HU symbolic emergency; model bypass',
                   'latency_ms':0,'dispatched':False,'source':source,
                   'original_transcript':original_transcript if source=='voice' else None}
            try:
                outcome=self.publish(safety_action,list(NAMES),{},1.0,symbolic=True)
                reply['dispatched']=outcome.get('ok') is True
                reply['decision_source']='SYMBOLIC SAFETY (NOT TRAINED HU)'
            except DispatchError as ex:reply['reason']=str(ex)
            return self.record(reply)
        # Mission/formation syntax is NOT in the frozen nine-action HU model.
        # Never feed it to the primitive classifier (where FORM V can become STOP).
        # A named, deterministic mission proposal must be confirmed by the operator.
        mission_started=time.perf_counter()
        mission=parse_mission(normalized)
        unrecognized=bool(mission and mission.get('unrecognized'))
        if unrecognized:
            mission=None   # let the trained HU model show its own decision (it has a REFUSE class)
        if mission is not None:
            cid=uuid.uuid4().hex[:8]
            reply={'cmd_id':cid,'utterance':raw,'normalized_utterance':normalized,
                   'original_transcript':original_transcript if source=='voice' else None,
                   'source':source,'decision_source':'DETERMINISTIC MISSION PLANNER',
                   'dispatched':False,'latency_ms':round(1000*(time.perf_counter()-mission_started),2)}
            if 'error' in mission:
                reply.update({'band':'REFUSE','action':'MISSION_UNSUPPORTED',
                              'targets':[],'reason':mission['error']})
            else:
                reply.update({'band':'CONFIRM','action':mission['action'].upper(),
                              'targets':mission['targets'],'params':mission['params'],
                              'reason':mission['label']+' — '+mission['reason']})
                with self.lock:
                    self.pending[cid]={'kind':'mission','action':mission['action'],
                                       'targets':mission['targets'],'params':mission['params'],
                                       'at':self.now()}
            return self.record(reply)
        # Existing, genuinely trained HU command path remains unchanged.
        r=self.hu.parse(normalized)
        cid=uuid.uuid4().hex[:8]
        reply={'cmd_id':cid,**r,'utterance':raw,'normalized_utterance':normalized,
               'original_transcript':original_transcript if source=='voice' else None,
               'decision_source':'REAL HU MODEL', 'source':source,'dispatched':False}
        # When the human explicitly says TAKE OFF, never execute a STOP/UP/etc.
        # Neural confidence does not override a contradictory utterance.
        intent=explicit_primitive_intent(normalized)
        actual_action=str(reply.get('action','')).upper()
        if unrecognized and reply.get('band')!='REFUSE':
            # Risk-asymmetric: the sentence matched no explicit flight command, so a non-REFUSE
            # neural prediction is never executed, whatever its confidence.
            reply['band']='REFUSE'
            reply['reason']=('No explicit flight command in the sentence; HU predicted '+actual_action+
                             f" ({reply.get('confidence')}) but unrecognized sentences are never executed")
        elif unrecognized:
            reply['reason']='HU refused: not a flight command'
        if intent and actual_action not in intent:
            reply['band']='REFUSE'
            reply['reason']=('Command-intent mismatch: text specifies '+'/'.join(sorted(intent))+
                             '; HU predicted '+actual_action+' (no dispatch)')
        # Reject wrong-aircraft dispatch even if the neural target head is overconfident.
        # For novel fleet names this will expose an HU match-head limitation,
        # not silently repair it with an untrained or fake parser.
        expected=stated_targets(normalized)
        actual=reply.get('targets',[])
        if expected is not None and set(actual)!=set(expected):
            reply['band']='REFUSE'
            reply['reason']=('Named-address safety guard: requested '+', '.join(expected)+
                             '; HU selected '+(', '.join(actual) if actual else '(none)'))
        if reply.get('params',{}).get('range_violation'):
            reply['band']='REFUSE'
            reply['reason']='unsafe parameter: '+str(reply['params']['range_violation'])
        band=reply.get('band')
        if band in ('EXECUTE','CONFIRM'):
            try:
                act,pr=translate(reply.get('action'),reply.get('params',{}))
                tg=reply.get('targets',[])
                if not isinstance(tg,list) or not tg or any(n not in NAMES for n in tg):
                    raise ValueError('Unknown or missing HU target')
                confidence=float(reply.get('confidence',0))
                if not 0<=confidence<=1:raise ValueError('Invalid HU confidence')
                if band=='EXECUTE':
                    if confidence<.85:raise ValueError('HU execution threshold not met')
                    outcome=self.publish(act,tg,pr,confidence)
                    reply['dispatched']=outcome.get('ok') is True
                elif band=='CONFIRM':
                    if confidence<.55:raise ValueError('HU confirmation threshold not met')
                    with self.lock:
                        self.pending[cid]={'action':act,'targets':tg,'params':pr,
                                           'confidence':confidence,'at':self.now()}
            except (ValueError,DispatchError) as ex:
                reply['band']='REFUSE';reply['reason']=str(ex)
        return self.record(reply)

    # HU_HANDSFREE_V3: The browser may send unreviewed, correctly transcribed
    # voice commands through this route. Any auto-correction must first be
    # acknowledged by a separate spoken confirmation, with a server-held ID.
    # No model decisions are changed here: command() applies the original
    # risk bands and exact-target safety guard.
    def voice_auto(self, utterance):
        if not isinstance(utterance, str) or not utterance.strip() or len(utterance)>300:
            raise ValueError('Invalid voice transcript')
        raw=utterance.strip()
        suggested=normalize_utterance(raw, 'voice')
        # Emergency phrases never wait for a spoken "yes" to a speech correction
        # (e.g. heard "yella land" -> "yalla land" must land immediately).
        if emergency_key(suggested):
            return self.command(suggested, 'voice', voice_reviewed=True,
                                original_transcript=raw)
        if suggested != raw:
            cid=uuid.uuid4().hex[:12]
            with self.lock:
                self.voice_pending.clear()  # Only one pending voice clarification.
                self.voice_pending[cid]={'utterance':suggested,'heard':raw,'at':self.now()}
            return self.record({'cmd_id':cid,'source':'voice','utterance':raw,
                                'normalized_utterance':suggested,'original_transcript':raw,
                                'suggested':suggested,'band':'VOICE_CLARIFY',
                                'reason':'Speech correction requires spoken confirmation; no auto-dispatch',
                                'dispatched':False})
        return self.command(raw, 'voice', voice_reviewed=True,
                            original_transcript=raw)

    def voice_accept(self, cmd_id):
        with self.lock:
            candidate=self.voice_pending.pop(cmd_id,None)
        if candidate is None: raise ValueError('Voice clarification missing or already used')
        if self.now()-candidate['at']>75:
            raise ValueError('Voice clarification expired; repeat the command')
        return self.command(candidate['utterance'], 'voice', voice_reviewed=True,
                            original_transcript=candidate['heard'])

    def cancel(self,cmd_id):
        with self.lock:
            removed=self.pending.pop(cmd_id,None)
            clarification=self.voice_pending.pop(cmd_id,None)
        return {'band':'CANCELLED','cmd_id':cmd_id,'dispatched':False,
                'reason':'Spoken cancellation','ok':bool(removed or clarification)}

    def confirm(self, cmd_id):
        with self.lock:
            p=self.pending.pop(cmd_id,None)
        if p is None:raise ValueError('Confirmation missing, expired, or already used')
        if self.now()-p['at']>90:raise ValueError('Confirmation expired after 90 seconds')
        if p.get('kind')=='mission':
            r=self.publish_mission(p['action'],p['targets'],p['params'])
            source='DETERMINISTIC MISSION PLANNER'
        else:
            r=self.publish(p['action'],p['targets'],p['params'],p['confidence'],confirmed=True)
            source='REAL HU MODEL'
        self.record({'cmd_id':cmd_id,'band':'CONFIRMED','targets':p['targets'],
                     'decision_source':source,'action':p['action'],'dispatched':r.get('ok') is True})
        return r

LATENCY_LOG=Path.home()/'hu-telepresence-gazebo'/'logs'/'hu_voice_to_motion.jsonl'
LATENCY_LOCK=threading.Lock()

def save_latency(record):
    """Persist browser-measured timing without changing HU execution or thresholds."""
    if not isinstance(record,dict) or record.get('version')!='hu-latency-v5':
        raise ValueError('Invalid latency record')
    if record.get('source') not in ('typed','voice'):
        raise ValueError('Invalid latency source')
    if not isinstance(record.get('outcome'),str) or len(record['outcome'])>60:
        raise ValueError('Invalid latency outcome')
    if len(str(record.get('utterance','')))>300:
        raise ValueError('Invalid latency utterance')
    for key,value in record.items():
        if key.endswith('_ms') and value is not None and not isinstance(value,dict):
            if not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value) or value < -250 or value > 600000:
                raise ValueError('Invalid latency millisecond value: '+key)
    targets=record.get('targets',[])
    if not isinstance(targets,list) or len(targets)>len(NAMES) or any(t not in NAMES for t in targets):
        raise ValueError('Invalid latency targets')
    LATENCY_LOG.parent.mkdir(parents=True,exist_ok=True)
    with LATENCY_LOCK:
        with LATENCY_LOG.open('a',encoding='utf-8') as out:
            out.write(json.dumps(record,ensure_ascii=False)+'\n')
    return {'ok':True,'saved':True}

class Handler(BaseHTTPRequestHandler):
    service=None; dashboard=None
    def log_message(self,*args):pass
    def send_json(self,status,obj):
        raw=json.dumps(obj).encode()
        self.send_response(status);self.send_header('Content-Type','application/json')
        self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(raw)))
        self.end_headers();self.wfile.write(raw)
    def do_GET(self):
        if self.path=='/':
            raw=self.dashboard.read_bytes();self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        elif self.path=='/hu_latency.js':
            raw=Path(__file__).with_name('latency_monitor.js').read_bytes()
            self.send_response(200)
            self.send_header('Content-Type','text/javascript; charset=utf-8')
            self.send_header('Cache-Control','no-store')
            self.send_header('Content-Length',str(len(raw)))
            self.end_headers();self.wfile.write(raw)
        elif self.path=='/api/state':
            try:self.send_json(200,get_json(FESTIVAL+'/api/state'))
            except Exception as ex:self.send_json(503,{'error':str(ex)})
        elif self.path=='/api/health':self.send_json(200,
            {'model':'REAL HU','checkpoint_loaded':True,'model_route':'FROZEN_HU_WITH_DETERMINISTIC_MISSION_EXTENSION',
             'session_id':self.service.session_id,'roster':list(NAMES)})
        else:self.send_json(404,{'error':'Not found'})
    def do_POST(self):
        # Block cross-site requests from other web pages open in the browser.
        port=self.server.server_port
        origin=self.headers.get('Origin')
        if origin and origin not in (f'http://127.0.0.1:{port}',f'http://localhost:{port}'):
            return self.send_json(403,{'error':'Cross-origin request rejected'})
        try:
            count=int(self.headers.get('Content-Length','0'))
            if not 0<count<=4096:raise ValueError('Invalid request size')
            payload=json.loads(self.rfile.read(count))
            if self.path=='/api/hu/latency':
                result=save_latency(payload)
            elif self.path=='/api/hu/voice-preview':
                result=preview_voice(payload.get('utterance'))
            elif self.path=='/api/hu/voice-auto':
                result=self.service.voice_auto(payload.get('utterance'))
            elif self.path=='/api/hu/voice-accept':
                result=self.service.voice_accept(str(payload.get('cmd_id','')))
            elif self.path=='/api/hu/cancel':
                result=self.service.cancel(str(payload.get('cmd_id','')))
            elif self.path=='/api/hu/command':
                result=self.service.command(payload.get('utterance'),
                    payload.get('source','typed'),
                    voice_reviewed=payload.get('voice_reviewed') is True,
                    original_transcript=payload.get('original_transcript'))
            elif self.path=='/api/hu/confirm':
                result=self.service.confirm(str(payload.get('cmd_id','')))
            else:return self.send_json(404,{'error':'Not found'})
            self.send_json(200,result)
        except (ValueError,TypeError,KeyError) as e:self.send_json(400,{'error':str(e)})
        except Exception as e:self.send_json(503,{'error':str(e)})

def sample_telemetry():
    logfile=Path.home()/'hu-telepresence-gazebo'/'logs'/'hu_live_telemetry.jsonl'
    logfile.parent.mkdir(parents=True,exist_ok=True)
    while True:
        try:
            state=get_json(FESTIVAL+'/api/state',timeout=2.5)
            with logfile.open('a',encoding='utf-8') as f:
                f.write(json.dumps({'timestamp_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
                                    'drones':state.get('drones',[])})+'\n')
        except Exception:pass
        time.sleep(1.5)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--checkpoint',default='')
    ap.add_argument('--port',type=int,default=8100)
    opts=ap.parse_args()
    from hu.gateway.hu_service import HU
    choices=[Path(opts.checkpoint)] if opts.checkpoint else [Path('hu/models/hu_heads_v1_1.pt'),Path('hu/models/hu_heads_v1.pt')]
    ckpt=next((p for p in choices if p.exists()),None)
    if ckpt is None:
        raise SystemExit('HU trained heads checkpoint missing. Check: ls hu/models/*.pt and rerun --checkpoint PATH')
    print(f'Loading REAL HU, checkpoint={ckpt}, five-drone roster={ROSTER}',flush=True)
    hu=HU(ckpt=str(ckpt),roster_path=str(ROSTER))
    service=Service(hu)
    Handler.service=service
    Handler.dashboard=Path(__file__).with_name('hu_live_dashboard.html')
    threading.Thread(target=sample_telemetry,daemon=True).start()
    srv=ThreadingHTTPServer(('127.0.0.1',opts.port),Handler)
    print(f'REAL HU live on http://localhost:{opts.port} — five drones',flush=True)
    try:srv.serve_forever()
    except KeyboardInterrupt:pass
    finally:srv.server_close()

if __name__=='__main__':main()
