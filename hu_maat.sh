#!/usr/bin/env bash
# =============================================================================
# hu_maat.sh - conference-safe master launcher for the HU-MA'AT drone simulation
# RSCL lab, Cal Poly Pomona.  Gazebo Harmonic + 5 drones + 3 Python services
# + the optional SwarmChat language service (chat LLM, :8767).
#
#   hu_maat.sh start [--env festival|forest|military|mountains]
#   hu_maat.sh env <festival|forest|military|mountains>
#   hu_maat.sh stop [--force]
#   hu_maat.sh status
#   hu_maat.sh preflight
#   hu_maat.sh chat-model <model-id>   (restarts ONLY the chat LLM service)
#
# Design rules (see OPERATOR_GUIDE.md):
#  * Readiness is decided by real health checks with deadlines, never fixed sleeps.
#  * Services are launched detached (setsid nohup ... < /dev/null &) so no launcher
#    exit/trap can ever take Gazebo down (the run_live.sh EXIT-trap problem).
#  * Process patterns are anchored at the start of the command line, so they can
#    never match the shell running this script.
#  * A port held by anything that is NOT our service is refused, never killed.
#  * Logs are only ever appended to; nothing in logs/ is truncated or deleted.
#  * status and preflight are strictly read-only (HTTP GETs + ps/ss/df only).
#  * The chat LLM is OPTIONAL for flight safety (it can never move aircraft; SpaceOps
#    falls back to deterministic chat without it): when it is down the result is
#    DEGRADED, never NOT READY.
# =============================================================================
set -u -o pipefail

BASE="${HU_MAAT_BASE:-$HOME/hu-telepresence-gazebo}"
REPO="${HU_MAAT_REPO:-$HOME/rscl-hu-latest}"
LOGS="$BASE/logs"
ENV_FILE="$LOGS/current_env"
LAUNCH_LOG="$LOGS/hu_maat.log"
HU_PY="$BASE/hu-venv/bin/python"
HU_CKPT="${HU_CKPT:-$REPO/hu/models/hu_heads_v1_1_REGISTERS.pt}"
NAMES=(Horus Ra Thoth Anubis Osiris)

# Deadlines (seconds). Override via environment if the laptop is slow.
GZ_TIMEOUT="${GZ_TIMEOUT:-120}"        # Gazebo up + 5 odometry topics advertised
TELEM_TIMEOUT="${TELEM_TIMEOUT:-90}"   # supervisor reports 5/5 fresh telemetry
HU_TIMEOUT="${HU_TIMEOUT:-240}"        # HU model checkpoint_loaded
OPS_TIMEOUT="${OPS_TIMEOUT:-45}"       # SpaceOps online
CHAT_TIMEOUT="${CHAT_TIMEOUT:-180}"    # chat LLM loaded (optional service)
STOP_TIMEOUT="${STOP_TIMEOUT:-20}"     # graceful stop per service before SIGKILL
MIN_FREE_MB="${MIN_FREE_MB:-2048}"     # preflight: free disk for logs
MIN_SEP_M="${MIN_SEP_M:-0.88}"         # preflight: min parked horizontal separation
PARK_Z_MAX="${PARK_Z_MAX:-0.30}"       # an aircraft above this altitude is never "parked"

# Anchored process patterns (pgrep -f matches the full command line).
PAT_GZ='^gz sim( |$)'
PAT_SUP='^/usr/bin/python3 ([^ ]*/)?festival/control_server_hu\.py( |$)'
PAT_HU='^[^ ]*hu-venv/bin/python3? [^ ]*festival/hu_live_server\.py( |$)'
PAT_OPS='^[^ ]*python3? [^ ]*spaceops_v8/spaceops_server\.py( |$)'
PAT_CHAT='^[^ ]*hu-venv/bin/python3? [^ ]*spaceops_v8/chat_llm_service\.py( |$)'

URL_SUP='http://127.0.0.1:8765/api/state'
URL_HU='http://127.0.0.1:8100/api/health'
URL_OPS='http://127.0.0.1:8766/api/ops/state'
URL_CHAT='http://127.0.0.1:8767/health'

# Chat LLM model: $CHAT_LLM_MODEL > config file (one line) > default.
CHAT_MODEL_FILE="$BASE/config/chat_llm_model"
CHAT_MODEL_DEFAULT='HuggingFaceTB/SmolLM2-360M-Instruct'
CHAT_THREADS="${CHAT_THREADS:-6}"
CHAT_LOG="$LOGS/chat_llm_service.log"

LAND_CURL="curl -s -X POST localhost:8765/api/command -H 'Content-Type: application/json' -d '{\"action\":\"emergency_land\",\"targets\":[\"Horus\",\"Ra\",\"Thoth\",\"Anubis\",\"Osiris\"]}'"

# ----------------------------------------------------------------------------- helpers
ts(){ date '+%Y-%m-%d %H:%M:%S'; }
WRITE_LOG=0   # only start/env/stop write to the launcher log; status/preflight stay read-only
say(){
  printf '%s\n' "$*"
  if [ "$WRITE_LOG" = 1 ]; then printf '[%s] %s\n' "$(ts)" "$*" >> "$LAUNCH_LOG" 2>/dev/null || true; fi
}
die(){ say "ERROR: $*"; exit 2; }

world_for_env(){
  case "$1" in
    festival)  echo hu_festival.sdf ;;
    forest)    echo hu_env_forest.sdf ;;
    military)  echo hu_env_military.sdf ;;
    mountains) echo hu_env_mountains.sdf ;;
    *) return 1 ;;
  esac
}
env_for_world(){
  case "$(basename "$1")" in
    hu_festival.sdf)       echo festival ;;
    hu_env_forest.sdf)     echo forest ;;
    hu_env_military.sdf)   echo military ;;
    hu_env_mountains.sdf)  echo mountains ;;
    *) echo "custom" ;;
  esac
}

