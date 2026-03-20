from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import requests
import os
from typing import Optional

app = FastAPI(title="OmniLottie Generator")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://orchestrator:8080")
APP_ID = "omnilottie"

class GenerateRequest(BaseModel):
    task_type: str = "text"  # text, image, video
    prompt: str = ""
    image_base64: Optional[str] = None
    video_base64: Optional[str] = None
    max_tokens: int = 5556
    use_sampling: bool = True
    temperature: float = 0.9
    top_p: float = 0.25
    top_k: int = 5

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Serve the OmniLottie UI."""
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

@app.get("/examples")
async def list_examples():
    """Return example assets for the UI gallery."""
    result = {"text": [], "images": [], "videos": []}

    txt_path = "static/example/demo.txt"
    if os.path.exists(txt_path):
        with open(txt_path, encoding="utf-8") as f:
            result["text"] = [l.strip() for l in f if l.strip()]

    img_dir = "static/example/demo_images"
    if os.path.exists(img_dir):
        for fn in sorted(os.listdir(img_dir)):
            if not fn.endswith(".png"):
                continue
            base = fn[:-4]
            txt_file = os.path.join(img_dir, base + ".txt")
            desc = ""
            if os.path.exists(txt_file):
                with open(txt_file, encoding="utf-8") as f:
                    desc = f.read().strip()
            result["images"].append({"url": f"static/example/demo_images/{fn}", "description": desc})

    vid_dir = "static/example/demo_video"
    if os.path.exists(vid_dir):
        result["videos"] = [
            f"static/example/demo_video/{fn}"
            for fn in sorted(os.listdir(vid_dir))
            if fn.endswith(".mp4")
        ]

    return result


@app.get("/api/user/jobs")
async def get_user_jobs(request: Request):
    """Proxy user's omnilottie job history from orchestrator."""
    auth_header = request.headers.get("Authorization", "")
    try:
        response = requests.get(
            f"{ORCHESTRATOR_URL}/user/jobs",
            params={"app_id": APP_ID},
            headers={"Authorization": auth_header},
            timeout=10
        )
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

@app.get("/api/gpu/health")
async def gpu_health():
    """Check GPU health status from orchestrator."""
    try:
        response = requests.get(f"{ORCHESTRATOR_URL}/health/gpu", timeout=5)
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "is_available": False, "error": "Cannot reach orchestrator"}
    except requests.exceptions.RequestException as e:
        return {"status": "error", "is_available": False, "error": str(e)}

@app.post("/api/generate")
async def generate(req: GenerateRequest, request: Request):
    """Submit OmniLottie generation job."""
    auth_header = request.headers.get("Authorization", "")

    params = {
        "task_type": req.task_type,
        "prompt": req.prompt,
        "max_tokens": str(req.max_tokens),
        "use_sampling": str(req.use_sampling),
        "temperature": str(req.temperature),
        "top_p": str(req.top_p),
        "top_k": str(req.top_k),
    }

    if req.image_base64:
        params["image_base64"] = req.image_base64
    if req.video_base64:
        params["video_base64"] = req.video_base64

    try:
        response = requests.post(
            f"{ORCHESTRATOR_URL}/submit",
            json={
                "app_id": APP_ID,
                "params": params
            },
            headers={"Authorization": auth_header},
            timeout=240
        )

        if response.status_code == 200:
            data = response.json()
            return {
                "success": True,
                "job_id": data["job_id"]
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
    uvicorn.run(app, host="0.0.0.0", port=7868)
