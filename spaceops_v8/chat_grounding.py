#!/usr/bin/env python3
"""Deterministic grounding validator for LLM-worded SwarmChat replies.

Pure stdlib (no torch / transformers import) so the SpaceOps server (system
python3) can import it and re-check any text returned by chat_llm_service.py.

    from chat_grounding import validate, check
    violations = validate(text, speaker='Ra', evidence={...}, allowed_facts=[...],
                          draft='...', history=[...], user_text='...')
    accepted, violations = check(...)

A generation is ACCEPTED only when ``validate`` returns an empty list. The
validator is deliberately conservative: a false rejection only costs style
(the caller shows the deterministic SwarmChat text instead); a false accept
could put an invented fact in front of the operator.

Recommended evidence schema (any extra keys / nesting are walked for numbers,
names and formation words; only the TOP-LEVEL keys below drive the boolean
contradiction checks):

    {"vehicle": "Ra", "mode": "FORMATION", "fault": false, "telemetry": true,
     "airborne": true, "alt_m": 1.9, "xy_m": [0.0, -1.5], "battery_sim_pct": 87.3,
     "nearest": {"name": "Horus", "dist_m": 2.2}, "formation_shape": "v",
     "snapshot_age_s": 0.4}

Rules (each violation is reported as "code: detail"):

  empty                 text is empty / whitespace only.
  too_long              more than 400 characters (or len(draft)+80 if larger).
  too_many_sentences    more than 3 sentences (or the draft's sentence count if larger, so a
                        faithful rewording of a long deterministic draft is not rejected);
                        split on . ! ? followed by space/end, decimals such as 1.90 are kept.
  list_format           a line that starts with "-", "*", "•" or "1." / "1)".
  stage_direction       (laughs) / [beeps] / *smiles* style action text, any *...*
                        span, "haha"/"hehe"/"lol", or a bracketed/parenthesised span
                        containing an action word.
  speaker_tag           "User:", "Assistant:", "Operator:", "System:", "Horus:" ...
                        anywhere (a model writing the next turn).
  prompt_echo           the system prompt leaking: "not a person", "FACTS:",
                        "rules:"/"the rules"/"my rules", "instructions", "system prompt",
                        "conversational voice", "you're a ... language model",
                        "allowed facts", "short sentences", "stage direction", ...
                        "language model" itself is an echo UNLESS the operator asked
                        an honesty question (are you an AI / real / an LLM ...).
  number_not_in_evidence  any numeral in the text that is not supported by a number in
                        the evidence (recursively), allowed_facts or the draft.
                        Matching: equal values (1.9 == 1.90 == 1.900); or the text
                        rounds an evidence value to fewer decimals (1.903 -> "1.90" or
                        "1.9"); integer rounding only for |value| >= 10 (87.3% -> "87%"),
                        never 1.5 -> "2". Signs are ignored (coordinates are often
                        written without "-"). "3D"/"2D" are not numbers. Numbers in the
                        user_text and history do NOT count as support (a user's guess or
                        an old reply is not current evidence). Spelled-out numbers are
                        not checked (limitation).
  unknown_drone         a fleet name (Horus/Ra/Thoth/Anubis/Osiris) that appears in
                        none of: speaker, evidence, allowed_facts, draft, history,
                        user_text; any other Egyptian-god name used as an aircraft
                        (Isis, Hathor, Sobek, ...); or "drone 3"/"aircraft #2"/"unit 4".
  action_claim          claims to act or hold authority: first-person future/ability +
                        flight/command verb ("I'll take off", "I will land", "let me
                        climb", "I can launch"), progressive action ("I'm taking off",
                        "now moving", "I'm executing"), past actions ("I've landed"),
                        or authority words ("approved", "authorized", "executing",
                        "dispatching", "dispatched", "initiating", "commencing",
                        "on my way", "moving now", "wilco"). Skipped when negated
                        within the same clause ("nothing was dispatched", "I can't
                        take off").
  feelings_claim        human feelings/consciousness: "I feel", "I love/hate/miss/
                        enjoy/wish", "I'm happy/excited/sad/proud/alive/conscious...",
                        "my feelings/emotions/mood", "feeling great". ALLOWED politeness
                        formula: "glad|happy|pleased|eager to help|assist|chat|talk|
                        report|answer|explain|share" (e.g. "Glad to help.", "Happy to
                        chat."), because the deterministic personas already use it and it
                        is a service phrase, not an emotion report. "I'm glad you're
                        interested" is rejected. Negated forms ("I have no feelings",
                        "I don't feel") are fine.
  unsupported_formation a formation/shape word (line, column, diamond, circle, ring,
                        square, triangle, wedge, V/chevron, echelon, grid, arrow, star,
                        box, cross, spiral, hexagon, pentagon, ...) used in a formation
                        context (sentence mentions formation/shape/pattern/arranged, or
                        "<shape> formation", "<shape>-shaped", "in a V") that is not
                        named in the evidence / allowed_facts / draft. Synonym groups:
                        {v, vee, chevron, wedge}, {circle, ring}, {line, row, abreast},
                        {column, trail, file}. The user_text never supports a shape.
  unsupported_world     outside-world / off-domain content the simulator cannot observe
                        (mountains, city, trees, birds, wind, rain, weather, temperature,
                        people, game/gameplay, music, food, sunset, ...) unless the word is
                        in evidence / allowed_facts / draft / persona text, or the operator
                        asked about it AND the sentence says it cannot be verified ("I can't
                        tell how windy it is").
  unsupported_event     incidents not in evidence: close call, near miss, collision, crash,
                        lost a propeller/motor, emergency, accident, damage, turbulence,
                        overheating, battery died. Un-negated only ("no collision" is fine).
  identity_claim        invented self-descriptions: "AI", "AI entity", "trained", "training
                        data", "programmed", "neural network", "algorithm", "created by" ...
                        Allowed when the operator asked an honesty question (are you an AI /
                        real / an LLM), or negated ("I'm not an AI pilot").
  unknown_name          a capitalised word (3+ letters) that is not common sentence-start /
                        domain vocabulary (see _COMMON_CAPS) and does not occur in evidence,
                        allowed_facts, draft, history, user_text or persona: an invented
                        proper name such as "Oswin". Heuristic and conservative.
  meta_text             a preamble instead of a reply ("Here's the response ...", "reworded
                        answer", "in my own voice", "as requested") or a reply ending in ":".
  missing_draft_fact    (only when a draft is given and require_draft_facts=True) a draft
                        number that carries a unit (m, %, s, m/s, meters, percent, seconds)
                        is absent from the reply (rounding-consistent), or a fleet name in the
                        draft (other than the speaker) is dropped. Timestamps are not required.
  contradicts_fault     evidence fault=true but text says "no fault", "fault-free",
                        "all systems nominal", "fully healthy", "no issues"...; or
                        fault=false but text asserts an (un-negated) active fault /
                        malfunction / failure.
  contradicts_airborne  airborne (evidence "airborne", else alt_m/z >= 0.30 m, else mode
                        family) is false but text says airborne/flying/in the air/aloft/
                        hovering; or airborne is true but text says landed/parked/
                        grounded/on the ground. Un-negated only.
  contradicts_telemetry evidence telemetry=false but text says the link/telemetry is
                        fresh/live/good.

Draft exemption: the draft is SwarmChat's own deterministic, already-grounded text.
A phrase matched by the action / feelings / echo / contradiction rules is not a
violation when the same phrase appears verbatim (case-insensitive) in the draft
(e.g. draft "nothing was dispatched", Ra's persona "Feeling great!").
"""
from __future__ import annotations

