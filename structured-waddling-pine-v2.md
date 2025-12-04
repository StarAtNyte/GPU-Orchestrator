# GPU Orchestrator Admin Dashboard - Implementation Plan (v2)
## Updated for Dynamic Worker System

## Executive Summary

Build a comprehensive admin dashboard for complete control over the GPU Orchestrator system using FastAPI + Jinja2 (matching existing frontend pattern). The dashboard will provide:

- **Simple password-based admin authentication**
- **Complete job management** (view, filter, cancel, retry)
- **Real-time GPU metrics** (utilization, VRAM, temperature)
- **Dynamic worker monitoring & control** (switch workers, view status, cleanup GPU)
- **User & cost tracking**

**Architecture**: Standalone FastAPI frontend (port 8090) + Extended Go orchestrator APIs (port 8080)

**Key System Features**:
- **Exclusive Worker Mode**: Only one GPU worker active at a time
- **Dynamic Worker Switching**: Automatic worker switching based on job app_id
- **GPU Memory Management**: HTTP cleanup endpoints for releasing GPU memory
- **etcd Service Discovery**: Workers register in etcd with TTL-based heartbeats

**Timeline**: 4 implementation phases

---

## Phase 1: Foundation - Auth & Database Schema

### 1.1 Database Migration

**File**: `orchestrator/migrations/000003_add_admin_schema.up.sql`

Create migration with:

```sql
-- Admin authentication tables
CREATE TABLE admin_users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR(50) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    email VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_login TIMESTAMPTZ,
    is_active BOOLEAN DEFAULT TRUE
);

CREATE TABLE admin_sessions (
    session_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    admin_id UUID REFERENCES admin_users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    ip_address INET,
    user_agent TEXT,
    last_activity TIMESTAMPTZ DEFAULT NOW()
);

-- Insert default admin (password: 'admin123')
INSERT INTO admin_users (username, password_hash, email)
VALUES ('admin', '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewY5lZjKpqMxlF4W', 'admin@localhost');

-- Cost tracking tables
CREATE TABLE cost_rules (
    id SERIAL PRIMARY KEY,
    app_id VARCHAR(50) NOT NULL,
    cost_per_second DECIMAL(10, 6) DEFAULT 0.001,
    cost_per_job DECIMAL(10, 4) DEFAULT 0,
    effective_from TIMESTAMPTZ DEFAULT NOW(),
    effective_until TIMESTAMPTZ,
    description TEXT
);

-- Insert default cost rules
INSERT INTO cost_rules (app_id, cost_per_second, cost_per_job, description)
VALUES
    ('z-image', 0.002, 0.01, 'Z-Image Turbo - $0.002/sec + $0.01/job'),
    ('sdxl-image-gen', 0.003, 0.015, 'SDXL - $0.003/sec + $0.015/job');

-- Auto-calculate cost on job completion
CREATE OR REPLACE FUNCTION calculate_job_cost()
RETURNS TRIGGER AS $$
DECLARE
    duration_seconds NUMERIC;
    cost_rule RECORD;
    calculated_cost NUMERIC;
BEGIN
    IF NEW.status = 'COMPLETED' AND NEW.completed_at IS NOT NULL THEN
        duration_seconds := EXTRACT(EPOCH FROM (NEW.completed_at - NEW.started_at));

        SELECT * INTO cost_rule
        FROM cost_rules
        WHERE app_id = NEW.app_id
            AND effective_from <= NEW.created_at
            AND (effective_until IS NULL OR effective_until > NEW.created_at)
        ORDER BY effective_from DESC LIMIT 1;

        IF cost_rule IS NOT NULL THEN
            calculated_cost := (duration_seconds * cost_rule.cost_per_second) + cost_rule.cost_per_job;
            NEW.cost_estimate := calculated_cost;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_calculate_job_cost
    BEFORE UPDATE ON jobs
    FOR EACH ROW
    EXECUTE FUNCTION calculate_job_cost();

-- Audit logging
CREATE TABLE admin_audit_log (
    id BIGSERIAL PRIMARY KEY,
    admin_id UUID REFERENCES admin_users(id),
    action VARCHAR(100) NOT NULL,
    resource_type VARCHAR(50),
    resource_id VARCHAR(255),
    details JSONB,
    ip_address INET,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- GPU metrics table (TimescaleDB hypertable)
CREATE TABLE gpu_metrics (
    time TIMESTAMPTZ NOT NULL,
    worker_id VARCHAR(100) NOT NULL,
    gpu_id INTEGER NOT NULL,
    gpu_utilization FLOAT,
    vram_used_mb BIGINT,
    vram_total_mb BIGINT,
    temperature_c INTEGER,
    power_draw_w INTEGER
);

-- Convert to TimescaleDB hypertable
SELECT create_hypertable('gpu_metrics', 'time');

-- Create index for efficient queries
CREATE INDEX idx_gpu_metrics_worker_time ON gpu_metrics (worker_id, time DESC);
```

### 1.2 Admin Dashboard Frontend Structure

**Directory**: `frontends/admin-dashboard/`

