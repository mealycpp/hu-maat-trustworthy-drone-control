#!/usr/bin/env bash
# Runs the full acceptance suite, keeps the full log, prints the summary lines.
# --background: start detached and return immediately (log: results/last_run.log).
cd "$(dirname "$0")"
if [ "${1:-}" = --background ]; then
  setsid nohup bash "$0" > /dev/null 2>&1 < /dev/null &
  echo "started in background; log: $(pwd)/results/last_run.log"; exit 0
fi
timeout 1500 bash run_acceptance.sh > results/last_run.log 2>&1
code=$?
grep -E '^(FAIL|==|[A-Z][a-z]+: |[A-Z][a-z]+ MOTIONS|SWARM|FLEET|FAULT|ACCEPT|OK|Ran|min obs|[0-9]+/[0-9]+|PASS formation|PASS swarm|PASS sub-group|PASS re-planned|PASS voice|not all)' results/last_run.log > results/last_summary.txt
cat results/last_summary.txt
exit $code
