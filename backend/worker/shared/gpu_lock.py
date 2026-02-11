"""
GPU Memory Lock - Distributed lock for coordinating GPU memory allocation across workers.

This prevents multiple workers from loading large models simultaneously and causing OOM errors.
Uses Redis for distributed locking and nvidia-smi to check actual GPU memory availability.
"""

import redis
import subprocess
import time
import logging
from typing import Optional, Tuple
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class GPUMemoryLock:
    """Distributed lock for GPU memory allocation coordination."""

    def __init__(self, redis_client: redis.Redis, worker_id: str):
        """
        Initialize GPU memory lock.

        Args:
            redis_client: Redis client instance
            worker_id: Unique identifier for this worker
        """
        self.redis = redis_client
        self.worker_id = worker_id
        self.lock_key = "gpu:model_loading_lock"
        self.lock_timeout = 300  # 5 minutes - max time to hold lock

    def get_gpu_memory_info(self) -> Tuple[int, int]:
        """
        Get GPU memory information using nvidia-smi.

        Returns:
            Tuple of (used_memory_mb, total_memory_mb)
        """
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                # Use first GPU
                used, total = lines[0].split(',')
                return int(float(used)), int(float(total))
            else:
                logger.warning("nvidia-smi failed, assuming GPU available")
                return 0, 24000  # Assume 24GB GPU if check fails

        except Exception as e:
            logger.warning(f"Failed to check GPU memory: {e}")
            return 0, 24000

    def is_memory_available(self, required_mb: int = 12000) -> bool:
        """
        Check if sufficient GPU memory is available.

        Args:
            required_mb: Required memory in MB (default 12GB for Qwen models)

        Returns:
            True if sufficient memory is available
        """
        used_mb, total_mb = self.get_gpu_memory_info()
        available_mb = total_mb - used_mb

        logger.info(f"GPU Memory: {used_mb}MB used / {total_mb}MB total ({available_mb}MB available)")
        logger.info(f"Required: {required_mb}MB")

        return available_mb >= required_mb

    def acquire(self, timeout: int = 120, required_memory_mb: int = 12000) -> bool:
        """
        Acquire the GPU memory lock.

        Blocks until lock is acquired or timeout is reached.

        Args:
            timeout: Max time to wait for lock (seconds)
            required_memory_mb: Required GPU memory in MB

        Returns:
            True if lock acquired, False if timeout
        """
        start_time = time.time()
        attempt = 0

        while time.time() - start_time < timeout:
            attempt += 1

            # Check if lock is held by same worker (from crashed instance)
            current_holder = self.redis.get(self.lock_key)
            if current_holder:
                holder_id = current_holder.decode('utf-8') if isinstance(current_holder, bytes) else current_holder
                if holder_id == self.worker_id:
                    logger.warning(f"[GPU_LOCK] Lock held by same worker (stale from crash) - force releasing")
                    self.redis.delete(self.lock_key)

            # Try to acquire lock
            acquired = self.redis.set(
                self.lock_key,
                self.worker_id,
                nx=True,  # Only set if not exists
                ex=self.lock_timeout  # Auto-expire after 5 minutes
            )

            if acquired:
                # Check if memory is actually available
                if self.is_memory_available(required_memory_mb):
                    logger.info(f"[GPU_LOCK] Lock acquired by {self.worker_id} (attempt {attempt})")
                    return True
                else:
                    # Release lock if memory not available
                    logger.warning(f"[GPU_LOCK] Memory not available, releasing lock")
                    self.release()
                    time.sleep(5)
                    continue

            # Lock held by another worker
            holder = self.redis.get(self.lock_key)
            if holder:
                holder_id = holder.decode('utf-8') if isinstance(holder, bytes) else holder
                if attempt == 1 or attempt % 6 == 0:  # Log every 30 seconds
                    logger.info(f"[GPU_LOCK] Waiting for lock (held by {holder_id})...")

            time.sleep(5)  # Wait before retry

        logger.warning(f"[GPU_LOCK] Timeout waiting for lock after {timeout}s")
        return False

    def release(self):
        """Release the GPU memory lock."""
        current_holder = self.redis.get(self.lock_key)

        if current_holder:
            holder_id = current_holder.decode('utf-8') if isinstance(current_holder, bytes) else current_holder

            # Only release if we own the lock
            if holder_id == self.worker_id:
                self.redis.delete(self.lock_key)
                logger.info(f"[GPU_LOCK] Lock released by {self.worker_id}")
            else:
                logger.warning(f"[GPU_LOCK] Cannot release lock owned by {holder_id}")
        else:
            logger.debug(f"[GPU_LOCK] No lock to release")

    @contextmanager
    def locked(self, timeout: int = 120, required_memory_mb: int = 12000):
        """
        Context manager for acquiring and releasing lock.

        Usage:
            with gpu_lock.locked(required_memory_mb=12000):
                # Load model
                pass

        Args:
            timeout: Max time to wait for lock
            required_memory_mb: Required GPU memory in MB
        """
        acquired = self.acquire(timeout=timeout, required_memory_mb=required_memory_mb)

        if not acquired:
            raise TimeoutError(f"Failed to acquire GPU lock after {timeout}s")

        try:
            yield
        finally:
            self.release()
