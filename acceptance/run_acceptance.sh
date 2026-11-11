#!/usr/bin/env bash
# HU-MA'AT acceptance suite. Requires Gazebo + control server (8765) + HU (8100) + SpaceOps (8766) running,
# all five aircraft on the ground. FLIES THE SIMULATED AIRCRAFT. Simulator only.
set -u
cd "$(dirname "$0")"
OUT=results/run_$(date -u +%Y%m%dT%H%M%SZ); mkdir -p "$OUT"
fail=0
reset_world(){
  # Default: NO teleport. Each stage starts wherever the aircraft actually landed,
  # like a real fleet. RESET=1 restores the old repeatable start-pose behaviour.
  [ "${RESET:-0}" = 1 ] || { echo "(no world reset: starting from landing spots)"; return 0; }
  # Only when every aircraft is parked: return them to the world's start poses.
  python3 -c 'import json,urllib.request,sys;s=json.load(urllib.request.urlopen("http://127.0.0.1:8765/api/state"));sys.exit(0 if all(d["mode"] in ("READY","GROUNDED") for d in s["drones"]) else 1)' || { echo "not all parked; no reset"; return 1; }
  gz service -s /world/hu_festival/control --reqtype gz.msgs.WorldControl --reptype gz.msgs.Boolean --timeout 3000 --req 'reset: {all: true}' >/dev/null
  sleep 3
}
echo "== Reset Gazebo world to start poses =="; reset_world || fail=1
echo "== Layer A/B: offline + HTTP tests (V9.1 suite) =="
# V9.1 suite + swarm-travel updates in tests/ (originals stay untouched in ~/hu_maat_v9_1)
bash run_offline.sh 3 | tee "$OUT/offline.txt"
grep -q '^OK' "$OUT/offline.txt" || fail=1
echo "== Layer C: live command matrix (non-actuating) =="
python3 command_matrix.py "$OUT" || fail=1
for n in Horus Ra Thoth Anubis Osiris; do
  reset_world || fail=1
  echo "== Layer C: single-aircraft flight $n =="
  python3 flight_test.py "$n" "$OUT" | tee -a "$OUT/flights.txt"
done
grep -q ': FAIL' "$OUT/flights.txt" && fail=1
echo "== Layer C: every motion primitive (Horus) =="
python3 motion_test.py Horus "$OUT" | tee "$OUT/motion.txt"; grep -q 'MOTIONS: PASS' "$OUT/motion.txt" || fail=1
echo "== Reset =="; reset_world || fail=1
echo "== Layer C: swarm travel =="
python3 swarm_test.py "$OUT" | tee "$OUT/swarm.txt"; grep -q 'SWARM: PASS' "$OUT/swarm.txt" || fail=1
echo "== Reset =="; reset_world || fail=1
echo "== Layer C: fleet formations + transitions =="
python3 fleet_test.py "$OUT" line v circle grid diamond | tee "$OUT/fleet.txt"; grep -q 'FLEET: PASS' "$OUT/fleet.txt" || fail=1
echo "== Reset =="; reset_world || fail=1
echo "== Layer C: fault containment + voice emergency =="
python3 fault_test.py "$OUT" | tee "$OUT/fault.txt"; grep -q 'SCENARIO: PASS' "$OUT/fault.txt" || fail=1
echo; [ $fail = 0 ] && echo "ACCEPTANCE: ALL PASS ($OUT)" || echo "ACCEPTANCE: FAILURES PRESENT ($OUT)"
exit $fail
