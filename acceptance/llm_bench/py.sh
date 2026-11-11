#!/bin/bash
# Usage: bash py.sh <bench subcommand and args>   (offline, no model load for summarize/recheck/dump)
cd ~/hu-telepresence-gazebo/acceptance/llm_bench
HF_HUB_OFFLINE=1 ~/hu-telepresence-gazebo/hu-venv/bin/python -I bench_smollm.py "$@"
