FROM ubuntu:22.04

# Avoid interactive prompts during apt install
ENV DEBIAN_FRONTEND=noninteractive

# Install dependencies
RUN apt-get update && \
    apt-get install -y \
    wget \
    unzip \
    ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Download and install Rhubarb Lip Sync
RUN wget -q https://github.com/DanielSWolf/rhubarb-lip-sync/releases/download/v1.13.0/rhubarb-lip-sync-1.13.0-linux.zip -O /tmp/rhubarb.zip && \
    unzip -q /tmp/rhubarb.zip -d /tmp && \
    mkdir -p /opt/rhubarb && \
    # Find the extracted directory and copy everything
    EXTRACTED_DIR=$(find /tmp -type d -name "rhubarb-lip-sync-*" | head -1) && \
    if [ -n "$EXTRACTED_DIR" ]; then \
        cp -r "$EXTRACTED_DIR"/* /opt/rhubarb/; \
    else \
        # Fallback: find and copy binary and resources separately
        find /tmp -name "rhubarb" -type f -executable | head -1 | xargs -I {} cp {} /opt/rhubarb/rhubarb; \
        find /tmp -type d -name "res" | head -1 | xargs -I {} cp -r {} /opt/rhubarb/ 2>/dev/null || true; \
    fi && \
    chmod +x /opt/rhubarb/rhubarb && \
    ln -sf /opt/rhubarb/rhubarb /usr/local/bin/rhubarb && \
    rm -rf /tmp/rhubarb.zip /tmp/rhubarb-lip-sync-* && \
    rhubarb --version || echo "Rhubarb installed"

# Set working directory
WORKDIR /voices

# Keep container running for docker exec usage
CMD ["sleep", "infinity"]
