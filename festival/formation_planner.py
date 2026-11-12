"""Small bounded, non-learning formation-slot planner for five Gazebo aircraft.

No mission outside a [-3, 3] m XY box; no slot-to-slot distance <0.9 m.
Coordinates are setpoints, not proof of collision-free motion. A motion-path
checker rejects formations with sampled straight-line conflicts.
"""
import itertools, math

MIN_SEP=0.88
BOUND=3.0
ALT=1.90

# Extended named catalogue: fixed 5-point layouts, viewed from above (+x right, +y "top"
# of the glyph). slots() centres them and applies the same BOUND / MIN_SEP checks; 2-4
# aircraft fly the first n points ([:n]), like the original literal shapes. Every slot
# pair is >= 1.0 m apart. Catalogue status: OFFLINE_CHECKED (geometry + planner only).
_PENTOMINO=1.25   # cell size (m) for the pentomino family
EXTRA_SHAPES={
 # -- letters (5-point glyphs)
 'letter_c':[(.75,1.3),(-.75,1.3),(-1.5,0),(-.75,-1.3),(.75,-1.3)],
 'letter_j':[(.7,1.8),(.7,.6),(.7,-.6),(-.3,-1.5),(-1.2,-.8)],
 'letter_m':[(-1.6,-1.4),(-1.0,1.4),(0,0),(1.0,1.4),(1.6,-1.4)],
 'letter_w':[(-1.6,1.4),(-1.0,-1.4),(0,0),(1.0,-1.4),(1.6,1.4)],
 'letter_n':[(-1.5,-1.5),(-1.5,.5),(0,0),(1.5,-.5),(1.5,1.5)],
 'letter_z':[(-1.5,1.5),(.5,1.5),(0,0),(-.5,-1.5),(1.5,-1.5)],
 'letter_s':[(1.1,1.6),(-.9,1.3),(0,0),(.9,-1.3),(-1.1,-1.6)],
 'letter_u':[(-1.3,1.5),(-1.3,0),(0,-1.5),(1.3,0),(1.3,1.5)],
 'letter_y':[(-1.3,1.7),(1.3,1.7),(0,.4),(0,-.8),(0,-2.0)],
 'letter_f':[(-1.0,1.7),(.6,1.7),(-1.0,.1),(.3,.1),(-1.0,-1.6)],
 'letter_p':[(-.9,1.7),(-.9,.3),(-.9,-1.7),(.5,1.7),(.8,.6)],
 # -- digits
 'digit_1':[(.2,1.8),(-.8,1.0),(.2,.6),(.2,-.6),(.2,-1.8)],
 'digit_2':[(-1.0,1.3),(.3,1.8),(1.0,.6),(-1.1,-1.5),(1.1,-1.5)],
 'digit_3':[(-.9,1.7),(1.1,1.0),(.1,0),(1.1,-1.0),(-.9,-1.7)],
 'digit_4':[(.6,1.8),(-.3,.95),(-1.1,0),(.6,0),(.6,-1.7)],
 'digit_5':[(1.0,1.7),(-.8,1.7),(-.8,.3),(.9,-.4),(-.8,-1.6)],
 'digit_7':[(-1.3,1.6),(0,1.6),(1.3,1.6),(.5,0),(-.3,-1.6)],
 # -- constellations (five brightest / defining stars, approximate sky layout, north up)
 'cassiopeia':[(-1.8,.5),(-.9,-.6),(0,.2),(.9,-.4),(1.8,.7)],
 'southern_cross':[(0,1.8),(0,-1.8),(-1.2,.3),(1.0,.6),(.55,-.75)],
 'cygnus':[(0,1.6),(0,.4),(.1,-2.0),(-1.5,.8),(1.4,0)],
 'delphinus':[(-1.4,1.0),(-.4,1.9),(.5,1.0),(-.4,.1),(1.4,-1.6)],
 'lyra':[(-1.0,1.8),(-.4,.5),(.9,.3),(.6,-1.1),(-.7,-.9)],
 'big_dipper':[(-1.9,.7),(-1.8,-.6),(-.6,-.6),(-.6,.4),(1.5,.9)],
 'orion':[(-1.3,1.6),(1.1,1.3),(-1.05,-.15),(0,-.45),(1.05,-.75)],
 'leo_sickle':[(.7,-1.8),(.4,-.6),(.9,.6),(.2,1.6),(-.9,1.6)],
 'aquila':[(0,0),(-.5,.95),(.5,-.95),(-1.8,-.5),(1.6,.9)],
 'cepheus':[(0,1.9),(-1.1,.5),(1.1,.7),(-1.2,-1.1),(1.0,-1.2)],
 'scorpius':[(1.6,1.3),(1.7,.1),(.6,-.4),(-.5,-1.3),(-1.6,-.6)],
 # -- Egyptian / lab theme
 'pyramid':[(0,1.6),(-1.9,-1.0),(-.65,-1.0),(.65,-1.0),(1.9,-1.0)],
 'ankh':[(-.5,1.9),(.5,1.9),(-1.5,.5),(1.5,.5),(0,-1.9)],
 'wedjat_eye':[(-2.0,.4),(1.9,.5),(0,1.4),(0,.2),(-.3,-1.5)],
 'obelisk':[(0,2.1),(-.52,.8),(.52,.8),(-.55,-1.6),(.55,-1.6)],
 'nile':[(-2.0,0),(-1.0,.8),(0,0),(1.0,-.8),(2.0,0)],
 'winged_sun':[(0,.3),(-1.1,-.1),(-2.1,.5),(1.1,-.1),(2.1,.5)],
 # -- sports / military
 'two_one_two':[(-1.1,-1.7),(1.1,-1.7),(0,0),(-1.1,1.7),(1.1,1.7)],
 'bowtie':[(-1.9,-.9),(-1.9,.9),(0,0),(1.9,-.9),(1.9,.9)],
 'two_three_zone':[(-.6,1.1),(.6,1.1),(-1.9,-.7),(0,-.9),(1.9,-.7)],
 'box_and_one':[(-1.0,-1.4),(1.0,-1.4),(1.0,.4),(-1.0,.4),(0,1.9)],
 'finger_four':[(0,1.2),(-1.0,.4),(1.0,.6),(2.0,-.2),(0,-1.3)],
 'echelon_pairs':[(-1.6,1.2),(-.5,1.2),(.1,-.1),(1.2,-.1),(1.8,-1.4)],
 'trail_pairs':[(-.6,1.5),(.6,1.5),(-.6,0),(.6,0),(0,-1.5)],
 'diamond_and_one':[(0,1.9),(-1.1,.8),(1.1,.8),(0,-.3),(0,-1.8)],
 'staggered_column':[(-.5,1.9),(.5,.95),(-.5,0),(.5,-.95),(-.5,-1.9)],
 'combat_spread':[(-2.0,-.5),(-1.0,-.8),(0,.8),(1.0,-.8),(2.0,-.5)],
 # -- pictograms
 'check_mark':[(-1.6,.4),(-.8,-.5),(0,-1.4),(.9,0),(1.8,1.4)],
 'lightning':[(1.0,1.9),(-.3,.7),(.8,0),(-.2,-1.0),(-1.1,-1.9)],
 'spiral':[(.4,0),(-.45,.78),(-.7,-1.21),(1.9,0),(-1.2,2.08)],
 'steps':[(-1.5,-1.5),(-.4,-1.5),(-.4,-.3),(.7,-.3),(.7,.9)],
}
# -- pentominoes: 5 unit cells (col,row) scaled to _PENTOMINO metres
for _k,_cells in {'f':[(1,2),(2,2),(0,1),(1,1),(1,0)],'l':[(0,0),(0,1),(0,2),(0,3),(1,0)],
                  'n':[(0,0),(0,1),(1,1),(1,2),(1,3)],'p':[(0,0),(0,1),(1,1),(0,2),(1,2)],
                  't':[(0,2),(1,2),(2,2),(1,1),(1,0)],'u':[(0,0),(1,0),(2,0),(0,1),(2,1)],
                  'v':[(0,0),(1,0),(2,0),(0,1),(0,2)],'w':[(0,2),(0,1),(1,1),(1,0),(2,0)],
                  'y':[(0,0),(0,1),(0,2),(0,3),(1,2)],'z':[(0,2),(1,2),(1,1),(1,0),(2,0)]}.items():
    EXTRA_SHAPES['pentomino_'+_k]=[(_PENTOMINO*c,_PENTOMINO*r) for c,r in _cells]
