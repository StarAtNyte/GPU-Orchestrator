"""
Zz Iimage Worker
Isolated worker for z-image jobs
"""

import os
import sys
import time
import logging
import redis
import psycopg2
from etcd3 import client as etcd_client

# Setup paths
sys.path.append('/app')
from shared import worker_pb2

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

STREAM_KEY = "jobs:z-image"
GROUP_NAME = "z-image-workers"
WORKER_ID = os.getenv("WORKER_ID", "z-image-worker-1")

# Import handler
from handler import ZImageHandler

# Global handler instance
handler = ZImageHandler()


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

        # Create lease with 10 second TTL
        lease = etcd.lease(10)

        # Put worker info with lease
        worker_info = f"app=z-image,queue=jobs:z-image,status=ONLINE"
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


def process_job(payload):
    """Process a single job."""
    try:
        # Parse protobuf
        job = worker_pb2.JobRequest()
        job.ParseFromString(payload)

        logger.info(f"[PROCESSING] Processing job {job.job_id} for app {job.app_id}")

        # Validate app_id
        if job.app_id != "z-image":
            error_msg = f"Worker for z-image received job for {job.app_id}"
            logger.error(error_msg)
            update_job_status(job.job_id, "FAILED", error=error_msg)
            return

        # Update status to PROCESSING
        update_job_status(job.job_id, "PROCESSING")

        # Convert params to dict
        params = dict(job.params)

        # Process with handler
        result = handler.process(job.job_id, params)

        # Update based on result
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


def main():
    """Main worker loop."""
    logger.info(f"[STARTUP] Starting Zz Iimage Worker")
    logger.info(f"Worker ID: {WORKER_ID}")
    logger.info(f"Queue: {STREAM_KEY}")
    logger.info(f"App ID: z-image")

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

    while True:
        try:
            # Keep etcd lease alive (every 5 seconds)
            if time.time() - last_heartbeat > 5:
                keep_alive_etcd(lease)
                last_heartbeat = time.time()

            # Read from stream
            entries = r.xreadgroup(
                GROUP_NAME,
                WORKER_ID,
                {STREAM_KEY: ">"},
                count=1,
                block=2000  # 2 second timeout
            )

            if entries:
                for stream, messages in entries:
                    for message_id, fields in messages:
                        # Process job
                        process_job(fields[b'payload'])

                        # Acknowledge message
                        r.xack(STREAM_KEY, GROUP_NAME, message_id)

        except KeyboardInterrupt:
            logger.info("Shutting down worker...")
            break
        except Exception as e:
            logger.error(f"Error in main loop: {e}", exc_info=True)
            time.sleep(1)


if __name__ == "__main__":
    main()
