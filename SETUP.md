# Setup Guide

## Prerequisites

- Ubuntu 20.04 / 22.04
- NVIDIA GPU with drivers installed (`nvidia-smi` should work)
- Python 3.10+
- Docker + Docker Compose v2
- NVIDIA Container Toolkit

---

## Step 1 — Clone and configure

```bash
git clone <repo-url>
cd animator
cp .env.example .env
```

Edit `.env` and fill in two things:

```bash
# Required for Web UI (Claude AI features)
ANTHROPIC_API_KEY=sk-ant-api03-...

# Optional: adjust ports if they conflict
COMFYUI_PORT=8188
PIPER_PORT=10200
```

Get an Anthropic API key at https://console.anthropic.com/

---

## Step 2 — Install Docker

```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Add yourself to the docker group (re-login after this)
sudo usermod -aG docker $USER
```

Verify:
```bash
docker compose version   # should show v2.x
```

---

## Step 3 — Install NVIDIA Container Toolkit

```bash
# Automated (recommended)
sudo ./scripts/install_nvidia_toolkit.sh

# Or manually
distribution=$(. /etc/os-release; echo $ID$VERSION_ID)
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

curl -s -L https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

sudo apt update && sudo apt install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

Verify GPU is accessible in Docker:
```bash
./scripts/check_gpu_docker.sh
# or manually:
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

---

## Step 4 — Download a Stable Diffusion model

Place a model checkpoint in `models/checkpoints/`.

Recommended for 4GB VRAM (RTX 3050):
- **SD 1.5** (`v1-5-pruned-emaonly.safetensors`)
- Download from: https://huggingface.co/runwayml/stable-diffusion-v1-5

```bash
# Helper script (downloads SD 1.5)
./scripts/download_model.sh
```

---

## Step 5 — Download Piper voice model

```bash
./scripts/download_piper_voice.sh
# Downloads en_US-lessac-medium.onnx to ./piper/
```

Or manually from: https://github.com/rhasspy/piper/releases

---

## Step 6 — Build Docker images

First build takes 10–15 minutes:

```bash
docker compose build
```

---

## Step 7 — Set GPU to efficient mode (laptops)

Caps GPU power to prevent overheating during ComfyUI generation:

```bash
./scripts/setup_gpu.sh
```

Run this once per session before starting the services. It detects your GPU's max power limit and sets it to 75%. Requires `sudo`.

---

## Step 8 — Start animation services

```bash
./app.sh start
```

Wait ~60 seconds, then check:
```bash
./app.sh status
```

Services:
- ComfyUI → http://localhost:8188
- Piper TTS → port 10200
- Rhubarb → CLI container

---

## Step 9 — Start the web UI

```bash
./start_ui.sh
```

This will:
1. Check your Anthropic API key in `.env`
2. Create a Python virtual environment (`.venv/`) if needed
3. Install web UI Python packages (`flask`, `anthropic`, `python-dotenv`)
4. Start the dashboard at http://localhost:5000

---

## Step 10 — Create your first project

1. Open http://localhost:5000
2. Go to the **Project** tab
3. Enter a project name and select type (Kids / Story)
4. Click **Create Project**

Or from the command line:
```bash
python3 scripts/create_project.py --name my_first_show --type kids
```

---

## Installing music tools (optional)

For composing backing tracks and recording your singing:

```bash
sudo apt install audacity lmms musescore3 ardour hydrogen
```

See [MUSIC_TOOLS.md](MUSIC_TOOLS.md) for full guide.

---

## Troubleshooting

**GPU not detected in Docker**
```bash
./scripts/check_gpu_docker.sh
# Ensure NVIDIA Container Toolkit is installed and Docker restarted
```

**System restarts during image generation**
```bash
./scripts/setup_gpu.sh   # cap power limit
# Then in ComfyUI: set image size to 512x512, steps to 15, batch to 1
```

**Web UI shows "No module named web_ui"**
```bash
# Must start via the launcher, not directly:
./start_ui.sh        # correct
python3 web_ui/server.py   # wrong — imports will fail
```

**Projects not showing in web UI**
- Hard refresh the browser: Ctrl+Shift+R
- Verify the server restarted after any code changes

For more, see [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
