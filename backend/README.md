# GPU Orchestrator Backend - Ubuntu PC

Production backend for GPU orchestration system.

## Setup

### 1. Copy GPU orchestrator code to backend/
```bash
# Copy from root
cp -r orchestrator/ backend/
cp -r worker/ backend/
cp -r config/ backend/
cp -r proto/ backend/
cp init.sql backend/
cp .env.example backend/.env
```

### 2. Configure .env
```bash
cd backend
cp .env.example .env
nano .env
# Set database credentials
```

### 3. Start services
```bash
docker-compose up -d postgres redis etcd orchestrator

# Wait for postgres to be healthy
docker-compose ps

# Then start workers
docker-compose up -d sdxl-worker z-image-worker whisper-worker
```

### 4. Verify
```bash
# Check orchestrator health
curl http://localhost:8080/health/gpu

# Check workers registered
curl http://localhost:8080/workers

# Check logs
docker-compose logs -f orchestrator
```

## Structure

```
backend/
├── docker-compose.yml      (this file - infrastructure + workers)
├── .env                    (environment config)
├── orchestrator/           (Go backend)
├── worker/                 (Python workers)
├── config/                 (app registry)
├── proto/                  (protobuf definitions)
└── init.sql               (database schema)
```

## Exposing to Linux Frontend

The orchestrator listens on `:8080`. To access from Linux frontend:

### Option 1: Port Forward (NAT)
```bash
# On Ubuntu router:
# Forward external port 8080 → 192.168.50.28:8080
# Then use: ORCHESTRATOR_URL=http://113.199.192.32:8080
```

### Option 2: Direct LAN Access
```bash
# If Linux frontend is on same LAN:
# Use: ORCHESTRATOR_URL=http://192.168.50.28:8080
```

### Option 3: VPN/SSH Tunnel
```bash
# If behind firewall:
ssh -L 8080:localhost:8080 user@192.168.50.28
# Then use: ORCHESTRATOR_URL=http://localhost:8080
```

## API Endpoints

- `GET /health/gpu` - GPU status (nvidia-smi)
- `POST /submit` - Submit job
- `GET /status/{job_id}` - Job status
- `GET /workers` - List registered workers
- `GET /admin/*` - Admin endpoints

## Monitoring

```bash
# View all logs
docker-compose logs -f

# View specific service
docker-compose logs -f orchestrator

# Monitor GPU status
watch -n 1 'curl -s http://localhost:8080/health/gpu | jq .'

# Check database
docker-compose exec postgres psql -U orchestrator -d gpu_orchestrator
```

## Troubleshooting

### Orchestrator can't connect to PostgreSQL
```bash
docker-compose logs postgres
# Check POSTGRES_HOST, POSTGRES_USER, POSTGRES_PASSWORD
```

### Workers not registering
```bash
docker-compose logs orchestrator
docker-compose logs sdxl-worker
# Check ETCD_ENDPOINT, REDIS_URL
```

### GPU not detected
```bash
docker-compose exec sdxl-worker nvidia-smi
# Check nvidia-docker is installed
```

### Port 8080 already in use
```bash
# Find what's using it
sudo lsof -i :8080
# Change docker-compose ports
```

## Scaling

Add more workers:
```bash
docker-compose up -d --scale sdxl-worker=2 sdxl-worker
```

## Stopping

```bash
# Stop all
docker-compose down

# Stop specific service
docker-compose stop orchestrator

# Remove volumes (WARNING: deletes data)
docker-compose down -v
```
