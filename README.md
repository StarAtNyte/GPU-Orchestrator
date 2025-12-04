# GPU Orchestrator

A GPU job orchestration system for AI/ML workloads with isolated workers, **dynamic worker management**, hybrid cloud support, and modern web UIs.

## What is This?

GPU Orchestrator is a distributed system that:
- **Routes AI/ML jobs** to local GPU workers or cloud providers (Modal, Replicate)
- **Isolates dependencies** - each model runs in its own container (no version conflicts!)
- **Dynamically manages GPU access** - automatically switches workers to avoid OOM errors
- **Scales horizontally** - run multiple workers per model type
- **Provides web UIs** - modern frontends for each application
- **Tracks jobs** - PostgreSQL for persistence, Redis for queuing

Perfect for:
- Running multiple AI models on shared GPU infrastructure with limited VRAM
- Building AI-powered web applications
- Mixing local GPUs with cloud burst capacity
- Research labs with diverse model requirements
- Single GPU setups with multiple large models (Z-Image, SDXL, LLaMA, etc.)

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      User Browser                           │
│              http://localhost:7861 (SDXL UI)                │
└────────────────────────┬────────────────────────────────────┘
                         │
                         │ HTTP Requests
                         ↓
┌─────────────────────────────────────────────────────────────┐
│                 Frontend Layer (No GPU)                     │
│  frontends/                                                 │
│    ├── sdxl-ui/          Modern web UI for image gen       │
│    ├── whisper-ui/       Speech-to-text interface          │
│    └── llama-chat/       Chat interface for LLMs           │
└────────────────────────┬────────────────────────────────────┘
                         │
                         │ Submit jobs via HTTP
                         ↓
┌─────────────────────────────────────────────────────────────┐
│              Orchestrator (Go) - Port 8080                  │
│  • Routes jobs to appropriate workers                       │
│  • Proxies cloud endpoints (Modal, Replicate)              │
│  • Manages app registry (config/apps.yaml)                 │
│  • Tracks status in PostgreSQL                             │
└────────────────────────┬────────────────────────────────────┘
                         │
                         │ Redis Streams (local) or HTTP (cloud)
                         │
        ┌────────────────┼────────────────┬──────────────────┐
        │                │                │                  │
        ↓                ↓                ↓                  ↓
┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ SDXL Worker  │  │Whisper Worker│  │ LLaMA Worker │  │ Modal Cloud  │
│ (Local GPU)  │  │  (CPU-only)  │  │ (Local GPU)  │  │  (Remote)    │
│              │  │              │  │              │  │              │
│ PyTorch 2.0  │  │ Whisper 2.2  │  │ vLLM 0.3.0   │  │ Serverless   │
│ CUDA 11.8    │  │ No GPU       │  │ CUDA 12.1    │  │ Auto-scale   │
│ Diffusers    │  │ ffmpeg       │  │ Llama 3 8B   │  │              │
└──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘
```

### Key Components

1. **Frontends** (`frontends/`) - Lightweight web UIs (FastAPI)
   - No GPU required, just serve web pages
   - Talk to orchestrator behind the scenes
   - Each app gets custom UI tailored to its needs

2. **Orchestrator** (`orchestrator/`) - Central router (Go)
   - Multi-backend: routes to local workers or cloud APIs
   - App registry from YAML config
   - Job tracking in PostgreSQL
   - Worker health monitoring via etcd

3. **Workers** (`worker/`) - Isolated GPU containers (Python)
   - Each model in separate container
   - No dependency conflicts between apps
   - Auto-scaling via consumer groups
   - Shared utilities in `worker/shared/`

4. **Infrastructure**
   - **Redis** - Job queues (streams) for local workers
   - **PostgreSQL** - Job status and results
   - **etcd** - Worker registration and health
   - **Docker** - Container isolation

## Quick Start

### Prerequisites

- Docker & Docker Compose
- NVIDIA GPU with drivers (for local workers)
- NVIDIA Container Toolkit

### 1. Clone and Configure

```bash
git clone <repo>
cd gpu-orchestrator