pids_of(){ pgrep -f "$1" 2>/dev/null | tr '\n' ' ' | sed 's/ *$//'; }
running(){ pgrep -f "$1" >/dev/null 2>&1; }

# World file of the running Gazebo (from its command line), empty if none.
running_world(){ pgrep -af "$PAT_GZ" 2>/dev/null | grep -o '[^ ]*\.sdf' | head -1; }

get(){ curl -s -m "${2:-3}" --fail "$1" 2>/dev/null; }

# Port ownership: prints free | ours <pid> | foreign <pid|?> <cmdline>
port_status(){
  local port=$1 pat=$2 line pid cmd
  line=$(ss -Hltnp "sport = :$port" 2>/dev/null | head -1)
  if [ -z "$line" ]; then echo free; return; fi
  pid=$(printf '%s' "$line" | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)
  if [ -z "$pid" ]; then echo "foreign ? (owner not visible - another user or root)"; return; fi
  if pgrep -f "$pat" 2>/dev/null | grep -qx "$pid"; then echo "ours $pid"; return; fi
  cmd=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | cut -c1-120)
  echo "foreign $pid $cmd"
}

# --------------------------------------------------------------- JSON analysis (python)
# Reads supervisor /api/state JSON on stdin.
PY_FLEET=$(cat <<'PYEOF'
import json, sys, math, itertools
mode = sys.argv[1]; zmax = float(sys.argv[2]) if len(sys.argv) > 2 else 0.30
try:
    st = json.load(sys.stdin); dr = st.get('drones', [])
except Exception:
    print('PARSE_ERROR'); sys.exit(3)
PARKED = ('READY', 'GROUNDED')
def parked(d): return d.get('mode') in PARKED and float(d.get('z', 9)) < zmax
if mode == 'fresh':
    print(sum(1 for d in dr if d.get('telemetry')))
elif mode == 'count':
    print(len(dr))
elif mode == 'notparked':
    print(' '.join('%s(%s,z=%.2f)' % (d['name'], d.get('mode'), float(d.get('z', 0))) for d in dr if not parked(d)))
elif mode == 'table':
    for d in dr:
        print('  %-7s %-10s z=%5.2f m  x=%6.2f y=%6.2f  telemetry=%s%s' % (
            d['name'], d.get('mode'), float(d.get('z', 0)), float(d.get('x', 0)), float(d.get('y', 0)),
            'FRESH' if d.get('telemetry') else 'STALE', '  FAULT' if d.get('fault') else ''))
elif mode == 'minsep':
    best = None
    for a, b in itertools.combinations(dr, 2):
        s = math.hypot(float(a['x']) - float(b['x']), float(a['y']) - float(b['y']))
        if best is None or s < best[0]: best = (s, a['name'], b['name'])
    print('%.3f %s-%s' % best if best else 'NA')
PYEOF
)
fleet(){ printf '%s' "$1" | python3 -c "$PY_FLEET" "$2" "$PARK_Z_MAX"; }

# Reads chat LLM /health JSON on stdin.  modes: loaded | failed | line
PY_CHAT=$(cat <<'PYEOF'
import json, sys
mode = sys.argv[1]
try:
    h = json.load(sys.stdin)
except Exception:
    print('PARSE_ERROR'); sys.exit(3)
if mode == 'loaded':
    sys.exit(0 if h.get('loaded') is True else 1)
elif mode == 'failed':   # loader finished without a model: waiting longer will not help
    sys.exit(0 if (not h.get('loaded') and not h.get('loading') and h.get('load_error')) else 1)
elif mode == 'line':
    lat = h.get('avg_latency_ms')
    s = 'model=%s loaded=%s router_loaded=%s requests=%s fallbacks=%s accepted=%s rejected=%s avg_latency=%s' % (
        h.get('model') or ('(requested %s)' % h.get('requested_model')), h.get('loaded'), h.get('router_loaded'),
        h.get('requests'), h.get('fallbacks'), h.get('accepted'), h.get('rejected'),
        ('%s ms' % lat) if lat is not None else 'n/a')
    if not h.get('loaded'):
        s += '  ' + ('LOADING' if h.get('loading') else 'load_error=%s' % str(h.get('load_error'))[:120])
    print(s)
PYEOF
)
chatj(){ printf '%s' "$1" | python3 -c "$PY_CHAT" "$2" 2>/dev/null; }

# Model id the chat service should run: env > config file > default.
chat_model(){
  local m=""
  if [ -n "${CHAT_LLM_MODEL:-}" ]; then m=$CHAT_LLM_MODEL
  elif [ -r "$CHAT_MODEL_FILE" ]; then m=$(head -1 "$CHAT_MODEL_FILE" | tr -d '[:space:]'); fi
  printf '%s\n' "${m:-$CHAT_MODEL_DEFAULT}"
}
valid_model_id(){ [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$ ]] && [[ "$1" != *..* ]]; }

