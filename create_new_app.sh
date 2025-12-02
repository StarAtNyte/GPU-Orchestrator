#!/bin/bash
# GPU Orchestrator - Isolated Worker Generator
# Creates a new isolated worker with its own Docker container and dependencies
# Usage: ./create_new_app.sh <app-name> <type>

set -e

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Check arguments
if [ $# -lt 2 ]; then
    echo -e "${RED}Usage:${NC} ./create_new_app.sh <app-name> <type>"
    echo ""
    echo "Arguments:"
    echo "  app-name: Your app name (lowercase, use hyphens, e.g., 'whisper-stt')"
    echo "  type:     'local', 'modal', or 'frontend'"
    echo ""
    echo "Examples:"
    echo "  ./create_new_app.sh whisper-stt local        # Creates isolated local worker"
    echo "  ./create_new_app.sh video-upscale modal      # Creates Modal cloud app"
    echo "  ./create_new_app.sh sdxl-ui frontend         # Creates frontend web UI"
    echo ""
    echo "Note: Local apps get their own isolated Docker container to avoid dependency conflicts"
    exit 1
fi

APP_NAME=$1
APP_TYPE=$2

# Validate type
if [ "$APP_TYPE" != "local" ] && [ "$APP_TYPE" != "modal" ] && [ "$APP_TYPE" != "frontend" ]; then
    echo -e "${RED}Error:${NC} Type must be 'local', 'modal', or 'frontend'"
    exit 1
fi

# Convert app-name to proper formats
APP_ID="$APP_NAME"
APP_CLASS_NAME=$(echo "$APP_NAME" | sed 's/-/ /g' | awk '{for(i=1;i<=NF;i++) $i=toupper(substr($i,1,1)) tolower(substr($i,2))}1' | sed 's/ //g')
APP_DISPLAY_NAME=$(echo "$APP_NAME" | sed 's/-/ /g' | awk '{for(i=1;i<=NF;i++) $i=toupper(substr($i,1,1)) $i}1')
HANDLER_CLASS="${APP_CLASS_NAME}Handler"
QUEUE_NAME="jobs:$APP_NAME"
WORKER_GROUP="${APP_NAME}-workers"
DEFAULT_WORKER_ID="${APP_NAME}-worker-1"
WORKER_DIR="${APP_NAME}_worker"

echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  GPU Orchestrator - Isolated Worker Generator${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════${NC}"
echo ""
echo -e "App ID:           ${GREEN}$APP_ID${NC}"
echo -e "Display Name:     ${GREEN}$APP_DISPLAY_NAME${NC}"
echo -e "Handler Class:    ${GREEN}$HANDLER_CLASS${NC}"
echo -e "Type:             ${GREEN}$APP_TYPE${NC}"

if [ "$APP_TYPE" == "local" ]; then
    echo -e "Worker Directory: ${GREEN}worker/$WORKER_DIR${NC}"
    echo -e "Queue:            ${GREEN}$QUEUE_NAME${NC}"
    echo -e "Container:        ${GREEN}${APP_NAME}-worker${NC}"
    echo -e "${YELLOW}Isolation:        ✓ Dedicated container (no dependency conflicts)${NC}"
fi
echo ""

# Confirm
read -p "Create this app? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Cancelled."
    exit 1
fi

echo ""

if [ "$APP_TYPE" == "local" ]; then
    # ============================================
    # LOCAL WORKER (Isolated Container)
    # ============================================

    echo -e "${BLUE}[1/6]${NC} Creating isolated worker directory structure..."

    WORKER_PATH="worker/$WORKER_DIR"
    mkdir -p "$WORKER_PATH"

    echo -e "${GREEN}✓${NC} Created $WORKER_PATH"

    # Create Dockerfile from template
    echo -e "${BLUE}[2/6]${NC} Generating Dockerfile..."

    sed -e "s/{{APP_NAME}}/$APP_DISPLAY_NAME/g" \
        -e "s/{{WORKER_DIR}}/$WORKER_DIR/g" \
        worker_template/Dockerfile.template > "$WORKER_PATH/Dockerfile"

    echo -e "${GREEN}✓${NC} Created $WORKER_PATH/Dockerfile"

    # Create main.py from template
    echo -e "${BLUE}[3/6]${NC} Generating worker main.py..."

    sed -e "s/{{APP_ID}}/$APP_ID/g" \
        -e "s/{{APP_DISPLAY_NAME}}/$APP_DISPLAY_NAME/g" \
        -e "s/{{QUEUE_NAME}}/$QUEUE_NAME/g" \
        -e "s/{{WORKER_GROUP}}/$WORKER_GROUP/g" \
        -e "s/{{DEFAULT_WORKER_ID}}/$DEFAULT_WORKER_ID/g" \
        -e "s/{{HANDLER_CLASS}}/$HANDLER_CLASS/g" \
        worker_template/main.py.template > "$WORKER_PATH/main.py"

    echo -e "${GREEN}✓${NC} Created $WORKER_PATH/main.py"

    # Create handler.py from template
    echo -e "${BLUE}[4/6]${NC} Generating handler.py..."

    sed -e "s/{{APP_ID}}/$APP_ID/g" \
        -e "s/{{APP_DISPLAY_NAME}}/$APP_DISPLAY_NAME/g" \
        -e "s/{{HANDLER_CLASS}}/$HANDLER_CLASS/g" \
        worker_template/handler.py.template > "$WORKER_PATH/handler.py"

    echo -e "${GREEN}✓${NC} Created $WORKER_PATH/handler.py"

    # Create requirements.txt from template
    echo -e "${BLUE}[5/6]${NC} Generating requirements.txt..."

    cp worker_template/requirements.txt.template "$WORKER_PATH/requirements.txt"

    echo -e "${GREEN}✓${NC} Created $WORKER_PATH/requirements.txt"

    # Add to config/apps.yaml
    echo -e "${BLUE}[6/6]${NC} Adding to config/apps.yaml..."

    cat >> config/apps.yaml << EOF

  # Auto-generated: $APP_ID (Isolated Worker)
  - id: "$APP_ID"
    name: "$APP_DISPLAY_NAME"
    type: "local"
    queue: "$QUEUE_NAME"
    description: "TODO: Add description"
    gpu_vram_gb: 12  # TODO: Adjust based on model requirements
    docker_image: "gpu-orchestrator-${APP_NAME}-worker:latest"
    parameters:
      - name: "input"
        type: "string"
        required: true
        description: "TODO: Add parameter description"
EOF

    echo -e "${GREEN}✓${NC} Updated config/apps.yaml"

    # Print docker-compose snippet
    echo ""
    echo -e "${BLUE}═══════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  Isolated Worker Created Successfully!${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════${NC}"
    echo ""
    echo "Generated files:"
    echo "  • $WORKER_PATH/Dockerfile"
    echo "  • $WORKER_PATH/main.py"
    echo "  • $WORKER_PATH/handler.py"
    echo "  • $WORKER_PATH/requirements.txt"
    echo "  • config/apps.yaml (updated)"
    echo ""
    echo -e "${YELLOW}[NEXT] Next Steps:${NC}"
    echo ""
    echo "1. Edit $WORKER_PATH/handler.py to implement your ML logic"
    echo "   - Replace TODO sections with your model loading code"
    echo "   - Add your processing logic in the process() method"
    echo ""
    echo "2. Add ML dependencies to $WORKER_PATH/requirements.txt"
    echo "   Examples:"
    echo "     torch>=2.1.0           # For PyTorch models"
    echo "     diffusers>=0.25.0      # For diffusion models"
    echo "     transformers>=4.36.0   # For HuggingFace models"
    echo "     vllm>=0.3.0            # For LLM inference"
    echo ""
    echo "3. Add this service to docker-compose.yml:"
    echo ""
    echo -e "${BLUE}─────────────────────────────────────────────────────${NC}"
    cat << COMPOSE_YAML
  ${APP_NAME}-worker:
    build:
      context: .
      dockerfile: worker/$WORKER_DIR/Dockerfile
    container_name: ${APP_NAME}-worker
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_started
      etcd:
        condition: service_started
    environment:
      - REDIS_HOST=redis
      - REDIS_PORT=6379
      - POSTGRES_HOST=postgres
      - POSTGRES_USER=\${POSTGRES_USER}
      - POSTGRES_PASSWORD=\${POSTGRES_PASSWORD}
      - POSTGRES_DB=\${POSTGRES_DB}
      - ETCD_HOST=etcd
      - ETCD_PORT=2379
      - WORKER_ID=${APP_NAME}-worker-1
      - MODEL_DIR=/models
    volumes:
      - ./worker/shared:/app/shared:ro
      - ${APP_NAME}-models:/models
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              capabilities: [gpu]
              # device_ids: ['0']  # Uncomment to pin to specific GPU
COMPOSE_YAML
    echo -e "${BLUE}─────────────────────────────────────────────────────${NC}"
    echo ""
    echo "4. Add volume for model cache in docker-compose.yml:"
    echo ""
    echo "   volumes:"
    echo "     ${APP_NAME}-models:"
    echo ""
    echo "5. Build and start the worker:"
    echo "   docker-compose build ${APP_NAME}-worker"
    echo "   docker-compose up -d ${APP_NAME}-worker"
    echo "   docker-compose restart orchestrator  # Reload app registry"
    echo ""
    echo "6. Test your worker:"
    echo "   curl -X POST http://localhost:8080/submit \\"
    echo "     -H \"Content-Type: application/json\" \\"
    echo "     -d '{\"app_id\":\"$APP_ID\",\"params\":{\"input\":\"test\"}}'"
    echo ""
    echo "7. Monitor logs:"
    echo "   docker-compose logs -f ${APP_NAME}-worker orchestrator"
    echo ""
    echo -e "${GREEN}✓ Each app runs in its own container = no dependency conflicts!${NC}"

elif [ "$APP_TYPE" == "modal" ]; then
    # ============================================
    # CLOUD APP (Modal/Replicate/etc)
    # ============================================

    echo -e "${BLUE}[1/3]${NC} Creating Modal app template..."

    cat > "${APP_NAME}_modal.py" << 'EOF'
"""
{{APP_DISPLAY_NAME}} - Modal App
Cloud-based ML inference endpoint
"""

import modal
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Dict, Any
import logging

app = modal.App("{{APP_NAME}}")

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Define container image with dependencies
image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install([
        "fastapi>=0.100.0",
        # TODO: Add your ML dependencies here
        # "torch>=2.1.0",
        # "diffusers>=0.25.0",
        # "transformers>=4.36.0",
    ])
)

# Request model for orchestrator integration
class JobRequest(BaseModel):
    job_id: str
    params: Dict[str, str]


@app.function(
    image=image,
    gpu="T4",  # Options: T4, A10G, A100, H100
    timeout=600,
    memory=8192,  # MB
    # secrets=[modal.Secret.from_name("huggingface")],  # Uncomment if needed
)
@modal.asgi_app()
def fastapi_app():
    """Create FastAPI app for ML inference."""

    web_app = FastAPI(
        title="{{APP_DISPLAY_NAME}}",
        description="Cloud ML inference endpoint"
    )

    # TODO: Load your model here (outside endpoints for reuse)
    # Example:
    # from transformers import pipeline
    # model = pipeline("text-generation", model="model-name", device=0)

    @web_app.get("/health")
    async def health_check():
        """Health check endpoint."""
        return {
            "status": "healthy",
            "app": "{{APP_ID}}",
            "gpu_available": True  # TODO: Check actual GPU
        }

    @web_app.post("/process")
    async def process_job(request: JobRequest):
        """
        Process job from orchestrator.

        Expected format:
        {
            "job_id": "uuid",
            "params": {
                "input": "your input data",
                ...
            }
        }

        Returns:
        {
            "success": true/false,
            "job_id": "uuid",
            "output": {...},
            "message": "status message"
        }
        """
        try:
            logger.info(f"Processing job {request.job_id}")

            # Extract parameters
            input_data = request.params.get("input")

            # Validate required parameters
            if not input_data:
                return {
                    "success": False,
                    "job_id": request.job_id,
                    "message": "Missing required parameter: input"
                }

            # TODO: Replace with your actual ML processing
            # Example:
            # result = model(input_data)
            # processed_output = {
            #     "text": result[0]["generated_text"],
            #     "tokens": len(result[0]["generated_text"].split())
            # }

            # Placeholder result
            processed_output = {
                "processed": True,
                "input_received": input_data[:100],
                "message": "TODO: Implement actual processing"
            }

            logger.info(f"Job {request.job_id} completed successfully")

            # Return standardized response
            return {
                "success": True,
                "job_id": request.job_id,
                "output": processed_output,
                "message": "Processing completed successfully"
            }

        except Exception as e:
            logger.error(f"Error processing job {request.job_id}: {e}", exc_info=True)
            return {
                "success": False,
                "job_id": request.job_id,
                "message": f"Error: {str(e)}"
            }

    return web_app


@app.local_entrypoint()
def main():
    """Local entrypoint for testing."""
    print("{{APP_DISPLAY_NAME}} - Modal App")
    print("Deploy: modal deploy {{APP_NAME}}_modal.py")
    print("Test:   modal run {{APP_NAME}}_modal.py")


if __name__ == "__main__":
    main()
EOF

    # Replace template variables
    sed -i "s/{{APP_NAME}}/$APP_NAME/g" "${APP_NAME}_modal.py"
    sed -i "s/{{APP_DISPLAY_NAME}}/$APP_DISPLAY_NAME/g" "${APP_NAME}_modal.py"
    sed -i "s/{{APP_ID}}/$APP_ID/g" "${APP_NAME}_modal.py"

    echo -e "${GREEN}✓${NC} Created ${APP_NAME}_modal.py"

    # Add to config/apps.yaml
    echo -e "${BLUE}[2/3]${NC} Adding to config/apps.yaml..."

    cat >> config/apps.yaml << EOF

  # Auto-generated: $APP_ID (Modal Cloud)
  - id: "$APP_ID"
    name: "$APP_DISPLAY_NAME"
    type: "modal"
    endpoint: "https://your-org--${APP_NAME}.modal.run/process"  # TODO: Update after deployment
    timeout_seconds: 600
    description: "TODO: Add description"
    parameters:
      - name: "input"
        type: "string"
        required: true
        description: "TODO: Add parameter description"
EOF

    echo -e "${GREEN}✓${NC} Updated config/apps.yaml"

    echo -e "${BLUE}[3/3]${NC} Done!"
    echo ""
    echo -e "${BLUE}═══════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  Modal Cloud App Created Successfully!${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════${NC}"
    echo ""
    echo "Generated files:"
    echo "  • ${APP_NAME}_modal.py"
    echo "  • config/apps.yaml (updated)"
    echo ""
    echo -e "${YELLOW}[NEXT] Next Steps:${NC}"
    echo ""
    echo "1. Edit ${APP_NAME}_modal.py to implement your ML logic"
    echo "   - Add dependencies to the image definition"
    echo "   - Load your model in the fastapi_app function"
    echo "   - Implement processing in the /process endpoint"
    echo ""
    echo "2. Install Modal CLI (if not already installed):"
    echo "   pip install modal"
    echo ""
    echo "3. Authenticate with Modal:"
    echo "   modal token new"
    echo ""
    echo "4. (Optional) Create Modal secrets for API keys:"
    echo "   modal secret create huggingface HF_TOKEN=<your-token>"
    echo ""
    echo "5. Deploy to Modal:"
    echo "   modal deploy ${APP_NAME}_modal.py"
    echo ""
    echo "6. Copy the endpoint URL from deployment output and update config/apps.yaml:"
    echo "   endpoint: \"https://your-org--${APP_NAME}.modal.run/process\""
    echo ""
    echo "7. Restart orchestrator to reload config:"
    echo "   docker-compose restart orchestrator"
    echo ""
    echo "8. Test your endpoint:"
    echo "   curl -X POST http://localhost:8080/submit \\"
    echo "     -H \"Content-Type: application/json\" \\"
    echo "     -d '{\"app_id\":\"$APP_ID\",\"params\":{\"input\":\"test\"}}'"
    echo ""
    echo "9. Monitor Modal logs:"
    echo "   modal logs ${APP_NAME}"

else
    # ============================================
    # FRONTEND APP (User-Facing UI)
    # ============================================

    echo -e "${BLUE}[1/7]${NC} Creating frontend directory structure..."

    FRONTEND_PATH="frontends/${APP_NAME}"
    mkdir -p "$FRONTEND_PATH"/{templates,static/{css,js}}

    echo -e "${GREEN}✓${NC} Created $FRONTEND_PATH with subdirectories"

    # Create app.py from SDXL template
    echo -e "${BLUE}[2/7]${NC} Generating FastAPI backend (app.py)..."

    cat > "$FRONTEND_PATH/app.py" << EOF
"""
$APP_DISPLAY_NAME - Modern FastAPI Frontend
"""

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import requests
import os
from typing import Optional, Dict

app = FastAPI(title="$APP_DISPLAY_NAME")

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Configuration
ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://orchestrator:8080")
APP_ID = "$APP_ID"


class ProcessRequest(BaseModel):
    # TODO: Define your request parameters
    input_text: str
    # Add more fields as needed


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Serve the main UI."""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/health")
async def health():
    """Health check endpoint."""
    try:
        response = requests.get(f"{ORCHESTRATOR_URL}/workers", timeout=5)
        orchestrator_healthy = response.status_code == 200
    except:
        orchestrator_healthy = False

    return {
        "status": "healthy",
        "orchestrator": "connected" if orchestrator_healthy else "disconnected"
    }


@app.post("/api/process")
async def process(request: ProcessRequest):
    """Submit processing job to orchestrator."""
    try:
        # Submit to orchestrator
        response = requests.post(
            f"{ORCHESTRATOR_URL}/submit",
            json={
                "app_id": APP_ID,
                "params": {
                    "input_text": request.input_text,
                    # TODO: Add your parameters here
                }
            },
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()
            return {
                "success": True,
                "job_id": data["job_id"],
                "status": data.get("status", "queued")
            }
        else:
            raise HTTPException(status_code=response.status_code, detail="Orchestrator error")

    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=503, detail=f"Cannot connect to orchestrator: {str(e)}")


@app.get("/api/status/{job_id}")
async def get_status(job_id: str):
    """Get job status from orchestrator."""
    try:
        response = requests.get(
            f"{ORCHESTRATOR_URL}/status/{job_id}",
            timeout=5
        )

        if response.status_code == 200:
            data = response.json()
            return {
                "success": True,
                "job_id": job_id,
                "status": data.get("status", "UNKNOWN"),
                "result": data.get("result"),
                "error": data.get("error_log"),
                "created_at": data.get("created_at"),
                "completed_at": data.get("completed_at")
            }
        elif response.status_code == 404:
            raise HTTPException(status_code=404, detail="Job not found")
        else:
            raise HTTPException(status_code=response.status_code, detail="Orchestrator error")

    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=503, detail=f"Cannot connect to orchestrator: {str(e)}")


@app.get("/api/workers")
async def get_workers():
    """Get active workers from orchestrator."""
    try:
        response = requests.get(f"{ORCHESTRATOR_URL}/workers", timeout=5)
        if response.status_code == 200:
            return response.json()
        else:
            return {"count": 0, "workers": []}
    except:
        return {"count": 0, "workers": []}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7861)
EOF

    echo -e "${GREEN}✓${NC} Created $FRONTEND_PATH/app.py"

    # Create basic HTML template
    echo -e "${BLUE}[3/7]${NC} Generating HTML template..."

    cat > "$FRONTEND_PATH/templates/index.html" << 'EOF'
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{APP_DISPLAY_NAME}}</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="/static/css/style.css">
</head>
<body>
    <nav class="nav">
        <div class="logo" id="logoHome" style="cursor: pointer;">{{APP_DISPLAY_NAME}}</div>
        <div style="display: flex; align-items: center; gap: 16px;">
            <div id="statusBadge" class="status-badge">
                <span id="statusText">Connecting...</span>
            </div>
        </div>
    </nav>

    <div class="container">
        <div class="content">
            <!-- Input Section -->
            <div class="input-section" id="inputSection">
                <div class="header">
                    <h1>{{APP_DISPLAY_NAME}}</h1>
                    <p>Process your data with AI-powered intelligence</p>
                </div>

                <div class="settings-container" style="border: 1px solid var(--gray-200); border-radius: var(--radius); overflow: hidden; background: var(--gray-50);">
                    <div class="settings-inner" style="padding: 24px; background: white;">
                        <form id="processForm">
                            <div class="form-group">
                                <label for="input">Input</label>
                                <textarea
                                    id="input"
                                    name="input_text"
                                    rows="6"
                                    placeholder="Enter your input here..."
                                    required
                                    style="width: 100%; padding: 12px; border: 1px solid var(--gray-200); border-radius: var(--radius); font-family: 'Inter', sans-serif; font-size: 14px; resize: vertical;"
                                ></textarea>
                            </div>

                            <button type="submit" class="btn" id="processBtn" style="width: 100%;">
                                Process
                            </button>
                        </form>
                    </div>
                </div>
            </div>

            <!-- Processing Status -->
            <div class="loading" id="loading" style="display: none;">
                <div class="spinner"></div>
                <div class="loading-text" id="loadingText">SUBMITTING...</div>
                <div style="margin-top: 16px; font-size: 12px; color: var(--gray-500); font-family: 'JetBrains Mono', monospace;" id="jobIdDisplay"></div>
            </div>

            <!-- Results Section -->
            <div class="results" id="results" style="display: none;">
                <div style="text-align: center; margin-bottom: 24px;">
                    <h3 style="font-size: 18px; font-weight: 600;">Processing Complete</h3>
                    <p style="font-size: 14px; color: var(--gray-500); margin-top: 8px;" id="resultInfo"></p>
                </div>

                <div id="resultsContainer" style="max-width: 1200px; margin-left: auto; margin-right: auto;">
                    <div class="result-container" style="padding: 24px; background: var(--gray-50); border-radius: var(--radius); border: 1px solid var(--gray-200);">
                        <pre id="resultContent" style="margin: 0; white-space: pre-wrap; word-wrap: break-word; font-family: 'JetBrains Mono', monospace; font-size: 13px;"></pre>
                    </div>
                </div>

                <div class="toolbar">
                    <button class="btn btn-outline" id="newBtn">Process Another</button>
                </div>
            </div>
        </div>
    </div>

    <script src="/static/js/app.js"></script>
</body>
</html>
EOF

    # Replace template variable
    sed -i "s/{{APP_DISPLAY_NAME}}/$APP_DISPLAY_NAME/g" "$FRONTEND_PATH/templates/index.html"

    echo -e "${GREEN}✓${NC} Created $FRONTEND_PATH/templates/index.html"

    # Create basic CSS
    echo -e "${BLUE}[4/7]${NC} Generating stylesheet..."

    cat > "$FRONTEND_PATH/static/css/style.css" << 'EOF'
:root {
    --bg: #ffffff;
    --fg: #111111;
    --gray-50: #f9fafb;
    --gray-100: #f3f4f6;
    --gray-200: #e5e7eb;
    --gray-300: #d1d5db;
    --gray-500: #6b7280;
    --accent: #111111;
    --radius: 8px;
}

* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

body {
    font-family: 'Inter', sans-serif;
    background: var(--bg);
    color: var(--fg);
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    -webkit-font-smoothing: antialiased;
}

.nav {
    padding: 24px 40px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 1px solid var(--gray-100);
}

.logo {
    font-family: 'JetBrains Mono', monospace;
    font-weight: 600;
    font-size: 14px;
    letter-spacing: -0.02em;
    display: flex;
    align-items: center;
    gap: 8px;
}

.logo::before {
    content: '';
    width: 12px;
    height: 12px;
    background: var(--fg);
    border-radius: 2px;
}

.container {
    max-width: 1000px;
    margin: 0 auto;
    padding: 60px 20px;
    width: 100%;
    flex: 1;
}

.header {
    text-align: center;
    margin-bottom: 48px;
}

.header h1 {
    font-size: 42px;
    font-weight: 600;
    letter-spacing: -0.03em;
    margin-bottom: 12px;
    color: var(--fg);
}

.header p {
    font-size: 16px;
    color: var(--gray-500);
    max-width: 500px;
    margin: 0 auto;
    line-height: 1.5;
}

.btn {
    background: var(--fg);
    color: white;
    border: 1px solid var(--fg);
    padding: 12px 24px;
    border-radius: 6px;
    font-size: 14px;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.2s ease;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-family: 'Inter', sans-serif;
}

.btn:hover {
    background: #333;
    border-color: #333;
    transform: translateY(-1px);
}

.btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
    transform: none;
}

.btn-outline {
    background: transparent;
    color: var(--fg);
    border: 1px solid var(--gray-200);
}

.btn-outline:hover {
    border-color: var(--fg);
    background: transparent;
}

.loading {
    display: none;
    text-align: center;
    padding: 60px 0;
}

.spinner {
    width: 24px;
    height: 24px;
    border: 2px solid var(--gray-200);
    border-top-color: var(--fg);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
    margin: 0 auto 16px;
}

@keyframes spin {
    to { transform: rotate(360deg); }
}

.loading-text {
    font-size: 14px;
    color: var(--gray-500);
    font-family: 'JetBrains Mono', monospace;
}

.results {
    display: none;
    animation: fadeIn 0.4s ease;
}

@keyframes fadeIn {
    from { opacity: 0; transform: translateY(10px); }
    to { opacity: 1; transform: translateY(0); }
}

.result-container {
    margin-bottom: 24px;
}

.toolbar {
    display: flex;
    gap: 12px;
    justify-content: center;
    flex-wrap: wrap;
    padding: 20px;
    background: var(--gray-50);
    border-radius: var(--radius);
    border: 1px solid var(--gray-200);
    margin-bottom: 24px;
}

.form-group {
    margin-bottom: 20px;
}

label {
    display: block;
    margin-bottom: 10px;
    font-weight: 500;
    color: var(--fg);
    font-size: 14px;
}

.status-badge {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    background: rgba(16, 185, 129, 0.12);
    padding: 8px 14px;
    border-radius: 8px;
    font-size: 0.8125rem;
    font-weight: 600;
    color: #10b981;
    border: 1px solid rgba(16, 185, 129, 0.2);
}

.status-badge.disconnected {
    background: rgba(239, 68, 68, 0.12);
    color: #ef4444;
    border: 1px solid rgba(239, 68, 68, 0.2);
}

.settings-container {
    border: 1px solid var(--gray-200);
    border-radius: var(--radius);
    overflow: hidden;
    background: var(--gray-50);
}

.settings-inner {
    padding: 16px;
    background: white;
}
EOF

    echo -e "${GREEN}✓${NC} Created $FRONTEND_PATH/static/css/style.css"

    # Create JavaScript
    echo -e "${BLUE}[5/7]${NC} Generating JavaScript..."

    cat > "$FRONTEND_PATH/static/js/app.js" << 'EOF'
class AppUI {
    constructor() {
        this.currentJobId = null;
        this.pollInterval = null;

        // Elements
        this.form = document.getElementById('processForm');
        this.inputSection = document.getElementById('inputSection');
        this.loading = document.getElementById('loading');
        this.loadingText = document.getElementById('loadingText');
        this.jobIdDisplay = document.getElementById('jobIdDisplay');
        this.results = document.getElementById('results');
        this.resultContent = document.getElementById('resultContent');
        this.resultInfo = document.getElementById('resultInfo');
        this.newBtn = document.getElementById('newBtn');
        this.statusBadge = document.getElementById('statusBadge');
        this.statusText = document.getElementById('statusText');
        this.logoHome = document.getElementById('logoHome');

        // Event listeners
        this.form.addEventListener('submit', (e) => this.handleSubmit(e));
        this.newBtn.addEventListener('click', () => this.reset());
        this.logoHome.addEventListener('click', () => this.reset());

        // Check health
        this.checkHealth();
        setInterval(() => this.checkHealth(), 5000);
    }

    async checkHealth() {
        try {
            const response = await fetch('/health');
            const data = await response.json();
            if (data.orchestrator === 'connected') {
                this.statusBadge.classList.remove('disconnected');
                this.statusText.textContent = 'Ready • Orchestrator Connected';
            } else {
                this.statusBadge.classList.add('disconnected');
                this.statusText.textContent = 'Disconnected • Check Orchestrator';
            }
        } catch (error) {
            this.statusBadge.classList.add('disconnected');
            this.statusText.textContent = 'Disconnected • Check Orchestrator';
        }
    }

    async handleSubmit(e) {
        e.preventDefault();

        const formData = new FormData(this.form);
        const data = {
            input_text: formData.get('input_text')
        };

        // Show loading
        this.inputSection.style.display = 'none';
        this.results.style.display = 'none';
        this.loading.style.display = 'block';
        this.loadingText.textContent = 'SUBMITTING JOB...';
        this.jobIdDisplay.textContent = '';

        try {
            const response = await fetch('/api/process', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });

            const result = await response.json();

            if (result.success) {
                this.currentJobId = result.job_id;
                this.jobIdDisplay.textContent = `Job ID: ${result.job_id}`;
                this.loadingText.textContent = 'QUEUED...';
                this.startPolling();
            } else {
                alert('Failed to submit job: ' + (result.error || 'Unknown error'));
                this.reset();
            }
        } catch (error) {
            alert('Error: ' + error.message);
            this.reset();
        }
    }

    startPolling() {
        this.pollInterval = setInterval(async () => {
            try {
                const response = await fetch(`/api/status/${this.currentJobId}`);
                const data = await response.json();

                if (data.success) {
                    const status = data.status;

                    if (status === 'QUEUED') {
                        this.loadingText.textContent = 'WAITING IN QUEUE...';
                    } else if (status === 'PROCESSING') {
                        this.loadingText.textContent = 'PROCESSING...';
                    } else if (status === 'COMPLETED') {
                        clearInterval(this.pollInterval);
                        this.showResult(data);
                    } else if (status === 'FAILED') {
                        clearInterval(this.pollInterval);
                        alert('Job failed: ' + (data.error || 'Unknown error'));
                        this.reset();
                    }
                }
            } catch (error) {
                console.error('Polling error:', error);
            }
        }, 1000);
    }

    showResult(data) {
        this.loading.style.display = 'none';
        this.results.style.display = 'block';

        // Display result
        if (data.result) {
            this.resultContent.textContent = JSON.stringify(data.result, null, 2);
            this.resultInfo.textContent = `Job completed successfully • ${this.currentJobId}`;
        } else {
            this.resultContent.textContent = 'No result returned';
            this.resultInfo.textContent = 'Job completed';
        }
    }

    reset() {
        if (this.pollInterval) {
            clearInterval(this.pollInterval);
        }
        this.loading.style.display = 'none';
        this.results.style.display = 'none';
        this.inputSection.style.display = 'block';
        this.currentJobId = null;
        this.form.reset();
    }
}

// Initialize
new AppUI();
EOF

    echo -e "${GREEN}✓${NC} Created $FRONTEND_PATH/static/js/app.js"

    # Create Dockerfile
    echo -e "${BLUE}[6/7]${NC} Generating Dockerfile..."

    cat > "$FRONTEND_PATH/Dockerfile" << EOF
# $APP_DISPLAY_NAME - Lightweight Frontend Container
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY app.py .
COPY static/ static/
COPY templates/ templates/

# Expose port
EXPOSE 7861

# Run the application
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7861"]
EOF

    echo -e "${GREEN}✓${NC} Created $FRONTEND_PATH/Dockerfile"

    # Create requirements.txt
    echo -e "${BLUE}[7/7]${NC} Generating requirements.txt..."

    cat > "$FRONTEND_PATH/requirements.txt" << EOF
# $APP_DISPLAY_NAME Frontend Dependencies
fastapi>=0.100.0
uvicorn[standard]>=0.23.0
jinja2>=3.1.0
python-multipart>=0.0.6
requests>=2.31.0
EOF

    echo -e "${GREEN}✓${NC} Created $FRONTEND_PATH/requirements.txt"

    echo ""
    echo -e "${BLUE}═══════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  Frontend UI Created Successfully!${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════${NC}"
    echo ""
    echo "Generated files:"
    echo "  • $FRONTEND_PATH/app.py"
    echo "  • $FRONTEND_PATH/templates/index.html"
    echo "  • $FRONTEND_PATH/static/css/style.css"
    echo "  • $FRONTEND_PATH/static/js/app.js"
    echo "  • $FRONTEND_PATH/Dockerfile"
    echo "  • $FRONTEND_PATH/requirements.txt"
    echo ""
    echo -e "${YELLOW}[NEXT] Next Steps:${NC}"
    echo ""
    echo "1. Customize the UI in $FRONTEND_PATH/templates/index.html"
    echo "   - Update form fields to match your worker's parameters"
    echo "   - Customize styling in static/css/style.css"
    echo ""
    echo "2. Update app.py to match your worker's API:"
    echo "   - Modify ProcessRequest model with your parameters"
    echo "   - Update /api/process endpoint to send correct params"
    echo ""
    echo "3. Add to docker-compose.yml:"
    echo ""
    echo -e "${BLUE}─────────────────────────────────────────────────────${NC}"
    cat << COMPOSE_YAML
  ${APP_NAME}:
    build:
      context: frontends/${APP_NAME}
    container_name: ${APP_NAME}
    restart: unless-stopped
    depends_on:
      - orchestrator
    environment:
      - ORCHESTRATOR_URL=http://orchestrator:8080
    ports:
      - "7861:7861"  # Change port if needed
COMPOSE_YAML
    echo -e "${BLUE}─────────────────────────────────────────────────────${NC}"
    echo ""
    echo "4. Build and start the frontend:"
    echo "   docker-compose build ${APP_NAME}"
    echo "   docker-compose up -d ${APP_NAME}"
    echo ""
    echo "5. Access the UI:"
    echo "   http://localhost:7861"
    echo ""
    echo "6. For a complete example, see frontends/sdxl-ui/"
    echo ""
    echo -e "${GREEN}✓ Frontend is lightweight - no GPU needed!${NC}"
fi

echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════${NC}"
echo ""
echo "📚 Documentation:"
echo "  - Full guide: ADDING_NEW_APPS.md"
echo "  - Architecture: WORKER_ISOLATION_STRATEGY.md"
echo "  - Frontend guide: FRONTEND_ARCHITECTURE.md"
echo "  - Quick ref: QUICK_REFERENCE.md"
echo ""