# Create environment file
cp .env.example .env
# Edit .env with your database credentials
```

### 2. Start Infrastructure

```bash
# Start core services
docker compose up -d postgres redis etcd orchestrator

# Check status
docker compose ps
```

### 3. Start Workers

```bash
# Start SDXL image generation worker
docker compose up -d sdxl-worker

# Start Whisper speech-to-text worker
docker compose up -d whisper-worker

# Check worker registration
curl http://localhost:8080/workers
```

### 4. Start Frontend

```bash
# Start SDXL web UI
docker compose up -d sdxl-ui

# Access at http://localhost:7861
```

### 5. Test the System

```bash
# Submit a job via API
curl -X POST http://localhost:8080/submit \
  -H "Content-Type: application/json" \
  -d '{
    "app_id": "sdxl-image-gen",
    "params": {
      "prompt": "A serene mountain landscape at sunset",
      "width": "1024",
      "height": "1024",
      "num_inference_steps": "30"
    }
  }'

# Returns: {"job_id": "uuid", "status": "queued"}

# Check status
curl http://localhost:8080/status/<job-id>
```

## Project Structure

```
gpu-orchestrator/
├── frontends/                    # Web UIs (lightweight, no GPU)
│   ├── sdxl-ui/                 # SDXL image generation UI
│   │   ├── app.py               # FastAPI backend
│   │   ├── templates/           # HTML templates
│   │   ├── static/              # CSS, JavaScript
│   │   ├── Dockerfile           # Container definition
│   │   └── requirements.txt     # Python dependencies
│   └── [other UIs]/
│
├── orchestrator/                 # Central router (Go)
│   ├── main.go                  # HTTP server & routing logic
│   ├── config.go                # YAML config loader
│   ├── Dockerfile               # Container definition
│   └── go.mod                   # Go dependencies
│
├── worker/                       # GPU workers (isolated)
│   ├── shared/                  # Common utilities
│   │   ├── redis_client.py      # Redis stream consumer
│   │   ├── db.py                # PostgreSQL client
│   │   ├── etcd_client.py       # Service discovery
│   │   └── worker_pb2.py        # Protobuf messages
│   │
│   ├── sdxl-image-gen_worker/   # SDXL isolated worker
│   │   ├── main.py              # Worker loop
│   │   ├── handler.py           # SDXL inference logic
│   │   ├── requirements.txt     # PyTorch, diffusers, etc.
│   │   └── Dockerfile           # CUDA 11.8 base
│   │
│   ├── whisper-stt_worker/      # Whisper isolated worker
│   │   ├── main.py              # Worker loop
│   │   ├── handler.py           # Whisper inference
│   │   ├── requirements.txt     # Whisper, ffmpeg
│   │   └── Dockerfile           # CPU-only base
│   │
│   └── [other workers]/
│
├── worker_template/              # Templates for new workers
│   ├── Dockerfile.template
│   ├── main.py.template
│   ├── handler.py.template
│   └── requirements.txt.template
│
├── config/
│   └── apps.yaml                # App registry (loaded by orchestrator)
│
├── docker compose.yml           # Service orchestration
├── create_new_app.sh            # Generator script
└── README.md                    # This file
```

## Adding New Applications

Use the generator script to create new workers or frontends:

```bash
# Create isolated local GPU worker
./create_new_app.sh my-model local

# Create cloud worker (Modal)
./create_new_app.sh my-model modal

# Create frontend UI
./create_new_app.sh my-model-ui frontend
```

Each generates all necessary files with templates. See [ADDING_NEW_APPS.md](ADDING_NEW_APPS.md) for detailed guide.

### Example: Adding Whisper STT

```bash
# 1. Generate worker template
./create_new_app.sh whisper-stt local

# 2. Edit handler.py to load Whisper model
# 3. Add dependencies to requirements.txt
# 4. Add to docker compose.yml
# 5. Build and start

