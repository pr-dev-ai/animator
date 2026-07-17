# Kids Animation Studio — Windows Setup Prompt for Claude Code

> **How to use:** Open Claude Code on the Windows machine, paste the contents of this file as your prompt, and let it run. It will set up and launch the entire application autonomously.

---

## YOUR TASK

You are setting up the **Kids Animation Studio** web application on this Windows machine. The app uses:
- A **Flask web UI** (Python) served by `waitress` on port 5000
- **ComfyUI** running in Docker for AI image generation (needs NVIDIA GPU)
- **Claude API** (Anthropic) for lyrics, chords, and storyboard prompts

Work through every phase below in order. Use PowerShell commands. Fix any errors you encounter before moving to the next phase. At the end, the browser should open to a working UI at `http://localhost:5000`.

---

## PHASE 1 — Check Prerequisites

Run each check. If something is missing, install it before continuing.

### 1.1 Python
```powershell
python --version
```
- Need Python 3.10 or newer.
- If missing: download from https://www.python.org/downloads/ and install with "Add Python to PATH" checked. Then reopen the terminal.

### 1.2 Git
```powershell
git --version
```
- If missing: download from https://git-scm.com/download/win and install.

### 1.3 Docker Desktop
```powershell
docker --version
docker compose version
```
- If missing: install Docker Desktop from https://www.docker.com/products/docker-desktop/
  - During install, select **"Use WSL 2 based engine"**
  - After install, open Docker Desktop and wait for it to finish starting (whale icon in taskbar turns solid)

### 1.4 NVIDIA GPU and drivers
```powershell
nvidia-smi
```
- Should show GPU name and driver version.
- If missing: install latest NVIDIA Game Ready or Studio drivers from https://www.nvidia.com/en-us/drivers/

### 1.5 WSL2 (needed for Docker GPU passthrough)
```powershell
wsl --status
```
- If not installed, run in an **Administrator** PowerShell:
```powershell
wsl --install
# Restart the machine, then continue
```

### 1.6 NVIDIA GPU visible inside WSL2
```powershell
wsl nvidia-smi
```
- If this fails, the NVIDIA Container Toolkit is missing inside WSL2. Run:
```powershell
wsl -e bash -c "
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
    sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
    sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
  sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
  sudo nvidia-ctk runtime configure --runtime=docker
  sudo service docker restart
"
```

---

## PHASE 2 — Get the Code

### 2.1 Clone the repository
```powershell
cd $env:USERPROFILE
git clone https://github.com/pr-dev-ai/animator.git
cd animator
```

If the folder already exists, just pull the latest:
```powershell
cd $env:USERPROFILE\animator
git pull origin dev
git checkout dev
```

### 2.2 Confirm you are on the `dev` branch
```powershell
git branch --show-current
```
Should print `dev`.

---

## PHASE 3 — Configure API Key

### 3.1 Create .env file
```powershell
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }
```

### 3.2 Open .env and add the Anthropic API key
```powershell
notepad .env
```
Set:
```
ANTHROPIC_API_KEY=sk-ant-api03-YOUR_REAL_KEY_HERE
```
Save and close Notepad.

### 3.3 Verify key is present
```powershell
Select-String "ANTHROPIC_API_KEY" .env
```
Should show a line with your key (not the placeholder).

---

## PHASE 4 — Python Virtual Environment

### 4.1 Create the venv (only needed once)
```powershell
python -m venv .venv
```

### 4.2 Activate it
```powershell
.\.venv\Scripts\Activate.ps1
```
If PowerShell blocks scripts, run first:
```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```
Then re-run the activate command.

### 4.3 Install Python dependencies
```powershell
pip install -r web_ui\requirements.txt waitress
```
Confirm the key packages installed:
```powershell
pip show flask anthropic waitress numpy
```

---

## PHASE 5 — GPU Power Limit

This prevents the PC from shutting down under peak GPU load during image generation.

Run in an **Administrator** PowerShell (right-click PowerShell → "Run as Administrator"):
```powershell
nvidia-smi -pl 90
```
Expected output: `Power limit for GPU ... set to 90.00 W ...`

If you see "Insufficient Permissions", you must run as Administrator. If you see "Invalid argument", your GPU's minimum power limit is higher than 90W — try 100W or 110W instead.

---

## PHASE 6 — Docker Services (ComfyUI + TTS + Rhubarb)

### 6.1 Make sure Docker Desktop is running
Look for the Docker whale icon in the Windows system tray. If it's not there, open Docker Desktop from the Start menu and wait ~60 seconds.

### 6.2 Start all services
```powershell
docker compose up -d
```
This builds containers on first run — it can take 5–15 minutes. Watch for errors.

