FROM ubuntu:22.04

# Avoid interactive prompts
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies
RUN apt-get update && \
    apt-get install -y \
    wget \
    curl \
    python3 \
    python3-pip \
    espeak-ng \
    libespeak-ng1 \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install onnxruntime and Piper TTS Python packages
# onnxruntime is required by piper-tts
RUN pip3 install --no-cache-dir \
    onnxruntime \
    piper-tts

# Create directories
RUN mkdir -p /config /voices

# Set working directory
WORKDIR /app

# Default command - keep container running for docker exec usage
CMD ["sleep", "infinity"]
