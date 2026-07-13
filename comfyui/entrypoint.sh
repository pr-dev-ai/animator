#!/bin/bash
set -e

mkdir -p /app/workspace
cd /app/workspace

# ── Install / verify ComfyUI ──────────────────────────────────────────────────
if [ ! -f ComfyUI/main.py ] || [ ! -d ComfyUI/comfy/ldm/models ]; then
  echo "Setting up ComfyUI..."

  if [ -d ComfyUI ]; then
    echo "Removing incomplete ComfyUI installation (preserving models/output)..."
    find ComfyUI -mindepth 1 -maxdepth 1 ! -name models ! -name output \
      -exec rm -rf {} + 2>/dev/null || true
    if [ "$(find ComfyUI -mindepth 1 -maxdepth 1 ! -name models ! -name output | wc -l)" -eq 0 ]; then
      rmdir ComfyUI 2>/dev/null || true
    fi
  fi

  if [ ! -d ComfyUI ]; then
    echo "Cloning ComfyUI repository..."
    git clone https://github.com/comfyanonymous/ComfyUI.git
  else
    echo "ComfyUI directory exists with mounted volumes, cloning to temp location..."
    git clone https://github.com/comfyanonymous/ComfyUI.git /tmp/ComfyUI_new
    rsync -a --exclude=models --exclude=output /tmp/ComfyUI_new/ ComfyUI/ || \
      (cd /tmp/ComfyUI_new && find . -mindepth 1 \
        ! -path "./models*" ! -path "./output*" \
        -exec cp -r {} /app/workspace/ComfyUI/ \;)
    rm -rf /tmp/ComfyUI_new
  fi

  cd ComfyUI
  echo "Installing dependencies (this may take 5-10 minutes)..."
  pip3 install --no-cache-dir -r requirements.txt
  pip3 install --no-cache-dir sqlalchemy alembic aiohttp requests || true
  echo "ComfyUI setup complete!"
else
  cd ComfyUI
  echo "ComfyUI already set up, verifying..."
fi

# ── Sanity check ──────────────────────────────────────────────────────────────
if [ ! -d comfy/ldm/models ]; then
  echo "ERROR: comfy/ldm/models missing"
  ls -la comfy/ 2>/dev/null || echo "comfy/ directory missing"
  exit 1
fi

echo "Verifying dependencies..."
pip3 install --no-cache-dir -r requirements.txt 2>&1 | tail -3 || true
python3 -c "import sqlalchemy, alembic, aiohttp, requests" 2>/dev/null || \
  pip3 install --no-cache-dir sqlalchemy alembic aiohttp requests

# ── Start ComfyUI ─────────────────────────────────────────────────────────────
echo "Starting ComfyUI on port 8188..."
# Use /app/launch_comfyui.py instead of `python3 main.py` directly.
# Running from /app ensures Python adds /app (not the ComfyUI dir) to
# sys.path[0], preventing ComfyUI's types.py from shadowing stdlib types.
cd /app
exec python3 /app/launch_comfyui.py --listen 0.0.0.0 --port 8188 ${COMFYUI_ARGS:-}
