#!/usr/bin/env bash
# Show the running world, env-state file history and recent process starts.
ps -eo pid,lstart,args | grep -E '[g]z sim -r|[c]ontrol_server_hu|[h]u_live_server|[s]paceops_server'
echo "-- current_env --"; cat ~/hu-telepresence-gazebo/logs/current_env
echo "-- env_history --"; cat ~/hu-telepresence-gazebo/logs/env_history.log 2>/dev/null
echo "-- hu_maat.log --"; tail -20 ~/hu-telepresence-gazebo/logs/hu_maat.log 2>/dev/null
echo "-- bash history mentions --"; grep -h -E 'switch_world|hu_maat.sh (start|env|stop)' ~/.bash_history 2>/dev/null | tail -5
ls -la --time-style=+%H:%M:%S ~/hu-telepresence-gazebo/acceptance/ | tail -25
