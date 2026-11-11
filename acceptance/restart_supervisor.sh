#!/usr/bin/env bash
# Restart ONLY the Gazebo flight supervisor (port 8765); Gazebo, HU and SpaceOps keep running.
# Waits for fresh telemetry from all five aircraft before returning.
cd ~/hu-telepresence-gazebo
pid=$(pgrep -f '^/usr/bin/python3 .*festival/control_server_hu\.py')
[ -n "$pid" ] && kill $pid && sleep 1
setsid nohup /usr/bin/python3 festival/control_server_hu.py > logs/hu_festival_supervisor.log 2>&1 < /dev/null &
for i in $(seq 1 30); do
  sleep 1
  n=$(curl -s localhost:8765/api/state | python3 -c 'import json,sys;print(sum(d["telemetry"] for d in json.load(sys.stdin)["drones"]))' 2>/dev/null)
  [ "$n" = 5 ] && { echo "supervisor up, 5/5 fresh telemetry"; exit 0; }
done
echo "supervisor NOT ready (fresh=$n)"; exit 1
