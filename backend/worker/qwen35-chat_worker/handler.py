"""
Qwen3.5-35B-A3B Chat Handler

Uses llama-server (built from the latest llama.cpp source) for GGUF inference.
llama-cpp-python on PyPI (0.3.16) predates qwen35moe architecture support.

Lifecycle:
  - load_model()    → spawns llama-server subprocess, waits for /health
  - process()       → POSTs to llama-server's OpenAI-compatible API, streams tokens
                       to Redis Stream chat-stream:{job_id} for the frontend SSE relay
  - offload_model() → terminates llama-server to free GPU VRAM
"""

import gc
import json
import logging
import os
import subprocess
import threading
import time
from typing import Any, Dict, List

import requests

logger = logging.getLogger(__name__)

MODEL_DIR = os.getenv("MODEL_DIR", "/models")
HF_REPO = os.getenv("HF_REPO", "unsloth/Qwen3.5-35B-A3B-GGUF")
MODEL_FILE = os.getenv("MODEL_FILE", "Qwen3.5-35B-A3B-UD-Q4_K_XL.gguf")
MODEL_PATH = os.path.join(MODEL_DIR, MODEL_FILE)

N_CTX = int(os.getenv("N_CTX", "16384"))
N_GPU_LAYERS = int(os.getenv("N_GPU_LAYERS", "99"))
N_THREADS = int(os.getenv("N_THREADS", "8"))
IDLE_TIMEOUT = int(os.getenv("IDLE_TIMEOUT", "300"))
SERVER_PORT = int(os.getenv("LLAMA_SERVER_PORT", "18080"))
SERVER_URL = f"http://127.0.0.1:{SERVER_PORT}"


