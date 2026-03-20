# Dockerfile for OmniLottie Worker
# Isolated worker container for video/image/text to Lottie animation

FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Install Python 3.11 and system dependencies
RUN apt-get update && apt-get install -y \
    python3.11 \
    python3.11-venv \
    python3-pip \
    git \
    wget \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Create symlinks for python3
RUN ln -sf /usr/bin/python3.11 /usr/bin/python3 && \
    ln -sf /usr/bin/python3 /usr/bin/python

WORKDIR /app

# Copy shared proto files
COPY worker/shared/ /app/shared/

# Copy worker-specific code
COPY worker/omnilottie_worker/ /app/worker/

# Copy the omnilottie model code (decoder, lottie package, etc.)
COPY omnilottie/ /app/omnilottie/

# Install dependencies
RUN pip3 install --no-cache-dir -r /app/worker/requirements.txt

# Create model cache directory
RUN mkdir -p /models /app/outputs

# Set environment variables
ENV HF_HOME=/models
ENV TRANSFORMERS_CACHE=/models
ENV MODEL_DIR=/models
ENV PYTHONPATH=/app:/app/omnilottie:$PYTHONPATH

CMD ["python3", "/app/worker/main.py"]
