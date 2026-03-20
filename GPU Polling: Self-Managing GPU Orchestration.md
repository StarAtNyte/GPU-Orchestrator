## The Problem

We had one GPU machine (Ubuntu, NVIDIA 24GB VRAM) and several AI models to serve. The models can't coexist in VRAM:

| Model | VRAM |
|-------|------|
| SDXL | ~12 GB |
| Z-Image Turbo | ~16 GB |
| Qwen Image Edit (4-bit) | ~12 GB |
| OmniLottie | ~12 GB |
| Qwen3.5-35B (Q4 GGUF) | ~22 GB |

The question was how to let the team use all of them through browser UIs without manually starting and stopping containers or coordinating who's using the GPU. The answer is GPU Polling: a job orchestrator that dynamically manages which model is loaded at any given time.

---

## Architecture

```
┌────────────────────────────────────────────────────────────────┐
│  BACKEND  (Ubuntu GPU PC)                                      │
│                                                                │
│  PostgreSQL :5433   Redis :16379   etcd :12379                 │
│                     │                   │                      │
│              ┌──────┴───────────────────┘                      │
│              ▼                                                  │
│       Orchestrator (Go)  :8890                                 │
│       ┌──────────────────────────────────────────────┐         │
│       │  Job routing · Worker lifecycle · Auth · API │         │
│       └────────────────────┬─────────────────────────┘         │
│                            │  Docker SDK                       │
│           ┌────────────────┼──────────────┐                    │
│           ▼                ▼              ▼                    │
│      [sdxl-worker]  [z-image-worker]  [omnilottie-worker]      │
│      [qwen-image-edit-worker]  [qwen35-chat-worker]  …         │
│      (one active at a time — exclusive_mode: true)             │
└────────────────────────────┬───────────────────────────────────┘
                             │  HTTP :8890
                             ▼
┌────────────────────────────────────────────────────────────────┐
│  FRONTEND  (separate server, behind nginx)                     │
│                                                                │
│  main-dashboard      :8810   /gpu-polling/                     │
│  admin-dashboard     :8811   /gpu-polling/admin/               │
│  sdxl-ui             :8861   /gpu-polling/sdxl/                │
│  z-image-ui          :8862   /gpu-polling/z-image/             │
│  qwen-image-edit-ui  :8865   /gpu-polling/qwen-image-edit/     │
│  qwen-image-var-ui   :8866   /gpu-polling/qwen-image-variat…/  │
│  qwen35-chat-ui      :8867   /gpu-polling/qwen35-chat/         │
│  omnilottie-ui       :8868   /gpu-polling/omnilottie/          │
└────────────────────────────────────────────────────────────────┘
```

The backend and frontend run on separate machines and communicate over HTTP. The GPU machine only runs GPU workloads. All frontend apps sit behind an nginx reverse proxy with path prefixes — one domain, one bookmark for the team.

---

## Infrastructure

PostgreSQL, Redis, etcd, and the orchestrator all run on the GPU machine via docker-compose. GPU worker containers are also defined in the same docker-compose but with `restart: "no"` — they are not started by docker-compose at all. The orchestrator starts and stops them at runtime via the Docker SDK.

```bash
# backend/.env
POSTGRES_PASSWORD=your_password
JWT_SECRET=your_jwt_secret_min_32_chars
HF_TOKEN=hf_xxxxx
ADMIN_KEY=your_admin_key
```

nginx routes each frontend app by path prefix:

```nginx
location /gpu-polling/omnilottie/ {
    proxy_pass http://localhost:8868/;
    proxy_http_version 1.1;
    proxy_read_timeout 300s;
}
```

Each frontend HTML sets `<base href="/gpu-polling/<app-name>/">` so relative asset paths resolve correctly through the proxy.

---

## The Orchestrator (Go)

The orchestrator is the brain of the system — a Go HTTP server that handles job submission, worker lifecycle management, auth, and status polling.

