# Kids Animation Studio — Web UI

A browser-based dashboard for creating kids animation videos end-to-end.

## Quick Start

1. Add your Anthropic API key to `.env`:
   ```
   ANTHROPIC_API_KEY=sk-ant-api03-...
   ```

2. Start the UI:
   ```bash
   ./start_ui.sh
   ```

3. Open your browser: http://localhost:5000

## Workflow

1. **Project** tab — Create or select your animation project
2. **Lyrics & Music** tab — Generate song lyrics and chord suggestions with Claude
3. **Storyboards** tab — Generate ComfyUI prompts, then create images in ComfyUI (http://localhost:8188)
4. **Audio** tab — Upload your recorded WAV files (one per shot) and generate lip-sync
5. **Video** tab — Build your animatic MP4

## Requirements

- Python 3.10+
- Running animation pipeline services: `./app.sh start`
- Anthropic API key
- ComfyUI running for storyboard generation (started with `./app.sh start`)