Create structure:
```
frontends/admin-dashboard/
├── app.py                          # FastAPI application
├── requirements.txt
├── Dockerfile
├── auth/
│   └── session.py                  # Session management utilities
├── templates/
│   ├── base.html                  # Base template with nav
│   ├── login.html                 # Login page
│   ├── dashboard.html             # Overview dashboard (Phase 4)
│   ├── jobs.html                  # Job management (Phase 2)
│   ├── workers.html               # Worker monitoring (Phase 3)
│   ├── metrics.html               # GPU metrics (Phase 3)
│   └── config.html                # App config viewer (Phase 4)
└── static/
    ├── css/
    │   └── admin.css              # Dashboard styles
    └── js/
        ├── jobs.js                # Job management logic
        ├── workers.js             # Worker control & monitoring
        ├── metrics.js             # Chart rendering (Chart.js)
        └── sse.js                 # Server-Sent Events handler
```

### 1.3 Authentication Implementation

**File**: `frontends/admin-dashboard/auth/session.py`

Implement:
- `create_session(admin_id, ip_address, user_agent)` → session_id
- `validate_session(session_id)` → admin info or None
- `destroy_session(session_id)` → bool
- `cleanup_expired_sessions()` → int (cleanup count)

**File**: `frontends/admin-dashboard/app.py`

```python
from fastapi import FastAPI, Request, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import bcrypt

app = FastAPI(title="GPU Orchestrator Admin")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Dependency for protected routes
def get_current_admin(request: Request):
    session_id = request.cookies.get("admin_session")
    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    admin = validate_session(session_id)
    if not admin:
        raise HTTPException(status_code=401, detail="Invalid session")

    return admin

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    # Validate credentials
    # Create session
    # Set cookie
    # Redirect to /dashboard
    pass

@app.get("/logout")
async def logout(request: Request):
    # Destroy session
    # Clear cookie
    # Redirect to /login
    pass
```

### 1.4 Docker Configuration

**File**: `docker-compose.yml`

Add service:
```yaml
  admin-dashboard:
    build:
      context: frontends/admin-dashboard
    container_name: admin-dashboard
    restart: unless-stopped
    depends_on:
      - orchestrator
      - postgres
    environment:
      - ORCHESTRATOR_URL=http://orchestrator:8080
      - POSTGRES_HOST=postgres
      - POSTGRES_USER=${POSTGRES_USER}
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
      - POSTGRES_DB=${POSTGRES_DB}
    ports:
      - "8090:8090"
```

**File**: `frontends/admin-dashboard/Dockerfile`
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8090
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8090"]
```

**Phase 1 Deliverables**:
- ✅ Database schema with auth tables
- ✅ Working login/logout system
- ✅ Protected routes with session validation
- ✅ Basic UI structure (base template + login page)

---

## Phase 2: Job Management

### 2.1 Go Orchestrator - Admin API Endpoints

**File**: `orchestrator/admin_handlers.go` (NEW FILE)

Implement handlers:

```go
// GET /admin/jobs - List jobs with filtering and pagination
func adminJobsHandler(w http.ResponseWriter, r *http.Request) {
    // Query params: status, app_id, worker_id, limit, offset, sort, order
    // Return: {jobs: [...], total: N, page: X, limit: Y}
}

// POST /admin/jobs/{id}/cancel - Cancel a job
func adminCancelJobHandler(w http.ResponseWriter, r *http.Request) {
    // Update job status to FAILED
    // Set error_log = 'Cancelled by admin'
    // Set completed_at = NOW()
}

// POST /admin/jobs/{id}/retry - Retry a failed job
func adminRetryJobHandler(w http.ResponseWriter, r *http.Request) {
    // Get original job params
    // Create new job with same params
    // Return new job_id
}
```

**File**: `orchestrator/main.go`

Add routes (after existing routes):
```go
http.HandleFunc("/admin/jobs", adminJobsHandler)
http.HandleFunc("/admin/jobs/", adminJobActionHandler)
```

Key SQL queries:

```sql
-- List jobs with filters
SELECT id, app_id, status, created_at, started_at, completed_at,
       worker_id, cost_estimate, error_log
FROM jobs
WHERE ($1 = '' OR status = $1)
  AND ($2 = '' OR app_id = $2)
  AND ($3 = '' OR worker_id = $3)
ORDER BY created_at DESC
LIMIT $4 OFFSET $5;

-- Cancel job
UPDATE jobs
SET status = 'FAILED',
    error_log = 'Cancelled by admin',
    completed_at = NOW()
WHERE id = $1
  AND status IN ('PENDING', 'QUEUED', 'PROCESSING');

-- Retry job
INSERT INTO jobs (id, app_id, status, params, created_at)
SELECT gen_random_uuid(), app_id, 'PENDING', params, NOW()
FROM jobs WHERE id = $1 AND status = 'FAILED';
```

### 2.2 Admin Dashboard - Jobs Page

**File**: `frontends/admin-dashboard/templates/jobs.html`

Features:
- Job table with columns: ID, App, Status, Created, Duration, Worker, Cost, Actions
- Filters: Status dropdown, App dropdown, Search by job ID
- Pagination (50 jobs per page)
- Actions: Cancel button, Retry button, View details link
- Status badges with colors (COMPLETED=green, FAILED=red, PROCESSING=blue, etc.)
- Auto-refresh every 10 seconds

**File**: `frontends/admin-dashboard/static/js/jobs.js`

```javascript
// Server-Sent Events for real-time updates
const evtSource = new EventSource('/stream/jobs');
evtSource.onmessage = (event) => {
    const jobs = JSON.parse(event.data);
    updateJobTable(jobs);
};

