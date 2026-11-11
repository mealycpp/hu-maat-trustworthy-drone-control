#!/usr/bin/env bash
# Formation show for the live Gazebo demo. Every step goes through the HU bridge exactly as an
# operator command would (EXECUTE band, or CONFIRM band confirmed by this script) and MA'AT re-checks it.
#
# The shape order was chosen offline so every transition keeps >= 1.23 m planned clearance
# (line -> diamond, for example, plans only 0.96 m and can trip the 0.82 m proximity watchdog).
# If MA'AT refuses a step (other than "not settled yet", which is retried), the show stops and
# the drones keep hovering safely.
#
#   bash acceptance/formation_show.sh                       # one pass, drones must be flying
#   LOOPS=999 SOUND=1 LEVELS=1 LANDLOOP=1 bash acceptance/formation_show.sh
#
# Options
#   LOOPS=n     repeat n times                       SETTLE=m  arrival tolerance (default 0.12 m)
#   SOUND=1     chime per shape, fanfare per loop    (Windows/WSL; ~/Music/hu_chime.wav, hu_victory.wav)
#   UPDOWN=1    whole swarm climbs 60 cm after each shape
#   LEVELS=1    each drone climbs a different amount (staircase, rotating) after each shape
#   LANDLOOP=1  land at the end of each loop, then take off again
# Formations are flown at 1.9 m, so the next shape always brings the drones back down while forming.
cd "$(dirname "$0")/.." || exit 1
SETTLE=${SETTLE:-0.12}; LOOPS=${LOOPS:-1}; SOUND=${SOUND:-0}
SHAPES=("a circle" "a zigzag" "a square" "a diamond" "a cross" "an X" "a triangle" "a circle" "a V" "a wedge" "a circle")
# SHAPES20=1: 20 distinct shapes, every transition >= 1.16 m planned clearance (computed offline)
[ "${SHAPES20:-0}" = 1 ] && SHAPES=("a circle" "a zigzag" "a square" "a staggered line" "an X" "a diamond" "a cross"
  "an arrow" "a T shape" "a fan" "an arc" "an inverted V" "a triangle" "a grid" "two rows" "a right echelon"
  "a perimeter" "a V" "a wedge" "an L shape" "a circle")
NAMES=(Horus Ra Thoth Anubis Osiris)
STEPS=(0 20 40 60 80)   # centimetres; 1.9 m + 0.8 m = 2.7 m, below the 2.8 m ceiling
PROBE="timeout 60 hu-venv/bin/python acceptance/hu_band_probe.py --confirm"
STATE=http://127.0.0.1:8765/api/state

play(){ [ "$SOUND" = 1 ] || return 0
  ( powershell.exe -NoProfile -c "(New-Object Media.SoundPlayer \"\$env:USERPROFILE\\Music\\$1\").PlaySync()" >/dev/null 2>&1 & ) ; }
speak(){ [ "${SPEAK:-0}" = 1 ] || return 0   # Windows built-in voice (System.Speech)
  ( powershell.exe -NoProfile -c "Add-Type -AssemblyName System.Speech; \$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; \$s.Rate=1; \$s.Speak('$1')" >/dev/null 2>&1 & ) ; }
err(){ curl -s -m 2 $STATE | python3 -c "
import json,sys,math;d=json.load(sys.stdin)['drones']
print('%.3f'%max(math.dist((x['x'],x['y'],x['z']),tuple(x['target'])) for x in d))"; }
modes(){ curl -s -m 2 $STATE | python3 -c "
import json,sys;print(','.join(sorted(set(x['mode'] for x in json.load(sys.stdin)['drones']))))"; }
settle(){ sleep 0.6; for i in $(seq 1 100); do e=$(err); python3 -c "import sys;sys.exit(0 if $e<$SETTLE else 1)" && return 0; sleep 0.3; done; }
# send a command; retry while MA'AT says a drone has not settled yet; stop the show on any other refusal
cmd(){ local r
  for try in 1 2 3 4 5 6 7 8; do r=$($PROBE "$1" | head -1); case "$r" in *"not settled"*|*"are moving"*|*"stable HOVER"*|*"No fresh odometry"*) sleep 1;; *) break;; esac; done
  case "$r" in *rejected*|*error*|REFUSE*) echo "STOP at '$1': ${r##*confirmed }"; exit 1;; esac; }
