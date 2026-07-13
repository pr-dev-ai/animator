# Kids Animation Studio
*Ubuntu · NVIDIA GPU · Docker · Claude AI · Blender*

An end-to-end open-source pipeline for creating kids animation videos.
Write lyrics, compose music guidance, generate storyboards, record your own audio, sync lip movement, and assemble a final video — all from one browser dashboard.

---

## What This Does

| Step | Tool | How |
|------|------|-----|
| Write lyrics & chords | Claude AI | Web UI → Lyrics tab |
| Generate storyboard images | ComfyUI (Stable Diffusion) | Web UI → Storyboards tab |
| Record audio / singing | Audacity + your voice | Upload in Web UI → Audio tab |
| Lip-sync | Rhubarb | Auto-runs after audio upload |
| Assemble animatic video | FFmpeg | Web UI → Video tab |
| Full animation & render | Blender (host) | Manual, using generated assets |

---

## Quick Start

### 1. Clone and configure

```bash
git clone <repo-url>
cd animator
cp .env.example .env
```

Edit `.env` and add your Anthropic API key:
```
ANTHROPIC_API_KEY=sk-ant-api03-...
```

Get a key at https://console.anthropic.com/

### 2. Set GPU to efficient mode (prevents overheating)

```bash
./scripts/setup_gpu.sh
```

This caps your GPU power to 75% of max before starting ComfyUI.
Run once per session before starting the Docker services.

### 3. Start animation services

```bash
./app.sh start
```

Services:
- ComfyUI (Stable Diffusion) → http://localhost:8188
- Piper TTS → port 10200
- Rhubarb lip-sync → CLI container

### 4. Start the web UI

```bash
./start_ui.sh
```

Open http://localhost:5000 in your browser.

---

## Web UI Workflow (5 tabs)

```
1. Project    → Create or select your animation project
2. Lyrics     → Enter a theme → Claude generates lyrics + chord chart
3. Storyboard → Claude generates SD prompts → generate images in ComfyUI
4. Audio      → Upload your recorded WAV files per shot → auto lip-sync
5. Video      → Build animatic MP4 → preview in browser
```

---

## Architecture

```
┌─────────────────────────────────┐
│  Web UI (http://localhost:5000) │  ← start with ./start_ui.sh
└──────────────┬──────────────────┘
               │
    ┌──────────┴──────────────────────────┐
    │                                     │
    ▼                                     ▼
Claude API                          Animation Pipeline
(lyrics, chords,                    (Docker services)
 SD prompts)
                                    ┌──────────────┐
                                    │  ComfyUI     │ → storyboard PNGs
                                    │  (SD 1.5)    │   (http://localhost:8188)
                                    └──────────────┘
                                    ┌──────────────┐
                                    │  Piper TTS   │ → WAV files (optional)
                                    └──────────────┘
                                    ┌──────────────┐
                                    │  Rhubarb     │ → lip-sync JSON
                                    └──────────────┘
                                    ┌──────────────┐
                                    │  FFmpeg      │ → animatic MP4
                                    └──────────────┘
                                    ┌──────────────┐
                                    │  Blender     │ → final render
                                    │  (host)      │
                                    └──────────────┘
```

---

## Repository Structure

```
.
├── start_ui.sh              # One-command web UI launcher
├── app.sh                   # Start/stop Docker services
├── docker-compose.yml
├── .env.example
├── Makefile
│
├── web_ui/                  # Browser dashboard
│   ├── server.py            # Flask API server
│   ├── claude_api.py        # Claude SDK integration
│   ├── pipeline_api.py      # Pipeline script wrappers
│   ├── requirements.txt
│   ├── templates/
│   │   └── index.html       # Single-page app
│   └── static/
│       ├── app.js
│       └── style.css
│
├── scripts/
│   ├── setup_gpu.sh         # Cap GPU power before running ComfyUI
│   ├── create_project.py    # Scaffold a new project from template
│   ├── import_audio.py      # Import your recorded WAV files
│   ├── gen_tts.py           # Generate TTS voices (Piper)
│   ├── gen_lipsync.py       # Generate lip-sync JSON (Rhubarb)
│   └── make_dailies.py      # Assemble animatic MP4 (FFmpeg)
│
├── projects/
│   ├── kids_template/       # Template for new kids animation projects
│   └── night_shift/         # Example project (noir short film)
│
├── models/                  # Stable Diffusion model checkpoints
├── outputs/                 # Generated storyboards + animatic MP4s
├── voices/                  # WAV files + Rhubarb lip-sync JSON
├── comfyui/                 # ComfyUI Dockerfile + workspace
├── piper/                   # Piper TTS Dockerfile + voice models
└── rhubarb/                 # Rhubarb Dockerfile
```