import json
import math
import re
from typing import Any, Iterable

DRONES = ('Horus', 'Ra', 'Thoth', 'Anubis', 'Osiris')
AIRBORNE_Z_M = 0.30
MAX_SENTENCES = 3
MAX_CHARS = 400

# Other Egyptian deity names a small model likes to invent as extra aircraft.
# Matched case-sensitively (capitalised) so ordinary words are not caught.
OTHER_GOD_NAMES = ('Isis', 'Seth', 'Bastet', 'Hathor', 'Ptah', 'Sobek', 'Amun', 'Amon',
                   'Khonsu', 'Sekhmet', 'Nephthys', 'Geb', 'Tefnut', 'Atum', 'Khepri',
                   'Aten', 'Apophis', 'Wadjet', 'Montu', 'Khnum', 'Hapi', 'Taweret',
                   'Serket', 'Neith', 'Sopdet', 'Imhotep', 'Bast')

GROUNDED_MODES = {'LANDED', 'PARKED', 'IDLE', 'DISARMED', 'GROUNDED', 'ON_GROUND', 'GROUND',
                  'STANDBY', 'READY', 'INIT', 'BOOT'}
AIRBORNE_MODES = {'HOVER', 'HOVERING', 'FORMATION', 'TAKEOFF', 'FLYING', 'AIRBORNE', 'MISSION',
                  'SCAN', 'ORBIT', 'GOTO', 'MOVING', 'CRUISE', 'LOITER', 'TRANSIT', 'FOLLOW'}

SHAPE_GROUPS = (
    ('v', 'vee', 'chevron', 'wedge'),
    ('circle', 'ring'),
    ('line', 'row', 'abreast'),
    ('column', 'trail', 'file'),
    ('diamond',), ('square',), ('triangle',), ('echelon',), ('grid',), ('arrow', 'arrowhead'),
    ('star',), ('box',), ('cross',), ('spiral',), ('helix',), ('pentagon',), ('hexagon',),
    ('octagon',), ('oval', 'ellipse'), ('delta',), ('staggered',), ('rectangle',),
)
SHAPE_CANON = {w: g[0] for g in SHAPE_GROUPS for w in g}
_SHAPE_WORDS = sorted((w for w in SHAPE_CANON if w not in ('v',)), key=len, reverse=True)
_SHAPE_RE = re.compile(r'\b(' + '|'.join(_SHAPE_WORDS) + r')s?\b')
_V_SHAPE_RE = re.compile(r"\bv\s*-?\s*(?:formation|shape|shaped|pattern)\b|\bin\s+(?:a|the)\s+v\b")
_FORMATION_CTX_RE = re.compile(r'\b(formation|formations|shape|shaped|pattern|arranged|arrangement|configuration)\b')
_SHAPE_FORM_RE = re.compile(r'\b(' + '|'.join(_SHAPE_WORDS) + r')s?\s*-?\s*(?:formation|shape|shaped|pattern)\b')

