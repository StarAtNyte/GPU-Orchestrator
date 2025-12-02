import torch
from diffusers import FlowMatchEulerDiscreteScheduler, ZImagePipeline
import logging
import base64
from io import BytesIO
from typing import Dict, Any
import re
import os
import gc

# Set CUDA memory allocation configuration
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

logger = logging.getLogger(__name__)

class ZImageHandler:
    def __init__(self):
        self.pipe = None
        self.device = "cuda"

    def load_model(self):
        """Load Z-Image model once, cache for future requests."""
        if self.pipe is not None:
            return

        logger.info("Loading Z-Image Turbo model...")

        self.pipe = ZImagePipeline.from_pretrained(
            "Tongyi-MAI/Z-Image-Turbo",
            torch_dtype=torch.bfloat16,
            low_cpu_mem_usage=False,
        )

        # Enable model CPU offloading to reduce VRAM usage
        # This will move model components to CPU when not in use
        self.pipe.enable_model_cpu_offload()

        # Enable VAE tiling to reduce memory usage during decoding
        self.pipe.vae.enable_tiling()

        # Enable VAE slicing for even lower memory usage
        self.pipe.vae.enable_slicing()

        logger.info("Z-Image loaded successfully")

    def get_resolution(self, resolution_str):
        """Parse resolution string like '1024x1024' or '576x1024'."""
        match = re.search(r"(\d+)\s*[×x]\s*(\d+)", resolution_str)
        if match:
            return int(match.group(1)), int(match.group(2))
        return 1024, 1024

    def process(self, job_id: str, params: Dict[str, str]) -> Dict[str, Any]:
        """Process a single image generation job."""
        try:
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
            scheduler = FlowMatchEulerDiscreteScheduler(
                num_train_timesteps=1000,
                shift=shift
            )
            self.pipe.scheduler = scheduler

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
            return {
                "success": False,
                "error": str(e)
            }