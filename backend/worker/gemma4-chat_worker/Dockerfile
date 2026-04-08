# Gemma 4 26B-A4B Chat Worker
# Builds llama.cpp from source (GitHub) with CUDA for GGUF inference.

FROM nvidia/cuda:12.4.1-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Point the linker at CUDA stubs so libcuda.so.1 resolves at build time.
# (The real libcuda.so.1 comes from the NVIDIA driver at runtime.)
ENV LIBRARY_PATH=/usr/local/cuda/lib64/stubs:${LIBRARY_PATH}

RUN apt-get update && apt-get install -y \
    python3.11 python3.11-dev python3-pip \
    cmake build-essential ninja-build git curl libcurl4-openssl-dev \
    && rm -rf /var/lib/apt/lists/*

RUN ln -sf /usr/bin/python3.11 /usr/bin/python3 && \
    ln -sf /usr/bin/python3 /usr/bin/python

RUN ln -s /usr/local/cuda/lib64/stubs/libcuda.so \
          /usr/local/cuda/lib64/stubs/libcuda.so.1 && \
    echo "/usr/local/cuda/lib64/stubs" > /etc/ld.so.conf.d/cuda-stubs.conf && \
    ldconfig

# Build llama.cpp from source with CUDA (RTX 4090 = sm_89 / Ada Lovelace).
RUN git clone https://github.com/ggml-org/llama.cpp /tmp/llama.cpp && \
    cmake /tmp/llama.cpp -B /tmp/llama.cpp/build \
        -DBUILD_SHARED_LIBS=OFF \
        -DGGML_CUDA=ON \
        -DCMAKE_CUDA_ARCHITECTURES=89 && \
    cmake --build /tmp/llama.cpp/build --config Release -j$(nproc) --target llama-server && \
    cp /tmp/llama.cpp/build/bin/llama-server /usr/local/bin/llama-server && \
    rm -rf /tmp/llama.cpp

# Install Python dependencies
RUN pip3 install --no-cache-dir \
    "huggingface_hub[hf_transfer]>=0.24.0" \
    requests

WORKDIR /app

COPY worker/shared/ /app/shared/
COPY worker/gemma4-chat_worker/ /app/worker/

RUN pip3 install --no-cache-dir -r /app/worker/requirements.txt

RUN mkdir -p /models

ENV HF_HOME=/models
ENV MODEL_DIR=/models
ENV PYTHONPATH=/app:$PYTHONPATH

CMD ["python3", "/app/worker/main.py"]
