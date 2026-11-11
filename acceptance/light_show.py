#!/usr/bin/env python3
"""Flicker the drones' LED parts in Gazebo (visual only; nothing here touches flight).

Recolours each drone's front LED, ID strip, top spine and gold mark through Gazebo's
/world/<world>/visual_config service: rainbow chase with random strobe flashes.
Ctrl+C (or SIGTERM) restores white LEDs.
  python3 acceptance/light_show.py [--hz 4] [--world hu_festival]
"""
import argparse, colorsys, random, signal, sys, threading, time
from gz.transport13 import Node
from gz.msgs10.visual_pb2 import Visual
from gz.msgs10.boolean_pb2 import Boolean

NAMES = ['Horus', 'Ra', 'Thoth', 'Anubis', 'Osiris']
PARTS = ['hu_rescue_front_LED', 'hu_rescue_id_strip']   # keep Gazebo load low (odometry must stay fresh)
LINK = 'X3/base_link'

ap = argparse.ArgumentParser()
ap.add_argument('--hz', type=float, default=2.0)
ap.add_argument('--world', default='hu_festival')
args = ap.parse_args()
SRV = f'/world/{args.world}/visual_config'
node = Node()
stop = threading.Event()


def paint(model, rgb, parts=PARTS):
    for part in parts:
        v = Visual(); v.name = part; v.parent_name = f'{model}::{LINK}'
        for c in (v.material.ambient, v.material.diffuse, v.material.emissive):
            c.r, c.g, c.b, c.a = rgb[0], rgb[1], rgb[2], 1.0
        node.request(SRV, v, Visual, Boolean, 500)


def drone_loop(i, name):
    rng = random.Random(i)
    t = 0
    while not stop.is_set():
        r = rng.random()
        if r < 0.10:                                   # white strobe
            paint(name, (1, 1, 1)); time.sleep(0.06); paint(name, (0, 0, 0))
        elif r < 0.18:                                 # blackout flicker
            paint(name, (0, 0, 0))
        else:                                          # rainbow chase, phase-shifted per drone
            h = ((t / (args.hz * 4)) + i / len(NAMES)) % 1.0
            paint(name, colorsys.hsv_to_rgb(h, 1.0, 1.0))
        t += 1
        stop.wait(1.0 / args.hz)


def finish(*_):
    stop.set()


signal.signal(signal.SIGTERM, finish)
signal.signal(signal.SIGINT, finish)
threads = [threading.Thread(target=drone_loop, args=(i, n), daemon=True) for i, n in enumerate(NAMES)]
for th in threads:
    th.start()
while not stop.is_set():
    time.sleep(0.2)
for th in threads:
    th.join(timeout=2)
for n in NAMES:
    paint(n, (1, 1, 1))
print('lights restored')
