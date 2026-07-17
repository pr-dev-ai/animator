@echo off
setlocal EnableDelayedExpansion

cd /d "%~dp0"

:: ── Load .env ──────────────────────────────────────────────────────────────
if not exist ".env" (
    if exist ".env.example" (
        copy ".env.example" ".env" >nul
        echo Created .env from .env.example
    ) else (
        echo ERROR: .env file not found
        exit /b 1
    )
)

for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
    set "line=%%A"
    if not "!line:~0,1!"=="#" if not "!line!"=="" (
        set "%%A=%%B"
    )
)

if "%ANTHROPIC_API_KEY%"=="" (
    echo.
    echo ERROR: ANTHROPIC_API_KEY is not configured.
    echo Please edit .env and add your Anthropic API key:
    echo   ANTHROPIC_API_KEY=sk-ant-api03-...
    echo.
    pause
    exit /b 1
)

:: ── Limit GPU power to prevent shutdown under peak load ────────────────────
echo Checking GPU power limit...
nvidia-smi -pl 90 >nul 2>&1
if %errorlevel%==0 (
    echo GPU power limit set to 90W to prevent shutdown.
) else (
    echo Note: Could not set GPU power limit ^(nvidia-smi not found or no permission^).
    echo       If the PC shuts down during image generation, run as Administrator.
)

:: ── Python virtual environment ─────────────────────────────────────────────
if not exist ".venv" (
    echo Creating Python virtual environment...
    python -m venv .venv
)

call .venv\Scripts\activate.bat

:: ── Install dependencies ───────────────────────────────────────────────────
echo Checking web UI dependencies...
pip install -q -r web_ui\requirements.txt waitress

echo.
echo Starting Kids Animation Studio...
echo Open your browser at: http://localhost:5000
echo ^(Press Ctrl+C to stop^)
echo.

:: waitress works on Windows; gunicorn does not
waitress-serve --host=0.0.0.0 --port=5000 --threads=16 --channel-timeout=600 web_ui.server:app