// Cancel job
async function cancelJob(jobId) {
    const response = await fetch(`/api/jobs/${jobId}/cancel`, {method: 'POST'});
    if (response.ok) {
        showNotification('Job cancelled successfully');
        refreshJobTable();
    }
}

// Retry job
async function retryJob(jobId) {
    const response = await fetch(`/api/jobs/${jobId}/retry`, {method: 'POST'});
    if (response.ok) {
        const data = await response.json();
        showNotification(`New job created: ${data.new_job_id}`);
        refreshJobTable();
    }
}
```

**File**: `frontends/admin-dashboard/app.py`

Add routes:
```python
@app.get("/jobs", response_class=HTMLResponse)
async def jobs_page(request: Request, admin: dict = Depends(get_current_admin)):
    return templates.TemplateResponse("jobs.html", {"request": request, "admin": admin})

@app.get("/api/jobs")
async def list_jobs(status: str = "", app_id: str = "", limit: int = 50, offset: int = 0):
    # Proxy to orchestrator /admin/jobs
    response = requests.get(f"{ORCHESTRATOR_URL}/admin/jobs", params={...})
    return response.json()

@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, admin: dict = Depends(get_current_admin)):
    # Log audit action
    # Proxy to orchestrator
    response = requests.post(f"{ORCHESTRATOR_URL}/admin/jobs/{job_id}/cancel")
    return response.json()

@app.get("/stream/jobs")
async def stream_jobs(request: Request, admin: dict = Depends(get_current_admin)):
    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            jobs = fetch_recent_jobs()
            yield f"data: {json.dumps(jobs, default=str)}\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

**Phase 2 Deliverables**:
- ✅ Job listing API with filters
- ✅ Job cancel/retry endpoints
- ✅ Complete jobs management UI
- ✅ Real-time job status updates via SSE
- ✅ Audit logging for admin actions

---

## Phase 3: GPU Metrics & Dynamic Worker Monitoring

### 3.1 Worker - GPU Metrics Collection

**File**: `worker/shared/gpu_metrics_collector.py` (NEW FILE)

```python
import pynvml
import threading
import time
import psycopg2
from typing import List, Dict

class GPUMetricsCollector:
    def __init__(self, worker_id: str, interval_seconds: int = 5):
        self.worker_id = worker_id
        self.interval = interval_seconds
        self.running = False
        pynvml.nvmlInit()
        self.device_count = pynvml.nvmlDeviceGetCount()

    def collect_metrics(self) -> List[Dict]:
        metrics = []
        for gpu_id in range(self.device_count):
            handle = pynvml.nvmlDeviceGetHandleByIndex(gpu_id)
            utilization = pynvml.nvmlDeviceGetUtilizationRates(handle)
            memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
            temperature = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
            power = pynvml.nvmlDeviceGetPowerUsage(handle) / 1000  # mW to W

            metrics.append({
                'gpu_id': gpu_id,
                'gpu_utilization': float(utilization.gpu),
                'vram_used_mb': memory.used // (1024 * 1024),
                'vram_total_mb': memory.total // (1024 * 1024),
                'temperature_c': temperature,
                'power_draw_w': int(power)
            })
        return metrics

    def write_metrics(self, metrics: List[Dict]):
        conn = get_postgres_connection()
        cursor = conn.cursor()
        for metric in metrics:
            cursor.execute("""
                INSERT INTO gpu_metrics
                (time, worker_id, gpu_id, gpu_utilization, vram_used_mb,
                 vram_total_mb, temperature_c, power_draw_w)
                VALUES (NOW(), %s, %s, %s, %s, %s, %s, %s)
            """, (self.worker_id, metric['gpu_id'], metric['gpu_utilization'],
                  metric['vram_used_mb'], metric['vram_total_mb'],
                  metric['temperature_c'], metric['power_draw_w']))
        conn.commit()

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self.collection_loop, daemon=True)
        self.thread.start()

    def collection_loop(self):
        while self.running:
            try:
                metrics = self.collect_metrics()
                self.write_metrics(metrics)
            except Exception as e:
                print(f"Metrics collection error: {e}")
            time.sleep(self.interval)
```

**File**: `worker/z-image_worker/main.py`

Modify main loop (add after line 46):
```python
from shared.gpu_metrics_collector import GPUMetricsCollector

def main():
    logger.info(f"[STARTUP] Starting Z-Image Worker")
    logger.info(f"Worker ID: {WORKER_ID}")
    logger.info(f"Queue: {STREAM_KEY}")
    logger.info(f"App ID: z-image")

    # Start GPU metrics collector
    metrics_collector = GPUMetricsCollector(worker_id=WORKER_ID, interval_seconds=5)
    metrics_collector.start()
    logger.info("[SUCCESS] GPU metrics collector started")

    # Start HTTP server in background thread
    http_thread = threading.Thread(target=start_http_server, daemon=True)
    http_thread.start()

    # ... rest of existing code
```

**File**: `worker/sdxl_worker/main.py`

Apply same changes as above.

**File**: `worker/shared/requirements.txt`

Add: `pynvml==11.5.0`

### 3.2 Go Orchestrator - Worker & Metrics APIs

**File**: `orchestrator/admin_handlers.go`

