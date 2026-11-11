#!/usr/bin/env bash
# Live exercise of hu_maat.sh: env switch there and back, full stop, full start.
L=~/hu-telepresence-gazebo/hu_maat.sh
step(){ echo; echo "################ $*"; }
step "env mountains";        bash $L env mountains;  echo "exit=$?"
step "status";               bash $L status | grep -E 'Gazebo|VERDICT'
step "env military";         bash $L env military;   echo "exit=$?"
step "stop";                 bash $L stop;           echo "exit=$?"
step "processes after stop"; pgrep -af '^gz sim|control_server_hu|hu_live_server|spaceops_server' || echo "(none running)"
step "start --env military"; bash $L start --env military; echo "exit=$?"
step "gui process";          pgrep -af '^gz sim gui' || echo "NO GUI PROCESS"
step "preflight";            bash $L preflight | tail -15; echo "exit=$?"