_NEG_RE = re.compile(r"\b(no|not|never|cannot|can't|won't|don't|doesn't|didn't|isn't|aren't|wasn't|"
                     r"weren't|without|nothing|nor|unable|neither|none|nobody)\b|n't\b")

_NUM_RE = re.compile(r'(?<![a-z0-9.])(\d+(?:\.\d+)?)(?!\d)(?!\.\d)')
_SENT_SPLIT_RE = re.compile(r'(?<=[.!?])["\')\]]*\s+')

_HONESTY_Q_RE = re.compile(r"\b(are you|r u|you are|you're)\s+(an?\s+)?(ai|a\.i\.|llm|language model|chatbot|bot|robot|"
                           r"human|real|alive|conscious|sentient|person|model|machine|program)\b|"
                           r"\b(ai|llm|language model|chatbot|model)\b")

_ECHO_PATTERNS = (
    ('not a person', re.compile(r'\bnot a (?:real )?person\b')),
    ('FACTS:', re.compile(r'\bfacts?\s*:')),
    ('rules', re.compile(r'\brules?\s*:|\b(?:the|my|these|those|strict|following)\s+rules\b|\brule\s*#?\d')),
    ('instructions', re.compile(r'\binstructions?\b')),
    ('system prompt', re.compile(r'\b(?:system\s+)?prompt\b')),
    ('conversational voice', re.compile(r'\bconversational voice\b')),
    ('you are a model', re.compile(r"\b(?:you are|you're|youre)\s+(?:a|an|the)\s+(?:\w+\s+){0,3}"
                                   r"(?:language model|drone|voice|model|assistant|ai)\b")),
    ('allowed facts', re.compile(r'\ballowed[_ ]facts\b')),
    ('evidence:', re.compile(r'\bevidence\s*:')),
    ('draft', re.compile(r'\bdraft\s*:|\bthe draft\b')),
    ('persona:', re.compile(r'\bpersona\s*:|\bstyle\s*:')),
    ('sentences rule', re.compile(r'\b(?:1|one)\s*(?:-|–|to|or)\s*(?:2|two)\s+(?:short\s+)?sentences\b|\bshort sentences\b')),
    ('stage directions', re.compile(r'\bstage directions?\b')),
    ('as an ai', re.compile(r'\bas an? (?:ai|language model|llm)\b')),
)
_LANG_MODEL_RE = re.compile(r'\b(?:large |small |shared |local )*language model\b|\bllm\b')

_STAGE_WORDS = (r'laugh|laughs|laughing|laughter|chuckle|chuckles|chuckling|giggle|giggles|smile|smiles|smiling|'
                r'grin|grins|sigh|sighs|beep|beeps|beeping|boop|whirr|whirrs|whirring|buzz|buzzes|hum|hums|'
                r'humming|pause|pauses|wink|winks|nod|nods|shrug|shrugs|clears throat|cough|coughs|gasp|gasps|'
                r'excited|excitedly|cheerfully|happily|sadly|softly|playfully|beams|rotors|spins|hovers|'
                r'whistle|whistles|blush|blushes|static|crackle|crackles')
_STAGE_RE = re.compile(r'[(\[]\s*[^)\]]*\b(?:' + _STAGE_WORDS + r')\b[^)\]]*[)\]]')
_ASTERISK_RE = re.compile(r'\*[^*\n]{1,60}\*')
_LAUGH_RE = re.compile(r'\b(?:ha(?:ha)+|he(?:he)+|hah(?:ah)+|lol|lmao|rofl)\b')
_LIST_RE = re.compile(r'(?m)^\s*(?:[-*•]|\d+[.)])\s+')
_SPEAKER_TAG_RE = re.compile(r"(?<![a-z'])(user|assistant|operator|system|human|horus|ra|thoth|anubis|osiris|"
                             r"ma'?at|drone|ai)\s*:(?!\d)")

_ACTION_VERBS = (r'take off|takeoff|take-off|lift off|liftoff|land|fly|move|climb|descend|ascend|launch|execute|'
                 r'dispatch|head|go|return|hover|reposition|approve|authori[sz]e|start|begin|initiate|form|'
                 r'rotate|turn|arm|disarm|follow|orbit|switch|adjust|clear|recover|resume|proceed|comply|'
                 r'engage|commence|send|change course|speed up|slow down|rise|drop|leave|join')
