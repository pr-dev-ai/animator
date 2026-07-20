<#
  restart.ps1 - Clear caches and restart the Kids Animation Studio server.

  Usage (from the app root):
    powershell -ExecutionPolicy Bypass -File .\restart.ps1          # clear caches + restart
    powershell -ExecutionPolicy Bypass -File .\restart.ps1 -Full    # also wipe rigs + plates (slow to rebuild)

  What "cache" means here:
    outputs/_puppet_build/  per-scene render cache (hashed) -> the main stale-render cache
    outputs/fx/             procedural FX sprites (cheap, regenerated on demand)
    web_ui|scripts __pycache__/  app Python bytecode (so edited code is picked up fresh)
  With -Full it ALSO clears:
    outputs/char_lib/*__indian/  character rigs   (regenerated via ComfyUI - slow)
    outputs/bg_plates/*.png      background plates (regenerated via ComfyUI - slow)
#>
param([switch]$Full)

$ErrorActionPreference = 'SilentlyContinue'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
Write-Host "== Kids Animation Studio: clear cache + restart =="
Write-Host "root: $root"

# 1) Stop the running server(s)
Write-Host "`n[1/3] Stopping server..."
$procs = Get-CimInstance Win32_Process -Filter "name='python.exe'" |
    Where-Object { $_.CommandLine -match 'waitress' -or $_.CommandLine -match 'server:app' }
if ($procs) { $procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; Write-Host "  stopped $($procs.Count) server process(es)" }
else { Write-Host "  no server running" }
Start-Sleep -Seconds 1

# 2) Clear caches
Write-Host "`n[2/3] Clearing caches..."
foreach ($p in @("outputs\_puppet_build", "outputs\fx")) {
    if (Test-Path $p) { Remove-Item -Recurse -Force $p; Write-Host "  removed $p" }
}
# only the APP's bytecode (never .venv - clearing that forces a slow full recompile)
$pyc = @("web_ui", "scripts") | ForEach-Object {
    Get-ChildItem $_ -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue
}
if ($pyc) { $pyc | Remove-Item -Recurse -Force; Write-Host "  removed $($pyc.Count) app __pycache__ dir(s)" }

if ($Full) {
    Write-Host "  -Full: clearing character rigs + background plates (these regenerate via ComfyUI)..."
    Get-ChildItem "outputs\char_lib" -Directory -Filter "*__indian" -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force
    Remove-Item -Force "outputs\bg_plates\*.png" -ErrorAction SilentlyContinue
    Write-Host "  rigs + plates cleared"
}

# 3) Restart the server (detached) and health-check
Write-Host "`n[3/3] Starting server on http://localhost:5000 ..."
$waitress = Join-Path $root ".venv\Scripts\waitress-serve.exe"
if (-not (Test-Path $waitress)) { Write-Host "  ERROR: $waitress not found"; exit 1 }
Start-Process -FilePath $waitress -ArgumentList "--host=0.0.0.0", "--port=5000", "web_ui.server:app" -WorkingDirectory $root -WindowStyle Hidden

$up = $false
for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Seconds 1
    try { Invoke-WebRequest -Uri "http://localhost:5000/api/health" -TimeoutSec 3 -UseBasicParsing | Out-Null; $up = $true; break } catch {}
}
if ($up) { Write-Host "`nServer is UP -> http://localhost:5000" }
else { Write-Host "`nServer started but health check timed out - give it a few more seconds." }