docker compose build whisper-worker
docker compose up -d whisper-worker
```

## Monitoring

### View Active Workers

```bash
curl http://localhost:8080/workers
```

Returns:
```json
{
  "count": 3,
  "workers": [
    {
      "worker_id": "sdxl-worker-1",
      "app_id": "sdxl-image-gen",
      "status": "idle",
      "last_heartbeat": "2024-01-15T10:30:00Z"
    }
  ]
}
```

### Check Job Status

```bash
curl http://localhost:8080/status/<job-id>
```

### View Logs

```bash
# Orchestrator logs
docker compose logs -f orchestrator

# Worker logs
docker compose logs -f sdxl-worker

# All services
docker compose logs -f
```

### Database Queries

```bash
# Connect to PostgreSQL
docker exec -it postgres psql -U orchestrator -d orchestrator

# View recent jobs
SELECT job_id, app_id, status, created_at
FROM jobs
ORDER BY created_at DESC
LIMIT 10;

# View job queue depth
SELECT app_id, status, COUNT(*)
FROM jobs
GROUP BY app_id, status;
```

## Available Applications

### SDXL Image Generation

**Worker**: `worker/sdxl-image-gen_worker/`
**Frontend**: `frontends/sdxl-ui/` (Port 7861)
**GPU**: Required (8GB VRAM)

Generate high-quality images from text prompts.

### Whisper Speech-to-Text

**Worker**: `worker/whisper-stt_worker/`
**GPU**: Optional (CPU mode available)

Transcribe audio files to text.

### LLaMA 3 Chat

**Worker**: `worker/llama3-chat_worker/`
**GPU**: Required (24GB VRAM for 8B model)

Conversational AI using Meta's LLaMA 3.

## Configuration

### Environment Variables

Create `.env` file:

```bash
# PostgreSQL
POSTGRES_USER=orchestrator
POSTGRES_PASSWORD=your-secure-password
POSTGRES_DB=orchestrator

# Redis
REDIS_HOST=redis
REDIS_PORT=6379

# etcd
ETCD_HOST=etcd
ETCD_PORT=2379

# HuggingFace (for gated models)
HF_TOKEN=your-hf-token
```

### App Registry

Edit `config/apps.yaml` to add/modify applications:

```yaml
apps:
  - id: "sdxl-image-gen"
    name: "SDXL Image Generator"
    type: "local"              # local or modal
    queue: "jobs:sdxl"         # Redis stream name
    description: "Generate images with Stable Diffusion XL"
    gpu_vram_gb: 12
    docker_image: "gpu-orchestrator-sdxl-worker:latest"
    parameters:
      - name: "prompt"
        type: "string"
        required: true
```

## Scaling

### Horizontal Scaling (Multiple Workers)

```bash
# Scale SDXL workers to 3 instances
docker compose up -d --scale sdxl-worker=3

# Each worker joins the same consumer group
# Jobs are load-balanced automatically
```

### GPU Assignment

Pin workers to specific GPUs:

```yaml
# docker compose.yml
sdxl-worker-gpu0:
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            device_ids: ['0']

sdxl-worker-gpu1:
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            device_ids: ['1']
```

### Hybrid Cloud

Mix local and cloud workers:

```yaml
# config/apps.yaml
apps:
  - id: "sdxl-local"
    type: "local"
    queue: "jobs:sdxl"

  - id: "sdxl-cloud"
    type: "modal"
    endpoint: "https://your-org--sdxl.modal.run/process"
```

## Deploying Code Changes

After modifying worker code, handler logic, or frontend files, you need to rebuild and restart the affected services.

### Rebuilding a Worker

```bash
# Stop the worker
docker compose stop z-image-worker

# Rebuild with latest code
docker compose build z-image-worker

# Start with new image
docker compose up -d z-image-worker

# Or do all in one command:
docker compose up -d --build z-image-worker
```

### Rebuilding the Orchestrator

```bash
# After changing orchestrator Go code
docker compose stop orchestrator
docker compose build orchestrator
docker compose up -d orchestrator
```

### Rebuilding a Frontend

```bash
# After changing UI code
docker compose restart z-image-ui

