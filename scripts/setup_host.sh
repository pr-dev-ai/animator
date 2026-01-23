#!/bin/bash
# Setup host environment for running Python scripts

set -e

echo "🔧 Setting up host environment for animation pipeline scripts"
echo ""

# Check if running as root
if [ "$EUID" -eq 0 ]; then 
   echo "⚠️  Don't run this script as root/sudo"
   exit 1
fi

# Get project root directory
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="$PROJECT_ROOT/.venv"

# Install pip and venv if needed
if ! command -v pip3 &> /dev/null || ! python3 -m venv --help &> /dev/null; then
    echo "📦 Installing python3-pip and python3-venv..."
    sudo apt update
    sudo apt install -y python3-pip python3-venv
fi

# Create virtual environment if it doesn't exist
if [ ! -d "$VENV_DIR" ]; then
    echo "📦 Creating Python virtual environment..."
    python3 -m venv "$VENV_DIR"
    echo "✅ Virtual environment created at: $VENV_DIR"
else
    echo "✅ Virtual environment already exists"
fi

# Activate virtual environment and install requirements
echo "📦 Installing Python dependencies in virtual environment..."
source "$VENV_DIR/bin/activate"
pip install --upgrade pip
pip install -r "$PROJECT_ROOT/requirements.txt"
deactivate

# Install FFmpeg if needed
if ! command -v ffmpeg &> /dev/null; then
    echo "📦 Installing FFmpeg (needed for animatic generation)..."
    sudo apt install -y ffmpeg
fi

echo ""
echo "✅ Host environment setup complete!"
echo ""
echo "📝 To use the virtual environment, run:"
echo "   source .venv/bin/activate"
echo ""
echo "Then you can run:"
echo "  python3 scripts/gen_tts.py --project night_shift"
echo "  python3 scripts/gen_lipsync.py --project night_shift"
echo "  python3 scripts/make_dailies.py --project night_shift"
echo ""
echo "Or create a helper script (see README for details)"
echo ""