---

## Project Structure (per animation)

```
projects/my_kids_show/
├── script.md              # Story / song script
├── shotlist.csv           # Shot list (shot_id, description, camera, duration)
├── dialogue.csv           # Lines per shot (type: dialogue | song)
├── styleguide.md          # Visual style guide + SD keywords
└── prompts/
    └── storyboards.md     # ComfyUI prompts per shot (generated by Claude)
```

`dialogue.csv` supports two track types:
- `type=dialogue` — spoken line (TTS or your recording)
- `type=song` — singing track (always your recording; TTS is skipped)

---

## Command-Line Scripts

### Create a new project

```bash
python3 scripts/create_project.py --name my_show --type kids
python3 scripts/create_project.py --name my_story --type story
```

### Import your recorded audio

```bash
# See which shots still need audio
python3 scripts/import_audio.py --project my_show --list

# Import a WAV file and auto-generate lip-sync
python3 scripts/import_audio.py \
  --project my_show \
  --shot SH020 \
  --character Narrator \
  --audio ~/recordings/verse1.wav \
  --lipsync
```

### Generate TTS (for dialogue lines only; song lines are skipped automatically)

```bash
python3 scripts/gen_tts.py --project my_show
```

### Generate lip-sync for all WAV files

```bash
python3 scripts/gen_lipsync.py --project my_show
```

### Build animatic

```bash
python3 scripts/make_dailies.py --project my_show
# → outputs/my_show_animatic.mp4
```

---

## GPU Efficiency (RTX 3050 / 4GB VRAM laptops)

ComfyUI uses `--lowvram --fp16-vae` flags automatically (set in `docker-compose.yml`).

Before each session, cap the GPU power limit to prevent overheating:

```bash
./scripts/setup_gpu.sh
```

Recommended ComfyUI settings for 4GB VRAM:
- Image size: 512×512
- Steps: 15–20
- Batch size: 1
- Model: SD 1.5 (not SDXL)

Monitor GPU during generation:
```bash
watch -n 2 nvidia-smi
```

---

## Music & Audio

You record and sing your own audio. See [MUSIC_TOOLS.md](MUSIC_TOOLS.md) for a full guide on Ubuntu software.

Short version:
- **Audacity** — record vocals, export WAV: `sudo apt install audacity`
- **LMMS** — compose backing music: `sudo apt install lmms`
- **MuseScore** — write sheet music: `sudo apt install musescore3`

Workflow: record in Audacity → export WAV → import with `scripts/import_audio.py` or the Audio tab in the web UI.

---

## Service Commands

```bash
# Docker services (ComfyUI, Piper, Rhubarb)
./app.sh start
./app.sh stop
./app.sh restart
./app.sh status
./app.sh logs

# Web UI
./start_ui.sh              # Start at http://localhost:5000

# Makefile shortcuts
make up                    # same as ./app.sh start
make down
make logs
make status
```

---

## System Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| GPU | 4GB VRAM (RTX 3050) | 8GB+ VRAM |
| RAM | 8GB | 16GB |
| Disk | 20GB | 40GB |
| OS | Ubuntu 20.04+ | Ubuntu 22.04 |
| Python | 3.10+ | 3.11+ |

Software: NVIDIA drivers, Docker + Compose v2, NVIDIA Container Toolkit, Blender 3.x (host)

---

## License

Scripts and pipeline code: MIT License.
Generated content is subject to the licenses of the models and assets you use.
