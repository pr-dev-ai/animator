#!/bin/bash
# Stop all Docker containers except animator app containers

set -euo pipefail

echo "Stopping all containers except animator app (comfyui, piper, rhubarb)..."
echo ""

# Get container IDs excluding animator containers
CONTAINERS=$(docker ps --format "{{.ID}} {{.Names}}" | grep -v -E "(comfyui|piper|rhubarb)" | awk '{print $1}')

if [ -z "$CONTAINERS" ]; then
    echo "✅ No other containers to stop"
    exit 0
fi

# Count containers to stop
COUNT=$(echo "$CONTAINERS" | wc -l)
echo "Found $COUNT container(s) to stop:"
docker ps --format "{{.Names}}" | grep -v -E "(comfyui|piper|rhubarb)"
echo ""

# Stop containers
echo "$CONTAINERS" | xargs -r docker stop

echo ""
echo "✅ Stopped all other containers"
echo ""
echo "Remaining containers:"
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
