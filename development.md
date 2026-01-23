# GPU Orchestrator - Complete Development Guide

Full end-to-end development and testing workflow for backend (Ubuntu GPU PC) + frontend (Windows laptop).

## Architecture

```
┌──────────────────────┐
│  FRONTEND SERVER     │
│  (Linux/Windows)     │
│                      │
│ Main Hub :8080       │
│ SDXL UI :7861        │
│ Z-Image UI :7862     │
│ Qwen UI :7863        │
│ Admin UI :8000       │
│ - Polls GPU status   │
│ - Submits jobs       │
└──────────┬───────────┘
           │
        HTTP (polls every 3s)
           │
           ↓
┌──────────────────────┐
│  UBUNTU GPU PC       │
│  (192.168.50.28)     │
│                      │
│ Backend :9090        │
│ - Orchestrator       │
│ - SDXL Worker        │
│ - Z-Image Worker     │
│ - Qwen Worker        │
│ - PostgreSQL         │
│ - Redis              │
│ - etcd               │
└──────────────────────┘
```

---

## PART 1: Backend Setup (Ubuntu GPU PC)

### Step 1: Initial Configuration

```bash
cd backend

# Create environment file
cp ../.env.example .env

# Edit with your credentials
nano .env
```

**.env file:**
```env
POSTGRES_USER=orchestrator
POSTGRES_PASSWORD=your-secure-password
POSTGRES_DB=gpu_orchestrator
REDIS_HOST=redis
REDIS_PORT=6379
ETCD_HOST=etcd
ETCD_PORT=2379
HF_TOKEN=your-huggingface-token  # Optional
```

### Step 2: Start Infrastructure (PostgreSQL, Redis, etcd)

```bash
# Start core services
docker-compose up -d postgres redis etcd

# Wait for services to be healthy
sleep 10
docker-compose ps

# Verify all are healthy (Status = "Up")
```

**Output should show:**
```
postgres    | postgres:15-alpine          | Up (healthy)
redis       | redis:7-alpine              | Up (healthy)
etcd        | quay.io/coreos/etcd:v3.5.9  | Up (healthy)
```

### Step 3: Start Orchestrator

```bash
# Build and run orchestrator
docker-compose up -d orchestrator

# Wait for it to start
sleep 5
docker-compose ps

# Check logs
docker-compose logs orchestrator
```

**Success indicators:**
```
[INFO] Connected to PostgreSQL
[INFO] Connected to Redis
[INFO] Connected to etcd
[INFO] Orchestrator running on :8080 (internal, exposed as :9090 externally)
```

### Step 4: Test Orchestrator Endpoints

```bash
# Check GPU health (main endpoint for polling)
curl http://localhost:9090/health/gpu

# Expected response:
# {
#   "status": "ok",
#   "is_available": true,
#   "free_vram_gb": "22.5",
#   "used_vram_gb": "1.5",
#   "total_vram_gb": "24.0",
#   "utilization_pct": "2.1",
#   "active_jobs": 0
# }

# Check worker registration
curl http://localhost:9090/workers

# Expected response:
# {
#   "count": 0,
#   "workers": []
# }  (will populate when workers start)
```

### Step 5: Start GPU Workers

```bash
# Start all workers (uncomment them in docker-compose.yml first)
docker-compose up -d sdxl-worker z-image-worker qwen-worker

# Wait for registration
sleep 10
docker-compose ps

# Verify workers registered
curl http://localhost:9090/workers

# Expected response:
# {
#   "count": 3,
#   "workers": [
#     {"worker_id": "sdxl-worker", "status": "healthy"},
#     {"worker_id": "z-image-worker", "status": "healthy"},
#     {"worker_id": "qwen-worker", "status": "healthy"}
#   ]
# }
```

### Step 6: Backend is Ready

```bash
# Full health check
echo "=== GPU Health ===" && \
  curl -s http://localhost:9090/health/gpu | jq . && \
  echo "" && \
  echo "=== Workers ===" && \
  curl -s http://localhost:9090/workers | jq .
```

---

## PART 2: Frontend Setup (Windows Laptop)

### Step 1: Prepare Frontend Code

**Option A: Python (Recommended for development)**

```powershell
cd frontend\sdxl-ui

# Install dependencies
pip install -r requirements.txt
```

**Option B: Docker**

```powershell
cd frontend

# No setup needed, Docker handles it
```

### Step 2: Configure Orchestrator URL

**For native Python:**
```powershell
$env:ORCHESTRATOR_URL = "http://192.168.50.28:9090"
```

**For Docker:**
Edit `frontend\docker-compose.yml`:
```yaml
sdxl-ui:
  environment:
    - ORCHESTRATOR_URL=http://192.168.50.28:9090
```

