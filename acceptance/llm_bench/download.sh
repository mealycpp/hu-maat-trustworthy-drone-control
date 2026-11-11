#!/bin/bash
# One-time download of the two SmolLM2 instruct models (approved by user). Only huggingface.co.
cd ~/hu-telepresence-gazebo/acceptance/llm_bench
~/hu-telepresence-gazebo/hu-venv/bin/python - <<'PY' > download.log 2>&1
import time
from huggingface_hub import snapshot_download
for r in ["HuggingFaceTB/SmolLM2-360M-Instruct", "HuggingFaceTB/SmolLM2-1.7B-Instruct"]:
    t = time.time()
    p = snapshot_download(r, allow_patterns=["*.json", "*.safetensors", "*.txt", "*.jinja"])
    print(r, p, round(time.time() - t, 1), "s", flush=True)
PY
du -sbL ~/.cache/huggingface/hub/models--HuggingFaceTB--SmolLM2-360M-Instruct ~/.cache/huggingface/hub/models--HuggingFaceTB--SmolLM2-1.7B-Instruct ~/.cache/huggingface/hub/models--HuggingFaceTB--SmolLM2-135M-Instruct >> download.log
echo DONE >> download.log
