#!/bin/bash
# Check ComfyUI installation and startup status

echo "=== ComfyUI Status Check ==="
echo ""

# Check container status
echo "Container Status:"
docker compose ps comfyui
echo ""

# Check if pip is still installing
if docker exec comfyui ps aux | grep -q "pip3 install"; then
    echo "⏳ Dependencies are still installing..."
    echo "   This can take 5-10 minutes on first run"
    echo ""
    echo "Recent installation progress:"
    docker logs comfyui 2>&1 | grep -E "(Collecting|Installing|Successfully)" | tail -5
else
    echo "✅ Dependency installation appears complete"
    echo ""
fi

# Check if main.py exists
if docker exec comfyui test -f /app/workspace/ComfyUI/main.py; then
    echo "✅ ComfyUI main.py found"
else
    echo "❌ ComfyUI main.py not found"
fi

# Check critical dependencies
echo ""
echo "Checking critical dependencies:"
for dep in sqlalchemy alembic aiohttp; do
    if docker exec comfyui python3 -c "import $dep" 2>/dev/null; then
        echo "  ✅ $dep"
    else
        echo "  ❌ $dep (missing)"
    fi
done

# Check if ComfyUI is responding
echo ""
echo "HTTP Status:"
if curl -s -o /dev/null -w "%{http_code}" http://localhost:8188 | grep -q "200\|302"; then
    echo "✅ ComfyUI is responding at http://localhost:8188"
    echo ""
    echo "Open in browser: http://localhost:8188"
else
    echo "⏳ ComfyUI is not responding yet"
    echo "   Wait for dependency installation to complete"
    echo ""
    echo "Access URL: http://localhost:8188 (NOT 0.0.0.0:8188)"
fi

echo ""
echo "To monitor logs: docker compose logs -f comfyui"