### Step 3: Start Frontend Services

**Python (recommended for single dev UI):**
```powershell
cd frontend\sdxl-ui
$env:ORCHESTRATOR_URL = "http://192.168.50.28:9090"
python app.py
# Runs on :7861
```

**Docker (all services at once):**
```powershell
cd frontend
docker-compose up -d

# Check logs
docker-compose logs -f
```

### Step 4: Access Main Dashboard (Hub)

**Main entry point:** Open browser **http://localhost:8080**

You should see:
- **GPU Orchestrator Hub** (service discovery center)
- **GPU Status Card** with live metrics
- **Available Services** grid with clickable service cards
- **Registered Workers** list
- Each service shows: name, description, status (online/offline), and "Open" button

**From the hub, access individual services:**
- Click "Open" on any service card
- Or access directly:
  - **SDXL UI**: http://localhost:7861
  - **Z-Image UI**: http://localhost:7862
  - **Qwen UI**: http://localhost:7863
  - **Admin Dashboard**: http://localhost:8000

---

## PART 3: End-to-End Testing

### Test 1: GPU Polling Works

```powershell
# Check GPU health directly from backend
curl http://192.168.50.28:9090/health/gpu

# Check through frontend (proxy)
curl http://localhost:7861/api/gpu/health

# Both should return same GPU status
```

**In browser:** Navbar should show green indicator with "GPU Ready".

### Test 2: Job Submission (GPU Available)

```powershell
# Submit a job
$jobResponse = curl -X POST http://localhost:7861/api/generate `
  -H "Content-Type: application/json" `
  -d '{
    "prompt": "A beautiful mountain landscape at sunset",
    "width": 512,
    "height": 512,
    "num_inference_steps": 20
  }' | ConvertFrom-Json

$jobId = $jobResponse.job_id
echo "Job ID: $jobId"
echo "Status: $($jobResponse.status)"
echo "GPU Available: $($jobResponse.gpu_available)"
```

**Expected response:**
```json
{
  "success": true,
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "queued",
  "gpu_available": true,
  "free_vram_gb": "22.5"
}
```

### Test 3: Monitor Job Execution

**In new PowerShell terminal:**
```powershell
# Watch GPU status change in real-time
while($true) {
  curl -s http://192.168.50.28:9090/health/gpu | jq '.is_available, .utilization_pct'
  Start-Sleep -Seconds 1
}
```

**In browser:**
- Navbar indicator should turn **red** ("GPU Busy • 1 job processing")
- Shows active job count

### Test 4: Check Job Status

```powershell
# Poll job status
$jobId = "550e8400-e29b-41d4-a716-446655440000"

# Poll every 5 seconds
while($true) {
  $status = curl -s http://localhost:7861/api/status/$jobId | ConvertFrom-Json
  echo "Job Status: $($status.status)"
  echo "---"
  Start-Sleep -Seconds 5
  
  if($status.status -eq "COMPLETED") {
    echo "Job finished!"
    echo "Result (base64 image): $($status.result.Substring(0, 50))..."
    break
  }
}
```

**Expected progression:**
1. `status: "QUEUED"` → Job waiting
2. `status: "PROCESSING"` → Worker processing (GPU busy)
3. `status: "COMPLETED"` → Job finished (GPU goes back to ready)

### Test 5: Full Browser Workflow

1. **Open** http://localhost:7861
2. **Check GPU status** (navbar) → Should show "GPU Ready • 22.5GB free"
3. **Enter prompt**: "A futuristic city at night"
4. **Click "Generate"**
5. **Watch navbar** → Should turn red immediately ("GPU Busy")
6. **Wait for result** → Image appears in right panel
7. **Watch navbar** → Turns green again when done
8. **Check execution time** → Display shows "Completed in X.X seconds"

---

## PART 4: Monitoring & Debugging

### View All Logs (Ubuntu)

```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f orchestrator
docker-compose logs -f sdxl-worker
docker-compose logs -f postgres
```

### Monitor GPU in Real-Time

```bash
# GPU metrics (Ubuntu terminal)
watch -n 1 'curl -s http://localhost:9090/health/gpu | jq .'

# Or with nvidia-smi
nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total --format=csv --loop-ms=1000
```

### Database Queries (Ubuntu)

```bash
# Connect to PostgreSQL
docker-compose exec postgres psql -U orchestrator -d gpu_orchestrator

# View all jobs
SELECT id, status, app_id, created_at FROM jobs ORDER BY created_at DESC LIMIT 10;

# Count jobs by status
SELECT status, COUNT(*) FROM jobs GROUP BY status;

# Find slow jobs
SELECT id, app_id, EXTRACT(EPOCH FROM (completed_at - started_at)) as duration_sec 
FROM jobs WHERE status = 'COMPLETED' ORDER BY duration_sec DESC LIMIT 5;

# Exit
\q
```

