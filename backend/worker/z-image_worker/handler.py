import torch
from diffusers import FlowMatchEulerDiscreteScheduler, ZImagePipeline
import logging
import base64
from io import BytesIO
from typing import Dict, Any
import re
import os
import gc
import sys
import time
import threading

sys.path.append('/app')
from shared.gpu_lock import GPUMemoryLock

# Set CUDA memory allocation configuration
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

logger = logging.getLogger(__name__)

class ZImageHandler:
    def __init__(self, redis_client=None, worker_id="z-image-worker", state_callback=None):
        self._state_cb = state_callback
        self.pipe = None
        self.device = "cuda"
        self.last_used = None
        self.cleanup_timer = None
        self.cleanup_delay = 300  # 5 minutes in seconds
        self.gpu_lock = GPUMemoryLock(redis_client, worker_id) if redis_client else None

    def _publish_state(self, state: str) -> None:
        """Fire the state callback if one was provided (safe to call from any thread)."""
        if self._state_cb is not None:
            try:
                self._state_cb(state)
            except Exception as exc:
                logger.warning(f"[STATE_CB] Failed to publish state={state}: {exc}")

    def load_model(self):
        """Load Z-Image model, keeping it cached for reuse."""
        if self.pipe is not None:
            logger.info("Z-Image model already loaded, reusing...")
            # Verify model is still on GPU
            try:
                if not next(self.pipe.transformer.parameters()).is_cuda:
                    logger.warning("Model was on CPU, moving back to GPU...")
                    self.pipe = self.pipe.to(self.device)
            except Exception as e:
                logger.warning(f"Could not verify device, reloading model: {e}")
                self.pipe = None
                return self.load_model()
            return

        if self.gpu_lock:
            logger.info("[GPU_LOCK] Acquiring lock before loading model...")
            try:
                with self.gpu_lock.locked(timeout=1800, required_memory_mb=16000):
                    self._load_model_internal()
            except TimeoutError as e:
                logger.error(f"[GPU_LOCK] Failed to acquire lock: {e}")
                raise
            return

        self._load_model_internal()

    def _load_model_internal(self):
        logger.info("Loading Z-Image Turbo model to GPU...")

        self.pipe = ZImagePipeline.from_pretrained(
            "Tongyi-MAI/Z-Image-Turbo",
            torch_dtype=torch.bfloat16,
            low_cpu_mem_usage=True,
        )

        # Move to GPU
        self.pipe = self.pipe.to(self.device)

        # Enable VAE tiling to reduce memory usage during decoding
        self.pipe.vae.enable_tiling()

        # Enable VAE slicing for even lower memory usage
        self.pipe.vae.enable_slicing()

        logger.info("Z-Image loaded successfully")

    def offload_model(self):
        """Completely remove model from GPU to free memory."""
        if self.pipe is None:
            logger.info("No model loaded, nothing to offload")
            return

        logger.info("Removing Z-Image model from GPU...")

        # Cancel any pending cleanup timer
        if self.cleanup_timer:
            self.cleanup_timer.cancel()
            self.cleanup_timer = None

        # Delete the pipeline
        del self.pipe
        self.pipe = None
        self.last_used = None

        # Aggressively free GPU memory
        torch.cuda.empty_cache()
        torch.cuda.synchronize()
        gc.collect()

        logger.info("Z-Image model removed from GPU - memory freed")
        self._publish_state("IDLE")

    def _schedule_cleanup(self):
        """Schedule model cleanup after delay."""
        # Cancel existing timer if any
        if self.cleanup_timer:
            self.cleanup_timer.cancel()

        # Schedule new cleanup
        self.cleanup_timer = threading.Timer(self.cleanup_delay, self.offload_model)
        self.cleanup_timer.daemon = True
        self.cleanup_timer.start()
        logger.info(f"Scheduled GPU cleanup in {self.cleanup_delay} seconds")
        self._publish_state("WARM")

    def cancel_cleanup(self):
        """Cancel scheduled cleanup (called when new job arrives)."""
        if self.cleanup_timer:
            self.cleanup_timer.cancel()
            self.cleanup_timer = None
            logger.info("Cancelled scheduled GPU cleanup")

    def get_resolution(self, resolution_str):
        """Parse resolution string like '1024x1024' or '576x1024'."""
        match = re.search(r"(\d+)\s*[×x]\s*(\d+)", resolution_str)
        if match:
            return int(match.group(1)), int(match.group(2))
        return 1024, 1024

    def process(self, job_id: str, params: Dict[str, str]) -> Dict[str, Any]:
        """Process a single image generation job."""
        try:
            # Cancel any scheduled cleanup since we have a new job
            self.cancel_cleanup()
            self._publish_state("PROCESSING")

            self.load_model()

            # Extract parameters
            prompt = params.get("prompt", "")
            resolution = params.get("resolution", "1024x1024")
            seed = int(params.get("seed", "42"))
            steps = int(params.get("steps", "9"))
            shift = float(params.get("shift", "3.0"))

            width, height = self.get_resolution(resolution)

            logger.info(f"RECEIVED PARAMS: steps={steps}, shift={shift}, seed={seed}")
            logger.info(f"Generating Z-Image: {prompt[:50]}... ({width}x{height})")

            # Clear cache before generation
            torch.cuda.empty_cache()

            # Setup generator and scheduler
            generator = torch.Generator(self.device).manual_seed(seed)

            # Create scheduler with shift parameter
            # Note: Scheduler doesn't have device-specific tensors, but we ensure
            # the pipeline is on the correct device after assignment
            scheduler = FlowMatchEulerDiscreteScheduler(
                num_train_timesteps=1000,
                shift=shift
            )
            self.pipe.scheduler = scheduler

            # Ensure pipeline is still on GPU after scheduler assignment
            if not next(self.pipe.transformer.parameters()).is_cuda:
                logger.warning("Pipeline moved to CPU, moving back to GPU...")
                self.pipe = self.pipe.to(self.device)

            # Generate image
            try:
                image = self.pipe(
                    prompt=prompt,
                    height=height,
                    width=width,
                    guidance_scale=0.0,  # Z-Image Turbo doesn't use guidance
                    num_inference_steps=steps,
                    generator=generator,
                    max_sequence_length=512,
                ).images[0]
            finally:
                # Aggressively free CUDA memory after generation
                del generator
                torch.cuda.empty_cache()
                torch.cuda.synchronize()
                # Force garbage collection
                gc.collect()

            # Convert to base64
            buffered = BytesIO()
            image.save(buffered, format="PNG")
            image_b64 = base64.b64encode(buffered.getvalue()).decode()

            # Cleanup - clear CUDA cache after conversion
            del image
            del buffered
            torch.cuda.empty_cache()
            gc.collect()

            # Update last used time
            self.last_used = time.time()

            # Schedule cleanup after 5 minutes of inactivity
            self._schedule_cleanup()

            return {
                "success": True,
                "output": {
                    "image_base64": image_b64,
                    "width": width,
                    "height": height,
                    "seed": seed,
                    "steps": steps
                }
            }

        except Exception as e:
            logger.error(f"Error generating image: {e}", exc_info=True)
            # Still schedule cleanup on error
            self._schedule_cleanup()
            return {
                "success": False,
                "error": str(e)
            }