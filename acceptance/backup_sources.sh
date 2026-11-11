#!/usr/bin/env bash
# Timestamped copy of the HU-MA'AT source files before an edit. Usage: backup_sources.sh <label>
cd ~/hu-telepresence-gazebo
B="backups/${1:-manual}_$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$B"
cp spaceops_v8/spaceops_server.py spaceops_v8/index.html spaceops_v8/swarmchat.py \
   festival/control_server_hu.py festival/hu_live_server.py festival/mission_intents.py \
   festival/formation_planner.py festival/hu_live_dashboard.html "$B"/
echo "backed up to $B"; ls "$B"
