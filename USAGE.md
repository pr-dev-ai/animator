# How to Use the App

A complete step-by-step guide to using the AI animation pipeline.

---

## 📋 Table of Contents

1. [Initial Setup](#initial-setup)
2. [Starting the App](#starting-the-app)
3. [Working with Projects](#working-with-projects)
4. [Generating Storyboards](#generating-storyboards)
5. [Generating Voices (TTS)](#generating-voices-tts)
6. [Generating Lip-Sync](#generating-lip-sync)
7. [Creating Animatics](#creating-animatics)
8. [Blender Workflow](#blender-workflow)
9. [Complete Workflow Example](#complete-workflow-example)

---

## Initial Setup

### 1. First-Time Setup

If you haven't set up the environment yet, follow `SETUP.md` first. Key steps:

```bash
# Verify GPU
./scripts/check_gpu_docker.sh

# Copy environment file
cp .env.example .env

# Download a Stable Diffusion model (SD 1.5 recommended for 4GB VRAM)
# Place it in: models/checkpoints/
```

### 2. Install Python Dependencies

```bash
pip3 install -r requirements.txt
```

---

## Starting the App

### Start Services

```bash
./app.sh start
```

This will:
- Check prerequisites
- Verify GPU access
- Start ComfyUI, Piper TTS, and Rhubarb containers
- Show service URLs

**Services:**
- **ComfyUI**: http://localhost:8188 (storyboard generation)
- **Piper TTS**: http://localhost:10200 (voice generation)
- **Rhubarb**: CLI container (lip-sync generation)

### Check Status

```bash
./app.sh status
```

### View Logs

```bash
./app.sh logs
```

### Stop Services

```bash
./app.sh stop
```

---

## Working with Projects

### Project Structure

Each project lives in `projects/<project_name>/`:

```
projects/night_shift/
├── script.md              # Story script
├── shotlist.csv          # Shot breakdown with durations
├── dialogue.csv          # Dialogue lines for TTS
├── styleguide.md         # Visual style guide
└── prompts/
    └── storyboards.md    # ComfyUI prompts per shot
```

### Using the Example Project

The `night_shift` project is included as an example. You can:
- Use it as a template
- Test the pipeline end-to-end
- Modify it for your own project

### Creating a New Project

1. **Create project directory:**
   ```bash
   mkdir -p projects/my_project/prompts
   ```

2. **Create required files:**
   - `script.md` - Your story script
   - `shotlist.csv` - Shot breakdown (see `projects/night_shift/shotlist.csv` for format)
   - `dialogue.csv` - Dialogue lines (see `projects/night_shift/dialogue.csv` for format)
   - `styleguide.md` - Visual style guide
   - `prompts/storyboards.md` - ComfyUI prompts

3. **Shot list CSV format:**
   ```csv
   shot_id,description,camera,duration,notes
   SH010,Establishing shot,Wide,3.0,Exterior
   SH020,Character enters,Medium,4.0,Interior
   ```

4. **Dialogue CSV format:**
   ```csv
   shot_id,character,text,voice_id,language
   SH020,Character,Hello world.,en_US-lessac-medium,en
   ```

---

## Generating Storyboards

### Method 1: Using ComfyUI Web Interface (Recommended)

1. **Open ComfyUI:**
   ```
   http://localhost:8188
   ```

2. **Load a prompt:**
   - Open `projects/night_shift/prompts/storyboards.md`
   - Copy a prompt (e.g., SH020 prompt)

3. **Configure ComfyUI:**
   - **Model**: Select SD 1.5 model from dropdown
   - **Prompt**: Paste the prompt from storyboards.md
   - **Negative Prompt**: Add "text, captions, logos, watermark"
   - **Width/Height**: 512x512 (for 4GB VRAM) or 640x360 (16:9)
   - **Steps**: 20-30
   - **Sampler**: DPM++ 2M Karras or Euler a

4. **Generate:**
   - Click "Queue Prompt"
   - Wait for generation
   - Save image with shot ID as filename (e.g., `SH020.png`)

5. **Save to outputs:**
   ```bash
   mkdir -p outputs/night_shift_storyboards
   # Move generated images here with shot IDs as filenames
   ```

### Method 2: Batch Generation (Manual)

1. Generate each shot using ComfyUI
2. Save images as: `outputs/<project>_storyboards/SH010.png`, `SH020.png`, etc.
3. Ensure filenames match shot IDs from `shotlist.csv`

### Tips for Storyboards

- **Consistency**: Use the same character descriptions across shots
- **Resolution**: 512x512 is VRAM-safe for RTX 3050 4GB
- **Style**: Follow your `styleguide.md` for visual consistency
- **Naming**: Use exact shot IDs (SH010, SH020, etc.) for automation

---

## Generating Voices (TTS)

### Prerequisites

- Services running: `./app.sh start`
- `dialogue.csv` file in your project directory

### Generate Voices

```bash
python3 scripts/gen_tts.py --project night_shift
```

This will:
- Read `projects/night_shift/dialogue.csv`
- Generate WAV files for each dialogue line
- Save to `voices/night_shift/SH020_Character.wav`

### Output Structure

```
voices/night_shift/
├── SH050_Operator.wav
└── SH100_Operator.wav
```

### Regenerate (Force)

```bash
python3 scripts/gen_tts.py --project night_shift --force
```

### Troubleshooting TTS

- **Service not running**: `./app.sh start`
- **Port conflict**: Check if Piper is accessible: `curl http://localhost:10200`
- **Voice model not found**: Check available voices in Piper container

---

## Generating Lip-Sync

### Prerequisites

- TTS WAV files generated (from previous step)
- Services running: `./app.sh start`

### Generate Lip-Sync Data

```bash
python3 scripts/gen_lipsync.py --project night_shift
```

This will:
- Find all WAV files in `voices/night_shift/`
- Generate JSON phoneme files using Rhubarb
- Save alongside WAV files

### Output Structure

```
voices/night_shift/
├── SH050_Operator.wav
├── SH050_Operator.json    # Lip-sync phoneme data
├── SH100_Operator.wav
└── SH100_Operator.json
```

### Using in Blender

The JSON files contain phoneme timing data that can be:
- Imported into Blender
- Applied to shape keys for lip-sync animation
- Used with addons like "Rhubarb Lip Sync" for Blender

### Regenerate (Force)

```bash
python3 scripts/gen_lipsync.py --project night_shift --force
```

---

## Creating Animatics

### Prerequisites

- Storyboard images in `outputs/<project>_storyboards/`
- Optional: TTS audio files in `voices/<project>/`
- FFmpeg installed: `sudo apt install ffmpeg`

### Generate Animatic

```bash
python3 scripts/make_dailies.py --project night_shift
```

This will:
- Read `shotlist.csv` for shot order and durations
- Assemble storyboard images in sequence
- Mix in dialogue audio if available
- Create `outputs/night_shift_animatic.mp4`

### Output

```
outputs/
└── night_shift_animatic.mp4
```

### How It Works

1. Reads `shotlist.csv` to get shot order
2. Finds storyboard images matching shot IDs
3. Uses shot durations from CSV
4. Mixes in dialogue audio if WAV files exist
5. Creates 24fps MP4 video

### Customization

Edit `scripts/make_dailies.py` to adjust:
- Frame rate (default: 24fps)
- Video codec settings
- Audio mixing behavior

---

## Blender Workflow

### 1. Import Assets

- **Storyboards**: Reference for camera angles and composition
- **Audio**: Import WAV files for dialogue
- **Lip-sync JSON**: Import phoneme data

### 2. Setup Scene

- **Frame Rate**: 24 fps
- **Resolution**: Match your target (e.g., 1280x720)
- **Render Engine**: Eevee (fast iteration) or Cycles (quality)

### 3. Apply Lip-Sync

1. Import Rhubarb JSON data
2. Create viseme shape keys on character
3. Map phonemes to shape keys
4. Animate based on JSON timing

### 4. Animate

- Use storyboards as reference
- Follow shot durations from `shotlist.csv`
- Sync animation to imported audio

### 5. Render

- Render per shot: `renders/<project>/<shot_id>/v001/%04d.png`
- Use consistent naming for assembly

### 6. Final Assembly

Use FFmpeg or video editor to:
- Assemble rendered shots
- Add final audio mix
- Add transitions/effects

---

## Complete Workflow Example

Here's a complete workflow using the `night_shift` example:

### Step 1: Start Services

```bash
./app.sh start
```

Wait for services to be healthy: `./app.sh status`

### Step 2: Generate Storyboards

1. Open http://localhost:8188
2. Load SD 1.5 model
3. For each shot in `projects/night_shift/prompts/storyboards.md`:
   - Copy prompt
   - Generate in ComfyUI
   - Save as `outputs/night_shift_storyboards/SH010.png`, etc.

### Step 3: Generate Voices

```bash
python3 scripts/gen_tts.py --project night_shift
```

Check output: `ls voices/night_shift/`

### Step 4: Generate Lip-Sync

```bash
python3 scripts/gen_lipsync.py --project night_shift
```

Check output: `ls voices/night_shift/*.json`

### Step 5: Create Animatic

```bash
python3 scripts/make_dailies.py --project night_shift
```

View: `outputs/night_shift_animatic.mp4`

### Step 6: Animate in Blender

1. Open Blender
2. Import storyboards as reference
3. Import WAV files for audio
4. Import JSON files for lip-sync
5. Animate and render

### Step 7: Final Assembly

Assemble rendered shots with audio in your video editor.

---

## Common Commands Reference

```bash
# Service Management
./app.sh start          # Start all services
./app.sh stop           # Stop all services
./app.sh restart        # Restart services
./app.sh status         # Check service status
./app.sh logs           # View logs

# GPU Validation
./scripts/check_gpu_docker.sh

# Project Workflow
python3 scripts/gen_tts.py --project <name>           # Generate voices
python3 scripts/gen_lipsync.py --project <name>        # Generate lip-sync
python3 scripts/make_dailies.py --project <name>       # Create animatic

# Docker Commands (if needed)
docker compose ps                    # Service status
docker compose logs comfyui          # ComfyUI logs
docker exec -it comfyui nvidia-smi   # Check GPU in container
```

---

## Tips & Best Practices

### Storyboards
- Generate in batches (5-10 shots at a time)
- Keep prompts consistent for character/environment
- Use negative prompts to avoid unwanted elements

### TTS
- Keep dialogue lines under 2.5 seconds
- Test voice models before batch generation
- Use `--force` to regenerate if needed

### Lip-Sync
- Ensure WAV files are clear and well-paced
- Check JSON output for timing accuracy
- Test in Blender before full production

### Animatics
- Review animatic before starting animation
- Adjust shot durations in `shotlist.csv` if needed
- Use animatic as timing reference in Blender

### Performance
- **4GB VRAM**: Use SD 1.5, 512x512 resolution
- **8GB+ VRAM**: Can use SDXL, higher resolutions
- Monitor GPU usage: `watch -n 1 nvidia-smi`

---

## Troubleshooting

### ComfyUI Not Loading
- Check logs: `./app.sh logs`
- Verify GPU: `docker exec -it comfyui nvidia-smi`
- Check port: `curl http://localhost:8188`

### TTS Not Working
- Verify Piper is running: `./app.sh status`
- Check port: `curl http://localhost:10200`
- Review logs: `docker compose logs piper`

### Lip-Sync Fails
- Ensure Rhubarb container is running
- Check WAV file exists and is valid
- Verify container: `docker exec -it rhubarb rhubarb --version`

### Animatic Missing Shots
- Verify storyboard images exist with correct filenames
- Check `shotlist.csv` has all shot IDs
- Ensure image filenames match shot IDs exactly

---

## Next Steps

1. **Create your own project** in `projects/`
2. **Experiment with prompts** in ComfyUI
3. **Test different voice models** in Piper
4. **Build your animation** in Blender
5. **Iterate and refine** your workflow

For detailed setup instructions, see `SETUP.md`.  
For architecture overview, see `README.md`.