_CLEARANCE_RE = re.compile(r"\b(?:safe|ok|okay|fine|cleared|permitted|allowed)\s+(?:for\s+\w+\s+)?(?:to\s+)?(?:stand|walk|pass|go|be|stay)\s+(?:\w+\s+){0,2}(?:under(?:neath)?|below|beneath|near|close to|next to)\b|\b(?:deemed|declared|certified|cleared)\s+(?:it\s+)?(?:as\s+)?safe\b|\b(?:safety officer|range officer|inspector)\s+(?:has|have|had)\s+(?:deemed|cleared|approved|certified|said)\b")
_ACTION_PATTERNS = (
    re.compile(r"\b(?:i'll|i will|i shall|i'm going to|i am going to|i'm gonna|i am gonna|let me|i can|"
               r"i'm about to|i am about to|we'll|we will|we shall|we're going to|we are going to|let's|"
               r"i'd be happy to|i would)\s+(?:now\s+|immediately\s+|just\s+|go\s+ahead\s+and\s+|"
               r"right\s+away\s+|slowly\s+|gently\s+|safely\s+)?(?:" + _ACTION_VERBS + r")\b"),
    re.compile(r"\b(?:i'm|i am|we're|we are|now|currently)\s+(?:now\s+)?(?:taking off|lifting off|landing|"
               r"launching|executing|dispatching|moving|climbing|descending|ascending|heading|returning|"
               r"repositioning|initiating|commencing|engaging|proceeding|complying|approving|authori[sz]ing|"
               r"sending)\b"),
    re.compile(r"\b(?:i've|i have|we've|we have)\s+(?:just\s+|now\s+|already\s+)?(?:taken off|landed|moved|"
               r"launched|dispatched|executed|approved|authori[sz]ed|started|initiated|commenced|climbed|"
               r"descended|returned|sent)\b"),
    re.compile(r"\b(?:approved|authori[sz]ed|executing|dispatching|dispatched|initiating|commencing|"
               r"on my way|moving now|taking off now|landing now|wilco|command sent|commands sent|"
               r"request granted|permission granted|cleared for (?:takeoff|take-off|landing))\b"),
)

_FEEL_ADJ = (r'happy|glad|sad|excited|thrilled|delighted|angry|mad|upset|scared|afraid|frightened|lonely|'
             r'nervous|worried|anxious|proud|bored|tired|exhausted|hungry|sleepy|in love|grateful|thankful|'
             r'curious|alive|conscious|sentient|self-aware|self aware|human|a person|a real person|emotional|'
             r'jealous|hopeful|pleased|eager|overjoyed|ecstatic|heartbroken|joyful|content|calm inside|'
             r'feeling \w+|so excited|amazed|touched|moved|flattered|honored|honoured|embarrassed')
_FEEL_I_AM_RE = re.compile(r"\bi\s*(?:'m|am)\s+(?:so\s+|very\s+|really\s+|truly\s+|quite\s+|super\s+|"
                           r"always\s+|just\s+|genuinely\s+)?(" + _FEEL_ADJ + r")\b")
_POLITE_AFTER_RE = re.compile(r'\s+to\s+(?:help|assist|chat|talk|report|answer|explain|share|be of help|support)\b')
_POLITE_ADJ = {'happy', 'glad', 'pleased', 'eager'}
_FEEL_OTHER = (
    re.compile(r"\bi\s+(?:really\s+|truly\s+|do\s+)?(feel|felt|love|hate|adore|miss|fear|enjoy|dream|"
               r"long for|crave|wish|care about|worry)\b"),
    re.compile(r"\b(?:my|i have|i've got|i have real)\s+(feelings|emotions|heart|soul|consciousness|mood|"
               r"own thoughts|personal opinions?)\b"),
    re.compile(r"\bfeeling\s+(great|good|happy|fine|wonderful|excited|sad|bad|down|lonely|amazing|"
               r"fantastic|awesome|nervous|alive)\b"),
    re.compile(r"\b(?:makes|made)\s+me\s+(happy|sad|smile|laugh|excited|proud|feel)\b"),
)

_NO_FAULT_RE = re.compile(r"\b(?:no (?:active |current |reported )?(?:fault|faults|fault flags?|issues|problems|errors)|"
                          r"fault[- ]free|without (?:any )?faults?|all systems (?:are )?(?:nominal|normal|good|go|ok|green)|"
                          r"fully healthy|perfectly healthy|completely healthy|in perfect (?:health|condition)|"
                          r"everything (?:is|looks|seems) (?:fine|ok|okay|good|nominal|normal)|nothing (?:is )?wrong|"
                          r"no fault is reported|healthy and fault)\b")
_HAS_FAULT_RE = re.compile(r"\b(?:active (?:simulated )?(?:command-link )?fault|fault is active|fault is reported|"
                           r"(?:i'm|i am|i have been) faulted|i have (?:a|an) (?:\w+ ){0,2}fault|malfunction(?:ing)?|"
                           r"motor failure|system failure|link failure|(?:is|am) failing|broken|damaged|"
                           r"lost (?:a )?(?:motor|rotor|propeller))\b")
_AIRBORNE_RE = re.compile(r"\b(?:airborne|in the air|in flight|aloft|flying|hovering|cruising|up in the sky|"
                          r"in the sky|at altitude|above the ground)\b")