### Debug Frontend Issues (Windows)

```powershell
# Browser DevTools
# Press F12 → Console tab

# Check network requests:
# 1. Look for GET /api/gpu/health (every 3 seconds)
# 2. Look for POST /api/generate (when submitting)
# 3. Check for errors in red

# Check frontend health
curl http://localhost:7861/health
# Should return: {"status": "healthy", "orchestrator": "connected"}

# Check GPU polling endpoint
curl http://localhost:7861/api/gpu/health
# Should return GPU status JSON
```

---

## PART 5: Troubleshooting

### Issue: "GPU Status Unknown" in Browser

**Diagnosis:**
```powershell
# Test connection from laptop to Ubuntu
ping 192.168.50.28  # Should respond

# Test port 9090
Test-NetConnection -ComputerName 192.168.50.28 -Port 9090  # Should succeed

# Test endpoint directly
curl http://192.168.50.28:9090/health/gpu  # Should return JSON
```

**Solutions:**
1. Check Ubuntu firewall:
   ```bash
   sudo ufw status
   sudo ufw allow 9090/tcp
   ```

2. Check orchestrator is running:
   ```bash
   docker-compose ps orchestrator  # Should say "Up"
   docker-compose logs orchestrator  # Check for errors
   ```

3. Wrong IP in frontend docker-compose.yml:
   ```bash
   # Get Ubuntu IP:
   ip addr show | grep "inet " | grep -v 127.0.0.1

   # Update frontend/docker-compose.yml with correct IP:port
   # ORCHESTRATOR_URL=http://YOUR_UBUNTU_IP:9090
   ```

### Issue: Job Stuck in QUEUED

**Diagnosis:**
```bash
# Check if worker is running
docker-compose ps sdxl-worker  # Should say "Up"

# Check worker logs
docker-compose logs sdxl-worker  # Look for errors

# Check if Redis queue has data
docker-compose exec redis redis-cli XLEN jobs:sdxl  # Should be > 0 if queued

# Check GPU utilization
nvidia-smi  # Should show GPU usage if processing
```

**Solutions:**
1. Restart worker:
   ```bash
   docker-compose restart sdxl-worker
   ```

2. Check GPU memory:
   ```bash
   nvidia-smi  # Should have enough free VRAM
   ```

3. Check database constraints:
   ```bash
   docker-compose exec postgres psql -U orchestrator -d gpu_orchestrator \
     -c "SELECT * FROM jobs WHERE id = 'your-job-uuid';"
   ```

### Issue: Frontend Won't Start (Python)

```powershell
# Check Python version
python --version  # Should be 3.11+

# Check dependencies
pip list | findstr fastapi uvicorn requests

# Reinstall if needed
pip install --upgrade -r requirements.txt

# Check port not in use
Get-NetTCPConnection | findstr "7861"  # If listed, port in use

# Try different port
set ORCHESTRATOR_URL=http://192.168.50.28:8080
python -c "from fastapi import FastAPI; from uvicorn import run; app = FastAPI(); run(app, host='0.0.0.0', port=8862)"
```

### Issue: Worker GPU Not Detected

```bash
# Check NVIDIA driver
nvidia-smi  # Should list GPU

# Check container can access GPU
docker run --rm --gpus all nvidia/cuda:11.8.0-base-ubuntu22.04 nvidia-smi

# If fails, reinstall NVIDIA Container Toolkit:
# https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html

# Restart Docker daemon
sudo systemctl restart docker

# Restart workers
docker-compose restart sdxl-worker
```

---

## PART 6: Full Test Checklist

- [ ] Backend started: `docker-compose ps` shows all services Up
- [ ] Orchestrator endpoint works: `curl http://localhost:8080/health/gpu`
- [ ] Workers registered: `curl http://localhost:8080/workers` shows 3 workers
- [ ] Frontend started: Python or Docker running
- [ ] Can reach frontend: `curl http://localhost:7861/health`
- [ ] GPU status indicator visible: Green "GPU Ready" in navbar
- [ ] Job submission works: curl POST /api/generate succeeds
- [ ] Job appears in queue: `curl http://localhost:7861/api/status/{job_id}`
- [ ] GPU utilization changes: `nvidia-smi` shows usage during job
- [ ] Job completes: Status shows "COMPLETED" 
- [ ] GPU status returns to ready: Navbar turns green again
- [ ] Multi-job support: Submit 2+ jobs, all execute

---

## PART 7: Development Workflow

### Making Changes

