@echo off
:: First-time setup checker for Kids Animation Studio on Windows
:: Run this once before start_ui.bat
setlocal

echo ============================================================
echo  Kids Animation Studio — Windows Setup Check
echo ============================================================
echo.

set ERRORS=0

:: ── Python ─────────────────────────────────────────────────────────────────
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [MISSING] Python not found.
    echo   Install Python 3.10+ from https://www.python.org/downloads/
    echo   Make sure to check "Add Python to PATH" during install.
    set /a ERRORS+=1
) else (
    for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo [OK]     %%v
)

:: ── Docker Desktop ─────────────────────────────────────────────────────────
docker --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [MISSING] Docker Desktop not found.
    echo   Install from https://www.docker.com/products/docker-desktop/
    echo   During install: select "Use WSL 2 based engine"
    set /a ERRORS+=1
) else (
    for /f "tokens=*" %%v in ('docker --version 2^>^&1') do echo [OK]     %%v
)

:: ── NVIDIA GPU / nvidia-smi ────────────────────────────────────────────────
nvidia-smi >nul 2>&1
if %errorlevel% neq 0 (
    echo [MISSING] nvidia-smi not found — install latest NVIDIA drivers.
    echo   Download from https://www.nvidia.com/en-us/drivers/
    set /a ERRORS+=1
) else (
    for /f "tokens=*" %%v in ('nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2^>^&1') do echo [OK]     GPU: %%v
)

:: ── WSL2 ───────────────────────────────────────────────────────────────────
wsl --status >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARN]    WSL2 not detected. Docker GPU support requires WSL2.
    echo   Run in PowerShell as Admin: wsl --install
) else (
    echo [OK]     WSL2 available
)

:: ── NVIDIA Container Toolkit in WSL2 ──────────────────────────────────────
wsl nvidia-smi >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARN]    NVIDIA drivers not visible inside WSL2.
    echo   Install NVIDIA Container Toolkit in WSL2:
    echo     wsl
    echo     distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
    echo     curl -s -L https://nvidia.github.io/libnvidia-container/gpgkey ^| sudo apt-key add -
    echo     curl -s -L https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list ^| sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
    echo     sudo apt-get update ^&^& sudo apt-get install -y nvidia-container-toolkit
) else (
    echo [OK]     NVIDIA GPU visible inside WSL2
)

echo.
if %ERRORS%==0 (
    echo All requirements met. Run start_ui.bat to launch.
) else (
    echo %ERRORS% requirement^(s^) missing — fix them then re-run this script.
)
echo.
pause
