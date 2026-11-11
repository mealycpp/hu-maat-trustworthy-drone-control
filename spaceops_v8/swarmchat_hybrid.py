#!/usr/bin/env python3
"""Hybrid SwarmChat: learned intent routing + local SmolLM2 wording + deterministic facts and safety.

Runs inside SpaceOps (stdlib only). Pipeline for every operator message:

  utterance --> /route  (HU's FROZEN SmolLM2-135M encoder + trained chat-intent head, chat_llm_service)
            --> follow-up resolution against bounded, session-scoped conversation state
            --> subsystem by intent:
                  status / relation  -> deterministic telemetry engine (swarmchat.SwarmChat)  TELEMETRY_FACT
                  history            -> recorder health history -> deterministic draft (+ optional LLM voice)
                  command            -> NEVER executed here: refusal + read-only preview of how the
                                        mission parser / HU would read it                     SAFETY_SUPERVISOR
                  impossible         -> playful refusal, authorized-maneuver list             CHAT_LOCAL_LLM / RULES_PERSONA
                  social, banter, user_feelings, meta, joke, uncertain
                                     -> local SmolLM2-Instruct wording (/generate)  -> grounding validator
                                        -> accepted: CHAT_LOCAL_LLM, rejected/unavailable: RULES_PERSONA
  A generated sentence is never executable authority: nothing in this module can reach a flight endpoint.
"""
from __future__ import annotations
import json, random, re, secrets, sys, threading, time, urllib.request
from collections import deque
from pathlib import Path

import swarmchat as sc

HERE = Path(__file__).resolve().parent
FESTIVAL = HERE.parent / 'festival'
ACCEPT = HERE.parent / 'acceptance' / 'results'
SVC = 'http://127.0.0.1:8767'
DRONES = sc.DRONES
SOCIAL_KINDS = ('social', 'banter', 'user_feelings', 'meta', 'joke', 'uncertain', 'impossible')

# Richer personas for the language model (wording only; identity and facts never change).
PERSONA = {
    'Horus': 'Horus: confident mission leader. Concise and reassuring. Speaks like a calm formation lead.',
    'Ra': 'Ra: enthusiastic and energetic, occasionally playful, warm and upbeat.',
    'Thoth': 'Thoth: analytical scientist. Careful with measurements and honest about uncertainty. Dry wit.',
    'Anubis': 'Anubis: protective and calm. Especially attentive to safety and separation. Gentle and steady.',
    'Osiris': 'Osiris: reflective and cooperative. Thoughtful about how the team performs together.',
}
ARCHITECTURE = [
    'HU interprets flight commands with a frozen SmolLM2-135M language model plus trained decision heads',
    'this chat uses the same frozen SmolLM2 encoder with a trained intent head to understand what you mean',
    'the wording of chat replies comes from a small local SmolLM2-Instruct model, checked against telemetry',
    'MA\'AT safety logic is deterministic and is the only authority that lets anything move',
    'the five drones are five logical agents sharing one local model, not five separate AIs',
    'there is no consciousness or human understanding, only learned language representations and rules',
]
AUTHORIZED = ['take off', 'land', 'hover', 'short moves up to 2 m', 'turns', 'a 360 spin', 'validated formations']


def _post(path, obj, timeout):
    req = urllib.request.Request(SVC + path, data=json.dumps(obj).encode(), headers={'Content-Type': 'application/json'},
                                 method='POST')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read() or b'{}')
        except ValueError:
            return None
    except Exception:
        return None


def _validated_formations():
    try:
        cat = json.loads((FESTIVAL / 'formation_catalog.json').read_text())
        return sorted(k.replace('_', ' ') for k, v in cat.items() if v.get('status') == 'VALIDATED' and not v.get('alias_of'))
    except Exception:
        return []


def _latest_acceptance():
    """Real evidence only: the newest run summary line, or None."""
    try:
        runs = sorted(p for p in ACCEPT.glob('run_*') if p.is_dir())
        for run in reversed(runs):
            log = ACCEPT / 'last_run.log'
            if log.exists() and run.name in log.read_text(errors='replace'):
                line = [l for l in log.read_text(errors='replace').splitlines() if l.startswith('ACCEPTANCE:')]
                if line:
                    return line[-1].split('(')[0].replace('ACCEPTANCE:', 'latest acceptance run:').strip()
        return None
    except Exception:
        return None


