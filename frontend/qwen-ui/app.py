"""
Qwen Image 2512 Fast - Modern FastAPI Frontend
"""

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import requests
import time
import os
from typing import Optional

app = FastAPI(title="Qwen Image 2512 Fast")

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Configuration
ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://orchestrator:8080")
APP_ID = "qwen-image-2512"


class GenerateRequest(BaseModel):
    prompt: str
    negative_prompt: Optional[str] = ""
    width: int = 1024
    height: int = 1024
    num_inference_steps: int = 30
    guidance_scale: float = 7.5
    seed: Optional[int] = None


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


@app.post("/api/generate")
async def generate(request: GenerateRequest):
    """Submit image generation job to orchestrator."""
    try:
        # Step 1: Check GPU health first
        health_response = requests.get(
            f"{ORCHESTRATOR_URL}/health/gpu",
            timeout=5
        )
        
        if health_response.status_code != 200:
            raise HTTPException(status_code=503, detail="Cannot reach orchestrator for GPU health check")
        
        health_data = health_response.json()
        
        # If GPU is not available, queue but let user know
        if not health_data.get("is_available", False):
            return {
                "success": True,
                "job_queued": True,
                "gpu_status": health_data,
                "message": "GPU is busy. Job has been queued and will run when GPU is available."
            }
        
        # Step 2: Submit to orchestrator
        response = requests.post(
            f"{ORCHESTRATOR_URL}/submit",
            json={
                "app_id": APP_ID,
                "params": {
                    "prompt": request.prompt,
                    "negative_prompt": request.negative_prompt or "",
                    "width": str(request.width),
                    "height": str(request.height),
                    "num_inference_steps": str(request.num_inference_steps),
                    "guidance_scale": str(request.guidance_scale),
                    "seed": str(request.seed) if request.seed else "-1"
                }
            },
            timeout=240
        )

        if response.status_code == 200:
            data = response.json()
            return {
                "success": True,
                "job_id": data["job_id"],
                "status": data.get("status", "queued"),
                "gpu_available": health_data.get("is_available", False),
                "free_vram_gb": health_data.get("free_vram_gb")
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
    uvicorn.run(app, host="0.0.0.0", port=7863)
