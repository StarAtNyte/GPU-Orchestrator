"""
GPU Metrics Collector
Collects GPU metrics (utilization, VRAM, temperature, power) and stores in PostgreSQL.
"""
import os
import time
import threading
import logging
from typing import List, Dict, Optional

try:
    import pynvml
    PYNVML_AVAILABLE = True
except ImportError:
    PYNVML_AVAILABLE = False
    print("[WARNING] pynvml not available - GPU metrics collection disabled")

import psycopg2
from psycopg2.extras import execute_batch

logger = logging.getLogger(__name__)


class GPUMetricsCollector:
    """Collects and stores GPU metrics in TimescaleDB."""

    def __init__(self, worker_id: str, interval_seconds: int = 5):
        """
        Initialize GPU metrics collector.

        Args:
            worker_id: Unique identifier for this worker
            interval_seconds: Collection interval (default: 5 seconds)
        """
        self.worker_id = worker_id
        self.interval = interval_seconds
        self.running = False
        self.thread: Optional[threading.Thread] = None

        if not PYNVML_AVAILABLE:
            logger.warning("pynvml not available - GPU metrics collection disabled")
            return

        try:
            pynvml.nvmlInit()
            self.device_count = pynvml.nvmlDeviceGetCount()
            logger.info(f"GPU metrics collector initialized for {self.device_count} GPU(s)")
        except Exception as e:
            logger.error(f"Failed to initialize NVML: {e}")
            self.device_count = 0

    def get_postgres_connection(self):
        """Get PostgreSQL database connection."""
        return psycopg2.connect(
            host=os.getenv("POSTGRES_HOST", "localhost"),
            database=os.getenv("POSTGRES_DB", "gpu_orchestrator"),
            user=os.getenv("POSTGRES_USER", "admin"),
            password=os.getenv("POSTGRES_PASSWORD", "admin123")
        )

    def collect_metrics(self) -> List[Dict]:
        """
        Collect current GPU metrics.

        Returns:
            List of metric dictionaries, one per GPU
        """
        if not PYNVML_AVAILABLE or self.device_count == 0:
            return []

        metrics = []
        try:
            for gpu_id in range(self.device_count):
                handle = pynvml.nvmlDeviceGetHandleByIndex(gpu_id)

                # Get utilization rates
                try:
                    utilization = pynvml.nvmlDeviceGetUtilizationRates(handle)
                    gpu_util = float(utilization.gpu)
                except Exception as e:
                    logger.debug(f"Failed to get utilization for GPU {gpu_id}: {e}")
                    gpu_util = None

                # Get memory info
                try:
                    memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
                    vram_used = memory.used // (1024 * 1024)  # Convert to MB
                    vram_total = memory.total // (1024 * 1024)
                except Exception as e:
                    logger.debug(f"Failed to get memory info for GPU {gpu_id}: {e}")
                    vram_used = None
                    vram_total = None

                # Get temperature
                try:
                    temperature = pynvml.nvmlDeviceGetTemperature(
                        handle,
                        pynvml.NVML_TEMPERATURE_GPU
                    )
                except Exception as e:
                    logger.debug(f"Failed to get temperature for GPU {gpu_id}: {e}")
                    temperature = None

                # Get power draw
                try:
                    power_mw = pynvml.nvmlDeviceGetPowerUsage(handle)
                    power_w = int(power_mw / 1000)  # Convert mW to W
                except Exception as e:
                    logger.debug(f"Failed to get power draw for GPU {gpu_id}: {e}")
                    power_w = None

                metrics.append({
                    'gpu_id': gpu_id,
                    'gpu_utilization': gpu_util,
                    'vram_used_mb': vram_used,
                    'vram_total_mb': vram_total,
                    'temperature_c': temperature,
                    'power_draw_w': power_w
                })

        except Exception as e:
            logger.error(f"Error collecting GPU metrics: {e}")

        return metrics

    def write_metrics(self, metrics: List[Dict]):
        """
        Write metrics to PostgreSQL database.

        Args:
            metrics: List of metric dictionaries
        """
        if not metrics:
            return

        try:
            conn = self.get_postgres_connection()
            cursor = conn.cursor()

            # Prepare batch insert
            values = []
            for metric in metrics:
                values.append((
                    self.worker_id,
                    metric['gpu_id'],
                    metric['gpu_utilization'],
                    metric['vram_used_mb'],
                    metric['vram_total_mb'],
                    metric['temperature_c'],
                    metric['power_draw_w']
                ))

            # Batch insert
            execute_batch(cursor, """
                INSERT INTO gpu_metrics
                (time, worker_id, gpu_id, gpu_utilization, vram_used_mb,
                 vram_total_mb, temperature_c, power_draw_w)
                VALUES (NOW(), %s, %s, %s, %s, %s, %s, %s)
            """, values)

            conn.commit()
            cursor.close()
            conn.close()

            logger.debug(f"Wrote {len(metrics)} GPU metric(s) to database")

        except Exception as e:
            logger.error(f"Failed to write GPU metrics to database: {e}")

    def collection_loop(self):
        """Main collection loop (runs in background thread)."""
        logger.info(f"GPU metrics collection started (interval: {self.interval}s)")

        while self.running:
            try:
                metrics = self.collect_metrics()
                if metrics:
                    self.write_metrics(metrics)
            except Exception as e:
                logger.error(f"Error in metrics collection loop: {e}")

            time.sleep(self.interval)

        logger.info("GPU metrics collection stopped")

    def start(self):
        """Start background metrics collection."""
        if not PYNVML_AVAILABLE or self.device_count == 0:
            logger.warning("GPU metrics collection not started (NVML not available or no GPUs)")
            return

        if self.running:
            logger.warning("GPU metrics collection already running")
            return

        self.running = True
        self.thread = threading.Thread(target=self.collection_loop, daemon=True)
        self.thread.start()
        logger.info("GPU metrics collection thread started")

    def stop(self):
        """Stop background metrics collection."""
        if not self.running:
            return

        logger.info("Stopping GPU metrics collection...")
        self.running = False

        if self.thread:
            self.thread.join(timeout=10)

    def __del__(self):
        """Cleanup on deletion."""
        self.stop()
        if PYNVML_AVAILABLE:
            try:
                pynvml.nvmlShutdown()
            except:
                pass
