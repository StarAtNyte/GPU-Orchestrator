"""
Qwen Image 2512 Worker
Isolated worker for Qwen image generation jobs
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

# Setup paths
sys.path.append('/app')
from shared import worker_pb2
from shared.gpu_metrics_collector import GPUMetricsCollector

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_USER = os.getenv("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "postgres")
POSTGRES_DB = os.getenv("POSTGRES_DB", "gpu_orchestrator")
ETCD_HOST = os.getenv("ETCD_HOST", "localhost")
ETCD_PORT = int(os.getenv("ETCD_PORT", "2379"))

STREAM_KEY = "jobs:qwen-image-2512"
GROUP_NAME = "qwen-workers"
WORKER_ID = os.getenv("WORKER_ID", "qwen-worker-1")

# Import handler
from handler import generate_image

# HTTP Request Handler for cleanup endpoint
class CleanupHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == '/cleanup':
            try:
                import torch
                import gc
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
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
        worker_info = f"app=qwen-image-2512,queue=jobs:qwen-image-2512,status=ONLINE"
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

        if job.app_id != "qwen-image-2512":
            error_msg = f"Worker for qwen-image-2512 received job for {job.app_id}"
            logger.error(error_msg)
            update_job_status(job.job_id, "FAILED", error=error_msg)
            return

        update_job_status(job.job_id, "PROCESSING")

        params = dict(job.params)

        try:
            result = generate_image(
                prompt=params.get("prompt", ""),
                negative_prompt=params.get("negative_prompt", ""),
                width=int(params.get("width", 1024)),
                height=int(params.get("height", 1024)),
                num_inference_steps=int(params.get("num_inference_steps", 30)),
                guidance_scale=float(params.get("guidance_scale", 7.5)),
                seed=int(params.get("seed", -1)) if params.get("seed") else -1
            )
            
            logger.info(f"[SUCCESS] Job {job.job_id} completed successfully")
            update_job_status(job.job_id, "COMPLETED", output={"image_base64": result})
        except Exception as e:
            logger.error(f"[ERROR] Job {job.job_id} failed: {str(e)}")
            update_job_status(job.job_id, "FAILED", error=str(e))

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
    logger.info(f"[STARTUP] Starting Qwen Image 2512 Worker")
    logger.info(f"Worker ID: {WORKER_ID}")
    logger.info(f"Queue: {STREAM_KEY}")
    logger.info(f"App ID: qwen-image-2512")

    # Start GPU metrics collector
    metrics_collector = GPUMetricsCollector(worker_id=WORKER_ID, interval_seconds=5)
    metrics_collector.start()
    logger.info("[SUCCESS] GPU metrics collector started")

    # Start HTTP server in background thread
    http_thread = threading.Thread(target=start_http_server, daemon=True)
    http_thread.start()

    # Connect to Redis
    try:
        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=False)
        r.ping()
        logger.info("[SUCCESS] Connected to Redis")
    except Exception as e:
        logger.error(f"[ERROR] Failed to connect to Redis: {e}")
        sys.exit(1)

    # Create consumer group
    try:
        r.xgroup_create(STREAM_KEY, GROUP_NAME, id="0", mkstream=True)
        logger.info(f"[SUCCESS] Created consumer group: {GROUP_NAME}")
    except redis.exceptions.ResponseError as e:
        if "BUSYGROUP" in str(e):
            logger.info(f"Consumer group {GROUP_NAME} already exists")
        else:
            logger.error(f"Error creating consumer group: {e}")

    # Register in etcd
    lease = register_worker_etcd()

    # Main loop
    logger.info(f"[LISTENING] Listening for jobs on '{STREAM_KEY}'...")
    last_heartbeat = time.time()

    # Check for pending messages
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
                        process_job(fields[b'payload'], r)
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
                        process_job(fields[b'payload'], r)
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
