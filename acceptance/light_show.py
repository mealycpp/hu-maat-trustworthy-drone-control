#!/usr/bin/env python3
"""HU-MA'AT drone LED "body language" for the Gazebo demo (visual only).

Nothing in this file can command an aircraft: it only READS the flight
supervisor (GET :8765/api/state) and SwarmChat (GET :8766/api/ops/chat/history)
and recolours two visual parts per drone ('hu_rescue_front_LED',
'hu_rescue_id_strip' on X3/base_link) through Gazebo's
/world/<world>/visual_config service.

Colour language (highest priority first):
  fault    FAULT mode, fault flag, or telemetry false ..... red blink (1 Hz)
  tight    nearest teammate closer than 1.0 m .............. solid red
  talking  new SwarmChat message from that drone ........... 0.6 s blue flash
           (any source)
  goal     all airborne drones (>= 2) went from moving to set together
           (formation completed) ........................... 1.5 s rainbow chase
                                                             across the fleet
  modes:
    READY / GROUNDED / WAITING / unknown (parked) .......... dim white
    QUEUED (waiting for takeoff) ........................... slow amber blink (2.4 s)
    TAKEOFF / LANDING ...................................... cyan
    MOVE / TURN / SPIN / FORMATION, error to target > 0.12 m  amber (moving)
    HOVER, or the above with error <= 0.12 m ............... green (set, in slot)

Gazebo load budget (too many visual_config calls make odometry go stale and
the supervisor then refuses moves): a drone is repainted ONLY when its colour
changes (last colour cached per drone), all requests share a token bucket of
--max-rps (default 10 requests/s), and every request times out after 0.5 s.
Request counts are logged every 10 s.

  python3 acceptance/light_show.py                 # body language (default)
  python3 acceptance/light_show.py --mode party    # old random rainbow/strobe
  python3 acceptance/light_show.py --world hu_festival --max-rps 10
SIGTERM / SIGINT restore white LEDs.
"""
import argparse, colorsys, json, math, random, signal, sys, threading, time, urllib.request
from collections import namedtuple

NAMES = ['Horus', 'Ra', 'Thoth', 'Anubis', 'Osiris']
PARTS = ['hu_rescue_front_LED', 'hu_rescue_id_strip']   # keep Gazebo load low (odometry must stay fresh)
LINK = 'X3/base_link'
STATE_URL = 'http://127.0.0.1:8765/api/state'
CHAT_URL = 'http://127.0.0.1:8766/api/ops/chat/history'

# ---------------------------------------------------------------- colour rules
WHITE = (1.0, 1.0, 1.0)
DIM_WHITE = (0.25, 0.25, 0.25)
OFF = (0.03, 0.03, 0.03)
AMBER = (1.0, 0.55, 0.0)
CYAN = (0.0, 0.9, 1.0)
GREEN = (0.0, 1.0, 0.15)
BLUE = (0.05, 0.2, 1.0)
RED = (1.0, 0.0, 0.0)

PARKED_MODES = ('READY', 'GROUNDED', 'WAITING')
TRANSIT_MODES = ('TAKEOFF', 'LANDING')
MOTION_MODES = ('MOVE', 'TURN', 'SPIN', 'FORMATION')
AIRBORNE_MODES = ('HOVER',) + MOTION_MODES
SET_TOL = 0.12          # m, position error that counts as "in slot"
TIGHT_DIST = 1.0        # m, nearest-teammate distance that turns a drone red
TALK_FLASH = 0.6        # s
GOAL_DURATION = 1.5     # s
GOAL_STEP = 0.25        # s between drones lighting up in the chase
QUEUED_PERIOD = 2.4     # s, slow amber blink
FAULT_PERIOD = 1.0      # s, red blink

Decision = namedtuple('Decision', 'rule rgb')


def _blink(now, period, on, off=OFF):
    return on if (now % period) < period / 2 else off


def pos(d):
    try:
        return float(d['x']), float(d['y']), float(d['z'])
    except (KeyError, TypeError, ValueError):
        return None


def target_error(d):
    p, t = pos(d), d.get('target')
    if p is None or not t or len(t) < 3:
        return None
    try:
        return math.dist(p, [float(v) for v in t[:3]])
    except (TypeError, ValueError):
        return None


def nearest_teammate(d, fleet):
    p = pos(d)
    if p is None:
        return None
    ds = [math.dist(p, q) for o in fleet if o.get('name') != d.get('name') for q in [pos(o)] if q is not None]
    return min(ds) if ds else None


def motion_class(d):
    """'moving' / 'set' for airborne drones, None otherwise."""
    mode = str(d.get('mode') or '').upper()
    if mode not in AIRBORNE_MODES:
        return None
    err = target_error(d)
    if mode in MOTION_MODES and (err is None or err > SET_TOL):
        return 'moving'
    return 'set'


