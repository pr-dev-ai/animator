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

---

## Web UI Fixes

### 4. ModuleNotFoundError: No module named 'web_ui'

**Problem**: Running `python3 web_ui/server.py` directly sets `sys.path[0]` to the `web_ui/` directory.
Inside the server, `from web_ui import pipeline_api` then fails because there's no `web_ui/` subdirectory
inside `web_ui/`.

**Fix**:
- Changed `start_ui.sh` to run `python3 -m web_ui.server` (module invocation adds the repo root to `sys.path`)
- Updated the entry guard in `server.py` to `if __name__ in ("__main__", "web_ui.server"):`

### 5. Projects not showing in the web UI

**Problem**: HTML element had `id="projects-list"` (with "s") but JavaScript called
`document.getElementById('project-list')` (without "s"), returning `null` — so the list was never
populated. The browser also cached the old JS, making the bug persist after the HTML fix.

**Fix**:
- Renamed the HTML element to `id="project-list"` (matching the JS)
- Changed element from `<div>` to `<ul>` with `class="project-list-items"` for semantic HTML
- Added CSS for clickable list items (hover highlight, pointer cursor)
- Added `app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0` to disable static file caching
- Added `Cache-Control: no-store` header for JS/CSS responses

### 6. ComfyUI overheating on RTX 3050 laptop

**Problem**: ComfyUI's default settings push the GPU to 100% power, causing thermal throttling
and system restarts on the RTX 3050 (60W max).

**Fix**:
- Added `scripts/setup_gpu.sh` — dynamically detects GPU max power and caps it to 75%
- Added `COMFYUI_ARGS=--lowvram --fp16-vae` to `docker-compose.yml` environment
- Updated `comfyui/Dockerfile` to pass `${COMFYUI_ARGS:-}` in the entrypoint

### 7. Song lines were getting TTS audio generated

**Problem**: `gen_tts.py` generated Piper TTS audio for singing lines even though the user
records their own vocals for those shots. Song tracks then got overwritten.

**Fix**: Updated `gen_tts.py` to read the `type` column from `dialogue.csv`. Rows with
`type=song` or `type=custom` are unconditionally skipped, even with `--force`.
