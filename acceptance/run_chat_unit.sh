#!/bin/bash
# Unit tests for SwarmChat, the grounding validator and the SpaceOps panels.
cd "$(dirname "$0")/tests" || exit 1
rc=0
for t in test_chat_grounding test_swarmchat test_spaceops_panels test_strategist test_light_rules; do
  echo "== $t"; PYTHONPATH="$(cd ../../spaceops_v8 && pwd)" python3 -m unittest -q "$t" 2>&1 | tail -3; [ "${PIPESTATUS[0]}" = 0 ] || rc=1
done
exit $rc
