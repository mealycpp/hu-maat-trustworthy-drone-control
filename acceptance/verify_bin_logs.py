#!/usr/bin/env python3
"""Read exported .BIN files back with ArduPilot's own pymavlink DataFlash reader."""
import glob, sys
from collections import Counter
from pymavlink import DFReader

d = sys.argv[1]
for path in sorted(glob.glob(d + '/ardupilot_logs/*.BIN')):
    log = DFReader.DFReader_binary(path)
    counts = Counter(); first_msgs = []; alts = []; lats = []; modes = []
    while True:
        m = log.recv_msg()
        if m is None: break
        t = m.get_type(); counts[t] += 1
        if t == 'MSG' and len(first_msgs) < 1: first_msgs.append(m.Message)
        if t == 'GPS': lats.append(m.Lat); alts.append(m.Alt)
        if t == 'MODE': modes.append(int(m.ModeNum))
    print(f"{path.split('/')[-1]:11} msgs={sum(counts.values()):6} GPS={counts['GPS']:5} MODE={counts['MODE']:3} EV-MSG={counts['MSG']:3} "
          f"alt={min(alts):.1f}..{max(alts):.1f} m lat={min(lats):.6f}..{max(lats):.6f} | {first_msgs[0]}")
