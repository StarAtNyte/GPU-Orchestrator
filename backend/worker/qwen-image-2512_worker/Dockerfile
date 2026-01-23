# Qwen Image 2512 Fast Worker
FROM nvidia/cuda:12.1.0-cudnn8-devel-ubuntu22.04

WORKDIR /app

# Install Python and dependencies
RUN apt-get update && apt-get install -y \
    python3.11 \
    python3.11-dev \
    python3-pip \
    git \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip
RUN python3.11 -m pip install --upgrade pip

# Copy requirements
COPY worker/qwen-image-2512_worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY worker/qwen-image-2512_worker/main.py .
COPY worker/qwen-image-2512_worker/handler.py .
COPY worker/shared/ shared/

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python3.11 -c "import torch; print('GPU available:', torch.cuda.is_available())"

# Run worker
CMD ["python3.11", "main.py"]