wait_modes(){ # $1 = regex the mode set must match
  for i in $(seq 1 80); do [[ "$(modes)" =~ $1 ]] && return 0; sleep 0.5; done; echo "timeout waiting for $1"; exit 1; }

[[ "$(modes)" =~ ^(GROUNDED|READY|GROUNDED,READY)$ ]] && { echo "taking off"; cmd "Everyone, take off"; wait_modes '^HOVER$'; }
k=0
for loop in $(seq 1 "$LOOPS"); do
  # STRATEGY=1: the drones choose each next shape themselves (acceptance/strategist.py), ROUND shapes per loop
  if [ "${STRATEGY:-0}" = 1 ]; then SHAPES=(); for r in $(seq 1 "${ROUND:-12}"); do SHAPES+=("@strategy"); done; fi
  for shape in "${SHAPES[@]}"; do
    if [ "$shape" = "@strategy" ]; then shape=$(python3 acceptance/strategist.py); [ -z "$shape" ] && { sleep 2; continue; }; fi
    t0=$(date +%s.%N)
    cmd "All form $shape"; settle
    printf '%-12s formed in %4.1f s\n' "${shape#a }" "$(python3 -c "import time;print(time.time()-$t0)")"
    play hu_goal.wav; n=${shape#a }; n=${n#an }; speak "$(shuf -n1 -e Goal Yes Nailed\ it Beautiful)! ${n}!"
    if [ "${LEVELS:-0}" = 1 ]; then
      # random height per drone: 0-80 cm in 10 cm steps (formations keep >= 0.88 m horizontal
      # spacing, so purely vertical moves cannot close the gap); RANDOM_LEVELS=0 -> rotating staircase
      # MA'AT allows a single-drone move only from HOVER and only while the rest of the fleet is
      # steady, so: whole swarm to hover, then each drone pops up to its height one after another.
      lv=""
      cmd "Everyone, hover in place"; wait_modes '^HOVER$'
      for j in 0 1 2 3 4; do
        # random level between -80 and +80 cm around the 1.9 m formation altitude (1.1-2.7 m;
        # limits 0.65-2.8 m); |h| < 10 cm is skipped (the minimum move is 10 cm)
        if [ "${RANDOM_LEVELS:-1}" = 1 ]; then h=$(( (RANDOM % 17 - 8) * 10 )); else h=${STEPS[$(( (j + k) % 5 ))]}; fi
        lv="$lv ${NAMES[$j]}$( [ $h -ge 0 ] && echo + )${h}"
        [ "$h" = 0 ] && continue
        if [ $h -gt 0 ]; then cmd "${NAMES[$j]}, go up $h centimeters"; else cmd "${NAMES[$j]}, go down $(( -h )) centimeters"; fi
        wait_modes '^HOVER$'
      done
      settle; echo "             levels (cm):$lv"; k=$((k + 1))
    elif [ "${UPDOWN:-0}" = 1 ]; then
      cmd "Everyone, go up 60 centimeters"; settle; echo "             climbed 60 cm"
    fi
  done
  echo "--- loop $loop complete ---"
  if [ "${LANDLOOP:-0}" = 1 ]; then
    cmd "Everyone, land"; wait_modes '^(GROUNDED|READY|GROUNDED,READY)$'; echo "             landed"
    play hu_victory.wav; sleep 3
    [ "$loop" -lt "$LOOPS" ] && { cmd "Everyone, take off"; wait_modes '^HOVER$'; echo "             took off"; }
  else
    play hu_victory.wav; sleep 2.5
  fi
done
echo "Show finished."