# Wait until "$@" succeeds, polling once per second, up to $1 seconds.
# Optional liveness guard: WAIT_ALIVE_PAT - abort early if that process dies.
# Optional failure probe: WAIT_FAIL_FN - abort early if that function succeeds.
wait_until(){
  local timeout=$1 desc=$2; shift 2
  local deadline=$(( $(date +%s) + timeout ))
  printf '  waiting for %s (max %ss) ' "$desc" "$timeout"
  while :; do
    if "$@"; then printf ' ok\n'; return 0; fi
    if [ -n "${WAIT_ALIVE_PAT:-}" ] && ! running "$WAIT_ALIVE_PAT"; then printf ' process exited\n'; return 1; fi
    if [ -n "${WAIT_FAIL_FN:-}" ] && "$WAIT_FAIL_FN"; then printf ' failed\n'; return 1; fi
    if [ "$(date +%s)" -ge "$deadline" ]; then printf ' TIMEOUT\n'; return 1; fi
    printf '.'; sleep 1
  done
}

# Readiness probes
probe_topics(){
  local list n
  list=$(timeout 8 gz topic -l 2>/dev/null) || return 1
  for n in "${NAMES[@]}"; do
    printf '%s\n' "$list" | grep -qx "/model/${n,,}/odometry" || return 1
  done
}
probe_telemetry(){ local j; j=$(get "$URL_SUP") || return 1; [ "$(fleet "$j" fresh)" = 5 ]; }
probe_hu(){ get "$URL_HU" | grep -q '"checkpoint_loaded": *true'; }
probe_ops(){ get "$URL_OPS" | grep -q '"online": *true'; }
probe_chat(){ local h; h=$(get "$URL_CHAT") || return 1; chatj "$h" loaded; }
probe_chat_failed(){ local h; h=$(get "$URL_CHAT") || return 1; chatj "$h" failed; }

# Graceful stop of every process matching a pattern: SIGINT -> SIGTERM -> SIGKILL.
# (SIGINT may be ignored by processes started detached, hence the escalation.)
stop_pattern(){
  local name=$1 pat=$2 pids sig t deadline
  pids=$(pids_of "$pat")
  if [ -z "$pids" ]; then say "  $name: not running"; return 0; fi
  for sig in INT TERM; do
    # shellcheck disable=SC2086
    kill -s "$sig" $pids 2>/dev/null
    if [ "$sig" = INT ]; then t=5; else t=$STOP_TIMEOUT; fi
    deadline=$(( $(date +%s) + t ))
    while running "$pat" && [ "$(date +%s)" -lt "$deadline" ]; do sleep 0.5; done
    if ! running "$pat"; then say "  $name: stopped (SIG$sig, pids $pids)"; return 0; fi
  done
  pids=$(pids_of "$pat")
  # shellcheck disable=SC2086
  [ -n "$pids" ] && kill -s KILL $pids 2>/dev/null
  deadline=$(( $(date +%s) + 5 ))
  while running "$pat" && [ "$(date +%s)" -lt "$deadline" ]; do sleep 0.5; done
  if running "$pat"; then say "  $name: STILL RUNNING after SIGKILL (pids $(pids_of "$pat"))"; return 1; fi
  say "  $name: stopped (SIGKILL, pids $pids)"
}

banner(){ printf '%s\n' "----- $(ts) hu_maat.sh $* -----"; }
log_header(){ printf '\n===== %s hu_maat.sh %s =====\n' "$(ts)" "$1" >> "$2" 2>/dev/null || true; }

record_env(){ # $1 env name, $2 world file
  printf '%s %s %s\n' "$1" "$2" "$(date -Iseconds)" > "$ENV_FILE.tmp" && mv -f "$ENV_FILE.tmp" "$ENV_FILE"
  printf '%s %s %s\n' "$(date -Iseconds)" "$1" "$2" >> "$LOGS/env_history.log"
}

# ---------------------------------------------------------------- launchers (detached)
launch_gazebo(){ # $1 world file name
  local lf; lf="$LOGS/gazebo_$(env_for_world "$1").log"
  log_header "start gazebo $1" "$lf"
  (cd "$BASE" && setsid nohup gz sim -r -v 3 "worlds/$1" >> "$lf" 2>&1 < /dev/null &)
  say "  Gazebo launched with worlds/$1 (log: $lf)"
}
launch_supervisor(){
  log_header "start supervisor" "$LOGS/hu_festival_supervisor.log"
  (cd "$BASE" && setsid nohup /usr/bin/python3 festival/control_server_hu.py >> logs/hu_festival_supervisor.log 2>&1 < /dev/null &)
  say "  supervisor launched (log: $LOGS/hu_festival_supervisor.log)"
}
launch_hu(){
  log_header "start hu" "$LOGS/hu_live_server.log"
  (cd "$REPO" && PYTHONPATH="$REPO" setsid nohup "$HU_PY" "$BASE/festival/hu_live_server.py" --port 8100 \
      --checkpoint "$HU_CKPT" >> "$LOGS/hu_live_server.log" 2>&1 < /dev/null &)
  say "  HU bridge launched (log: $LOGS/hu_live_server.log)"
}
launch_ops(){
  log_header "start spaceops" "$LOGS/spaceops_server.log"
  (cd "$BASE/spaceops_v8" && setsid nohup python3 "$BASE/spaceops_v8/spaceops_server.py" --port 8766 \
      >> "$LOGS/spaceops_server.log" 2>&1 < /dev/null &)
  say "  SpaceOps launched (log: $LOGS/spaceops_server.log)"
}
launch_chat(){ # $1 model id
  log_header "start chat llm --model $1" "$CHAT_LOG"
  (cd "$BASE/spaceops_v8" && setsid nohup "$HU_PY" "$BASE/spaceops_v8/chat_llm_service.py" --port 8767 \
      --model "$1" --threads "$CHAT_THREADS" >> "$CHAT_LOG" 2>&1 < /dev/null &)
  say "  chat LLM launched, model $1 (log: $CHAT_LOG)"
}

