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
from shared.gpu_lock import GPUMemoryLock

logger = logging.getLogger(__name__)

MODEL_DIR = os.getenv("MODEL_DIR", "/models")
HF_REPO = os.getenv(
    "HF_REPO", "Jackrong/Qwen3.5-27B-Claude-4.6-Opus-Reasoning-Distilled-GGUF"
)
MODEL_FILE = os.getenv(
    "MODEL_FILE", "Qwen3.5-27B-Claude-4.6-Opus-Reasoning-Distilled-Q4_K_M.gguf"
)
MMPROJ_FILE = os.getenv("MMPROJ_FILE", "mmproj-F16.gguf")
DISABLE_VISION = os.getenv("DISABLE_VISION", "true").lower() in ("1", "true", "yes")
MODEL_PATH = os.path.join(MODEL_DIR, MODEL_FILE)
MMPROJ_PATH = os.path.join(MODEL_DIR, MMPROJ_FILE)

N_CTX = int(os.getenv("N_CTX", "16384"))
N_GPU_LAYERS = int(os.getenv("N_GPU_LAYERS", "99"))
N_THREADS = int(os.getenv("N_THREADS", "8"))
N_UBATCH = int(os.getenv("N_UBATCH", "256"))

# When vision is enabled, mmproj takes ~857 MiB extra VRAM.
# Auto-reduce context to 8192 (KV: 170→85 MiB) to fit. Override with N_CTX_VISION.
N_CTX_VISION = int(os.getenv("N_CTX_VISION", "8192"))
N_UBATCH_VISION = int(os.getenv("N_UBATCH_VISION", "256"))
IDLE_TIMEOUT = int(os.getenv("IDLE_TIMEOUT", "300"))
SERVER_PORT = int(os.getenv("LLAMA_SERVER_PORT", "18080"))
SERVER_URL = f"http://127.0.0.1:{SERVER_PORT}"

WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the web for current, up-to-date information, news, facts, blog posts, "
            "GitHub repos, or anything not covered by arxiv. For research topics, use this "
            "alongside arxiv_search to find blog posts, project pages, code releases, and "
            "discussions that supplement the academic papers."
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

ARXIV_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "arxiv_search",
        "description": (
            "Search arXiv for recent academic papers and preprints, sorted by submission date. "
            "Use this for finding scientific research papers on technical topics. "
            "For comprehensive research, combine with web_search to also get blog posts, "
            "project pages, and code. Returns titles, authors, submission dates, and full abstracts."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search terms (e.g. 'monocular 3D Gaussian Splatting single image')",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Number of papers to return (default 5, max 10)",
                },
            },
            "required": ["query"],
        },
    },
}


