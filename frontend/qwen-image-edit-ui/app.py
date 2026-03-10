from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import requests
import os
from typing import Optional

app = FastAPI(title="Qwen Image Edit")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://orchestrator:8080")
APP_ID = "qwen-image-edit"


class EditRequest(BaseModel):
    prompt: str
    image_base64: str
    negative_prompt: str = ""
    steps: int = 4
    cfg_scale: float = 1.0


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Serve the Qwen Image Edit UI."""
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


@app.post("/api/edit")
async def edit_image(req: EditRequest, request: Request):
    """Submit Qwen Image Edit job."""
    auth_header = request.headers.get("Authorization", "")
    try:
        response = requests.post(
            f"{ORCHESTRATOR_URL}/submit",
            json={
                "app_id": APP_ID,
                "params": {
                    "prompt": req.prompt,
                    "image_base64": req.image_base64,
                    "negative_prompt": req.negative_prompt,
                    "steps": str(req.steps),
                    "cfg_scale": str(req.cfg_scale)
                }
            },
            headers={"Authorization": auth_header},
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
    uvicorn.run(app, host="0.0.0.0", port=7865)
