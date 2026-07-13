# Troubleshooting Guide

---

## Web UI Issues

### "No module named 'web_ui'" on startup

The server must be started as a Python module from the repo root, not run directly.

```bash
# Wrong — imports fail
python3 web_ui/server.py

# Correct — use the launcher
./start_ui.sh

# Or manually from repo root
python3 -m web_ui.server
```

### Projects not showing in the YOUR PROJECTS list

1. **Hard refresh the browser** (Ctrl+Shift+R) — the old cached JavaScript may still be loaded
2. **Restart the server** (Ctrl+C then `./start_ui.sh`) — picks up any code changes
3. Verify the API works: `curl http://localhost:5000/api/project/list`

If the API returns projects but the UI doesn't show them, it's a browser cache issue. Hard refresh fixes it.

### "ANTHROPIC_API_KEY is not configured" on startup

Edit `.env` and add your key:
```
ANTHROPIC_API_KEY=sk-ant-api03-...
```

Get a key at https://console.anthropic.com/

The key must not contain the placeholder text `your-key-here`.

### Lyrics / Chord generation shows an error

- Verify your API key is valid: try it at https://console.anthropic.com/
- Check the Flask server log for the actual error message
- Common cause: key expired or quota exceeded

### ComfyUI shows red in the SERVICES indicator

ComfyUI Docker service is not running. Start it:
```bash
./app.sh start
./app.sh status    # wait until "healthy"
```

---

## GPU / Overheating Issues

### System restarts or shuts down during image generation

This is a thermal/power issue. Fix in order:

**Step 1 — Cap GPU power limit**
```bash
./scripts/setup_gpu.sh
```
Caps GPU to 75% of its max power. Re-run before every session.

**Step 2 — Reduce image size in ComfyUI**
In the `Empty Latent Image` node:
- Width: 512, Height: 512 (not 1024)
- 512×512 uses ~75% less VRAM than 1024×1024

**Step 3 — Reduce sampling steps**
In the `KSampler` node:
- Steps: 15 (not 20–30)

**Step 4 — Close other GPU applications**
Close browser tabs, games, other GPU-heavy apps.

**Step 5 — Check temperatures during generation**
```bash
watch -n 2 nvidia-smi
```
- Temperature > 85°C → throttling risk
- Memory > 90% → OOM risk
- Power near limit → thermal risk

**Step 6 — Physical cooling**
- Use a laptop cooling pad
- Ensure vents are unobstructed
- Clean dust from fans

### CUDA out of memory

```
CUDA out of memory. Tried to allocate X GiB
```

Fixes:
1. In ComfyUI: set all `batch_size` nodes to **1**
2. Reduce image size to 512×512
3. Use `--lowvram` flag (already set in `docker-compose.yml`)
4. Restart ComfyUI container: `./app.sh restart`
5. Close other GPU applications

ComfyUI already launches with `--lowvram --fp16-vae` (configured in `docker-compose.yml`).

### Check GPU in Docker

```bash
./scripts/check_gpu_docker.sh

# Or manually
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

---

## Docker / Services Issues

### ComfyUI not accessible at http://localhost:8188

```bash
# Check if container is running
docker ps | grep comfyui

# Check logs
docker compose logs comfyui

# Restart
./app.sh restart
```

Note: ComfyUI downloads and installs itself on first run. This takes 5–10 minutes. Watch the log:
```bash
docker compose logs -f comfyui
```

### Piper TTS not generating voices

```bash
# Check Piper is running
./app.sh status

# Test manually
docker exec -it piper piper --help

# Check voice model exists
docker exec -it piper ls /config/*.onnx
```

If no `.onnx` files, download a voice model:
```bash
./scripts/download_piper_voice.sh
```

### Rhubarb lip-sync fails

```bash
# Check Rhubarb container is running
docker exec -it rhubarb rhubarb --version

# Common cause: WAV file doesn't exist
ls voices/<project>/*.wav

# Regenerate
python3 scripts/gen_lipsync.py --project <name> --force
```

### Port conflicts

If ports 8188 or 10200 are already in use:
```bash
sudo lsof -i :8188
sudo lsof -i :10200
```

Change ports in `.env` or `docker-compose.yml`.

---

## Audio Issues

### TTS generates audio for song lines

Edit `dialogue.csv` and set `type=song` for singing lines. TTS is unconditionally skipped for `type=song` even with `--force`.

### Custom WAV gets overwritten by TTS

If a WAV file exists for a shot, `gen_tts.py` skips it by default. To force regeneration anyway:
```bash
python3 scripts/gen_tts.py --project <name> --force
```

Use `--force` only when you want TTS to overwrite custom recordings.

### Lip-sync JSON is missing

```bash
python3 scripts/gen_lipsync.py --project <name>
```

Or import the audio with `--lipsync` flag:
```bash
python3 scripts/import_audio.py --project <name> --shot SH020 --character Narrator \
  --audio recording.wav --lipsync
```

---

## Animatic Issues

### Animatic missing shots (black frames)

Storyboard images must exist with filenames matching shot IDs:
```bash
ls outputs/<project>_storyboards/
# Should show: SH010.png SH020.png SH030.png ...
```

Filenames must exactly match `shot_id` values in `shotlist.csv`.

### FFmpeg not found

```bash
sudo apt install ffmpeg
ffmpeg -version
```

### Animatic has no audio

Check that WAV files exist:
```bash
ls voices/<project>/*.wav
```

`make_dailies.py` only mixes audio if WAV files match shot IDs from `dialogue.csv`.

---

## Diagnostic Commands

```bash
# Full system diagnostic
./scripts/diagnose_system_crash.sh

# GPU status
nvidia-smi
watch -n 2 nvidia-smi

# Docker GPU test
./scripts/check_gpu_docker.sh

# Service status
./app.sh status

# View service logs
./app.sh logs

# Web UI API check
curl http://localhost:5000/api/health
curl http://localhost:5000/api/project/list

# Check crash logs after restart
sudo journalctl -b -1 --priority=err | tail -50
sudo dmesg | grep -i -E "(error|crash|gpu|nvidia|thermal)"
```
