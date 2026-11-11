"""Small bounded, non-learning formation-slot planner for five Gazebo aircraft.

No mission outside a [-3, 3] m XY box; no slot-to-slot distance <0.9 m.
Coordinates are setpoints, not proof of collision-free motion. A motion-path
checker rejects formations with sampled straight-line conflicts.
"""
import itertools, math

MIN_SEP=0.88
BOUND=3.0
ALT=1.90


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
