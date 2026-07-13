FROM nvidia/cuda:12.1.0-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
    apt-get install -y python3 python3-pip git wget curl rsync && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Launcher lives at /app (outside ComfyUI dir) so stdlib loads before
# ComfyUI's types.py can shadow it. See launch_comfyui.py for details.
COPY launch_comfyui.py /app/launch_comfyui.py

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8188
ENTRYPOINT ["/entrypoint.sh"]