Add handlers:
```go
// GET /admin/workers/status - Get worker status from etcd and worker manager
func adminWorkersStatusHandler(w http.ResponseWriter, r *http.Request) {
    // Read from etcd /workers/* keys
    // Call worker_manager.py status to get active worker
    // Return worker list with status
}

// POST /admin/workers/start - Start a specific worker
func adminWorkersStartHandler(w http.ResponseWriter, r *http.Request) {
    // Parse request body: {worker_name: "z-image-worker"}
    // Call worker_manager.py switch <worker-name> (starts if not running)
    // Return success/failure with startup time estimate
}

// POST /admin/workers/stop - Stop the active worker
func adminWorkersStopHandler(w http.ResponseWriter, r *http.Request) {
    // Call worker_manager.py stop
    // Return success/failure
}

// POST /admin/workers/switch - Switch to a different worker
func adminWorkersSwitchHandler(w http.ResponseWriter, r *http.Request) {
    // Parse request body: {worker_name: "z-image-worker"}
    // Call worker_manager.py switch <worker-name>
    // Return success/failure
}

// POST /admin/workers/{worker_id}/cleanup - Trigger GPU cleanup
func adminWorkerCleanupHandler(w http.ResponseWriter, r *http.Request) {
    // Get worker container name from worker_id
    // Call HTTP POST to http://<worker-container>:8000/cleanup
    // Return cleanup result
}

// GET /admin/workers/ui-info - Get UI URLs for all workers
func adminWorkersUIInfoHandler(w http.ResponseWriter, r *http.Request) {
    // Read apps.yaml to get frontend_url for each app
    // Map worker names to their UI URLs
    // Return: {worker_name: {app_id, ui_url, ui_port}}
}

// GET /admin/metrics/gpu - Query time-series GPU metrics
func adminGPUMetricsHandler(w http.ResponseWriter, r *http.Request) {
    // Query params: worker_id, start_time, end_time, interval
    // Use TimescaleDB time_bucket for aggregation
}

// GET /admin/metrics/latest - Get latest GPU metrics for all workers
func adminLatestMetricsHandler(w http.ResponseWriter, r *http.Request) {
    // Query latest gpu_metrics for each worker_id
    // Return current GPU state
}
```

SQL queries:
```sql
-- GPU metrics time-series
SELECT
    time_bucket('5 minutes', time) AS bucket,
    worker_id,
    gpu_id,
    AVG(gpu_utilization) as avg_utilization,
    AVG(vram_used_mb) as avg_vram_used,
    AVG(temperature_c) as avg_temperature
FROM gpu_metrics
WHERE worker_id = $1
  AND time >= $2
  AND time <= $3
GROUP BY bucket, worker_id, gpu_id
ORDER BY bucket;

-- Latest metrics per worker
SELECT DISTINCT ON (worker_id, gpu_id)
    worker_id, gpu_id, time,
    gpu_utilization, vram_used_mb, vram_total_mb,
    temperature_c, power_draw_w
FROM gpu_metrics
ORDER BY worker_id, gpu_id, time DESC;
```

### 3.3 Admin Dashboard - Workers & Metrics Pages

**File**: `frontends/admin-dashboard/templates/workers.html`

Features:
- **Active Worker Card** (highlighted):
  - Worker name, App ID, Status badge (ONLINE/green)
  - GPU name, VRAM total
  - Live GPU metrics: Utilization %, VRAM usage (X/Y MB), Temperature
  - **Link to Worker UI** (opens in new tab) - e.g., "Open Z-Image UI →"
  - Actions: 
    - **Cleanup GPU** button (orange)
    - **Stop Worker** button (red)
- **Inactive Workers List**:
  - Worker name, App ID, Status (Stopped/gray)
  - VRAM requirement
  - **Link to Worker UI** (grayed out if worker stopped)
  - Actions: 
    - **Start Worker** button (green) - starts this worker only
    - **Switch to Worker** button (purple) - stops current & starts this one
- **Worker Switching**:
  - Confirmation dialog before switching
  - Progress indicator during switch (30-45s)
  - Success/error notifications
- **Worker Control**:
  - Start button: Starts worker without stopping others (if exclusive mode allows)
  - Stop button: Stops active worker completely
  - Switch button: Atomic stop current + start new worker
- Auto-refresh every 5 seconds via SSE

**File**: `frontends/admin-dashboard/static/js/workers.js`

