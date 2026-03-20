# GPU Polling — Building a Self-Managing GPU Server for AI Apps

## The Starting Point

I wanted a server that could run GPU-powered AI apps — image generation with SDXL, fast turbo generation with Z-Image, image editing with Qwen — all accessible through simple web UIs. Something I could just open in a browser and use.

Sounds straightforward. Except for one problem.

## The Problem: One GPU, Many Models

I had a single machine — an Ubuntu PC with one NVIDIA GPU (24GB VRAM). And these AI models are hungry:

- **SDXL** needs ~12GB VRAM
- **Z-Image Turbo** needs ~16GB VRAM
- **Qwen Image Edit** needs 22~GB VRAM

You can't just load all of them at once. 24GB isn't enough. And even if you could, the GPU isn't always free — when one model is running inference, the GPU is maxed out. Other jobs just have to wait.

So I couldn't treat this like a normal web server where you spin up all your services and let them run. I needed something smarter.

## The Solution: A Job Orchestrator with Dynamic Worker Switching

The core idea: **only one model runs on the GPU at a time**. When a user submits a job, the system figures out which worker (model) is needed, stops whatever's currently running if necessary, starts the right worker, processes the job, and then keeps that worker alive for a while in case more jobs come in for the same model.

This is what GPU Polling does.

## How It Works

### Architecture

The system is split into two parts that can run on separate machines:

```
┌─────────────────────────────────────────┐
│  BACKEND (Ubuntu GPU PC)                │
│                                         │
│  PostgreSQL + Redis + etcd              │
│           ↓                             │
│      Orchestrator (Go)                  │
│           ↓                             │
│  GPU Workers (Python, one at a time)    │
│  - SDXL Worker                          │
│  - Z-Image Worker                       │
│  - Qwen Image Edit Worker               │
│  - Qwen Image Variations Worker         │
└───────────────┬─────────────────────────┘
                │ HTTP (port 9090)
                ↓
┌─────────────────────────────────────────┐
│  FRONTEND (Any server)                  │
│                                         │
│  Main Dashboard  :8080                  │
│  SDXL UI         :7861                  │
│  Z-Image UI      :7862                  │
│  Qwen UI         :7863                  │
│  Admin Dashboard  :8000                 │
└─────────────────────────────────────────┘
```

The frontend is just web UIs — each app gets its own FastAPI server with HTML/JS/CSS. They don't touch the GPU at all. They talk to the backend over HTTP.

The backend is where everything interesting happens.

### The Job Lifecycle

1. **User submits a job** from a web UI (e.g., types a prompt in the SDXL interface)
2. **Frontend sends it to the orchestrator** via `POST /submit`
3. **Orchestrator validates it**, looks up which app it belongs to from `apps.yaml`, and inserts a record into PostgreSQL with status `PENDING`
4. **Job gets serialized as Protobuf** and pushed to a Redis Stream (each app has its own queue: `jobs:sdxl`, `jobs:z-image`, etc.)
5. **Orchestrator checks if the right worker is running:**
   - If yes → worker picks up the job from Redis
   - If no → orchestrator uses the Docker SDK to start the worker container
   - If a *different* worker is running → wait for it to go idle, stop it, then start the needed one
6. **Worker processes the job** on the GPU, writes the result back to PostgreSQL
7. **Frontend polls `/status/{job_id}`** to get the result
8. **Worker goes idle** — after 5 minutes of no jobs, the orchestrator automatically stops it to free the GPU

### The Polling

This is the "polling" in GPU Polling. The frontends don't use WebSockets — they just poll. Every few seconds, each frontend hits `/health/gpu` to show the GPU status in the navbar (green = available, red = busy), and when a job is submitted, it polls `/status/{job_id}` until completion.

Simple, reliable, and easy to debug.

### Dynamic Worker Management

