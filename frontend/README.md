# GPU Orchestrator Frontend - Linux Server

Lightweight UI services for remote orchestrator polling.

## Setup

### 1. Copy frontend code to frontend/
```bash
# From root directory
cp -r frontends/sdxl-ui frontend/
cp -r frontends/z-image-ui frontend/
cp -r frontends/admin-dashboard frontend/
```

### 2. Update ORCHESTRATOR_URL
Edit `docker-compose.yml` and set the Ubuntu PC IP:

```yaml
environment:
  - ORCHESTRATOR_URL=http://113.199.192.32:8080
```

Or use sed:
```bash
sed -i 's/113.199.192.32/YOUR_UBUNTU_IP/g' docker-compose.yml
```

### 3. Start services
```bash
docker-compose up -d

# Wait for services to start
docker-compose ps

# View logs
docker-compose logs -f
```

### 4. Access UIs
- SDXL: http://localhost:7861
- Z-Image: http://localhost:7862
- Admin Dashboard: http://localhost:8000

## Structure

```
frontend/
├── docker-compose.yml      (service definitions)
├── sdxl-ui/               (FastAPI + UI)
│   ├── app.py             (backend with GPU polling)
│   ├── templates/         (HTML)
│   ├── static/            (CSS, JS with GPU status)
│   ├── Dockerfile
│   └── requirements.txt
├── z-image-ui/            (similar structure)
└── admin-dashboard/       (similar structure)
```

## How it Works

```
Browser
  ↓
Frontend Service (:7861)
  ↓ polls every 3 seconds
GET /api/gpu/health
  ↓ proxy request
Orchestrator (:8080 remote)
  ↓ nvidia-smi query
Ubuntu GPU PC
  ↓ return JSON
Orchestrator
  ↓ return to frontend
Frontend
  ↓ update navbar
Browser (shows "GPU Ready" or "GPU Busy")
```

## Configuration

### Orchestrator URL
Default: `http://113.199.192.32:8080` (public IP with port forwarding)

Change in `docker-compose.yml`:
```yaml
environment:
  - ORCHESTRATOR_URL=http://YOUR_IP:8080
```

Rebuild:
```bash
docker-compose up -d --build
```

## Monitoring

### Check if frontend can reach orchestrator
```bash
curl http://113.199.192.32:8080/health/gpu

# Should return GPU status JSON
```

### View logs
```bash
docker-compose logs -f sdxl-ui
```

### Test API endpoints
```bash
# GPU health
curl http://localhost:7861/api/gpu/health

# Submit job
curl -X POST http://localhost:7861/api/generate \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "A cat",
    "width": 512,
    "height": 512
  }'

# Job status
curl http://localhost:7861/api/status/job-uuid
```

## Troubleshooting

### "GPU Status Unknown" in navbar
Check connectivity:
```bash
curl http://113.199.192.32:8080/health/gpu

# If fails:
# 1. Wrong IP? Check docker-compose.yml
# 2. Port forwarding not working? Check Ubuntu router settings
# 3. Orchestrator not running? Check backend/docker-compose.ps
```

### Jobs not submitting
```bash
# Check orchestrator endpoint
curl http://113.199.192.32:8080/submit

# Check logs
docker-compose logs sdxl-ui | grep -i error
```

### Container won't start
```bash
docker-compose logs sdxl-ui

# If dependency issue, check backend is running
# Rebuild if code changed
docker-compose build --no-cache
docker-compose up -d
```

## Scaling

Run multiple copies:
```bash
docker-compose up -d --scale sdxl-ui=3
```

## Production Deployment

### HTTPS Setup
Use nginx reverse proxy:
```nginx
server {
    listen 443 ssl;
    server_name your-domain.com;
    
    ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;
    
    location / {
        proxy_pass http://localhost:7861;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### Authentication (Future)
Add to orchestrator:
```python
@app.post("/api/generate")
async def generate(request: GenerateRequest, token: str = Header(...)):
    if not validate_token(token):
        raise HTTPException(status_code=401)
    # ... rest of code
```

## Stopping

```bash
# Stop all
docker-compose down

# Stop specific
docker-compose stop sdxl-ui

# Remove volumes
docker-compose down -v
```

## Remote Access

### From outside your network

#### Option 1: Port Forward
Forward port 7861 → Linux server on router

#### Option 2: Nginx Reverse Proxy
```bash
sudo apt install nginx
# Configure as shown in HTTPS Setup above
```

#### Option 3: Cloudflare Tunnel
```bash
docker run cloudflare/cloudflared tunnel --url http://localhost:7861
# Share public URL
```

#### Option 4: ngrok
```bash
ngrok http 7861
# Get public URL
```
