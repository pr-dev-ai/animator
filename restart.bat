@echo off
cd /d "%~dp0"

echo --- Stopping web UI ---
taskkill /F /FI "IMAGENAME eq waitress-serve.exe" >nul 2>&1
taskkill /F /FI "IMAGENAME eq python.exe" /FI "WINDOWTITLE eq Kids Animation Studio*" >nul 2>&1

echo --- Restarting Docker services ---
docker compose restart
if %errorlevel% neq 0 (
    echo Docker not running or not found — skipping.
)

echo --- Starting web UI ---
call "%~dp0start_ui.bat"
