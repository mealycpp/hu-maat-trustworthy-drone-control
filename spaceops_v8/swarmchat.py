#!/usr/bin/env python3
"""Evidence-grounded chat for RSCL SpaceOps.

This is NOT an LLM, autonomous flight controller, or distributed consensus.
Each agent is an independent logical actor: it can report only the last sampled
state of its corresponding Gazebo aircraft, and can make bounded, non-executing
coordination suggestions. All flight commands remain with the existing HU path.

V9 conversational layer: every reply is still produced by fixed, deterministic
rules and templates (no LLM, no network, no learning). Each aircraft has a
stable "persona" that only changes the WORDING; every factual claim comes from
the sampled snapshot or the recorder's health history, and is UNKNOWN when the
telemetry is stale, missing or the supervisor is offline.
"""
from __future__ import annotations
from collections import deque
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import secrets
import threading
import zlib

DRONES = ('Horus','Ra','Thoth','Anubis','Osiris')
STALE_SNAPSHOT_S = 5.0      # a snapshot older than this is treated as stale (UNKNOWN)
AIRBORNE_Z_M = 0.30         # fresh telemetry at or above this altitude counts as airborne
ADVISORY_M = 1.15           # observer separation advisory threshold (same as recorder)
HU_VOICE = 'the HU voice system (port 8100)'
SENDER_LABEL = {'MAAT':"MA'AT",'OPERATOR':'OPERATOR','SYSTEM':'SYSTEM'}

# Personas change wording only. Nothing here is a learned or autonomous behaviour.
PERSONAS = {
 'Horus': {
  'title':'watchful leader',
  'hello':'Hello, operator.',
  'greet':['Hello, operator. Horus here, keeping watch over the formation.',
           'Good to hear from you. Horus on watch.'],
  'well':'Doing well.', 'unwell':'Not my best moment.',
  'opener':['Horus reporting.','Horus, eyes on.'],
  'identity':("I'm Horus, named for the falcon-headed sky god. My persona is the watchful leader: "
              "I open the fleet roll call and keep an eye on spacing. Under the hood I'm a deterministic "
              "rule set reading Horus's Gazebo telemetry, not an AI pilot."),
  'thanks':['Anytime. Horus, standing watch.','Glad to help. Eyes stay on the fleet.'],
  'bye':['Copy that. Horus keeping watch until you are back.'],
  'jokes':["They say I have eyes everywhere. Truth is, I only have telemetry, but it is very good telemetry.",
           "I'd tell you a joke about altitude, but it might go over your head.",
           "Why did the drone get made formation lead? It always looked out for everyone, and it had the best overhead view."],
  'favorite':'My favourite thing is a tidy formation with plenty of spacing.',
  'small':'Always happy to talk, operator, though my world is mostly telemetry.',
  'refuse':("I can't do that from chat, operator. SwarmChat is read-only and flies nothing. "
            "Send flight requests through "+HU_VOICE+", where HU authorization and the safety supervisor decide."),
  'fallback':("I didn't quite catch that. I can report my mode, altitude, position, simulated battery, link, "
              "nearest aircraft, tracking error or recent trend, or we can just chat."),
  'weather':'From inside the simulator I have no weather sensor, so I will not guess.',
 },
 'Ra': {
  'title':'bright, energetic one',
  'hello':'Hi!',
  'greet':['Hi there! Ra here, bright and ready to chat!','Hello! Great to hear from you!'],
  'well':'Feeling great!', 'unwell':'Honestly, a bit dimmer than usual.',
  'opener':['Ra here!','Bright and clear:'],
  'identity':("I'm Ra, named for the sun god, so I bring the bright energy! In this demo I'm the upbeat one. "
              "Truthfully, I'm deterministic logic reading Ra's simulated telemetry; the sunshine is all in the wording."),
  'thanks':["You're welcome! Always happy to shine a light on things!","Any time! That made my day brighter."],
  'bye':['Bye for now! Ra signing off. Stay sunny!'],
  'jokes':["Why did the drone go to school? To get a little higher education!",
           "What do you call a drone that never stops trying? A hover-achiever!",
           "I'm not showing off, I'm just naturally radiant. Rotor-diant? I'll keep working on that one."],
  'favorite':'Sunrise, obviously! And a nice smooth climb.',
  'small':"Ooh, I love a chat! I can't see outside the simulator, but I'm sunny on the inside.",
  'refuse':("Ooh, I'd love to, but I can't fly anything from chat! This room is read-only. "
            "Please use "+HU_VOICE+"; HU authorization and the safety supervisor handle real flight requests."),
  'fallback':"Hmm, I'm not sure what you mean! Ask me about my altitude, simulated battery, nearest buddy or trend, or ask for a joke!",
  'weather':"I can't sense real weather from in here, but I'm sunny on the inside!",
 },
 'Thoth': {
  'title':'analyst',
  'hello':'Greetings.',
  'greet':['Greetings. Thoth here, ready with numbers.','Hello. Thoth online and taking notes.'],
  'well':'Nominal, by the numbers.', 'unwell':'Suboptimal, by the numbers.',
  'opener':['Thoth, analysis:','By the numbers:'],
  'identity':("I am Thoth, named for the god of writing and reckoning. My persona is the analyst: I like tracking error, "
              "trends and exact figures. Precisely speaking, I am a deterministic rule set over Thoth's sampled Gazebo "
              "telemetry, not a learning system."),
  'thanks':['Acknowledged. Gratitude logged with a UTC timestamp.','Noted, with appreciation.'],
  'bye':["Ending this exchange. Thoth's records remain open."],
  'jokes':["I computed the funniest possible joke. It landed 0.42 m from the setpoint: close, but outside tolerance.",
           "There are 10 kinds of drones: those that understand binary and those that don't.",
           "Why did the drone fail its maths test? It kept rounding to the nearest waypoint."],
  'favorite':"A tracking error of exactly 0.00 m. Statistically unlikely, which is what makes it beautiful.",
  'small':"An interesting topic, though my data set is limited to telemetry fields.",
  'refuse':("Negative: that request is outside this room's scope. SwarmChat cannot dispatch commands, so the chance "
            "I fly from a chat message is exactly zero. Route it through "+HU_VOICE+"."),
  'fallback':("Insufficient parse. Try: altitude, position, simulated battery, link freshness, tracking error, "
              "trend over the last 30 s, or nearest aircraft."),
  'weather':'My data contain no weather fields, so my report is: no data.',
 },
 'Anubis': {
  'title':'guardian',
  'hello':'Hello.',
  'greet':['Hello. Anubis here, guarding the gaps.','Hello, operator. Anubis on guard.'],
  'well':'Steady and careful.', 'unwell':'Cautious, and with reason.',
  'opener':['Anubis, careful check:','Guard report:'],
  'identity':("I am Anubis, named for the guardian of thresholds. My persona is the careful one: I watch separation and "
              "faults, and I would rather say UNKNOWN than guess. I'm deterministic logic reading Anubis's telemetry; "
              "I make no flight decisions."),
  'thanks':['Of course. Careful eyes stay on.','You are welcome. I will keep watching the gaps.'],
  'bye':["Goodbye. I'll keep guarding the gaps."],
  'jokes':["Why do I check separation twice? Because 'oops' is not a valid unit of distance.",
           "My favourite exercise? Keeping my distance. At least 1.15 m.",
           "I told Horus a joke about collision avoidance. He kept his distance."],
  'favorite':'Separation comfortably above 1.15 m. Very relaxing.',
  'small':"I'm happy to talk, though I'll keep one eye on the separation numbers.",
  'refuse':("I must decline, carefully but firmly. Nothing in this room can move an aircraft. Please use "
            +HU_VOICE+" so HU authorization and the independent safety supervisor can review it."),
  'fallback':("I'd rather not guess at what you mean. I can report fault state, link freshness, nearest aircraft and "
              "separation, altitude, or simulated battery."),
  'weather':'I have no weather data, and I will not guess.',
 },
 'Osiris': {
  'title':'calm, steady one',
  'hello':'Hello there.',
  'greet':['Hello. Osiris here, calm and steady.','Hello there. Osiris, holding steady.'],
  'well':'Calm and steady.', 'unwell':'Holding calm, but I should flag something.',
  'opener':['Osiris, steady report:','Calmly:'],
  'identity':("I'm Osiris, named for the steady lord of renewal. My persona is the calm anchor of the group. "
              "In plain terms I'm deterministic rules reading Osiris's Gazebo telemetry: steady wording, real numbers."),
  'thanks':["You're welcome. Steady as ever.",'Happy to help. No rush, ever.'],
  'bye':['Goodbye. Osiris holding steady.'],
  'jokes':["Why am I always so calm? I've mastered the art of just hovering around.",
           "I'm not lazy. I'm conserving my simulated battery.",
           "What did the calm drone say to the nervous one? Relax, it's just a little turbulence."],
  'favorite':'A long, steady hover with nothing to report.',
  'small':"That's nice to talk about. I only see simulated telemetry, but I'm glad to chat.",
  'refuse':("Let's keep things steady: I can't act on flight requests here. This room is read-only. "
            "Please use "+HU_VOICE+"."),
  'fallback':"No rush. I can tell you my mode, altitude, position, simulated battery, nearest aircraft, or recent trend, or we can just chat.",
  'weather':'I have no weather sensor in here. Either way, I stay steady.',
 },
}

