# GPU Orchestrator Admin Dashboard

## Overview

The Admin Dashboard provides comprehensive control over the GPU Orchestrator system, including:

- **Authentication**: Password-based admin authentication with session management
- **Job Management**: View, filter, cancel, and retry jobs
- **Worker Monitoring**: Monitor GPU workers, view metrics, and control worker switching
- **GPU Metrics**: Real-time GPU utilization, VRAM, temperature, and power draw tracking
- **Configuration**: View apps and workers configuration

## Quick Start

### Using Docker Compose

The admin dashboard is included in the main docker-compose.yml:

```bash
# Start all services including admin dashboard
docker compose up -d

# Access the dashboard
open http://localhost:8090
```

### Default Credentials

```
Username: admin
Password: admin123
```

**IMPORTANT**: Change the default password immediately after first login!

## Architecture

### Technology Stack

- **Backend**: FastAPI 0.109.0 + Python 3.11
- **Frontend**: Jinja2 templates + TailwindCSS + Vanilla JavaScript
- **Database**: PostgreSQL 15 + TimescaleDB (shared with orchestrator)
- **Authentication**: bcrypt password hashing + session cookies
- **Charts**: Chart.js 4.x for GPU metrics visualization

### Directory Structure

```
frontends/admin-dashboard/
├── app.py                      # Main FastAPI application
├── requirements.txt            # Python dependencies
├── Dockerfile                  # Docker build file
├── auth/
│   ├── __init__.py
│   └── session.py             # Session management utilities
├── templates/
│   ├── base.html              # Base template with navigation
│   ├── login.html             # Login page
│   ├── dashboard.html         # Overview dashboard
│   ├── jobs.html              # Job management
│   ├── workers.html           # Worker monitoring
│   ├── metrics.html           # GPU metrics
│   └── config.html            # Configuration viewer
└── static/
    ├── css/
    │   └── admin.css          # Custom styles (if needed)
    └── js/
        ├── jobs.js            # Job management logic (Phase 2)
        ├── workers.js         # Worker control (Phase 3)
        └── metrics.js         # Chart rendering (Phase 3)
```

## Features

### Phase 1: Authentication & Foundation ✅

- [x] Database schema with admin users and sessions
- [x] Password-based authentication (bcrypt)
- [x] Session management (24-hour expiry)
- [x] Protected routes with session validation
- [x] Base UI with sidebar navigation
- [x] Login page with dark theme

### Phase 2: Job Management (Pending)

- [ ] Job listing with filters (status, app_id)
- [ ] Job cancel functionality
- [ ] Job retry functionality
- [ ] Real-time job status updates via SSE
- [ ] Audit logging for admin actions

### Phase 3: GPU Metrics & Worker Control (Pending)

- [ ] GPU metrics collection (every 5 seconds)
- [ ] Worker status monitoring via etcd
- [ ] Worker start/stop/switch controls
- [ ] GPU cleanup endpoint integration
- [ ] Time-series metrics visualization
- [ ] Real-time worker status updates

### Phase 4: Dashboard Overview & Polish (Pending)

- [ ] Summary statistics (total jobs, costs, etc.)
- [ ] Recent jobs table
- [ ] Configuration viewer (apps.yaml, workers.yaml)
- [ ] UI polish and responsive design
- [ ] Documentation

## Database Schema

The admin dashboard adds the following tables:

### admin_users

Stores admin user accounts:

```sql
- id (UUID, PRIMARY KEY)
- username (VARCHAR, UNIQUE)
- password_hash (VARCHAR)
- email (VARCHAR)
- created_at (TIMESTAMPTZ)
- last_login (TIMESTAMPTZ)
- is_active (BOOLEAN)
```

### admin_sessions

Tracks active sessions:

```sql
- session_id (UUID, PRIMARY KEY)
- admin_id (UUID, FOREIGN KEY)
- created_at (TIMESTAMPTZ)
- expires_at (TIMESTAMPTZ)
- ip_address (INET)
- user_agent (TEXT)
- last_activity (TIMESTAMPTZ)
```

### admin_audit_log

Audit trail of admin actions:

```sql
- id (BIGSERIAL, PRIMARY KEY)
- admin_id (UUID, FOREIGN KEY)
- action (VARCHAR) - e.g., 'CANCEL_JOB', 'SWITCH_WORKER'
- resource_type (VARCHAR) - e.g., 'job', 'worker'
- resource_id (VARCHAR)
- details (JSONB)
- ip_address (INET)
- created_at (TIMESTAMPTZ)
```

### cost_rules

Cost calculation rules per app:

