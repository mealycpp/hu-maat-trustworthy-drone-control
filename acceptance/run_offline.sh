#!/usr/bin/env bash
# Offline + HTTP regression tests (no Gazebo, nothing flies) on copies of the live sources.
A="$(cd "$(dirname "$0")" && pwd)"
T=$(mktemp -d)
cp "$A"/../festival/*.py "$A"/../festival/*.html "$A"/../festival/*.js "$T"/
cp "$A"/tests/test_*.py "$T"/
cd "$T" && /usr/bin/python3 -m unittest test_v9 test_missions test_http_v9 test_mission_coordinator test_emergency_phrases test_compound_commands test_formation_catalog_offline test_exclusions test_guard_targets test_emergency_latch 2>&1 | tail -${1:-25}