### API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/auth/signup` | — | Register new user |
| `POST` | `/auth/login` | — | Get JWT token |
| `GET` | `/auth/me` | JWT | Get current user info |
| `POST` | `/submit` | JWT | Submit a job |
| `GET` | `/status/{job_id}` | — | Poll job status |
| `GET` | `/workers` | — | List workers and their states |
| `GET` | `/health/gpu` | — | GPU health via nvidia-smi |
| `GET` | `/user/jobs` | JWT | Job history for current user |
| `DELETE` | `/user/jobs/{id}` | JWT | Delete a job |
| `GET` | `/admin/jobs` | Admin key | All jobs |
| `POST` | `/admin/jobs/{id}/cancel` | Admin key | Cancel a job |
| `POST` | `/admin/jobs/{id}/retry` | Admin key | Retry a failed job |

Admin endpoints use an `X-Admin-Key` header instead of JWT. Rate limiting is 20 requests/minute per user, sliding window.

### Job Submission

`POST /submit` body:

```json
{
  "app_id": "omnilottie",
  "params": {
    "task_type": "text",
    "prompt": "a bouncing blue ball",
    "max_tokens": "5556",
    "temperature": "0.9"
  }
}
```

All params are strings — everything is `map<string, string>` at the transport layer. Workers parse their own types. The orchestrator doesn't need to know what each worker does with them.

The orchestrator validates the JWT, inserts a `PENDING` job into PostgreSQL, serializes it as Protobuf, pushes it to the app's Redis Stream, then dispatches the right worker. Returns `{ "job_id": "uuid" }`. The frontend polls `/status/{job_id}` from there.

### Worker State Machine and Dispatch

The orchestrator tracks each worker's state via etcd watches:

```
STOPPED → STARTING → IDLE → WARM → PROCESSING → WARM → (idle timeout) → STOPPED
```

When a job arrives:

- Right worker is `WARM` or `IDLE` → dispatch immediately
- No worker running → `docker start <container>` via Docker SDK
- Different worker is running → wait for it to go idle, call its `/cleanup` endpoint, wait for it to stop, start the needed worker

`exclusive_mode: true` in `workers.yaml` enforces that only one worker is in a non-STOPPED state at any time.

### Circuit Breaker

If a worker fails to start 5 consecutive times, the circuit breaker opens: pending jobs are marked `FAILED`, and the worker enters exponential backoff cooldown (30s → 1m → 2m → 4m → 5m max). Without this, a misconfigured image causes an infinite restart loop that fills the disk with logs within an hour.

---

## App & Worker Configuration

### config/apps.yaml

Defines every app the system knows about — queue key, VRAM requirement, Docker image, accepted parameters:

```yaml
apps:
  - id: "omnilottie"
    name: "OmniLottie"
    type: "local"
    queue: "jobs:omnilottie"
    description: "Convert text, images, or videos into Lottie animations"
    gpu_vram_gb: 12
    docker_image: "backend-omnilottie-worker:latest"
    container_name: "omnilottie-worker"
    idle_timeout_seconds: 300
    startup_timeout_seconds: 180
    environment:
      HF_HOME: "/models"
      TRANSFORMERS_CACHE: "/models"
    parameters:
      - name: "task_type"
        type: "string"
        required: true
        description: "Generation mode: 'text', 'image', or 'video'"
      - name: "prompt"
        type: "string"
        required: false
      - name: "image_base64"
        type: "string"
        required: false
        description: "Base64 encoded input image (for image mode)"
      - name: "video_base64"
        type: "string"
        required: false
      - name: "max_tokens"
        type: "string"
        required: false
      - name: "temperature"
        type: "string"
        required: false
      - name: "top_p"
        type: "string"
        required: false
      - name: "top_k"
        type: "string"
        required: false
      - name: "use_sampling"
        type: "string"
        required: false
```

### config/workers.yaml

Resource requirements and global scheduling settings:

