"""
Main Dashboard - Service Discovery & Hub
Central entry point to all GPU orchestrator services
"""

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import requests
import os
import json
from typing import Optional

app = FastAPI(title="GPU Orchestrator Hub")

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Configuration
ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://192.168.50.28:8080")

# Service registry (frontend services)
FRONTEND_SERVICES = {
    "sdxl": {
        "name": "SDXL Image Generator",
        "url": "http://localhost:7861",
        "container_name": "sdxl-ui",
        "port": 7861,
        "icon": "🎨",
        "description": "Generate images with Stable Diffusion XL",
        "app_id": "sdxl-image-gen"
    },
    "z-image": {
        "name": "Z-Image Generator",
        "url": "http://localhost:7862",
        "container_name": "z-image-ui",
        "port": 7862,
        "icon": "🖼️",
        "description": "Generate images with Z-Image model",
        "app_id": "z-image"
    },
    "qwen": {
        "name": "Qwen Image 2512 Fast",
        "url": "http://localhost:7863",
        "container_name": "qwen-ui",
        "port": 7863,
        "icon": "⚡",
        "description": "Ultra-fast image generation with Qwen",
        "app_id": "qwen-image-2512"
    },
    "whisper": {
        "name": "Whisper Speech-to-Text",
        "url": "http://localhost:7864",
        "container_name": "whisper-ui",
        "port": 7864,
        "icon": "🎤",
        "description": "Convert speech to text with Whisper",
        "app_id": "whisper-stt"
    },
    "admin": {
        "name": "Admin Dashboard",
        "url": "http://localhost:8000",
        "container_name": "admin-dashboard",
        "port": 8000,
        "icon": "⚙️",
        "description": "Monitor jobs, workers, and system metrics",
        "app_id": None
    }
}


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Serve the main hub."""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/history", response_class=HTMLResponse)
async def history_page(request: Request):
    """Serve the user history page."""
    return templates.TemplateResponse("history.html", {"request": request})


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


@app.get("/api/services")
async def get_services():
    """Get all available services and their status."""
    services = []

    for service_key, service_info in FRONTEND_SERVICES.items():
        try:
            # Health check from inside container - use container name on Docker network
            health_url = f"http://{service_info['container_name']}:{service_info['port']}/health"
            response = requests.get(health_url, timeout=3)
            status = "online" if response.status_code == 200 else "offline"
        except Exception as e:
            status = "offline"

        services.append({
            "key": service_key,
            "name": service_info["name"],
            "url": service_info["url"],  # Keep localhost URL for browser
            "icon": service_info["icon"],
            "description": service_info["description"],
            "status": status,
            "app_id": service_info["app_id"]
        })

    return {"services": services}


@app.get("/api/gpu/health")
async def gpu_health():
    """Check GPU health status from orchestrator."""
    try:
        response = requests.get(
            f"{ORCHESTRATOR_URL}/health/gpu",
            timeout=5
        )
        if response.status_code == 200:
            return response.json()
        else:
            return {
                "status": "error",
                "is_available": False,
                "error": "Cannot reach orchestrator"
            }
    except requests.exceptions.RequestException as e:
        return {
            "status": "error",
            "is_available": False,
            "error": str(e)
        }


@app.get("/api/workers")
async def get_workers():
    """Get registered workers from orchestrator."""
    try:
        response = requests.get(f"{ORCHESTRATOR_URL}/workers", timeout=5)
        if response.status_code == 200:
            return response.json()
        else:
            return {"count": 0, "workers": []}
    except:
        return {"count": 0, "workers": []}


@app.get("/api/apps")
async def get_apps():
    """Get available applications from orchestrator."""
    try:
        response = requests.get(f"{ORCHESTRATOR_URL}/admin/config", timeout=5)
        if response.status_code == 200:
            return response.json()
        else:
            return {"apps": {}}
    except:
        return {"apps": {}}


@app.get("/api/user/jobs")
async def get_user_jobs(username: str, app_id: Optional[str] = None, status: Optional[str] = None):
    """Get user's job history from orchestrator."""
    try:
        params = {"username": username}
        if app_id:
            params["app_id"] = app_id
        if status:
            params["status"] = status

        response = requests.get(
            f"{ORCHESTRATOR_URL}/user/jobs",
            params=params,
            timeout=10
        )
        if response.status_code == 200:
            return response.json()
        else:
            return {"username": username, "count": 0, "jobs": []}
    except Exception as e:
        return {
            "username": username,
            "count": 0,
            "jobs": [],
            "error": str(e)
        }


@app.get("/api/user/jobs/{job_id}")
async def get_user_job_details(job_id: str, username: str):
    """Get detailed job information from orchestrator."""
    try:
        response = requests.get(
            f"{ORCHESTRATOR_URL}/user/jobs/{job_id}",
            params={"username": username},
            timeout=10
        )
        if response.status_code == 200:
            return response.json()
        else:
            raise HTTPException(status_code=response.status_code, detail="Job not found")
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8888)
