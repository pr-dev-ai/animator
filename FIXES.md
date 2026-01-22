# Fixes Applied

## Issues Fixed

### 1. Obsolete Docker Compose Version Field
**Problem**: `version: '3.8'` is obsolete in Docker Compose v2
**Fix**: Removed the version field from `docker-compose.yml`

### 2. Non-Existent Docker Images
**Problem**: 
- `rhasspy/piper:latest` doesn't exist on Docker Hub
- `comfyui/comfyui:latest` may not be available

**Fix**: 
- Created `piper/Dockerfile` to build Piper TTS from source
- Created `comfyui/Dockerfile` to build ComfyUI from source
- Both services now use `build:` instead of `image:`

### 3. Updated docker-compose.yml

All services now build from Dockerfiles:
- **ComfyUI**: Builds from `./comfyui/Dockerfile` (clones ComfyUI repo)
- **Piper**: Builds from `./piper/Dockerfile` (installs piper-tts package)
- **Rhubarb**: Already had Dockerfile (unchanged)

## Next Steps

1. **Build the images:**
   ```bash
   docker compose build
   ```

2. **Start services:**
   ```bash
   ./app.sh start
   ```

3. **For Piper TTS**, download voice models:
   - Visit: https://github.com/rhasspy/piper/releases
   - Download `.onnx` voice model files
   - Place in `./piper/` directory
   - See `piper/README.md` for details

4. **For ComfyUI**, download Stable Diffusion models:
   - Place in `./models/checkpoints/`
   - See `models/README.md` for details

## Testing

After building, verify services:

```bash
# Check status
./app.sh status

# Check ComfyUI
curl http://localhost:8188

# Check Piper (if HTTP API is enabled)
# Otherwise use: docker exec -it piper piper --help
```

## Notes

- First build may take 10-15 minutes (downloading dependencies)
- ComfyUI will clone the repository on first run
- Ensure GPU is accessible: `./scripts/check_gpu_docker.sh`
