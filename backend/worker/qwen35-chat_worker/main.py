"""
Qwen3.5-35B-A3B Chat Worker
Follows the same pattern as the other GPU workers (Redis Streams + etcd + PostgreSQL).

Three-state model lifecycle
────────────────────────────
  IDLE       – container running, model NOT loaded in GPU VRAM (GPU is free)
  WARM       – model loaded in GPU VRAM, idle timer ticking, not processing
  PROCESSING – actively running inference (GPU VRAM + compute occupied)

State is published to etcd on every heartbeat tick AND immediately whenever
the model lifecycle changes, so the orchestrator always has an up-to-date view.
The orchestrator can call POST /cleanup to force an immediate WARM → IDLE
transition (model offload) without stopping the container.
"""

import json
import logging
import os
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import psycopg2
import redis
from etcd3 import client as etcd_client

sys.path.append("/app")
from handler import Qwen35ChatHandler
from shared import worker_pb2
from shared.gpu_metrics_collector import GPUMetricsCollector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ── environment ───────────────────────────────────────────────────────────────
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_USER = os.getenv("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "postgres")
POSTGRES_DB = os.getenv("POSTGRES_DB", "gpu_orchestrator")
ETCD_HOST = os.getenv("ETCD_HOST", "localhost")
ETCD_PORT = int(os.getenv("ETCD_PORT", "2379"))

STREAM_KEY = "jobs:qwen35-chat"
GROUP_NAME = "qwen35-chat-workers"
WORKER_ID = os.getenv("WORKER_ID", "qwen35-chat-worker-1")
APP_ID = "qwen35-chat"
QUEUE = STREAM_KEY

shutdown_flag = threading.Event()


def handle_shutdown(sig, frame):
    logger.info(f"Received signal {sig}, shutting down…")
    shutdown_flag.set()


signal.signal(signal.SIGTERM, handle_shutdown)
signal.signal(signal.SIGINT, handle_shutdown)

# ── global etcd state ─────────────────────────────────────────────────────────
# Set once by register_etcd(); reused by publish_state() and the heartbeat loop.
_etcd_client = None
_etcd_key = None
_etcd_lease = None
_current_state = "IDLE"  # IDLE | WARM | PROCESSING
_etcd_state_lock = threading.Lock()

# Global handler instance (set in main())
handler: Qwen35ChatHandler = None


# ── state publishing ──────────────────────────────────────────────────────────


def _build_etcd_value(state: str) -> str:
    return f"app={APP_ID},queue={QUEUE},status={state}"


def publish_state(state: str) -> None:
    """
    Update the in-memory state and push it to etcd immediately.

    Called by the handler on every model-lifecycle event (load, unload,
    job start, job finish) and by the heartbeat loop every few seconds so
    the orchestrator always has a live, accurate view of this worker.
    """
    global _current_state
    with _etcd_state_lock:
        _current_state = state
        try:
            if _etcd_client and _etcd_key and _etcd_lease:
                _etcd_client.put(_etcd_key, _build_etcd_value(state), lease=_etcd_lease)
                logger.info(f"[STATE] → {state}")
        except Exception as exc:
            logger.warning(f"[STATE] Failed to publish state={state}: {exc}")


# ── etcd registration + heartbeat ─────────────────────────────────────────────


def register_etcd():
    """Register in etcd and store the client/lease for later state updates."""
    global _etcd_client, _etcd_key, _etcd_lease
    try:
        _etcd_client = etcd_client(host=ETCD_HOST, port=ETCD_PORT)
        _etcd_key = f"/workers/{WORKER_ID}"
        _etcd_lease = _etcd_client.lease(10)  # 10-second TTL
        _etcd_client.put(_etcd_key, _build_etcd_value("IDLE"), lease=_etcd_lease)
        logger.info(f"[ETCD] Registered {WORKER_ID} (state=IDLE)")
        return _etcd_lease
    except Exception as exc:
        logger.error(f"[ETCD] Registration failed: {exc}")
        return None


def start_heartbeat(lease) -> None:
    """
    Background thread: re-publish the current model state every 3 seconds.

    Re-publishing with the existing lease both refreshes the TTL *and* keeps
    the orchestrator's view accurate (the heartbeat value includes the state).
    """

    def _loop():
        while True:
            try:
                if _etcd_client and _etcd_key and lease:
                    lease.refresh()
                    with _etcd_state_lock:
                        state = _current_state
                    _etcd_client.put(_etcd_key, _build_etcd_value(state), lease=lease)
            except Exception as exc:
                logger.error(f"[ETCD] Heartbeat error: {exc}")
            time.sleep(3)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()


# ── HTTP server (cleanup + status) ────────────────────────────────────────────


class WorkerHTTPHandler(BaseHTTPRequestHandler):
    """
    POST /cleanup  – offload the model from GPU VRAM immediately (WARM → IDLE).
                     The orchestrator calls this to free VRAM for another app
                     without killing this container.
    GET  /status   – return the current model state as JSON.
    """

    def do_POST(self):
        if self.path == "/cleanup":
            try:
                logger.info("[HTTP] /cleanup called — offloading model")
                handler.offload_model()  # triggers publish_state("IDLE") via callback
                publish_state("IDLE")  # safety-net in case callback wasn't wired
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps(
                        {"status": "success", "message": "Model offloaded"}
                    ).encode()
                )
            except Exception as exc:
                logger.error(f"[HTTP] /cleanup error: {exc}")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps({"status": "error", "message": str(exc)}).encode()
                )
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path == "/status":
            with _etcd_state_lock:
                state = _current_state
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {"worker_id": WORKER_ID, "model_state": state, "app_id": APP_ID}
                ).encode()
            )
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):  # suppress default access log
        pass


