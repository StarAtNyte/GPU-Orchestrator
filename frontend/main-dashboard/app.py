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
    "qwen35-chat": {
        "name": "Qwen3.5 Chat",
        "url": "/gpu-polling/qwen35-chat/",
        "container_name": "qwen35-chat-ui",
        "port": 7867,
        "icon": "💬",
        "description": "Chat with Qwen3.5-35B-A3B running locally on RTX 4090",
        "app_id": "qwen35-chat"
    },
    "omnilottie": {
        "name": "OmniLottie",
        "url": "/gpu-polling/omnilottie/",
        "container_name": "omnilottie-ui",
        "port": 7868,
        "icon": "🎬",
        "description": "Convert text, images, or videos into Lottie animations",
        "app_id": "omnilottie"
    },
}


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Serve the main hub."""
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/auth/login")
async def auth_login(request: Request):
    """Proxy login to orchestrator."""
    body = await request.body()
    try:
        response = requests.post(f"{ORCHESTRATOR_URL}/auth/login", data=body,
                                 headers={"Content-Type": "application/json"}, timeout=10)
        return JSONResponse(content=response.json(), status_code=response.status_code)
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.post("/auth/signup")
async def auth_signup(request: Request):
    """Proxy signup to orchestrator."""
    body = await request.body()
    try:
        response = requests.post(f"{ORCHESTRATOR_URL}/auth/signup", data=body,
                                 headers={"Content-Type": "application/json"}, timeout=10)
        return JSONResponse(content=response.json(), status_code=response.status_code)
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


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
async def get_user_jobs(request: Request, app_id: Optional[str] = None, status: Optional[str] = None):
    """Get user's job history from orchestrator (JWT authenticated)."""
    auth_header = request.headers.get("Authorization", "")
    try:
        params = {}
        if app_id:
            params["app_id"] = app_id
        if status:
            params["status"] = status

        response = requests.get(
            f"{ORCHESTRATOR_URL}/user/jobs",
            params=params,
            headers={"Authorization": auth_header},
            timeout=10
        )
        if response.status_code == 200:
            return response.json()
        else:
            try:
                content = response.json()
            except Exception:
                content = {"error": response.text.strip()}
            return JSONResponse(content=content, status_code=response.status_code)
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.get("/api/user/jobs/{job_id}")
async def get_user_job_details(job_id: str, request: Request):
    """Get detailed job information from orchestrator."""
    auth_header = request.headers.get("Authorization", "")
    try:
        response = requests.get(
            f"{ORCHESTRATOR_URL}/user/jobs/{job_id}",
            headers={"Authorization": auth_header},
            timeout=10
        )
        if response.status_code == 200:
            return response.json()
        else:
            raise HTTPException(status_code=response.status_code, detail="Job not found")
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/user/jobs/{job_id}")
async def delete_user_job(job_id: str, request: Request):
    """Delete a job from user's history."""
    auth_header = request.headers.get("Authorization", "")
    try:
        response = requests.delete(
            f"{ORCHESTRATOR_URL}/user/jobs/{job_id}",
            headers={"Authorization": auth_header},
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