# Bring the optional chat LLM up (or reuse it) and wait for loaded=true.
# $1 = port state from check_port (0 free, 1 ours, 2 foreign). Returns 0 loaded, 1 degraded.
ensure_chat(){
  local state=$1 model; model=$(chat_model)
  if [ "$state" = 2 ]; then say "  chat LLM NOT started: port 8767 is held by a foreign process (left untouched)."; return 1; fi
  [ -f "$BASE/spaceops_v8/chat_llm_service.py" ] || { say "  chat LLM NOT started: $BASE/spaceops_v8/chat_llm_service.py missing"; return 1; }
  if [ "$state" = 1 ]; then say "  already running (pids $(pids_of "$PAT_CHAT"))"; else launch_chat "$model"; fi
  if WAIT_ALIVE_PAT="$PAT_CHAT" WAIT_FAIL_FN=probe_chat_failed wait_until "$CHAT_TIMEOUT" "chat model loaded" probe_chat; then
    return 0
  fi
  say "  chat LLM DEGRADED: SwarmChat falls back to deterministic replies. See $CHAT_LOG"
  tail -5 "$CHAT_LOG" 2>/dev/null
  return 1
}

# Check one port before starting its service. Returns 0=free 1=ours-running 2=foreign.
check_port(){ # $1 port $2 pattern $3 label
  local ps; ps=$(port_status "$1" "$2")
  case "$ps" in
    free) return 0 ;;
    ours*) return 1 ;;
    *) say "REFUSED: port $1 ($3) is held by a process that is NOT the HU-MA'AT $3: ${ps#foreign }"
       say "         It will not be killed. Free the port yourself, then re-run start."
       return 2 ;;
  esac
}

print_urls(){
  cat <<EOF
URLs
  HU language console   http://localhost:8100
  SpaceOps ground stn   http://localhost:8766
  Drone health page     http://localhost:8766/#dronehealth
  Flight supervisor     http://localhost:8765
  Chat LLM health       http://localhost:8767/health   (optional)
EOF
}

# ======================================================================== subcommands
cmd_status(){
  local gzp world envst j hu ops fresh=0 verdict=READY ps
  printf "HU-MA'AT status  %s\n" "$(ts)"
  gzp=$(pids_of "$PAT_GZ"); world=$(running_world)
  envst=$(cat "$ENV_FILE" 2>/dev/null || echo "none recorded")
  if [ -n "$gzp" ]; then
    printf '  Gazebo      : UP    world=%s (env %s)  pids %s\n' "${world:-?}" "$(env_for_world "${world:-?}")" "$gzp"
  else
    printf '  Gazebo      : DOWN\n'; verdict="NOT READY"
  fi
  printf '  Env file    : %s\n' "$envst"
  # supervisor
  ps=$(port_status 8765 "$PAT_SUP")
  if j=$(get "$URL_SUP"); then
    fresh=$(fleet "$j" fresh)
    printf '  Supervisor  : UP    :8765  telemetry %s/5 fresh  [port %s]\n' "$fresh" "${ps%% *}"
    [ "$fresh" = 5 ] || verdict="NOT READY"
  else
    printf '  Supervisor  : DOWN  :8765  [port %s]\n' "$ps"; verdict="NOT READY"
  fi
  # HU
  ps=$(port_status 8100 "$PAT_HU")
  if hu=$(get "$URL_HU"); then
    if printf '%s' "$hu" | grep -q '"checkpoint_loaded": *true'; then
      printf '  HU bridge   : UP    :8100  model loaded  [port %s]\n' "${ps%% *}"
    else
      printf '  HU bridge   : UP    :8100  model NOT loaded (still loading?)  [port %s]\n' "${ps%% *}"; verdict="NOT READY"
    fi
  else
    printf '  HU bridge   : DOWN  :8100  [port %s]\n' "$ps"; verdict="NOT READY"
  fi
  # SpaceOps
  ps=$(port_status 8766 "$PAT_OPS")
  if ops=$(get "$URL_OPS") && printf '%s' "$ops" | grep -q '"online": *true'; then
    printf '  SpaceOps    : UP    :8766  online, session %s  [port %s]\n' \
      "$(printf '%s' "$ops" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("session_id"))' 2>/dev/null)" "${ps%% *}"
  elif [ -n "${ops:-}" ]; then
    printf '  SpaceOps    : UP    :8766  but NOT online  [port %s]\n' "${ps%% *}"; verdict="NOT READY"
  else
    printf '  SpaceOps    : DOWN  :8766  [port %s]\n' "$ps"; verdict="NOT READY"
  fi
  # Chat LLM (optional: never changes READY/NOT READY, only flags DEGRADED)
  local ch chat_ok=0
  ps=$(port_status 8767 "$PAT_CHAT")
  if ch=$(get "$URL_CHAT"); then
    chatj "$ch" loaded && chat_ok=1
    printf '  Chat LLM    : UP    :8767  %s  [port %s]\n' "$(chatj "$ch" line)" "${ps%% *}"
  else
    printf '  Chat LLM    : DOWN  :8767  (optional; deterministic chat fallback)  config model=%s  [port %s]\n' "$(chat_model)" "$ps"
  fi
  case "$ps" in foreign*) chat_ok=0 ;; esac
  local np=""
  if [ -n "${j:-}" ]; then
    printf 'Aircraft\n'; fleet "$j" table
    np=$(fleet "$j" notparked)
    if [ -n "$np" ]; then printf '  AIRBORNE / ACTIVE: %s\n' "$np"; else printf '  All 5 aircraft parked.\n'; fi
  fi
  print_urls
  local note=""
  [ "$verdict" = READY ] && [ $chat_ok = 0 ] && note="; DEGRADED: chat LLM not loaded - SwarmChat uses deterministic replies"
  if [ "$verdict" = READY ] && [ -n "$np" ]; then
    printf 'VERDICT: READY (all flight services healthy; aircraft are ACTIVE - not parked%s)\n' "$note"
  elif [ -n "$note" ]; then
    printf 'VERDICT: READY (%s)\n' "${note#; }"
  else
    printf 'VERDICT: %s\n' "$verdict"
  fi
  [ "$verdict" = READY ]
}