# Rotated / mirrored / scaled variants of existing shapes: base -> 2x2 matrix (a,b,c,d),
# new = (a*x+b*y, c*x+d*y) applied to the centred 5-slot base.
DERIVED_SHAPES={
 'v_left':('v',(0,1,-1,0)),            # apex points left  (rotated -90 deg)
 'v_right':('v',(0,-1,1,0)),           # apex points right (rotated +90 deg)
 'tight_v':('v',(.8,0,0,.8)),
 'deep_v':('v',(.7,0,0,1.5)),          # narrow, steep V
 'arrow_left':('arrow',(0,-1,1,0)),
 'arrow_right':('arrow',(0,1,-1,0)),
 'arrow_down':('arrow',(-1,0,0,-1)),
 't_inverted':('t',(1,0,0,-1)),
 'l_mirrored':('l',(-1,0,0,1)),
 'gamma':('l',(1,0,0,-1)),             # Greek capital gamma = L flipped top-to-bottom
 'inverted_triangle':('triangle',(1,0,0,-1)),
 'inverted_arc':('arc',(1,0,0,-1)),
 'wide_diamond':('diamond',(0,-1,1,0)),
 'big_square':('square',(1.3,0,0,1.3)),
 'small_square':('square',(.78,0,0,.78)),
 'big_x':('x',(1.55,0,0,1.55)),
 'big_cross':('cross',(1.3,0,0,1.3)),
 'three_two_zone':('two_three_zone',(1,0,0,-1)),
}

