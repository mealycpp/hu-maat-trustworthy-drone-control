#!/usr/bin/env bash
set -euo pipefail
BASE="$HOME/hu-telepresence-gazebo"
REPO="$HOME/rscl-hu-latest"
cd "$REPO"
if [ ! -f "$BASE/worlds/hu_festival.sdf" ]; then echo 'Missing HU Festival world'; exit 1; fi
# Choose a Python interpreter that can load the original HU dependencies.
# Gazebo bindings remain in system Python, inside the separate supervisor process.
PYTHON="${HU_PYTHON:-}"
if [ -n "$PYTHON" ]; then
  if ! "$PYTHON" -c 'from hu.gateway.hu_service import HU; import yaml, torch, transformers' >/dev/null 2>&1; then
    echo "HU_PYTHON=$PYTHON cannot import the HU dependencies. Check its packages."; exit 1
  fi
else
  for candidate in "$REPO/.venv/bin/python" "$HOME/rscl-codrone-swarm/.venv/bin/python" python3; do
    if [ "$candidate" = python3 ] || [ -x "$candidate" ]; then
      if "$candidate" -c 'from hu.gateway.hu_service import HU; import yaml, torch, transformers' >/dev/null 2>&1; then PYTHON="$candidate"; break; fi
    fi
  done
  if [ -z "$PYTHON" ]; then
    echo 'No suitable HU Python interpreter found.'
    echo "Try: $HOME/rscl-codrone-swarm/.venv/bin/python -m pip install 'torch' 'transformers' 'pyyaml'"
    exit 1
  fi
fi
printf 'Using HU Python interpreter: %s\n' "$PYTHON"

/usr/bin/python3 -c 'from gz.transport13 import Node; from gz.msgs10.odometry_pb2 import Odometry' || {
  echo 'Install Gazebo native Python bindings: sudo apt install python3-gz-transport13 python3-gz-msgs10'; exit 1;
}
if pgrep -af '^gz sim( |$)' > /dev/null; then
  echo 'Close old Gazebo windows before launching one HU Festival world.'; exit 1
fi
cd "$BASE"
gz sim -r -v 3 "$BASE/worlds/hu_festival.sdf" >"$BASE/logs/hu_festival_live.log" 2>&1 &
GZ_PID=$!
/usr/bin/python3 "$BASE/festival/control_server_hu.py" >"$BASE/logs/hu_festival_supervisor.log" 2>&1 &
SUP_PID=$!
cleanup(){ kill "$SUP_PID" "$GZ_PID" 2>/dev/null || true; }
trap cleanup EXIT
sleep 4
if ! kill -0 "$GZ_PID" 2>/dev/null; then
  echo 'Gazebo exited during startup; see:' "$BASE/logs/hu_festival_live.log"; exit 1
fi
if ! kill -0 "$SUP_PID" 2>/dev/null; then
  echo 'Gazebo supervisor exited during startup; see:' "$BASE/logs/hu_festival_supervisor.log"; exit 1
fi
cd "$REPO"
HU_ARGS=(--port 8100)
if [ -n "${HU_CKPT:-}" ]; then HU_ARGS+=(--checkpoint "$HU_CKPT"); fi
"$PYTHON" "$BASE/festival/hu_live_server.py" "${HU_ARGS[@]}"
