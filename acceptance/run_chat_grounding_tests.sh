#!/usr/bin/env bash
# Offline: runs the grounding-validator unit tests with system python3 (no torch). Touches no ports.
A="$(cd "$(dirname "$0")" && pwd)"
cd "$A/tests" || exit 1
/usr/bin/python3 -m unittest -v test_chat_grounding 2>&1 | tail -${1:-80}
