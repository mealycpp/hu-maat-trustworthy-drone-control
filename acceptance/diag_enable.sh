#!/usr/bin/env bash
# Disable Ra's motor controller and watch whether it settles onto its pad.
gz topic -t /Ra/enable -m gz.msgs.Boolean -p 'data: false'
for i in 1 2 3 4; do
  sleep 1
  curl -s localhost:8765/api/state | python3 -c 'import json,sys;print([(d["name"],d["z"]) for d in json.load(sys.stdin)["drones"]])'
done
grep -n -A3 'MulticopterVelocityControl' ~/hu-telepresence-gazebo/worlds/hu_festival.sdf | grep -iE 'enable|topic' | head -6
