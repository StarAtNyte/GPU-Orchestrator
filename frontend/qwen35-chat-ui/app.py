from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import redis.asyncio as aioredis
import requests
import os
import json
import asyncio
from typing import List, Any, Union

app = FastAPI(title="Qwen3.5 Chat")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://172.17.0.1:8890")
REDIS_HOST = os.getenv("REDIS_HOST", "172.17.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", "16379"))
APP_ID = "qwen35-chat"


class Message(BaseModel):
    role: str
    content: Union[str, List[Any]]  # str for text-only, list for multimodal


class ChatRequest(BaseModel):
    messages: List[Message]
    temperature: float = 0.7
    top_p: float = 0.8
    top_k: int = 20
    max_tokens: int = 8192
    presence_penalty: float = 1.5
    enable_thinking: bool = False
    enable_web_search: bool = False


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
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
    try:
        response = requests.get(f"{ORCHESTRATOR_URL}/workers", timeout=5)
        orchestrator_ok = response.status_code == 200
    except Exception:
        orchestrator_ok = False
    return {
        "status": "healthy",
        "orchestrator": "connected" if orchestrator_ok else "disconnected",
    }


@app.post("/api/chat")
async def submit_chat(chat_request: ChatRequest, request: Request):
    """Submit a chat job to the orchestrator. Returns job_id immediately."""
    auth_header = request.headers.get("Authorization", "")
    # Serialize messages — content may be str or list (multimodal)
    messages = [{"role": m.role, "content": m.content} for m in chat_request.messages]
    try:
        response = requests.post(
            f"{ORCHESTRATOR_URL}/submit",
            json={
                "app_id": APP_ID,
                "params": {
                    "messages": json.dumps(messages),
                    "temperature": str(chat_request.temperature),
                    "top_p": str(chat_request.top_p),
                    "top_k": str(chat_request.top_k),
                    "max_tokens": str(chat_request.max_tokens),
                    "presence_penalty": str(chat_request.presence_penalty),
                    "enable_thinking": "true" if chat_request.enable_thinking else "false",
                    "enable_web_search": "true" if chat_request.enable_web_search else "false",
                },
            },
            headers={"Authorization": auth_header},
            timeout=30,
        )
        if response.status_code == 200:
            data = response.json()
            return {"success": True, "job_id": data["job_id"]}
        return {"success": False, "error": f"Orchestrator returned {response.status_code}: {response.text}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/stream/{job_id}")
async def stream_response(job_id: str, request: Request):
    """
    SSE endpoint. Reads from Redis Stream  chat-stream:{job_id}
    and forwards tokens to the browser as Server-Sent Events.

    Event types from worker:
      token       → {content: <text>}
      tool_call   → {tool_call: {name, arguments}}
      tool_result → {tool_result: {name, result}}
      done        → {done: true}
      error       → {error: <message>}
    """

    async def generate():
        r = aioredis.from_url(
            f"redis://{REDIS_HOST}:{REDIS_PORT}", decode_responses=True
        )
        stream_key = f"chat-stream:{job_id}"
        last_id = "0-0"
        deadline = asyncio.get_event_loop().time() + 600  # 10-minute timeout

        try:
            while True:
                if await request.is_disconnected():
                    break

                if asyncio.get_event_loop().time() > deadline:
                    yield f"data: {json.dumps({'error': 'Timeout — model may still be loading'})}\n\n"
                    break

                try:
                    entries = await r.xread({stream_key: last_id}, count=50, block=5000)
                except Exception as e:
                    yield f"data: {json.dumps({'error': str(e)})}\n\n"
                    break

                if not entries:
                    yield ": waiting\n\n"
                    continue

                for _, messages in entries:
                    for msg_id, fields in messages:
                        last_id = msg_id
                        msg_type = fields.get("type", "")

                        if msg_type == "token":
                            yield f"data: {json.dumps({'content': fields.get('content', '')})}\n\n"

                        elif msg_type == "tool_call":
                            # content is JSON-encoded {name, arguments}
                            try:
                                payload = json.loads(fields.get("content", "{}"))
                            except Exception:
                                payload = {"name": "unknown", "arguments": {}}
                            yield f"data: {json.dumps({'tool_call': payload})}\n\n"

                        elif msg_type == "tool_result":
                            # content is JSON-encoded {name, result}
                            try:
                                payload = json.loads(fields.get("content", "{}"))
                            except Exception:
                                payload = {"name": "unknown", "result": ""}
                            yield f"data: {json.dumps({'tool_result': payload})}\n\n"

                        elif msg_type == "done":
                            yield f"data: {json.dumps({'done': True})}\n\n"
                            return

                        elif msg_type == "error":
                            yield f"data: {json.dumps({'error': fields.get('content', 'Unknown error')})}\n\n"
                            return
        finally:
            await r.aclose()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/warmup")
async def warmup(request: Request):
    """Submit a warmup job to pre-load the model. Returns job_id to track via SSE."""
    auth_header = request.headers.get("Authorization", "")
    try:
        response = requests.post(
            f"{ORCHESTRATOR_URL}/submit",
            json={
                "app_id": APP_ID,
                "params": {"warmup": "true"},
            },
            headers={"Authorization": auth_header},
            timeout=30,
        )
        if response.status_code == 200:
            data = response.json()
            return {"success": True, "job_id": data["job_id"]}
        return {"success": False, "error": f"Orchestrator returned {response.status_code}: {response.text}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/status/{job_id}")
async def get_status(job_id: str):
    """Fallback: get full job result from orchestrator (for reconnects)."""
    try:
        response = requests.get(f"{ORCHESTRATOR_URL}/status/{job_id}", timeout=5)
        if response.status_code == 200:
            data = response.json()
            return {
                "success": True,
                "status": data.get("status"),
                "result": data.get("result"),
                "error": data.get("error_log"),
            }
        return {"success": False, "error": "Job not found"}
    except Exception as e:
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7867)