def start_http_server() -> None:
    port = int(os.getenv("CLEANUP_PORT", "8000"))
    server = HTTPServer(("0.0.0.0", port), WorkerHTTPHandler)
    logger.info(f"[HTTP] Worker HTTP server listening on port {port}")
    server.serve_forever()


# ── PostgreSQL helpers ────────────────────────────────────────────────────────


def get_pg():
    return psycopg2.connect(
        host=POSTGRES_HOST,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        dbname=POSTGRES_DB,
    )


def update_job_status(job_id, status, error=None, output=None):
    try:
        conn = get_pg()
        cur = conn.cursor()
        if status == "PROCESSING":
            cur.execute(
                "UPDATE jobs SET status=%s, started_at=NOW() WHERE id=%s",
                (status, job_id),
            )
        elif status == "COMPLETED":
            cur.execute(
                "UPDATE jobs SET status=%s, completed_at=NOW(), output=%s WHERE id=%s",
                (status, json.dumps(output) if output else None, job_id),
            )
        elif status == "FAILED":
            cur.execute(
                "UPDATE jobs SET status=%s, completed_at=NOW(), error_log=%s WHERE id=%s",
                (status, error, job_id),
            )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as exc:
        logger.error(f"[DB] update_job_status error: {exc}")


# ── job processing ────────────────────────────────────────────────────────────


def mark_active(r):
    from datetime import datetime

    try:
        r.setex(f"worker:{WORKER_ID}:last_active", 120, datetime.utcnow().isoformat())
    except Exception:
        pass


def process_job(payload, r):
    job = worker_pb2.JobRequest()
    job.ParseFromString(payload)

    logger.info(f"[JOB] Processing {job.job_id}")

    if job.app_id != APP_ID:
        update_job_status(job.job_id, "FAILED", error=f"Wrong app: {job.app_id}")
        return

    mark_active(r)
    update_job_status(job.job_id, "PROCESSING")

    try:
        result = handler.process(job.job_id, dict(job.params))
        if result.get("success"):
            update_job_status(job.job_id, "COMPLETED", output=result.get("output"))
            logger.info(f"[JOB] {job.job_id} COMPLETED")
        else:
            update_job_status(job.job_id, "FAILED", error=result.get("error"))
            logger.error(f"[JOB] {job.job_id} FAILED: {result.get('error')}")
    except Exception as exc:
        logger.error(f"[JOB] {job.job_id} ERROR: {exc}", exc_info=True)
        update_job_status(job.job_id, "FAILED", error=str(exc))


# ── main ──────────────────────────────────────────────────────────────────────


def main():
    global handler

    logger.info(f"Starting Qwen3.5-35B-A3B Chat Worker | ID={WORKER_ID}")

    # GPU metrics collector
    GPUMetricsCollector(worker_id=WORKER_ID, interval_seconds=5).start()

    # HTTP server (cleanup + status endpoints)
    threading.Thread(target=start_http_server, daemon=True).start()

    # Redis
    r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=False)
    r.ping()
    logger.info("[REDIS] Connected")

    # Handler — publish_state is wired as the lifecycle callback so the handler
    # notifies us on every model load/unload and job start/finish.
    handler = Qwen35ChatHandler(
        redis_client=r,
        worker_id=WORKER_ID,
        state_callback=publish_state,
    )

    # Consumer group
    try:
        r.xgroup_create(STREAM_KEY, GROUP_NAME, id="0", mkstream=True)
    except redis.exceptions.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise

    # etcd — register first, then start heartbeat
    lease = register_etcd()
    if lease:
        start_heartbeat(lease)

    # Reclaim pending messages from a previous crash
    try:
        pending = r.xpending_range(STREAM_KEY, GROUP_NAME, "-", "+", 10)
        for msg in pending:
            claimed = r.xclaim(
                STREAM_KEY, GROUP_NAME, WORKER_ID, 0, [msg["message_id"]]
            )
            for cid, fields in claimed:
                try:
                    process_job(fields[b"payload"], r)
                finally:
                    r.xack(STREAM_KEY, GROUP_NAME, cid)
                    r.xdel(STREAM_KEY, cid)
    except Exception as exc:
        logger.warning(f"[STARTUP] Pending message recovery: {exc}")

    logger.info(f"[READY] Listening on '{STREAM_KEY}'…")

    while not shutdown_flag.is_set():
        try:
            entries = r.xreadgroup(
                GROUP_NAME,
                WORKER_ID,
                {STREAM_KEY: ">"},
                count=1,
                block=2000,
            )
            if entries:
                for _, messages in entries:
                    for msg_id, fields in messages:
                        try:
                            process_job(fields[b"payload"], r)
                        finally:
                            r.xack(STREAM_KEY, GROUP_NAME, msg_id)
                            r.xdel(STREAM_KEY, msg_id)
        except redis.RedisError as exc:
            logger.error(f"[REDIS] Error in main loop: {exc}")
            time.sleep(1)
        except Exception as exc:
            logger.error(f"[MAIN] Loop error: {exc}", exc_info=True)
            time.sleep(1)

    logger.info("[SHUTDOWN] Worker exiting")


if __name__ == "__main__":
    main()