```sql
- id (SERIAL, PRIMARY KEY)
- app_id (VARCHAR)
- cost_per_second (DECIMAL)
- cost_per_job (DECIMAL)
- effective_from (TIMESTAMPTZ)
- effective_until (TIMESTAMPTZ)
- description (TEXT)
```

## API Endpoints

### Authentication

- `GET /login` - Login page
- `POST /login` - Login form submission
- `GET /logout` - Logout and destroy session

### Dashboard Pages

- `GET /dashboard` - Overview dashboard
- `GET /jobs` - Job management page
- `GET /workers` - Worker monitoring page
- `GET /metrics` - GPU metrics page
- `GET /config` - Configuration viewer

### API Endpoints (to be implemented in Phase 2 & 3)

- `GET /api/jobs` - List jobs with filtering
- `POST /api/jobs/{id}/cancel` - Cancel a job
- `POST /api/jobs/{id}/retry` - Retry a failed job
- `GET /api/workers/status` - Get worker status
- `POST /api/workers/start` - Start a worker
- `POST /api/workers/stop` - Stop active worker
- `POST /api/workers/switch` - Switch to different worker
- `POST /api/workers/{id}/cleanup` - Cleanup GPU memory
- `GET /api/metrics/gpu` - Get GPU metrics time-series
- `GET /stream/jobs` - SSE stream for real-time job updates
- `GET /stream/workers` - SSE stream for real-time worker updates

## Security

### Authentication

- **Password Hashing**: bcrypt with 12 rounds
- **Session Cookies**: HTTPOnly, SameSite=Lax
- **Session Expiry**: 24 hours
- **Session Validation**: On every protected route

### Audit Logging

All admin actions are logged to `admin_audit_log` including:
- Admin user ID
- Action performed
- Resource affected
- Timestamp
- IP address

### Network Security

The admin dashboard runs on port 8090 and should be:
- Protected by firewall rules
- Accessed via HTTPS in production
- Rate-limited to prevent brute force attacks

## Development

### Local Development

```bash
cd frontends/admin-dashboard

# Install dependencies
pip install -r requirements.txt

# Set environment variables
export POSTGRES_HOST=localhost
export POSTGRES_USER=admin
export POSTGRES_PASSWORD=admin123
export POSTGRES_DB=gpu_orchestrator
export ORCHESTRATOR_URL=http://localhost:8080

# Run the app
uvicorn app:app --host 0.0.0.0 --port 8090 --reload
```

### Running Migrations

Migrations are automatically run by the orchestrator on startup. The admin schema migration is:

```
orchestrator/migrations/000003_add_admin_schema.up.sql
```

To manually run migrations:

```bash
cd orchestrator
migrate -path migrations -database "postgres://user:pass@localhost:5432/gpu_orchestrator?sslmode=disable" up
```

## Production Deployment

### Environment Variables

```bash
ORCHESTRATOR_URL=http://orchestrator:8080  # Orchestrator API URL
POSTGRES_HOST=postgres                     # PostgreSQL host
POSTGRES_USER=admin                        # PostgreSQL user
POSTGRES_PASSWORD=<secure-password>        # PostgreSQL password
POSTGRES_DB=gpu_orchestrator               # PostgreSQL database
```

### Security Checklist

- [ ] Change default admin password
- [ ] Use HTTPS with valid SSL certificate
- [ ] Configure firewall to restrict access
- [ ] Set up rate limiting (e.g., nginx)
- [ ] Enable audit log monitoring
- [ ] Regular security updates
- [ ] Backup admin_users table

## Troubleshooting

### Cannot login

1. Check if database migration ran successfully:
   ```bash
   docker-compose logs orchestrator | grep migration
   ```

2. Verify admin user exists:
   ```sql
   SELECT * FROM admin_users WHERE username = 'admin';
   ```

3. Check session table:
   ```sql
   SELECT * FROM admin_sessions ORDER BY created_at DESC LIMIT 5;
   ```

### Worker status not showing

1. Check if etcd is running:
   ```bash
   docker-compose ps etcd
   ```

2. Verify workers are registered in etcd:
   ```bash
   docker exec etcd etcdctl get --prefix "/workers/"
   ```

### Metrics not loading

1. Check if TimescaleDB extension is enabled:
   ```sql
   SELECT * FROM pg_extension WHERE extname = 'timescaledb';
   ```

2. Verify gpu_metrics table exists:
   ```sql
   SELECT * FROM gpu_metrics LIMIT 1;
   ```

## Support

For issues and questions:
- Check the main project README
- Review the implementation plan: `structured-waddling-pine-v2.md`
- Check orchestrator logs: `docker-compose logs orchestrator`
- Check admin dashboard logs: `docker-compose logs admin-dashboard`

## License

Same as main GPU Orchestrator project.