cmd_preflight(){
  local fail=0 j hu ops t1 t2 v
  pf(){ # $1 PASS|FAIL|WARN  $2 item  $3 detail   (WARN = optional item, never fails preflight)
    printf '  [%s] %-38s %s\n' "$1" "$2" "$3"; [ "$1" = PASS ] || [ "$1" = WARN ] || fail=1
  }
  printf "HU-MA'AT preflight (read-only)  %s\n" "$(ts)"
  if running "$PAT_GZ"; then pf PASS "Gazebo running" "$(running_world)"; else pf FAIL "Gazebo running" "no gz sim process"; fi
  j=$(get "$URL_SUP"); if [ -n "$j" ]; then pf PASS "Supervisor :8765 healthy" "/api/state OK"; else pf FAIL "Supervisor :8765 healthy" "no response"; fi
  hu=$(get "$URL_HU")
  if printf '%s' "$hu" | grep -q '"checkpoint_loaded": *true'; then pf PASS "HU bridge :8100 model loaded" "checkpoint_loaded=true"
  elif [ -n "$hu" ]; then pf FAIL "HU bridge :8100 model loaded" "responding, checkpoint NOT loaded"
  else pf FAIL "HU bridge :8100 model loaded" "no response"; fi
  ops=$(get "$URL_OPS")
  if printf '%s' "$ops" | grep -q '"online": *true'; then pf PASS "SpaceOps :8766 online" "online=true"
  elif [ -n "$ops" ]; then pf FAIL "SpaceOps :8766 online" "responding, online=false"
  else pf FAIL "SpaceOps :8766 online" "no response"; fi
  local p lbl pat r
  for r in "8100 HU $PAT_HU" "8765 supervisor $PAT_SUP" "8766 SpaceOps $PAT_OPS"; do
    p=${r%% *}; lbl=$(printf '%s' "$r" | cut -d' ' -f2); pat=${r#* * }
    v=$(port_status "$p" "$pat")
    case "$v" in ours*) pf PASS "Port $p owned by $lbl" "pid ${v#ours }";; free) pf FAIL "Port $p owned by $lbl" "nothing listening";;
      *) pf FAIL "Port $p owned by $lbl" "FOREIGN: ${v#foreign }";; esac
  done
  if [ -n "$j" ]; then
    v=$(fleet "$j" fresh)
    if [ "$v" = 5 ]; then pf PASS "Telemetry 5/5 fresh" "5/5"; else pf FAIL "Telemetry 5/5 fresh" "$v/5"; fi
    local np; np=$(fleet "$j" notparked)
    if [ -z "$np" ]; then pf PASS "All aircraft parked" "READY/GROUNDED, z<${PARK_Z_MAX} m"; else pf FAIL "All aircraft parked" "$np"; fi
    v=$(fleet "$j" minsep)
    if [ -n "$np" ]; then
      pf FAIL "Min parked separation >= ${MIN_SEP_M} m" "not all parked (current min ${v%% *} m, ${v#* })"
    elif [ "$v" != NA ] && python3 -c "import sys;sys.exit(0 if float('${v%% *}')>=float('$MIN_SEP_M') else 1)"; then
      pf PASS "Min parked separation >= ${MIN_SEP_M} m" "${v%% *} m (${v#* })"
    else pf FAIL "Min parked separation >= ${MIN_SEP_M} m" "$v m"; fi
  else
    pf FAIL "Telemetry 5/5 fresh" "supervisor unavailable"
    pf FAIL "All aircraft parked" "supervisor unavailable"
    pf FAIL "Min parked separation >= ${MIN_SEP_M} m" "supervisor unavailable"
  fi
  local avail; avail=$(df -Pk "$LOGS" 2>/dev/null | awk 'NR==2{print int($4/1024)}')
  if [ -n "$avail" ] && [ "$avail" -ge "$MIN_FREE_MB" ] && [ -w "$LOGS" ]; then pf PASS "Disk space for logs" "${avail} MB free in $LOGS"
  else pf FAIL "Disk space for logs" "${avail:-?} MB free (need ${MIN_FREE_MB}), writable=$([ -w "$LOGS" ] && echo yes || echo no)"; fi
  # SpaceOps recording: logging flag, 5 per-drone CSVs, session dir present, history advancing.
  if [ -n "$ops" ]; then
    t1=$(printf '%s' "$ops" | python3 -c 'import json,sys;d=json.load(sys.stdin);h=d.get("history") or [];print(d.get("logging"),d.get("csv_count"),d.get("session_id"),h[-1]["t"] if h else -1)' 2>/dev/null)
    local deadline=$(( $(date +%s) + 5 )); t2="$t1"
    while [ "$(date +%s)" -lt "$deadline" ]; do
      sleep 1
      t2=$(get "$URL_OPS" | python3 -c 'import json,sys;h=json.load(sys.stdin).get("history") or [];print(h[-1]["t"] if h else -1)' 2>/dev/null)
      [ -n "$t2" ] && [ "$t2" != "${t1##* }" ] && break
    done
    # shellcheck disable=SC2086
    set -- $t1
    if [ "${1:-}" = True ] && [ "${2:-}" = 5 ] && [ -d "$LOGS/spaceops_sessions/${3:-x}" ] && [ -n "$t2" ] && [ "$t2" != "${4:-}" ]; then
      pf PASS "SpaceOps recording" "session ${3}, 5 CSVs, samples advancing"
    else
      pf FAIL "SpaceOps recording" "logging=${1:-?} csv=${2:-?} session=${3:-?} t:${4:-?}->${t2:-?}"
    fi
  else
    pf FAIL "SpaceOps recording" "SpaceOps unavailable"
  fi
  # Optional: chat LLM (WARN only - SpaceOps falls back to deterministic chat).
  local ch cps; ch=$(get "$URL_CHAT"); cps=$(port_status 8767 "$PAT_CHAT")
  case "$cps" in
    foreign*) pf WARN "Chat LLM loaded (optional)" "port 8767 FOREIGN: ${cps#foreign } (deterministic chat fallback)" ;;
    *) if [ -n "$ch" ] && chatj "$ch" loaded; then
         pf PASS "Chat LLM loaded (optional)" "$(chatj "$ch" line | cut -d' ' -f1-3)"
       elif [ -n "$ch" ]; then pf WARN "Chat LLM loaded (optional)" "responding, NOT loaded: $(chatj "$ch" line | cut -d' ' -f1-2) (deterministic chat fallback)"
       else pf WARN "Chat LLM loaded (optional)" "no response on :8767 (deterministic chat fallback)"; fi ;;
  esac
  if [ "$fail" = 0 ]; then printf 'PREFLIGHT: PASS\n'; else printf 'PREFLIGHT: FAIL\n'; fi
  return "$fail"
}

