"""
Main Dashboard - Service Discovery & Hub
Central entry point to all GPU orchestrator services
"""

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import requests
import asyncio
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
        "url": "/gpu-polling/sdxl/",
        "container_name": "sdxl-ui",
        "port": 7861,
        "icon": "🎨",
        "description": "Generate images with Stable Diffusion XL",
        "app_id": "sdxl-image-gen"
    },
    "z-image": {
        "name": "Z-Image Generator",
        "url": "/gpu-polling/zimage/",
        "container_name": "z-image-ui",
        "port": 7862,
        "icon": "🖼️",
        "description": "Generate images with Z-Image model",
        "app_id": "z-image"
    },
    "qwen-variations": {
        "name": "Qwen Image Variations",
        "url": "/gpu-polling/qwen-variations/",
        "container_name": "qwen-image-variations-ui",
        "port": 7866,
        "icon": "🎨",
        "description": "Generate random photo variations of a person",
        "app_id": "qwen-image-variations"
    },
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
        response = requests.get(f"{ORCHESTRATOR_URL}/workers", timeout=(2, 5))
        orchestrator_healthy = response.status_code == 200
    except:
        orchestrator_healthy = False

    return {
        "status": "healthy",
        "orchestrator": "connected" if orchestrator_healthy else "disconnected"
    }


async def check_service_health(container_name: str, port: int) -> str:
    """Check a single service's health with a hard timeout."""
    def _check():
        try:
            r = requests.get(f"http://{container_name}:{port}/", timeout=(1, 2))
            return "online" if r.status_code == 200 else "offline"
        except Exception:
            return "offline"
    try:
        return await asyncio.wait_for(asyncio.to_thread(_check), timeout=3.0)
    except Exception:
        return "offline"


@app.get("/api/services")
async def get_services():
    """Get all available services and their status."""
    keys = list(FRONTEND_SERVICES.keys())
    infos = list(FRONTEND_SERVICES.values())

    statuses = await asyncio.gather(*[
        check_service_health(s["container_name"], s["port"]) for s in infos
    ])

    return {"services": [
        {
            "key": key,
            "name": info["name"],
            "url": info["url"],
            "icon": info["icon"],
            "description": info["description"],
            "status": status,
            "app_id": info["app_id"]
        }
        for key, info, status in zip(keys, infos, statuses)
    ]}


@app.get("/api/gpu/health")
async def gpu_health():
    """Check GPU health status from orchestrator."""
    try:
        response = requests.get(
            f"{ORCHESTRATOR_URL}/health/gpu",
            timeout=(2, 5)
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


@app.delete("/api/user/jobs/{job_id}")
async def delete_user_job(job_id: str, username: str):
    """Delete a job from user's history."""
    try:
        response = requests.delete(
            f"{ORCHESTRATOR_URL}/user/jobs/{job_id}",
            params={"username": username},
            timeout=10
        )
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 404:
            raise HTTPException(status_code=404, detail="Job not found")
        else:
            raise HTTPException(status_code=response.status_code, detail="Failed to delete job")
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8888)