```javascript
// Start worker
async function startWorker(workerName) {
    if (!confirm(`Start ${workerName}?`)) {
        return;
    }

    showProgress(`Starting ${workerName}...`);

    const response = await fetch('/api/workers/start', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({worker_name: workerName})
    });

    if (response.ok) {
        const data = await response.json();
        showNotification(`${workerName} started successfully (took ${data.startup_time}s)`);
        refreshWorkerStatus();
    } else {
        showError('Worker start failed');
    }
}

// Stop worker
async function stopWorker() {
    if (!confirm('Stop the active worker? This will free up GPU memory.')) {
        return;
    }

    showProgress('Stopping worker...');

    const response = await fetch('/api/workers/stop', {
        method: 'POST'
    });

    if (response.ok) {
        showNotification('Worker stopped successfully');
        refreshWorkerStatus();
    } else {
        showError('Worker stop failed');
    }
}

// Switch worker
async function switchWorker(workerName) {
    if (!confirm(`Switch to ${workerName}? This will stop the current worker.`)) {
        return;
    }

    showProgress(`Switching to ${workerName}...`);

    const response = await fetch('/api/workers/switch', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({worker_name: workerName})
    });

    if (response.ok) {
        showNotification(`Successfully switched to ${workerName}`);
        refreshWorkerStatus();
    } else {
        showError('Worker switch failed');
    }
}

// Cleanup GPU
async function cleanupGPU(workerId) {
    const response = await fetch(`/api/workers/${workerId}/cleanup`, {method: 'POST'});
    if (response.ok) {
        showNotification('GPU memory cleaned successfully');
        refreshMetrics();
    }
}

// Open worker UI in new tab
function openWorkerUI(workerName) {
    // Fetch UI info and open in new tab
    fetch('/api/workers/ui-info')
        .then(res => res.json())
        .then(data => {
            const uiInfo = data[workerName];
            if (uiInfo && uiInfo.ui_url) {
                window.open(uiInfo.ui_url, '_blank');
            } else {
                showError('No UI available for this worker');
            }
        });
}

// Real-time updates
const evtSource = new EventSource('/stream/workers');
evtSource.onmessage = (event) => {
    const workers = JSON.parse(event.data);
    updateWorkerCards(workers);
};
```

**File**: `frontends/admin-dashboard/templates/metrics.html`

Features:
- Time range selector (1h, 6h, 24h, 7d)
- Worker selector dropdown (shows all workers with historical data)
- Charts (using Chart.js):
  1. GPU Utilization % (line chart)
  2. VRAM Usage (area chart)
  3. Temperature (line chart)
  4. Power Draw (line chart)
- Multi-GPU support (multiple lines per chart if worker has multiple GPUs)
- Export data as CSV

**File**: `frontends/admin-dashboard/static/js/metrics.js`

```javascript
// Initialize Chart.js charts
let utilizationChart, vramChart, temperatureChart, powerChart;

function initCharts() {
    const ctx1 = document.getElementById('utilizationChart').getContext('2d');
    utilizationChart = new Chart(ctx1, {
        type: 'line',
        data: {datasets: []},
        options: {
            responsive: true,
            scales: {y: {min: 0, max: 100, title: {display: true, text: 'Utilization %'}}}
        }
    });

    // Similar for other charts...
}

// Fetch and update metrics
async function updateMetrics(workerId, timeRange) {
    const response = await fetch(`/api/metrics/gpu?worker_id=${workerId}&range=${timeRange}`);
    const data = await response.json();
    updateCharts(data);
}

function updateCharts(data) {
    // Transform data and update Chart.js datasets
    utilizationChart.data.datasets = data.map(gpu => ({
        label: `GPU ${gpu.gpu_id}`,
        data: gpu.utilization_points,
        borderColor: getColorForGPU(gpu.gpu_id)
    }));
    utilizationChart.update();
}
```

**File**: `frontends/admin-dashboard/app.py`

