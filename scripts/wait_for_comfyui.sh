#!/bin/bash
# Wait for ComfyUI to be ready

echo "Waiting for ComfyUI to start..."
echo "This can take 5-10 minutes on first start (installing dependencies)"
echo ""

max_wait=600  # 10 minutes
elapsed=0
interval=5

while [ $elapsed -lt $max_wait ]; do
    if curl -s http://localhost:8188 > /dev/null 2>&1; then
        echo ""
        echo "✅ ComfyUI is ready!"
        echo "   Open: http://localhost:8188"
        exit 0
    fi
    
    # Check if container is still running
    if ! docker ps | grep -q comfyui; then
        echo ""
        echo "❌ ComfyUI container stopped. Check logs:"
        echo "   docker logs comfyui"
        exit 1
    fi
    
    # Show progress
    printf "\r⏳ Waiting... (%d/%d seconds) - Checking dependencies installation" $elapsed $max_wait
    
    sleep $interval
    elapsed=$((elapsed + interval))
done

echo ""
echo "⏱️  Timeout: ComfyUI didn't start within 10 minutes"
echo "   Check logs: docker logs comfyui"
echo "   Or view logs: ./app.sh logs"
exit 1