class Qwen35ChatHandler:
    def __init__(
        self, redis_client=None, worker_id="qwen35-chat-worker", state_callback=None
    ):
        self.server_proc = None
        self.redis_client = redis_client
        self.worker_id = worker_id
        self.last_used = None
        self.cleanup_timer = None
        self._model_lock = threading.Lock()
        self.gpu_lock = GPUMemoryLock(redis_client, worker_id) if redis_client else None
        # Callable(state: str) → None.  Called on every model-lifecycle event so
        # main.py can push the new state to etcd immediately.
        # States: "IDLE" | "WARM" | "PROCESSING"
        self._state_cb = state_callback

    def _publish_state(self, state: str) -> None:
        """Fire the state callback if one was provided (safe to call from any thread)."""
        if self._state_cb is not None:
            try:
                self._state_cb(state)
            except Exception as exc:
                logger.warning(f"[STATE_CB] Failed to publish state={state}: {exc}")

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
            hf_hub_download(
                repo_id=HF_REPO, filename=MODEL_FILE, local_dir=MODEL_DIR, token=token
            )
            logger.info(f"Download complete: {MODEL_PATH}")
        else:
            logger.info(f"Model found at {MODEL_PATH}")

        if not os.path.exists(MMPROJ_PATH):
            logger.info(f"Downloading vision encoder {MMPROJ_FILE}…")
            try:
                hf_hub_download(
                    repo_id=HF_REPO,
                    filename=MMPROJ_FILE,
                    local_dir=MODEL_DIR,
                    token=token,
                )
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

            if self.gpu_lock:
                logger.info("[GPU_LOCK] Acquiring lock before loading llama-server...")
                try:
                    with self.gpu_lock.locked(timeout=1800, required_memory_mb=15000):
                        self._load_model_internal()
                except TimeoutError as e:
                    logger.error(f"[GPU_LOCK] Failed to acquire lock: {e}")
                    raise
                return

            self._load_model_internal()

    def _load_model_internal(self):
        """Internal: download and start llama-server (call with gpu_lock held)."""
        self._download_model()

        vision_active = not DISABLE_VISION and os.path.exists(MMPROJ_PATH)
        ctx = N_CTX_VISION if vision_active else N_CTX
        ubatch = N_UBATCH_VISION if vision_active else N_UBATCH

        cmd = [
            "llama-server",
            "--model",
            MODEL_PATH,
            "--ctx-size",
            str(ctx),
            "--n-gpu-layers",
            str(N_GPU_LAYERS),
            "--threads",
            str(N_THREADS),
            "--parallel",
            "1",  # single slot — reduces KV/rs cache, prevents OOM
            "--ubatch-size",
            str(ubatch),  # micro-batch for compute buffer; smaller = less VRAM
            "--flash-attn",
            "on",  # halves KV cache VRAM, enables larger context
            "--cache-type-k",
            "q8_0",  # quantize KV cache for more headroom
            "--cache-type-v",
            "q8_0",
            "--port",
            str(SERVER_PORT),
            "--host",
            "127.0.0.1",
        ]
        if vision_active:
            cmd += ["--mmproj", MMPROJ_PATH]
            logger.info("Vision projector attached")
        elif DISABLE_VISION:
            logger.info("Vision projector disabled (DISABLE_VISION=true)")

        logger.info(
            f"Starting llama-server | {MODEL_FILE} "
            f"| ctx={ctx} | ubatch={ubatch} | gpu_layers={N_GPU_LAYERS} "
            f"| vision={'on' if vision_active else 'off'} | port={SERVER_PORT}"
        )
        log_path = f"/tmp/llama-server-{SERVER_PORT}.log"
        self._llama_log = open(log_path, "w")
        self.server_proc = subprocess.Popen(
            cmd, stdout=self._llama_log, stderr=subprocess.STDOUT
        )

        deadline = time.time() + 600
        while time.time() < deadline:
            if self.server_proc.poll() is not None:
                self._llama_log.flush()
                with open(log_path) as f:
                    out = f.read()
                raise RuntimeError(f"llama-server exited:\n{out[-2000:]}")
            try:
                if requests.get(f"{SERVER_URL}/health", timeout=2).status_code == 200:
                    logger.info("llama-server ready")
                    return
            except requests.exceptions.ConnectionError:
                pass
            time.sleep(1)

        self.server_proc.kill()
        raise RuntimeError("llama-server did not become ready within 10 minutes")

    def offload_model(self):
        """Terminate llama-server to free GPU VRAM for other workers (WARM → IDLE)."""
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
            self.last_used = None
            gc.collect()
            logger.info("llama-server stopped — GPU VRAM freed")
        # Publish IDLE outside the lock so the callback can itself acquire locks
        self._publish_state("IDLE")

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
                lines.append(f"   {r['body'][:250]}\n")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"Web search error: {e}")
            return f"Search failed: {e}"

    def _arxiv_search(self, query: str, max_results: int = 5) -> str:
        """Query the arXiv API and return structured paper results sorted by date."""
        import urllib.parse
        import urllib.request
        import xml.etree.ElementTree as ET

        max_results = min(int(max_results), 10)
        try:
            encoded = urllib.parse.quote(query)
            url = (
                f"http://export.arxiv.org/api/query?"
                f"search_query=all:{encoded}"
                f"&max_results={max_results}"
                f"&sortBy=submittedDate&sortOrder=descending"
            )
            req = urllib.request.Request(
                url, headers={"User-Agent": "qwen35-chat-worker/1.0"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                content = resp.read().decode("utf-8")

            ns = {"atom": "http://www.w3.org/2005/Atom"}
            root = ET.fromstring(content)
            entries = root.findall("atom:entry", ns)

            if not entries:
                return f"No arXiv papers found for: {query}"

            lines = [f"arXiv papers (sorted by date, newest first) for: {query}\n"]
            for i, entry in enumerate(entries, 1):
                title = entry.find("atom:title", ns)
                summary = entry.find("atom:summary", ns)
                pub = entry.find("atom:published", ns)
                pid = entry.find("atom:id", ns)
                authors = entry.findall("atom:author", ns)

                title_text = (title.text or "").strip().replace("\n", " ")
                abstract = (summary.text or "").strip().replace("\n", " ")[:300]
                pub_date = (pub.text or "")[:10]
                paper_id = (pid.text or "").strip()
                author_names = [
                    (a.find("atom:name", ns).text or "")
                    for a in authors[:4]
                    if a.find("atom:name", ns) is not None
                ]
                lines.append(f"{i}. {title_text}")
                lines.append(f"   Date: {pub_date}")
                lines.append(f"   Authors: {', '.join(author_names)}")
                lines.append(f"   URL: {paper_id}")
                lines.append(f"   Abstract: {abstract}\n")

            return "\n".join(lines)
        except Exception as e:
            logger.error(f"arXiv search error: {e}")
            return f"arXiv search failed: {e}"

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
            "messages": messages,
            "temperature": temperature,
            "top_p": top_p,
            "top_k": top_k,
            "max_tokens": max_tokens,
            "presence_penalty": presence_penalty,
            "stream": True,
        }
        if not enable_thinking:
            # Qwen3.5 ignores /no_think; the proper way is chat_template_kwargs
            body["chat_template_kwargs"] = {"enable_thinking": False}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        resp = requests.post(
            f"{SERVER_URL}/v1/chat/completions",
            json=body,
            stream=True,
            timeout=600,
        )
        if resp.status_code == 400:
            detail = resp.text[:500]
            raise RuntimeError(
                f"Input too long for model context ({N_CTX} tokens). "
                f"Try shorter messages or fewer attached files. Server: {detail}"
            )
        resp.raise_for_status()

        parts: List[str] = []
        token_count = 0
        think_open = False
        think_closed = False
        tool_buf: Dict[int, Dict] = {}  # index → {id, name, arguments}

        for raw_line in resp.iter_lines():
            if not raw_line or not raw_line.startswith(b"data: "):
                continue
            data = raw_line[6:]
            if data == b"[DONE]":
                break
            try:
                chunk = json.loads(data)
                choice = chunk["choices"][0]
                delta = choice.get("delta", {})

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

        State transitions inside this method:
          IDLE / WARM → load_model() (no-op if already loaded) → PROCESSING → WARM
        """
        self.cancel_cleanup()

        try:
            # Warmup: load the model and immediately signal done with no tokens
            if params.get("warmup", "false").lower() == "true":
                stream_key = f"chat-stream:{job_id}"
                self.load_model()
                self._publish_state("WARM")  # model is now in VRAM, not inferring
                if self.redis_client:
                    self.redis_client.xadd(stream_key, {"type": "done", "content": ""})
                    self.redis_client.expire(stream_key, 60)
                self.last_used = time.time()
                self._reset_idle_timer()
                logger.info(f"[JOB {job_id}] Warmup complete")
                return {"success": True, "output": {"response": "", "warmup": True}}

            self.load_model()
            # Model is now in VRAM and we are about to start inference
            self._publish_state("PROCESSING")

            messages: List[Dict] = json.loads(params.get("messages", "[]"))
            temperature = float(params.get("temperature", "0.7"))
            top_p = float(params.get("top_p", "0.8"))
            top_k = int(params.get("top_k", "20"))
            max_tokens = int(params.get("max_tokens", "8192"))
            presence_penalty = float(params.get("presence_penalty", "1.5"))
            enable_thinking = params.get("enable_thinking", "false").lower() == "true"
            enable_web_search = (
                params.get("enable_web_search", "false").lower() == "true"
            )

            if not messages:
                return {"success": False, "error": "No messages provided"}

            stream_key = f"chat-stream:{job_id}"
            logger.info(
                f"[JOB {job_id}] {'thinking' if enable_thinking else 'no-think'}"
                f"{' +search' if enable_web_search else ''} | {len(messages)} msgs"
            )

            tools = [WEB_SEARCH_TOOL, ARXIV_SEARCH_TOOL] if enable_web_search else None
            working_messages = list(messages)
            total_tokens = 0
            final_text = ""
            MAX_TOOL_ROUNDS = 5  # after this many search rounds, force a final response

            for _round in range(MAX_TOOL_ROUNDS + 2):
                # After MAX_TOOL_ROUNDS, strip tools so the model is forced to respond
                active_tools = tools if (tools and _round < MAX_TOOL_ROUNDS) else None
                text, n_tok, pending = self._stream_round(
                    stream_key,
                    working_messages,
                    temperature,
                    top_p,
                    top_k,
                    max_tokens,
                    presence_penalty,
                    enable_thinking,
                    active_tools,
                )
                total_tokens += n_tok

                if not pending:
                    final_text = text
                    break

                # Build assistant tool-call message
                working_messages.append(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": tc["id"],
                                "type": "function",
                                "function": {
                                    "name": tc["name"],
                                    "arguments": tc["arguments"],
                                },
                            }
                            for tc in pending
                        ],
                    }
                )

                # Execute each tool call
                for tc in pending:
                    try:
                        args = json.loads(tc["arguments"])
                    except json.JSONDecodeError:
                        args = {}
                    query = args.get("query", "")

                    if tc["name"] == "web_search":
                        logger.info(f"[JOB {job_id}] web_search({query!r})")
                        self._pub(
                            stream_key,
                            "tool_call",
                            json.dumps({"name": "web_search", "query": query}),
                        )
                        result = self._web_search(query)
                        self._pub(
                            stream_key,
                            "tool_result",
                            json.dumps({"name": "web_search", "query": query}),
                        )

                    elif tc["name"] == "arxiv_search":
                        max_r = args.get("max_results", 8)
                        logger.info(
                            f"[JOB {job_id}] arxiv_search({query!r}, max={max_r})"
                        )
                        self._pub(
                            stream_key,
                            "tool_call",
                            json.dumps({"name": "arxiv_search", "query": query}),
                        )
                        result = self._arxiv_search(query, max_r)
                        self._pub(
                            stream_key,
                            "tool_result",
                            json.dumps({"name": "arxiv_search", "query": query}),
                        )

                    else:
                        result = f"Unknown tool: {tc['name']}"

                    working_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": result,
                        }
                    )
                # loop → stream again with tool results in context

            # Signal completion
            if self.redis_client:
                self.redis_client.xadd(stream_key, {"type": "done", "content": ""})
                self.redis_client.expire(stream_key, 120)

            logger.info(f"[JOB {job_id}] Done — {total_tokens} tokens")
            self.last_used = time.time()
            self._reset_idle_timer()
            # Inference finished; model is still in VRAM with the idle timer running
            self._publish_state("WARM")

            return {
                "success": True,
                "output": {
                    "response": final_text,
                    "token_count": total_tokens,
                    "enable_thinking": enable_thinking,
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
            # Even on error the model is still loaded and the idle timer is running
            self._publish_state("WARM")
            return {"success": False, "error": str(e)}
