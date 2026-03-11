"""
Qwen Image Variations Worker
Isolated worker that generates random variations of a person's image using Qwen-Image-Edit

Three-state lifecycle:
    IDLE        → model not loaded, GPU free
    WARM        → model loaded on GPU, waiting for work
    PROCESSING  → actively running inference
"""

import os
import signal
import sys
import time
import logging
import redis
import psycopg2
from etcd3 import client as etcd_client
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import json

sys.path.append('/app')
from shared import worker_pb2
from shared.gpu_metrics_collector import GPUMetricsCollector

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_USER = os.getenv("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "postgres")
POSTGRES_DB = os.getenv("POSTGRES_DB", "gpu_orchestrator")
ETCD_HOST = os.getenv("ETCD_HOST", "localhost")
ETCD_PORT = int(os.getenv("ETCD_PORT", "2379"))

STREAM_KEY = "jobs:qwen-image-variations"
GROUP_NAME = "qwen-image-variations-workers"
WORKER_ID = os.getenv("WORKER_ID", "qwen-image-variations-worker-1")
APP_ID = "qwen-image-variations"
QUEUE = STREAM_KEY

shutdown_flag = threading.Event()


def handle_shutdown(sig, frame):
    logger.info(f"Received signal {sig}, shutting down...")
    shutdown_flag.set()


signal.signal(signal.SIGTERM, handle_shutdown)
signal.signal(signal.SIGINT, handle_shutdown)

# ── global etcd state ─────────────────────────────────────────────────────────
_etcd_client = None
_etcd_key = None
_etcd_lease = None
_current_state = "IDLE"  # IDLE | WARM | PROCESSING
_etcd_state_lock = threading.Lock()

from handler import QwenImageVariationsHandler

# Handler will be initialized after Redis connection
handler = None


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


def get_postgres_connection():
    """Create PostgreSQL connection."""
    return psycopg2.connect(
        host=POSTGRES_HOST,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        dbname=POSTGRES_DB
    )


def update_job_status(job_id, status, error=None, output=None):
    """Update job status in PostgreSQL."""
    try:
        conn = get_postgres_connection()
        cursor = conn.cursor()

        if status == "PROCESSING":
            cursor.execute(
                "UPDATE jobs SET status = %s, started_at = NOW() WHERE id = %s",
                (status, job_id)
            )
        elif status == "COMPLETED":
            cursor.execute(
                "UPDATE jobs SET status = %s, completed_at = NOW(), output = %s WHERE id = %s",
                (status, json.dumps(output) if output else None, job_id)
            )
        elif status == "FAILED":
            cursor.execute(
                "UPDATE jobs SET status = %s, completed_at = NOW(), error_log = %s WHERE id = %s",
                (status, error, job_id)
            )

        conn.commit()
        cursor.close()
        conn.close()
        logger.info(f"Updated job {job_id} to {status}")
    except Exception as e:
        logger.error(f"Failed to update job status: {e}")


def register_etcd():
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


def mark_worker_active(redis_client):
    """Mark worker as currently processing a job."""
    try:
        from datetime import datetime
        redis_client.setex(
            f"worker:{WORKER_ID}:last_active",
            120,
            datetime.utcnow().isoformat()
        )
    except Exception as e:
        logger.error(f"Failed to mark worker active: {e}")


def process_job(payload, redis_client):
    """Process a single job."""
    try:
        job = worker_pb2.JobRequest()
        job.ParseFromString(payload)

        logger.info(f"[PROCESSING] Processing job {job.job_id} for app {job.app_id}")

        mark_worker_active(redis_client)

        if job.app_id != "qwen-image-variations":
            error_msg = f"Worker for qwen-image-variations received job for {job.app_id}"
            logger.error(error_msg)
            update_job_status(job.job_id, "FAILED", error=error_msg)
            return

        update_job_status(job.job_id, "PROCESSING")

        params = dict(job.params)
        result = handler.process(job.job_id, params)

        if result.get("success"):
            logger.info(f"[SUCCESS] Job {job.job_id} completed successfully")
            update_job_status(job.job_id, "COMPLETED", output=result.get("output"))
        else:
            logger.error(f"[ERROR] Job {job.job_id} failed: {result.get('error')}")
            update_job_status(job.job_id, "FAILED", error=result.get("error"))

    except Exception as e:
        logger.error(f"Error processing job: {e}", exc_info=True)
        try:
            job_id = job.job_id if 'job' in locals() else "unknown"
            update_job_status(job_id, "FAILED", error=str(e))
        except:
            pass


def start_http_server():
    """Start HTTP server for cleanup endpoint in background thread."""
    port = int(os.getenv("CLEANUP_PORT", "8000"))
    server = HTTPServer(('0.0.0.0', port), WorkerHTTPHandler)
    logger.info(f"[HTTP] Cleanup endpoint running on port {port}")
    server.serve_forever()


def check_model_availability():
    """Check if model files are available before starting worker."""
    import os
    model_path = "/models/hub/models--Qwen--Qwen-Image-Edit"

    if not os.path.exists(model_path):
        logger.warning(f"[PREFLIGHT] Model directory not found: {model_path}")
        logger.warning("[PREFLIGHT] Model will be downloaded on first job (~20GB)")
        return True

    # Check for incomplete downloads
    incomplete_files = []
    for root, dirs, files in os.walk(model_path):
        for file in files:
            if file.endswith('.incomplete'):
                incomplete_files.append(os.path.join(root, file))

    if incomplete_files:
        logger.error(f"[PREFLIGHT] Found {len(incomplete_files)} incomplete model files!")
        logger.error("[PREFLIGHT] Model download was interrupted. Worker will fail on job processing.")
        logger.error("[PREFLIGHT] Fix: docker volume rm backend_qwen_models && restart container")
        # Don't block startup, but log the warning
        return False

    logger.info("[PREFLIGHT] Model files check passed")
    return True


def main():
    """Main worker loop."""
    logger.info(f"[STARTUP] Starting Qwen Image Variations Worker")
    logger.info(f"Worker ID: {WORKER_ID}")
    logger.info(f"Queue: {STREAM_KEY}")
    logger.info(f"App ID: qwen-image-variations")

    # Pre-flight check for model files
    check_model_availability()

    metrics_collector = GPUMetricsCollector(worker_id=WORKER_ID, interval_seconds=5)
    metrics_collector.start()
    logger.info("[SUCCESS] GPU metrics collector started")

    http_thread = threading.Thread(target=start_http_server, daemon=True)
    http_thread.start()

    try:
        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=False)
        r.ping()
        logger.info("[SUCCESS] Connected to Redis")

        # Initialize handler with Redis client for GPU lock coordination
        global handler
        handler = QwenImageVariationsHandler(redis_client=r, worker_id=WORKER_ID, state_callback=publish_state)
        logger.info("[SUCCESS] Handler initialized with GPU lock coordination + state callback")
    except Exception as e:
        logger.error(f"[ERROR] Failed to connect to Redis: {e}")
        sys.exit(1)

    try:
        r.xgroup_create(STREAM_KEY, GROUP_NAME, id="0", mkstream=True)
        logger.info(f"[SUCCESS] Created consumer group: {GROUP_NAME}")
    except redis.exceptions.ResponseError as e:
        if "BUSYGROUP" in str(e):
            logger.info(f"Consumer group {GROUP_NAME} already exists")
        else:
            logger.error(f"Error creating consumer group: {e}")

    lease = register_etcd()
    if lease:
        start_heartbeat(lease)

    logger.info(f"[LISTENING] Listening for jobs on '{STREAM_KEY}'...")

    logger.info("[STARTUP] Checking for pending messages from previous instances...")
    try:
        pending = r.xpending_range(STREAM_KEY, GROUP_NAME, "-", "+", 10)
        if pending:
            logger.info(f"[STARTUP] Found {len(pending)} pending message(s), claiming and processing...")
            for msg in pending:
                message_id = msg['message_id']
                claimed = r.xclaim(STREAM_KEY, GROUP_NAME, WORKER_ID, min_idle_time=0, message_ids=[message_id])
                if claimed:
                    for claimed_msg in claimed:
                        msg_id, fields = claimed_msg
                        logger.info(f"[STARTUP] Processing claimed pending message {msg_id}")
                        try:
                            process_job(fields[b'payload'], r)
                        except Exception as e:
                            logger.error(f"[STARTUP] Failed to process pending message {msg_id}: {e}")
                        finally:
                            r.xack(STREAM_KEY, GROUP_NAME, msg_id)
                            r.xdel(STREAM_KEY, msg_id)
                            logger.info(f"[CLEANUP] Removed claimed message {msg_id} from stream")
    except Exception as e:
        logger.warning(f"[STARTUP] Error processing pending messages: {e}")

    logger.info("[STARTUP] Pending messages processed, entering main loop...")

    while not shutdown_flag.is_set():
        try:
            entries = r.xreadgroup(
                GROUP_NAME,
                WORKER_ID,
                {STREAM_KEY: ">"},
                count=1,
                block=2000
            )

            if entries:
                for stream, messages in entries:
                    for message_id, fields in messages:
                        try:
                            process_job(fields[b'payload'], r)
                        except Exception as e:
                            logger.error(f"[ERROR] Failed to process message {message_id}: {e}")
                        finally:
                            r.xack(STREAM_KEY, GROUP_NAME, message_id)
                            r.xdel(STREAM_KEY, message_id)
                            logger.info(f"[CLEANUP] Removed message {message_id} from stream")

        except redis.RedisError as e:
            logger.error(f"Redis error in main loop: {e}")
            time.sleep(1)
        except Exception as e:
            logger.error(f"Error in main loop: {e}", exc_info=True)
            time.sleep(1)

    logger.info("Shutting down worker...")


if __name__ == "__main__":
    main()