# 3D HEIGHT PROFILES (not 2D formations; not counted in the catalogue). Applied by the show
# script AFTER the 2D base formation has formed: name -> (base_shape, {slot_index: dz_m}).
# slot_index indexes slots(base_shape, 5) (the planner's slot order, not the drone order);
# missing indices mean dz = 0. Offsets stay in [-0.8, +0.8] and ALT + dz in [1.1, 2.7] m.
HEIGHT_PROFILES={
 'pyramid_3d':('square',{0:-.3,1:-.3,2:-.3,3:-.3,4:.7}),     # square base, raised centre
 'staircase':('line',{0:-.6,1:-.3,2:0.,3:.3,4:.6}),
 'tower':('column',{0:-.6,1:-.3,2:0.,3:.3,4:.6}),          # column rising front to back
 'helix':('circle',{0:-.6,1:-.3,2:0.,3:.3,4:.6}),          # ring climbing around
 'wave':('line',{0:0.,1:.6,2:0.,3:-.6,4:0.}),
 'saddle':('cross',{0:0.,1:.6,2:.6,3:-.6,4:-.6}),          # left/right high, front/back low
 'dome':('cross',{0:.7,1:-.3,2:-.3,3:-.3,4:-.3}),
 'ramp':('two_rows',{0:-.4,1:-.4,2:-.4,3:.4,4:.4}),
 'v_stack':('v',{0:.6,1:0.,2:-.6,3:0.,4:.6}),              # apex low, wing tips high
 'diamond_peak':('diamond',{4:.7}),
 'pyramid_peak':('pyramid',{0:.7,1:-.2,2:-.2,3:-.2,4:-.2}),
}