```yaml
workers:
  omnilottie-worker:
    app_id: "omnilottie"
    queue: "jobs:omnilottie"
    vram_required_gb: 12
    startup_time_seconds: 90
    shutdown_time_seconds: 10

  qwen35-chat-worker:
    app_id: "qwen35-chat"
    queue: "jobs:qwen35-chat"
    vram_required_gb: 22
    startup_time_seconds: 60
    shutdown_time_seconds: 10

  # sdxl-worker, z-image-worker, qwen-image-edit-worker, qwen-image-variations-worker …

settings:
  exclusive_mode: true
  idle_timeout: 300       # seconds before stopping an idle worker
  max_startup_wait: 180   # seconds to wait for worker to register in etcd
  max_shutdown_wait: 30
```

---

## Job Serialization: Protobuf over Redis

Jobs are serialized as Protobuf before being pushed to Redis Streams:

```protobuf
syntax = "proto3";
package worker;
option go_package = "./pb";

message JobRequest {
  string job_id       = 1;
  string app_id       = 2;
  string handler_type = 3;  // "local_gpu" or "cloud_http"
  map<string, string> params = 4;
}
```

Compiled stubs: Python in `backend/worker/shared/worker_pb2.py`, Go in `backend/orchestrator/pb/`. All job parameters — including base64-encoded images and video — go into `params`. This keeps the orchestrator generic; it doesn't need to know anything about individual workers.

---

## Redis Streams

Each app has its own stream key:

```
jobs:sdxl    jobs:z-image    jobs:qwen-image-edit    jobs:omnilottie    jobs:qwen35-chat
```

The orchestrator writes with `XADD`, workers read with consumer groups (`XREADGROUP`). Consumer groups give exactly-once delivery — if a worker crashes, the message stays in the PEL and gets reclaimed on next startup via `XAUTOCLAIM`.

```bash
# Worker reads its queue (blocking, 5s timeout)
XREADGROUP GROUP omnilottie-workers omnilottie-worker-1 COUNT 1 BLOCK 5000 STREAMS jobs:omnilottie >

# Worker acknowledges on completion
XACK jobs:omnilottie omnilottie-workers <message_id>

# On startup: reclaim messages stuck in PEL for >60s (from crashed instances)
XAUTOCLAIM jobs:omnilottie omnilottie-workers omnilottie-worker-1 60000 0-0
```

### Token Streaming (Qwen3.5 Chat)

The chat app uses Redis Streams in the opposite direction. The worker writes tokens as they're generated; the frontend reads them and relays to the browser as SSE:

```
Browser ← SSE ← Frontend (XREAD BLOCK) ← chat-stream:{job_id} ← Worker (XADD)
```

Worker publishes each token to `chat-stream:{job_id}` with a `done` flag on the final entry. The frontend FastAPI server does a blocking `XREAD` in an async generator and yields `text/event-stream` chunks. This avoids WebSockets entirely while still giving real-time output.

---

## The Worker Pattern

### Three-State Lifecycle

Every worker maintains one of three states, published to etcd on every heartbeat:

```
IDLE       — container running, model NOT in VRAM (GPU is free)
WARM       — model loaded in VRAM, idle timer ticking, not processing
PROCESSING — actively running inference
```

The orchestrator watches these via etcd. When a worker registers as `WARM` after startup, the orchestrator knows it's ready for jobs.

### etcd Registration

Each worker registers on startup with a 10-second TTL lease and refreshes it every 3 seconds via a background heartbeat thread. The lease key is `/workers/{app_id}/{worker_id}`. If the heartbeat stops (crash, OOM kill), the key expires in ≤10 seconds and the orchestrator's watch fires immediately — no polling needed.

### handler.py Interface

Every worker implements the same three-method interface. Everything else — Redis consumption, PostgreSQL updates, etcd registration, heartbeat, idle timer, `/cleanup` endpoint — is shared `main.py` boilerplate identical across all workers:

