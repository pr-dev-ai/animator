FROM nvidia/cuda:12.1.0-cudnn8-runtime-ubuntu22.04

# Avoid interactive prompts
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies
RUN apt-get update && \
    apt-get install -y \
    python3 \
    python3-pip \
    git \
    wget \
    curl \
    rsync \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Create entrypoint script that handles ComfyUI setup
RUN echo '#!/bin/bash\n\
set -e\n\
# Ensure workspace directory exists\n\
mkdir -p /app/workspace\n\
cd /app/workspace\n\
# Check if ComfyUI needs setup\n\
if [ ! -f ComfyUI/main.py ] || [ ! -d ComfyUI/comfy/ldm/models ]; then\n\
  echo "Setting up ComfyUI..."\n\
  # Remove incomplete installation if exists (but preserve mounted volumes)\n\
  if [ -d ComfyUI ]; then\n\
    echo "Removing incomplete ComfyUI installation (preserving models/output)..."\n\
    # Remove everything except models and output directories\n\
    find ComfyUI -mindepth 1 -maxdepth 1 ! -name models ! -name output -exec rm -rf {} + 2>/dev/null || true\n\
    # If directory is now empty except for models/output, try to remove and recreate\n\
    if [ "$(find ComfyUI -mindepth 1 -maxdepth 1 ! -name models ! -name output | wc -l)" -eq 0 ]; then\n\
      # Directory only has models/output, safe to remove and recreate\n\
      rmdir ComfyUI 2>/dev/null || true\n\
    fi\n\
  fi\n\
  # Clone directly to workspace\n\
  if [ ! -d ComfyUI ]; then\n\
    echo "Cloning ComfyUI repository (this may take a minute)..."\n\
    git clone https://github.com/comfyanonymous/ComfyUI.git\n\
  else\n\
    echo "ComfyUI directory exists with mounted volumes, cloning to temp location..."\n\
    git clone https://github.com/comfyanonymous/ComfyUI.git /tmp/ComfyUI_new\n\
    # Copy files excluding models and output\n\
    rsync -a --exclude=models --exclude=output /tmp/ComfyUI_new/ ComfyUI/ || \\\n\
    (cd /tmp/ComfyUI_new && find . -mindepth 1 ! -path "./models*" ! -path "./output*" -exec cp -r {} /app/workspace/ComfyUI/ \\;)\n\
    rm -rf /tmp/ComfyUI_new\n\
  fi\n\
  cd ComfyUI\n\
  echo "Installing dependencies (this will take 5-10 minutes)..."\n\
  pip3 install --no-cache-dir -r requirements.txt\n\
  # Install missing dependencies\n\
  pip3 install --no-cache-dir sqlalchemy alembic aiohttp || true\n\
  echo "ComfyUI setup complete!"\n\
else\n\
  cd ComfyUI\n\
  echo "ComfyUI already set up, verifying..."\n\
fi\n\
# Verify critical structure exists\n\
if [ ! -d comfy/ldm/models ]; then\n\
  echo "ERROR: Critical directory comfy/ldm/models missing"\n\
  ls -la comfy/ 2>/dev/null || echo "comfy/ directory missing"\n\
  exit 1\n\
fi\n\
# Ensure all dependencies are installed\n\
echo "Verifying dependencies..."\n\
pip3 install --no-cache-dir -r requirements.txt 2>&1 | tail -3 || true\n\
python3 -c "import sqlalchemy, alembic, aiohttp" 2>/dev/null || pip3 install --no-cache-dir sqlalchemy alembic aiohttp\n\
echo "Starting ComfyUI on port 8188..."\n\
python3 main.py --listen 0.0.0.0 --port 8188 ${COMFYUI_ARGS:-}\n\
' > /entrypoint.sh && chmod +x /entrypoint.sh

# Expose port
EXPOSE 8188

# Use entrypoint script
ENTRYPOINT ["/entrypoint.sh"]
