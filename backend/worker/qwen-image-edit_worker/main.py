"""
Qwen Image Edit Worker
Isolated worker for image editing jobs using Qwen-Image-Edit
"""

import os
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

STREAM_KEY = "jobs:qwen-image-edit"
GROUP_NAME = "qwen-image-edit-workers"
WORKER_ID = os.getenv("WORKER_ID", "qwen-image-edit-worker-1")

from handler import QwenImageEditHandler

# Handler will be initialized after Redis connection
handler = None


class CleanupHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == '/cleanup':
            try:
                handler.offload_model()
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success", "message": "GPU memory cleaned"}).encode())
            except Exception as e:
                logger.error(f"Cleanup error: {e}")
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode())
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
            import json
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


def register_worker_etcd():
    """Register worker in etcd with TTL."""
    try:
        etcd = etcd_client(host=ETCD_HOST, port=ETCD_PORT)
        key = f"/workers/{WORKER_ID}"
        lease = etcd.lease(10)
        worker_info = f"app=qwen-image-edit,queue=jobs:qwen-image-edit,status=ONLINE"
        etcd.put(key, worker_info, lease=lease)
        logger.info(f"[SUCCESS] Worker {WORKER_ID} registered in etcd")
        return lease
    except Exception as e:
        logger.error(f"Failed to register in etcd: {e}")
        return None


def keep_alive_etcd(lease):
    """Keep etcd lease alive."""
    if lease:
        try:
            lease.refresh()
        except Exception as e:
            logger.error(f"Failed to refresh etcd lease: {e}")


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

        if job.app_id != "qwen-image-edit":
            error_msg = f"Worker for qwen-image-edit received job for {job.app_id}"
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
    server = HTTPServer(('0.0.0.0', port), CleanupHandler)
    logger.info(f"[HTTP] Cleanup endpoint running on port {port}")
    server.serve_forever()


def main():
    """Main worker loop."""
    logger.info(f"[STARTUP] Starting Qwen Image Edit Worker")
    logger.info(f"Worker ID: {WORKER_ID}")
    logger.info(f"Queue: {STREAM_KEY}")
    logger.info(f"App ID: qwen-image-edit")

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
        handler = QwenImageEditHandler(redis_client=r, worker_id=WORKER_ID)
        logger.info("[SUCCESS] Handler initialized with GPU lock coordination")
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

    lease = register_worker_etcd()

    logger.info(f"[LISTENING] Listening for jobs on '{STREAM_KEY}'...")
    last_heartbeat = time.time()

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

    while True:
        try:
            if time.time() - last_heartbeat > 5:
                keep_alive_etcd(lease)
                last_heartbeat = time.time()

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

        except KeyboardInterrupt:
            logger.info("Shutting down worker...")
            break
        except Exception as e:
            logger.error(f"Error in main loop: {e}", exc_info=True)
            time.sleep(1)


if __name__ == "__main__":
    main()
