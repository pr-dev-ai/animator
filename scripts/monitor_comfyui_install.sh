#!/bin/bash
# Monitor ComfyUI installation progress

echo "🔍 Monitoring ComfyUI Installation Progress"
echo "=========================================="
echo ""

while true; do
    # Check if container is running
    if ! docker ps | grep -q comfyui; then
        echo "❌ ComfyUI container stopped!"
        exit 1
    fi
    
    # Check if pip is still running
    if docker exec comfyui pgrep -f "pip3 install" > /dev/null 2>&1; then
        # Check what's installed
        torch_installed=$(docker exec comfyui python3 -c "import torch; print('YES')" 2>&1 | grep -c "YES" || echo "0")
        
        if [ "$torch_installed" = "1" ]; then
            echo "✅ PyTorch installed! Installing remaining packages..."
        else
            echo "⏳ Installing PyTorch (this takes 5-15 minutes, be patient)..."
        fi
        
        # Count installed packages
        pkg_count=$(docker exec comfyui pip3 list 2>/dev/null | wc -l || echo "0")
        echo "   Packages installed: $pkg_count"
    else
        # pip finished, check if ComfyUI is starting
        if curl -s http://localhost:8188 > /dev/null 2>&1; then
            echo ""
            echo "✅ ComfyUI is ready!"
            echo "   Open: http://localhost:8188"
            exit 0
        else
            echo "⏳ Pip finished, ComfyUI starting..."
            sleep 2
        fi
    fi
    
    sleep 5
done
