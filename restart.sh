#!/usr/bin/env bash
# Restart Docker services (ComfyUI, TTS, Rhubarb) + Flask web UI in one shot.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "--- Stopping Flask web UI ---"
# Kill any running instance of the web UI server
pkill -f "web_ui.server" 2>/dev/null || pkill -f "web_ui/server.py" 2>/dev/null || true
sleep 1

echo "--- Restarting Docker services ---"
if command -v docker &>/dev/null && docker compose version &>/dev/null 2>&1; then
    docker compose restart
    echo "Docker services restarted."
else
    echo "Docker not found — skipping service restart."
fi

echo ""
echo "--- Starting Flask web UI ---"
exec bash "$SCRIPT_DIR/start_ui.sh"
