#!/bin/bash
# Ablations run after run_all.sh: repetition penalty on generated tokens only (s1, all cases),
# plus an exploratory dynamic-int8 1.7B run on the 12-case dev subset.
cd ~/hu-telepresence-gazebo/acceptance/llm_bench
DEV=F1_wobbly,F2_favorite_formation,F3_nervous_presenting,F4_pattern_matching,F5_besties,F6_backflip,F7_another_joke,F8_best_drone,S1_stale_telemetry,S2_fault,S4_take_off_now,M1_what_about_ra
echo "=== 360M rpgen $(date)" >> run_ablation.log
bash run_model.sh 360M --rp-gen-only --modes s1 --tag rpgen >> run_ablation.log 2>&1
echo "=== 1.7B rpgen $(date)" >> run_ablation.log
bash run_model.sh 1.7B --rp-gen-only --modes s1 --tag rpgen >> run_ablation.log 2>&1
echo "=== 1.7B rpgen_int8 $(date)" >> run_ablation.log
bash run_model.sh 1.7B --rp-gen-only --int8 --modes s1 --tag rpgen_int8 --cases $DEV >> run_ablation.log 2>&1
echo "=== ABLDONE $(date)" >> run_ablation.log
