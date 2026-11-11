"""Fail-closed mission and natural-language extension for the HU Gazebo *simulator*.

This is a separate deterministic route, NOT trained HU inference. Every accepted
mission requires human confirmation; ambiguous/unimplemented requests are refused.
Direct nine-action HU requests still use the original frozen model and thresholds.
"""
import re

NAMES=('Horus','Ra','Thoth','Anubis','Osiris')
SHAPES={
 'inverted v':'inverted_v','reverse v':'inverted_v',
 'left echelon':'left_echelon','right echelon':'right_echelon',
 'relay chain':'relay_chain','communication relay':'relay_chain',
 'two rows':'two_rows','double row':'two_rows','two row':'two_rows',
 'staggered line':'staggered','straight line':'line',
 'v formation':'v','v shape':'v','v':'v','wedge':'wedge',
 'line':'line','column':'column','circle':'circle','ring':'circle',
 'diamond':'diamond','square':'square','grid':'grid','triangle':'triangle',
 'cross':'cross','x shape':'x','x':'x','arc':'arc','fan':'fan',
 'zigzag':'zigzag','staggered':'staggered','echelon left':'left_echelon',
 'echelon right':'right_echelon','pentagon':'pentagon','arrow':'arrow',
 't shape':'t','t':'t','l shape':'l','l':'l','perimeter':'perimeter'}
COLLECTIVE=re.compile(r'\b(?:all|everyone|everybody|the fleet|the swarm|all drones|all five)\b')
EXCLUSION=re.compile(r'\b(?:except|excluding|but not)\s+(horus|ra|thoth|anubis|osiris)\b')
FORMATION=re.compile(r'\b(?:form|formation|shape|make|perform|arrange|rearrange|assume|configure|switch to|change to|change into)\b')
TURN=re.compile(r'\b(?:turn|rotate|spin)\b')
UNSUPPORTED=re.compile(r'\b(?:orbit|patrol|scan|search|sweep|survey|inspect|navigate|follow|dance|flip|backflip|acrobat|recharge|dock|deliver|attack|surround|track|chase|return to home|rth|mission|formation|shape|relay|perimeter|pentagon|wedge|circle|grid|zigzag)\b')
MOVE=re.compile(r'\b(?:move|go|fly|shift|slide|rise|ascend|descend|climb|lower|drop|up|down|forward|forwards|back|backward|backwards|left|right|higher|lower)\b')
ALT=re.compile(r'\b(?:take\s*off|launch|lift\s*off|land|landing|touch\s*down|hover|hold|stop|freeze)\b')
NUMBER=r'(?:\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|fifteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|one hundred|two hundred|half)'
VALUE_RE=re.compile(r'\b(?P<n>'+NUMBER+r')\s*(?P<unit>cm|centimet(?:er|re)s?|meters?|metres?|m)\b')
WORDS={'one':1,'two':2,'three':3,'four':4,'five':5,'six':6,'seven':7,'eight':8,'nine':9,'ten':10,'fifteen':15,
       'twenty':20,'thirty':30,'forty':40,'fifty':50,'sixty':60,'seventy':70,'eighty':80,'ninety':90,
       'hundred':100,'one hundred':100,'two hundred':200,'half':.5}


def addresses(s):
    collective=bool(COLLECTIVE.search(s))
    excluded=EXCLUSION.search(s)
    named=[n for n in NAMES if re.search(r'\b'+n.lower()+r'\b',s)]
    if re.search(r'\b(?:or|maybe|perhaps|probably|either)\b',s):
        return [],'Ambiguous target wording; restate one definite aircraft set.'
    if collective:
        if named and not excluded:
            return [],'Do not combine ALL with individual names unless using EXCEPT.'
        if re.search(r'\b(?:except|excluding|but not)\b',s) and not excluded:
            return [],'Unrecognized exclusion. Say "all except Ra".'
        return [n for n in NAMES if not excluded or n.lower()!=excluded.group(1)],None
    if excluded:return [],'Exclusion requires ALL, e.g. "all except Ra".'
    if named:return named,None
    return [],'Specify a named aircraft or ALL.'


PRIMITIVE_VERBS=('take off','takeoff','launch','lift off','land','touch down','hover','hold position','stop')


def plain_primitive(s):
    """True if s is just HU primitive verb(s) + addressees (names, group, exclusion) + politeness."""
    bare=' '.join(re.sub(r'[^\w\s]',' ',s.lower()).split())
    rest=re.sub(r'\b(?:except|excluding|but not)\s+(?:horus|ra|thoth|anubis|osiris)\b',' ',bare)
    rest=re.sub(r'\b(?:horus|ra|thoth|anubis|osiris|all|everyone|everybody|drones?|the|fleet|swarm|five|and|please|now|ok|okay|hey|guys|team|you)\b',' ',rest)
    rest=' '.join(rest.split())
    return rest in PRIMITIVE_VERBS


def output(action,names,params,label):
    return {'action':action,'targets':names,'params':params,'label':label,
            'reason':'Deterministic simulator mission route; explicit operator confirmation required; not trained HU.'}


def distance_m(s):
    values=list(VALUE_RE.finditer(s))
    if len(values)!=1:return None,'Give exactly one explicit distance and unit, e.g. "10 centimeters".'
    token=values[0].group('n')
    num=WORDS.get(token)
    if num is None:
        try:num=float(token)
        except ValueError:return None,'Unrecognized distance'
    unit=values[0].group('unit')
    meters=float(num) * (.01 if unit=='cm' or unit.startswith('centimet') else 1.)
    if not 0.10-1e-8 <= meters <= 2.0+1e-8:
        return None,'Per-command movement must be from 10 centimeters to 2 meters.'
    return round(meters,4),None


