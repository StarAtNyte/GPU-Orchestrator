"""
Qwen3.5-35B-A3B Chat Handler

Uses llama-server (latest llama.cpp from source) for GGUF inference.

Fixes vs original:
  - reasoning_content: Qwen3.5 streams thinking into delta.reasoning_content,
    not delta.content. We wrap it in <think>...</think> for the frontend.
  - non-thinking mode: uses chat_template_kwargs instead of /no_think prefix
    (Qwen3.5 does not support /no_think unlike Qwen3).
  - --parallel 1: reduces KV/rs cache footprint, avoids OOM on RTX 4090.
  - mmproj: downloads mmproj-F16.gguf for vision support if not present.
  - web search: DuckDuckGo tool-calling loop, publishes tool_call/tool_result
    events to the Redis stream so the frontend can show progress.
"""

import gc
import json
import logging
import os
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

MODEL_DIR    = os.getenv("MODEL_DIR",    "/models")
HF_REPO      = os.getenv("HF_REPO",      "unsloth/Qwen3.5-35B-A3B-GGUF")
MODEL_FILE   = os.getenv("MODEL_FILE",   "Qwen3.5-35B-A3B-UD-Q4_K_XL.gguf")
MMPROJ_FILE  = os.getenv("MMPROJ_FILE",  "mmproj-F16.gguf")
MODEL_PATH   = os.path.join(MODEL_DIR, MODEL_FILE)
MMPROJ_PATH  = os.path.join(MODEL_DIR, MMPROJ_FILE)

N_CTX           = int(os.getenv("N_CTX",          "16384"))
N_GPU_LAYERS    = int(os.getenv("N_GPU_LAYERS",    "99"))
N_THREADS       = int(os.getenv("N_THREADS",       "8"))
IDLE_TIMEOUT    = int(os.getenv("IDLE_TIMEOUT",    "300"))
SERVER_PORT     = int(os.getenv("LLAMA_SERVER_PORT","18080"))
SERVER_URL      = f"http://127.0.0.1:{SERVER_PORT}"

WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the web for current, up-to-date information, news, or facts. "
            "Use this when you need information that may have changed after your training cutoff."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query"}
            },
            "required": ["query"],
        },
    },
}


