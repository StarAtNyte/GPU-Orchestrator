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
        "port": 7861,
        "icon": "🎨",
        "description": "Generate images with Stable Diffusion XL",
        "app_id": "sdxl-image-gen"
    },
    "z-image": {
        "name": "Z-Image Generator",
        "url": "http://localhost:7862",
        "port": 7862,
        "icon": "🖼️",
        "description": "Generate images with Z-Image model",
        "app_id": "z-image"
    },
    "qwen": {
        "name": "Qwen Image 2512 Fast",
        "url": "http://localhost:7863",
        "port": 7863,
        "icon": "⚡",
        "description": "Ultra-fast image generation with Qwen",
        "app_id": "qwen-image-2512"
    },
    "whisper": {
        "name": "Whisper Speech-to-Text",
        "url": "http://localhost:7864",
        "port": 7864,
        "icon": "🎤",
        "description": "Convert speech to text with Whisper",
        "app_id": "whisper-stt"
    },
    "admin": {
        "name": "Admin Dashboard",
        "url": "http://localhost:8000",
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
            # Try to reach service health endpoint
            response = requests.get(f"{service_info['url']}/health", timeout=3)
            status = "online" if response.status_code == 200 else "offline"
        except:
            status = "offline"
        
        services.append({
            "key": service_key,
            "name": service_info["name"],
            "url": service_info["url"],
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8888)