```python
class ModelHandler:
    def load_model(self) -> None:
        """Load model weights into GPU VRAM. Called once before first job."""

    def offload_model(self) -> None:
        """Unload model from VRAM, free GPU memory."""

    def process(self, params: dict) -> dict:
        """
        Run inference. params is the map<string,string> from the Protobuf job.
        Return value is stored as output JSONB in PostgreSQL.
        """
```

### Cleanup Endpoint

Each worker runs a minimal HTTP server on port 8000:

```
POST /cleanup  — force WARM → IDLE (model offload) without stopping the container
GET  /status   — returns current state JSON
```

The orchestrator calls `/cleanup` before stopping a worker container, giving it a chance to release VRAM cleanly before Docker sends SIGKILL.

### GPU Memory Coordination

Before `load_model()`, workers acquire a distributed Redis lock (`SET gpu:model_loading_lock <worker_id> NX EX 300`) to prevent two workers from loading simultaneously and OOM-ing the GPU. The lock releases when loading completes, or expires automatically if the process dies.

---

## Authentication

JWT-based, implemented directly in the Go orchestrator. Tokens expire after 7 days, passwords hashed with bcrypt. Signup validation: username 3-30 chars, valid email, password 8+ chars.

Protected endpoints (`/submit`, `/user/jobs`) require `Authorization: Bearer <token>`. Each frontend FastAPI app proxies `/auth/login` and `/auth/signup` to the orchestrator, and forwards the `Authorization` header on every call to a protected route.

The shared `static/js/auth.js` handles token storage in localStorage, the login/signup modal, and automatic redirect to the modal on 401. Every frontend includes the same file — written once, dropped into each app's `static/js/`.

---

## The Apps

### SDXL Image Generator

**Queue**: `jobs:sdxl` · **VRAM**: ~12 GB · **Startup**: ~45s

Text-to-image with Stable Diffusion XL. First worker built, most battle-tested.
Params: `prompt`, `negative_prompt`, `width`, `height`, `num_inference_steps`, `guidance_scale`.

### Z-Image Turbo

**Queue**: `jobs:z-image` · **VRAM**: ~16 GB · **Startup**: ~30s

Tongyi's single-stream diffusion transformer. Faster than SDXL and handles Chinese prompts natively.
Params: `prompt`, `resolution` (e.g. `896x1200`), `steps`, `shift`, `seed`.

### Qwen Image Edit

**Queue**: `jobs:qwen-image-edit` · **VRAM**: ~12 GB · **Startup**: ~60s

Edit images with natural language. 4-bit quantized + Lightning LoRA, ~14s inference.
Params: `prompt`, `image_base64`, `negative_prompt`, `steps`, `cfg_scale`.

### Qwen Image Variations

**Queue**: `jobs:qwen-image-variations` · **VRAM**: ~12 GB · **Startup**: ~60s

Same model as Image Edit, applies randomly sampled prompts from a curated `variationsPrompts.json`. User just uploads a photo, no text input.
Params: `image_base64`.

### Qwen3.5 Chat

**Queue**: `jobs:qwen35-chat` · **VRAM**: ~22 GB · **Startup**: ~60s

Qwen3.5-35B-A3B MoE in GGUF Q4, loaded with llama-cpp-python compiled with CUDA support. Token streaming via Redis Streams + SSE. Thinking mode disabled by default via `/no_think` prefix injection on the user message.

Build note: llama-cpp-python compiles native CUDA extensions during `docker compose build` — first build takes ~5 minutes. Subsequent builds use the layer cache.

Params: `messages` (JSON array of `{role, content}`), `temperature`, `top_p`, `top_k`, `max_tokens`, `enable_thinking`.

### OmniLottie

**Queue**: `jobs:omnilottie` · **VRAM**: ~12 GB · **Startup**: ~90s

