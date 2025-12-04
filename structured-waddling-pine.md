# GPU Orchestrator Admin Dashboard - Implementation Plan

## Executive Summary

Build a comprehensive admin dashboard for complete control over the GPU Orchestrator system using FastAPI + Jinja2 (matching existing frontend pattern). The dashboard will provide:

- **Simple password-based admin authentication**
- **Complete job management** (view, filter, cancel, retry)
- **Real-time GPU metrics** (utilization, VRAM, temperature)
- **User & cost tracking**
- **Worker monitoring & control**

**Architecture**: Standalone FastAPI frontend (port 8090) + Extended Go orchestrator APIs (port 8080)

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

-- Enhance workers table
ALTER TABLE workers
    ADD COLUMN IF NOT EXISTS app_ids TEXT[],
    ADD COLUMN IF NOT EXISTS queue_names TEXT[],
    ADD COLUMN IF NOT EXISTS current_job_id UUID,
    ADD COLUMN IF NOT EXISTS jobs_completed INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS jobs_failed INTEGER DEFAULT 0;
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
        ├── workers.js             # Worker monitoring
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

**File**: `docker compose.yml`

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

## Phase 3: GPU Metrics & Worker Monitoring

### 3.1 Worker - GPU Metrics Collection

**File**: `worker/shared/gpu_metrics_collector.py` (NEW FILE)

```python
import pynvml
import threading
import time
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

**File**: `worker/shared/worker_registration.py` (NEW FILE)

```python
def register_worker(worker_id: str, app_ids: List[str], queue_names: List[str]):
    """Register worker in PostgreSQL workers table"""
    conn = get_postgres_connection()
    cursor = conn.cursor()

    # Get GPU info
    pynvml.nvmlInit()
    handle = pynvml.nvmlDeviceGetHandleByIndex(0)
    gpu_name = pynvml.nvmlDeviceGetName(handle)
    memory_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
    gpu_memory_total_mb = memory_info.total // (1024 * 1024)

    cursor.execute("""
        INSERT INTO workers (worker_id, hostname, gpu_name, gpu_memory_total_mb,
                            app_ids, queue_names, status, last_heartbeat, registered_at)
        VALUES (%s, %s, %s, %s, %s, %s, 'ONLINE', NOW(), NOW())
        ON CONFLICT (worker_id) DO UPDATE
        SET hostname = EXCLUDED.hostname,
            gpu_name = EXCLUDED.gpu_name,
            app_ids = EXCLUDED.app_ids,
            queue_names = EXCLUDED.queue_names,
            status = 'ONLINE',
            last_heartbeat = NOW()
    """, (worker_id, os.uname().nodename, gpu_name, gpu_memory_total_mb,
          app_ids, queue_names))
    conn.commit()
```

**File**: `worker/z-image_worker/main.py`

Modify main loop:
```python
from shared.gpu_metrics_collector import GPUMetricsCollector
from shared.worker_registration import register_worker, update_worker_heartbeat

def main():
    # Register worker in PostgreSQL
    register_worker(
        worker_id=WORKER_ID,
        app_ids=['z-image'],
        queue_names=[STREAM_KEY]
    )

    # Start GPU metrics collector
    metrics_collector = GPUMetricsCollector(worker_id=WORKER_ID, interval_seconds=5)
    metrics_collector.start()

    last_heartbeat = time.time()

    while True:
        # Heartbeat update (every 10 seconds)
        if time.time() - last_heartbeat > 10:
            update_worker_heartbeat(WORKER_ID)
            last_heartbeat = time.time()

        # Process jobs (existing logic)
        # ...
```

**File**: `worker/z-image_worker/requirements.txt`

Add: `pynvml==11.5.0`

**File**: `worker/shared/requirements.txt`

Add: `pynvml==11.5.0`

### 3.2 Go Orchestrator - Worker & Metrics APIs

**File**: `orchestrator/admin_handlers.go`

Add handlers:
```go
// GET /admin/workers/stats - Get worker stats with latest GPU metrics
func adminWorkersStatsHandler(w http.ResponseWriter, r *http.Request) {
    // Join workers table with latest gpu_metrics
    // Return worker list with GPU info
}

// GET /admin/metrics/gpu - Query time-series GPU metrics
func adminGPUMetricsHandler(w http.ResponseWriter, r *http.Request) {
    // Query params: worker_id, start_time, end_time, interval
    // Use TimescaleDB time_bucket for aggregation
}
```

SQL queries:
```sql
-- Worker stats with latest GPU metrics
SELECT
    w.worker_id, w.hostname, w.gpu_name, w.status, w.last_heartbeat,
    w.jobs_completed, w.jobs_failed, w.current_job_id,
    gm.gpu_utilization, gm.vram_used_mb, gm.vram_total_mb,
    gm.temperature_c, gm.power_draw_w
