#!/usr/bin/env bash
# Read-only exercise of hu_maat.sh: lint, then status and preflight (GETs only).
S=~/hu-telepresence-gazebo/hu_maat.sh
bash -n "$S" && echo "bash -n OK"
shellcheck -S warning "$S" && echo "shellcheck (warning+) clean"
ls -la ~/hu-telepresence-gazebo/logs/current_env 2>&1
echo "================ status"; bash "$S" status; echo "exit=$?"
echo "================ preflight"; bash "$S" preflight; echo "exit=$?"
echo "== launcher log untouched by read-only commands?"; ls -la ~/hu-telepresence-gazebo/logs/hu_maat.log 2>&1
