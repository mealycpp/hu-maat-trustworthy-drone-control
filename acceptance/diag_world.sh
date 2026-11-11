#!/usr/bin/env bash
# Diagnose drone resting height and simulation speed in the running world.
for i in 1 2 3; do
  curl -s localhost:8765/api/state | python3 -c 'import json,sys;print([(d["name"],d["mode"],d["z"]) for d in json.load(sys.stdin)["drones"]])'
  sleep 2
done
echo "-- sim stats --"
timeout 4 gz topic -e -n 1 -t /stats 2>/dev/null | grep -E 'real_time_factor|sim_time|paused' | head -6
echo "-- gazebo log errors --"
grep -iE 'error|warn' ~/hu-telepresence-gazebo/logs/hu_festival_live.log | grep -v -i 'resource path' | tail -8