FROM workers w
LEFT JOIN LATERAL (
    SELECT * FROM gpu_metrics
    WHERE worker_id = w.worker_id
    ORDER BY time DESC LIMIT 1
) gm ON true
ORDER BY w.worker_id;

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
```

### 3.3 Admin Dashboard - Workers & Metrics Pages

**File**: `frontends/admin-dashboard/templates/workers.html`

Features:
- Worker cards showing: Worker ID, Status badge, GPU name, Hostname
- Live GPU metrics: Utilization %, VRAM usage (X/Y MB), Temperature
- Job stats: Jobs completed, Jobs failed
- Current job indicator
- Auto-refresh every 5 seconds via SSE

**File**: `frontends/admin-dashboard/templates/metrics.html`

Features:
- Time range selector (1h, 6h, 24h, 7d)
- Worker selector dropdown
- Charts (using Chart.js):
  1. GPU Utilization % (line chart)
  2. VRAM Usage (area chart)
  3. Temperature (line chart)
  4. Power Draw (line chart)
- Multi-GPU support (multiple lines per chart)

**File**: `frontends/admin-dashboard/static/js/metrics.js`

```javascript
// Initialize Chart.js charts
let utilizationChart, vramChart, temperatureChart;

function initCharts() {
    utilizationChart = new Chart(ctx, {
        type: 'line',
        data: {...},
        options: {
            responsive: true,
            scales: {y: {min: 0, max: 100}}
        }
    });
}

// Fetch and update metrics
async function updateMetrics(workerId, timeRange) {
    const response = await fetch(`/api/metrics/gpu?worker_id=${workerId}&range=${timeRange}`);
    const data = await response.json();
    updateCharts(data);
}
```

**Phase 3 Deliverables**:
- ✅ GPU metrics collection in workers (every 5 seconds)
- ✅ Workers table populated with GPU info
- ✅ Worker monitoring dashboard with live stats
- ✅ GPU metrics visualization with time-series charts
- ✅ Real-time updates via SSE

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
    // - Active workers count
    // - Total cost (all time)
    // - Jobs by status breakdown
    // - Jobs by app breakdown
    // - Average job duration by app
}
```

### 4.2 Admin Dashboard - Overview Page

**File**: `frontends/admin-dashboard/templates/dashboard.html`

Layout:
1. **Summary Cards** (top row):
   - Total Jobs
   - Active Workers
   - Jobs (24h)
   - Total Cost

2. **Charts** (middle):
   - Job Status Pie Chart (COMPLETED, FAILED, PROCESSING, QUEUED)
   - Jobs per Hour (last 24h) Bar Chart

3. **Recent Jobs Table** (bottom):
   - Last 10 jobs with status, app, duration, cost

### 4.3 Configuration Viewer

**File**: `frontends/admin-dashboard/templates/config.html`

Features:
- Read-only view of `apps.yaml`
- Table showing: App ID, Name, Type, Queue, GPU VRAM, Docker Image
- Parameter schemas per app

**File**: `orchestrator/admin_handlers.go`

```go
// GET /admin/config/apps - View apps configuration
func adminAppsConfigHandler(w http.ResponseWriter, r *http.Request) {
    // Return apps.yaml as JSON
}
```

### 4.4 UI Polish & Documentation

**CSS Enhancements** (`frontends/admin-dashboard/static/css/admin.css`):
- Consistent color scheme (follow z-image-ui pattern)
- Responsive design (mobile-friendly)
- Loading states and spinners
- Toast notifications for actions
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
5. `worker/shared/worker_registration.py` - Worker PostgreSQL registration

### Existing Files to Modify:
1. `orchestrator/main.go:296` - Add admin API routes
2. `worker/z-image_worker/main.py:87` - Integrate metrics collector & registration
3. `worker/z-image_worker/requirements.txt` - Add pynvml
4. `docker compose.yml:150` - Add admin-dashboard service
5. `ADDING_NEW_APPS.md` - Document admin dashboard usage

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

**Infrastructure**:
- PostgreSQL 15 + TimescaleDB (time-series metrics)
- Redis 7.2 (job queuing)
- etcd 3.5.9 (service discovery)

---

## Implementation Order

1. **Phase 1 (Foundation)**: Database migration → Auth system → Basic UI structure
2. **Phase 2 (Jobs)**: Go admin APIs → Jobs page → Cancel/retry functionality
3. **Phase 3 (Metrics)**: Worker metrics collection → Metrics APIs → Visualization
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
- [ ] Workers table populated with correct GPU info
- [ ] Worker cards show live metrics
- [ ] Charts update in real-time
- [ ] Historical data queries work

**Phase 4**:
- [ ] Dashboard shows accurate summary stats
- [ ] Cost calculation working correctly
- [ ] Config viewer displays apps.yaml
- [ ] UI responsive on mobile
- [ ] All navigation links work

---

## Future Enhancements

1. **Multi-User Support**: Add user roles (admin, operator, viewer)
2. **Alerting**: Email/Slack alerts for worker failures, job errors
3. **Advanced Job Control**: Priority queues, job dependencies, scheduling
4. **Configuration Management**: Edit apps.yaml via UI
5. **Advanced Analytics**: Cost forecasting, resource optimization recommendations
6. **API Keys**: Allow programmatic admin access
7. **Export**: CSV/PDF reports for jobs and costs

---

## Estimated Effort

- **Phase 1**: 2-3 days (database + auth + basic structure)
- **Phase 2**: 2-3 days (job management APIs + UI)
- **Phase 3**: 3-4 days (metrics collection + visualization)
- **Phase 4**: 2-3 days (overview + polish + docs)

**Total**: ~10-13 days (1 developer)
