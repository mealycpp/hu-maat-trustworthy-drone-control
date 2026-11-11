#!/usr/bin/env bash
set -euo pipefail
BASE="$HOME/hu-telepresence-gazebo"
cd "$BASE"
if [ ! -f worlds/hu_festival.sdf ]; then
  echo "Missing hu_festival.sdf. First run: python3 ~/hu_festival_v2_install/install.py"
  exit 1
fi
python3 -c 'from gz.transport13 import Node; from gz.msgs10.odometry_pb2 import Odometry' || {
  echo 'Install dependencies: sudo apt install -y python3-gz-transport13 python3-gz-msgs10'
  exit 1
}
mkdir -p logs
if pgrep -af '^gz sim( |$)' >/dev/null; then
  echo 'An existing Gazebo session is running. Close its window before launch to avoid multiple worlds.'
  exit 1
fi
printf 'Launching HU Festival world...\n'
gz sim -r -v 3 "$BASE/worlds/hu_festival.sdf" >"$BASE/logs/hu_festival_gz.log" 2>&1 &
GZ_PID=$!
trap 'kill "$GZ_PID" 2>/dev/null || true' EXIT
sleep 4
printf '\nOpen http://localhost:8765 on your Windows laptop for the command console.\n'
printf 'The Gazebo window shows five CoDrone-inspired presentation models.\n'
printf 'Press Ctrl+C to stop the supervisor (Gazebo will also close).\n\n'
python3 "$BASE/festival/control_server.py"