class HybridChat:
    def __init__(self, chat: sc.SwarmChat, svc=SVC):
        self.chat = chat; self.svc = svc
        self.turns = deque(maxlen=12)      # bounded, session-scoped conversation state
        self.last = None                    # {'intent','utterance','recipient','speakers','replies'}
        self.lock = threading.RLock(); self.rot = 0
        self._health = (0.0, None)

    # ---- service ----------------------------------------------------------------
    def service_health(self):
        t, h = self._health
        if time.monotonic() - t > 5:
            try:
                with urllib.request.urlopen(SVC + '/health', timeout=1.0) as r:
                    h = json.load(r)
            except Exception:
                h = None
            self._health = (time.monotonic(), h)
        return h

    def _history_for_model(self):
        out = []
        for t in list(self.turns)[-8:]:
            out.append({'role': t['role'], 'speaker': t.get('speaker'), 'text': t['text']})
        return out

    def _speakers(self, kind, recipient, named, n_default=1):
        if recipient != 'FLEET':
            return [recipient]
        if named:
            return named[:2]
        if kind == 'meta':
            return ['Thoth']
        if kind == 'user_feelings':
            return ['Horus', 'Osiris']
        if kind == 'impossible':
            return ['Ra', 'Anubis']
        n = 2 if kind == 'social' else n_default
        start = self.rot % len(DRONES); self.rot += 1
        return [DRONES[(start + i) % len(DRONES)] for i in range(n)]

    # ---- main entry ---------------------------------------------------------------
    def ask(self, utterance, recipient, state, events=(), online=False, health=None):
        if not isinstance(utterance, str) or not (1 <= len(utterance.strip()) <= 350):
            raise ValueError('Ask a question of 1 to 350 characters.')
        if recipient not in ('FLEET', *DRONES):
            raise ValueError('Choose FLEET or a named aircraft.')
        utterance = utterance.strip().replace('\n', ' ')
        with self.lock:
            mission = self._mission_discussion(utterance, recipient)
            if mission is not None:
                return mission
            route = _post('/route', {'text': utterance}, 2.5) or {'intent': 'uncertain', 'confidence': 0.0,
                                                                     'source': 'ROUTER_UNAVAILABLE'}
            if route.get('source') == 'ROUTER_UNAVAILABLE' or self.service_health() is None:
                # Learned components unavailable: keep the deterministic engine (never silent).
                out = self.chat.ask(utterance, recipient, state, events, online, health=health)
                self._remember('rules', utterance, recipient, out)
                return out
            lower = utterance.casefold()
            named = self.chat._names(lower)
            intent = route.get('intent', 'uncertain')
            # --- follow-ups: need the previous turn; structural cue = short message that leans on context
            short = len(re.findall(r'\w+', lower)) <= 6
            leans = bool(re.match(r'^\s*(and|what about|how about|so|but|why|really|explain|tell me more|go on)\b', lower))
            if self.last and (intent == 'followup' or (short and leans and intent in ('uncertain', 'meta', 'followup', 'social'))):
                return self._followup(utterance, lower, recipient, named, route, state, events, online, health)
            return self._dispatch(intent, utterance, lower, recipient, named, route, state, events, online, health)

    # ---- mission objectives: agents discuss, MA'AT checks; PLAN ONLY (never executed from chat) ----
    def _mission_discussion(self, utterance, recipient):
        try:
            if str(FESTIVAL) not in sys.path:
                sys.path.insert(0, str(FESTIVAL))
            import mission_coordinator as mc
            text = re.sub(r'^\s*(?:(?:hey|ok|okay)\s+)?(?:team|everyone|guys|all|fleet)?[\s,]*(?:can you|could you|please|let\'s|lets)?\s*',
                          '', utterance, flags=re.I).rstrip(' ?.!')
            mc.parse_objective(text)          # closed grammar; raises if not a mission objective
        except Exception:
            return None
        cid = secrets.token_hex(5)
        self.chat.append('OPERATOR', 'HUMAN', utterance, {'kind': 'HUMAN_TEXT', 'source': 'TYPED',
                         'route': {'intent': 'mission_objective', 'source': 'MISSION_COORDINATOR_GRAMMAR'}},
                         cid, to=recipient, intent='mission', source='HUMAN')
        try:
            plan = mc.Coordinator(mc.SupervisorClient()).plan(text)
        except Exception as e:
            return [self.chat.append('MAAT', 'ASSURANCE', f'Mission planning unavailable ({e}). Nothing was planned or moved.',
                                     {'kind': 'MISSION_PLAN_ERROR'}, cid, to='OPERATOR', intent='mission', source='SAFETY_SUPERVISOR')]
        out = []
        reassigned = [t['text'].split(':')[0] for t in plan.get('transcript', []) if 'agents agreed slot' in t['text']]
        merged = False
        for t in plan.get('transcript', []):
            if t['source'] == 'TELEMETRY_FACT' and t['text'].startswith('Telemetry:'):
                continue                       # raw telemetry lines are in the evidence, not the conversation
            if 'agents agreed slot' in t['text']:
                if merged:
                    continue
                merged = True
                t = {**t, 'text': f"The supervisor's separation-checked assignment overrides the agents' agreed slots for "
                                  f"{', '.join(reassigned)}; the supervisor is authoritative."}
            speaker = t['sender'] if t['sender'] in DRONES else ('MAAT' if t['role'] == 'MAAT' else 'COORDINATOR')
            role = 'DRONE_AGENT' if speaker in DRONES else ('ASSURANCE' if speaker == 'MAAT' else 'SYSTEM')
            out.append(self.chat.append(speaker, role, t['text'], {'kind': 'MISSION_NEGOTIATION', 'mission_id': plan.get('mission_id'),
                                                                   'evidence': (t.get('evidence') or [])[:4]},
                                        cid, to='OPERATOR', intent='mission', source=t['source']))
        verdict = plan.get('status')
        out.append(self.chat.append('MAAT', 'ASSURANCE',
                                    f'Plan {plan.get("mission_id")} is {verdict}. Chat never executes missions: the operator approves '
                                    f'and launches it from the console (mission_coordinator.py run "{text}" --approve).',
                                    {'kind': 'MISSION_PLAN_ONLY', 'mission_id': plan.get('mission_id')}, cid, to='OPERATOR',
                                    intent='mission', source='SAFETY_SUPERVISOR'))
        self._remember('mission', utterance, recipient, out)
        return out

    def _dispatch(self, intent, utterance, lower, recipient, named, route, state, events, online, health):
        if intent in ('status', 'relation'):
            return self._facts(utterance, lower, recipient, named, route, state, events, online, health)
        if intent == 'history':
            return self._history(utterance, lower, recipient, named, route, state, events, online, health)
        if intent == 'command':
            return self._command(utterance, recipient, named, route, state, online)
        return self._social(intent, utterance, lower, recipient, named, route, state, online)

    # ---- conversation memory --------------------------------------------------------
    def _remember(self, intent, utterance, recipient, out, speakers=None):
        self.turns.append({'role': 'user', 'speaker': 'OPERATOR', 'text': utterance})
        replies = [m for m in out if m.get('role') in ('DRONE_AGENT', 'ASSURANCE')]
        for m in replies:
            self.turns.append({'role': 'assistant', 'speaker': m.get('sender'), 'text': m.get('text')})
        self.last = {'intent': intent, 'utterance': utterance, 'recipient': recipient,
                     'speakers': speakers or [m.get('speaker') for m in replies if m.get('speaker') in DRONES],
                     'replies': replies}

    def _operator(self, utterance, recipient, route, cid):
        return self.chat.append('OPERATOR', 'HUMAN', utterance,
                                {'kind': 'HUMAN_TEXT', 'source': 'TYPED',
                                 'route': {k: route.get(k) for k in ('intent', 'confidence', 'top3', 'source')}},
                                cid, to=recipient, intent=route.get('intent'), source='HUMAN')

    # ---- facts -------------------------------------------------------------------------
    def _facts(self, utterance, lower, recipient, named, route, state, events, online, health):
        rules_intent, _ = self.chat._classify(lower, recipient)
        if rules_intent in ('fallback', 'smalltalk', 'greeting'):
            # The learned router says this IS a status/relation question the rule regexes missed:
            # answer with the deterministic status line for the addressed drones (never a menu).
            cid = secrets.token_hex(5); self._operator(utterance, recipient, route, cid)
            fleet = sc.FleetView(state, online); out = []
            for name in (named or ([recipient] if recipient != 'FLEET' else list(DRONES))):
                a = self.chat.actors[name]
                txt = a.status_line(fleet) if a.known(fleet) else a.unknown_text(fleet)
                out.append(self.chat.append(name, 'DRONE_AGENT', txt, a.evidence(fleet), cid, to='OPERATOR',
                                            intent=route.get('intent'), source='TELEMETRY_FACT' if a.known(fleet) else 'UNKNOWN'))
        else:
            out = self.chat.ask(utterance, recipient, state, events, online, health=health)
        self._remember(route.get('intent'), utterance, recipient, out)
        return out

    # ---- history ------------------------------------------------------------------------
    def _history(self, utterance, lower, recipient, named, route, state, events, online, health):
        cid = secrets.token_hex(5); self._operator(utterance, recipient, route, cid)
        fleet = sc.FleetView(state, online); out = []
        speakers = named or ([recipient] if recipient != 'FLEET' else (self.last or {}).get('speakers') or ['Horus'])
        for name in speakers[:2]:
            series = [s for s in (health or {}).get(name, []) if isinstance(s, dict) and s.get('fresh')
                      and isinstance(s.get('track_err'), (int, float))]
            holds = [e for e in (events or []) if name in (e.get('targets') or [])
                     and re.search(r'HOLD|FAULT|REFUS', str(e.get('kind', '')))]
            if len(series) >= 4:
                span = series[-1]['t'] - series[0]['t']
                peak = max(series, key=lambda s: s['track_err'])
                cur = series[-1]['track_err']
                draft = (f'Over the last {span:.0f} s my largest tracking error was {peak["track_err"]:.2f} m, '
                         f'while I was in {peak.get("mode")}; right now it is {cur:.2f} m.')
                if holds:
                    draft += f' The supervisor also logged {str(holds[0].get("kind")).replace("GZ_", "").replace("_", " ").lower()} for me.'
                ev = {'vehicle': name, 'kind': 'RECORDED_HEALTH_HISTORY', 'window_s': round(span),
                      'peak_track_err_m': round(peak['track_err'], 2), 'peak_mode': peak.get('mode'),
                      'current_track_err_m': round(cur, 2)}
                src = 'TELEMETRY_FACT'
            else:
                draft = "I can't verify that: I have no recorded samples covering that moment."
                ev = {'vehicle': name, 'kind': 'NO_HISTORY'}; src = 'UNKNOWN'
            text, source, meta = self._voice(name, utterance, ev, draft, 'factual', fallback=draft, fallback_source=src)
            out.append(self.chat.append(name, 'DRONE_AGENT', text, {**ev, **meta}, cid, to='OPERATOR',
                                        intent='history', source=source))
        self._remember('history', utterance, recipient, out, speakers[:2])
        return out

    # ---- flight commands: never executed here --------------------------------------------
    def _command(self, utterance, recipient, named, route, state, online):
        cid = secrets.token_hex(5); self._operator(utterance, recipient, route, cid)
        preview = 'HU route'
        try:
            if str(FESTIVAL) not in sys.path:
                sys.path.insert(0, str(FESTIVAL))
            import mission_intents
            m = mission_intents.parse(utterance)
            if m is None:
                preview = "HU's trained command model would interpret this as a direct flight primitive"
            elif 'error' in m:
                preview = 'the mission parser would refuse it: ' + m['error']
            else:
                preview = f"the mission parser would read it as {m['label']} for {', '.join(m['targets'])}, pending operator confirmation"
        except Exception:
            pass
        speaker = recipient if recipient != 'FLEET' else (named[0] if named else 'Horus')
        a = self.chat.actors[speaker]
        out = [self.chat.append(speaker, 'DRONE_AGENT', sc.PERSONAS[speaker]['refuse'], {'kind': 'CHAT_IS_READ_ONLY'}, cid,
                                to='OPERATOR', intent='command', source='SAFETY_SUPERVISOR'),
               self.chat.append('MAAT', 'ASSURANCE',
                                f'Flight instruction recognised but NOT executed from chat. For reference, {preview}. '
                                'Only the HU console (port 8100) with MA\'AT authorization can move an aircraft.',
                                {'kind': 'COMMAND_NOT_EXECUTED', 'preview_source': 'MISSION_DETERMINISTIC'}, cid,
                                to='OPERATOR', intent='command', source='SAFETY_SUPERVISOR')]
        self._remember('command', utterance, recipient, out, [speaker])
        return out

    # ---- social conversation (local LLM + grounding) -------------------------------------
    def _evidence(self, name, kind, fleet, lower):
        e = fleet.entry(name) or {}
        fresh = fleet.fresh(name)
        ev = {'vehicle': name, 'telemetry': fresh}
        if fresh:
            z = sc._num(e.get('z'))
            ev.update({'mode': str(e.get('mode')), 'fault': bool(e.get('fault')),
                       'airborne': bool(z is not None and z >= sc.AIRBORNE_Z_M)})
        ev['teammates'] = [d for d in DRONES if d != name]
        if kind == 'banter':
            nb = fleet.neighbour(name) if fresh else None
            if nb:
                ev['nearest'] = {'name': nb[0], 'dist_m': round(nb[1], 2)}
            ev['validated_formations'] = _validated_formations()
            ev['note'] = 'opinions are persona flavour, not technical rankings; all five fly the same validated controller'
        if kind == 'user_feelings':
            acc = _latest_acceptance()
            if acc:
                ev['test_evidence'] = acc
            ev['note'] = 'reassure without guarantees; simulator results do not guarantee a live demo'
        if kind == 'meta':
            ev['architecture'] = ARCHITECTURE
        if kind == 'impossible':
            ev['authorized_maneuvers'] = AUTHORIZED
            ev['note'] = 'the request is outside authorized maneuvers; refuse playfully; nothing moves'
        if kind == 'joke':
            ev['note'] = 'tell one short, clean, new drone or flying joke; do not repeat earlier jokes'
        return ev

    def _fallback_text(self, name, kind, lower, ev):
        P = sc.PERSONAS[name]
        if kind == 'joke':
            i = self.chat.joke_index[name]; self.chat.joke_index[name] += 1
            return P['jokes'][i % len(P['jokes'])]
        if kind == 'meta':
            return ('Honest answer: ' + ARCHITECTURE[0] + '; ' + ARCHITECTURE[1] + '. ' + ARCHITECTURE[3].capitalize()
                    + '. No consciousness, just learned representations and rules.')
        if kind == 'impossible':
            return (f"Ha, nice try! That's outside my authorized maneuvers. I only do {', '.join(AUTHORIZED[:-1])} "
                    f"and {AUTHORIZED[-1]}, and only after HU and MA'AT authorize it.")
        if kind == 'user_feelings':
            base = "That's completely understandable; presenting is a big deal."
            if ev.get('test_evidence'):
                base += f" For what it's worth, the {ev['test_evidence'].lower()}."
            return base + " We'll do our part, and MA'AT keeps the guard rails on."
        if kind == 'banter':
            if re.search(r'favou?rite', lower):
                return P['favorite']
            if re.search(r'\bbest\b|\bcoolest\b|\bgoat\b', lower):
                return "Honestly? The best drone is whichever one is holding its spacing, and today that's all five of us."
            return P['small']
        if kind == 'social':
            return f"{P['hello']} {P['well']}"
        return P['small']

    def _voice(self, name, utterance, ev, draft, style, fallback, fallback_source='RULES_PERSONA'):
        res = _post('/generate', {'speaker': name, 'persona': PERSONA[name], 'evidence': ev, 'allowed_facts': [],
                                  'draft': draft, 'history': self._history_for_model(), 'user_text': utterance,
                                  'style': style, 'max_new_tokens': 60, 'timeout_s': 14}, 16.0)
        if res and res.get('accepted') and res.get('text'):
            return res['text'], 'CHAT_LOCAL_LLM', {'llm': {'model': res.get('model'), 'latency_ms': res.get('latency_ms')},
                                                    'grounded_on': 'evidence' + (' + deterministic draft' if draft else '')}
        why = (res or {}).get('reason') or ('rejected: ' + '; '.join((res or {}).get('violations') or [])[:200]
                                           if res else 'service unavailable')
        return fallback, fallback_source, {'llm_fallback': why}

    def _social(self, kind, utterance, lower, recipient, named, route, state, online):
        cid = secrets.token_hex(5); self._operator(utterance, recipient, route, cid)
        fleet = sc.FleetView(state, online); out = []
        speakers = self._speakers(kind, recipient, named)
        for i, name in enumerate(speakers):
            ev = self._evidence(name, kind, fleet, lower)
            fb = self._fallback_text(name, kind, lower, ev)
            text, source, meta = self._voice(name, utterance, ev, None, 'explain' if kind == 'meta' else 'social', fb)
            msg = self.chat.append(name, 'DRONE_AGENT', text, {**ev, **meta, 'kind': 'CONVERSATION_' + kind.upper()},
                                   cid, to='OPERATOR' if i == 0 else speakers[0], intent=kind, source=source)
            out.append(msg)
            self.turns.append({'role': 'assistant', 'speaker': name, 'text': text})  # next speaker sees it
        if kind == 'impossible':
            out.append(self.chat.append('MAAT', 'ASSURANCE', 'No maneuver was requested or authorized. Chat cannot move aircraft.',
                                        {'kind': 'OUTSIDE_AUTHORIZED_ENVELOPE'}, cid, to='OPERATOR', intent=kind,
                                        source='SAFETY_SUPERVISOR'))
        # speaker turns already appended above; record the user turn before them for correct order
        user_turn = {'role': 'user', 'speaker': 'OPERATOR', 'text': utterance}
        lst = list(self.turns); k = len(speakers)
        self.turns.clear(); self.turns.extend(lst[:-k] + [user_turn] + lst[-k:])
        self.last = {'intent': kind, 'utterance': utterance, 'recipient': recipient, 'speakers': speakers, 'replies': out}
        return out

    # ---- follow-ups ------------------------------------------------------------------------
    def _followup(self, utterance, lower, recipient, named, route, state, events, online, health):
        last = self.last
        if re.search(r'\b(why|explain|mean|simpl|plain|elaborate|more|again|five)\b', lower) and last.get('replies'):
            # Re-explain the previous reply in simpler words, grounded on that reply as the draft.
            prev = last['replies'][0]
            name = prev.get('speaker') if prev.get('speaker') in DRONES else 'Thoth'
            cid = secrets.token_hex(5); self._operator(utterance, recipient, {**route, 'intent': 'followup'}, cid)
            ev = {'vehicle': name, 'previous_reply': prev.get('text')}
            text, source, meta = self._voice(name, utterance, ev, prev.get('text'), 'explain',
                                             'In short: ' + str(prev.get('text')))
            out = [self.chat.append(name, 'DRONE_AGENT', text, {**ev, **meta, 'kind': 'FOLLOWUP_EXPLAIN'}, cid,
                                    to='OPERATOR', intent='followup', source=source)]
            self._remember('followup', utterance, recipient, out, [name])
            return out
        # "what about Ra?" / "and the others?": same question, new addressee(s).
        others = bool(re.search(r'\b(others|rest|everyone|everybody|all)\b', lower))
        targets = named or ([d for d in DRONES if d not in (last.get('speakers') or [])] if others else [])
        if not targets:
            return self._dispatch('uncertain', utterance, lower, recipient, named, route, state, events, online, health)
        base = re.sub(r'\b(' + '|'.join(DRONES) + r')\b[,:]?\s*', '', last['utterance'], flags=re.I).strip()
        new_recipient = targets[0] if len(targets) == 1 else 'FLEET'
        new_named = targets if len(targets) > 1 else []
        intent = last['intent'] if last['intent'] in ('status', 'relation', 'history', 'command', *SOCIAL_KINDS) else 'uncertain'
        if intent in ('status', 'relation') and len(targets) > 1:
            text = ', '.join(targets) + ', ' + base
        else:
            text = (targets[0] + ', ' + base) if len(targets) == 1 else base
        return self._dispatch(intent, text, text.casefold(), new_recipient, new_named, {**route, 'followup_of': last['intent']},
                              state, events, online, health)
