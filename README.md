# 🎬 Open-Source AI Animated Short Film Pipeline
*(Ubuntu • NVIDIA GPU • Docker • Blender)*

This repository provides a **fully open-source, low-cost, reproducible pipeline**
for creating **animated short films** using AI-assisted tooling.

It is designed for **developers / engineers** who are comfortable with:
- Linux (Ubuntu)
- Docker & Docker Compose
- Python scripting
- Blender (basic to intermediate)

No paid SaaS tools are required. Everything runs **locally** using your **NVIDIA GPU**.

---

## 📌 What This Project Solves

Traditional animation pipelines are expensive and manual.
This project provides:

- Local **AI-assisted asset creation** (storyboards, textures)
- **Automatic voice / dubbing generation** (offline)
- **Automatic lip-sync** from generated audio
- A **Dockerized AI stack**
- A **Blender-first animation workflow**
- Python automation for repeatability

Think of it as **“CI/CD for animated short films.”**

---

## 🧱 High-Level Architecture

```
┌──────────────┐
│   Script     │
└──────┬───────┘
       ↓
┌──────────────┐
│ Storyboards  │  (ComfyUI / Stable Diffusion)
└──────┬───────┘
       ↓
┌──────────────┐
│ Animatic     │  (FFmpeg / Blender VSE)
└──────┬───────┘
       ↓
┌──────────────┐
│ Voice (TTS)  │  (Piper – Docker)
└──────┬───────┘
       ↓
┌──────────────┐
│ Lip Sync     │  (Rhubarb – Docker)
└──────┬───────┘
       ↓
┌──────────────┐
│ Animation    │  (Blender – Host)
└──────┬───────┘
       ↓
┌──────────────┐
│ Rendering    │  (Blender – Eevee/Cycles)
└──────┬───────┘
       ↓
┌──────────────┐
│ Final Edit   │  (FFmpeg / Kdenlive)
└──────────────┘
```

---

## 🧰 Tech Stack

### Core
- **Blender** – animation, rendering, compositing
- **Docker / Docker Compose** – AI services
- **Python** – orchestration & automation
- **FFmpeg** – video/audio assembly

### AI (Open Source)
- **ComfyUI (Stable Diffusion)** – storyboards, concept art, textures
- **Piper TTS** – offline voice & dubbing
- **Rhubarb Lip Sync** – phoneme extraction

---

## 🖥️ System Requirements

### Hardware
- NVIDIA GPU (6GB VRAM minimum recommended)
- 16GB RAM (8GB minimum)
- 30GB free disk space

### Software
- Ubuntu 20.04 / 22.04
- NVIDIA drivers
- Docker + Docker Compose v2
- NVIDIA Container Toolkit
- Blender 3.x (installed on host)

---

## 🚀 Quick Start

### 1️⃣ Verify GPU access in Docker

```bash
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

You should see your GPU listed.

---

### 2️⃣ Clone the repository

```bash
git clone <your-repo-url>
cd <repo>
```

---

### 3️⃣ Prepare environment

```bash
cp .env.example .env
```

---

### 4️⃣ Start AI services

```bash
# Option 1: Using app control script (recommended)
./app.sh start

# Option 2: Using Makefile
make up
```

Services started:
- ComfyUI → http://localhost:8188
- Piper TTS → port 10200
- Rhubarb → CLI container

**📖 For detailed usage instructions, see [USAGE.md](USAGE.md)**

**⚠️ If your system restarts when generating images, see [TROUBLESHOOTING.md](TROUBLESHOOTING.md)**

---

## 📂 Repository Structure

```
.
├── docker-compose.yml
├── Makefile
├── README.md
├── .env.example
│
├── models/          # Stable Diffusion models
├── outputs/         # Generated images & videos
├── voices/          # Generated WAV + phoneme JSON
│
├── piper/           # Piper config & voices
├── comfyui/         # ComfyUI workspace
├── rhubarb/         # Rhubarb Dockerfile
│
├── scripts/         # Python automation scripts
└── projects/        # Individual film projects
```

---

## 🎞️ Project Structure (per film)

```
projects/night_shift/
├── script.md
├── shotlist.csv
├── dialogue.csv
├── styleguide.md
├── prompts/
│   └── storyboards.md
└── animatic/
```

---

## 🗣️ Automatic Voice & Lip-Sync Flow

**First-time setup:**
```bash
./scripts/setup_host.sh  # Creates virtual environment and installs dependencies
```

### Generate voices
```bash
./run_script.sh python3 scripts/gen_tts.py --project night_shift
```

### Generate lip-sync data
```bash
./run_script.sh python3 scripts/gen_lipsync.py --project night_shift
```

Outputs:
```
voices/night_shift/
├── SH020_Operator.wav
└── SH020_Operator.json
```

**📖 See [USAGE.md](USAGE.md) for complete workflow guide**

---

## 🎥 Blender Workflow (Host)

1. Open Blender on host
2. Load shot `.blend` file
3. Import generated WAV
4. Apply Rhubarb phonemes to viseme shape keys
5. Animate / light / render

Blender is **not containerized** to avoid GUI and input issues.

---

## 🎞️ Animatic & Dailies

```bash
python3 scripts/make_dailies.py --project night_shift
```

Creates:
```
outputs/night_shift_animatic.mp4
```

---

## 🧪 Example Projects Included

- **The Night Shift** – single room, audio-driven tension
- **Courier** – rooftop + alley, minimal dialogue
- **Signal** – one character, abstract visuals

Each is designed to be:
- < 90 seconds
- Solo-creator friendly
- Low animation complexity

---

## 🛠️ Makefile Commands

```bash
make up       # start stack
make down     # stop stack
make logs     # view logs
make build    # rebuild images
make status   # service status
make clean    # cleanup
```

---

## 🧩 Why This Pipeline Works

- Deterministic & reproducible
- No vendor lock-in
- Fully offline capable
- Scales from 30s shorts to multi-minute films
- Engineer-friendly (scripts, configs, version control)

---

## 🧭 Recommended First Milestone

**Goal:** First 60-second short

1. Generate script + shotlist
2. Create storyboard frames
3. Build animatic
4. Animate 2–3 dialogue shots
5. Render & assemble

---

## 📜 License

All scripts and pipeline code are provided under **MIT License**.
Generated content depends on the licenses of models/assets you use.

---

## 🙌 Final Note

This project treats animation like **software engineering**:
repeatable, testable, and automatable.

If you can build a backend system, you can build an animated film.

Happy filmmaking 🚀