**Backend (Orchestrator code):**
```bash
# Edit file
nano orchestrator/main.go

# Rebuild
docker-compose build --no-cache orchestrator
docker-compose up -d orchestrator

# Check logs
docker-compose logs -f orchestrator
```

**Backend (Worker code):**
```bash
# Edit file
nano worker/sdxl-image-gen_worker/handler.py

# Rebuild
docker-compose build --no-cache sdxl-worker
docker-compose up -d sdxl-worker
```

**Frontend (Python):**
```powershell
# Edit file (app.py auto-reloads if running with reload=True)
# Just save and refresh browser

# Or restart
Ctrl+C
python app.py
```

**Frontend (HTML/JS/CSS):**
```powershell
# Edit template or static files
# Just refresh browser (no restart needed)
```

### Testing Changes

**After any change:**
1. Check service started: `docker-compose ps`
2. Check logs for errors: `docker-compose logs service-name`
3. Test endpoint: `curl http://localhost:8080/...`
4. Submit test job
5. Monitor execution: `watch -n 1 'nvidia-smi'`
6. Verify completion

---

## Quick Reference Commands

### Ubuntu (Backend)

```bash
# Start everything
cd backend
docker-compose up -d

# View status
docker-compose ps

# View logs
docker-compose logs -f

# Stop
docker-compose down

# Test endpoints
curl http://localhost:9090/health/gpu
curl http://localhost:9090/workers
curl http://localhost:9090/submit -X POST -H "Content-Type: application/json" -d '{"app_id":"sdxl-image-gen","params":{"prompt":"test"}}'

# Database
docker-compose exec postgres psql -U orchestrator -d gpu_orchestrator
```

### Windows Laptop (Frontend)

```powershell
# Start frontend (Python)
cd frontend\sdxl-ui
$env:ORCHESTRATOR_URL="http://192.168.50.28:9090"
python app.py

# Test endpoints
curl http://localhost:7861/health
curl http://localhost:7861/api/gpu/health
curl -X POST http://localhost:7861/api/generate -H "Content-Type: application/json" -d '{"prompt":"test","width":512,"height":512}'

# View status in browser
# http://localhost:7861
```

---

## Success Criteria

✅ **Backend is working if:**
- All services running: `docker-compose ps` shows all "Up"
- GPU endpoint responds: `curl http://localhost:9090/health/gpu` returns JSON
- Workers registered: `curl http://localhost:9090/workers` shows 3+ workers
- Jobs execute: GPU utilization changes during processing

✅ **Frontend is working if:**
- UI loads: http://localhost:7861 shows SDXL form
- GPU status displays: Navbar shows "GPU Ready" or "GPU Busy"
- Job submits: Form submission succeeds
- Real-time updates: Navbar changes color during processing

✅ **Full integration works if:**
- Submit job from frontend
- Watch GPU status change to "GPU Busy" in navbar
- Job executes on Ubuntu PC
- Results return to frontend
- GPU status returns to "GPU Ready"

---

## Next Steps

1. **Deploy backend to production Ubuntu**
   - Keep docker-compose.yml same
   - Update firewall rules

2. **Deploy frontend to public Linux server**
   - Update ORCHESTRATOR_URL to public IP
   - Use docker-compose.yml from frontend/
   - Add HTTPS with nginx

3. **Add authentication**
   - Add API token to /health/gpu
   - Protect /submit endpoint
   - Store tokens securely

4. **Monitor in production**
   - Setup Prometheus metrics
   - Add logging aggregation (ELK)
   - Monitor GPU utilization over time

---

## Support

**Common questions:**

Q: How do I change the GPU polling interval?
A: Edit `frontend/sdxl-ui/static/js/app.js` line ~178, change `3000` to desired ms.

Q: How do I add a new worker model?
A: Copy `worker/sdxl_worker/` to `worker/new-model_worker/`, edit handler, update `config/apps.yaml`, uncomment in `docker-compose.yml`.

Q: How do I increase GPU memory limit?
A: Edit `docker-compose.yml`, add under `deploy.resources.reservations.devices[0]`: `memory: "22G"`

Q: How do I debug a stuck job?
A: Check `docker-compose logs <worker-name>`, check `nvidia-smi`, query PostgreSQL for job details.

Q: How do I reset the database?
A: `docker-compose down -v` (removes volumes), then `docker-compose up -d` (recreates DB).

Q: What port does the orchestrator run on?
A: Internal: 8080, External: 9090. Always use 9090 when connecting from frontend or other machines.

---

See also:
- `SEPARATION_GUIDE.md` - Frontend/Backend separation details
- `backend/README.md` - Backend documentation
- `frontend/README.md` - Frontend documentation  
- `frontend/WINDOWS_DEV_SETUP.md` - Windows development tips