need_files(){ # $1 world
  [ -f "$BASE/worlds/$1" ] || die "world file missing: $BASE/worlds/$1"
}

cmd_start(){
  local envname=festival explicit=0 world rw rc ok=0 gz_fresh=0 j
  while [ $# -gt 0 ]; do
    case "$1" in
      --env) envname=${2:-}; explicit=1; shift 2 || die "--env needs a value" ;;
      --env=*) envname=${1#--env=}; explicit=1; shift ;;
      *) die "unknown option for start: $1" ;;
    esac
  done
  world=$(world_for_env "$envname") || die "unknown environment '$envname' (festival|forest|military|mountains)"
  WRITE_LOG=1; mkdir -p "$LOGS"; banner "start --env $envname" >> "$LAUNCH_LOG"
  say "HU-MA'AT start (env $envname)"
  need_files "$world"
  [ -x "$HU_PY" ] || die "HU python not found: $HU_PY"
  [ -f "$HU_CKPT" ] || die "HU checkpoint not found: $HU_CKPT"
  /usr/bin/python3 -c 'from gz.transport13 import Node; from gz.msgs10.odometry_pb2 import Odometry' 2>/dev/null \
    || die "Gazebo python bindings missing for /usr/bin/python3 (python3-gz-transport13 python3-gz-msgs10)"

  # 1) Port safety first: refuse before touching anything if a foreign process holds a port.
  local hu_state sup_state ops_state chat_state chat_ok=0
  check_port 8765 "$PAT_SUP" supervisor; sup_state=$?
  check_port 8100 "$PAT_HU" "HU bridge"; hu_state=$?
  check_port 8766 "$PAT_OPS" SpaceOps; ops_state=$?
  if [ $sup_state = 2 ] || [ $hu_state = 2 ] || [ $ops_state = 2 ]; then say "start ABORTED: nothing was started."; exit 2; fi
  # Chat LLM is optional: a foreign holder of 8767 is refused (never killed) but does not abort the flight stack.
  check_port 8767 "$PAT_CHAT" "chat LLM"; chat_state=$?
  [ $chat_state = 2 ] && say "         (chat LLM is optional: flight services will still start; result will be DEGRADED)"
  # Our process running but not (yet) listening also counts as running: never start a duplicate.
  running "$PAT_SUP" && sup_state=1; running "$PAT_HU" && hu_state=1; running "$PAT_OPS" && ops_state=1
  [ $chat_state != 2 ] && running "$PAT_CHAT" && chat_state=1

  # 2) Gazebo
  say "[1/5] Gazebo"
  if running "$PAT_GZ"; then
    rw=$(running_world)
    say "  already running: ${rw:-unknown world} (pids $(pids_of "$PAT_GZ"))"
    if [ "$explicit" = 1 ] && [ "$(basename "${rw:-x}")" != "$world" ]; then
      say "  NOTE: requested '$envname' but Gazebo is running $(basename "${rw:-?}"). Leaving it untouched;"
      say "        use 'hu_maat.sh env $envname' (all aircraft parked) to switch."
    fi
    [ -n "$rw" ] && record_env "$(env_for_world "$rw")" "$(basename "$rw")"
  else
    launch_gazebo "$world"; gz_fresh=1
    record_env "$envname" "$world"
  fi
  WAIT_ALIVE_PAT="$PAT_GZ" wait_until "$GZ_TIMEOUT" "5 odometry topics" probe_topics \
    || { say "Gazebo NOT ready; see $LOGS/gazebo_$(env_for_world "$world").log"; tail -5 "$LOGS/gazebo_$(env_for_world "$world").log" 2>/dev/null; exit 1; }

  # 3) Supervisor
  say "[2/5] Flight supervisor :8765"
  if [ $sup_state = 1 ]; then
    say "  already running (pids $(pids_of "$PAT_SUP"))"
    if ! wait_until "$TELEM_TIMEOUT" "5/5 fresh telemetry" probe_telemetry; then
      j=$(get "$URL_SUP")
      if [ "$gz_fresh" = 1 ] || { [ -n "$j" ] && [ -z "$(fleet "$j" notparked)" ]; }; then
        say "  supervisor has stale telemetry and no aircraft is airborne: restarting it"
        stop_pattern supervisor "$PAT_SUP"; launch_supervisor
        WAIT_ALIVE_PAT="$PAT_SUP" wait_until "$TELEM_TIMEOUT" "5/5 fresh telemetry" probe_telemetry || ok=1
      else
        say "  supervisor telemetry not 5/5 and aircraft may be airborne: NOT restarting it automatically."; ok=1
      fi
    fi
  else
    launch_supervisor
    WAIT_ALIVE_PAT="$PAT_SUP" wait_until "$TELEM_TIMEOUT" "5/5 fresh telemetry" probe_telemetry || ok=1
  fi
  [ $ok = 0 ] || { say "Supervisor NOT ready; see $LOGS/hu_festival_supervisor.log"; tail -5 "$LOGS/hu_festival_supervisor.log" 2>/dev/null; }

  # 4) HU bridge
  say "[3/5] HU language-model bridge :8100"
  if [ $hu_state = 1 ]; then say "  already running (pids $(pids_of "$PAT_HU"))"; else launch_hu; fi
  WAIT_ALIVE_PAT="$PAT_HU" wait_until "$HU_TIMEOUT" "checkpoint_loaded" probe_hu \
    || { ok=1; say "HU bridge NOT ready; see $LOGS/hu_live_server.log"; tail -5 "$LOGS/hu_live_server.log" 2>/dev/null; }

  # 5) SpaceOps
  say "[4/5] SpaceOps :8766"
  if [ $ops_state = 1 ]; then say "  already running (pids $(pids_of "$PAT_OPS"))"; else launch_ops; fi
  WAIT_ALIVE_PAT="$PAT_OPS" wait_until "$OPS_TIMEOUT" "SpaceOps online" probe_ops \
    || { ok=1; say "SpaceOps NOT ready; see $LOGS/spaceops_server.log"; tail -5 "$LOGS/spaceops_server.log" 2>/dev/null; }

  # 6) Chat LLM (optional; can never move aircraft)
  say "[5/5] SwarmChat language service (chat LLM, optional) :8767"
  ensure_chat "$chat_state" && chat_ok=1

  echo
  cmd_status; rc=$?
  if [ $ok = 0 ] && [ $rc = 0 ]; then
    if [ $chat_ok = 1 ]; then say "START RESULT: READY"
    else say "START RESULT: DEGRADED (flight services READY; chat LLM unavailable - SwarmChat uses deterministic replies; see $CHAT_LOG)"; fi
    exit 0
  fi
  say "START RESULT: NOT READY (see messages above; logs in $LOGS)"; exit 1
}

