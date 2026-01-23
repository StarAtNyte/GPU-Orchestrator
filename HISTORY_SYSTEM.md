# User History & Persistent Queue System

## Overview
This implementation enables users to:
- Submit jobs from any service UI
- Close the webapp - jobs continue processing in the background
- View all past submissions and results in a unified History page
- Track jobs across sessions using a username-based system

## Architecture Changes

### 1. Database Schema
**New Migration:** `backend/orchestrator/migrations/02_add_username.up.sql`
- Added `username` VARCHAR(100) field to `jobs` table
- Created index on `username` for fast lookups
- Stores user identifier for job tracking

### 2. Backend API (Orchestrator)

#### Updated Endpoints:
**POST /submit**
- Now requires `username` field in request body
- Stores username with each job submission
- Added CORS headers for cross-origin requests

**GET /status/{job_id}**
- Added CORS headers
- Returns job status, params, and output

#### New Endpoints:
**GET /user/jobs**
- Query params: `username` (required), `app_id` (optional), `status` (optional)
- Returns all jobs for a specific user (up to 100 most recent)
- Includes job details, params, output, and timestamps
- Filters by service or status

**GET /user/jobs/{job_id}**
- Query params: `username` (required)
- Returns detailed job information
- Verifies job belongs to requesting user
- Full params and output data

### 3. Frontend Changes

#### Main Dashboard (port 8888)
**New Page:** `/history`
- Beautiful unified history interface
- Username management (localStorage-based)
- Filter by service (SDXL, Z-Image, Qwen)
- Filter by status (Completed, Processing, Queued, Failed)
- Auto-refresh for pending jobs (every 5 seconds)
- Image previews for completed jobs
- Click to view full job details

**New API Proxies:**
- `GET /api/user/jobs` - Proxies to orchestrator
- `GET /api/user/jobs/{job_id}` - Proxies to orchestrator

**UI Enhancements:**
- History link added to main dashboard nav

#### Service UIs (Z-Image Example)
**Updated:** `frontend/z-image-ui/`
- Added username to submit requests
- Username prompt on first use (localStorage)
- "View History" link in navigation
- Updated loading text: "QUEUED - You can close this page, check /history for results"

## User Flow

### First Time User
1. User visits any service UI (e.g., Z-Image)
2. Enters a prompt and clicks "Generate"
3. Prompted to enter username (stored in localStorage)
4. Job submitted to orchestrator with username
5. User can close the page - job continues processing

### Returning User
1. Username automatically loaded from localStorage
2. Submit jobs without re-entering username
3. Click "View History" to see all past submissions
4. Filter by service or status
5. View completed results with image previews

### History Page Features
- **Session Management:** Username stored in browser localStorage
- **Job Cards:** Visual cards showing:
  - Service icon and name
  - Status badge (color-coded)
  - Prompt preview
  - Image preview (for completed jobs)
  - Timestamp (relative: "2h ago", "1d ago")
- **Filters:**
  - Service: All, SDXL, Z-Image, Qwen
  - Status: All, Completed, Processing, Queued, Failed
- **Auto-refresh:** Polls every 5 seconds if any jobs are processing/queued
- **Responsive:** Works on desktop and mobile

## Data Storage

### Jobs Table Schema
```sql
jobs (
  id UUID PRIMARY KEY,
  username VARCHAR(100),  -- NEW
  app_id VARCHAR(50),
  status VARCHAR(20),
  params JSONB,          -- Input parameters
  output JSONB,          -- Results (images as base64)
  error_log TEXT,
  created_at TIMESTAMPTZ,
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ
)
```

### Output Format (JSONB)
For image generation services:
```json
{
  "image": "data:image/png;base64,iVBORw0KGgoAAAANS...",
  "seed": 42,
  "steps": 9
}
```
or
```json
{
  "images": ["data:image/png;base64,..."],
  "metadata": {...}
}
```

## Testing Instructions

