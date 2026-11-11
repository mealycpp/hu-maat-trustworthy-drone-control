#!/bin/bash
# Usage: bash run_model.sh <135M|360M|1.7B> [extra bench args...]
# Runs fully offline (HF_HUB_OFFLINE=1) with the compute cap from the bench (threads default 6).
cd ~/hu-telepresence-gazebo/acceptance/llm_bench
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1
M=$1; shift
THREADS=6
for ((i=1;i<=$#;i++)); do if [ "${!i}" = "--threads" ]; then j=$((i+1)); THREADS=${!j}; fi; done
export OMP_NUM_THREADS=$THREADS MKL_NUM_THREADS=$THREADS
LOG="log_${M}_$(echo "$@" | grep -oE -- "--tag [^ ]+" | cut -d" " -f2)_$(date +%H%M%S).txt"
~/hu-telepresence-gazebo/hu-venv/bin/python -I bench_smollm.py run --model "$M" "$@" >> "$LOG" 2>&1
echo "EXIT $?" >> "$LOG"
tail -3 "$LOG"