Add routes:
```python
@app.get("/workers", response_class=HTMLResponse)
async def workers_page(request: Request, admin: dict = Depends(get_current_admin)):
    return templates.TemplateResponse("workers.html", {"request": request, "admin": admin})

@app.get("/api/workers/status")
async def get_workers_status(admin: dict = Depends(get_current_admin)):
    # Proxy to orchestrator /admin/workers/status
    response = requests.get(f"{ORCHESTRATOR_URL}/admin/workers/status")
    return response.json()

@app.post("/api/workers/start")
async def start_worker(request: Request, admin: dict = Depends(get_current_admin)):
    data = await request.json()
    # Log audit action
    log_audit_action(admin['id'], 'START_WORKER', 'worker', data['worker_name'])
    # Proxy to orchestrator
    response = requests.post(f"{ORCHESTRATOR_URL}/admin/workers/start", json=data)
    return response.json()

@app.post("/api/workers/stop")
async def stop_worker(admin: dict = Depends(get_current_admin)):
    # Log audit action
    log_audit_action(admin['id'], 'STOP_WORKER', 'worker', 'active')
    # Proxy to orchestrator
    response = requests.post(f"{ORCHESTRATOR_URL}/admin/workers/stop")
    return response.json()

@app.post("/api/workers/switch")
async def switch_worker(request: Request, admin: dict = Depends(get_current_admin)):
    data = await request.json()
    # Log audit action
    log_audit_action(admin['id'], 'SWITCH_WORKER', 'worker', data['worker_name'])
    # Proxy to orchestrator
    response = requests.post(f"{ORCHESTRATOR_URL}/admin/workers/switch", json=data)
    return response.json()

@app.post("/api/workers/{worker_id}/cleanup")
async def cleanup_worker(worker_id: str, admin: dict = Depends(get_current_admin)):
    # Log audit action
    log_audit_action(admin['id'], 'CLEANUP_GPU', 'worker', worker_id)
    # Proxy to orchestrator
    response = requests.post(f"{ORCHESTRATOR_URL}/admin/workers/{worker_id}/cleanup")
    return response.json()

@app.get("/api/workers/ui-info")
async def get_workers_ui_info(admin: dict = Depends(get_current_admin)):
    # Proxy to orchestrator to get worker UI URLs
    response = requests.get(f"{ORCHESTRATOR_URL}/admin/workers/ui-info")
    return response.json()

@app.get("/metrics", response_class=HTMLResponse)
async def metrics_page(request: Request, admin: dict = Depends(get_current_admin)):
    return templates.TemplateResponse("metrics.html", {"request": request, "admin": admin})

@app.get("/api/metrics/gpu")
async def get_gpu_metrics(worker_id: str, range: str = "1h"):
    # Proxy to orchestrator /admin/metrics/gpu
    response = requests.get(f"{ORCHESTRATOR_URL}/admin/metrics/gpu", params={...})
    return response.json()

@app.get("/stream/workers")
async def stream_workers(request: Request, admin: dict = Depends(get_current_admin)):
    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            workers = fetch_worker_status()
            yield f"data: {json.dumps(workers, default=str)}\n\n"
            await asyncio.sleep(5)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

**Phase 3 Deliverables**:
- ✅ GPU metrics collection in workers (every 5 seconds)
- ✅ Dynamic worker monitoring dashboard
- ✅ **Worker start/stop controls** (independent of switching)
- ✅ Worker switching functionality
- ✅ **Links to individual worker UIs** (opens in new tab)
- ✅ GPU cleanup endpoint integration
- ✅ GPU metrics visualization with time-series charts
- ✅ Real-time updates via SSE
- ✅ Audit logging for all worker control actions

---

## Phase 4: Dashboard Overview & Polish

### 4.1 Go Orchestrator - Summary Metrics API

**File**: `orchestrator/admin_handlers.go`

Add handler:
```go
// GET /admin/metrics/summary - Dashboard overview metrics
func adminMetricsSummaryHandler(w http.ResponseWriter, r *http.Request) {
    // Return:
    // - Total jobs (all time)
    // - Jobs last 24h
    // - Active worker info
    // - Total cost (all time)
    // - Jobs by status breakdown
    // - Jobs by app breakdown
    // - Average job duration by app
    // - Worker uptime stats
}
```

### 4.2 Admin Dashboard - Overview Page

**File**: `frontends/admin-dashboard/templates/dashboard.html`

Layout:
1. **Summary Cards** (top row):
   - Total Jobs
   - Active Worker (with app name)
   - Jobs (24h)
   - Total Cost

2. **Worker Status** (second row):
   - Current worker card with live GPU metrics
   - Quick switch buttons for other workers

3. **Charts** (middle):
   - Job Status Pie Chart (COMPLETED, FAILED, PROCESSING, QUEUED)
   - Jobs per Hour (last 24h) Bar Chart
   - Cost per App (pie chart)

4. **Recent Jobs Table** (bottom):
   - Last 10 jobs with status, app, duration, cost

### 4.3 Configuration Viewer

**File**: `frontends/admin-dashboard/templates/config.html`

Features:
- **Apps Configuration** (from `apps.yaml`):
  - Table showing: App ID, Name, Type, Queue, GPU VRAM, Docker Image
  - Parameter schemas per app
- **Workers Configuration** (from `workers.yaml`):
  - Table showing: Worker Name, App ID, VRAM Required, Startup Time, Shutdown Time
  - Settings: Exclusive mode, Idle timeout, Max startup/shutdown wait

**File**: `orchestrator/admin_handlers.go`

```go
// GET /admin/config/apps - View apps configuration
func adminAppsConfigHandler(w http.ResponseWriter, r *http.Request) {
    // Return apps.yaml as JSON
}

// GET /admin/config/workers - View workers configuration
func adminWorkersConfigHandler(w http.ResponseWriter, r *http.Request) {
    // Read /app/config/workers.yaml
    // Return as JSON
}
```

### 4.4 UI Polish & Documentation

**CSS Enhancements** (`frontends/admin-dashboard/static/css/admin.css`):
- Consistent color scheme (follow z-image-ui pattern)
- Responsive design (mobile-friendly)
- Loading states and spinners
- Toast notifications for actions
- Status badges with icons
- Dark mode support (optional)

**Navigation** (`templates/base.html`):
```html
<nav>
    <a href="/dashboard">Dashboard</a>
    <a href="/jobs">Jobs</a>
    <a href="/workers">Workers</a>
    <a href="/metrics">Metrics</a>
    <a href="/config">Config</a>
    <a href="/logout">Logout</a>
</nav>
```

**Documentation**:
- Update `README.md` with admin dashboard section
- Document API endpoints
- Create admin user guide
- Add worker switching guide

**Phase 4 Deliverables**:
- ✅ Complete overview dashboard
- ✅ Cost tracking functional
- ✅ Configuration viewer
- ✅ Polished UI with consistent styling
- ✅ Documentation complete

---

## Critical Files to Modify

### New Files to Create:
1. `orchestrator/migrations/000003_add_admin_schema.up.sql` - Database schema
2. `orchestrator/admin_handlers.go` - Admin API handlers
3. `frontends/admin-dashboard/` - Complete new directory structure
4. `worker/shared/gpu_metrics_collector.py` - GPU metrics collection

### Existing Files to Modify:
1. `orchestrator/main.go` - Add admin API routes
2. `worker/z-image_worker/main.py` - Integrate metrics collector
3. `worker/sdxl_worker/main.py` - Integrate metrics collector
4. `worker/shared/requirements.txt` - Add pynvml
5. `docker-compose.yml` - Add admin-dashboard service
6. `ADDING_NEW_APPS.md` - Document admin dashboard usage

---

## Dynamic Worker System Integration

### Worker Manager Integration

The admin dashboard integrates with the existing `worker_manager.py` system:

**Worker Switching Flow**:
1. Admin clicks "Switch to Worker X" in dashboard
2. Dashboard calls `POST /admin/workers/switch` with `{worker_name: "x-worker"}`
3. Orchestrator calls `python3 /app/scripts/worker_manager.py switch x-worker`
4. Worker manager:
   - Stops current worker (if any)
   - Waits for graceful shutdown (10s)
   - Starts new worker
   - Waits for startup (30-45s)
   - Updates state file
5. Dashboard polls for completion and shows success

**GPU Cleanup Flow**:
1. Admin clicks "Cleanup GPU" for active worker
2. Dashboard calls `POST /admin/workers/{worker_id}/cleanup`
3. Orchestrator calls `POST http://{worker-container}:8000/cleanup`
4. Worker's HTTP server calls `handler.offload_model()`
5. GPU memory is released
6. Dashboard shows success notification