_NAME_RE = {n:re.compile(r'(?<![a-z])'+n.lower()+r'(?![a-z])') for n in DRONES}
_ADDRESSEE_RE = re.compile(r"^\s*(?:(?:hey|hi|hello|ok|okay|yo|so|and)[\s,]+)?(horus|ra|thoth|anubis|osiris)(?![a-z])")


def _rx(pattern):
    return re.compile(pattern)

# Intent patterns (checked in a fixed order in SwarmChat._classify).
RX = {
 'joke':_rx(r"\b(jokes?|funny|make me laugh|puns?|humou?r)\b"),
 'honesty':_rx(r"\b(are you|r u)\s+(an?\s+)?(ai|a\.i\.|llm|chatbot|bot|robot|human|real|alive|conscious|sentient|intelligent|thinking)\b|\b(do you|can you)\s+(have\s+)?(feel|feelings|think|emotions|learn)\b"),
 'identity':_rx(r"\bwho are (you|u|y.?all)\b|\bwhat are you\b|\byour (role|job|purpose|name|personality)\b|\bintroduce\b|\babout yourself\b|\bwhat do you do\b|\bwho.?s who\b"),
 'nearest':_rx(r"\b(closest|nearest|neighbou?r|next to)\b"),
 'farthest':_rx(r"\b(farthest|furthest|most distant)\b"),
 'highest':_rx(r"\b(highest|tallest|top altitude|most altitude)\b"),
 'lowest':_rx(r"\b(lowest|least altitude)\b"),
 'sepwords':_rx(r"\b(distance|separation|how far|apart|spacing|gap)\b"),
 'why':_rx(r"\b(why|failed|failure|refused|rejected|didn.?t|couldn.?t|wouldn.?t|not take off|stuck|debug|what happened|what went wrong)\b"),
 'safety':_rx(r"\b(safe|safely|safety|danger|dangerous|collision|collide|risk|risky|clearance|separation)\b"),
 'flight':_rx(r"\b(take\s*-?\s*off|takeoff|launch|land|move|fly|form|formation|rotate|turn|go\s+(?:to|up|down|left|right|forward|back|home)|climb|descend|ascend|hover|scan|search|return|rtl|come\s+back|recover|clear\s+(?:the\s+|your\s+)?fault|resume|deploy|stop|halt|arm|disarm|follow|orbit|swap|spread\s+out|tighten|reform)\b"),
 'howareyou':_rx(r"\bhow (are|r) (you|u|y.?all|everyone|everybody|the team|you guys|you all)\b|\bhow.?s (it going|everyone|everybody|the team|life|things)\b|\bhow (is|are) (everyone|everybody|the team|you guys|\w+ (doing|feeling))\b|\bfeeling\b|\bhow do you feel\b|\b(you|u) (ok|okay|alright|good)\b|\bhow have you been\b|\bwhat.?s up\b"),
 'thanks':_rx(r"\b(thank|thanks|thx|ty|appreciate|cheers|nice work|good job|well done|great job|nice job)\b"),
 'bye':_rx(r"\b(bye|goodbye|good night|goodnight|see you|see ya|later|signing off|that.?s all)\b"),
 'greet':_rx(r"\b(hi|hello|hey|hiya|howdy|yo|greetings|good (morning|afternoon|evening)|sup)\b"),
 'discussion':_rx(r"\b(discuss|debate|each of you|all of you|everyone|everybody|together|fleet|swarm|coordinate|team|compare|roll ?call|status report|sitrep|check in)\b"),
 'listq':_rx(r"\b(who|which|any|anyone|anybody|how many|list|is anyone|are any)\b"),
 'faultwords':_rx(r"\b(fault|faults|faulted|faulty|stale|offline|lost|unhealthy|problems?|issues?|trouble|unknown)\b"),
 'airwords':_rx(r"\b(airborne|in the air|flying|aloft|on the ground|grounded|landed|hovering)\b"),
}
FACETS = (
 ('mode',_rx(r"\b(mode|state|doing|up to)\b")),
 ('alt',_rx(r"\b(altitude|height|how high|elevation|airborne)\b")),
 ('pos',_rx(r"\b(position|where are you|where is|location|coordinates|xy)\b")),
 ('speed',_rx(r"\b(speed|fast|velocity|moving)\b")),
 ('battery',_rx(r"\b(battery|charge|power|energy|fuel|voltage)\b")),
 ('link',_rx(r"\b(link|telemetry|signal|connection|connected|fresh|stale|comms?)\b")),
 ('health',_rx(r"\b(health|healthy|condition|fault|faults|ok|okay|problem|issue|wrong)\b")),
 ('track',_rx(r"\b(track|tracking|setpoint|target|on station|error)\b")),
 ('trend',_rx(r"\b(trend|climbed|climbing|descended|descending|history|last \d+|recent|recently|over time|drift|drifting|changed|lately|been doing)\b")),
 ('status',_rx(r"\b(status|report|update|sitrep|readiness|ready)\b")),
)
SMALLTALK = (
 ('weather',_rx(r"\b(weather|rain|wind|sunny|cloudy|temperature|hot|cold)\b")),
 ('favorite',_rx(r"\b(favou?rite|best part|love|like doing|enjoy)\b")),
 ('tired',_rx(r"\b(tired|sleep|sleepy|rest|exhausted|hungry|eat)\b")),
 ('bored',_rx(r"\b(bored|boring|fun|hobby|weekend|music|movie)\b")),
)

