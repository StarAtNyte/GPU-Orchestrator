# Adding New Applications

## Overview

Each application consists of **two components** working together:

1. **Worker** (GPU container) - Runs the AI/ML model
2. **Frontend** (web UI) - Provides user interface

This architecture provides:
- ✅ **Isolated Dependencies** - Each worker has its own Python environment
- ✅ **Custom UIs** - Each app gets a tailored user experience
- ✅ **Independent Scaling** - Scale workers and frontends separately
- ✅ **No Conflicts** - Z-Image uses diffusers@main, Whisper uses stable? No problem!

## Application Architecture

```
┌─────────────────────────────────────────┐
│  User accesses:                         │
│  http://localhost:7861 (Z-Image UI)     │
│  http://localhost:7862 (Whisper UI)     │
│  http://localhost:7863 (LLaMA Chat)     │
└──────────────┬──────────────────────────┘
               │
               │ Each frontend talks to orchestrator
               ↓
┌──────────────────────────────────────────┐
│         Orchestrator (Port 8080)         │
│    Routes jobs to appropriate worker     │
└──────────────┬───────────────────────────┘
               │
               │ Redis Streams or HTTP
               │
    ┌──────────┼──────────┬────────────┐
    │          │          │            │
    ↓          ↓          ↓            ↓
┌────────┐ ┌────────┐ ┌────────┐ ┌─────────┐
│Z-Image │ │Whisper │ │ LLaMA  │ │ Modal   │
│ Worker │ │ Worker │ │ Worker │ │ Cloud   │
└────────┘ └────────┘ └────────┘ └─────────┘
```

**Key Principle**: One app = One worker + One frontend

## 📋 Table of Contents

