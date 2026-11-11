#!/usr/bin/env bash
# Current aircraft state + recent supervisor events and HU decisions (who commanded what).
curl -s localhost:8765/api/state | python3 -c '
import json,sys
s=json.load(sys.stdin)
for d in s["drones"]: print(d["name"],d["mode"],d["z"],d["fault"])
print("-- supervisor events (newest first) --")
for e in s["events"][:25]: print(e["time"],e["kind"],e["message"])'
echo "-- last HU decisions --"
tail -6 ~/hu-telepresence-gazebo/logs/hu_real_decisions.jsonl | python3 -c '
import json,sys
for l in sys.stdin:
    d=json.loads(l); print(d.get("timestamp_utc"),d.get("band"),d.get("action"),d.get("targets"),repr(d.get("utterance")),d.get("decision_source"))'
ls -t ~/hu-telepresence-gazebo/logs/missions 2>/dev/null | head -3
