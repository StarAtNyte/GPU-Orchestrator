# GPU-Polling

A distributed GPU orchestration system for managing job submission and execution across GPU workers. The system uses a separated architecture with a backend running on an Ubuntu GPU PC and a frontend for user interfaces.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│ BACKEND (Ubuntu GPU PC)                                             │
│                                                                     │
│  ┌──────────┐  ┌───────┐  ┌──────┐                                 │
│  │PostgreSQL│  │ Redis │  │ etcd │   Infrastructure                │
│  └────┬─────┘  └───┬───┘  └──┬───┘                                 │
│       └────────────┼────────┘                                       │
│                    ▼                                                │
│            ┌──────────────┐                                         │
│            │ Orchestrator │  Job routing & GPU management           │
│            └──────┬───────┘                                         │
│                   │                                                 │
│     ┌─────────────┼─────────────┐                                   │
│     ▼             ▼             ▼                                   │
│ ┌────────┐  ┌──────────┐  ┌────────────┐                           │
│ │  SDXL  │  │ Z-Image  │  │   Qwen     │   GPU Workers              │
│ │ Worker │  │  Worker  │  │   Worker   │                           │
│ └────────┘  └──────────┘  └────────────┘                           │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              │ HTTP (port 9090)
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│ FRONTEND (Linux/Windows Server)                                     │
│                                                                     │
│  ┌────────────┐  ┌─────────┐  ┌──────────┐  ┌────────┐  ┌───────┐  │
│  │    Main    │  │  SDXL   │  │ Z-Image  │  │  Qwen  │  │ Admin │  │
│  │ Dashboard  │  │   UI    │  │    UI    │  │   UI   │  │  UI   │  │
│  │   :8080    │  │  :7861  │  │  :7862   │  │ :7863  │  │ :8000 │  │
│  └────────────┘  └─────────┘  └──────────┘  └────────┘  └───────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

## Quick Start

### Prerequisites

- Docker and Docker Compose
- NVIDIA GPU with drivers installed
- NVIDIA Container Toolkit

### 1. Start the Backend (Ubuntu GPU PC)

```bash
cd backend

# Copy and configure environment
cp ../.env.example .env
# Edit .env with your settings (POSTGRES_PASSWORD, HF_TOKEN, etc.)

# Start all services
docker-compose up -d

# Verify services are running
docker-compose ps
```

### 2. Start the Frontend (Linux/Windows Server)

```bash
cd frontend

# Edit docker-compose.yml to set ORCHESTRATOR_URL to your backend IP
# Example: http://192.168.50.28:9090

# Start all UI services
docker-compose up -d

# Verify services are running
docker-compose ps
```

### 3. Access the Application

| Service | URL | Description |
|---------|-----|-------------|
| Main Dashboard | http://localhost:8080 | Service hub & discovery |
| SDXL UI | http://localhost:7861 | SDXL image generation |
| Z-Image UI | http://localhost:7862 | Z-Image Turbo generation |
| Qwen UI | http://localhost:7863 | Qwen vision-language model |
| Admin Dashboard | http://localhost:8000 | System monitoring |

## Docker Commands

### Start Services

```bash
# Start all backend services
cd backend && docker-compose up -d

# Start all frontend services
cd frontend && docker-compose up -d

# Start specific service
docker-compose up -d <service-name>
```

### Stop Services

```bash
# Stop all services (keeps containers)
docker-compose stop

# Stop and remove containers
docker-compose down

# Stop and remove containers + volumes (WARNING: deletes data)
docker-compose down -v
```

### Restart Services

```bash
# Restart all services
docker-compose restart

# Restart specific service
docker-compose restart <service-name>

# Full restart (rebuild if needed)
docker-compose down && docker-compose up -d

# Restart with rebuild (after code changes)
docker-compose up -d --build
```

### View Logs

```bash
# View all logs
docker-compose logs

# Follow logs in real-time
docker-compose logs -f

# View logs for specific service
docker-compose logs -f <service-name>

# View last 100 lines
docker-compose logs --tail=100
```

### Check Status

```bash
# Check running containers
docker-compose ps

# Check GPU status (backend)
curl http://localhost:9090/health/gpu

# Check registered workers
curl http://localhost:9090/workers
```

## Project Structure

```
GPU-Polling/
├── backend/                    # Ubuntu GPU PC services
│   ├── docker-compose.yml      # Backend orchestration
│   ├── orchestrator/           # Go-based job router
│   ├── worker/                 # GPU worker implementations
│   │   ├── sdxl_worker/        # SDXL image generation
│   │   ├── z-image_worker/     # Z-Image Turbo
│   │   └── qwen-image-2512_worker/  # Qwen vision-language
│   ├── config/                 # Configuration files
│   └── init.sql                # Database schema
│
├── frontend/                   # UI services
│   ├── docker-compose.yml      # Frontend orchestration
│   ├── main-dashboard/         # Service hub (:8080)
│   ├── sdxl-ui/                # SDXL UI (:7861)
│   ├── z-image-ui/             # Z-Image UI (:7862)
│   ├── qwen-ui/                # Qwen UI (:7863)
│   └── admin-dashboard/        # Admin panel (:8000)
│
├── .env.example                # Environment template
├── development.md              # Development guide
└── ADDING_NEW_APPS.md          # Guide for new models
```

## Configuration

### Environment Variables

Copy `.env.example` to `.env` and configure:

```bash
# Database
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your_secure_password
POSTGRES_DB=gpu_orchestrator

# Hugging Face (for model downloads)
HF_TOKEN=your_huggingface_token
```

### Changing Backend URL

Edit `frontend/docker-compose.yml` and update `ORCHESTRATOR_URL`:

```yaml
environment:
  - ORCHESTRATOR_URL=http://<BACKEND_IP>:9090
```

## Troubleshooting

### Services not starting

```bash
# Check container logs
docker-compose logs <service-name>

# Check if ports are in use
sudo lsof -i :9090
sudo lsof -i :8080
```

### GPU not detected

```bash
# Verify NVIDIA drivers
nvidia-smi

# Check NVIDIA Container Toolkit
docker run --rm --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 nvidia-smi
```

### Workers not registering

```bash
# Check orchestrator logs
cd backend && docker-compose logs orchestrator

# Check worker logs
docker-compose logs sdxl-worker

# Verify network connectivity
docker network ls
docker network inspect backend_backend-network
```

### Database issues

```bash
# Reset database (WARNING: deletes all data)
cd backend
docker-compose down -v
docker-compose up -d postgres
docker-compose up -d
```

## Adding New Workers

See [ADDING_NEW_APPS.md](ADDING_NEW_APPS.md) for instructions on adding new GPU workers.

## Development

See [development.md](development.md) for the complete development setup guide.