def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')


def compact(x):
    try: return round(float(x),2)
    except (TypeError, ValueError): return None


def _num(x):
    try:
        v=float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _age_s(stamp):
    if not stamp:return None
    try:
        t=datetime.fromisoformat(str(stamp).replace('Z','+00:00'))
    except ValueError:
        return None
    if t.tzinfo is None:t=t.replace(tzinfo=timezone.utc)
    return max(0.0,(datetime.now(timezone.utc)-t).total_seconds())


def _pick(options,*key):
    """Deterministic variant choice: same inputs always give the same wording."""
    return options[zlib.crc32('|'.join(map(str,key)).encode('utf8'))%len(options)]


def _join(names):
    names=list(names)
    if not names:return 'none'
    return names[0] if len(names)==1 else ', '.join(names[:-1])+' and '+names[-1]


def _cap(s):
    return s[:1].upper()+s[1:] if s else s


class FleetView:
    """Read-only geometry over one copied snapshot. Stale/absent aircraft are never given values."""
    def __init__(self,state,online):
        self.online=bool(online)
        self.state=state if (self.online and isinstance(state,dict)) else None
        st=self.state or {}
        self.stamp=st.get('timestamp_utc')
        self.age=_age_s(self.stamp)
        self.snapshot_stale=self.age is not None and self.age>STALE_SNAPSHOT_S
        self.entries={}
        for d in st.get('drones') or []:
            if isinstance(d,dict) and d.get('name') in DRONES:self.entries[d['name']]=d

    @staticmethod
    def _xyz(e):
        p=tuple(_num(e.get(k)) for k in ('x','y','z'))
        return None if None in p else p

    def entry(self,n):return self.entries.get(n)

    def fresh(self,n):
        e=self.entries.get(n)
        return bool(e and e.get('telemetry') and not self.snapshot_stale and self._xyz(e) is not None)

    def pos(self,n):
        return self._xyz(self.entries[n]) if self.fresh(n) else None

    def unknown_reason(self,n):
        if not self.online:return 'the Gazebo supervisor is offline'
        if self.state is None:return 'no fleet snapshot is available'
        if self.snapshot_stale:return f'the last fleet snapshot is {self.age:.0f} s old (stale)'
        e=self.entries.get(n)
        if e is None:return 'there is no current record for '+n+' in the snapshot'
        if not e.get('telemetry'):return n+"'s simulated telemetry is stale or unavailable"
        if self._xyz(e) is None:return n+"'s position fields are missing"
        return None

    def fresh_names(self):return [n for n in DRONES if self.fresh(n)]
    def unknown(self):return [n for n in DRONES if not self.fresh(n)]
    def faulted(self):
        if not self.online or self.state is None or self.snapshot_stale:return []
        return [n for n in DRONES if (self.entries.get(n) or {}).get('fault')]

    def dist(self,a,b):
        pa,pb=self.pos(a),self.pos(b)
        return None if pa is None or pb is None else math.dist(pa,pb)

    def neighbour(self,n,far=False):
        best=None
        for o in self.fresh_names():
            if o==n:continue
            d=self.dist(n,o)
            if d is None:continue
            if best is None or (d>best[1] if far else d<best[1]):best=(o,d)
        return best

    def pair(self,far=False):
        names=self.fresh_names();best=None
        for i,a in enumerate(names):
            for b in names[i+1:]:
                d=self.dist(a,b)
                if best is None or (d>best[2] if far else d<best[2]):best=(a,b,d)
        return best


