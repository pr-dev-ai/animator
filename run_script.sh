#!/bin/bash
# Helper script to run Python scripts with virtual environment activated

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

# Check if virtual environment exists
if [ ! -d "$VENV_DIR" ]; then
    echo "❌ Virtual environment not found!"
    echo "   Run: ./scripts/setup_host.sh"
    exit 1
fi

# Activate virtual environment and run the command
source "$VENV_DIR/bin/activate"

# Run the provided command
exec "$@"