**Worker Status Monitoring**:
- Dashboard queries etcd `/workers/*` keys for heartbeats
- Reads state file `/tmp/gpu_orchestrator_active_worker.txt` for active worker
- Shows worker as ONLINE if heartbeat within last 10 seconds
- Shows worker as OFFLINE otherwise

### etcd Integration

Workers register in etcd with TTL-based leases:

```python
# In worker main.py
def register_worker_etcd():
    etcd = etcd_client(host=ETCD_HOST, port=ETCD_PORT)
    key = f"/workers/{WORKER_ID}"
    lease = etcd.lease(10)  # 10 second TTL
    worker_info = f"app={APP_ID},queue={STREAM_KEY},status=ONLINE"
    etcd.put(key, worker_info, lease=lease)
    return lease

def keep_alive_etcd(lease):
    lease.refresh()  # Called every 5 seconds
```

Admin dashboard queries etcd to get worker status:

```go
// In orchestrator admin_handlers.go
func adminWorkersStatusHandler(w http.ResponseWriter, r *http.Request) {
    // Get all workers from etcd
    resp, _ := etcdCli.Get(ctx, "/workers/", clientv3.WithPrefix())
    
    // Parse worker info
    workers := []WorkerStatus{}
    for _, kv := range resp.Kvs {
        // Parse key: /workers/z-image-worker-1
        // Parse value: app=z-image,queue=jobs:z-image,status=ONLINE
        workers = append(workers, parseWorkerInfo(kv))
    }
    
    // Get active worker from worker_manager
    activeWorker := getActiveWorkerFromManager()
    
    json.NewEncoder(w).Encode(map[string]interface{}{
        "workers": workers,
        "active_worker": activeWorker,
    })
}
```

### Worker UI Linking

Each worker has an associated frontend UI (e.g., z-image-ui, sdxl-ui). The admin dashboard provides direct links to these UIs:

**How it works**:

1. **Read apps.yaml** - Orchestrator reads frontend URLs from app configuration:
```yaml
# apps.yaml
apps:
  - id: z-image
    name: "Z-Image Turbo"
    frontend_url: "http://localhost:3000"  # or http://z-image-ui:3000
    # ...
  
  - id: sdxl-image-gen
    name: "SDXL Image Generator"
    frontend_url: "http://localhost:3001"  # or http://sdxl-ui:3001
    # ...
```

2. **Map workers to UIs** - Admin dashboard maps worker names to their app UIs:
```go
// In orchestrator admin_handlers.go
func adminWorkersUIInfoHandler(w http.ResponseWriter, r *http.Request) {
    // Read workers.yaml to get worker -> app_id mapping
    workersConfig := readWorkersConfig()
    
    // Read apps.yaml to get app_id -> frontend_url mapping
    appsConfig := readAppsConfig()
    
    // Build response
    uiInfo := make(map[string]interface{})
    for workerName, workerConfig := range workersConfig {
        appID := workerConfig.AppID
        app := findAppByID(appsConfig, appID)
        
        uiInfo[workerName] = map[string]string{
            "app_id": appID,
            "app_name": app.Name,
            "ui_url": app.FrontendURL,
            "status": getWorkerStatus(workerName),
        }
    }
    
    json.NewEncoder(w).Encode(uiInfo)
}
```

3. **Display in UI** - Workers page shows clickable links:
```html
<!-- Active Worker Card -->
<div class="worker-card active">
    <h3>z-image-worker</h3>
    <p>App: Z-Image Turbo</p>
    <a href="http://localhost:3000" target="_blank" class="ui-link">
        Open Z-Image UI →
    </a>
    <button onclick="cleanupGPU('z-image-worker')">Cleanup GPU</button>
    <button onclick="stopWorker()">Stop Worker</button>
</div>

<!-- Inactive Worker Card -->
<div class="worker-card inactive">
    <h3>sdxl-worker</h3>
    <p>App: SDXL Image Generator</p>
    <a href="http://localhost:3001" target="_blank" class="ui-link disabled">
        Open SDXL UI → (worker stopped)
    </a>
    <button onclick="startWorker('sdxl-worker')">Start Worker</button>
    <button onclick="switchWorker('sdxl-worker')">Switch to Worker</button>
</div>
```

**Benefits**:
- Quick access to worker UIs without remembering ports
- Links are grayed out when worker is stopped
- Opens in new tab for easy multitasking
- Automatically updated when worker status changes

---

## Security Considerations