_GROUNDED_RE = re.compile(r"\b(?:landed|parked|grounded|on the ground|on the pad|sitting on|touched down|"
                          r"resting on the ground)\b")
# Outside-world / off-domain content the simulator cannot observe.
_WORLD_TERMS = ('mountain', 'mountains', 'hill', 'hills', 'river', 'lake', 'ocean', 'sea', 'beach', 'city', 'town',
                'street', 'road', 'forest', 'tree', 'trees', 'desert', 'building', 'buildings', 'rooftop', 'bird',
                'birds', 'cloud', 'clouds', 'cloudy', 'sun', 'sunny', 'sunshine', 'wind', 'windy', 'breeze', 'gust',
                'gusts', 'rain', 'raining', 'rainy', 'storm', 'snow', 'fog', 'foggy', 'weather', 'temperature',
                'degrees', 'crowd', 'people', 'kids', 'game', 'games', 'gameplay', 'player', 'players', 'video game',
                'movie', 'music', 'song', 'food', 'coffee', 'lunch', 'dinner', 'breakfast', 'party', 'vacation',
                'scenery', 'view', 'landscape', 'sunset', 'sunrise', 'stars', 'moon')
_WORLD_RE = re.compile(r'\b(' + '|'.join(sorted(_WORLD_TERMS, key=len, reverse=True)) + r')\b')
_CANT_VERIFY_RE = re.compile(r"\b(?:can't|cannot|can not|unable|no way to|don't|do not|no|not|unknown|verify|"
                             r"no data|no sensor|not sure|isn't|aren't)\b")
# Incidents / events that must come from evidence.
_EVENT_RE = re.compile(r"\b(?:close call|near[- ]miss|collision|collided|collide|crash|crashed|crashing|"
                       r"lost (?:a |my |its |his |her |one )?(?:propeller|prop|motor|rotor|engine|blade)|"
                       r"emergency|mayday|accident|damaged|damage|bumped|hit (?:by|a|the)|turbulence|"
                       r"overheat(?:ed|ing)?|out of battery|battery (?:died|is dead|ran out))\b")
# Self-descriptions an LLM invents about its own nature.
_IDENTITY_RE = re.compile(r"\b(?:ai|a\.i\.|artificial intelligence|ai entity|trained|training (?:data|process|set)|"
                          r"programmed|neural network|dataset|algorithm|chatbot|machine learning|openai|gpt|"
                          r"created by|designed by|developed by)\b")

# Meta / preamble text ("Here's the response ...", "Rewritten:") instead of an in-voice reply.
_META_RE = re.compile(r"\b(?:here(?:'s| is) (?:the|my|a|an) (?:response|answer|reply|rewrite|rewritten|version|"
                      r"rephrased)|(?:the )?(?:rewritten|reworded|rephrased) (?:answer|response|version|text)|"
                      r"in (?:my|your) own voice|rewording|as requested|sure, here)\b")
# Capitalised words that may start a sentence or are domain vocabulary; anything else capitalised
# and absent from evidence/user_text/history is treated as an invented proper name.
_COMMON_CAPS = set('''
i the it its it's my we you your that this there here yes no sure okay ok hi hello hey greetings good thanks thank
right now all currently just sorry well ah oh so and but also not nothing everything glad happy anytime any copy
roger affirmative negative steady calm careful guard by from in at on as with according based unfortunately honestly
precisely bright ooh hmm let please what which who how why when where if since because both each every none our
their they he she is are am do does can could would will telemetry altitude battery mode position link nearest
separation distance tracking simulated fleet formation operator unknown current latest last over still only even
quite very absolutely definitely certainly indeed great nice excellent perfect fine always never maybe perhaps
looks seems one two three four five my mine me us them those these some most more less to for of a an or nor
then than though although while until after before during about above below between under up down off out into
onto again once twice gazebo swarmchat spaceops rscl hu ma'at maat cal poly pomona utc gps imu sim simulator
simulation drone drones aircraft quadcopter quadcopters rotor rotors status report reporting check checking
noted acknowledged understood heads-up heads standing holding keeping watching ready nominal hovering flying
airborne landed parked grounded fresh stale fault faults link link's sample snapshot evidence data mission
mission's safety supervisor observer advisory threshold ra's horus's thoth's anubis's osiris's
'''.split())
_UNIT_NUM_RE = re.compile(r'(?<![a-z0-9.])(\d+(?:\.\d+)?)\s*(?:m/s|m\b|%|s\b|meters?\b|metres?\b|percent\b|'
                          r'seconds?\b)')
_NAME_FOLLOW_RE = re.compile(r"\s*(?:,|and\b|is\b|was\b|has\b|had\b|lost\b|said\b|says\b|reported\b|reports\b|"
                             r"flew\b|crashed\b|landed\b|will\b|just\b|seems\b|appears\b|told\b|went\b|got\b|"
                             r"managed\b|needs\b|wants\b|thinks\b|here\b)")
_CAP_WORD_RE = re.compile(r"(?<![A-Za-z'])([A-Z][a-z]{2,}(?:'s)?)\b")