class Qwen35ChatHandler:
    def __init__(self, redis_client=None, worker_id="qwen35-chat-worker"):
        self.server_proc   = None
        self.redis_client  = redis_client
        self.worker_id     = worker_id
        self.last_used     = None
        self.cleanup_timer = None
        self._model_lock   = threading.Lock()

    # ------------------------------------------------------------------
    # Model lifecycle
    # ------------------------------------------------------------------

    def _download_model(self):
        """Download GGUF and mmproj from HuggingFace if not already present."""
        from huggingface_hub import hf_hub_download
        os.makedirs(MODEL_DIR, exist_ok=True)
        token = os.environ.get("HF_TOKEN") or None

        if not os.path.exists(MODEL_PATH):
            logger.info(f"Downloading {HF_REPO}/{MODEL_FILE} (~22 GB)…")
            hf_hub_download(repo_id=HF_REPO, filename=MODEL_FILE,
                            local_dir=MODEL_DIR, token=token)
            logger.info(f"Download complete: {MODEL_PATH}")
        else:
            logger.info(f"Model found at {MODEL_PATH}")

        if not os.path.exists(MMPROJ_PATH):
            logger.info(f"Downloading vision encoder {MMPROJ_FILE}…")
            try:
                hf_hub_download(repo_id=HF_REPO, filename=MMPROJ_FILE,
                                local_dir=MODEL_DIR, token=token)
                logger.info(f"mmproj downloaded: {MMPROJ_PATH}")
            except Exception as e:
                logger.warning(f"mmproj download failed (vision disabled): {e}")
        else:
            logger.info(f"mmproj found at {MMPROJ_PATH} — vision enabled")

    def load_model(self):
        """Start llama-server and wait until /health returns 200 (idempotent)."""
        with self._model_lock:
            if self.server_proc and self.server_proc.poll() is None:
                logger.info("llama-server already running — reusing")
                return

            self._download_model()

            cmd = [
                "llama-server",
                "--model",       MODEL_PATH,
                "--ctx-size",    str(N_CTX),
                "--n-gpu-layers",str(N_GPU_LAYERS),
                "--threads",     str(N_THREADS),
                "--parallel",    "1",          # single slot — reduces KV/rs cache, prevents OOM
                "--port",        str(SERVER_PORT),
                "--host",        "127.0.0.1",
            ]
            if os.path.exists(MMPROJ_PATH):
                cmd += ["--mmproj", MMPROJ_PATH]
                logger.info("Vision projector attached")

            logger.info(
                f"Starting llama-server | {MODEL_FILE} "
                f"| ctx={N_CTX} | gpu_layers={N_GPU_LAYERS} | port={SERVER_PORT}"
            )
            self.server_proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
            )

            deadline = time.time() + 300
            while time.time() < deadline:
                if self.server_proc.poll() is not None:
                    out = self.server_proc.stdout.read().decode(errors="replace")
                    raise RuntimeError(f"llama-server exited:\n{out[-2000:]}")
                try:
                    if requests.get(f"{SERVER_URL}/health", timeout=2).status_code == 200:
                        logger.info("llama-server ready")
                        return
                except requests.exceptions.ConnectionError:
                    pass
                time.sleep(1)

            self.server_proc.kill()
            raise RuntimeError("llama-server did not become ready within 5 minutes")

    def offload_model(self):
        """Terminate llama-server to free GPU VRAM for other workers."""
        with self._model_lock:
            if self.server_proc is None:
                return
            logger.info("Stopping llama-server — freeing GPU VRAM…")
            if self.cleanup_timer:
                self.cleanup_timer.cancel()
                self.cleanup_timer = None
            self.server_proc.terminate()
            try:
                self.server_proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.server_proc.kill()
            self.server_proc = None
            self.last_used   = None
            gc.collect()
            logger.info("llama-server stopped")

    def _reset_idle_timer(self):
        if self.cleanup_timer:
            self.cleanup_timer.cancel()
        self.cleanup_timer = threading.Timer(IDLE_TIMEOUT, self.offload_model)
        self.cleanup_timer.daemon = True
        self.cleanup_timer.start()
        logger.info(f"Idle timer reset — offloads in {IDLE_TIMEOUT}s")

    def cancel_cleanup(self):
        if self.cleanup_timer:
            self.cleanup_timer.cancel()
            self.cleanup_timer = None

    # ------------------------------------------------------------------
    # Tool: web search
    # ------------------------------------------------------------------

    def _web_search(self, query: str, max_results: int = 5) -> str:
        """Run a DuckDuckGo search and return formatted text results."""
        try:
            from duckduckgo_search import DDGS
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results))
            if not results:
                return "No results found."
            lines = [f"Web search results for: {query}\n"]
            for i, r in enumerate(results, 1):
                lines.append(f"{i}. {r['title']}")
                lines.append(f"   {r['href']}")
                lines.append(f"   {r['body'][:400]}\n")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"Web search error: {e}")
            return f"Search failed: {e}"

    # ------------------------------------------------------------------
    # Streaming helper
    # ------------------------------------------------------------------

    def _pub(self, stream_key: str, msg_type: str, content: str):
        if self.redis_client:
            self.redis_client.xadd(
                stream_key, {"type": msg_type, "content": content}, maxlen=20000
            )

    def _stream_round(
        self,
        stream_key: str,
        messages: List[Dict],
        temperature: float,
        top_p: float,
        top_k: int,
        max_tokens: int,
        presence_penalty: float,
        enable_thinking: bool,
        tools: Optional[List[Dict]],
    ) -> Tuple[str, int, List[Dict]]:
        """
        One streaming call to llama-server.

        Returns (text, token_count, pending_tool_calls).
        text includes <think>…</think> wrapping for reasoning_content.
        If pending_tool_calls is non-empty the caller should execute them
        and call again.
        """
        body: Dict[str, Any] = {
            "messages":        messages,
            "temperature":     temperature,
            "top_p":           top_p,
            "top_k":           top_k,
            "max_tokens":      max_tokens,
            "presence_penalty":presence_penalty,
            "stream":          True,
        }
        if not enable_thinking:
            # Qwen3.5 ignores /no_think; the proper way is chat_template_kwargs
            body["chat_template_kwargs"] = {"enable_thinking": False}
        if tools:
            body["tools"]       = tools
            body["tool_choice"] = "auto"

        resp = requests.post(
            f"{SERVER_URL}/v1/chat/completions",
            json=body, stream=True, timeout=600,
        )
        resp.raise_for_status()

        parts: List[str] = []
        token_count  = 0
        think_open   = False
        think_closed = False
        tool_buf: Dict[int, Dict] = {}   # index → {id, name, arguments}

        for raw_line in resp.iter_lines():
            if not raw_line or not raw_line.startswith(b"data: "):
                continue
            data = raw_line[6:]
            if data == b"[DONE]":
                break
            try:
                chunk  = json.loads(data)
                choice = chunk["choices"][0]
                delta  = choice.get("delta", {})

                # ── reasoning content (thinking mode) ──────────────────
                reasoning = delta.get("reasoning_content") or ""
                if reasoning:
                    if not think_open:
                        think_open = True
                        tok = "<think>" + reasoning
                    else:
                        tok = reasoning
                    parts.append(tok)
                    token_count += 1
                    self._pub(stream_key, "token", tok)

                # ── regular content ─────────────────────────────────────
                content = delta.get("content") or ""
                if content:
                    if think_open and not think_closed:
                        think_closed = True
                        close = "</think>"
                        parts.append(close)
                        self._pub(stream_key, "token", close)
                    parts.append(content)
                    token_count += 1
                    self._pub(stream_key, "token", content)

                # ── tool calls (accumulate fragmented JSON) ─────────────
                for tc in delta.get("tool_calls", []):
                    idx = tc.get("index", 0)
                    if idx not in tool_buf:
                        tool_buf[idx] = {"id": "", "name": "", "arguments": ""}
                    if tc.get("id"):
                        tool_buf[idx]["id"] += tc["id"]
                    fn = tc.get("function", {})
                    if fn.get("name"):
                        tool_buf[idx]["name"] += fn["name"]
                    if fn.get("arguments"):
                        tool_buf[idx]["arguments"] += fn["arguments"]

            except (json.JSONDecodeError, KeyError, IndexError):
                pass

        # Close any unclosed <think> block
        if think_open and not think_closed:
            close = "</think>"
            parts.append(close)
            self._pub(stream_key, "token", close)

        pending = list(tool_buf.values()) if tool_buf else []
        return "".join(parts), token_count, pending

    # ------------------------------------------------------------------
    # Job processing
    # ------------------------------------------------------------------

    def process(self, job_id: str, params: Dict[str, str]) -> Dict[str, Any]:
        """
        Process a chat job.
        Streams tokens (and tool call status) to Redis Stream chat-stream:{job_id}.
        """
        self.cancel_cleanup()

        try:
            # Warmup: just load the model and signal done with no tokens
            if params.get("warmup", "false").lower() == "true":
                stream_key = f"chat-stream:{job_id}"
                self.load_model()
                if self.redis_client:
                    self.redis_client.xadd(stream_key, {"type": "done", "content": ""})
                    self.redis_client.expire(stream_key, 60)
                self.last_used = time.time()
                self._reset_idle_timer()
                logger.info(f"[JOB {job_id}] Warmup complete")
                return {"success": True, "output": {"response": "", "warmup": True}}

            self.load_model()

            messages: List[Dict]  = json.loads(params.get("messages", "[]"))
            temperature            = float(params.get("temperature",      "0.7"))
            top_p                  = float(params.get("top_p",            "0.8"))
            top_k                  = int(  params.get("top_k",            "20"))
            max_tokens             = int(  params.get("max_tokens",       "8192"))
            presence_penalty       = float(params.get("presence_penalty", "1.5"))
            enable_thinking        = params.get("enable_thinking",  "false").lower() == "true"
            enable_web_search      = params.get("enable_web_search","false").lower() == "true"

            if not messages:
                return {"success": False, "error": "No messages provided"}

            stream_key = f"chat-stream:{job_id}"
            logger.info(
                f"[JOB {job_id}] {'thinking' if enable_thinking else 'no-think'}"
                f"{' +search' if enable_web_search else ''} | {len(messages)} msgs"
            )

            tools             = [WEB_SEARCH_TOOL] if enable_web_search else None
            working_messages  = list(messages)
            total_tokens      = 0
            final_text        = ""

            for _round in range(6):   # up to 5 tool-call rounds + 1 final
                text, n_tok, pending = self._stream_round(
                    stream_key, working_messages,
                    temperature, top_p, top_k, max_tokens, presence_penalty,
                    enable_thinking, tools,
                )
                total_tokens += n_tok

                if not pending:
                    final_text = text
                    break

                # Build assistant tool-call message
                working_messages.append({
                    "role":    "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id":       tc["id"],
                            "type":     "function",
                            "function": {
                                "name":      tc["name"],
                                "arguments": tc["arguments"],
                            },
                        }
                        for tc in pending
                    ],
                })

                # Execute each tool call
                for tc in pending:
                    if tc["name"] != "web_search":
                        continue
                    try:
                        args = json.loads(tc["arguments"])
                    except json.JSONDecodeError:
                        args = {}
                    query = args.get("query", "")

                    logger.info(f"[JOB {job_id}] web_search({query!r})")
                    self._pub(stream_key, "tool_call",
                              json.dumps({"name": "web_search", "query": query}))

                    result = self._web_search(query)

                    self._pub(stream_key, "tool_result",
                              json.dumps({"name": "web_search", "query": query}))

                    working_messages.append({
                        "role":         "tool",
                        "tool_call_id": tc["id"],
                        "content":      result,
                    })
                # loop → stream again with tool results in context

            # Signal completion
            if self.redis_client:
                self.redis_client.xadd(stream_key, {"type": "done", "content": ""})
                self.redis_client.expire(stream_key, 120)

            logger.info(f"[JOB {job_id}] Done — {total_tokens} tokens")
            self.last_used = time.time()
            self._reset_idle_timer()

            return {
                "success": True,
                "output": {
                    "response":       final_text,
                    "token_count":    total_tokens,
                    "enable_thinking":enable_thinking,
                },
            }

        except Exception as e:
            logger.error(f"[JOB {job_id}] Error: {e}", exc_info=True)
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