def parse(text):
    s=' '.join(str(text or '').lower().strip(' .!?').split())
    if not s:return {'error':'Empty command.'}
    names,problem=addresses(s)
    # Reject compound commands; the simulator does not have transactional sequences.
    if re.search(r'\b(?:then|after that|followed by|and then)\b',s):
        return {'error':'Compound commands are not implemented. Issue one command at a time.'}
    if FORMATION.search(s):
        if problem:return {'error':problem}
        if len(names)<2:return {'error':'A formation needs at least two aircraft.'}
        # Avoid treating arbitrary prose that includes a shape as an instruction.
        for alias,kind in sorted(SHAPES.items(),key=lambda x:-len(x[0])):
            if re.search(r'(?<!\w)'+re.escape(alias)+r'(?!\w)',s):
                return output('formation',names,{'shape':kind},kind.replace('_',' ').upper()+' FORMATION')
        return {'error':'Unknown formation shape. Try V, circle, diamond, grid, wedge, X, arc or line.'}
    if TURN.search(s):
        if re.search(r'\borbit\b',s):return {'error':'Orbit is not a yaw rotation and is not implemented.'}
        if problem:return {'error':problem}
        if re.search(r'\b(?:spin|full rotation|full turn|360|around yourself|in place)\b',s):
            if re.search(r'\b(?:\d{1,3}|one hundred)\s*(?:degrees?|deg|°)\b',s) and not re.search(r'\b360\b',s):
                return {'error':'Spin means a full rotation; use TURN for a specified angle.'}
            direction=-1 if re.search(r'\b(?:right|clockwise)\b',s) else 1
            return output('spin',names,{'direction':direction},'360-DEGREE IN-PLACE SPIN')
        angle=re.search(r'\b(\d{1,3})\s*(?:°|degrees?|deg)\b',s)
        degrees=int(angle.group(1)) if angle else (180 if 'around' in s else 90)
        if not 5<=degrees<=180:return {'error':'Yaw turn must be from 5 to 180 degrees.'}
        left=bool(re.search(r'\b(?:left|counterclockwise|anticlockwise)\b',s))
        right=bool(re.search(r'\b(?:right|clockwise)\b',s))
        if left==right:return {'error':'Specify exactly one direction: left or right.'}
        return output('turn',names,{'degrees':degrees*(1 if left else -1)},f'TURN {degrees} DEGREES '+('LEFT' if left else 'RIGHT'))
    # Extension vocabulary: deliberately routes via an operator-confirmed, labeled
    # deterministic mission action; it must not be attributed to neural HU.
    if ALT.search(s):
        if problem:return {'error':problem}
        tak=bool(re.search(r'\b(?:take\s*off|launch|lift\s*off)\b',s))
        land=bool(re.search(r'\b(?:land|landing|touch\s*down)\b',s))
        hold=bool(re.search(r'\b(?:hover|hold|stop|freeze)\b',s))
        if sum([tak,land,hold])!=1:return {'error':'Conflicting flight verbs. Issue one command.'}
        action='takeoff' if tak else 'land' if land else 'hover'
        # The trained HU model is the authority for its own primitives (take off / land /
        # hover / stop) addressed to named drones, groups and exclusions - exactly what the
        # HU paper demonstrates. Route to HU (return None) when, after removing addressees,
        # collective words, the exclusion phrase and politeness, only the verb remains.
        # hu_live_server still enforces the exact-target and command-intent guards.
        if plain_primitive(s):
            return None
        return output(action,names,{},action.upper()+' '+', '.join(names))
    if MOVE.search(s):
        if problem:return {'error':problem}
        # One aircraft: body-relative move. Several: SWARM TRAVEL, a rigid translation
        # in the arena frame that the supervisor path-checks before dispatch.
        if not names:return {'error':'Specify a named aircraft or ALL.'}
        directions={
          'up':r'\b(?:up|rise|ascend|climb|higher)\b',
          'down':r'\b(?:down|descend|lower|drop)\b',
          'forward':r'\b(?:forward|forwards|ahead)\b',
          'back':r'\b(?:back|backward|backwards)\b',
          'left':r'\bleft\b','right':r'\bright\b'}
        found=[k for k,p in directions.items() if re.search(p,s)]
        if len(found)!=1:return {'error':'Specify exactly one direction (up/down/forward/back/left/right).'}
        meters,error=distance_m(s)
        if error:return {'error':error}
        d=found[0]
        params={'dz':meters*(1 if d=='up' else -1)} if d in ('up','down') else {
            'forward_m':meters*(1 if d=='forward' else -1)} if d in ('forward','back') else {
            'right_m':meters*(1 if d=='right' else -1)}
        if len(names)==1:
            return output('move',names,params,f'MOVE {names[0]} {d.upper()} {meters:.2f} METERS')
        return output('move',names,params,
                      f'SWARM MOVE {", ".join(names)} {d.upper()} {meters:.2f} METERS (ARENA FRAME, FORMATION KEPT)')
    if UNSUPPORTED.search(s):
        return {'error':'Mission requested but not implemented. No aircraft moved.'}
    # Never force an arbitrary sentence through a closed-label model.
    # 'unrecognized' lets the HU bridge show HU's OWN decision (normally REFUSE) for arbitrary
    # sentences; the bridge never executes a non-REFUSE prediction for such a sentence.
    return {'error':'Unsupported instruction. Use a named takeoff/landing, exact relative move, turn, or formation.',
            'unrecognized':True}
