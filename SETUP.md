# Setup Guide

## Prerequisites

### 1. System Requirements
- Ubuntu 20.04 / 22.04
- NVIDIA GPU with drivers installed
- Docker + Docker Compose v2
- Python 3.8+

### 2. Verify GPU on Host
```bash
nvidia-smi
```
You should see your GPU listed.

### 3. Install Docker & Docker Compose
```bash
# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Add user to docker group
sudo usermod -aG docker $USER
# Log out and log back in for this to take effect

# Verify Docker Compose v2
docker compose version
```

### 4. Install NVIDIA Container Toolkit

**Option 1: Automated installation (recommended)**
```bash
sudo ./scripts/install_nvidia_toolkit.sh
```

**Option 2: Manual installation**
```bash
# Add NVIDIA package repositories
distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

curl -s -L https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list | \
    sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
    sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

# Install
sudo apt update
sudo apt install -y nvidia-container-toolkit

# Configure Docker runtime
sudo nvidia-ctk runtime configure --runtime=docker

# Restart Docker
sudo systemctl restart docker
```

### 5. Verify GPU in Docker
```bash
./scripts/check_gpu_docker.sh
```

Or manually:
```bash
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

### 6. Install Python Dependencies
```bash
pip3 install -r requirements.txt
```

## Quick Start

1. **Copy environment file**
   ```bash
   cp .env.example .env
   ```

2. **Download a Stable Diffusion model**
   - Place in `models/checkpoints/`
   - Recommended: SD 1.5 for 4GB VRAM

3. **Start services**
   ```bash
   # Option 1: Using the app control script (recommended)
   ./app.sh start
   
   # Option 2: Using Makefile
   make up
   ```

4. **Verify services**
   - ComfyUI: http://localhost:8188
   - Check logs: `./app.sh logs` or `make logs`
   - Check status: `./app.sh status` or `make status`

5. **Test with example project**
   ```bash
   # Generate voices
   python3 scripts/gen_tts.py --project night_shift
   
   # Generate lip-sync
   python3 scripts/gen_lipsync.py --project night_shift
   
   # Create animatic (requires storyboard images first)
   python3 scripts/make_dailies.py --project night_shift
   ```

## Troubleshooting

### GPU not detected in Docker
- Run `./scripts/check_gpu_docker.sh` for diagnostics
- Ensure NVIDIA Container Toolkit is installed and Docker is restarted
- Verify user is in docker group: `groups | grep docker`

### Port conflicts
- Change ports in `docker-compose.yml` or `.env`
- Check what's using ports: `sudo lsof -i :8188`

### Permission errors
- Ensure user is in docker group
- Check volume mount permissions: `ls -la models/ outputs/ voices/`

### ComfyUI not accessible
- Check logs: `docker compose logs comfyui`
- Verify GPU: `docker exec -it comfyui nvidia-smi`
- Check health: `docker compose ps`

## Application Control

Use the `app.sh` script for easy service management:

```bash
./app.sh start      # Start all services
./app.sh stop       # Stop all services
./app.sh restart    # Restart all services
./app.sh status     # Show service status
./app.sh logs       # View logs (follow mode)
./app.sh help       # Show help
```

Alternatively, use Makefile commands: `make up`, `make down`, `make logs`, etc.

## Next Steps

1. Create your own project in `projects/`
2. Generate storyboards using ComfyUI
3. Create dialogue.csv
4. Generate voices and lip-sync
5. Animate in Blender
6. Render and assemble

See `README.md` for full documentation.