# Returns 0 if supervisor reachable and every aircraft parked; prints reason otherwise.
fleet_parked_or_explain(){
  local j np
  j=$(get "$URL_SUP") || { say "  cannot reach the supervisor (:8765): aircraft state UNKNOWN"; return 1; }
  np=$(fleet "$j" notparked)
  [ -z "$np" ] && return 0
  say "  Aircraft not parked: $np"; return 1
}
land_first_msg(){
  say "  Land first: say \"land now\" / \"everyone down\" / \"emergency land\", or from a terminal:"
  say "    $LAND_CURL"
}

cmd_env(){
  local envname=${1:-} world rw
  [ -n "$envname" ] || die "usage: hu_maat.sh env <festival|forest|military|mountains>"
  world=$(world_for_env "$envname") || die "unknown environment '$envname' (festival|forest|military|mountains)"
  WRITE_LOG=1; mkdir -p "$LOGS"; banner "env $envname" >> "$LAUNCH_LOG"
  say "HU-MA'AT environment switch -> $envname ($world)"
  need_files "$world"
  if check_port 8765 "$PAT_SUP" supervisor; [ $? = 2 ]; then exit 2; fi
  if running "$PAT_GZ"; then
    rw=$(running_world)
    if ! fleet_parked_or_explain; then
      say "REFUSED: environment switch is only allowed when every aircraft is parked (READY/GROUNDED)."
      land_first_msg; exit 3
    fi
    if [ "$(basename "${rw:-x}")" = "$world" ]; then
      say "  Gazebo is already running $world; nothing to switch."
      record_env "$envname" "$world"
      wait_until "$TELEM_TIMEOUT" "5/5 fresh telemetry" probe_telemetry && exit 0 || exit 1
    fi
    # Re-check immediately before acting to keep the race window minimal.
    fleet_parked_or_explain || { say "REFUSED: aircraft state changed."; land_first_msg; exit 3; }
    say "  stopping supervisor (no commands accepted during the swap)"
    stop_pattern supervisor "$PAT_SUP" || die "supervisor did not stop"
    say "  stopping Gazebo ($(basename "${rw:-?}"))"
    stop_pattern Gazebo "$PAT_GZ" || die "Gazebo did not stop; NOT restarting. Run status."
  else
    say "  Gazebo not running; starting it with $world"
    stop_pattern supervisor "$PAT_SUP"
  fi
  launch_gazebo "$world"; record_env "$envname" "$world"
  WAIT_ALIVE_PAT="$PAT_GZ" wait_until "$GZ_TIMEOUT" "5 odometry topics" probe_topics \
    || { say "Gazebo NOT ready; see $LOGS/gazebo_$envname.log"; exit 1; }
  launch_supervisor
  WAIT_ALIVE_PAT="$PAT_SUP" wait_until "$TELEM_TIMEOUT" "5/5 fresh telemetry" probe_telemetry \
    || { say "Supervisor NOT ready; see $LOGS/hu_festival_supervisor.log"; exit 1; }
  probe_hu  && say "  HU bridge still up, model loaded"  || say "  WARNING: HU bridge not healthy (run status)"
  probe_ops && say "  SpaceOps still up, online"          || say "  WARNING: SpaceOps not online (run status)"
  probe_chat && say "  chat LLM still up, model loaded"   || say "  NOTE: chat LLM not loaded (optional; deterministic chat fallback)"
  say "ENV RESULT: READY in $envname"
}

