# Build stage
FROM golang:1.24-alpine AS builder

WORKDIR /app

# Copy go mod files
COPY go.mod go.sum ./
RUN go mod download

# Copy source code
COPY . .

# Build the application
RUN CGO_ENABLED=0 GOOS=linux go build -a -installsuffix cgo -o orchestrator .

# Runtime stage - Use NVIDIA CUDA base image for nvidia-smi support
FROM nvidia/cuda:12.1.0-base-ubuntu22.04

# Install dependencies
RUN apt-get update && apt-get install -y \
    ca-certificates \
    python3 \
    python3-yaml \
    python3-requests \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Docker CLI
RUN curl -fsSL https://download.docker.com/linux/ubuntu/gpg | apt-key add - && \
    echo "deb [arch=amd64] https://download.docker.com/linux/ubuntu jammy stable" > /etc/apt/sources.list.d/docker.list && \
    apt-get update && \
    apt-get install -y docker-ce-cli && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /root/

# Copy the binary from builder
COPY --from=builder /app/orchestrator .

# Copy migrations directory
COPY --from=builder /app/migrations ./migrations

# Expose the port
EXPOSE 8080

# Run the orchestrator
CMD ["./orchestrator"]