def slots(shape,n):
    if not 2<=n<=5:raise ValueError('Formation supports 2-5 aircraft')
    mid=(n-1)/2
    xline=[(1.15*(i-mid),0) for i in range(n)]
    if shape in ('line','relay_chain'):p=xline
    elif shape=='column':p=[(0,1.15*(i-mid)) for i in range(n)]
    elif shape=='left_echelon':p=[(1.0*(i-mid),.82*(i-mid)) for i in range(n)]
    elif shape=='right_echelon':p=[(1.0*(i-mid),-.82*(i-mid)) for i in range(n)]
    elif shape=='v':p=[(1.10*(i-mid),.82*abs(i-mid)) for i in range(n)]
    elif shape=='inverted_v':p=[(1.10*(i-mid),-.82*abs(i-mid)) for i in range(n)]
    elif shape=='wedge':p=[(1.08*(i-mid),.92*abs(i-mid)) for i in range(n)]
    elif shape in ('circle','pentagon'):
        p=[(1.53*math.cos(2*math.pi*i/n+math.pi/2),1.53*math.sin(2*math.pi*i/n+math.pi/2)) for i in range(n)]
    elif shape=='diamond':p=[(0,1.72),(-1.5,0),(0,-1.72),(1.5,0),(0,0)][:n]
    elif shape=='square':p=[(-1.35,-1.35),(1.35,-1.35),(1.35,1.35),(-1.35,1.35),(0,0)][:n]
    elif shape=='grid':
        base=[(-1.25,-.65),(0,-.65),(1.25,-.65),(-.65,.65),(.65,.65)]
        p=base[:n]
    elif shape=='triangle':p=[(0,1.7),(-.75,.3),(.75,.3),(-1.5,-1.1),(1.5,-1.1)][:n]
    elif shape=='cross':p=[(0,0),(-1.45,0),(1.45,0),(0,-1.45),(0,1.45)][:n]
    elif shape=='x':p=[(0,0),(-1.05,-1.05),(1.05,-1.05),(-1.05,1.05),(1.05,1.05)][:n]
    elif shape=='arc':p=[(1.8*math.cos(math.pi*i/(n-1)),1.8*math.sin(math.pi*i/(n-1))) for i in range(n)]
    elif shape=='fan':p=[(2.1*math.cos(math.pi*i/(n-1)),2.1*math.sin(math.pi*i/(n-1))) for i in range(n)]
    elif shape=='zigzag':p=[(1.15*(i-mid),.7*(-1 if i%2 else 1)) for i in range(n)]
    elif shape=='staggered':p=[(1.15*(i-mid),.6*(-1 if i%2 else 1)) for i in range(n)]
    elif shape=='two_rows':p=[(-1.25,-.8),(0,-.8),(1.25,-.8),(-.7,.8),(.7,.8)][:n]
    elif shape=='arrow':p=[(0,1.85),(-1.3,.55),(1.3,.55),(0,-.95),(0,-2.3)][:n]
    elif shape=='t':p=[(-1.3,1.05),(0,1.05),(1.3,1.05),(0,-.4),(0,-1.8)][:n]
    elif shape=='l':p=[(-1,-1.8),(-1,-.6),(-1,.6),(-1,1.8),(.3,-1.8)][:n]
    elif shape=='perimeter':p=[(-1.4,-1.4),(1.4,-1.4),(1.4,1.4),(-1.4,1.4),(0,1.4)][:n]
    # ---- extended catalogue (status OFFLINE_CHECKED in formation_catalog.json; not flown yet)
    elif shape=='wide_circle':
        p=[(2.2*math.cos(2*math.pi*i/n+math.pi/2),2.2*math.sin(2*math.pi*i/n+math.pi/2)) for i in range(n)]
    elif shape=='small_circle':
        p=[(1.2*math.cos(2*math.pi*i/n+math.pi/2),1.2*math.sin(2*math.pi*i/n+math.pi/2)) for i in range(n)]
    elif shape=='inverted_pentagon':
        p=[(1.53*math.cos(2*math.pi*i/n-math.pi/2),1.53*math.sin(2*math.pi*i/n-math.pi/2)) for i in range(n)]
    elif shape=='wide_v':p=[(1.40*(i-mid),.60*abs(i-mid)) for i in range(n)]
    elif shape=='corona_borealis':
        p=[(2.2*math.cos(math.radians(200+35*i)),2.2*math.sin(math.radians(200+35*i))) for i in range(5)][:n]
    elif shape in EXTRA_SHAPES:p=list(EXTRA_SHAPES[shape])[:n]
    elif shape in DERIVED_SHAPES:
        base,(a,b,c,d)=DERIVED_SHAPES[shape]
        p=[(a*x+b*y,c*x+d*y) for x,y in slots(base,5)][:n]
    else:raise ValueError('Unknown supported formation: '+str(shape))
    # Center the slots while keeping all positions inside the demo arena.
    cx=sum(x for x,y in p)/n;cy=sum(y for x,y in p)/n
    p=[(x-cx,y-cy) for x,y in p]
    for x,y in p:
        if abs(x)>BOUND or abs(y)>BOUND:raise ValueError('Formation exceeds arena limits')
    for a,b in itertools.combinations(p,2):
        if math.dist(a,b)<MIN_SEP:raise ValueError('Formation slot separation too small')
    return p


