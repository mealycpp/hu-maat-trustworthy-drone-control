#!/usr/bin/env bash
# Kept for compatibility: environment switching now lives in the master launcher.
# Usage: switch_world.sh festival|forest|military|mountains
exec bash ~/hu-telepresence-gazebo/hu_maat.sh env "$@"