1. **Authentication**:
   - Bcrypt password hashing (12 rounds)
   - HTTPOnly cookies (prevent XSS)
   - SameSite=Lax (CSRF protection)
   - 24-hour session expiry

2. **Default Credentials**:
   - Username: `admin`
   - Password: `admin123`
   - **MUST be changed on first login!**

3. **Audit Logging**:
   - All admin actions logged to `admin_audit_log`
   - Includes: admin_id, action, resource, timestamp, IP address

4. **API Security**:
   - All admin routes require valid session
   - SQL injection prevention (parameterized queries)
   - Input validation on all endpoints

5. **Worker Control Security**:
   - Worker switching logged in audit log
   - Cleanup endpoint only accessible from orchestrator network
   - Worker manager script runs with limited permissions

---

## Technology Stack

**Backend**:
- Go 1.24 (orchestrator APIs)
- Python 3.11 (admin dashboard, workers)

**Frontend**:
- FastAPI 0.109.0
- Jinja2 3.1.3
- Chart.js 4.x (GPU metrics visualization)
- Vanilla JavaScript (SSE, AJAX)

**Dependencies**:
- `pynvml` 11.5.0 - GPU metrics collection
- `bcrypt` 4.1.2 - Password hashing
- `psycopg2-binary` 2.9.9 - PostgreSQL client
- `etcd3` 0.12.0 - etcd client (already in workers)

**Infrastructure**:
- PostgreSQL 15 + TimescaleDB (time-series metrics)
- Redis 7.2 (job queuing)
- etcd 3.5.9 (service discovery, worker heartbeats)

---

## Implementation Order

1. **Phase 1 (Foundation)**: Database migration → Auth system → Basic UI structure
2. **Phase 2 (Jobs)**: Go admin APIs → Jobs page → Cancel/retry functionality
3. **Phase 3 (Metrics)**: Worker metrics collection → Metrics APIs → Worker control → Visualization
4. **Phase 4 (Polish)**: Overview dashboard → Config viewer → Documentation

**Dependencies**:
- Phase 2 depends on Phase 1 (auth required)
- Phase 3 can be done in parallel with Phase 2
- Phase 4 depends on Phases 2 & 3

---

## Testing Checklist

**Phase 1**:
- [ ] Can log in with admin/admin123
- [ ] Session persists across page navigation
- [ ] Unauthorized access redirects to login
- [ ] Logout clears session

**Phase 2**:
- [ ] Can view all jobs in table
- [ ] Filters work (status, app_id)
- [ ] Can cancel in-progress job
- [ ] Can retry failed job
- [ ] Real-time updates appear

**Phase 3**:
- [ ] GPU metrics appearing every 5 seconds in database
- [ ] Worker status shows correct active worker
- [ ] Can switch between workers successfully
- [ ] Worker switch takes 30-45s and shows progress
- [ ] GPU cleanup endpoint works
- [ ] Worker cards show live metrics
- [ ] Charts update in real-time
- [ ] Historical data queries work

**Phase 4**:
- [ ] Dashboard shows accurate summary stats
- [ ] Cost calculation working correctly
- [ ] Config viewer displays apps.yaml and workers.yaml
- [ ] UI responsive on mobile
- [ ] All navigation links work

---

## Future Enhancements

1. **Multi-User Support**: Add user roles (admin, operator, viewer)
2. **Alerting**: Email/Slack alerts for worker failures, job errors
3. **Advanced Job Control**: Priority queues, job dependencies, scheduling
4. **Configuration Management**: Edit apps.yaml and workers.yaml via UI
5. **Advanced Analytics**: Cost forecasting, resource optimization recommendations
6. **API Keys**: Allow programmatic admin access
7. **Export**: CSV/PDF reports for jobs and costs
8. **Worker Preloading**: Keep models loaded in memory between switches
9. **Multi-GPU Support**: Run multiple workers on different GPUs simultaneously
10. **Idle Timeout Management**: Auto-stop workers after idle period (configurable)

---

## Estimated Effort

- **Phase 1**: 2-3 days (database + auth + basic structure)
- **Phase 2**: 2-3 days (job management APIs + UI)
- **Phase 3**: 4-5 days (metrics collection + worker control + visualization)
- **Phase 4**: 2-3 days (overview + polish + docs)

**Total**: ~11-14 days (1 developer)

---

## Key Differences from Original Plan

### What Changed:
1. **Worker Registration**: Workers now register in **etcd** (not PostgreSQL workers table)
2. **Worker Management**: Added integration with `worker_manager.py` for dynamic switching
3. **GPU Cleanup**: Added HTTP cleanup endpoint integration (port 8000)
4. **Worker Status**: Status determined by etcd heartbeats + state file
5. **Exclusive Mode**: Only one worker active at a time (enforced by worker_manager)
6. **Worker Switching**: Added UI for manual worker switching with progress indicators

### What Stayed the Same:
1. Authentication system (password-based, sessions)
2. Job management (list, cancel, retry)
3. GPU metrics collection (pynvml, TimescaleDB)
4. Cost tracking (automatic calculation)
5. Admin dashboard architecture (FastAPI + Jinja2)
6. Real-time updates (Server-Sent Events)

### New Features:
1. Worker switching UI with progress tracking
2. GPU cleanup button for active worker
3. Worker configuration viewer (workers.yaml)
4. Integration with existing worker_manager.py
5. etcd-based worker discovery
6. State file monitoring for active worker