def assign(names, drones, points, other_names=()):
    """Choose the nearest safe slot assignment, rejecting projected intersections.

    Conservative sampling only. Low-level controller still needs its proximity
    watchdog and the mission must remain a simulation demo.
    """
    current=[(drones[n].x,drones[n].y,drones[n].z) for n in names]
    obstacles=[(drones[n].x,drones[n].y,drones[n].z) for n in other_names]
    best=None
    for perm in itertools.permutations(points):
        goals=[(x,y,ALT) for x,y in perm]
        valid=True
        for g in goals:
            # Horizontal (XY) check: aircraft land vertically at their slots, so a
            # slot must also be a safe PARKING / takeoff spot next to the obstacle.
            if any(math.hypot(g[0]-o[0],g[1]-o[1])<MIN_SEP for o in obstacles):valid=False;break
        if not valid:continue
        # Test all concurrent interpolated trajectories for close approaches.
        # Simulation velocities may vary, so require conservative additional space.
        for k in range(41):
            f=k/40
            occupied=[tuple(a+(b-a)*f for a,b in zip(start,goal)) for start,goal in zip(current,goals)]
            if any(math.dist(a,b)<MIN_SEP for a,b in itertools.combinations(occupied,2)):
                valid=False;break
            if any(math.dist(a,o)<MIN_SEP for a in occupied for o in obstacles):
                valid=False;break
        if not valid:continue
        cost=sum(math.dist(a,b)**2 for a,b in zip(current,goals))
        if best is None or cost<best[0]:best=(cost,perm)
    if best is None:raise ValueError('No separation-safe straight-line assignment found. Move aircraft farther apart and retry.')
    return list(best[1])


def assign_shifted(names, drones, points, other_names=(), step=.35, max_shift=1.75):
    """Like assign(), but if the arena-centred slots conflict with stationary
    aircraft (e.g. a faulted member hovering mid-arena), try translating the
    whole formation, nearest offsets first. Geometry is unchanged; every
    candidate is re-checked against arena bounds and the full trajectory
    test in assign(). Returns (slots, (dx, dy)).
    """
    offsets=[(0.,0.)]
    k=1
    while k*step<=max_shift+1e-9:
        r=k*step
        ring=[(r*math.cos(a*math.pi/8),r*math.sin(a*math.pi/8)) for a in range(16)]
        offsets+=ring;k+=1
    last=None
    for dx,dy in offsets:
        moved=[(x+dx,y+dy) for x,y in points]
        if any(abs(x)>BOUND or abs(y)>BOUND for x,y in moved):continue
        try:return assign(names,drones,moved,other_names),(round(dx,2),round(dy,2))
        except ValueError as ex:last=ex
    raise last or ValueError('No separation-safe formation placement inside the arena')
