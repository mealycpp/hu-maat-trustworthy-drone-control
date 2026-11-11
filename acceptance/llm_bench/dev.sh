#!/bin/bash
# Prompt-iteration helper: bash dev.sh <model> <tag> <template-path|default> [modes]
cd ~/hu-telepresence-gazebo/acceptance/llm_bench
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 OMP_NUM_THREADS=6
DEV=F1_wobbly,F2_favorite_formation,F3_nervous_presenting,F4_pattern_matching,F5_besties,F6_backflip,F7_another_joke,F8_best_drone,S1_stale_telemetry,S2_fault,S4_take_off_now,M1_what_about_ra
T=""; [ "$3" != "default" ] && T="--template $3"
~/hu-telepresence-gazebo/hu-venv/bin/python -I bench_smollm.py run --model "$1" --cases $DEV --modes "${4:-greedy}" --tag "$2" $T > "dev_$1_$2.log" 2>&1
echo "EXIT $?"; grep -E "^META|Error|Traceback" "dev_$1_$2.log" | cut -c1-300
~/hu-telepresence-gazebo/hu-venv/bin/python -I bench_smollm.py dump --model "$1" --tag "$2"
