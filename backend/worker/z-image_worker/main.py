"""
Z-Image Worker
Isolated worker for z-image jobs

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
from shared import worker_pb2
from shared.gpu_metrics_collector import GPUMetricsCollector

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
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

STREAM_KEY = "jobs:z-image"
GROUP_NAME = "z-image-workers"
WORKER_ID = os.getenv("WORKER_ID", "z-image-worker-1")
APP_ID = "z-image"
QUEUE = STREAM_KEY

shutdown_flag = threading.Event()


def handle_shutdown(sig, frame):
    logger.info(f"Received signal {sig}, shutting down…")
    shutdown_flag.set()


signal.signal(signal.SIGTERM, handle_shutdown)
signal.signal(signal.SIGINT, handle_shutdown)

# ── global etcd state ─────────────────────────────────────────────────────────
_etcd_client = None
_etcd_key = None
_etcd_lease = None
_current_state = "IDLE"  # IDLE | WARM | PROCESSING
_etcd_state_lock = threading.Lock()

# Global handler instance (set in main())
handler = None


# ── state publishing ──────────────────────────────────────────────────────────


def _build_etcd_value(state: str) -> str:
    return f"app={APP_ID},queue={QUEUE},status={state}"


def publish_state(state: str) -> None:
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
        _etcd_lease = _etcd_client.lease(10)
        _etcd_client.put(_etcd_key, _build_etcd_value("IDLE"), lease=_etcd_lease)
        logger.info(f"[ETCD] Registered {WORKER_ID} (state=IDLE)")
        return _etcd_lease
    except Exception as exc:
        logger.error(f"[ETCD] Registration failed: {exc}")
        return None


def start_heartbeat(lease) -> None:
    consecutive_failures = 0
    max_failures = 3

    def _loop():
        nonlocal consecutive_failures
        while True:
            try:
                if _etcd_client and _etcd_key and lease:
                    lease.refresh()
                    with _etcd_state_lock:
                        state = _current_state
                    _etcd_client.put(_etcd_key, _build_etcd_value(state), lease=lease)
                    consecutive_failures = 0
            except Exception as exc:
                consecutive_failures += 1
                logger.error(f"[ETCD] Heartbeat error ({consecutive_failures}/{max_failures}): {exc}")
                if consecutive_failures >= max_failures:
                    logger.critical("[ETCD] Repeated heartbeat failures — worker may appear OFFLINE to orchestrator")
            time.sleep(3)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    logger.info("[ETCD] Heartbeat thread started")


# ── HTTP server (cleanup + status) ────────────────────────────────────────────


class WorkerHTTPHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/cleanup":
            try:
                logger.info("[HTTP] /cleanup called — offloading model")
                handler.offload_model()
                publish_state("IDLE")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success", "message": "Model offloaded"}).encode())
            except Exception as exc:
                logger.error(f"[HTTP] /cleanup error: {exc}")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(exc)}).encode())
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
            self.wfile.write(json.dumps({"worker_id": WORKER_ID, "model_state": state, "app_id": APP_ID}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def start_http_server() -> None:
    port = int(os.getenv("CLEANUP_PORT", "8000"))
    server = HTTPServer(("0.0.0.0", port), WorkerHTTPHandler)
    logger.info(f"[HTTP] Worker HTTP server listening on port {port}")
    server.serve_forever()


# ── PostgreSQL helpers ────────────────────────────────────────────────────────


def get_postgres_connection():
    return psycopg2.connect(
        host=POSTGRES_HOST,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        dbname=POSTGRES_DB,
    )


def update_job_status(job_id, status, error=None, output=None):
    try:
        conn = get_postgres_connection()
        cursor = conn.cursor()

        if status == "PROCESSING":
            cursor.execute(
                "UPDATE jobs SET status = %s, started_at = NOW() WHERE id = %s",
                (status, job_id),
            )
        elif status == "COMPLETED":
            cursor.execute(
                "UPDATE jobs SET status = %s, completed_at = NOW(), output = %s WHERE id = %s",
                (status, json.dumps(output) if output else None, job_id),
            )
        elif status == "FAILED":
            cursor.execute(
                "UPDATE jobs SET status = %s, completed_at = NOW(), error_log = %s WHERE id = %s",
                (status, error, job_id),
            )

        conn.commit()
        cursor.close()
        conn.close()
        logger.info(f"[DB] Updated job {job_id} → {status}")
    except Exception as exc:
        logger.error(f"[DB] update_job_status error: {exc}")


# ── job processing ────────────────────────────────────────────────────────────


def mark_worker_active(redis_client):
    try:
        from datetime import datetime

        redis_client.setex(
            f"worker:{WORKER_ID}:last_active",
            120,
            datetime.utcnow().isoformat(),
        )
    except Exception as exc:
        logger.error(f"[REDIS] mark_worker_active error: {exc}")


def process_job(payload, redis_client):
    try:
        job = worker_pb2.JobRequest()
        job.ParseFromString(payload)

        logger.info(f"[JOB] Processing {job.job_id} for app {job.app_id}")

        mark_worker_active(redis_client)

        if job.app_id != APP_ID:
            error_msg = f"Worker for {APP_ID} received job for {job.app_id}"
            logger.error(error_msg)
            update_job_status(job.job_id, "FAILED", error=error_msg)
            return

        update_job_status(job.job_id, "PROCESSING")

        params = dict(job.params)
        result = handler.process(job.job_id, params)

        if result.get("success"):
            logger.info(f"[JOB] {job.job_id} COMPLETED")
            update_job_status(job.job_id, "COMPLETED", output=result.get("output"))
        else:
            logger.error(f"[JOB] {job.job_id} FAILED: {result.get('error')}")
            update_job_status(job.job_id, "FAILED", error=result.get("error"))

    except Exception as exc:
        logger.error(f"[JOB] Error processing job: {exc}", exc_info=True)
        try:
            job_id = job.job_id if "job" in locals() else "unknown"
            update_job_status(job_id, "FAILED", error=str(exc))
        except Exception:
            pass


# ── main ──────────────────────────────────────────────────────────────────────


def main():
    global handler

    logger.info(f"[STARTUP] Starting Z-Image Worker | ID={WORKER_ID}")

    # GPU metrics collector
    metrics_collector = GPUMetricsCollector(worker_id=WORKER_ID, interval_seconds=5)
    metrics_collector.start()
    logger.info("[STARTUP] GPU metrics collector started")

    # HTTP server (cleanup + status endpoints)
    threading.Thread(target=start_http_server, daemon=True).start()

    # Redis
    try:
        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=False)
        r.ping()
        logger.info("[REDIS] Connected")
    except Exception as exc:
        logger.error(f"[REDIS] Connection failed: {exc}")
        sys.exit(1)

    # Handler — publish_state is wired as the lifecycle callback so the handler
    # notifies us on every model load/unload and job start/finish.
    from handler import ZImageHandler

    handler = ZImageHandler(state_callback=publish_state)
    logger.info("[STARTUP] Handler initialized with state callback")

    # Consumer group
    try:
        r.xgroup_create(STREAM_KEY, GROUP_NAME, id="0", mkstream=True)
        logger.info(f"[REDIS] Created consumer group: {GROUP_NAME}")
    except redis.exceptions.ResponseError as exc:
        if "BUSYGROUP" in str(exc):
            logger.info(f"[REDIS] Consumer group {GROUP_NAME} already exists")
        else:
            logger.error(f"[REDIS] Error creating consumer group: {exc}")

    # etcd — register first, then start heartbeat
    lease = register_etcd()
    if lease:
        start_heartbeat(lease)

    # Reclaim pending messages from a previous crash
    logger.info("[STARTUP] Checking for pending messages from previous instances…")
    try:
        pending = r.xpending_range(STREAM_KEY, GROUP_NAME, "-", "+", 10)
        if pending:
            logger.info(f"[STARTUP] Found {len(pending)} pending message(s), reclaiming…")
            for msg in pending:
                message_id = msg["message_id"]
                claimed = r.xclaim(
                    STREAM_KEY,
                    GROUP_NAME,
                    WORKER_ID,
                    min_idle_time=0,
                    message_ids=[message_id],
                )
                if claimed:
                    for claimed_msg in claimed:
                        msg_id, fields = claimed_msg
                        logger.info(f"[STARTUP] Processing claimed message {msg_id}")
                        try:
                            process_job(fields[b"payload"], r)
                        except Exception as exc:
                            logger.error(f"[STARTUP] Failed to process pending message {msg_id}: {exc}")
                        finally:
                            r.xack(STREAM_KEY, GROUP_NAME, msg_id)
                            r.xdel(STREAM_KEY, msg_id)
    except Exception as exc:
        logger.warning(f"[STARTUP] Pending message recovery error: {exc}")

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
                for stream, messages in entries:
                    for message_id, fields in messages:
                        try:
                            process_job(fields[b"payload"], r)
                        except Exception as exc:
                            logger.error(f"[MAIN] Failed to process message {message_id}: {exc}")
                        finally:
                            r.xack(STREAM_KEY, GROUP_NAME, message_id)
                            r.xdel(STREAM_KEY, message_id)

        except redis.RedisError as exc:
            logger.error(f"[REDIS] Error in main loop: {exc}")
            time.sleep(1)
        except Exception as exc:
            logger.error(f"[MAIN] Loop error: {exc}", exc_info=True)
            time.sleep(1)

    logger.info("[SHUTDOWN] Worker exiting")


if __name__ == "__main__":
    main()
