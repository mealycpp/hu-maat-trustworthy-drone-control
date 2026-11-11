#!/usr/bin/env bash
# Smoke test for spaceops_v8/chat_llm_service.py.
# Starts the service on a NON-default port (default 8797) with SmolLM2-135M-Instruct and 4 threads,
# calls /health and 3 /generate requests, then stops ONLY the process it started (by its own PID).
# Never touches 8100 (HU), 8765 (Gazebo control), 8766 (SpaceOps), 8767 (chat LLM default) or Gazebo.
set -u
A="$(cd "$(dirname "$0")" && pwd)"
ROOT="$A/.."
PY="$ROOT/hu-venv/bin/python"
PORT="${PORT:-8797}"
MODEL="${MODEL:-HuggingFaceTB/SmolLM2-135M-Instruct}"
THREADS="${THREADS:-4}"
case "$PORT" in 8100|8765|8766|8767) echo "refusing reserved port $PORT"; exit 2;; esac
if ss -ltn 2>/dev/null | grep -q "127.0.0.1:$PORT "; then echo "port $PORT already in use; aborting"; exit 2; fi
OUT="$(mktemp -t chat_llm_smoke.XXXXXX.log)"
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 nice -n 10 "$PY" "$ROOT/spaceops_v8/chat_llm_service.py" \
    --port "$PORT" --model "$MODEL" --threads "$THREADS" >"$OUT" 2>&1 &
PID=$!
echo "started chat_llm_service pid=$PID port=$PORT model=$MODEL threads=$THREADS (stdout: $OUT)"
cleanup() {
  if kill -0 "$PID" 2>/dev/null; then
    kill -TERM "$PID"; for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$PID" 2>/dev/null || break; sleep 0.5; done
    kill -0 "$PID" 2>/dev/null && kill -KILL "$PID"
  fi
  echo "stopped pid=$PID"; echo "--- service stdout ---"; cat "$OUT"; rm -f "$OUT"
}
trap cleanup EXIT
T0=$(date +%s.%N)
"$PY" "$A/chat_llm_smoke_client.py" "$PORT"
RC=$?
echo "total smoke wall time: $(echo "$(date +%s.%N) - $T0" | bc 2>/dev/null || echo '?') s"

# Graceful load failure: empty HF cache -> no model offline -> /health loaded=false, /generate fallback.
FPORT=$((PORT+1))
case "$FPORT" in 8100|8765|8766|8767) echo "skip load-failure check"; exit $RC;; esac
EMPTY="$(mktemp -d)"
HF_HOME="$EMPTY" HF_HUB_CACHE="$EMPTY/hub" "$PY" "$ROOT/spaceops_v8/chat_llm_service.py" --port "$FPORT" \
    --model HuggingFaceTB/SmolLM2-135M-Instruct --threads 1 >/dev/null 2>&1 &
FPID=$!
for _ in $(seq 1 60); do
  H="$(curl -s "http://127.0.0.1:$FPORT/health" 2>/dev/null)"
  echo "$H" | grep -q '"loading": false' && break; sleep 0.5
done
echo; echo "== load-failure service pid=$FPID port=$FPORT =="
echo "$H" | "$PY" -c 'import json,sys; h=json.load(sys.stdin); print({k:h[k] for k in ("ok","loaded","model","load_error")})'
curl -s -X POST "http://127.0.0.1:$FPORT/generate" -H 'Content-Type: application/json' \
     -d '{"speaker":"Ra","evidence":{"vehicle":"Ra"},"user_text":"hi"}' -w '  [HTTP %{http_code}]\n'
kill -TERM "$FPID"; wait "$FPID" 2>/dev/null; rm -rf "$EMPTY"; echo "stopped pid=$FPID"
exit $RC