class Qwen35ChatHandler:
    def __init__(self, redis_client=None, worker_id="qwen35-chat-worker"):
        self.server_proc = None
        self.redis_client = redis_client
        self.worker_id = worker_id
        self.last_used = None
        self.cleanup_timer = None
        self._model_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Model lifecycle
    # ------------------------------------------------------------------

    def _download_model(self):
        """Download GGUF from HuggingFace if not already present."""
        if os.path.exists(MODEL_PATH):
            logger.info(f"Model found at {MODEL_PATH}")
            return

        os.makedirs(MODEL_DIR, exist_ok=True)
        logger.info(f"Downloading {HF_REPO}/{MODEL_FILE} (~22 GB) — this will take a while...")

        from huggingface_hub import hf_hub_download

        token = os.environ.get("HF_TOKEN") or None
        hf_hub_download(
            repo_id=HF_REPO,
            filename=MODEL_FILE,
            local_dir=MODEL_DIR,
            token=token,
        )
        logger.info(f"Download complete: {MODEL_PATH}")

    def load_model(self):
        """Start llama-server subprocess and wait for it to be ready (idempotent)."""
        with self._model_lock:
            if self.server_proc and self.server_proc.poll() is None:
                logger.info("llama-server already running — reusing")
                return

            self._download_model()

            logger.info(
                f"Starting llama-server | model={MODEL_FILE} "
                f"| ctx={N_CTX} | gpu_layers={N_GPU_LAYERS} | port={SERVER_PORT}"
            )

            cmd = [
                "llama-server",
                "--model", MODEL_PATH,
                "--ctx-size", str(N_CTX),
                "--n-gpu-layers", str(N_GPU_LAYERS),
                "--threads", str(N_THREADS),
                "--port", str(SERVER_PORT),
                "--host", "127.0.0.1",
            ]

            self.server_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )

            # Wait for llama-server to finish loading (up to 5 minutes)
            deadline = time.time() + 300
            while time.time() < deadline:
                if self.server_proc.poll() is not None:
                    out = self.server_proc.stdout.read().decode(errors="replace")
                    raise RuntimeError(f"llama-server exited unexpectedly:\n{out[-2000:]}")
                try:
                    r = requests.get(f"{SERVER_URL}/health", timeout=2)
                    if r.status_code == 200:
                        logger.info("llama-server ready")
                        return
                except requests.exceptions.ConnectionError:
                    pass
                time.sleep(1)

            self.server_proc.kill()
            raise RuntimeError("llama-server failed to become ready within 5 minutes")

    def offload_model(self):
        """Terminate llama-server to free GPU VRAM for other workers."""
        with self._model_lock:
            if self.server_proc is None:
                return
            logger.info("Stopping llama-server — freeing GPU VRAM...")
            if self.cleanup_timer:
                self.cleanup_timer.cancel()
                self.cleanup_timer = None
            self.server_proc.terminate()
            try:
                self.server_proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.server_proc.kill()
            self.server_proc = None
            self.last_used = None
            gc.collect()
            logger.info("llama-server stopped")

    def _reset_idle_timer(self):
        """Restart the idle-timeout timer after each job."""
        if self.cleanup_timer:
            self.cleanup_timer.cancel()
        self.cleanup_timer = threading.Timer(IDLE_TIMEOUT, self.offload_model)
        self.cleanup_timer.daemon = True
        self.cleanup_timer.start()
        logger.info(f"Idle timer reset — server offloads in {IDLE_TIMEOUT}s if no new jobs")

    def cancel_cleanup(self):
        if self.cleanup_timer:
            self.cleanup_timer.cancel()
            self.cleanup_timer = None

    # ------------------------------------------------------------------
    # Job processing
    # ------------------------------------------------------------------

    def process(self, job_id: str, params: Dict[str, str]) -> Dict[str, Any]:
        """
        Process a chat job.

        Calls llama-server's OpenAI-compatible API with stream=True and
        publishes each token to Redis Stream  chat-stream:{job_id}  so the
        frontend SSE endpoint can relay them to the browser in real time.
        """
        self.cancel_cleanup()

        try:
            self.load_model()

            # --- parse params ---
            messages: List[Dict] = json.loads(params.get("messages", "[]"))
            temperature = float(params.get("temperature", "0.7"))
            top_p = float(params.get("top_p", "0.8"))
            top_k = int(params.get("top_k", "20"))
            max_tokens = int(params.get("max_tokens", "8192"))
            presence_penalty = float(params.get("presence_penalty", "1.5"))
            enable_thinking = params.get("enable_thinking", "false").lower() == "true"

            if not messages:
                return {"success": False, "error": "No messages provided"}

            # Non-thinking mode: prepend /no_think to the last user message.
            if not enable_thinking:
                for i in range(len(messages) - 1, -1, -1):
                    if messages[i]["role"] == "user":
                        messages[i] = dict(messages[i])
                        content = messages[i]["content"]
                        if not content.startswith("/no_think"):
                            messages[i]["content"] = "/no_think " + content
                        break

            stream_key = f"chat-stream:{job_id}"
            logger.info(
                f"[JOB {job_id}] Generating ({'thinking' if enable_thinking else 'no-think'}) "
                f"| {len(messages)} messages | temp={temperature}"
            )

            response = requests.post(
                f"{SERVER_URL}/v1/chat/completions",
                json={
                    "messages": messages,
                    "temperature": temperature,
                    "top_p": top_p,
                    "top_k": top_k,
                    "max_tokens": max_tokens,
                    "presence_penalty": presence_penalty,
                    "stream": True,
                },
                stream=True,
                timeout=600,
            )
            response.raise_for_status()

            full_response = []
            token_count = 0

            for line in response.iter_lines():
                if not line:
                    continue
                if line.startswith(b"data: "):
                    data = line[6:]
                    if data == b"[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                        token = chunk["choices"][0]["delta"].get("content", "")
                        if token:
                            full_response.append(token)
                            token_count += 1
                            if self.redis_client:
                                self.redis_client.xadd(
                                    stream_key,
                                    {"type": "token", "content": token},
                                    maxlen=20000,
                                )
                    except (json.JSONDecodeError, KeyError, IndexError):
                        pass

            # Signal completion
            if self.redis_client:
                self.redis_client.xadd(stream_key, {"type": "done", "content": ""})
                self.redis_client.expire(stream_key, 120)

            complete_response = "".join(full_response)
            logger.info(f"[JOB {job_id}] Done — {token_count} tokens generated")

            self.last_used = time.time()
            self._reset_idle_timer()

            return {
                "success": True,
                "output": {
                    "response": complete_response,
                    "token_count": token_count,
                    "enable_thinking": enable_thinking,
                },
            }

        except Exception as e:
            logger.error(f"[JOB {job_id}] Error: {e}", exc_info=True)

            # Signal error to the stream so the browser isn't left hanging
            if self.redis_client:
                try:
                    self.redis_client.xadd(
                        f"chat-stream:{job_id}",
                        {"type": "error", "content": str(e)},
                    )
                    self.redis_client.expire(f"chat-stream:{job_id}", 60)
                except Exception:
                    pass

            self._reset_idle_timer()
            return {"success": False, "error": str(e)}