# Change the chat LLM model and restart ONLY the chat service.
# Never touches Gazebo, the supervisor, HU or SpaceOps (the chat service cannot move aircraft).
cmd_chat_model(){
  local model=${1:-} st
  [ $# = 1 ] && [ -n "$model" ] || die "usage: hu_maat.sh chat-model <model-id>   (e.g. HuggingFaceTB/SmolLM2-360M-Instruct)"
  valid_model_id "$model" || die "invalid model id '$model' (allowed: letters, digits, . _ - /)"
  WRITE_LOG=1; mkdir -p "$LOGS" "$(dirname "$CHAT_MODEL_FILE")"; banner "chat-model $model" >> "$LAUNCH_LOG"
  say "HU-MA'AT chat LLM model -> $model"
  [ -f "$BASE/spaceops_v8/chat_llm_service.py" ] || die "chat service missing: $BASE/spaceops_v8/chat_llm_service.py"
  [ -x "$HU_PY" ] || die "HU python not found: $HU_PY"
  check_port 8767 "$PAT_CHAT" "chat LLM"; st=$?
  [ $st = 2 ] && { say "chat-model ABORTED: config not changed, nothing restarted."; exit 2; }
  printf '%s\n' "$model" > "$CHAT_MODEL_FILE.tmp" && mv -f "$CHAT_MODEL_FILE.tmp" "$CHAT_MODEL_FILE" \
    || die "cannot write $CHAT_MODEL_FILE"
  say "  wrote $CHAT_MODEL_FILE"
  if [ -n "${CHAT_LLM_MODEL:-}" ] && [ "$CHAT_LLM_MODEL" != "$model" ]; then
    say "  NOTE: CHAT_LLM_MODEL=$CHAT_LLM_MODEL is set in this shell and overrides the config file on later starts;"
    say "        this restart uses $model. Unset it (unset CHAT_LLM_MODEL) to make the file authoritative."
  fi
  stop_pattern "chat LLM" "$PAT_CHAT" || die "chat LLM did not stop; not relaunching (run status)"
  if CHAT_LLM_MODEL="$model" ensure_chat 0; then
    say "CHAT-MODEL RESULT: READY ($(chatj "$(get "$URL_CHAT")" line))"; exit 0
  fi
  say "CHAT-MODEL RESULT: DEGRADED (flight services untouched; SwarmChat uses deterministic replies)"; exit 1
}

cmd_stop(){
  local force=0
  case "$#:${1:-}" in
    0:) ;;
    1:--force) force=1 ;;
    *) die "usage: hu_maat.sh stop [--force]" ;;
  esac
  WRITE_LOG=1; mkdir -p "$LOGS"; banner "stop$([ $force = 1 ] && echo ' --force')" >> "$LAUNCH_LOG"
  say "HU-MA'AT shutdown"
  if running "$PAT_GZ" || running "$PAT_SUP"; then
    if ! fleet_parked_or_explain; then
      if [ $force = 1 ]; then say "  --force given: stopping anyway."
      else say "REFUSED: aircraft may be airborne. Land everything first, then run stop again (or stop --force)."
           land_first_msg; exit 3; fi
    fi
  fi
  local rc=0
  stop_pattern "chat LLM"   "$PAT_CHAT" || rc=1  # optional wording service first
  stop_pattern "HU bridge"  "$PAT_HU"  || rc=1   # no new language commands
  stop_pattern supervisor   "$PAT_SUP" || rc=1
  stop_pattern Gazebo       "$PAT_GZ"  || rc=1
  stop_pattern SpaceOps     "$PAT_OPS" || rc=1   # last, so it records the shutdown
  say "  Logs preserved in $LOGS"
  [ $rc = 0 ] && say "STOP RESULT: all HU-MA'AT processes stopped" || say "STOP RESULT: some processes did not stop (run status)"
  exit $rc
}

usage(){
  cat <<EOF
Usage: $(basename "$0") <command>
  start [--env festival|forest|military|mountains]   bring everything up, wait for health, READY/NOT READY
  env <festival|forest|military|mountains>           switch world (only when all aircraft are parked)
  stop [--force]                                      graceful shutdown (refuses if airborne unless --force)
  status                                              one-screen readiness report (read-only)
  preflight                                           read-only PASS/FAIL checklist
  chat-model <model-id>                               set chat LLM model, restart ONLY the chat service
EOF
}

case "${1:-}" in
  start)     shift; cmd_start "$@" ;;
  env)       shift; cmd_env "$@" ;;
  stop)      shift; cmd_stop "$@" ;;
  chat-model) shift; cmd_chat_model "$@" ;;
  status)    cmd_status ;;
  preflight) cmd_preflight ;;
  -h|--help|help|"") usage ;;
  *) usage; exit 2 ;;
esac