### 1. Run Database Migration
```bash
cd backend/orchestrator
# Migration will run automatically on next orchestrator start
# Or manually apply:
psql -h localhost -U postgres -d gpu_orchestrator -f migrations/02_add_username.up.sql
```

### 2. Restart Services
```bash
# Backend
cd backend
docker-compose down
docker-compose up -d

# Frontend
cd frontend
docker-compose down
docker-compose up -d
```

### 3. Test User Flow
1. Visit http://localhost:7862 (Z-Image UI)
2. Enter a prompt
3. Click "Generate Image"
4. Enter username when prompted (e.g., "alice")
5. Job should submit and start processing
6. **Close the browser tab** (job continues in background!)
7. Wait 30 seconds
8. Visit http://localhost:8888/history
9. Enter the same username ("alice")
10. You should see your submitted job
11. If completed, click to view the result with image

### 4. Test Multiple Services
1. Submit a job from Z-Image
2. Submit a job from SDXL (if available)
3. Visit /history
4. Use service filter to view jobs from specific services

### 5. Test Status Filters
1. Submit multiple jobs (some will queue)
2. Visit /history
3. Use status filter:
   - "Processing" - jobs currently running
   - "Queued" - jobs waiting for GPU
   - "Completed" - finished jobs with results
   - "Failed" - jobs that errored

## API Examples

### Submit Job with Username
```bash
curl -X POST http://localhost:8080/submit \
  -H "Content-Type: application/json" \
  -d '{
    "app_id": "z-image",
    "username": "alice",
    "params": {
      "prompt": "A beautiful sunset",
      "resolution": "1024x1024"
    }
  }'
```

### Get User History
```bash
curl "http://localhost:8080/user/jobs?username=alice"
```

### Get User History (Filtered)
```bash
curl "http://localhost:8080/user/jobs?username=alice&app_id=z-image&status=COMPLETED"
```

### Get Job Details
```bash
curl "http://localhost:8080/user/jobs/{job_id}?username=alice"
```

## Future Enhancements

### Potential Additions:
1. **User Authentication:** Replace username with proper login/signup
2. **Result Downloads:** Direct download button for images
3. **Job Sharing:** Share results via public links
4. **Notifications:** Email/push when jobs complete
5. **Favorites:** Star favorite results
6. **Search:** Full-text search in prompts
7. **Pagination:** Load more than 100 jobs
8. **Export:** Download all results as ZIP
9. **Cost Tracking:** Show compute costs per job
10. **Job Retry:** Retry failed jobs with one click

## Architecture Benefits

### Scalability
- Jobs persist in PostgreSQL (crash-safe)
- Redis Streams handle queuing (reliable)
- Username-based partitioning (easy to shard)

### User Experience
- Fire-and-forget job submission
- No need to keep browser open
- All history in one place
- Filter and search capabilities

### Fault Tolerance
- Jobs survive orchestrator restarts
- Worker crashes don't lose jobs
- Results stored permanently (configurable retention)

## Configuration

### Environment Variables
```bash
# Orchestrator
POSTGRES_HOST=postgres
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
POSTGRES_DB=gpu_orchestrator
REDIS_URL=redis:6379
ETCD_ENDPOINT=etcd:2379

# Frontend
ORCHESTRATOR_URL=http://192.168.50.28:8080
```

### Retention Policy
By default, job history is kept indefinitely. To add cleanup:

```sql
-- Delete jobs older than 30 days
DELETE FROM jobs
WHERE created_at < NOW() - INTERVAL '30 days';
```

Add this as a cron job or scheduled task.

## Support

For issues or questions:
1. Check orchestrator logs: `docker logs orchestrator`
2. Check database: `psql -h localhost -U postgres -d gpu_orchestrator`
3. Verify migrations: `SELECT * FROM jobs LIMIT 1;` (should have username column)

---

**Implementation Date:** 2025-01-23
**Status:** ✅ Complete and Ready for Testing
