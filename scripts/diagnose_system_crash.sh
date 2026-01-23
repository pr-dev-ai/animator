#!/bin/bash
# System Crash Diagnostic Script
# Diagnoses why the system restarts when running ComfyUI

set -euo pipefail

echo "=== System Crash Diagnostic ==="
echo ""

# Check system crash logs
echo "--- Recent System Crashes (last 10 minutes) ---"
if command -v journalctl &> /dev/null; then
    journalctl --since "10 minutes ago" --priority=err --no-pager | tail -20 || echo "No recent errors in journal"
else
    echo "journalctl not available"
fi
echo ""

# Check kernel messages
echo "--- Recent Kernel Messages (dmesg) ---"
if [ -r /var/log/dmesg ] || dmesg &> /dev/null; then
    dmesg -T | tail -30 | grep -i -E "(error|crash|panic|gpu|nvidia|thermal|power|oom)" || echo "No relevant kernel messages"
else
    echo "dmesg not accessible (may need sudo)"
fi
echo ""

# Check GPU temperature and power
echo "--- GPU Status (Current) ---"
if command -v nvidia-smi &> /dev/null; then
    nvidia-smi --query-gpu=name,temperature.gpu,power.limit,power.draw,memory.used,memory.total --format=csv,noheader,nounits || echo "nvidia-smi failed"
    echo ""
    echo "GPU Details:"
    nvidia-smi --query-gpu=name,driver_version,temperature.gpu,power.limit,power.draw,clocks.current.graphics,clocks.current.memory --format=csv || echo "nvidia-smi query failed"
else
    echo "ERROR: nvidia-smi not found"
fi
echo ""

# Check system resources
echo "--- System Resources ---"
echo "CPU Temperature (if available):"
if [ -f /sys/class/thermal/thermal_zone0/temp ]; then
    cpu_temp=$(cat /sys/class/thermal/thermal_zone0/temp)
    echo "  CPU: $((cpu_temp / 1000))°C"
else
    echo "  CPU temperature not available"
fi
echo ""

echo "Memory Usage:"
free -h
echo ""

echo "Disk Space:"
df -h / | tail -1
echo ""

# Check power supply info (if available)
echo "--- Power Supply Info (if available) ---"
if [ -f /sys/class/power_supply/BAT0/capacity ]; then
    echo "Battery: $(cat /sys/class/power_supply/BAT0/capacity)%"
fi
if [ -d /sys/class/power_supply/AC ]; then
    echo "AC Power: $(cat /sys/class/power_supply/AC/online 2>/dev/null || echo 'unknown')"
fi
echo ""

# Check Docker container resource usage
echo "--- Docker Container Status ---"
if command -v docker &> /dev/null; then
    docker stats --no-stream --format "table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}" 2>/dev/null || echo "No running containers"
else
    echo "Docker not found"
fi
echo ""

# Recommendations
echo "=== Recommendations ==="
echo ""
echo "If GPU temperature is > 80°C:"
echo "  - Check GPU fans and cooling"
echo "  - Clean dust from GPU/PC"
echo "  - Improve case ventilation"
echo ""
echo "If power draw is near power limit:"
echo "  - Your PSU may be insufficient"
echo "  - Reduce GPU power limit: sudo nvidia-smi -pl <lower_watts>"
echo ""
echo "If you see OOM (Out of Memory) errors:"
echo "  - Reduce image size in ComfyUI (512x512 instead of 1024x1024)"
echo "  - Reduce batch size"
echo "  - Close other applications"
echo ""
echo "If you see driver crashes:"
echo "  - Update NVIDIA drivers"
echo "  - Check driver logs: cat /var/log/nvidia-installer.log"
echo ""
