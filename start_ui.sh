#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Check .env exists
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo "Created .env from .env.example"
    else
        echo "ERROR: .env file not found"
        exit 1
    fi
fi

# Load .env safely: skip blank lines and comments, strip CR for CRLF files,
# quote values to handle spaces without shell injection.
set +u
while IFS= read -r line || [ -n "$line" ]; do
    # Strip carriage returns (CRLF support)
    line="${line//$'\r'/}"
    # Skip blank lines and comments
    [[ -z "$line" || "$line" == \#* ]] && continue
    # Extract key and value; skip lines without '='
    key="${line%%=*}"
    value="${line#*=}"
    # Only export simple identifiers to avoid injection
    if [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
        export "$key=$value"
    fi
done < .env
set -u

if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
    echo ""
    echo "ERROR: ANTHROPIC_API_KEY is not configured."
    echo ""
    echo "Please edit .env and add your Anthropic API key:"
    echo "  ANTHROPIC_API_KEY=sk-ant-api03-..."
    echo ""
    echo "Get your key at: https://console.anthropic.com/"
    echo ""
    exit 1
fi

# Reject the placeholder value (any key starting with "sk-ant-..." is a placeholder pattern)
if [[ "${ANTHROPIC_API_KEY}" == *"your-key-here"* ]] || [[ "${ANTHROPIC_API_KEY}" == "sk-ant-...your"* ]]; then
    echo ""
    echo "ERROR: ANTHROPIC_API_KEY still contains the placeholder value."
    echo ""
    echo "Please edit .env and replace the placeholder with your real key:"
    echo "  ANTHROPIC_API_KEY=sk-ant-api03-..."
    echo ""
    echo "Get your key at: https://console.anthropic.com/"
    echo ""
    exit 1
fi

# Check python3 is available
if ! command -v python3 &>/dev/null; then
    echo ""
    echo "ERROR: python3 is not installed or not in PATH."
    echo ""
    echo "Install Python 3.10+ and re-run this script."
    echo ""
    exit 1
fi

# Set up virtual environment
if [ ! -d ".venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv .venv
fi

source .venv/bin/activate

# Install web UI dependencies (skip if already up to date)
echo "Checking web UI dependencies..."
pip install -q -r web_ui/requirements.txt

echo ""
echo "Starting Kids Animation Studio..."
echo "Open your browser at: http://localhost:5000"
echo "(Press Ctrl+C to stop)"
echo ""

exec gunicorn \
    --workers 2 \
    --worker-class gthread \
    --threads 8 \
    --timeout 600 \
    --bind 0.0.0.0:5000 \
    --log-level warning \
    "web_ui.server:app"