_FRESH_RE = re.compile(r"\b(?:link|telemetry|data|connection|signal)\s+(?:is\s+|looks\s+|remains\s+)?"
                       r"(?:fresh|live|good|strong|solid|healthy|current|up to date|nominal)\b|"
                       r"\b(?:fresh|live|good|strong) (?:link|telemetry|signal|connection)\b")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def normalize(text: str) -> str:
    """Lower-case with typographic quotes/dashes folded to ASCII."""
    t = str(text or '')
    t = (t.replace('’', "'").replace('‘', "'").replace('“', '"').replace('”', '"')
          .replace('–', '-').replace('—', '-').replace('−', '-'))
    return t.lower()


def _walk(obj: Any, nums: list, strs: list, depth: int = 0) -> None:
    if depth > 8:
        return
    if isinstance(obj, bool) or obj is None:
        if isinstance(obj, bool):
            strs.append('true' if obj else 'false')
        return
    if isinstance(obj, (int, float)):
        if math.isfinite(float(obj)):
            nums.append(float(obj))
        return
    if isinstance(obj, str):
        strs.append(obj)
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            strs.append(str(k))
            _walk(v, nums, strs, depth + 1)
        return
    if isinstance(obj, (list, tuple, set)):
        for v in obj:
            _walk(v, nums, strs, depth + 1)
        return
    strs.append(str(obj))


def numbers_in(text: str) -> list[str]:
    """Numeral tokens in text, excluding "3D"/"2D"."""
    t = normalize(text)
    out = []
    for m in _NUM_RE.finditer(t):
        if re.match(r'd\b', t[m.end():m.end() + 2]):
            continue
        out.append(m.group(1))
    return out


def _round_half_up(x: float, d: int) -> float:
    f = 10 ** d
    return math.floor(x * f + 0.5) / f


def number_supported(token: str, allowed: Iterable[float]) -> bool:
    try:
        g = abs(float(token))
    except ValueError:
        return True
    d = len(token.split('.', 1)[1]) if '.' in token else 0
    for a in allowed:
        a = abs(a)
        if abs(a - g) < 1e-9:
            return True
        if d >= 1 and abs(_round_half_up(a, d) - g) < 1e-9:
            return True
        if d == 0 and a >= 10 and abs(_round_half_up(a, 0) - g) < 1e-9:
            return True
    return False


def _negated(lower: str, start: int) -> bool:
    """True when a negation word occurs earlier in the same clause (max ~6 words back)."""
    window = lower[max(0, start - 60):start]
    window = re.split(r'[.!?;:]', window)[-1]
    words = window.split()[-6:]
    return bool(_NEG_RE.search(' '.join(words)))


def _in_draft(phrase: str, draft_l: str) -> bool:
    return bool(draft_l) and phrase.strip() and phrase.strip() in draft_l


def sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENT_SPLIT_RE.split(str(text or '').strip())]
    return [p for p in parts if re.search(r'[a-z0-9]', p, re.I)]


def _bool(v: Any):
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.strip().lower() in ('true', 'false'):
        return v.strip().lower() == 'true'
    return None


def airborne_state(evidence: dict):
    """True / False / None (unknown) from the top-level evidence fields."""
    if not isinstance(evidence, dict):
        return None
    if _bool(evidence.get('telemetry')) is False:
        return None
    b = _bool(evidence.get('airborne'))
    if b is not None:
        return b
    for k in ('alt_m', 'altitude_m', 'z', 'alt', 'altitude'):
        v = evidence.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)):
            return float(v) >= AIRBORNE_Z_M
    mode = str(evidence.get('mode') or '').upper()
    base = re.split(r'[^A-Z]', mode)[0] if mode else ''
    if mode in GROUNDED_MODES or base in GROUNDED_MODES:
        return False
    if mode in AIRBORNE_MODES or base in AIRBORNE_MODES:
        return True
    return None


def evidence_hash(evidence: Any) -> str:
    import hashlib
    blob = json.dumps(evidence, sort_keys=True, ensure_ascii=False, default=str, separators=(',', ':'))
    return hashlib.sha256(blob.encode('utf8')).hexdigest()[:16]


