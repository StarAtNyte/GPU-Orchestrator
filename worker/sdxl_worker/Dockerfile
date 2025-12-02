# SDXL Worker Dockerfile
#
# This creates a containerized environment for SDXL image generation.
#
# Build: docker build -t sdxl-worker:latest -f sdxl_worker/Dockerfile .
# Run:   docker run --gpus all -e REDIS_HOST=host.docker.internal sdxl-worker:latest

# Use NVIDIA CUDA base image for GPU support
# This includes CUDA toolkit and cuDNN libraries
FROM nvidia/cuda:12.1.0-runtime-ubuntu22.04

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Install Python 3.11 and system dependencies
RUN apt-get update && apt-get install -y \
    python3.11 \
    python3.11-venv \
    python3.11-dev \
    python3-pip \
    git \
    && rm -rf /var/lib/apt/lists/*

# Set Python 3.11 as default
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1

# Create working directory
WORKDIR /app

# Copy shared utilities first (for Docker layer caching)
COPY worker/shared/ /app/shared/

# Copy SDXL worker code
COPY worker/sdxl_worker/ /app/sdxl_worker/

# Install Python dependencies
# Doing this separately allows Docker to cache this layer
RUN pip3 install --no-cache-dir -r sdxl_worker/requirements.txt

# Create output directory for generated images
RUN mkdir -p /tmp/gpu-orchestrator/results

# Environment variables (can be overridden at runtime)
ENV REDIS_HOST=localhost
ENV WORKER_ID=sdxl-worker-1
ENV PYTHONUNBUFFERED=1

# Health check: Verify the worker can import dependencies
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python3 -c "import torch; import diffusers; print('OK')" || exit 1

# Run the worker
CMD ["python3", "sdxl_worker/main.py"]
