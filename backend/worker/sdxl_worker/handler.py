import base64
import gc
import logging
import os
import sys
import threading
import time
from io import BytesIO
from typing import Any, Dict

import torch
from diffusers import StableDiffusionXLPipeline

sys.path.append('/app')
from shared.gpu_lock import GPUMemoryLock

logger = logging.getLogger(__name__)


class SDXLHandler:
    def __init__(self, redis_client=None, worker_id="sdxl-worker", state_callback=None):
        self.pipe = None
        self.device = "cuda"
        self.last_used = None
        self.cleanup_timer = None
        self.cleanup_delay = 300  # 5 minutes in seconds
        self._state_cb = state_callback
        self.gpu_lock = GPUMemoryLock(redis_client, worker_id) if redis_client else None

    def _publish_state(self, state: str) -> None:
        """Fire the state callback if one was provided (safe to call from any thread)."""
        if self._state_cb is not None:
            try:
                self._state_cb(state)
            except Exception as exc:
                logger.warning(f"[STATE_CB] Failed to publish state={state}: {exc}")

    def load_model(self):
        """Load SDXL model, keeping it cached for reuse."""
        if self.pipe is not None:
            logger.info("SDXL model already loaded, reusing...")
            # Verify model is still on GPU
            try:
                if not next(self.pipe.unet.parameters()).is_cuda:
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
                with self.gpu_lock.locked(timeout=1800, required_memory_mb=12000):
                    self._load_model_internal()
            except TimeoutError as e:
                logger.error(f"[GPU_LOCK] Failed to acquire lock: {e}")
                raise
            return

        self._load_model_internal()

    def _load_model_internal(self):
        logger.info("Loading SDXL model to GPU...")

        self.pipe = StableDiffusionXLPipeline.from_pretrained(
            "stabilityai/stable-diffusion-xl-base-1.0",
            torch_dtype=torch.float16,
            use_safetensors=True,
            variant="fp16",
        )

        # Move to GPU
        self.pipe = self.pipe.to(self.device)

        # Enable memory optimizations
        self.pipe.enable_vae_slicing()
        self.pipe.enable_vae_tiling()

        logger.info("SDXL loaded successfully")

    def offload_model(self):
        """Completely remove model from GPU to free memory."""
        if self.pipe is None:
            logger.info("No model loaded, nothing to offload")
            return

        logger.info("Removing SDXL model from GPU...")

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

        logger.info("SDXL model removed from GPU — memory freed")
        # Publish IDLE outside the lock so the callback can itself acquire locks
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
        # Model is loaded and idle timer is running — the worker is WARM
        self._publish_state("WARM")

    def cancel_cleanup(self):
        """Cancel scheduled cleanup (called when new job arrives)."""
        if self.cleanup_timer:
            self.cleanup_timer.cancel()
            self.cleanup_timer = None
            logger.info("Cancelled scheduled GPU cleanup")

    def process(self, job_id: str, params: Dict[str, str]) -> Dict[str, Any]:
        """
        Process a single image generation job.

        State transitions inside this method:
          IDLE / WARM → load_model() (no-op if already loaded) → PROCESSING → WARM
        """
        try:
            # Cancel any scheduled cleanup since we have a new job
            self.cancel_cleanup()

            self._publish_state("PROCESSING")
            # Load model (will reuse if already loaded)
            self.load_model()

            # Extract parameters with defaults
            prompt = params.get("prompt", "")
            negative_prompt = params.get("negative_prompt", "")
            width = int(params.get("width", "1024"))
            height = int(params.get("height", "1024"))
            num_inference_steps = int(params.get("num_inference_steps", "50"))
            guidance_scale = float(params.get("guidance_scale", "7.5"))
            seed = int(params.get("seed", "42"))

            logger.info(f"Generating SDXL image: {prompt[:50]}... ({width}x{height})")
            logger.info(
                f"Steps: {num_inference_steps}, Guidance: {guidance_scale}, Seed: {seed}"
            )

            # Clear cache before generation
            torch.cuda.empty_cache()

            # Setup generator
            generator = torch.Generator(self.device).manual_seed(seed)

            # Generate image
            try:
                image = self.pipe(
                    prompt=prompt,
                    negative_prompt=negative_prompt if negative_prompt else None,
                    height=height,
                    width=width,
                    num_inference_steps=num_inference_steps,
                    guidance_scale=guidance_scale,
                    generator=generator,
                ).images[0]
            finally:
                # Free CUDA memory after generation
                del generator
                torch.cuda.empty_cache()
                torch.cuda.synchronize()
                gc.collect()

            # Convert to base64
            buffered = BytesIO()
            image.save(buffered, format="PNG")
            image_b64 = base64.b64encode(buffered.getvalue()).decode()

            # Cleanup
            del image
            del buffered
            torch.cuda.empty_cache()
            gc.collect()

            # Update last used time
            self.last_used = time.time()

            # Schedule cleanup after 5 minutes of inactivity.
            # _schedule_cleanup() calls _publish_state("WARM") internally.
            self._schedule_cleanup()

            return {
                "success": True,
                "output": {
                    "image_base64": image_b64,
                    "width": width,
                    "height": height,
                    "seed": seed,
                    "steps": num_inference_steps,
                    "guidance_scale": guidance_scale,
                },
            }

        except Exception as e:
            logger.error(f"Error generating image: {e}", exc_info=True)
            # Even on error the model is still loaded; reschedule cleanup.
            # _schedule_cleanup() calls _publish_state("WARM") internally.
            self._schedule_cleanup()
            return {"success": False, "error": str(e)}
