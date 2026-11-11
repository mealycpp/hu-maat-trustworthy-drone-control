#!/usr/bin/env bash
# Which SmolLM2 weights are cached locally, and can the HU venv use a GPU?
echo "-- HuggingFace cache --"
ls -d ~/.cache/huggingface/hub/models--HuggingFaceTB--* 2>/dev/null || echo "(no HuggingFaceTB models cached)"
du -sh ~/.cache/huggingface/hub/models--HuggingFaceTB--* 2>/dev/null
echo "-- torch / transformers / device --"
~/hu-telepresence-gazebo/hu-venv/bin/python - <<'PY'
import torch, transformers
print('torch', torch.__version__, '| transformers', transformers.__version__)
print('cuda available:', torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')
import os; print('cpu threads:', os.cpu_count())
PY
free -g | head -2
