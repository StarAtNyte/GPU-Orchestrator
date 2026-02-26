"""
Qwen3.5-35B-A3B Chat Worker
Follows the same pattern as the other GPU workers (Redis Streams + etcd + PostgreSQL).
"""

import json
import logging
import os
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

from handler import Qwen35ChatHandler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# --- env ---
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

handler: Qwen35ChatHandler = None


# ------------------------------------------------------------------
# Cleanup HTTP endpoint (orchestrator calls this to free GPU on switch)
# ------------------------------------------------------------------

class CleanupHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/cleanup":
            try:
                handler.offload_model()
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success"}).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def start_http_server():
    port = int(os.getenv("CLEANUP_PORT", "8000"))
    server = HTTPServer(("0.0.0.0", port), CleanupHandler)
    logger.info(f"[HTTP] Cleanup endpoint on port {port}")
    server.serve_forever()


# ------------------------------------------------------------------
# PostgreSQL helpers
# ------------------------------------------------------------------

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
    except Exception as e:
        logger.error(f"DB update error: {e}")


# ------------------------------------------------------------------
# etcd registration + heartbeat
# ------------------------------------------------------------------

def register_etcd():
    try:
        etcd = etcd_client(host=ETCD_HOST, port=ETCD_PORT)
        key = f"/workers/{WORKER_ID}"
        lease = etcd.lease(10)
        etcd.put(key, "app=qwen35-chat,queue=jobs:qwen35-chat,status=ONLINE", lease=lease)
        logger.info(f"Registered in etcd: {WORKER_ID}")
        return lease
    except Exception as e:
        logger.error(f"etcd registration failed: {e}")
        return None


def start_heartbeat(lease):
    def loop():
        while True:
            try:
                if lease:
                    lease.refresh()
            except Exception as e:
                logger.error(f"etcd heartbeat error: {e}")
            time.sleep(3)

    t = threading.Thread(target=loop, daemon=True)
    t.start()


# ------------------------------------------------------------------
# Job processing
# ------------------------------------------------------------------

def mark_active(r):
    from datetime import datetime
    try:
        r.setex(f"worker:{WORKER_ID}:last_active", 120, datetime.utcnow().isoformat())
    except Exception:
        pass


def process_job(payload, r):
    job = worker_pb2.JobRequest()
    job.ParseFromString(payload)

    logger.info(f"[PROCESSING] Job {job.job_id}")

    if job.app_id != "qwen35-chat":
        update_job_status(job.job_id, "FAILED", error=f"Wrong app: {job.app_id}")
        return

    mark_active(r)
    update_job_status(job.job_id, "PROCESSING")

    try:
        result = handler.process(job.job_id, dict(job.params))
        if result.get("success"):
            update_job_status(job.job_id, "COMPLETED", output=result.get("output"))
            logger.info(f"[SUCCESS] Job {job.job_id}")
        else:
            update_job_status(job.job_id, "FAILED", error=result.get("error"))
            logger.error(f"[FAILED] Job {job.job_id}: {result.get('error')}")
    except Exception as e:
        logger.error(f"[ERROR] Job {job.job_id}: {e}", exc_info=True)
        update_job_status(job.job_id, "FAILED", error=str(e))


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def main():
    global handler

    logger.info(f"Starting Qwen3.5-35B-A3B Chat Worker | ID={WORKER_ID}")

    GPUMetricsCollector(worker_id=WORKER_ID, interval_seconds=5).start()

    threading.Thread(target=start_http_server, daemon=True).start()

    r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=False)
    r.ping()
    logger.info("Connected to Redis")

    handler = Qwen35ChatHandler(redis_client=r, worker_id=WORKER_ID)

    # Create consumer group
    try:
        r.xgroup_create(STREAM_KEY, GROUP_NAME, id="0", mkstream=True)
    except redis.exceptions.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise

    lease = register_etcd()
    if lease:
        start_heartbeat(lease)

    # Reclaim pending messages from previous crash
    try:
        pending = r.xpending_range(STREAM_KEY, GROUP_NAME, "-", "+", 10)
        for msg in pending:
            claimed = r.xclaim(STREAM_KEY, GROUP_NAME, WORKER_ID, 0, [msg["message_id"]])
            for cid, fields in claimed:
                try:
                    process_job(fields[b"payload"], r)
                finally:
                    r.xack(STREAM_KEY, GROUP_NAME, cid)
                    r.xdel(STREAM_KEY, cid)
    except Exception as e:
        logger.warning(f"Pending message recovery: {e}")

    logger.info(f"Listening on '{STREAM_KEY}'...")

    while True:
        try:
            entries = r.xreadgroup(GROUP_NAME, WORKER_ID, {STREAM_KEY: ">"}, count=1, block=2000)
            if entries:
                for _, messages in entries:
                    for msg_id, fields in messages:
                        try:
                            process_job(fields[b"payload"], r)
                        finally:
                            r.xack(STREAM_KEY, GROUP_NAME, msg_id)
                            r.xdel(STREAM_KEY, msg_id)
        except KeyboardInterrupt:
            logger.info("Shutting down")
            break
        except Exception as e:
            logger.error(f"Main loop error: {e}", exc_info=True)
            time.sleep(1)


if __name__ == "__main__":
    main()
