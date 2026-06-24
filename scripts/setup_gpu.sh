#!/usr/bin/env bash
# Sets GPU to efficient mode before running ComfyUI.
# Run once before ./app.sh start to prevent overheating.
set -euo pipefail

# Detect GPU and set appropriate power limit
GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
MAX_POWER=$(nvidia-smi --query-gpu=power.max_limit --format=csv,noheader,nounits 2>/dev/null | head -1 | xargs)
TARGET_POWER=45

if [ -z "$MAX_POWER" ]; then
    echo "Warning: Could not detect GPU power limit. Skipping power cap."
else
    # Cap target to 75% of max, but not below GPU's minimum
    TARGET_POWER=$(echo "$MAX_POWER * 0.75 / 1" | bc 2>/dev/null || echo 45)
    echo "GPU: $GPU_NAME | Max: ${MAX_POWER}W | Setting limit to: ${TARGET_POWER}W"
    sudo nvidia-smi -pl "$TARGET_POWER" || echo "Warning: Could not set power limit (sudo required). Continuing anyway."
fi

echo ""
echo "Current GPU state:"
nvidia-smi --query-gpu=name,power.limit,temperature.gpu,memory.total --format=csv,noheader

echo ""
echo "Done. Now start ComfyUI with: ./app.sh start"
echo "Monitor during generation: watch -n 2 nvidia-smi"
