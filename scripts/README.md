# GPU Orchestrator Scripts

This directory contains helper scripts for managing workers and submitting jobs.

## Worker Management

### `worker_manager.py`

Python script for managing GPU workers with exclusive access mode.

**Commands:**

```bash
# Check current status
./scripts/worker_manager.py status

# Switch to specific worker
./scripts/worker_manager.py switch z-image-worker
./scripts/worker_manager.py switch sdxl-worker

# Switch by app ID (recommended)
./scripts/worker_manager.py app z-image
./scripts/worker_manager.py app sdxl-image-gen

# Stop all workers
./scripts/worker_manager.py stop
```

**Example Output:**

```
=== GPU Worker Status ===
Mode: Exclusive (one worker at a time)
Active Worker: z-image-worker

Configured Workers:
  🟢 z-image-worker
     App: z-image
     VRAM: 16GB
     Status: Running ← ACTIVE
  ⚫ sdxl-worker
     App: sdxl-image-gen
     VRAM: 12GB
     Status: Stopped
```

## Job Submission

### `submit_job.sh`

Submit jobs to the orchestrator with automatic worker switching.

**Usage:**

```bash
./scripts/submit_job.sh <app_id> '<json_params>'
```

**Examples:**

```bash
# Z-Image generation
./scripts/submit_job.sh z-image '{"prompt": "A cat in space", "steps": "20"}'

# SDXL generation
./scripts/submit_job.sh sdxl-image-gen '{"prompt": "A beautiful sunset", "width": "1024", "height": "1024", "num_inference_steps": "50"}'
```

### `monitor_job.sh`

Monitor a job in real-time until completion.

**Usage:**

```bash
./scripts/monitor_job.sh <job_id>
```

**Example:**

```bash
./scripts/monitor_job.sh abc123-def456-ghi789
```

**Output:**

```
=========================================
Monitoring Job: abc123-def456-ghi789
=========================================

[2025-12-04 01:00:00] Status: PENDING
[2025-12-04 01:00:02] Status: QUEUED
[2025-12-04 01:00:35] Status: PROCESSING
[2025-12-04 01:01:20] Status: COMPLETED

=========================================
Job Completed Successfully!
=========================================

Full response:
{
  "job_id": "abc123-def456-ghi789",
  "status": "COMPLETED",
  "result": {...}
}
```

### `test_dynamic_switching.sh`

Automated test for dynamic worker switching functionality.

**Usage:**

```bash
./scripts/test_dynamic_switching.sh
```

This script will:
1. Submit a Z-Image job
2. Submit an SDXL job (triggers worker switch from z-image to sdxl)
3. Submit another Z-Image job (triggers worker switch back)

Perfect for verifying the worker switching system works correctly.

## Configuration

Worker settings are configured in `config/workers.yaml`:

```yaml
workers:
  z-image-worker:
    app_id: "z-image"
    queue: "jobs:z-image"
    vram_required_gb: 16
    startup_time_seconds: 30
    shutdown_time_seconds: 10

  sdxl-worker:
    app_id: "sdxl-image-gen"
    queue: "jobs:sdxl"
    vram_required_gb: 12
    startup_time_seconds: 45
    shutdown_time_seconds: 10

settings:
  exclusive_mode: true
  idle_timeout: 300
  max_startup_wait: 120
  max_shutdown_wait: 30
```

## Troubleshooting

### Worker won't start

```bash
# Check Docker status
docker compose ps

# View worker logs
docker compose logs -f z-image-worker
docker compose logs -f sdxl-worker

# Manually restart worker
docker compose restart z-image-worker
```

### Worker switch timeout

If worker switching is taking too long, adjust timing in `config/workers.yaml`:

```yaml
workers:
  z-image-worker:
    startup_time_seconds: 45  # Increase if model loading is slow
    shutdown_time_seconds: 15  # Increase if shutdown is slow
```

### State file out of sync

```bash
# Reset worker manager state
rm /tmp/gpu_orchestrator_active_worker.txt

# Stop all workers
./scripts/worker_manager.py stop

# Start fresh
./scripts/worker_manager.py switch z-image-worker
```

### GPU memory not released

```bash
# Check GPU memory
nvidia-smi

# Force remove all workers
docker compose stop z-image-worker sdxl-worker
docker compose rm -f z-image-worker sdxl-worker

# Restart
docker compose up -d z-image-worker
```

## Integration with Orchestrator

The orchestrator automatically calls the worker manager before submitting local GPU jobs:

```go
// In submitJobHandler
if appConfig.Type == "local" {
    if err := switchWorker(req.AppID); err != nil {
        // Handle error
    }
}
```

This ensures the correct worker is always running before a job is queued.
