#!/bin/bash
# Install and configure NVIDIA Container Toolkit for Docker

set -euo pipefail

echo "=== NVIDIA Container Toolkit Installation ==="
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo "This script must be run with sudo"
    echo "Usage: sudo ./scripts/install_nvidia_toolkit.sh"
    exit 1
fi

# Detect distribution
if [ -f /etc/os-release ]; then
    . /etc/os-release
    DISTRO=$ID
    VERSION=$VERSION_ID
else
    echo "ERROR: Cannot detect distribution"
    exit 1
fi

echo "Detected: $DISTRO $VERSION"
echo ""

# Check if nvidia-smi works
if ! command -v nvidia-smi &> /dev/null; then
    echo "ERROR: nvidia-smi not found. Install NVIDIA drivers first."
    exit 1
fi

echo "✅ NVIDIA drivers detected:"
nvidia-smi --query-gpu=name,driver_version --format=csv,noheader
echo ""

# Add NVIDIA package repositories
echo "--- Adding NVIDIA package repositories ---"

# Use generic .deb repository (works for Ubuntu and Debian)
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

# Use stable generic .deb repository (compatible with Ubuntu 24.04)
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
    sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
    tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

echo "✅ Repository added"
echo ""

# Update package list
echo "--- Updating package list ---"
apt-get update
echo "✅ Package list updated"
echo ""

# Install NVIDIA Container Toolkit
echo "--- Installing NVIDIA Container Toolkit ---"
apt-get install -y nvidia-container-toolkit
echo "✅ NVIDIA Container Toolkit installed"
echo ""

# Configure Docker runtime
echo "--- Configuring Docker runtime ---"
nvidia-ctk runtime configure --runtime=docker
echo "✅ Docker runtime configured"
echo ""

# Restart Docker
echo "--- Restarting Docker service ---"
systemctl restart docker
echo "✅ Docker service restarted"
echo ""

# Verify installation
echo "--- Verifying installation ---"
if docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi &> /dev/null; then
    echo "✅ SUCCESS: GPU is now accessible in Docker containers!"
    echo ""
    echo "Test output:"
    docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi --query-gpu=name,driver_version --format=csv,noheader
    echo ""
    echo "You can now run: ./scripts/check_gpu_docker.sh"
else
    echo "⚠️  WARNING: Installation completed but GPU test failed"
    echo "   Try running: ./scripts/check_gpu_docker.sh"
    echo "   If it still fails, you may need to log out and log back in"
fi
