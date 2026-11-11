#!/usr/bin/env bash
# Restart the HU-MA'AT services WITHOUT touching Gazebo (unlike run_live.sh, whose
# trap kills Gazebo when the HU bridge exits). Usage: restart_services.sh [supervisor] [hu] [spaceops]
# (no arguments = all three). Each waits for its health check before returning.
BASE=~/hu-telepresence-gazebo; REPO=~/rscl-hu-latest
want(){ [ $# -eq 0 ] && return 0; for a in "${ARGS[@]}"; do [ "$a" = "$1" ] && return 0; done; return 1; }
ARGS=("$@"); [ ${#ARGS[@]} -eq 0 ] && ARGS=(supervisor hu spaceops)
stop(){ pid=$(pgrep -f "$1"); [ -n "$pid" ] && kill $pid && sleep 1; }
ok=0
if want supervisor; then
  stop '^/usr/bin/python3 .*festival/control_server_hu\.py'
  (cd "$BASE" && setsid nohup /usr/bin/python3 festival/control_server_hu.py > logs/hu_festival_supervisor.log 2>&1 < /dev/null &)
  for i in $(seq 1 30); do sleep 1; n=$(curl -s localhost:8765/api/state | python3 -c 'import json,sys;print(sum(d["telemetry"] for d in json.load(sys.stdin)["drones"]))' 2>/dev/null); [ "$n" = 5 ] && break; done
  [ "$n" = 5 ] && echo "supervisor :8765 up, 5/5 fresh telemetry" || { echo "supervisor NOT ready"; ok=1; }
fi
if want hu; then
  stop 'hu-venv/bin/python .*festival/hu_live_server\.py'
  (cd "$REPO" && PYTHONPATH="$REPO" setsid nohup "$BASE/hu-venv/bin/python" "$BASE/festival/hu_live_server.py" --port 8100 \
      --checkpoint "$REPO/hu/models/hu_heads_v1_1_REGISTERS.pt" > "$BASE/logs/hu_live_server.log" 2>&1 < /dev/null &)
  for i in $(seq 1 90); do sleep 1; curl -s -m 2 localhost:8100/api/health | grep -q '"checkpoint_loaded": true' && break; done
  curl -s -m 2 localhost:8100/api/health | grep -q '"checkpoint_loaded": true' && echo "HU bridge :8100 up, checkpoint loaded" || { echo "HU bridge NOT ready"; tail -5 "$BASE/logs/hu_live_server.log"; ok=1; }
fi
if want spaceops; then
  stop 'spaceops_v8/spaceops_server\.py'
  (cd "$BASE/spaceops_v8" && setsid nohup python3 "$BASE/spaceops_v8/spaceops_server.py" --port 8766 > "$BASE/logs/spaceops_server.log" 2>&1 < /dev/null &)
  for i in $(seq 1 20); do sleep 1; curl -s -m 2 localhost:8766/api/ops/state | grep -q '"online": true' && break; done
  curl -s -m 2 localhost:8766/api/ops/state | grep -q '"online": true' && echo "SpaceOps :8766 up, recording" || { echo "SpaceOps NOT ready"; ok=1; }
fi
exit $ok
