#!/usr/bin/env bash
# Is pymavlink (ArduPilot DataFlash reader) available in any of our Pythons?
for py in /usr/bin/python3 ~/hu-telepresence-gazebo/hu-venv/bin/python ~/rscl-codrone-swarm/.venv/bin/python ~/rscl-hu-latest/.venv/bin/python; do
  [ -x "$py" ] || continue
  v=$("$py" -c 'import pymavlink,sys;print(pymavlink.__version__ if hasattr(pymavlink,"__version__") else "present")' 2>/dev/null) && echo "$py: pymavlink $v" || echo "$py: no pymavlink"
done
command -v mavlogdump.py mavexplorer.py 2>/dev/null
ls ~/hu-telepresence-gazebo/logs/spaceops_sessions | tail -3
head -2 "$(ls -d ~/hu-telepresence-gazebo/logs/spaceops_sessions/*/ | tail -1)horus_flight.csv"