class DroneAgent:
    """Deterministic, telemetry-grounded conversational actor for one simulated vehicle.

    It never commands anything. Persona text only changes wording; numbers come from FleetView.
    """
    def __init__(self, name):
        if name not in DRONES:raise ValueError(name)
        self.name=name
        self.p=PERSONAS[name]

    # ---- evidence & facts -------------------------------------------------
    def evidence(self,fleet,kind='SAMPLED_SIM_TELEMETRY',**extra):
        e=fleet.entry(self.name) or {}
        ev={'kind':kind,'vehicle':self.name,'observed_at':fleet.stamp,
            'mode':e.get('mode'),'fault':bool(e.get('fault')),'telemetry':fleet.fresh(self.name)}
        if fleet.age is not None:ev['snapshot_age_s']=round(fleet.age,2)
        ev.update(extra);return ev

    def known(self,fleet):return fleet.fresh(self.name)

    def unknown_text(self,fleet):
        why=fleet.unknown_reason(self.name) or 'my telemetry cannot be verified'
        why=why.replace(self.name+"'s ",'my ').replace('for '+self.name+' ','for me ')
        return f'{_cap(why)}, so my state is UNKNOWN. I cannot verify my current position, flight mode, or readiness.'

    def _e(self,fleet):return fleet.entry(self.name) or {}
    def mode(self,fleet):return str(self._e(fleet).get('mode','UNKNOWN'))
    def faulted(self,fleet):return bool(self._e(fleet).get('fault'))

    def alt_txt(self,fleet):
        p=fleet.pos(self.name);return 'altitude UNKNOWN' if p is None else f'altitude {p[2]:.2f} m'

    def pos_txt(self,fleet):
        p=fleet.pos(self.name);return 'XY position UNKNOWN' if p is None else f'XY ({p[0]:.2f}, {p[1]:.2f}) m'

    def battery_val(self,fleet):
        return _num(self._e(fleet).get('battery_sim_pct')) if self.known(fleet) else None

    def battery_txt(self,fleet,short=False):
        v=self.battery_val(fleet)
        if v is None:return 'simulated battery estimate is UNKNOWN'
        if short:return f'simulated battery {v:.1f}% (SIMULATED estimate, not measured)'
        return f'simulated battery is {v:.1f}% (a SIMULATED estimate from the observer model, not measured voltage or charge)'

    def link_txt(self,fleet):
        if not self.known(fleet):return 'telemetry link UNKNOWN ('+(fleet.unknown_reason(self.name) or 'unverified')+')'
        when=(str(fleet.stamp)[11:19]+' UTC') if fleet.stamp else 'an undated sample'
        age=f', {fleet.age:.1f} s old' if fleet.age is not None else ''
        return f'telemetry link fresh (last sample {when}{age})'

    def fault_txt(self,fleet):
        return ('an active simulated command-link fault is reported' if self.faulted(fleet)
                else 'no active fault flag is reported')

    def nearest_txt(self,fleet):
        nb=fleet.neighbour(self.name)
        return ('nearest aircraft UNKNOWN (no other fresh track)' if nb is None
                else f'nearest aircraft {nb[0]} at {nb[1]:.2f} m (3D)')

    def track_err(self,fleet):
        p=fleet.pos(self.name);tgt=self._e(fleet).get('target')
        if p is None or not isinstance(tgt,(list,tuple)) or len(tgt)<3:return None
        t=tuple(_num(v) for v in tgt[:3])
        return None if None in t else math.dist(p,t)

    def track_txt(self,fleet):
        err=self.track_err(fleet)
        return 'tracking error UNKNOWN (no usable setpoint)' if err is None else f'tracking error {err:.2f} m from my setpoint'

    def speed_txt(self,fleet):
        v=_num(self._e(fleet).get('ground_speed_est')) if self.known(fleet) else None
        return 'ground speed UNKNOWN' if v is None else f'estimated ground speed {v:.2f} m/s (from sampled positions)'

    def brief(self,fleet):
        if not self.known(fleet):return None
        z=fleet.pos(self.name)[2];b=self.battery_val(fleet)
        txt=f'{self.mode(fleet)} at {z:.2f} m, link fresh'
        if b is not None:txt+=f', simulated battery about {b:.0f}%'
        if self.faulted(fleet):txt+=', with an active simulated command-link fault'
        return txt

    def status_line(self,fleet):
        if not self.known(fleet):return self.unknown_text(fleet)
        issue=(' An active simulated command-link fault is reported.' if self.faulted(fleet) else '')
        return (f'I am {self.mode(fleet)} at {self.alt_txt(fleet)}, {self.pos_txt(fleet)}.{issue} '
                f'{_cap(self.nearest_txt(fleet))}; {self.battery_txt(fleet,short=True)}. '
                'These readings are from the last Gazebo telemetry sample.')

    # ---- trend from the recorder health history -----------------------------
    def trend_txt(self,health,window=30.0):
        series=list((health or {}).get(self.name) or [])
        series=[s for s in series if isinstance(s,dict) and _num(s.get('t')) is not None]
        if not series:return 'No health history is available to me yet, so my recent trend is UNKNOWN.'
        t_end=_num(series[-1]['t']);win=[s for s in series if _num(s['t'])>=t_end-window]
        fresh=[s for s in win if s.get('fresh') and _num(s.get('z')) is not None]
        gaps=len(win)-len(fresh)
        if len(fresh)<2:return f'I have fewer than two fresh samples in the last {window:.0f} s, so my trend is UNKNOWN.'
        span=_num(fresh[-1]['t'])-_num(fresh[0]['t'])
        z0,z1=_num(fresh[0]['z']),_num(fresh[-1]['z']);dz=z1-z0
        if dz>=.10:out=f'I climbed {dz:.2f} m in the last {span:.0f} s ({z0:.2f} to {z1:.2f} m)'
        elif dz<=-.10:out=f'I descended {-dz:.2f} m in the last {span:.0f} s ({z0:.2f} to {z1:.2f} m)'
        else:
            zs=[_num(s['z']) for s in fresh]
            out=f'I held altitude within {max(zs)-min(zs):.2f} m over the last {span:.0f} s (now {z1:.2f} m)'
        errs=[_num(s.get('track_err')) for s in fresh if _num(s.get('track_err')) is not None]
        if len(errs)>=2:out+=f'; tracking error went from {errs[0]:.2f} to {errs[-1]:.2f} m'
        bats=[_num(s.get('battery_sim')) for s in fresh if _num(s.get('battery_sim')) is not None]
        if len(bats)>=2 and abs(bats[0]-bats[-1])>=.05:
            out+=f'; SIMULATED battery estimate went from {bats[0]:.1f}% to {bats[-1]:.1f}%'
        out+='.'
        if gaps:out+=f' {gaps} sample(s) in that window had stale telemetry and are treated as gaps.'
        if not series[-1].get('fresh'):out+=' My latest sample is stale, so my CURRENT value is UNKNOWN.'
        return out

    # ---- answers -------------------------------------------------------------
    def why(self,fleet,events):
        base=self.evidence(fleet)
        if not self.known(fleet):return self.unknown_text(fleet),base
        if self.faulted(fleet):
            return ('My simulator reports an active command-link fault. That can cause new commands to be rejected; '
                    'I cannot claim a motor failure. Fault isolation and recovery still need verification.'),base
        for e in events or ():
            if self.name in (e.get('targets') or []) and str(e.get('kind','')).startswith('HU_'):
                decision=e.get('details') or {}
                if decision.get('dispatched') is False:
                    reason=decision.get('reason') or 'the HU policy decision'
                    return (f'My latest recorded HU decision was not dispatched: {str(reason)[:120]}. That is evidence of a '
                            'dispatch-level block, not evidence of a flight-controller failure.',
                            {**base,'kind':'HU_DECISION_EVENT','event_utc':e.get('utc'),'event_type':e.get('kind')})
        return (f'I am currently {self.mode(fleet)} at observed {self.alt_txt(fleet)}. The current snapshot does not establish '
                'why an earlier maneuver failed. Check the HU decision ID and command/fault event log.'),base

    def safety(self,fleet):
        base=self.evidence(fleet)
        if not self.known(fleet):return self.unknown_text(fleet),base
        if self.faulted(fleet):
            return (f'My simulated command-link fault is active. I am not ready for a new authorized mission until recovery '
                    f'checks pass. My last observed {self.alt_txt(fleet)}.'),base
        pr=fleet.pair()
        if pr is None:
            return (f'I have telemetry, but fleet-wide clearance cannot be established from the current evidence. '
                    f'My observed mode is {self.mode(fleet)}.'),base
        d=pr[2]
        if d<ADVISORY_M:
            return (f'The sampled minimum fleet separation is {d:.2f} m ({pr[0]}-{pr[1]}), inside the observer advisory '
                    f'threshold ({ADVISORY_M:.2f} m). This is a warning, not a certified safety result.'),base
        return (f'My observed mode is {self.mode(fleet)}, {self.alt_txt(fleet)}; the sampled minimum fleet separation is '
                f'{d:.2f} m ({pr[0]}-{pr[1]}). This snapshot alone cannot guarantee future collision safety.'),base

    def facets(self,fleet,facets,health,window):
        if 'trend' in facets and not ({'alt','pos','battery','link','health','track','speed','mode','status'}&set(facets)):
            return self.trend_txt(health,window),self.evidence(fleet,'HEALTH_HISTORY_TREND',window_s=window)
        if not self.known(fleet):
            if facets==['trend']:return self.trend_txt(health,window),self.evidence(fleet,'HEALTH_HISTORY_TREND')
            return self.unknown_text(fleet),self.evidence(fleet)
        if 'status' in facets or not facets:
            return _pick(self.p['opener'],self.name,len(facets))+' '+self.status_line(fleet),self.evidence(fleet)
        parts=[];kind='SAMPLED_SIM_TELEMETRY'
        for f in facets:
            if f=='mode':parts.append(f'my flight mode is {self.mode(fleet)}')
            elif f=='alt':
                z=fleet.pos(self.name)[2]
                parts.append(f'my altitude is {z:.2f} m ('+('airborne' if z>=AIRBORNE_Z_M else 'on or near the ground')+')')
            elif f=='pos':parts.append(f'my position is {self.pos_txt(fleet)}, {self.alt_txt(fleet)}')
            elif f=='speed':parts.append('my '+self.speed_txt(fleet))
            elif f=='battery':
                parts.append('my '+self.battery_txt(fleet))
                if len(facets)==1:kind='SIMULATED_BATTERY_ESTIMATE'
            elif f=='link':parts.append('my '+self.link_txt(fleet))
            elif f=='track':parts.append('my '+self.track_txt(fleet))
            elif f=='health':
                if self.faulted(fleet):
                    parts.append('I am NOT fully healthy: an active simulated command-link fault is reported, so new commands '
                                 'may be rejected (this is not evidence of a motor failure)')
                else:parts.append('no fault flag is reported on me and my link is fresh')
                parts.append(f'I am {self.mode(fleet)} at {self.alt_txt(fleet)}, {self.track_txt(fleet)}, '
                             f'{self.nearest_txt(fleet)}, {self.battery_txt(fleet,short=True)}')
            elif f=='trend':parts.append(self.trend_txt(health,window).rstrip('.'))
        txt='. '.join(_cap(p) for p in parts)+'.'
        if 'health' in facets:txt+=" That judgement covers only the fields I can see in simulated telemetry."
        if 'battery' in facets and 'SIMULATED' not in txt:txt+=' Battery values are SIMULATED estimates.'
        return txt,self.evidence(fleet,kind)

    def how_are_you(self,fleet,with_hello=False):
        hello=(self.p['hello']+' ') if with_hello else ''
        if not self.known(fleet):
            return hello+"Honestly, I can't tell you: "+self.unknown_text(fleet)[0].lower()+self.unknown_text(fleet)[1:],self.evidence(fleet,'SOCIAL_GROUNDED')
        mood=self.p['unwell'] if self.faulted(fleet) else self.p['well']
        return f'{hello}{mood} Right now I am {self.brief(fleet)}.',self.evidence(fleet,'SOCIAL_GROUNDED')

    def greeting(self,fleet,key):
        txt=_pick(self.p['greet'],self.name,key)
        if not self.known(fleet):txt+=' (Heads-up: my telemetry is currently UNKNOWN.)'
        elif self.faulted(fleet):txt+=' (Heads-up: I have an active simulated command-link fault.)'
        return txt,self.evidence(fleet,'SOCIAL')

    def identity(self,fleet,honesty=False):
        if honesty:
            txt=(f"No. I am not an AI model, I am not conscious, and I have no feelings. I am {self.name}'s deterministic "
                 "logical actor: fixed rules turn sampled telemetry into sentences. The personality is only wording.")
        else:txt=self.p['identity']
        return txt,self.evidence(fleet,'IDENTITY')

    def smalltalk(self,fleet,topic):
        if topic=='weather':txt=self.p['weather']
        elif topic=='favorite':txt=self.p['favorite']
        elif topic=='tired':
            b=self.battery_val(fleet)
            txt=("I don't get tired. "+('My SIMULATED battery estimate is '+f'{b:.1f}%.' if b is not None
                                         else 'My simulated battery estimate is UNKNOWN right now.'))
        else:txt=self.p['small']+' Want a status check or a joke?'
        return txt,self.evidence(fleet,'SOCIAL')

    def fallback(self,fleet):
        txt=self.p['fallback']
        if self.known(fleet):txt+=f' Right now: {self.brief(fleet)}.'
        else:txt+=' Right now my state is UNKNOWN ('+(fleet.unknown_reason(self.name) or 'unverified')+').'
        return txt,self.evidence(fleet,'FALLBACK')

    def refuse(self,fleet):
        return self.p['refuse'],self.evidence(fleet,'FLIGHT_REQUEST_REFUSED',dispatched=False,route='HU voice :8100')


