#!/usr/bin/env bash
# Read-only checks of hu_maat.sh: syntax, shellcheck, CRLF, pattern matching. No start/stop.
S=~/hu-telepresence-gazebo/hu_maat.sh
chmod +x "$S"
echo "== CRLF check"; if grep -q $'\r' "$S"; then echo "CRLF FOUND"; else echo "LF only"; fi
echo "== bash -n"; bash -n "$S" && echo "bash -n OK"
echo "== shellcheck"; shellcheck -S warning "$S" && echo "shellcheck (warning+) clean"
echo "== pattern match vs live processes"
for p in '^gz sim( |$)' '^/usr/bin/python3 ([^ ]*/)?festival/control_server_hu\.py( |$)' \
         '^[^ ]*hu-venv/bin/python3? [^ ]*festival/hu_live_server\.py( |$)' '^[^ ]*python3? [^ ]*spaceops_v8/spaceops_server\.py( |$)'; do
  echo "-- $p"; pgrep -af "$p"
done
echo "== usage"; bash "$S" help
