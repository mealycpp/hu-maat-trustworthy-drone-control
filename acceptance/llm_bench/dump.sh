#!/bin/bash
# Usage: bash dump.sh <model> [mode] [tag]  -- prints replies for review
cd ~/hu-telepresence-gazebo/acceptance/llm_bench
A="--model $1"; [ -n "$2" ] && [ "$2" != "-" ] && A="$A --mode $2"; [ -n "$3" ] && A="$A --tag $3"
~/hu-telepresence-gazebo/hu-venv/bin/python -I bench_smollm.py dump $A
