#!/usr/bin/env bash
# Read-only probe used while writing hu_maat.sh (GETs only, no process changes).
echo "== tools"; command -v shellcheck curl ss pgrep setsid gz python3 jq
echo "== worlds"; ls -la ~/hu-telepresence-gazebo/worlds/
echo "== logs"; ls -la ~/hu-telepresence-gazebo/logs/ | head -40
echo "== listeners"; ss -ltnp 2>/dev/null | grep -E ':(8100|8765|8766)\b'
echo "== procs"; pgrep -af 'gz sim|control_server_hu|hu_live_server|spaceops_server' | grep -v probe_launcher
echo "== health"; curl -s -m 3 localhost:8100/api/health; echo
echo "== state"; curl -s -m 3 localhost:8765/api/state | python3 -c 'import json,sys;d=json.load(sys.stdin);d.pop("events",None);print(json.dumps(d))'
echo "== ops keys"; curl -s -m 3 localhost:8766/api/ops/state | python3 -c 'import json,sys;d=json.load(sys.stdin);print({k:(v if not isinstance(v,(list,dict)) else type(v).__name__) for k,v in d.items()})'
echo "== df"; df -Pk ~/hu-telepresence-gazebo/logs