Converts text prompts, reference images, or short video clips into Lottie JSON animations. Multimodal model (Qwen2.5-VL-3B backbone + LottieDecoder): generates Lottie JSON token-by-token, then a post-processing pass fixes layer structure, keyframe rounding, and canvas sizing before returning.

The frontend UI includes an example gallery served from `static/example/`:

- `demo.txt` — 38 text prompts (one per line)
- `demo_images/` — 26 reference images
- `demo_video/` — 30 demo video clips

Clicking any example loads it directly into the generator. The `/examples` endpoint reads these at request time and returns images/video as base64.

Params: `task_type` (`text`|`image`|`video`), `prompt`, `image_base64`, `video_base64`, `max_tokens`, `temperature`, `top_p`, `top_k`, `use_sampling`.

---

## Adding New Apps

### Method 1: Scaffolding Scripts (for new models)

```bash
./create_worker.sh my-model
```

Generates `backend/worker/my-model_worker/` with `handler.py`, `main.py`, `Dockerfile`, `requirements.txt` — all boilerplate filled in. Implement `load_model`, `offload_model`, `process` in `handler.py`, add pip dependencies, then:

```bash
./create_new_app.sh my-model frontend
# Generates frontend/my-model-ui/ — FastAPI app with auth, generate/status proxies, Jinja2 template
```

Registration steps:

1. Add entry to `config/apps.yaml` and `config/workers.yaml`
2. Add docker-compose service to `backend/docker-compose.yml`
3. `docker compose build my-model-worker`
4. `docker compose restart orchestrator`

See `ADDING_NEW_APPS.md` for the full guide.

### Method 2: Adapting an Existing Codebase (OmniLottie)

When a model already has its own working code, the scaffolding scripts aren't useful — the work is adapting existing logic to the worker interface, not generating boilerplate.

OmniLottie had a standalone FastAPI `app.py` with model loading, inference, and UI all in one file. The integration:

**Step 1 — Create `backend/worker/omnilottie_worker/` with four files:**

- `handler.py` — extracted inference logic from the original `app.py`. The main adaptation: the original took file uploads over HTTP multipart; the worker receives `image_base64`/`video_base64` strings in the params dict instead.
- `main.py` — near-verbatim copy from an existing worker, with `STREAM_KEY`, `GROUP_NAME`, and handler import updated.
- `Dockerfile` — standard template + `ffmpeg` for video frame extraction.
- `requirements.txt` — core worker deps + `torch`, `transformers`, `decord`, `qwen-vl-utils`.

**Step 2 — Handle the model package dependency:**

OmniLottie has its own `lottie/` Python package that isn't on PyPI. Copy the model code into the backend build context and expose it via `PYTHONPATH`:

```dockerfile
COPY omnilottie/ /app/omnilottie/
ENV PYTHONPATH=/app:/app/omnilottie:$PYTHONPATH
```

The source lives at `backend/omnilottie/` (inside the backend build context). Without this, `docker compose build` fails with a module not found error.

**Step 3 — Register, build, deploy:**

```bash
# Add to config/apps.yaml, config/workers.yaml, backend/docker-compose.yml
docker compose build omnilottie-worker
docker compose restart orchestrator
```

**Step 4 — Build the frontend:**

The frontend was designed from scratch to match the original app's aesthetic. Key additions to `app.py` beyond the standard template:

- `/examples` endpoint — reads `static/example/` and returns prompts/images/videos as base64
- `/api/user/jobs` — proxies to `/user/jobs?app_id=omnilottie` on the orchestrator

The UI renders returned Lottie JSON using the `lottie-web` JS library (`lottie.loadAnimation({ animationData: ... })`).

