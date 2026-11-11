#!/usr/bin/env bash
# Validate every generated environment world with Gazebo's SDF checker.
rc=0
for f in ~/hu-telepresence-gazebo/worlds/hu_env_*.sdf; do
  if out=$(gz sdf -k "$f" 2>&1); then echo "valid:   $(basename "$f")"; else echo "INVALID: $(basename "$f")"; echo "$out" | head -8; rc=1; fi
done
exit $rc