def decide(d, fleet, now, flags=None):
    """Pure colour rule. d = one drone dict from /api/state, fleet = all drone dicts.
    flags: {'talk_until': float|None, 'goal_t0': float|None, 'index': int, 'n': int}.
    Returns Decision(rule, rgb)."""
    flags = flags or {}
    mode = str(d.get('mode') or '').upper()
    if mode == 'FAULT' or d.get('fault') or d.get('telemetry') is False:
        return Decision('fault', _blink(now, FAULT_PERIOD, RED))
    near = nearest_teammate(d, fleet)
    if near is not None and near < TIGHT_DIST:
        return Decision('tight', RED)
    talk_until = flags.get('talk_until')
    if talk_until is not None and now < talk_until:
        return Decision('talking', BLUE)
    goal_t0 = flags.get('goal_t0')
    if goal_t0 is not None and goal_t0 <= now < goal_t0 + GOAL_DURATION:
        i, n = flags.get('index', 0), max(1, flags.get('n', 1))
        if now - goal_t0 >= i * GOAL_STEP:       # chase: drones light up one after another
            return Decision('goal', colorsys.hsv_to_rgb(i / n, 1.0, 1.0))
    if mode == 'QUEUED':
        return Decision('queued', _blink(now, QUEUED_PERIOD, AMBER))
    if mode in TRANSIT_MODES:
        return Decision(mode.lower(), CYAN)
    mc = motion_class(d)
    if mc == 'moving':
        return Decision('moving', AMBER)
    if mc == 'set':
        return Decision('set', GREEN)
    return Decision('parked', DIM_WHITE)


class GoalDetector:
    """Fires once when every airborne drone (>= 2) is 'set' after at least two of
    them were 'moving' since the fleet was last all-set (formation completed)."""

    def __init__(self, min_drones=2):
        self.min_drones = min_drones
        self.was_moving = set()

    def update(self, fleet):
        classes = {d.get('name'): motion_class(d) for d in fleet}
        transit = any(str(d.get('mode') or '').upper() in TRANSIT_MODES + ('QUEUED',) for d in fleet)
        airborne = {n: c for n, c in classes.items() if c is not None}
        self.was_moving |= {n for n, c in airborne.items() if c == 'moving'}
        self.was_moving &= set(airborne)            # landed drones no longer count
        if transit or len(airborne) < self.min_drones:
            return False
        if all(c == 'set' for c in airborne.values()):
            fired = len(self.was_moving) >= self.min_drones
            self.was_moving.clear()
            return fired
        return False


class TokenBucket:
    def __init__(self, rate, burst=None, clock=time.monotonic):
        self.rate, self.burst, self.clock = float(rate), float(burst or rate), clock
        self.tokens, self.t = self.burst, clock()
        self.lock = threading.Lock()

    def try_take(self, n=1):
        with self.lock:
            now = self.clock()
            self.tokens = min(self.burst, self.tokens + (now - self.t) * self.rate)
            self.t = now
            if self.tokens >= n:
                self.tokens -= n
                return True
            return False

    def take(self, n, stop):
        while not self.try_take(n):
            if stop.wait(0.02):
                return False
        return True


def chat_speakers(messages, seen, names=NAMES):
    """New drone speakers in a chat history list; updates `seen` (set of ids)."""
    out = []
    for m in messages or []:
        mid = m.get('id')
        if mid is None or mid in seen:
            continue
        seen.add(mid)
        who = str(m.get('speaker') or m.get('sender') or '')
        for n in names:
            if who.lower() == n.lower():
                out.append(n)
    return out


