# Quick Start - GPU Orchestrator

Get up and running in 5 minutes.

## Backend (Ubuntu GPU PC)

```bash
cd backend
docker-compose up -d postgres redis etcd orchestrator
docker-compose up -d sdxl-worker z-image-worker whisper-worker

# Test
curl http://localhost:8080/health/gpu
```

## Frontend (Windows Laptop / Linux Server)

### Option 1: Python (Development)

```powershell
cd frontend\sdxl-ui
pip install -r requirements.txt
$env:ORCHESTRATOR_URL = "http://192.168.50.28:8080"
python app.py
# Open http://localhost:7861
```

### Option 2: Docker (All Services)

```bash
cd frontend
# Update docker-compose.yml with your Ubuntu IP
sed -i 's/113.199.192.32/192.168.50.28/g' docker-compose.yml

docker-compose up -d
# Open http://localhost:8080 (Main Dashboard)
```

## What You Get

- **Main Dashboard** (http://localhost:8080) - Service hub
- **SDXL UI** (http://localhost:7861) - Image generation
- **Z-Image UI** (http://localhost:7862) - Alternative models
- **Admin Dashboard** (http://localhost:8000) - Monitoring

## Test It

1. Open **http://localhost:8080**
2. See **"GPU Ready"** indicator (green)
3. Click **"SDXL"** service
4. Submit image generation job
5. Watch GPU status change to **"GPU Busy"** (red)
6. Job completes → indicator returns to **green**

## Detailed Setup

See `DEVELOPMENT.md` for complete instructions.

## Structure

```
GPU-Orchestrator/
├── backend/          (Ubuntu GPU PC)
│   └── docker-compose.yml
├── frontend/         (Linux Server / Windows Laptop)
│   ├── main-dashboard/    (Service Hub :8080)
│   ├── sdxl-ui/          (:7861)
│   ├── z-image-ui/       (:7862)
│   ├── admin-dashboard/  (:8000)
│   └── docker-compose.yml
└── DEVELOPMENT.md    (Full guide)
```

Done! Your GPU orchestrator is running.