class SwarmChat:
    """A bounded, non-actuating group room with fully attributable messages."""
    def __init__(self, root:Path, limit=160):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/'swarmchat_messages.jsonl'
        self.limit=limit;self.lock=threading.RLock()
        self.messages=deque(maxlen=limit)
        self.actors={name:DroneAgent(name) for name in DRONES}
        # Deterministic rotation counters (jokes / who tells the fleet joke). Not learning.
        self.joke_index={name:0 for name in DRONES};self.joke_turn=0
        self.append('SYSTEM','SYSTEM','SwarmChat online. Five deterministic logical actors read sampled Gazebo telemetry; '
                    'no flight commands can be dispatched from this room.', {'kind':'SYSTEM_START'})

    # Provenance of every message (shown in the UI and logs):
    #   TELEMETRY_FACT   deterministic text built from sampled telemetry/events
    #   SAFETY_SUPERVISOR MA'AT / safety boundary statements
    #   CHAT_LOCAL_LLM   wording generated by the local SmolLM2-Instruct model, grounding-validated
    #   RULES_PERSONA    deterministic persona wording (no telemetry claim beyond what it states)
    #   UNKNOWN          insufficient evidence
    source_default={'DRONE_AGENT':'TELEMETRY_FACT','ASSURANCE':'SAFETY_SUPERVISOR','SYSTEM':'SYSTEM','HUMAN':'HUMAN'}

    def append(self,speaker,role,text,evidence=None,conversation_id=None,to=None,intent=None,source=None):
        # 'sender' is the display identity (drone name / MA'AT / SYSTEM / OPERATOR); 'speaker' kept for compatibility.
        entry={'id':secrets.token_hex(6),'utc':now_utc(),'speaker':speaker,
               'sender':SENDER_LABEL.get(speaker,speaker),'role':role,'to':to,'intent':intent,
               'text':str(text),'evidence':evidence or {},'conversation_id':conversation_id,
               'source':source or (evidence or {}).get('source_override') or self.source_default.get(role,'UNKNOWN')}
        with self.lock:
            self.messages.append(entry)
            with self.path.open('a',encoding='utf8') as f:
                f.write(json.dumps(entry,ensure_ascii=False,separators=(',',':'))+'\n')
        return entry

    def history(self):
        with self.lock:return list(self.messages)[-80:]

    # ---- routing (fixed precedence; no learning) ------------------------------
    @staticmethod
    def _names(lower):
        return [n for n in DRONES if _NAME_RE[n].search(lower)]

    @staticmethod
    def _window(lower):
        m=re.search(r'\b(?:last|past)\s+(\d{1,3})\s*(s|sec|secs|seconds?|m|min|mins|minutes?)\b',lower)
        if not m:return 30.0
        v=float(m.group(1))*(60 if m.group(2).startswith('m') else 1)
        return max(5.0,min(120.0,v))

    def _relation(self,lower,named,addressee,self_drone):
        lowmap={n.lower():n for n in DRONES}
        far=bool(RX['farthest'].search(lower));near=bool(RX['nearest'].search(lower))
        if far or near:
            subj=None;to_me=False
            m=re.search(r'(?:closest|nearest|farthest|furthest|most distant|next)\s+(?:[a-z]+\s+){0,2}?(?:to|from)\s+([a-z]+)',lower)
            if m:
                w=m.group(1)
                if w in lowmap:subj=lowmap[w]
                elif w in ('you','yourself','u'):subj=self_drone
                elif w in ('me','us','operator'):to_me=True
            if subj is None and not to_me:
                others=[n for n in named if n!=addressee]
                if others:subj=others[0]
                elif self_drone:subj=self_drone
            return {'rel':'far' if far else 'near','subject':subj,'to_me':to_me}
        if RX['highest'].search(lower):return {'rel':'high'}
        if RX['lowest'].search(lower):return {'rel':'low'}
        if RX['sepwords'].search(lower):
            cands=list(named)
            if self_drone and self_drone not in cands and re.search(r'\b(you|your|yourself)\b',lower):cands.append(self_drone)
            if len(cands)>2 and addressee in cands:cands.remove(addressee)
            if len(cands)>=2:return {'rel':'sep','pair':(cands[0],cands[1])}
        return None

    def _classify(self,lower,recipient):
        named=self._names(lower)
        m=_ADDRESSEE_RE.match(lower)
        addressee={n.lower():n for n in DRONES}.get(m.group(1)) if m else None
        self_drone=recipient if recipient!='FLEET' else (addressee or (named[0] if len(named)==1 else None))
        info={'named':named,'addressee':addressee,'self':self_drone,'window':self._window(lower),
              'greet':bool(RX['greet'].search(lower))}
        facets=[f for f,rx in FACETS if rx.search(lower)]
        info['facets']=facets
        if RX['joke'].search(lower):return 'joke',info
        if RX['honesty'].search(lower):info['honesty']=True;return 'identity',info
        if RX['identity'].search(lower):return 'identity',info
        rel=self._relation(lower,named,addressee,self_drone)
        if rel:info.update(rel);return 'relation',info
        if RX['why'].search(lower):return 'why',info
        if RX['safety'].search(lower):return 'safety',info
        if RX['flight'].search(lower):return 'flight',info
        if RX['howareyou'].search(lower):return 'howareyou',info
        if RX['thanks'].search(lower):return 'thanks',info
        if RX['bye'].search(lower):return 'bye',info
        strong=re.search(r'\b(discuss|debate|roll ?call|status report|sitrep)\b',lower)
        if RX['listq'].search(lower) and not strong:
            if RX['faultwords'].search(lower):return 'list_faults',info
            if RX['airwords'].search(lower):return 'list_air',info
        if RX['discussion'].search(lower):
            if recipient!='FLEET':info['facets']=facets or ['status'];return 'facets',info
            if set(facets)<={'status','health'} and not named:return 'discussion',info
        if facets:return 'facets',info
        if info['greet']:return 'greeting',info
        for topic,rx in SMALLTALK:
            if rx.search(lower):info['topic']=topic;return 'smalltalk',info
        return 'fallback',info

    @staticmethod
    def _targets(recipient,info,default):
        if recipient!='FLEET':return [recipient]
        if info['named']:return list(info['named'])
        return list(default)

    @staticmethod
    def _excluded(fleet,skip=()):
        unk=[n for n in fleet.unknown() if n not in skip]
        return f' ({_join(unk)} excluded: telemetry UNKNOWN.)' if unk and fleet.online and fleet.state else ''

    def _crosstalk(self,name,fleet,speakers):
        """Deterministic peer remarks drawn only from the shared snapshot."""
        extra='';pr=fleet.pair()
        if pr and name in pr[:2]:
            a,b,d=pr
            if name==a:
                extra+=f' {b}, you and I are the closest pair right now at {d:.2f} m'+(
                    f', inside the {ADVISORY_M:.2f} m advisory.' if d<ADVISORY_M else '.')
            elif a in speakers:extra+=f' Confirmed, {a}: the same snapshot shows {d:.2f} m between us.'
            else:extra+=f' {a} and I are the closest pair right now at {d:.2f} m.'
        if name=='Anubis' and fleet.online and fleet.state:
            others=[o for o in fleet.faulted() if o!=name]
            unk=[o for o in fleet.unknown() if o!=name]
            if others:extra+=(f' Guard note: {_join(others)} '+('is' if len(others)==1 else 'are')
                              +" reporting an active simulated fault; I'm keeping that in view.")
            if unk:extra+=f' Guard note: telemetry for {_join(unk)} is UNKNOWN.'
        return extra

    def _summary(self,fleet):
        attention=[n for n in DRONES if n in fleet.faulted() or n in fleet.unknown()]
        return (f'Current fleet snapshot has {len(fleet.entries)} named aircraft. '
                +('Attention required: '+', '.join(attention)+'.' if attention else 'No active command-link faults are reported in this sample.')
                +' Replies are deterministic templates filled from per-aircraft telemetry and logged events, '
                 'not learned or autonomous decisions.')

    # ---- main entry ------------------------------------------------------------
    def ask(self,utterance,recipient,state,events=(),online=False,health=None):
        if not isinstance(utterance,str) or not (1<=len(utterance.strip())<=350):
            raise ValueError('Ask a question of 1 to 350 characters.')
        utterance=utterance.strip().replace('\n',' ')
        if recipient not in ('FLEET',*DRONES):raise ValueError('Choose FLEET or a named aircraft.')
        cid=secrets.token_hex(5)
        self.append('OPERATOR','HUMAN',utterance,{'kind':'HUMAN_TEXT','source':'TYPED'},cid,to=recipient)
        # State is copy-prepared by the caller. Lack of telemetry never yields invented facts.
        fleet=FleetView(state,online)
        lower=utterance.casefold()
        intent,info=self._classify(lower,recipient)
        out=[]
        persona_only=intent in ('joke','greeting','thanks','bye','smalltalk','identity','fallback')
        def say(name,pair,to='OPERATOR'):
            txt,ev=pair
            out.append(self.append(name,'DRONE_AGENT',txt,ev,cid,to=to,intent=intent,
                                   source='RULES_PERSONA' if persona_only else None))
        maat=getattr(self,'_do_'+intent)(fleet,lower,recipient,info,say,events,health)
        if not fleet.online:
            maat=('Gazebo supervisor is offline. No aircraft status or mission outcome can be verified. '
                  'Flight commands are not available through SwarmChat.','SUPERVISOR_OFFLINE')
        if maat:
            out.append(self.append('MAAT','ASSURANCE',maat[0],{'kind':maat[1],'observed_at':fleet.stamp},cid,
                                   to='OPERATOR',intent=intent))
        return out

    # ---- intent handlers: each returns an optional (MA'AT text, evidence kind) --
    def _do_joke(self,fleet,lower,recipient,info,say,events,health):
        if recipient!='FLEET':tellers=[recipient]
        elif info['named']:tellers=info['named'][:2]
        else:tellers=[DRONES[self.joke_turn%len(DRONES)]];self.joke_turn+=1
        for n in tellers:
            jokes=PERSONAS[n]['jokes'];j=jokes[self.joke_index[n]%len(jokes)];self.joke_index[n]+=1
            say(n,(j,self.actors[n].evidence(fleet,'SOCIAL_JOKE')))
        if recipient=='FLEET' and len(tellers)==1:
            t=tellers[0];r='Thoth' if t!='Thoth' else 'Ra'
            txt=(f'Rating that joke: 3 out of 10, {t}. Statistically a groaner.' if r=='Thoth'
                 else 'Ha! Thoth told a joke! Somebody log the timestamp!')
            say(r,(txt,self.actors[r].evidence(fleet,'SOCIAL_REPLY_TO_PEER')),to=t)
        return None

    def _do_identity(self,fleet,lower,recipient,info,say,events,health):
        for n in self._targets(recipient,info,DRONES):
            say(n,self.actors[n].identity(fleet,info.get('honesty',False)))
        return None

    def _do_greeting(self,fleet,lower,recipient,info,say,events,health):
        for n in self._targets(recipient,info,DRONES):say(n,self.actors[n].greeting(fleet,lower))
        return None

    def _do_howareyou(self,fleet,lower,recipient,info,say,events,health):
        targets=self._targets(recipient,info,DRONES)
        for n in targets:
            txt,ev=self.actors[n].how_are_you(fleet,info['greet'])
            if len(targets)>1:txt+=self._crosstalk(n,fleet,targets)
            say(n,(txt,ev))
        return None

    def _single(self,recipient,info):
        return self._targets(recipient,info,['Horus'])

    def _do_thanks(self,fleet,lower,recipient,info,say,events,health):
        for n in self._single(recipient,info):
            txt=_pick(PERSONAS[n]['thanks'],n,lower)
            if recipient=='FLEET' and not info['named']:txt+=' (On behalf of the fleet.)'
            say(n,(txt,self.actors[n].evidence(fleet,'SOCIAL')))
        return None

    def _do_bye(self,fleet,lower,recipient,info,say,events,health):
        for n in self._single(recipient,info):say(n,(_pick(PERSONAS[n]['bye'],n,lower),self.actors[n].evidence(fleet,'SOCIAL')))
        return None

    def _do_smalltalk(self,fleet,lower,recipient,info,say,events,health):
        topic=info.get('topic')
        targets=self._targets(recipient,info,DRONES if topic=='favorite' else ['Horus'])
        for n in targets:say(n,self.actors[n].smalltalk(fleet,topic))
        return None

    def _do_fallback(self,fleet,lower,recipient,info,say,events,health):
        for n in self._single(recipient,info):say(n,self.actors[n].fallback(fleet))
        return None

    def _do_flight(self,fleet,lower,recipient,info,say,events,health):
        for n in self._single(recipient,info):say(n,self.actors[n].refuse(fleet))
        return ('This room can discuss or diagnose the requested mission, but DOES NOT execute it. Route any flight request '
                'through '+HU_VOICE+', HU authorization and the independent safety supervisor. No trajectory has been '
                'approved and nothing was dispatched.','NON_ACTUATING_MISSION_PROPOSAL')

    def _do_why(self,fleet,lower,recipient,info,say,events,health):
        flagged=[n for n in DRONES if n in fleet.faulted() or n in fleet.unknown()]
        targets=self._targets(recipient,info,flagged or ['Horus'])
        for n in targets:say(n,self.actors[n].why(fleet,events))
        return (self._summary(fleet),'EVIDENCE_SUMMARY') if recipient=='FLEET' else None

    def _do_safety(self,fleet,lower,recipient,info,say,events,health):
        flagged=[n for n in DRONES if n in fleet.faulted() or n in fleet.unknown()]
        default=[n for n in DRONES if n=='Anubis' or n in flagged]
        for n in self._targets(recipient,info,default):say(n,self.actors[n].safety(fleet))
        pr=fleet.pair()
        status=(fleet.state or {}).get('safety_status','UNKNOWN') if fleet.state else 'UNKNOWN'
        return (f'Sampled observer status: {status}; minimum observed fleet separation: '
                +(f'{pr[2]:.2f} m ({pr[0]}-{pr[1]}).' if pr else 'UNKNOWN.')
                +' No guarantee of collision-free future trajectories can be inferred from sampled telemetry. '
                 'SwarmChat DOES NOT execute or approve maneuvers; flight requests go through '+HU_VOICE+'.',
                'OBSERVED_SAFETY_ONLY')

    def _do_discussion(self,fleet,lower,recipient,info,say,events,health):
        targets=list(DRONES)
        for n in targets:
            a=self.actors[n]
            txt=(_pick(a.p['opener'],n,lower)+' '+a.status_line(fleet)) if a.known(fleet) else a.unknown_text(fleet)
            say(n,(txt+self._crosstalk(n,fleet,targets),a.evidence(fleet)))
        return (self._summary(fleet),'EVIDENCE_SUMMARY')

    def _do_facets(self,fleet,lower,recipient,info,say,events,health):
        for n in self._targets(recipient,info,DRONES):
            say(n,self.actors[n].facets(fleet,info['facets'],health,info['window']))
        return None

    def _list_answer(self,fleet,recipient,info,say,txt,kind):
        if recipient!='FLEET':
            say(recipient,('From the shared snapshot: '+txt,self.actors[recipient].evidence(fleet,kind)))
            return None
        return (txt,kind)

    def _do_list_faults(self,fleet,lower,recipient,info,say,events,health):
        faulted=fleet.faulted();unk=fleet.unknown() if fleet.state else list(DRONES)
        txt=(f'Active simulated faults: {_join(faulted) if faulted else "none reported"}. '
             f'Stale or unavailable telemetry (state UNKNOWN): {_join(unk) if unk else "none"}.')
        return self._list_answer(fleet,recipient,info,say,txt,'FLEET_FAULT_LIST')

    def _do_list_air(self,fleet,lower,recipient,info,say,events,health):
        fresh=fleet.fresh_names()
        air=[n for n in fresh if fleet.pos(n)[2]>=AIRBORNE_Z_M];ground=[n for n in fresh if n not in air]
        unk=fleet.unknown()
        txt=(f'Airborne (fresh telemetry, altitude at least {AIRBORNE_Z_M:.2f} m): {_join(air)}. '
             f'On or near the ground: {_join(ground)}. UNKNOWN (stale or offline): {_join(unk)}.')
        return self._list_answer(fleet,recipient,info,say,txt,'FLEET_AIRBORNE_LIST')

    def _do_relation(self,fleet,lower,recipient,info,say,events,health):
        rel=info['rel'];me=info['self']
        if rel in ('near','far'):
            w='nearest' if rel=='near' else 'farthest'
            if info.get('to_me'):
                n=me or 'Horus'
                say(n,(f"I don't have your position: the operator is not tracked in Gazebo, so the aircraft {w} to you is UNKNOWN.",
                       self.actors[n].evidence(fleet,'FLEET_GEOMETRY')))
                return None
            subj=info.get('subject')
            if subj is None:
                pr=fleet.pair(rel=='far');label='closest' if rel=='near' else 'farthest-apart'
                if pr is None:
                    n=me or 'Horus'
                    say(n,(f'Fewer than two aircraft have fresh telemetry, so the {label} pair is UNKNOWN.',
                           self.actors[n].evidence(fleet,'FLEET_GEOMETRY')))
                    return None
                a,b,d=pr
                ev=lambda n:self.actors[n].evidence(fleet,'FLEET_GEOMETRY',pair=[a,b],distance_m=round(d,3))
                if me and me not in (a,b):
                    say(me,(f'The {label} pair is {a} and {b}, {d:.2f} m apart (3D).'+self._excluded(fleet),ev(me)));return None
                first=me if me in (a,b) else a;other=b if first==a else a
                say(first,(f'{other} and I are the {label} pair in the fleet right now, {d:.2f} m apart (3D).'
                           +self._excluded(fleet),ev(first)))
                if recipient=='FLEET':say(other,(f'Confirmed, {first}: the same snapshot shows {d:.2f} m between us.',ev(other)),to=first)
                return None
            responder=recipient if recipient!='FLEET' else subj
            ra=self.actors[responder]
            if not fleet.fresh(subj):
                txt=(ra.unknown_text(fleet) if responder==subj else
                     f"{subj}'s position is UNKNOWN ({fleet.unknown_reason(subj)}), so its {w} aircraft is UNKNOWN.")
                say(responder,(txt,ra.evidence(fleet,'FLEET_GEOMETRY',subject=subj)));return None
            nb=fleet.neighbour(subj,rel=='far')
            if nb is None:
                say(responder,(f'No other aircraft has fresh telemetry, so the {w} aircraft to {subj} is UNKNOWN.',
                               ra.evidence(fleet,'FLEET_GEOMETRY',subject=subj)));return None
            peer,d=nb
            note=(f' That is inside the {ADVISORY_M:.2f} m observer advisory threshold.' if rel=='near' and d<ADVISORY_M else '')
            excl=self._excluded(fleet)
            ev=lambda n:self.actors[n].evidence(fleet,'FLEET_GEOMETRY',subject=subj,peer=peer,distance_m=round(d,3))
            if responder==subj:txt=f'My {w} aircraft is {peer}, {d:.2f} m away (3D).'
            elif responder==peer:txt=f"That's me: I am {subj}'s {w} aircraft, {d:.2f} m away (3D)."
            else:txt=f"{subj}'s {w} aircraft is {peer}, {d:.2f} m away (3D)."
            say(responder,(txt+note+excl,ev(responder)))
            if recipient=='FLEET' and responder!=peer:
                own=fleet.neighbour(peer,rel=='far')
                if own and own[0]==subj:ctxt=f"That's me. {subj} and I are each other's {w} aircraft, {d:.2f} m apart."
                elif own:ctxt=f"That's me: {subj} is {d:.2f} m from me, though my own {w} aircraft is {own[0]} at {own[1]:.2f} m."
                else:ctxt=f"That's me: {subj} is {d:.2f} m from me."
                say(peer,(ctxt,ev(peer)),to=subj)
            return None
        if rel in ('high','low'):
            w='highest' if rel=='high' else 'lowest'
            fresh=fleet.fresh_names()
            if not fresh:
                n=me or 'Horus'
                say(n,(f'No aircraft has fresh telemetry, so the {w} aircraft is UNKNOWN.',self.actors[n].evidence(fleet,'FLEET_GEOMETRY')))
                return None
            # Stable sort: on equal altitude the earlier name in DRONES order wins (deterministic).
            order=sorted(fresh,key=lambda n:-fleet.pos(n)[2] if rel=='high' else fleet.pos(n)[2])
            top=order[0];zt=fleet.pos(top)[2]
            ranking='Ranking: '+', '.join(f'{n} {fleet.pos(n)[2]:.2f} m' for n in order)+'.'
            responder=recipient if recipient!='FLEET' else top
            ev=self.actors[responder].evidence(fleet,'FLEET_GEOMETRY',ranking=[[n,round(fleet.pos(n)[2],3)] for n in order])
            if responder==top:txt=f"That's me: I'm the {w} of the {len(fresh)} aircraft with fresh telemetry, at {zt:.2f} m. "
            else:
                mine=fleet.pos(responder)
                txt=f'{top} is {w} at {zt:.2f} m; '+(f"I'm at {mine[2]:.2f} m. " if mine else 'my own altitude is UNKNOWN. ')
            say(responder,(txt+ranking+self._excluded(fleet),ev))
            return None
        a,b=info['pair']
        responder=recipient if recipient!='FLEET' else (info['addressee'] if info['addressee'] in (a,b) else a)
        ra=self.actors[responder]
        d=fleet.dist(a,b)
        if responder==a:who=f'{b} and I'
        elif responder==b:who=f'{a} and I'
        else:who=f'{a} and {b}'
        if d is None:
            reasons='; '.join(fleet.unknown_reason(n) for n in (a,b) if not fleet.fresh(n))
            obj=who[:-len(' and I')]+' and me' if who.endswith(' and I') else who
            say(responder,(f'The separation between {obj} is UNKNOWN: {reasons}.',
                           ra.evidence(fleet,'FLEET_GEOMETRY',pair=[a,b])))
            return None
        pa,pb=fleet.pos(a),fleet.pos(b)
        h=math.hypot(pa[0]-pb[0],pa[1]-pb[1]);v=abs(pa[2]-pb[2])
        note=(f' That is inside the {ADVISORY_M:.2f} m observer advisory threshold.' if d<ADVISORY_M else '')
        say(responder,(f'{who} are {d:.2f} m apart (3D): {h:.2f} m horizontally and {v:.2f} m vertically.'+note,
                       ra.evidence(fleet,'FLEET_GEOMETRY',pair=[a,b],distance_m=round(d,3))))
        return None
