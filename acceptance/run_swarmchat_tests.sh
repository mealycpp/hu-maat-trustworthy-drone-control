#!/usr/bin/env bash
# Offline only: copies spaceops_v8 sources into a temp dir; nothing contacts 8100/8765/8766.
A="$(cd "$(dirname "$0")" && pwd)"
SRC="$A/../spaceops_v8"
T=$(mktemp -d)
cp "$SRC"/*.py "$SRC"/index.html "$SRC"/rscl_logo.png "$SRC"/orbital_backdrop.svg "$T"/
cp "$A"/tests/test_swarmchat.py "$T"/
cp /home/hadedy/hu_maat_v9_1/spaceops_v8/test_v8.py /home/hadedy/hu_maat_v9_1/spaceops_v8/test_v9_ops.py "$T"/
cd "$T" || exit 1
echo "== test_swarmchat =="; /usr/bin/python3 -m unittest -v test_swarmchat 2>&1 | tail -40
echo "== test_v8 =="; /usr/bin/python3 test_v8.py 2>&1 | tail -15
echo "== test_v9_ops =="; /usr/bin/python3 -m unittest -v test_v9_ops 2>&1 | tail -15
if [ "$1" = "transcript" ]; then echo "== transcript =="; /usr/bin/python3 "$A"/swarmchat_transcript.py; fi
rm -rf "$T"
