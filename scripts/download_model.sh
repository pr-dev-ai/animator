#!/bin/bash
# Download Stable Diffusion model for ComfyUI
# Usage: ./scripts/download_model.sh [model_name]

set -e

MODELS_DIR="models/checkpoints"
mkdir -p "$MODELS_DIR"

cd "$(dirname "$0")/.."

MODEL_NAME="${1:-sd15}"

case "$MODEL_NAME" in
    sd15|sd1.5|stable-diffusion-1.5)
        echo "Downloading Stable Diffusion 1.5 Pruned (7.2GB)..."
        echo "This may take 10-30 minutes depending on your connection."
        wget --progress=bar:force \
            "https://huggingface.co/runwayml/stable-diffusion-v1-5/resolve/main/v1-5-pruned.ckpt" \
            -O "$MODELS_DIR/v1-5-pruned.ckpt"
        echo "✅ Download complete: $MODELS_DIR/v1-5-pruned.ckpt"
        ;;
    sd21|sd2.1|stable-diffusion-2.1)
        echo "Downloading Stable Diffusion 2.1 Base (5GB)..."
        wget --progress=bar:force \
            "https://huggingface.co/stabilityai/stable-diffusion-2-1-base/resolve/main/v2-1_512-ema-pruned.safetensors" \
            -O "$MODELS_DIR/sd-2.1-base.safetensors"
        echo "✅ Download complete: $MODELS_DIR/sd-2.1-base.safetensors"
        ;;
    *)
        echo "Usage: $0 [sd15|sd21]"
        echo ""
        echo "Available models:"
        echo "  sd15  - Stable Diffusion 1.5 (7.2GB, recommended for 4GB VRAM)"
        echo "  sd21  - Stable Diffusion 2.1 Base (5GB)"
        echo ""
        echo "Example:"
        echo "  $0 sd15"
        exit 1
        ;;
esac

# Verify download
if [ -f "$MODELS_DIR"/*.ckpt ] || [ -f "$MODELS_DIR"/*.safetensors ]; then
    echo ""
    echo "📦 Downloaded models:"
    ls -lh "$MODELS_DIR"/*.{ckpt,safetensors} 2>/dev/null || true
    echo ""
    echo "✅ Model ready! Restart ComfyUI or refresh the web interface to see it."
else
    echo "❌ Download may have failed. Check the output above."
    exit 1
fi
