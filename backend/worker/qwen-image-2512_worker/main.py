"""
Qwen Image 2512 Fast - GPU Worker
Processes image generation jobs from Redis queue
"""

import os
import sys
import json
import logging
from pathlib import Path

# Add shared utilities to path
sys.path.insert(0, str(Path(__file__).parent.parent / "shared"))

from redis_client import RedisClient
from db import Database
from etcd_client import EtcdClient
from handler import generate_image

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(name)s] %(message)s'
)
logger = logging.getLogger("qwen-worker")

# Configuration
WORKER_ID = os.getenv("WORKER_ID", "qwen-worker")
APP_ID = os.getenv("APP_ID", "qwen-image-2512")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
QUEUE_NAME = f"jobs:{APP_ID}"

logger.info(f"Starting {WORKER_ID} for {APP_ID}")
logger.info(f"Queue: {QUEUE_NAME}")

# Initialize clients
redis_client = RedisClient(REDIS_URL)
db = Database()
etcd = EtcdClient()

def process_job(job_data):
    """Process a single job from the queue."""
    job_id = job_data.get("job_id")
    params = job_data.get("params", {})
    
    logger.info(f"[{job_id}] Processing job")
    
    try:
        # Update status to PROCESSING
        db.update_job_status(job_id, "PROCESSING", WORKER_ID)
        
        # Mark worker as active
        redis_client.mark_active(WORKER_ID)
        
        # Generate image
        logger.info(f"[{job_id}] Generating image with Qwen-2512...")
        result = generate_image(
            prompt=params.get("prompt", ""),
            negative_prompt=params.get("negative_prompt", ""),
            width=int(params.get("width", 1024)),
            height=int(params.get("height", 1024)),
            num_inference_steps=int(params.get("num_inference_steps", 30)),
            guidance_scale=float(params.get("guidance_scale", 7.5)),
            seed=int(params.get("seed", -1)) if params.get("seed") else -1
        )
        
        # Save result
        logger.info(f"[{job_id}] Saving result to database")
        db.update_job_result(job_id, result)
        db.update_job_status(job_id, "COMPLETED")
        
        logger.info(f"[{job_id}] Job completed successfully")
        
    except Exception as e:
        logger.error(f"[{job_id}] Job failed: {str(e)}", exc_info=True)
        db.update_job_status(job_id, "FAILED", error_log=str(e))

def main():
    """Main worker loop."""
    logger.info("Registering worker with etcd...")
    etcd.register_worker(WORKER_ID, APP_ID)
    
    logger.info(f"Starting job consumer for {QUEUE_NAME}...")
    consumer_group = f"{APP_ID}-workers"
    
    try:
        # Create consumer group if it doesn't exist
        try:
            redis_client.create_consumer_group(QUEUE_NAME, consumer_group)
        except:
            pass  # Group already exists
        
        while True:
            try:
                # Read pending messages
                messages = redis_client.read_messages(QUEUE_NAME, consumer_group, WORKER_ID)
                
                if not messages:
                    continue
                
                for message_id, message_data in messages:
                    try:
                        # Parse job data
                        job_data = json.loads(message_data.get(b"payload", b"{}").decode())
                        
                        # Process job
                        process_job(job_data)
                        
                        # Acknowledge message
                        redis_client.ack_message(QUEUE_NAME, consumer_group, message_id)
                        
                    except Exception as e:
                        logger.error(f"Error processing message {message_id}: {str(e)}")
                        redis_client.ack_message(QUEUE_NAME, consumer_group, message_id)
                        
            except KeyboardInterrupt:
                logger.info("Shutting down...")
                break
            except Exception as e:
                logger.error(f"Consumer error: {str(e)}", exc_info=True)
                
    finally:
        logger.info("Unregistering worker...")
        etcd.unregister_worker(WORKER_ID)
        logger.info("Worker stopped")

if __name__ == "__main__":
    main()
