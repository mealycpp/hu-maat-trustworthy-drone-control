#!/usr/bin/env bash
# Wait (up to $1 seconds, default 560) for a background acceptance run, then print its status.
LOG=~/hu-telepresence-gazebo/acceptance/results/last_run.log
end=$((SECONDS+${1:-560}))
while [ $SECONDS -lt $end ]; do grep -q '^ACCEPTANCE:' "$LOG" 2>/dev/null && break; sleep 5; done
grep -E '^(FAIL|==|ACCEPT|Ran|OK|[0-9]+/|[A-Z][a-z]+: |[A-Z][a-z]+ MOTIONS|SWARM|FLEET|FAULT|min obs)' "$LOG"
grep -q '^ACCEPTANCE:' "$LOG" || echo "(still running...)"
