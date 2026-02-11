from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import requests
import os

app = FastAPI(title="Qwen Image Variations")

templates = Jinja2Templates(directory="templates")

ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://orchestrator:8080")
APP_ID = "qwen-image-variations"


class VariationRequest(BaseModel):
    username: str
    image_base64: str
    steps: int = 4
    cfg_scale: float = 1.0


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Serve the Qwen Image Variations UI."""
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


@app.post("/api/variation")
async def generate_variation(request: VariationRequest):
    """Submit a variation generation job. Prompt is selected randomly by the worker."""
    try:
        response = requests.post(
            f"{ORCHESTRATOR_URL}/submit",
            json={
                "app_id": APP_ID,
                "username": request.username,
                "params": {
                    "image_base64": request.image_base64,
                    "steps": str(request.steps),
                    "cfg_scale": str(request.cfg_scale)
                }
            },
            timeout=300
        )

        if response.status_code == 200:
            data = response.json()
            return {
                "success": True,
                "job_id": data["job_id"],
                "status": data.get("status", "queued")
            }
        else:
            return {
                "success": False,
                "error": "Orchestrator error"
            }

    except requests.exceptions.RequestException as e:
        return {
            "success": False,
            "error": f"Cannot connect to orchestrator: {str(e)}"
        }


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
        else:
            return {
                "success": False,
                "error": "Job not found"
            }

    except requests.exceptions.RequestException as e:
        return {
            "success": False,
            "error": f"Cannot connect to orchestrator: {str(e)}"
        }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7866)
