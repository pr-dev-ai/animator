#!/bin/bash
# Download Piper TTS voice models

set -e

PIPER_DIR="$(cd "$(dirname "$0")/.." && pwd)/piper"
MODEL_NAME="${1:-en_US-lessac-medium}"

echo "📥 Downloading Piper TTS voice model: $MODEL_NAME"
echo ""

# Create piper directory if it doesn't exist
mkdir -p "$PIPER_DIR"

# Piper voice models are hosted on Hugging Face
# Format: https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx
# But we'll use the direct GitHub releases approach

# Try Hugging Face first (more reliable)
# Piper needs both .onnx and .onnx.json files
HF_BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium"
HF_MODEL_URL="${HF_BASE}/${MODEL_NAME}.onnx"
HF_CONFIG_URL="${HF_BASE}/${MODEL_NAME}.onnx.json"

echo "Downloading model and config from Hugging Face..."
echo "  Model: ${MODEL_NAME}.onnx"
echo "  Config: ${MODEL_NAME}.onnx.json"
echo ""

# Download both files
if wget -q --show-progress -O "$PIPER_DIR/${MODEL_NAME}.onnx" "$HF_MODEL_URL" 2>&1 && \
   wget -q --show-progress -O "$PIPER_DIR/${MODEL_NAME}.onnx.json" "$HF_CONFIG_URL" 2>&1; then
    echo ""
    echo "✅ Downloaded both files:"
    ls -lh "$PIPER_DIR/${MODEL_NAME}.onnx" "$PIPER_DIR/${MODEL_NAME}.onnx.json"
    echo ""
    echo "Restart services to use the new voice model:"
    echo "  ./app.sh restart"
    exit 0
fi

echo "⚠️  Hugging Face download failed, trying alternative..."
echo ""
echo "Manual download instructions:"
echo "1. Visit: https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_US/lessac/medium"
echo "2. Download: ${MODEL_NAME}.onnx"
echo "3. Place in: $PIPER_DIR/"
echo ""
echo "Or use wget directly:"
echo "  wget -O $PIPER_DIR/${MODEL_NAME}.onnx \\"
echo "    https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/${MODEL_NAME}.onnx"
echo ""

exit 1
