# ComfyUI Configuration Guide

Complete guide to loading prompts, configuring models, and using workflows in ComfyUI.

---

## 🚀 Quick Start

1. **Open ComfyUI**: http://localhost:8188
2. **Load a model**: Click the model dropdown (top of interface)
3. **Enter prompt**: Use prompts from `projects/<project>/prompts/storyboards.md`
4. **Generate**: Click "Queue Prompt" button

---

## 📋 Table of Contents

1. [Basic Text-to-Image Setup](#basic-text-to-image-setup)
2. [Loading Prompts from Project Files](#loading-prompts-from-project-files)
3. [Configuring Models](#configuring-models)
4. [Advanced Workflows](#advanced-workflows)
5. [Saving and Loading Workflows](#saving-and-loading-workflows)
6. [Troubleshooting](#troubleshooting)

---

## Basic Text-to-Image Setup

### Step 1: Verify ComfyUI is Running

```bash
./app.sh status
# Should show: comfyui ... Up
```

Open in browser: http://localhost:8188

### Step 2: Load a Stable Diffusion Model

1. **Check available models:**
   ```bash
   ls models/checkpoints/
   ```
   You should see files like:
   - `v1-5-pruned.ckpt` (SD 1.5)
   - `v1-5-pruned-ckpt.safetensors` (SD 1.5)

2. **In ComfyUI interface:**
   - Look for a **model dropdown** or **"Load Checkpoint"** node
   - If you see a simple interface, ComfyUI may be using a default workflow
   - If you see an empty canvas, you need to add nodes (see [Advanced Workflows](#advanced-workflows))

### Step 3: Simple Interface (Default Workflow)

If ComfyUI shows a simple interface with:
- Model dropdown
- Prompt text box
- Negative prompt text box
- Width/Height sliders
- Generate button

**Configuration:**
- **Model**: Select your SD 1.5 model (e.g., `v1-5-pruned.ckpt`)
- **Prompt**: Copy from `projects/night_shift/prompts/storyboards.md`
- **Negative Prompt**: `text, captions, logos, watermark, signature, low quality, blurry`
- **Width**: 512 (or 640 for 16:9)
- **Height**: 512 (or 360 for 16:9)
- **Steps**: 20-30
- **CFG Scale**: 7-9
- **Sampler**: DPM++ 2M Karras (or Euler a)

### Step 4: Generate

1. Click **"Queue Prompt"** or **"Generate"** button
2. Wait for generation (10-60 seconds depending on GPU)
3. Image appears in the **"Generated"** tab (left sidebar → Assets)
4. Right-click image → **"Save"** or download

---

## Loading Prompts from Project Files

### Method 1: Copy-Paste (Recommended for Beginners)

1. **Open prompt file:**
   ```bash
   cat projects/night_shift/prompts/storyboards.md
   ```

2. **Copy a prompt** (e.g., SH020):
   ```
   Medium shot, security operator at console, multiple monitors visible, 
   dim monitor glow lighting, tense mood, control room, 
   surveillance aesthetic, cinematic lighting, high contrast, 
   cool blue color palette, no text, no captions, no logos, 
   professional photography, 4k
   ```

3. **Paste into ComfyUI prompt field**

4. **Generate and save** with shot ID as filename: `SH020.png`

### Method 2: Batch Processing Script

For automated batch generation, you can create a script that:
- Reads `prompts/storyboards.md`
- Extracts each shot prompt
- Calls ComfyUI API
- Saves images with shot IDs

*(This would require ComfyUI API integration - see Advanced section)*

---

## Configuring Models

### Available Models

Check what models are available:
```bash
ls -lh models/checkpoints/
```

### Model Recommendations

| GPU VRAM | Model | Resolution | Steps |
|----------|-------|------------|-------|
| 4GB      | SD 1.5 | 512x512    | 20-25 |
| 6GB      | SD 1.5 | 640x360    | 25-30 |
| 8GB+     | SD 1.5 | 768x512    | 30-40 |
| 12GB+    | SDXL   | 1024x1024  | 30-40 |

### Loading a New Model

1. **Download model** to `models/checkpoints/`:
   ```bash
   ./scripts/download_model.sh
   ```

2. **Restart ComfyUI** (if needed):
   ```bash
   ./app.sh restart
   ```

3. **Refresh browser** and select new model from dropdown

---

## Advanced Workflows

### Node-Based Interface

If ComfyUI shows an empty canvas with a grid, you're in **workflow mode**. You need to build a workflow:

#### Basic Workflow Nodes

1. **Load Checkpoint** node:
   - Right-click canvas → **Add Node** → **loaders** → **CheckpointLoaderSimple**
   - Select model: `v1-5-pruned.ckpt` from the dropdown
   - This node outputs: MODEL (purple), CLIP (yellow), and VAE (red)

2. **CLIP Text Encode (Prompt)** node:
   - Right-click canvas → **Add Node** → **conditioning** → **CLIPTextEncode**
   - This creates a CLIP Text Encode node
   - Connect the **yellow CLIP output** (right side of Load Checkpoint) to the **CLIP input** (left side of CLIPTextEncode)
   - Enter your prompt text in the text field
   - **Note**: You'll add TWO of these nodes - one for positive prompt, one for negative

3. **CLIP Text Encode (Negative)** node:
   - Right-click canvas → **Add Node** → **conditioning** → **CLIPTextEncode**
   - Connect the **yellow CLIP output** from Load Checkpoint to this node's CLIP input
   - Enter negative prompt in the text field

4. **Empty Latent Image** node:
   - Right-click canvas → **Add Node** → **latent** → **EmptyLatentImage**
   - Set width: `512`, height: `512`, batch_size: `1`

5. **KSampler** node:
   - Right-click canvas → **Add Node** → **sampling** → **KSampler**
   - Connect:
     - **MODEL** (purple): From Load Checkpoint MODEL output → KSampler model input
     - **positive** (green): From first CLIPTextEncode CONDITIONING output → KSampler positive input
     - **negative** (red): From second CLIPTextEncode CONDITIONING output → KSampler negative input
     - **latent_image** (grey): From EmptyLatentImage LATENT output → KSampler latent_image input
   - Configure:
     - **seed**: Leave as random (or set specific number)
     - **steps**: `20`
     - **cfg_scale**: `7`
     - **sampler_name**: Select `dpmpp_2m` (DPM++ 2M Karras)
     - **scheduler**: `normal` or `karras`

6. **VAE Decode** node:
   - Right-click canvas → **Add Node** → **latent** → **VAEDecode**
   - Connect:
     - **samples** (grey): From KSampler LATENT output → VAEDecode samples input
     - **vae** (orange): From Load Checkpoint VAE output (red dot) → VAEDecode vae input

7. **Save Image** node:
   - Right-click canvas → **Add Node** → **image** → **SaveImage**
   - Connect:
     - **images** (blue): From VAEDecode IMAGE output → SaveImage images input
   - Configure (optional):
     - **filename_prefix**: `SH020` (use your shot ID)
     - Images will save to: `outputs/` directory

#### Quick Workflow Template

Instead of building manually, you can:
1. Click **"Load"** button (top right)
2. Load a workflow JSON file
3. Or use ComfyUI's built-in examples

---

## Saving and Loading Workflows

### Save Current Workflow

1. Click **"Save"** button (top right of ComfyUI)
2. Workflow saves as JSON
3. Files saved to: `comfyui/ComfyUI/output/` (or check ComfyUI settings)

### Load a Workflow

1. Click **"Load"** button (top right)
2. Browse to workflow JSON file
3. Workflow loads with all node connections

### Example Workflow Location

Workflows can be stored in:
```
projects/night_shift/workflows/
├── basic_storyboard.json
└── advanced_storyboard.json
```

### Creating a Reusable Workflow

1. Build your workflow in ComfyUI
2. Configure all settings (model, resolution, etc.)
3. Save workflow JSON
4. Store in project directory
5. Load when needed

---

## Using ComfyUI API (Advanced)

For batch processing, you can use ComfyUI's API:

### Check API Endpoint

```bash
curl http://localhost:8188/system_stats
```

### Example API Call

```python
import requests
import json

# Queue a prompt
url = "http://localhost:8188/prompt"
payload = {
    "prompt": "your prompt here",
    "model": "v1-5-pruned.ckpt",
    "width": 512,
    "height": 512,
    "steps": 20
}
response = requests.post(url, json=payload)
```

*(Full API documentation: https://github.com/comfyanonymous/ComfyUI/wiki/API)*

---

## Configuration Tips

### For Storyboards

- **Resolution**: 512x512 or 640x360 (16:9 aspect ratio)
- **Steps**: 20-30 (balance speed vs quality)
- **CFG Scale**: 7-9 (higher = more adherence to prompt)
- **Sampler**: DPM++ 2M Karras (fast, good quality)
- **Seed**: Leave random for variety, or set for consistency

### Negative Prompts (Recommended)

Always include:
```
text, captions, logos, watermark, signature, low quality, blurry, 
distorted, deformed, bad anatomy, extra limbs, duplicate
```

### Style Consistency

- Use same **seed** for similar shots
- Use same **model** throughout project
- Keep **prompt structure** consistent
- Reference `styleguide.md` for visual consistency

---

## Troubleshooting

### Model Not Showing in Dropdown

1. **Check model location:**
   ```bash
   ls models/checkpoints/
   ```

2. **Verify ComfyUI can see models:**
   ```bash
   docker exec -it comfyui ls /app/workspace/ComfyUI/models/checkpoints/
   ```

3. **Restart ComfyUI:**
   ```bash
   ./app.sh restart
   ```

### Generation Fails / Out of Memory

**Most common cause: batch_size is set too high!**

1. **Check `Empty Latent Image` node:**
   - `batch_size` must be `1` (not 2, 4, or higher)
   - `width: 512`, `height: 512` (not 1024)

2. **Reduce resolution**: 512x512 instead of 768x768 or 1024x1024
3. **Reduce steps**: 15-20 instead of 30
4. **Clear GPU memory:**
   ```bash
   ./app.sh restart
   ```
5. **Check GPU memory:**
   ```bash
   docker exec -it comfyui nvidia-smi
   # Or on host:
   watch -n 1 nvidia-smi
   ```

**For RTX 3050 (4GB VRAM): Always use batch_size = 1, resolution = 512x512**

### Images Not Saving

1. **Check output directory:**
   ```bash
   ls outputs/
   ```

2. **Verify volume mount:**
   ```bash
   docker compose config | grep outputs
   ```

3. **Check ComfyUI logs:**
   ```bash
   ./app.sh logs comfyui
   ```

### Workflow Not Loading

1. **Check JSON format**: Valid JSON required
2. **Check node compatibility**: Some workflows require custom nodes
3. **Try basic workflow first**: Build simple workflow, then expand

### ComfyUI Interface Blank

1. **Check browser console**: F12 → Console tab
2. **Check ComfyUI logs:**
   ```bash
   docker compose logs comfyui
   ```
3. **Restart service:**
   ```bash
   ./app.sh restart
   ```

---

## Quick Reference

### Essential Settings for Storyboards

```
Model: v1-5-pruned.ckpt (or v1-5-pruned-ckpt.safetensors)
Resolution: 512x512 (or 640x360 for 16:9)
Steps: 20-30
CFG Scale: 7-9
Sampler: dpmpp_2m (DPM++ 2M Karras)
Seed: Random (or specific for consistency)
```

**⚠️ Important for RTX 3050 (4GB VRAM) or Laptops:**
- **Always use 512x512 resolution** (not 1024x1024)
- **Use 15-20 steps** (not 30+)
- **Batch size: 1** (never use batch > 1)
- If system restarts, see [TROUBLESHOOTING.md](TROUBLESHOOTING.md)

### File Locations

- **Models**: `models/checkpoints/`
- **Generated Images**: `outputs/` (or ComfyUI's output folder)
- **Prompts**: `projects/<project>/prompts/storyboards.md`
- **Workflows**: Save to `projects/<project>/workflows/` (optional)

---

## Next Steps

1. **Generate your first storyboard**: Use SH010 prompt from `night_shift` project
2. **Experiment with settings**: Try different resolutions, steps, samplers
3. **Build a workflow**: Create reusable workflow for your project
4. **Batch generate**: Use API or manual process for all shots

For complete workflow, see [USAGE.md](USAGE.md).

---

## Step-by-Step: Building Your First Workflow

### Current State: You Have "Load Checkpoint" Node

If you already have a **"Load Checkpoint"** node on the canvas (like in your screenshot), follow these steps to complete the workflow:

### Step 1: Add CLIP Text Encode Nodes

1. **Right-click** on the canvas (anywhere empty)
2. Navigate to: **Add Node** → **conditioning** → **CLIPTextEncode**
3. **Add TWO of these nodes**:
   - One for **positive prompt** (your storyboard description)
   - One for **negative prompt** (what to avoid)

4. **Connect the CLIP output** from "Load Checkpoint" node:
   - Click the **yellow CLIP output** (right side of Load Checkpoint)
   - Drag to the **CLIP input** (left side) of both CLIPTextEncode nodes

### Step 2: Add Empty Latent Image Node

1. **Right-click** canvas → **Add Node** → **latent** → **EmptyLatentImage**
2. **Configure**:
   - **width**: `512`
   - **height**: `512`
   - **batch_size**: `1`

### Step 3: Add KSampler Node

1. **Right-click** canvas → **Add Node** → **sampling** → **KSampler**
2. **Configure KSampler**:
   - **seed**: Leave as random (or set specific number for consistency)
   - **steps**: `20`
   - **cfg_scale**: `7`
   - **sampler_name**: Select `dpmpp_2m` (DPM++ 2M Karras)
   - **scheduler**: `normal` or `karras`

3. **Connect inputs to KSampler**:
   - **MODEL** (purple): From Load Checkpoint → MODEL input (left side of KSampler)
   - **positive** (green): From CLIPTextEncode (positive prompt) → positive input
   - **negative** (red): From CLIPTextEncode (negative prompt) → negative input
   - **latent_image** (grey): From EmptyLatentImage → latent_image input

### Step 4: Add VAE Decode Node

1. **Right-click** canvas → **Add Node** → **latent** → **VAEDecode**
2. **Connect**:
   - **samples** (grey): From KSampler output → samples input
   - **vae** (orange): From Load Checkpoint VAE output (red dot) → vae input

### Step 5: Add Save Image Node

1. **Right-click** canvas → **Add Node** → **image** → **SaveImage**
2. **Connect**:
   - **images** (blue): From VAEDecode output → images input
3. **Configure** (optional):
   - **filename_prefix**: `SH020` (use your shot ID)
   - Images will save to: `outputs/` directory

### Step 6: Enter Your Prompts

1. **Click on the first CLIPTextEncode node** (positive prompt)
2. **In the text field**, paste your prompt from `projects/night_shift/prompts/storyboards.md`
   
   Example (SH020):
   ```
   Medium shot, security operator at console, multiple monitors visible, 
   dim monitor glow lighting, tense mood, control room, 
   surveillance aesthetic, cinematic lighting, high contrast, 
   cool blue color palette, no text, no captions, no logos, 
   professional photography, 4k
   ```

3. **Click on the second CLIPTextEncode node** (negative prompt)
4. **Enter negative prompt**:
   ```
   text, captions, logos, watermark, signature, low quality, blurry, 
   distorted, deformed, bad anatomy, extra limbs, duplicate, 
   watermark, signature, username, artist name
   ```

### Step 7: Generate Your Image

1. **Click the "Queue Prompt" button** (top right, with play icon)
2. **Wait for generation** (10-60 seconds depending on GPU)
3. **Check the "Assets" tab** (left sidebar) → **"Generated"** tab
4. **Your image will appear** - right-click to save or download

### Complete Workflow Structure

```
Load Checkpoint (v1-5-pruned.ckpt)
    ├─ MODEL (purple) ──────────────┐
    ├─ CLIP (yellow) ────┐           │
    └─ VAE (red) ────────┐           │
                         │           │
         CLIPTextEncode (positive)   │
         CLIPTextEncode (negative)   │
         EmptyLatentImage (512x512)  │
                         │           │
                         ▼           ▼
                      KSampler (steps: 20, cfg: 7)
                         │
                         ▼
                      VAEDecode
                         │
                         ▼
                      SaveImage (filename: SH020)
```

### Quick Tips

- **To move nodes**: Click and drag
- **To delete a node**: Select it and press `Delete` key
- **To zoom**: Mouse wheel or bottom-right zoom control
- **To see connections**: Hover over connection lines
- **To copy a node**: Right-click node → Copy, then paste

### Testing Your Workflow

1. **Use a simple prompt first** to test:
   ```
   A simple test image, high quality, professional photography
   ```

2. **If generation works**, replace with your storyboard prompt

3. **Save your workflow** (top right "Save" button) for reuse

### Common Issues

**"No valid connections" error:**
- Make sure you're connecting the right output types (purple=MODEL, yellow=CLIP, etc.)

**Generation fails:**
- Check GPU memory: `docker exec -it comfyui nvidia-smi`
- Reduce resolution to 512x512
- Reduce steps to 15-20

**Image doesn't appear:**
- Check ComfyUI logs: `./app.sh logs comfyui`
- Verify SaveImage node is connected
- Check `outputs/` directory on host

---

## Workflow Templates

### Basic Storyboard Workflow (JSON)

Save this as `basic_storyboard.json` in your project:

```json
{
  "last_node_id": 7,
  "last_link_id": 8,
  "nodes": [
    {
      "id": 1,
      "type": "CheckpointLoaderSimple",
      "pos": [100, 100],
      "size": {"0": 315, "1": 98},
      "flags": {},
      "order": 0,
      "mode": 0,
      "outputs": [
        {"name": "MODEL", "type": "MODEL", "links": [1], "slot_index": 0},
        {"name": "CLIP", "type": "CLIP", "links": [2, 3], "slot_index": 1},
        {"name": "VAE", "type": "VAE", "links": [6], "slot_index": 2}
      ],
      "properties": {},
      "widgets_values": ["v1-5-pruned.ckpt"]
    },
    {
      "id": 2,
      "type": "CLIPTextEncode",
      "pos": [450, 50],
      "size": {"0": 400, "1": 200},
      "flags": {},
      "order": 1,
      "mode": 0,
      "inputs": [
        {"name": "CLIP", "type": "CLIP", "link": 2}
      ],
      "outputs": [
        {"name": "CONDITIONING", "type": "CONDITIONING", "links": [4], "slot_index": 0}
      ],
      "properties": {},
      "widgets_values": ["your prompt here"]
    },
    {
      "id": 3,
      "type": "CLIPTextEncode",
      "pos": [450, 300],
      "size": {"0": 400, "1": 200},
      "flags": {},
      "order": 2,
      "mode": 0,
      "inputs": [
        {"name": "CLIP", "type": "CLIP", "link": 3}
      ],
      "outputs": [
        {"name": "CONDITIONING", "type": "CONDITIONING", "links": [5], "slot_index": 0}
      ],
      "properties": {},
      "widgets_values": ["text, captions, logos, watermark"]
    },
    {
      "id": 4,
      "type": "EmptyLatentImage",
      "pos": [100, 250],
      "size": {"0": 315, "1": 106},
      "flags": {},
      "order": 3,
      "mode": 0,
      "outputs": [
        {"name": "LATENT", "type": "LATENT", "links": [7], "slot_index": 0}
      ],
      "properties": {},
      "widgets_values": [512, 512, 1]
    },
    {
      "id": 5,
      "type": "KSampler",
      "pos": [900, 100],
      "size": {"0": 315, "1": 262},
      "flags": {},
      "order": 4,
      "mode": 0,
      "inputs": [
        {"name": "model", "type": "MODEL", "link": 1},
        {"name": "positive", "type": "CONDITIONING", "link": 4},
        {"name": "negative", "type": "CONDITIONING", "link": 5},
        {"name": "latent_image", "type": "LATENT", "link": 7}
      ],
      "outputs": [
        {"name": "LATENT", "type": "LATENT", "links": [8], "slot_index": 0}
      ],
      "properties": {},
      "widgets_values": [12345, "randomize", 20, 7, "dpmpp_2m", "normal"]
    },
    {
      "id": 6,
      "type": "VAEDecode",
      "pos": [1250, 100],
      "size": {"0": 210, "1": 46},
      "flags": {},
      "order": 5,
      "mode": 0,
      "inputs": [
        {"name": "samples", "type": "LATENT", "link": 8},
        {"name": "vae", "type": "VAE", "link": 6}
      ],
      "outputs": [
        {"name": "IMAGE", "type": "IMAGE", "links": [9], "slot_index": 0}
      ],
      "properties": {}
    },
    {
      "id": 7,
      "type": "SaveImage",
      "pos": [1500, 100],
      "size": {"0": 315, "1": 270},
      "flags": {},
      "order": 6,
      "mode": 0,
      "inputs": [
        {"name": "images", "type": "IMAGE", "link": 9}
      ],
      "properties": {},
      "widgets_values": ["SH020"]
    }
  ],
  "links": [
    [1, 1, 0, 5, 0, "MODEL"],
    [2, 1, 1, 2, 0, "CLIP"],
    [3, 1, 1, 3, 0, "CLIP"],
    [4, 2, 0, 5, 1, "CONDITIONING"],
    [5, 3, 0, 5, 2, "CONDITIONING"],
    [6, 1, 2, 6, 1, "VAE"],
    [7, 4, 0, 5, 3, "LATENT"],
    [8, 5, 0, 6, 0, "LATENT"],
    [9, 6, 0, 7, 0, "IMAGE"]
  ],
  "groups": [],
  "config": {},
  "extra": {},
  "version": 0.4
}
```

**To use this template:**
1. Copy the JSON above
2. Save as `projects/night_shift/workflows/basic_storyboard.json`
3. In ComfyUI, click **"Load"** button (top right)
4. Select the JSON file
5. Update the prompt in the CLIPTextEncode node
6. Click **"Queue Prompt"**

---

## Keyboard Shortcuts

- **Delete**: Remove selected node(s)
- **Ctrl+C / Ctrl+V**: Copy/paste nodes
- **Ctrl+Z / Ctrl+Y**: Undo/redo
- **Space**: Pan canvas (hold and drag)
- **Mouse Wheel**: Zoom in/out
- **F**: Fit all nodes to view
- **Ctrl+F**: Search nodes

---

## Advanced: Batch Generation Script

Create a Python script to generate all storyboards automatically:

```python
#!/usr/bin/env python3
"""
Batch generate storyboards using ComfyUI API
"""
import requests
import json
import re
from pathlib import Path

def extract_prompts(md_file: Path):
    """Extract shot prompts from storyboards.md"""
    prompts = {}
    current_shot = None
    current_prompt = []
    
    with open(md_file) as f:
        for line in f:
            # Match shot ID: ## SH010 - Title
            shot_match = re.match(r'^##\s+(SH\d+)\s*-', line)
            if shot_match:
                if current_shot:
                    prompts[current_shot] = '\n'.join(current_prompt).strip()
                current_shot = shot_match.group(1)
                current_prompt = []
            elif current_shot and line.strip().startswith('```'):
                continue  # Skip code block markers
            elif current_shot and line.strip():
                current_prompt.append(line.strip())
    
    if current_shot:
        prompts[current_shot] = '\n'.join(current_prompt).strip()
    
    return prompts

def queue_prompt(prompt_text: str, shot_id: str, api_url: str = "http://localhost:8188"):
    """Queue a prompt to ComfyUI"""
    # This is a simplified example - full API requires workflow JSON
    # See: https://github.com/comfyanonymous/ComfyUI/wiki/API
    
    workflow = {
        # Your workflow JSON here (from saved workflow)
    }
    
    # Replace prompt in workflow
    # workflow['nodes'][1]['widgets_values'][0] = prompt_text
    
    response = requests.post(f"{api_url}/prompt", json={"prompt": workflow})
    return response.json()

if __name__ == "__main__":
    project = "night_shift"
    prompts_file = Path(f"projects/{project}/prompts/storyboards.md")
    
    prompts = extract_prompts(prompts_file)
    
    print(f"Found {len(prompts)} prompts")
    for shot_id, prompt_text in prompts.items():
        print(f"Generating {shot_id}...")
        # queue_prompt(prompt_text, shot_id)
        print(f"  Prompt: {prompt_text[:50]}...")
```

*(Note: Full API implementation requires the complete workflow JSON structure)*

---

For complete workflow, see [USAGE.md](USAGE.md).
