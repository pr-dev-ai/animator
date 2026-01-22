#!/bin/bash
# GPU Docker Validation Script
# Validates NVIDIA GPU access inside Docker containers

set -euo pipefail

echo "=== GPU Docker Validation ==="
echo ""

# System Information
echo "--- System Information ---"
echo "OS: $(lsb_release -d 2>/dev/null | cut -f2 || uname -s)"
echo "Kernel: $(uname -r)"
echo ""

# NVIDIA Driver Information
echo "--- NVIDIA Driver (Host) ---"
if command -v nvidia-smi &> /dev/null; then
    nvidia-smi --query-gpu=name,driver_version --format=csv,noheader || echo "nvidia-smi failed"
else
    echo "ERROR: nvidia-smi not found. Install NVIDIA drivers first."
    exit 1
fi
echo ""

# Docker Information
echo "--- Docker Information ---"
if command -v docker &> /dev/null; then
    docker --version || echo "docker --version failed"
else
    echo "ERROR: docker not found. Install Docker first."
    exit 1
fi

if command -v docker &> /dev/null && docker compose version &> /dev/null; then
    docker compose version || echo "docker compose version failed"
else
    echo "WARNING: docker compose not found or not working"
fi
echo ""

# GPU Test in Docker
echo "--- GPU Test in Docker Container ---"
if docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi; then
    echo ""
    echo "✅ SUCCESS: GPU is accessible inside Docker containers"
    exit 0
else
    echo ""
    echo "❌ FAILURE: GPU is not accessible inside Docker containers"
    echo ""
    echo "=== Troubleshooting Steps ==="
    echo ""
    echo "QUICK FIX: Run the automated installation script:"
    echo "   sudo ./scripts/install_nvidia_toolkit.sh"
    echo ""
    echo "OR manually install:"
    echo ""
    echo "1. Install NVIDIA Container Toolkit:"
    echo "   sudo apt update"
    echo "   sudo apt install -y nvidia-container-toolkit"
    echo ""
    echo "2. Configure Docker runtime:"
    echo "   sudo nvidia-ctk runtime configure --runtime=docker"
    echo ""
    echo "3. Restart Docker service:"
    echo "   sudo systemctl restart docker"
    echo ""
    echo "4. Verify user permissions:"
    echo "   - Ensure your user is in the 'docker' group:"
    echo "     sudo usermod -aG docker \$USER"
    echo "     (then log out and log back in)"
    echo ""
    echo "5. Check driver/kernel module:"
    echo "   lsmod | grep nvidia"
    echo "   (should show nvidia modules loaded)"
    echo ""
    echo "6. Re-run this script to verify:"
    echo "   ./scripts/check_gpu_docker.sh"
    echo ""
    exit 1
fi