This was the hardest part to get right. The orchestrator manages worker containers through the Docker SDK — it creates, starts, stops, and removes containers programmatically. No docker-compose for runtime worker management.

Key details:
- **Exclusive mode**: Only one worker container can use the GPU at a time (`exclusive_mode: true` in `workers.yaml`)
- **Idle timeout**: Workers stay alive for 5 minutes after their last job, in case more come in. After that, they're stopped automatically.
- **Startup monitoring**: The orchestrator waits for workers to register in etcd before considering them ready
- **Circuit breaker**: If a worker fails to start 5 times in a row, the system stops trying and marks pending jobs as failed (instead of retrying infinitely)
- **Exponential backoff**: Failed workers get progressively longer cooldown periods (30s → 1m → 2m → 4m → 5m max)

### The Worker Pattern

Each worker is a Python container that:
1. Connects to Redis, PostgreSQL, and etcd on startup
2. Registers itself in etcd (so the orchestrator knows it's alive)
3. Loads the ML model into GPU memory
4. Reads jobs from its Redis Stream
5. Processes them, writes results to PostgreSQL
6. Sends heartbeats to etcd

I built a template system (`create_worker.sh`) so adding new workers is just:
```bash
./create_worker.sh my-new-model
# Edit handler.py with your model logic
# Done
```

## Tech Stack

| Component | Technology | Why |
|-----------|-----------|-----|
| Orchestrator | **Go** | Fast, handles concurrency well with goroutines, Docker SDK has first-class Go support |
| Workers | **Python** | All ML libraries (diffusers, transformers) are Python |
| Job Queue | **Redis Streams** | Persistent queues, consumer groups, exactly what I needed for job routing |
| Database | **PostgreSQL + TimescaleDB** | Job tracking, GPU metrics time-series, user history |
| Service Discovery | **etcd** | Workers register themselves, orchestrator watches for changes |
| Serialization | **Protobuf** | Compact binary format for job payloads over Redis |
| Frontend | **FastAPI + Tailwind CSS** | Quick to build, each app gets its own UI |
| Containers | **Docker** | GPU isolation, dependency management, reproducible builds |

## Development Process

The project evolved over about 3 weeks:

**Jan 22 — First commit**: Got the basic orchestrator running with a single SDXL worker. Everything in one big docker-compose. Frontend and backend on the same machine.

**Jan 22 — Separated frontend and backend**: Realized the frontend doesn't need to be on the GPU machine. Split them into two docker-compose files so the frontend can run anywhere and just point at the backend's IP.

**Jan 23 — Job history**: Added the ability to view past jobs, their status, parameters, and results. User-facing history page and admin dashboard.

**Jan 30 — Job deletion**: Users can now clean up their job history. Admin can cancel and retry jobs.

**Feb 11 — Revamp**: Major restructuring. Added dynamic worker management, the circuit breaker pattern, idle timeouts, the worker template system, and the main dashboard hub. This was the big one.

**Feb 13 — Production polish**: Fixed routing for domain deployment, reorganized ports so the most-used services get the convenient port numbers.

## Challenges

**GPU memory management**: You can't just `docker stop` a container and expect VRAM to be freed instantly. Sometimes GPU processes linger. I added cleanup scripts and health checks via `nvidia-smi` to handle this.

**Worker startup time**: Loading a 16GB model into VRAM takes 30-60 seconds. During this time, the job is queued and the user is waiting. I show GPU status in real-time so users know something is happening.

**Worker crashes**: If a worker crashes mid-job, the job would be stuck in `PROCESSING` forever. The timeout monitor catches these — any job processing for more than 30 minutes gets marked as failed.

**Single GPU contention**: When two users submit jobs for different models at the same time, one has to wait. The queue handles this, but I had to think carefully about the UX — showing queue position, estimated wait time, and clear status indicators.

## What's Running

Right now, the system supports 6 AI apps:

1. **SDXL Image Generator** — Text-to-image with Stable Diffusion XL
2. **Z-Image Turbo** — Fast image generation with Tongyi's single-stream diffusion transformer (supports Chinese & English prompts)
3. **Qwen Image Edit** — Edit existing images with natural language instructions (4-bit quantized, ~14s inference)
4. **Qwen Image Variations** — Generate random style variations of a person's photo
5. **Qwen3.5 Chat** — Chat with Qwen3.5-35B-A3B (MoE, 22GB Q4) with streaming via Redis Streams
6. **OmniLottie** — Convert text, images, or videos into Lottie animations using the OmniLottie AI model. The UI ships with a built-in example gallery: 38 text prompts, 26 reference images, and 30 demo videos that load directly into the generator.

Plus a **Panorama Processor** configured for cloud deployment via Modal (for when the local GPU isn't enough).

## Adding New Apps

The system is designed to be extensible. To add a new AI model:

1. Run `./create_worker.sh my-model` to scaffold the worker
2. Implement your model logic in `handler.py`
3. Add it to `config/apps.yaml` and `config/workers.yaml`
4. Add the docker service to `docker-compose.yml`
5. Optionally, create a frontend UI

The orchestrator picks it up automatically — it reads the config on startup and knows how to route jobs and manage the worker container.

### Case Study: Adding OmniLottie

OmniLottie was a good test of the system's extensibility. It's a multimodal model (text + image + video → Lottie JSON) that already had its own standalone FastAPI app (`omnilottie/app.py`). To integrate it into GPU Polling, I:

1. **Created the worker directory** at `backend/worker/omnilottie_worker/` with four files:
   - `handler.py` — Extracted the model loading + inference logic from the original `app.py` into the standard handler pattern (`load_model`, `offload_model`, `process`, cleanup timer)
   - `main.py` — Standard worker main loop (copied from z-image, changed the stream key, group name, and handler import)
   - `Dockerfile` — Based on the standard template, but adds `ffmpeg` (for video processing) and copies the `omnilottie/` model code into the container
   - `requirements.txt` — Core worker deps (redis, protobuf, etcd3, psycopg2) + model-specific deps (torch, transformers, decord, qwen-vl-utils)

2. **Registered it in configs:**
   - `config/apps.yaml` — Added the app definition with its parameters (task_type, prompt, image_base64, video_base64, sampling params)
   - `config/workers.yaml` — Added resource requirements (12GB VRAM, 90s startup)

3. **Added docker-compose service** — Standard worker service entry with `MODEL_PATH` env var pointing to the model weights volume

The key insight: the original app had the model logic *and* the web UI in one file. For GPU Polling, I only need the model logic — the orchestrator handles job routing, and any frontend just talks to the orchestrator's HTTP API. The handler's `process()` method takes job params (with base64-encoded media), runs inference, and returns the Lottie JSON. That's the entire interface.

The frontend UI was redesigned to match the original standalone app's look: gradient header, segmented tab controls, panel-based layout with a live Lottie preview, JSON inspector, and a stats bar showing token count, layer count, and generation time. The example gallery (38 text prompts, 26 reference images, 30 demo videos) was copied from the original repo into `static/example/` and served via an `/examples` endpoint — clicking any example loads it directly into the generator.

**Total time to integrate**: About 30 minutes, most of which was adapting the handler class. The main.py was a near-verbatim copy.

## Takeaways

- **Polling is underrated**. For a system like this, simple HTTP polling every 3 seconds is more than enough. No WebSocket complexity, no connection management headaches.
- **Docker SDK > shell scripts**. Managing containers programmatically via the Go Docker SDK is way more reliable than calling `docker compose up` from inside a container.
- **Circuit breakers matter**. Without them, a broken worker config would cause infinite restart loops that filled up the disk with logs.
- **Separate what you can**. The frontend/backend split means the GPU machine only runs GPU workloads. The UIs can run on a cheap server or even a laptop.