**Total time**: ~30 minutes for the worker. The frontend UI took a few hours (designed from scratch to match the original app's look).

---

## Deployment

### Initial Setup

```bash
# Backend
cd backend
cp ../.env.example .env  # set POSTGRES_PASSWORD, JWT_SECRET, HF_TOKEN

docker compose up -d postgres redis etcd
docker compose up -d orchestrator

# Verify
curl http://localhost:8890/workers
curl http://localhost:8890/health/gpu
```

```bash
# Frontend — edit docker-compose.yml: set ORCHESTRATOR_URL=http://172.17.0.1:8890
cd frontend
docker compose up -d
curl http://localhost:8810/health
```

### Building Worker Images

```bash
cd backend
docker compose build sdxl-worker
docker compose build omnilottie-worker
docker compose build qwen35-chat-worker  # ~5 min first time (CUDA compile)
```

Model weights are mounted from the host at runtime. Download models to `~/models/<worker-name>/` before starting the system — each worker's docker-compose service maps `${HOME}/models/<app-name>:/models`.

### Useful Debug Commands

```bash
# Which worker is active and what state
curl http://localhost:8890/workers | jq

# Raw GPU VRAM usage
nvidia-smi --query-gpu=memory.used,memory.total --format=csv

# Orchestrator logs
docker logs gpu-orchestrator -f

# GPU memory stuck after container exit
bash scripts/cleanup_orphaned_gpu.sh

# Submit a test job
bash scripts/submit_job.sh sdxl-image-gen '{"prompt": "a red cat"}'

# Watch a job's progress
bash scripts/monitor_job.sh <job_id>

# Test dynamic worker switching
bash scripts/test_dynamic_switching.sh
```

---

## Challenges

**GPU memory not freed on container stop.** `docker stop` sends SIGTERM but GPU processes can linger. `cleanup_orphaned_gpu.sh` handles this manually; the orchestrator also calls each worker's `/cleanup` endpoint before stopping the container to release VRAM cleanly first.

**Worker crashes mid-job.** If a worker dies while processing, the job sits at `PROCESSING` forever. Two safety nets: (1) `XAUTOCLAIM` on next worker startup reclaims messages stuck in the Redis PEL for over 60 seconds, and (2) a timeout monitor marks anything in `PROCESSING` for over 30 minutes as `FAILED`.

**OmniLottie output validation.** The model occasionally generates Lottie JSON with invalid layer structure — negative keyframe times, out-of-range canvas sizes, missing required fields. `handler.py` runs a post-processing pass to fix common issues. This was carried over from the original standalone app and was already the most complex part of the original implementation.

**Qwen3.5 build time.** llama-cpp-python with `CUDA=1` compiles from source. First build takes ~5 minutes. The Docker layer caches after that, so rebuilds are fast unless `requirements.txt` changes.

**Single GPU contention.** When one user is generating with SDXL and another submits a Qwen job, the second user waits for: (1) the current job to finish, (2) idle timeout or immediate cleanup, (3) model swap (~60s). The queue is transparent about this — job status shows `PENDING` and the GPU status badge shows which app is currently active.

---

## Takeaways

**Polling works.** HTTP polling every 3 seconds is sufficient for everything except the chat app. No WebSocket complexity, nothing to reconnect. The one exception — token streaming for Qwen3.5 — was worth the extra complexity because waiting for a full 35B response before showing anything would feel broken.

**Docker SDK over shell scripts.** Managing containers programmatically gives proper error handling and real event streams. Calling `docker compose up` from a shell inside a container is fragile and hard to reason about.

**Circuit breakers before you need them.** The first version had none. A Dockerfile typo caused an infinite restart loop that filled the disk overnight. Added the next morning.

**Separate frontend and backend early.** GPU machine runs GPU workloads only. UIs run on a separate server. Clean resource isolation, and the team can use the UIs even when the GPU machine is rebooting.

**The handler interface is the whole system.** Everything complicated — Docker orchestration, etcd coordination, Redis consumer groups, circuit breakers — is hidden behind `load_model / offload_model / process`. Adding a new model means implementing those three methods and filling in YAML. The OmniLottie integration (an existing codebase with its own inference pipeline) took about 30 minutes. That's the measure of whether the abstraction is working.
