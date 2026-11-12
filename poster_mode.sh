#!/usr/bin/env bash
# RSCL — Reconfigurable Space Computing Lab · HU–MA'AT poster/demo autopilot.
# One command per situation, so nothing has to be typed at the booth.
#
#   bash ~/hu-telepresence-gazebo/poster_mode.sh start     # everything up + automatic show + open pages in Edge
#   bash ~/hu-telepresence-gazebo/poster_mode.sh visitor   # pause the show, land: visitors give their own commands
#   bash ~/hu-telepresence-gazebo/poster_mode.sh show      # resume the automatic agent show
#   bash ~/hu-telepresence-gazebo/poster_mode.sh stop      # land and shut the show and lights down
#   bash ~/hu-telepresence-gazebo/poster_mode.sh status    # one-screen health check
#   bash ~/hu-telepresence-gazebo/poster_mode.sh pages     # (re)open HU console + SpaceOps in Edge
#
# Safety: every flight command still goes through HU and MA'AT; this script only sequences them.
# Emergency at any time: say or type "stop" / "khalas" / "land now" in the HU console.
BASE="$HOME/hu-telepresence-gazebo"; cd "$BASE" || exit 1
ENV_NAME=${ENV_NAME:-military}
PROBE="timeout 60 $BASE/hu-venv/bin/python $BASE/acceptance/hu_band_probe.py"
SHOW_ARGS="SOUND=1 LEVELS=1 PROFILES=1 LANDLOOP=1 STRATEGY=1 ROUND=8 LOOPS=999"

say(){ printf '\033[1;33m[RSCL]\033[0m %s\n' "$*"; }
modes(){ curl -s -m 3 http://127.0.0.1:8765/api/state 2>/dev/null | python3 -c "import json,sys;print(','.join(sorted(set(x['mode'] for x in json.load(sys.stdin)['drones']))))" 2>/dev/null; }
parked(){ [[ "$(modes)" =~ ^(GROUNDED|READY|GROUNDED,READY|READY,GROUNDED)$ ]]; }
show_running(){ pgrep -f "bash acceptance/formation_show.sh" >/dev/null; }

stop_show(){ if show_running; then pkill -f "bash acceptance/formation_show.sh"; say "show paused"; sleep 6; fi; }
land(){ parked && return 0; say "landing all drones"; $PROBE --confirm "Everyone, land" >/dev/null 2>&1
  for i in $(seq 1 60); do parked && break; sleep 1; done; say "drones: $(modes)"; }
spread_ok(){ # parked drones must be >= 1.0 m apart for a safe take-off; otherwise put them back on their pads
  curl -s -m 3 http://127.0.0.1:8765/api/state | python3 -c "
import json,sys,math,itertools;d=json.load(sys.stdin)['drones']
sys.exit(0 if min(math.dist((a['x'],a['y']),(b['x'],b['y'])) for a,b in itertools.combinations(d,2))>=1.0 else 1)"; }
reset_pads(){ say "drones parked too close together: resetting them to their pads (simulation only)"
  gz service -s /world/hu_festival/control --reqtype gz.msgs.WorldControl --reptype gz.msgs.Boolean --timeout 5000 --req "reset: {all: true}" >/dev/null
  sleep 6; gz service -s /world/hu_festival/control --reqtype gz.msgs.WorldControl --reptype gz.msgs.Boolean --timeout 5000 --req "pause: false" >/dev/null; sleep 4; }
pages(){ if command -v cmd.exe >/dev/null; then
    cmd.exe /c start "" msedge --new-window http://localhost:8100 >/dev/null 2>&1
    sleep 1; cmd.exe /c start "" msedge --new-window http://localhost:8766 >/dev/null 2>&1
    say "opened HU console (8100) and SpaceOps (8766) in Edge — in SpaceOps click 🔊 DRONE VOICES"
  else say "open http://localhost:8100 and http://localhost:8766 in your browser"; fi; }
start_show(){ show_running && { say "show already running"; return; }
  parked && ! spread_ok && reset_pads
  env $SHOW_ARGS setsid nohup bash acceptance/formation_show.sh > /tmp/show.log 2>&1 < /dev/null &
  say "automatic show started: the drones choose every formation in team strategy rounds"; }

case "${1:-status}" in
  start)
    if ! pgrep -f '^gz sim' >/dev/null; then
      # Gazebo was closed (or crashed): the other services still hold the OLD fleet state. Restart everything
      # cleanly so MA'AT, the agents and the show all start from the drones' real positions on their pads.
      say "Gazebo is not running: clean restart of all services (2–3 minutes)"
      stop_show; pkill -f acceptance/light_show.py
      bash hu_maat.sh stop 2>&1 | tail -1
      # copies started by hand (e.g. during development) are not recognised by the launcher's safety check
      # and would block the restart; they are ours, so stop them too
      for pat in 'festival/control_server_hu.py' 'python3 control_server_hu.py' 'spaceops_v8/spaceops_server.py' 'python3 spaceops_server.py' 'hu_live_server.py' 'chat_llm_service.py'; do
        pkill -f "$pat" 2>/dev/null
      done; sleep 3
    fi
    say "starting HU–MA'AT ($ENV_NAME world). This takes 1–3 minutes the first time."
    # write to a file, not a pipe: launcher helper processes may keep a pipe open and hang this script
    bash hu_maat.sh start --env "$ENV_NAME" > /tmp/rscl_start.log 2>&1 < /dev/null
    grep -E "START RESULT|REFUSED|NOT ready|failed" /tmp/rscl_start.log | tail -3
    bash hu_maat.sh status 2>&1 | grep -q "VERDICT: READY" || { say "NOT READY — run: bash hu_maat.sh status"; exit 1; }
    pgrep -f acceptance/light_show.py >/dev/null || { setsid nohup python3 acceptance/light_show.py > /tmp/lights.log 2>&1 < /dev/null & say "LED body language on"; }
    start_show; sleep 2; pages
    say "READY. Visitors: 'bash poster_mode.sh visitor'. Back to the show: 'bash poster_mode.sh show'." ;;
  visitor)
    stop_show; land
    say "VISITOR MODE: the drones are on the ground. Visitors type or speak in the HU console (http://localhost:8100)."
    say "Try: 'Everyone, take off'  ·  'All form a circle'  ·  'Everyone except Anubis, hover'  ·  'This project is taking off'  ·  'khalas'" ;;
  show)
    start_show ;;
  stop)
    stop_show; land
    pkill -f acceptance/light_show.py && say "lights off"
    say "stopped. Gazebo and the services stay up; full shutdown: bash hu_maat.sh stop" ;;
  pages) pages ;;
  status)
    bash hu_maat.sh status 2>&1 | grep -E "Supervisor|HU bridge|SpaceOps|Chat LLM  |VERDICT"
    echo "  Drones      : $(modes)"
    echo "  Show        : $(show_running && echo running || echo stopped)$(show_running && echo " — last: $(grep -E 'formed' /tmp/show.log | tail -1)")"
    echo "  LED lights  : $(pgrep -f acceptance/light_show.py >/dev/null && echo on || echo off)" ;;
  *) sed -n '2,13p' "$0" ;;
esac