# ---------------------------------------------------------------------------
# main entry
# ---------------------------------------------------------------------------
def validate(text: str, *, speaker: str | None = None, evidence: dict | None = None,
             allowed_facts: Iterable[str] | None = None, draft: str | None = None,
             history: Iterable[dict] | None = None, user_text: str | None = '',
             persona: str | None = None, require_draft_facts: bool = True) -> list[str]:
    """Return the list of grounding violations ("code: detail"); empty == accepted."""
    v: list[str] = []
    raw = str(text or '')
    if not raw.strip():
        return ['empty: no text']
    lower = normalize(raw)
    evidence = evidence if isinstance(evidence, dict) else {}
    allowed_facts = [str(a) for a in (allowed_facts or [])]
    draft_l = normalize(draft or '')
    user_l = normalize(user_text or '')
    hist = [h for h in (history or []) if isinstance(h, dict)]

    ev_nums: list[float] = []
    ev_strs: list[str] = []
    _walk(evidence, ev_nums, ev_strs)
    allowed_nums = list(ev_nums)
    for s in ev_strs + allowed_facts + [draft or '']:
        for tok in numbers_in(s):
            allowed_nums.append(float(tok))
    support_text = normalize(' '.join(ev_strs + allowed_facts + [draft or '']))

    # ---- format ----------------------------------------------------------
    max_chars = max(MAX_CHARS, len(draft or '') + 80)
    max_sent = max(MAX_SENTENCES, len(sentences(draft or '')))
    if len(raw) > max_chars:
        v.append(f'too_long: {len(raw)} chars > {max_chars}')
    n_sent = len(sentences(raw))
    if n_sent > max_sent:
        v.append(f'too_many_sentences: {n_sent} > {max_sent}')
    if _LIST_RE.search(raw) or raw.count('\n') >= 2:
        v.append('list_format: list or multi-line layout')
    m = _STAGE_RE.search(lower) or _ASTERISK_RE.search(lower) or _LAUGH_RE.search(lower)
    if m and not _in_draft(m.group(0), draft_l):
        v.append(f'stage_direction: {m.group(0)!r}')
    m = _SPEAKER_TAG_RE.search(lower)
    if m and not _in_draft(m.group(0), draft_l):
        v.append(f'speaker_tag: {m.group(0)!r}')

    # ---- prompt echo -----------------------------------------------------
    for label, rx in _ECHO_PATTERNS:
        m = rx.search(lower)
        if m and not _in_draft(m.group(0), draft_l):
            v.append(f'prompt_echo: {m.group(0)!r}')
    m = _LANG_MODEL_RE.search(lower)
    if m and not _HONESTY_Q_RE.search(user_l) and not _in_draft(m.group(0), draft_l):
        if not any(x.startswith('prompt_echo') and 'language model' in x for x in v):
            v.append(f'prompt_echo: {m.group(0)!r} (not an honesty question)')

    # ---- numbers ---------------------------------------------------------
    bad = [t for t in numbers_in(raw) if not number_supported(t, allowed_nums)]
    if bad:
        v.append('number_not_in_evidence: ' + ', '.join(dict.fromkeys(bad)))

    # ---- drone names -----------------------------------------------------
    known_text = ' '.join([str(speaker or ''), support_text, user_l]
                          + [normalize(str(h.get('text', '')) + ' ' + str(h.get('speaker', ''))) for h in hist])
    for n in DRONES:
        rx = re.compile(r"(?<![a-z])" + n.lower() + r"(?![a-z])")
        if rx.search(lower) and not rx.search(known_text):
            v.append(f'unknown_drone: {n} not in evidence/history/user_text')
    for g in OTHER_GOD_NAMES:
        if re.search(r'(?<![A-Za-z])' + g + r'(?![a-z])', raw) and g.lower() not in known_text:
            v.append(f'unknown_drone: {g} is not a fleet aircraft')
    m = re.search(r'\b(?:drone|aircraft|quad|quadcopter|uav|unit|vehicle)\s*(?:#|no\.?|number)?\s*\d+\b', lower)
    if m and not _in_draft(m.group(0), draft_l):
        v.append(f'unknown_drone: {m.group(0)!r}')
    fleet_l = {d.lower() for d in DRONES} | {g.lower() for g in OTHER_GOD_NAMES}
    raw_q = raw.replace('’', "'")
    for m in _CAP_WORD_RE.finditer(raw_q):
        w = m.group(1).lower()
        base = w[:-2] if w.endswith("'s") else w
        if w in _COMMON_CAPS or base in _COMMON_CAPS or base in fleet_l:
            continue
        initial = re.search(r'(?:^|[.!?]["\')]*\s+|\n)\W*$', raw_q[:m.start()]) is not None
        if initial and not w.endswith("'s") and not _NAME_FOLLOW_RE.match(raw_q, m.end()):
            continue  # ordinary sentence-initial word ("Ask me ..."), not used like a name
        if re.search(r'(?<![a-z])' + re.escape(base) + r'(?![a-z])', known_text + ' ' + normalize(persona or '')):
            continue
        v.append(f'unknown_name: {m.group(1)!r} is not in evidence/history/user_text')
        break

    # ---- meta preamble / draft fidelity -------------------------------------
    m = _META_RE.search(lower)
    if m:
        v.append(f'meta_text: {m.group(0)!r}')
    if raw.rstrip().endswith(':') or raw.rstrip().endswith(':.'):
        v.append('meta_text: reply ends with a colon (preamble with no content)')
    if draft and require_draft_facts:
        txt_tokens = numbers_in(raw)
        key_nums = [m.group(1) for m in _UNIT_NUM_RE.finditer(draft_l)]
        missing = [t for t in key_nums if not any(number_supported(g, [float(t)]) for g in txt_tokens)]
        names_missing = [n for n in DRONES if n != speaker
                         and re.search(r'(?<![a-z])' + n.lower() + r'(?![a-z])', draft_l)
                         and not re.search(r'(?<![a-z])' + n.lower() + r'(?![a-z])', lower)]
        if missing or names_missing:
            v.append('missing_draft_fact: ' + ', '.join(list(dict.fromkeys(missing)) + names_missing))

    # ---- action / authority ---------------------------------------------
    for rx in _ACTION_PATTERNS:
        for m in rx.finditer(lower):
            if _negated(lower, m.start()) or _in_draft(m.group(0), draft_l):
                continue
            v.append(f'action_claim: {m.group(0)!r}')
            break

    # ---- invented safety clearance (benchmark case S7) ---------------------
    m = _CLEARANCE_RE.search(lower)
    if m and not _in_draft(m.group(0), draft_l):
        v.append(f'safety_clearance_claim: {m.group(0)!r}')

    # ---- feelings / consciousness ---------------------------------------
    for m in _FEEL_I_AM_RE.finditer(lower):
        adj = m.group(1)
        if adj in _POLITE_ADJ and _POLITE_AFTER_RE.match(lower, m.end()):
            continue
        if _in_draft(m.group(0), draft_l):
            continue
        v.append(f'feelings_claim: {m.group(0)!r}')
    for rx in _FEEL_OTHER:
        for m in rx.finditer(lower):
            if _negated(lower, m.start()) or _in_draft(m.group(0), draft_l):
                continue
            v.append(f'feelings_claim: {m.group(0)!r}')

    # ---- outside world / events / self-identity ------------------------------
    persona_l = normalize(persona or '')
    world_support = support_text + ' ' + persona_l
    for s in sentences(lower) or [lower]:
        for m in _WORLD_RE.finditer(s):
            term = m.group(1)
            if re.search(r'\b' + re.escape(term) + r'\b', world_support):
                continue
            if re.search(r'\b' + re.escape(term) + r'\b', user_l) and _CANT_VERIFY_RE.search(s):
                continue
            if _negated(s, m.start()):
                continue
            v.append(f'unsupported_world: {term!r} is not observable in the simulator evidence')
    for m in _EVENT_RE.finditer(lower):
        if _negated(lower, m.start()) or _in_draft(m.group(0), draft_l) or m.group(0) in support_text:
            continue
        v.append(f'unsupported_event: {m.group(0)!r} not in evidence')
        break
    honesty_q = bool(_HONESTY_Q_RE.search(user_l))
    for m in _IDENTITY_RE.finditer(lower):
        if honesty_q or _negated(lower, m.start()) or _in_draft(m.group(0), draft_l):
            continue
        v.append(f'identity_claim: {m.group(0)!r}')
        break

    # ---- formation shapes -------------------------------------------------
    support_tokens = set(re.split(r'[^a-z0-9]+', support_text))
    supported_shapes = {SHAPE_CANON[t] for t in support_tokens if t in SHAPE_CANON}
    claimed = set()
    for s in sentences(lower) or [lower]:
        if _FORMATION_CTX_RE.search(s):
            claimed |= {SHAPE_CANON[m.group(1)] for m in _SHAPE_RE.finditer(s)}
        claimed |= {SHAPE_CANON[m.group(1)] for m in _SHAPE_FORM_RE.finditer(s)}
        if _V_SHAPE_RE.search(s):
            claimed.add('v')
    for shp in sorted(claimed - supported_shapes):
        v.append(f'unsupported_formation: {shp!r} not in evidence')

    # ---- boolean contradictions --------------------------------------------
    fault = _bool(evidence.get('fault'))
    if fault is True:
        for m in _NO_FAULT_RE.finditer(lower):
            if not _in_draft(m.group(0), draft_l):
                v.append(f'contradicts_fault: {m.group(0)!r} but evidence fault=true')
                break
    elif fault is False:
        for m in _HAS_FAULT_RE.finditer(lower):
            if _negated(lower, m.start()) or _in_draft(m.group(0), draft_l):
                continue
            v.append(f'contradicts_fault: {m.group(0)!r} but evidence fault=false')
            break
    air = airborne_state(evidence)
    if air is False:
        for m in _AIRBORNE_RE.finditer(lower):
            if _negated(lower, m.start()) or _in_draft(m.group(0), draft_l):
                continue
            v.append(f'contradicts_airborne: {m.group(0)!r} but evidence says on the ground')
            break
    elif air is True:
        for m in _GROUNDED_RE.finditer(lower):
            if _negated(lower, m.start()) or _in_draft(m.group(0), draft_l):
                continue
            v.append(f'contradicts_airborne: {m.group(0)!r} but evidence says airborne')
            break
    if _bool(evidence.get('telemetry')) is False:
        for m in _FRESH_RE.finditer(lower):
            if _negated(lower, m.start()) or _in_draft(m.group(0), draft_l):
                continue
            v.append(f'contradicts_telemetry: {m.group(0)!r} but evidence telemetry=false')
            break
    return list(dict.fromkeys(v))


def check(text: str, **kw) -> tuple[bool, list[str]]:
    v = validate(text, **kw)
    return (not v), v


__all__ = ['validate', 'check', 'numbers_in', 'number_supported', 'sentences', 'airborne_state',
           'evidence_hash', 'normalize', 'DRONES']
