from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import redis.asyncio as aioredis
import requests
import os
import json
import asyncio
from typing import List

app = FastAPI(title="Qwen3.5 Chat")
templates = Jinja2Templates(directory="templates")

ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://172.17.0.1:8890")
REDIS_HOST = os.getenv("REDIS_HOST", "172.17.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", "16379"))
APP_ID = "qwen35-chat"


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: List[Message]
    temperature: float = 0.7
    top_p: float = 0.8
    top_k: int = 20
    max_tokens: int = 8192
    presence_penalty: float = 1.5
    enable_thinking: bool = False
    username: str = "local"


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


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
async def submit_chat(chat_request: ChatRequest):
    """Submit a chat job to the orchestrator. Returns job_id immediately."""
    messages = [{"role": m.role, "content": m.content} for m in chat_request.messages]
    try:
        response = requests.post(
            f"{ORCHESTRATOR_URL}/submit",
            json={
                "app_id": APP_ID,
                "username": chat_request.username,
                "params": {
                    "messages": json.dumps(messages),
                    "temperature": str(chat_request.temperature),
                    "top_p": str(chat_request.top_p),
                    "top_k": str(chat_request.top_k),
                    "max_tokens": str(chat_request.max_tokens),
                    "presence_penalty": str(chat_request.presence_penalty),
                    "enable_thinking": "true" if chat_request.enable_thinking else "false",
                },
            },
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

    The worker publishes  {type: token, content: <text>}  for each token
    and  {type: done}  when finished.
    Waits up to 3 minutes to allow for model loading time.
    """

    async def generate():
        r = aioredis.from_url(
            f"redis://{REDIS_HOST}:{REDIS_PORT}", decode_responses=True
        )
        stream_key = f"chat-stream:{job_id}"
        last_id = "0-0"
        deadline = asyncio.get_event_loop().time() + 600  # 10-minute timeout (covers llama-server model load ~2min + generation)

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
                    # No tokens yet — send SSE keepalive comment and keep waiting
                    yield ": waiting\n\n"
                    continue

                for _, messages in entries:
                    for msg_id, fields in messages:
                        last_id = msg_id
                        msg_type = fields.get("type", "")
                        if msg_type == "token":
                            yield f"data: {json.dumps({'content': fields.get('content', '')})}\n\n"
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