# ---------------------------------------------------------------- runtime
def get_json(url, timeout=1.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


class Painter:
    def __init__(self, world, max_rps):
        from gz.transport13 import Node          # lazy: module imports without gz installed
        from gz.msgs10.visual_pb2 import Visual
        from gz.msgs10.boolean_pb2 import Boolean
        self.Visual, self.Boolean = Visual, Boolean
        self.node = Node()
        self.srv = f'/world/{world}/visual_config'
        self.bucket = TokenBucket(max_rps)
        self.lock = threading.Lock()
        self.sent = 0
        self.failed = 0

    def paint(self, model, rgb, parts=PARTS):
        ok = True
        for part in parts:
            v = self.Visual(); v.name = part; v.parent_name = f'{model}::{LINK}'
            for c in (v.material.ambient, v.material.diffuse, v.material.emissive):
                c.r, c.g, c.b, c.a = rgb[0], rgb[1], rgb[2], 1.0
            res = self.node.request(self.srv, v, self.Visual, self.Boolean, 500)   # 0.5 s max
            with self.lock:
                self.sent += 1
                good = bool(res[0]) if isinstance(res, tuple) else bool(res)
                if not good:
                    self.failed += 1
                ok = ok and good
        return ok


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def run_body(painter, stop, tick=0.1):
    fleet, state_t = [], 0.0
    seen, chat_primed = set(), False
    talk_until, goal_t0 = {}, None
    goal = GoalDetector()
    last_rgb, last_rule = {}, {}
    lock = threading.Lock()

    def poll_state():
        nonlocal fleet, state_t
        while not stop.is_set():
            try:
                ds = get_json(STATE_URL).get('drones') or []
                with lock:
                    fleet, state_t = ds, time.monotonic()
            except Exception as e:
                log('state poll failed:', e)
                stop.wait(1.0)
            stop.wait(0.25)

    def poll_chat():
        nonlocal chat_primed
        while not stop.is_set():
            try:
                msgs = get_json(CHAT_URL).get('messages') or []
                speakers = chat_speakers(msgs, seen)
                if chat_primed:
                    now = time.monotonic()
                    with lock:
                        for n in speakers:
                            talk_until[n] = now + TALK_FLASH
                    for n in speakers:
                        log(f'{n} talking')
                chat_primed = True
            except Exception as e:
                log('chat poll failed:', e)
            stop.wait(1.0)

    for f in (poll_state, poll_chat):
        threading.Thread(target=f, daemon=True).start()

    last_goal_t, last_report, sent_at_report = None, time.monotonic(), 0
    while not stop.is_set():
        now = time.monotonic()
        with lock:
            ds, st, tu = list(fleet), state_t, dict(talk_until)
        if ds and st != last_goal_t:
            last_goal_t = st
            if goal.update(ds):
                goal_t0 = now
                log('formation complete: rainbow chase')
        by_name = {d.get('name'): d for d in ds}
        wanted = []
        for i, name in enumerate(NAMES):
            d = by_name.get(name)
            if d is None:
                continue
            dec = decide(d, ds, now, {'talk_until': tu.get(name), 'goal_t0': goal_t0,
                                      'index': i, 'n': len(NAMES)})
            rgb = tuple(round(c, 3) for c in dec.rgb)
            if last_rule.get(name) != dec.rule:
                log(f'{name}: {last_rule.get(name)} -> {dec.rule} (mode {d.get("mode")})')
                last_rule[name] = dec.rule
            if last_rgb.get(name) != rgb:
                rank = {'fault': 0, 'tight': 0, 'talking': 1, 'goal': 2}.get(dec.rule, 3)
                wanted.append((rank, name, rgb))
        for _, name, rgb in sorted(wanted):           # urgent changes first when budget is short
            if stop.is_set() or not painter.bucket.try_take(len(PARTS)):
                break
            if painter.paint(name, rgb):
                last_rgb[name] = rgb                   # cache only on success; retry later otherwise
        if now - last_report >= 10.0:
            n = painter.sent - sent_at_report
            log(f'rate: {n / (now - last_report):.2f} req/s over last {now - last_report:.1f} s '
                f'(total {painter.sent}, failed {painter.failed})')
            last_report, sent_at_report = now, painter.sent
        stop.wait(tick)


def run_party(painter, stop, hz):
    def drone_loop(i, name):
        rng = random.Random(i)
        t = 0
        while not stop.is_set():
            r = rng.random()
            if r < 0.10:                                   # white strobe
                if painter.bucket.take(2 * len(PARTS), stop):
                    painter.paint(name, (1, 1, 1)); time.sleep(0.06); painter.paint(name, (0, 0, 0))
            elif r < 0.18:                                 # blackout flicker
                if painter.bucket.take(len(PARTS), stop):
                    painter.paint(name, (0, 0, 0))
            else:                                          # rainbow chase, phase-shifted per drone
                h = ((t / (hz * 4)) + i / len(NAMES)) % 1.0
                if painter.bucket.take(len(PARTS), stop):
                    painter.paint(name, colorsys.hsv_to_rgb(h, 1.0, 1.0))
            t += 1
            stop.wait(1.0 / hz)

    threads = [threading.Thread(target=drone_loop, args=(i, n), daemon=True) for i, n in enumerate(NAMES)]
    for th in threads:
        th.start()
    while not stop.is_set():
        time.sleep(0.2)
    for th in threads:
        th.join(timeout=2)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--mode', choices=('body', 'party'), default='body',
                    help='body = state-driven body language (default); party = old random effect')
    ap.add_argument('--world', default='hu_festival')
    ap.add_argument('--hz', type=float, default=2.0, help='party mode update rate per drone')
    ap.add_argument('--max-rps', type=float, default=10.0, help='cap on visual_config requests/s (all drones)')
    args = ap.parse_args(argv)

    painter = Painter(args.world, args.max_rps)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    t0 = time.monotonic()
    log(f'light show mode={args.mode} world={args.world} max_rps={args.max_rps}')
    if args.mode == 'party':
        run_party(painter, stop, args.hz)
    else:
        run_body(painter, stop)
    dt = time.monotonic() - t0
    log(f'summary: {painter.sent} requests in {dt:.1f} s = {painter.sent / max(dt, 1e-6):.2f} req/s '
        f'(failed {painter.failed})')
    for n in NAMES:
        painter.paint(n, WHITE)
    log('lights restored')


if __name__ == '__main__':
    sys.exit(main())