1. [Quick Start with Generator](#quick-start-with-generator)
2. [Complete Example: Z-Image Generator](#complete-example-z-image-generator)
3. [Complete Example: Whisper STT](#complete-example-whisper-stt)
4. [Complete Example: LLaMA Chat](#complete-example-llama-chat)
5. [Cloud Applications (Modal)](#cloud-applications)
6. [Customizing Your UI](#customizing-your-ui)
7. [Troubleshooting](#troubleshooting)

---

## Quick Start with Generator

The fastest way to create a new application:

```bash
# Step 1: Create worker (GPU container)
./create_new_app.sh my-app local

# Step 2: Create frontend (web UI)
./create_new_app.sh my-app-ui frontend

# Step 3: Implement worker logic in worker/my-app_worker/handler.py
# Step 4: Customize frontend in frontends/my-app-ui/
# Step 5: Add both to docker-compose.yml
# Step 6: Deploy!
```

Let's see complete examples:

---

## Complete Example: Z-Image Generator

This example shows how to build a complete image generation app using Z-Image (Tongyi's single-stream diffusion transformer).

### Application Overview

- **Name**: Z-Image Generator
- **Worker**: Runs Z-Image Turbo model on GPU
- **Frontend**: Modern web UI with prompt input, resolution selector, image preview
- **Use Case**: Fast, high-quality image generation with Chinese & English prompts
- **Special**: Already has Gradio UI that we'll adapt to our architecture

### Step 1: Create Worker

```bash
./create_new_app.sh z-image local
```

This creates `worker/z-image_worker/` with:
- `Dockerfile` - CUDA container
- `main.py` - Worker loop
- `handler.py` - Model logic (your code goes here)
- `requirements.txt` - Dependencies

### Step 2: Implement Z-Image Handler

Edit `worker/z-image_worker/handler.py`:

```python
import torch
from diffusers import FlowMatchEulerDiscreteScheduler, ZImagePipeline
import logging
import base64
from io import BytesIO
from typing import Dict, Any
import re

logger = logging.getLogger(__name__)

class ZImageHandler:
    def __init__(self):
        self.pipe = None
        self.device = "cuda"

    def load_model(self):
        """Load Z-Image model once, cache for future requests."""
        if self.pipe is not None:
            return

        logger.info("Loading Z-Image Turbo model...")

        self.pipe = ZImagePipeline.from_pretrained(
            "Tongyi-MAI/Z-Image-Turbo",
            torch_dtype=torch.bfloat16,
            low_cpu_mem_usage=False,
        )
        self.pipe.to(self.device)

        # Enable VAE tiling to reduce memory usage
        self.pipe.vae.enable_tiling()

        logger.info("Z-Image loaded successfully")

    def get_resolution(self, resolution_str):
        """Parse resolution string like '1024x1024' or '576x1024'."""
        match = re.search(r"(\d+)\s*[×x]\s*(\d+)", resolution_str)
        if match:
            return int(match.group(1)), int(match.group(2))
        return 1024, 1024

    def process(self, job_id: str, params: Dict[str, str]) -> Dict[str, Any]:
        """Process a single image generation job."""
        try:
            self.load_model()

            # Extract parameters
            prompt = params.get("prompt", "")
            resolution = params.get("resolution", "1024x1024")
            seed = int(params.get("seed", "42"))
            steps = int(params.get("steps", "50"))
            shift = float(params.get("shift", "3.0"))

            width, height = self.get_resolution(resolution)

            logger.info(f"Generating Z-Image: {prompt[:50]}... ({width}x{height})")

            # Clear cache before generation
            torch.cuda.empty_cache()

            # Setup generator and scheduler
            generator = torch.Generator(self.device).manual_seed(seed)
            scheduler = FlowMatchEulerDiscreteScheduler(
                num_train_timesteps=1000,
                shift=shift
            )
            self.pipe.scheduler = scheduler

            # Generate image
            image = self.pipe(
                prompt=prompt,
                height=height,
                width=width,
                guidance_scale=0.0,  # Z-Image Turbo doesn't use guidance
                num_inference_steps=steps + 1,
                generator=generator,
                max_sequence_length=512,
            ).images[0]

            # Convert to base64
            buffered = BytesIO()
            image.save(buffered, format="PNG")
            image_b64 = base64.b64encode(buffered.getvalue()).decode()

            # Cleanup
            del generator
            torch.cuda.empty_cache()
            torch.cuda.synchronize()

            return {
                "success": True,
                "output": {
                    "image_base64": image_b64,
                    "width": width,
                    "height": height,
                    "seed": seed,
                    "steps": steps
                }
            }

        except Exception as e:
            logger.error(f"Error generating image: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }
```

### Step 3: Add Worker Dependencies

Edit `worker/z-image_worker/requirements.txt`:

```txt
# Core dependencies (already included)
redis==5.0.1
protobuf==4.25.1
psycopg2-binary==2.9.9
etcd3==0.12.0

# Z-Image specific dependencies
torch>=2.0.0
transformers
accelerate
git+https://github.com/huggingface/diffusers.git  # Need latest for Z-Image support
kernels
```

**Note**: Z-Image requires the latest diffusers from GitHub, not PyPI!

### Step 4: Create Frontend

```bash
./create_new_app.sh z-image-ui frontend
```

This creates `frontends/z-image-ui/` with basic template.

### Step 5: Customize Frontend for Z-Image

The existing `z-image.py` has a Gradio UI. We'll adapt it to our FastAPI architecture.

Edit `frontends/z-image-ui/app.py`:

```python
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import requests
import os
from typing import Optional

app = FastAPI(title="Z-Image Generator")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://orchestrator:8080")
APP_ID = "z-image"

# Resolution choices matching Z-Image capabilities
RESOLUTIONS = [
    "1024x1024",  # 1:1
    "576x1024",   # 9:16 (portrait)
    "896x1200",   # 3:4
]

class GenerateRequest(BaseModel):
    prompt: str
    resolution: str = "1024x1024"
    seed: int = 42
    steps: int = 50
    shift: float = 3.0
    random_seed: bool = True

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Serve the Z-Image UI."""
    return templates.TemplateResponse("index.html", {
        "request": request,
        "resolutions": RESOLUTIONS
    })

@app.get("/health")
async def health():
    """Health check endpoint."""
    try:
        response = requests.get(f"{ORCHESTRATOR_URL}/workers", timeout=5)
        orchestrator_healthy = response.status_code == 200
    except:
        orchestrator_healthy = False

    return {
        "status": "healthy",
        "orchestrator": "connected" if orchestrator_healthy else "disconnected"
    }

@app.post("/api/generate")
async def generate(request: GenerateRequest):
    """Submit Z-Image generation job."""
    import random

    # Handle random seed
    if request.random_seed:
        seed = random.randint(1, 1000000)
    else:
        seed = request.seed

    try:
        response = requests.post(
            f"{ORCHESTRATOR_URL}/submit",
            json={
                "app_id": APP_ID,
                "params": {
                    "prompt": request.prompt,
                    "resolution": request.resolution,
                    "seed": str(seed),
                    "steps": str(request.steps),
                    "shift": str(request.shift)
                }
            },
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()
            return {
                "success": True,
                "job_id": data["job_id"],
                "status": data.get("status", "queued"),
                "seed": seed
            }
        else:
            return {
                "success": False,
                "error": "Orchestrator error"
            }

    except requests.exceptions.RequestException as e:
        return {
            "success": False,
            "error": f"Cannot connect to orchestrator: {str(e)}"
        }

@app.get("/api/status/{job_id}")
async def get_status(job_id: str):
    """Get job status from orchestrator."""
    try:
        response = requests.get(
            f"{ORCHESTRATOR_URL}/status/{job_id}",
            timeout=5
        )

        if response.status_code == 200:
            data = response.json()
            return {
                "success": True,
                "job_id": job_id,
                "status": data.get("status", "UNKNOWN"),
                "result": data.get("result"),
                "error": data.get("error_log"),
                "created_at": data.get("created_at"),
                "completed_at": data.get("completed_at")
            }
        else:
            return {
                "success": False,
                "error": "Job not found"
            }

    except requests.exceptions.RequestException as e:
        return {
            "success": False,
            "error": f"Cannot connect to orchestrator: {str(e)}"
        }

@app.get("/api/workers")
async def get_workers():
    """Get active workers from orchestrator."""
    try:
        response = requests.get(f"{ORCHESTRATOR_URL}/workers", timeout=5)
        if response.status_code == 200:
            return response.json()
        else:
            return {"count": 0, "workers": []}
    except:
        return {"count": 0, "workers": []}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7861)
```

Now customize the HTML template with Z-Image specific features (Chinese/English prompts, resolution selector, etc.). See `frontends/z-image-ui/` for the complete modern UI.

### Step 6: Update App Registry

Edit `config/apps.yaml`:

```yaml
apps:
  - id: "z-image"
    name: "Z-Image Generator"
    type: "local"
    queue: "jobs:z-image"
    description: "Fast image generation with Z-Image Turbo (single-stream diffusion transformer)"
    gpu_vram_gb: 16
    docker_image: "gpu-orchestrator-z-image-worker:latest"
    parameters:
      - name: "prompt"
        type: "string"
        required: true
        description: "Text description (Chinese or English)"
      - name: "resolution"
        type: "string"
        required: false
        description: "Image resolution (1024x1024, 576x1024, 896x1200)"
      - name: "seed"
        type: "integer"
        required: false
        description: "Random seed for reproducibility"
      - name: "steps"
        type: "integer"
        required: false
        description: "Inference steps (20-100)"
      - name: "shift"
        type: "float"
        required: false
        description: "Time shift parameter (1.0-10.0)"
```

### Step 7: Add to Docker Compose

Edit `docker-compose.yml`:

```yaml
  # Z-Image Worker (GPU container)
  z-image-worker:
    build:
      context: .
      dockerfile: worker/z-image_worker/Dockerfile
    container_name: z-image-worker
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_started
      etcd:
        condition: service_started
    environment:
      - REDIS_HOST=redis
      - REDIS_PORT=6379
      - POSTGRES_HOST=postgres
      - POSTGRES_USER=${POSTGRES_USER}
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
      - POSTGRES_DB=${POSTGRES_DB}
      - ETCD_HOST=etcd
      - ETCD_PORT=2379
      - WORKER_ID=z-image-worker-1
      - MODEL_DIR=/models
      - HF_TOKEN=${HF_TOKEN}  # For downloading Z-Image model
    volumes:
      - ./worker/shared:/app/shared:ro
      - z-image-models:/models
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              capabilities: [gpu]
              # device_ids: ['0']  # Pin to specific GPU

  # Z-Image Frontend (web UI)
  z-image-ui:
    build:
      context: frontends/z-image-ui
    container_name: z-image-ui
    restart: unless-stopped
    depends_on:
      - orchestrator
    environment:
      - ORCHESTRATOR_URL=http://orchestrator:8080
    ports:
      - "7861:7861"

volumes:
  z-image-models:
```

### Step 8: Deploy the Complete App

```bash
# Build both worker and frontend
docker compose build z-image-worker z-image-ui

# Start the worker
docker compose up -d z-image-worker

# Start the frontend
docker compose up -d z-image-ui

# Restart orchestrator to reload config
docker compose restart orchestrator

# Check everything is running
docker compose ps

# View logs
docker compose logs -f z-image-worker z-image-ui
```

### Step 9: Access Your App

Open http://localhost:7861 in your browser!

You'll see:
- Modern dark theme interface
- Prompt input (supports Chinese & English)
- Resolution selector (1024x1024, 576x1024, 896x1200)
- Steps and shift sliders
- Random seed checkbox
- Real-time job status
- Image preview
- Download button
- Generation history

### Example Prompts

Z-Image works great with both languages:

**Chinese**:
```
一位男士和他的贵宾犬穿着配套的服装参加狗狗秀，室内灯光，背景中有观众。
```

**English**:
```
Young Chinese woman in red Hanfu, intricate embroidery. Impeccable makeup,
red floral forehead pattern. Elaborate high bun, golden phoenix headdress.
```

### Architecture Summary

```
User → http://localhost:7861 (Z-Image Frontend)
         ↓
       FastAPI backend submits job
         ↓
       Orchestrator:8080 (/submit with app_id="z-image")
         ↓
       Redis Stream (jobs:z-image)
         ↓
       Z-Image Worker (pulls job, generates image)
         ↓
       PostgreSQL (saves result with base64 image)
         ↓
       Frontend polls /status (displays image)
         ↓
       User downloads image
```

---

## Complete Example: Whisper STT

Speech-to-text application with audio upload interface.

### Application Overview

- **Name**: Whisper Speech-to-Text
- **Worker**: Runs OpenAI Whisper model (CPU or GPU)
- **Frontend**: Web UI with audio upload, transcription display
- **Use Case**: Transcribe audio files to text

### Step 1: Create Worker

```bash
./create_new_app.sh whisper-stt local
```

### Step 2: Implement Whisper Handler

Edit `worker/whisper-stt_worker/handler.py`:

```python
import whisper
import logging
from typing import Dict, Any
import os

logger = logging.getLogger(__name__)

class WhisperSttHandler:
    def __init__(self):
        self.model = None

    def load_model(self):
        if self.model is not None:
            return

        logger.info("Loading Whisper model...")
        # Options: tiny, base, small, medium, large
        self.model = whisper.load_model("base")
        logger.info("Whisper model loaded")

    def process(self, job_id: str, params: Dict[str, str]) -> Dict[str, Any]:
        try:
            self.load_model()

            audio_path = params.get("audio_path")
            if not audio_path or not os.path.exists(audio_path):
                return {
                    "success": False,
                    "error": "Audio file not found"
                }

            logger.info(f"Transcribing: {audio_path}")

            # Transcribe
            result = self.model.transcribe(audio_path)

            return {
                "success": True,
                "output": {
                    "text": result["text"],
                    "language": result["language"],
                    "segments": len(result.get("segments", []))
                }
            }

        except Exception as e:
            logger.error(f"Transcription error: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }
```

### Step 3: Add Dependencies

Edit `worker/whisper-stt_worker/requirements.txt`:

```txt
redis==5.0.1
protobuf==4.25.1
psycopg2-binary==2.9.9
etcd3==0.12.0

# Whisper (CPU-only is fine!)
openai-whisper==20231117
```

### Step 4: Update Dockerfile for CPU

Edit `worker/whisper-stt_worker/Dockerfile`:

```dockerfile
# Use CPU-only base (Whisper works great on CPU)
FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Install ffmpeg for audio processing
RUN apt-get update && apt-get install -y \
    ffmpeg \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY shared/ /app/shared/
COPY whisper-stt_worker/ /app/worker/

RUN pip3 install --no-cache-dir -r /app/worker/requirements.txt

CMD ["python3", "/app/worker/main.py"]
```

### Step 5: Create Frontend

```bash
./create_new_app.sh whisper-ui frontend
```

### Step 6: Customize Frontend for File Upload

Edit `frontends/whisper-ui/app.py` to add file upload endpoint:

```python
from fastapi import UploadFile, File
import os
import uuid

@app.post("/api/transcribe")
async def transcribe(file: UploadFile = File(...)):
    """Upload audio file and submit transcription job."""

    # Save uploaded file
    upload_dir = "/tmp/uploads"
    os.makedirs(upload_dir, exist_ok=True)

    file_id = str(uuid.uuid4())
    file_path = f"{upload_dir}/{file_id}_{file.filename}"

    with open(file_path, "wb") as f:
        f.write(await file.read())

    # Submit to orchestrator
    response = requests.post(
        f"{ORCHESTRATOR_URL}/submit",
        json={
            "app_id": "whisper-stt",
            "params": {
                "audio_path": file_path
            }
        }
    )

    return response.json()
```

Update the HTML for file upload UI and JavaScript for polling.

### Step 7: Add to Config and Docker Compose

Add to `config/apps.yaml`:

```yaml
  - id: "whisper-stt"
    name: "Whisper Speech-to-Text"
    type: "local"
    queue: "jobs:whisper"
    description: "Transcribe audio to text using OpenAI Whisper"
    gpu_vram_gb: 0  # CPU-only!
```

Add to `docker-compose.yml`:

```yaml
  whisper-worker:
    build:
      context: .
      dockerfile: worker/whisper-stt_worker/Dockerfile
    container_name: whisper-worker
    restart: unless-stopped
    depends_on:
      - postgres
      - redis
      - etcd
    environment:
      - REDIS_HOST=redis
      - POSTGRES_HOST=postgres
      - POSTGRES_USER=${POSTGRES_USER}
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
      - POSTGRES_DB=${POSTGRES_DB}
      - ETCD_HOST=etcd
      - WORKER_ID=whisper-worker-1
    volumes:
      - ./worker/shared:/app/shared:ro
      - whisper-models:/root/.cache/whisper
      - /tmp/uploads:/tmp/uploads  # Share upload directory
    # No GPU needed!

  whisper-ui:
    build:
      context: frontends/whisper-ui
    container_name: whisper-ui
    restart: unless-stopped
    depends_on:
      - orchestrator
    environment:
      - ORCHESTRATOR_URL=http://orchestrator:8080
    ports:
      - "7862:7861"  # Different port!
    volumes:
      - /tmp/uploads:/tmp/uploads  # Share upload directory

volumes:
  whisper-models:
```

### Step 8: Deploy

```bash
docker-compose build whisper-worker whisper-ui
docker-compose up -d whisper-worker whisper-ui
docker-compose restart orchestrator
```

Access at http://localhost:7862

---

## Complete Example: LLaMA Chat

Conversational AI with chat interface.

### Quick Setup

```bash
# Create worker and frontend
./create_new_app.sh llama3-chat local
./create_new_app.sh llama-chat-ui frontend

# Implement vLLM handler in worker/llama3-chat_worker/handler.py
# Customize chat UI in frontends/llama-chat-ui/
# Add to docker-compose.yml with port 7863
```

Key differences:
- Uses vLLM for fast inference
- Requires 24GB+ VRAM
- Chat-style UI with message bubbles
- Streaming responses (optional)

Access at http://localhost:7863

---

## Cloud Applications

For cloud workers (Modal, Replicate), you only need a frontend since the worker runs remotely.

### Step 1: Deploy to Modal

```bash
./create_new_app.sh my-cloud-app modal
# Edit the generated file
modal deploy my-cloud-app_modal.py
```

### Step 2: Create Frontend

```bash
./create_new_app.sh my-cloud-app-ui frontend
```

### Step 3: Update Config

```yaml
  - id: "my-cloud-app"
    type: "modal"
    endpoint: "https://your-org--my-cloud-app.modal.run/process"
```

The frontend talks to the orchestrator, which proxies to Modal.

---

## Customizing Your UI

Each frontend is fully customizable. Common patterns:

### Color Scheme

Edit `static/css/style.css`:

```css
:root {
    --primary: #10b981;      /* Green theme */
    --secondary: #14b8a6;    /* Teal accent */
    --bg-primary: #0f172a;   /* Dark background */
}
```

### Form Fields

Edit `templates/index.html` to match your worker's parameters.

### Processing Logic

Edit `static/js/app.js` to handle results differently (e.g., display images vs text vs audio).

### Layout

Modify the HTML structure:
- Single column for simple apps
- Two columns for input + output
- Chat layout for conversational apps
- Gallery for image generation

---

## Deploying Changes to Your Application

After making changes to your worker or frontend, you need to rebuild and restart the services properly.

### Development Workflow

**Step 1: Make Your Changes**
```bash
# Edit handler code
vim worker/z-image_worker/handler.py

# Edit frontend code
vim frontends/z-image-ui/templates/index.html
```

**Step 2: Rebuild the Worker**
```bash
# Stop, rebuild, and start worker
docker-compose stop z-image-worker
docker-compose build z-image-worker
docker-compose up -d z-image-worker

# Verify it's running the new code
docker-compose logs -f z-image-worker
```

**Step 3: Restart the Frontend**
```bash
# Frontend changes usually just need a restart
docker-compose restart z-image-ui

# But if you changed requirements.txt or Dockerfile:
docker-compose up -d --build z-image-ui
```

**Step 4: Verify**
```bash
# Check all services are running
docker-compose ps

# Test in browser
# Open http://localhost:7861
```

### Common Deployment Scenarios

**Scenario 1: Changed worker handler.py**
```bash
# Worker processes job logic, needs full rebuild
docker-compose stop z-image-worker
docker-compose build z-image-worker
docker-compose up -d z-image-worker

# IMPORTANT: Just running 'up -d' won't use the new image!
# Must explicitly rebuild or use --build flag
```

**Scenario 2: Changed frontend HTML/CSS/JS**
```bash
# Static files, just restart container
docker-compose restart z-image-ui

# Verify: Force refresh browser (Ctrl+Shift+R)
```

**Scenario 3: Changed worker dependencies**
```bash
# Edited requirements.txt
# Need clean rebuild to install new packages
docker-compose stop z-image-worker
docker-compose build --no-cache z-image-worker
docker-compose up -d z-image-worker
```

**Scenario 4: Changed config/apps.yaml**
```bash
# Orchestrator reads this at startup
docker-compose restart orchestrator
```

**Scenario 5: Changed both worker and frontend**
```bash
# Rebuild both together
docker-compose stop z-image-worker z-image-ui
docker-compose build z-image-worker z-image-ui
docker-compose up -d z-image-worker z-image-ui
```

### Critical Deployment Gotchas

⚠️ **GOTCHA #1: Container Not Restarted**
```bash
# This builds the image but doesn't restart the container:
docker-compose build z-image-worker
docker-compose up -d z-image-worker  # ← Won't restart if already running!

# Solution: Use --force-recreate or restart explicitly
docker-compose up -d --force-recreate z-image-worker
# OR
docker-compose stop z-image-worker && docker-compose up -d z-image-worker
```

⚠️ **GOTCHA #2: Docker Build Cache**
```bash
# Sometimes Docker caches old code
# Force clean rebuild with:
docker-compose build --no-cache z-image-worker
```

⚠️ **GOTCHA #3: Background Build Jobs**
```bash
# If you ran build in background, wait for it to complete:
docker-compose build z-image-worker

# Then restart:
docker-compose restart z-image-worker

# Check the image was updated:
docker images | grep z-image-worker
```

⚠️ **GOTCHA #4: Shared Volumes**
```bash
# Changes to shared/ require rebuilding ALL workers
docker-compose stop gpu-worker z-image-worker
docker-compose build gpu-worker z-image-worker
docker-compose up -d gpu-worker z-image-worker
```

### Quick Command Reference

```bash
# Worker code changed
docker-compose up -d --build z-image-worker

# Frontend HTML/CSS/JS changed
docker-compose restart z-image-ui

# Frontend Python code changed
docker-compose up -d --build z-image-ui

# Requirements changed
docker-compose build --no-cache z-image-worker
docker-compose up -d --force-recreate z-image-worker

# Orchestrator changed
docker-compose up -d --build orchestrator

# Config changed
docker-compose restart orchestrator

# Everything changed (nuclear option)
docker-compose build --no-cache
docker-compose up -d --force-recreate
```

### Testing Your Deployment

After deploying changes, verify they're active:

```bash
# 1. Check container is running new image
docker-compose ps z-image-worker
# Look at "Created" timestamp

# 2. Check logs for startup messages
docker-compose logs -f z-image-worker
# Should see "Loading Z-Image Turbo model..." with new code

# 3. Submit a test job
curl -X POST http://localhost:8080/submit \
  -H "Content-Type: application/json" \
  -d '{"app_id":"z-image","params":{"prompt":"test","steps":"9"}}'

# 4. Check frontend in browser
# Open http://localhost:7861
# Check browser console for JavaScript errors
```

### Rollback Strategy

If your changes break something:

```bash
# Option 1: Revert code and rebuild
git checkout HEAD -- worker/z-image_worker/handler.py
docker-compose up -d --build z-image-worker

# Option 2: Use previous image (if available)
docker images | grep z-image-worker  # Find old image ID
docker tag <old-image-id> gpuorchestrator-z-image-worker:latest
docker-compose restart z-image-worker

# Option 3: Nuclear option - start fresh
docker-compose down
git checkout main  # or your stable branch
docker-compose build
docker-compose up -d
```

---

## Troubleshooting

### Worker Not Showing Up

```bash
# Check worker registration
curl http://localhost:8080/workers

# Check etcd
docker exec -it etcd etcdctl get "" --prefix

# Check worker logs
docker-compose logs z-image-worker
```

### Frontend Can't Connect

```bash
# Check orchestrator is running
curl http://localhost:8080/health

# Check frontend logs
docker-compose logs z-image-ui

# Verify ORCHESTRATOR_URL is correct
```

### Jobs Stuck

```bash
# Check Redis queue
docker exec -it redis redis-cli XLEN jobs:z-image

# Check worker is processing
docker-compose logs -f z-image-worker
```

### Port Conflicts

Each frontend needs a unique port:
- Z-Image: 7861
- Whisper: 7862
- LLaMA: 7863
- Your app: 7864+

### Z-Image Specific Issues

**Out of Memory**:
- VAE tiling is enabled by default
- Reduce resolution (use 576x1024 instead of 1024x1024)
- Enable model CPU offload in handler

**Model Download Fails**:
```bash
# Set HF_TOKEN in .env
HF_TOKEN=your_huggingface_token

# Or manually download
docker exec z-image-worker python3 -c "
from diffusers import ZImagePipeline
ZImagePipeline.from_pretrained('Tongyi-MAI/Z-Image-Turbo')
"
```

---

## Summary

**Each application = Worker + Frontend**

1. Create worker with `./create_new_app.sh my-app local`
2. Implement `handler.py` with your ML logic
3. Create frontend with `./create_new_app.sh my-app-ui frontend`
4. Customize UI for your app's needs
5. Add both to `docker-compose.yml` with unique ports
6. Deploy together!

**Ports to remember:**
- Orchestrator: 8080
- Z-Image UI: 7861
- Whisper UI: 7862
- LLaMA Chat: 7863
- Your app: 7864+

**Each app is completely isolated!**

For more details:
- **FRONTEND_ARCHITECTURE.md** - Frontend design patterns
- **WORKER_ISOLATION_STRATEGY.md** - Why isolated workers
- **README.md** - System overview
