import torch
from diffusers import StableDiffusionXLPipeline
import logging
import base64
from io import BytesIO
from typing import Dict, Any
import os
import gc
import time
import threading

logger = logging.getLogger(__name__)

class SDXLHandler:
    def __init__(self):
        self.pipe = None
        self.device = "cuda"
        self.last_used = None
        self.cleanup_timer = None
        self.cleanup_delay = 300  # 5 minutes in seconds

    def load_model(self):
        """Load SDXL model, keeping it cached for reuse."""
        if self.pipe is not None:
            logger.info("SDXL model already loaded, reusing...")
            return

        logger.info("Loading SDXL model to GPU...")

        self.pipe = StableDiffusionXLPipeline.from_pretrained(
            "stabilityai/stable-diffusion-xl-base-1.0",
            torch_dtype=torch.float16,
            use_safetensors=True,
            variant="fp16"
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

        logger.info("SDXL model removed from GPU - memory freed")

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

    def cancel_cleanup(self):
        """Cancel scheduled cleanup (called when new job arrives)."""
        if self.cleanup_timer:
            self.cleanup_timer.cancel()
            self.cleanup_timer = None
            logger.info("Cancelled scheduled GPU cleanup")

    def process(self, job_id: str, params: Dict[str, str]) -> Dict[str, Any]:
        """Process a single image generation job."""
        try:
            # Cancel any scheduled cleanup since we have a new job
            self.cancel_cleanup()

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
            logger.info(f"Steps: {num_inference_steps}, Guidance: {guidance_scale}, Seed: {seed}")

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

            # Schedule cleanup after 5 minutes of inactivity
            self._schedule_cleanup()

            return {
                "success": True,
                "output": {
                    "image_base64": image_b64,
                    "width": width,
                    "height": height,
                    "seed": seed,
                    "steps": num_inference_steps,
                    "guidance_scale": guidance_scale
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