# Or rebuild if you changed Dockerfile/requirements
docker compose up -d --build z-image-ui
```

### Rebuilding Everything

```bash
# Nuclear option - rebuild all services
docker compose build
docker compose up -d

# Or rebuild specific services
docker compose up -d --build orchestrator z-image-worker z-image-ui
```

### Common Gotchas

**Container is running old code:**
- Running `docker compose up -d` after building won't restart containers
- Use `docker compose up -d --force-recreate` to force restart
- Or explicitly restart: `docker compose restart service-name`

**Build cache issues:**
- Use `--no-cache` to force clean build: `docker compose build --no-cache`
- Useful when dependencies change or builds behave unexpectedly

**Template changes:**
- Changes to `worker_template/` only affect NEW workers created after the change
- Existing workers need manual updates to their code

### Quick Reference

```bash
# Change worker code → rebuild worker
docker compose up -d --build z-image-worker

# Change orchestrator code → rebuild orchestrator
docker compose up -d --build orchestrator

# Change frontend code → restart frontend (fast)
docker compose restart z-image-ui

# Change frontend Dockerfile → rebuild frontend
docker compose up -d --build z-image-ui

# Change config/apps.yaml → restart orchestrator
docker compose restart orchestrator

# Change .env → restart affected services
docker compose up -d
```

## Dynamic Worker Management (NEW!)

For GPUs with limited VRAM, the orchestrator can automatically switch between workers:

```bash
# Check worker status
./scripts/worker_manager.py status

# Submit job (auto-switches workers)
./scripts/submit_job.sh sdxl-image-gen '{"prompt": "A cat"}'

# Test automatic switching
./scripts/test_dynamic_switching.sh
```

**Features:**
- Exclusive GPU access (one worker at a time)
- Automatic worker switching on job submission
- Configurable startup/shutdown times
- Zero manual intervention required

**Quick Start:**
```bash
# Start infrastructure only
docker compose up -d redis postgres etcd orchestrator

# Workers are managed dynamically (not started automatically)
# Submit a job and the orchestrator starts the right worker
./scripts/submit_job.sh z-image '{"prompt": "A futuristic city"}'
```

See **[QUICKSTART_DYNAMIC_WORKERS.md](QUICKSTART_DYNAMIC_WORKERS.md)** for complete guide.

## Documentation

- **[README.md](README.md)** - This file (system overview)
- **[ADDING_NEW_APPS.md](ADDING_NEW_APPS.md)** - Complete guide to adding workers and frontends
- **[DYNAMIC_WORKER_MANAGEMENT.md](DYNAMIC_WORKER_MANAGEMENT.md)** - Dynamic GPU management (detailed)
- **[QUICKSTART_DYNAMIC_WORKERS.md](QUICKSTART_DYNAMIC_WORKERS.md)** - Quick start guide for dynamic workers
- **[scripts/README.md](scripts/README.md)** - Helper scripts documentation

## Roadmap

- [x] Multi-backend orchestration (local + cloud)
- [x] Isolated worker architecture
- [x] PostgreSQL job tracking
- [x] Web UI framework (FastAPI + modern CSS)
- [x] Worker health monitoring (etcd)
- [x] App registry (YAML config)
- [x] **Dynamic worker management with exclusive GPU access**
- [x] **Automatic worker switching**
- [x] **Helper scripts for job submission and monitoring**
- [ ] Prometheus metrics
- [ ] Job prioritization
- [ ] Cost tracking (cloud vs local)
- [ ] Auto-scaling based on queue depth
- [ ] Admin dashboard
- [ ] API authentication


Built with:
- **Go** - Orchestrator backend
- **Python** - ML workers
- **FastAPI** - Web frontends
- **Redis Streams** - Job queuing
- **PostgreSQL** - Job persistence
- **etcd** - Service discovery
- **Docker** - Container isolation
- **PyTorch/Diffusers** - ML frameworks

---