### 6.3 Wait for ComfyUI to be ready
```powershell
# Poll until ComfyUI responds (up to 3 minutes)
$timeout = 180
$start = Get-Date
do {
    Start-Sleep -Seconds 5
    try {
        $r = Invoke-WebRequest -Uri "http://localhost:8188" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        Write-Host "ComfyUI is up!"
        break
    } catch {
        $elapsed = ((Get-Date) - $start).TotalSeconds
        Write-Host "Waiting for ComfyUI... ($([int]$elapsed)s)"
        if ($elapsed -gt $timeout) { Write-Host "Timed out — check: docker compose logs comfyui"; break }
    }
} while ($true)
```

### 6.4 If ComfyUI fails to start, check logs
```powershell
docker compose logs comfyui --tail 50
```
Common fixes:
- **"No module named 'requests'"** → `docker exec comfyui pip install requests`
- **"CUDA not available"** → GPU passthrough not working; verify Phase 1.6
- **"header too small"** → a model file is 0 bytes; check `models\checkpoints\` folder

---

## PHASE 7 — Download the SD 1.5 Model (if not present)

### 7.1 Check if a checkpoint exists
```powershell
Get-ChildItem models\checkpoints\ -ErrorAction SilentlyContinue
```
If the folder is empty or missing any `.safetensors` or `.ckpt` files:

### 7.2 Download SD 1.5 (pruned, ~4GB)
```powershell
# Create folder if needed
New-Item -ItemType Directory -Force -Path models\checkpoints

# Download using PowerShell (this will take several minutes on a normal connection)
$url = "https://huggingface.co/runwayml/stable-diffusion-v1-5/resolve/main/v1-5-pruned.ckpt"
$dest = "models\checkpoints\v1-5-pruned.ckpt"
Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
```
After download, restart ComfyUI:
```powershell
docker compose restart comfyui
```

---

## PHASE 8 — Launch the Web UI

Make sure the `.venv` is still active (you should see `(.venv)` in the prompt). If not:
```powershell
.\.venv\Scripts\Activate.ps1
```

Load the environment variables:
```powershell
Get-Content .env | ForEach-Object {
    if ($_ -match "^([^#][^=]*)=(.*)$") {
        [System.Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim(), "Process")
    }
}
```

Start the server:
```powershell
waitress-serve --host=0.0.0.0 --port=5000 --threads=16 --channel-timeout=600 web_ui.server:app
```

Leave this terminal open — it runs the server. Open a browser and go to:
```
http://localhost:5000
```

---

## PHASE 9 — Verify Everything Works

Check each of these in the browser:

1. **Sidebar shows 5 tabs**: Project, Lyrics & Music, Storyboards, Audio, Video
2. **Project tab**: Create a test project named `test_win` — it should appear in the dropdown
3. **Lyrics tab**: Enter theme "Animals at the park", click "Generate with Claude" — lyrics should appear within 10 seconds
4. **Storyboards tab**: After generating lyrics, click "Generate Prompts with Claude" — scene prompts should appear (not generic placeholders)
5. **ComfyUI link**: The "Open ComfyUI →" button should open `http://localhost:8188`
6. **Generate All Images**: Click it and watch the log — should say "Queued" and then download images

If any step fails, read the error in the browser or terminal and fix it before marking done.

---

## PHASE 10 — Make It Start Automatically (Optional)

To launch the app with a single double-click going forward, use the provided batch file:

```powershell
# Right-click start_ui.bat → "Run as administrator" for GPU power limit to work
```

Or create a desktop shortcut:
```powershell
$WScript = New-Object -ComObject WScript.Shell
$shortcut = $WScript.CreateShortcut("$env:USERPROFILE\Desktop\Animation Studio.lnk")
$shortcut.TargetPath = "$env:USERPROFILE\animator\start_ui.bat"
$shortcut.WorkingDirectory = "$env:USERPROFILE\animator"
$shortcut.Save()
```

---

## TROUBLESHOOTING QUICK REFERENCE

| Symptom | Fix |
|---|---|
| PC shuts down during image gen | Run `nvidia-smi -pl 90` as Administrator before starting |
| "waitress-serve not found" | Run `pip install waitress` in the activated venv |
| ComfyUI at 8188 not reachable | `docker compose up -d` then wait 2 min |
| "ANTHROPIC_API_KEY not set" | Edit `.env`, add the key, restart the server |
| Images always fail / timeout | Check `docker compose logs comfyui` for GPU errors |
| Prompts are generic (not lyrics-based) | Generate lyrics first, then generate prompts |
| Browser shows old content | Hard refresh: Ctrl+Shift+R |

---

## IMPORTANT NOTES FOR CLAUDE CODE

- Always activate `.venv` before running Python or pip commands
- Use `waitress-serve` not `gunicorn` — gunicorn does not run on Windows
- The repo branch is `dev` — always pull from and work on `dev`
- GPU power limit (`nvidia-smi -pl 90`) must be run as Administrator
- The app binds to `0.0.0.0:5000` so it is accessible from other devices on the same Wi-Fi at `http://192.168.29.73:5000`
- ComfyUI takes 1–3 minutes to start after `docker compose up -d` — be patient before declaring it broken
