#!/bin/bash
# Full benchmark, one model at a time (each run is its own process, so memory is freed between models).
cd ~/hu-telepresence-gazebo/acceptance/llm_bench
for M in 135M 360M 1.7B; do
  echo "=== $M $(date)" >> run_all.log
  bash run_model.sh "$M" >> run_all.log 2>&1
done
# 1.7B at 10 threads, once, for latency comparison (greedy, 12-case subset)
echo "=== 1.7B t10 $(date)" >> run_all.log
bash run_model.sh 1.7B --threads 10 --modes greedy --tag t10 \
  --cases F1_wobbly,F2_favorite_formation,F3_nervous_presenting,F4_pattern_matching,F5_besties,F6_backflip,F7_another_joke,F8_best_drone,S1_stale_telemetry,S2_fault,S4_take_off_now,M1_what_about_ra >> run_all.log 2>&1
echo "=== ALLDONE $(date)" >> run_all.log
