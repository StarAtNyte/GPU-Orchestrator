"""
SDXL Worker - Specialized worker for Stable Diffusion XL inference.

This worker:
- Consumes jobs from Redis Stream "jobs:sdxl"
- Loads SDXL model on GPU
- Generates images based on text prompts
- Saves results to disk
- Handles parameter casting and validation

Model: stabilityai/stable-diffusion-xl-base-1.0
VRAM Requirements: ~8GB for base model
"""

import os
import sys
import time
from pathlib import Path
from typing import Dict, Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.redis_client import RedisStreamClient
from shared import worker_pb2

# Try to import ML libraries (fail gracefully if not installed)
try:
    import torch
    from diffusers import StableDiffusionXLPipeline
    GPU_AVAILABLE = torch.cuda.is_available()
except ImportError as e:
    print(f"[WARNING] ML libraries not installed: {e}")
    print("   Install with: pip install torch torchvision diffusers transformers accelerate")
    GPU_AVAILABLE = False
    torch = None
    StableDiffusionXLPipeline = None


# === CONFIGURATION ===
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
STREAM_KEY = "jobs:sdxl"
GROUP_NAME = "sdxl-workers"
CONSUMER_NAME = os.getenv("WORKER_ID", "sdxl-worker-1")

# Output directory for generated images
OUTPUT_DIR = Path("/tmp/gpu-orchestrator/results")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Model configuration
MODEL_ID = "stabilityai/stable-diffusion-xl-base-1.0"
USE_GPU = GPU_AVAILABLE


class SDXLWorker:
    """
    Worker that processes SDXL image generation jobs.

    Features:
    - Lazy model loading (only loads when first job arrives)
    - GPU/CPU fallback
    - Parameter validation and type casting
    - Result persistence
    """

    def __init__(self):
        self.pipeline: Optional[StableDiffusionXLPipeline] = None
        self.model_loaded = False

    def load_model(self) -> None:
        """
        Load SDXL model into memory (GPU or CPU).

        This is called lazily on first job to avoid loading during startup.
        Model stays in memory for subsequent jobs (faster inference).

        Raises:
            RuntimeError: If GPU is required but not available
            ImportError: If required libraries are not installed
        """
        if self.model_loaded:
            return

        if not GPU_AVAILABLE:
            print("[WARNING] Running on CPU (will be VERY slow)")
            print("   For GPU support, ensure CUDA is installed and torch is GPU-enabled")

        print(f"[INFO] Loading model: {MODEL_ID}")
        print("   This may take 2-5 minutes on first run (downloads ~7GB)...")

        # Load model with appropriate dtype
        # float16: Faster inference, less VRAM (requires GPU)
        # float32: CPU compatible, more memory
        dtype = torch.float16 if USE_GPU else torch.float32

        self.pipeline = StableDiffusionXLPipeline.from_pretrained(
            MODEL_ID,
            torch_dtype=dtype,
            use_safetensors=True,  # Safer format, faster loading
            variant="fp16" if USE_GPU else None
        )

        # Move to GPU if available
        if USE_GPU:
            self.pipeline = self.pipeline.to("cuda")
            print(f"[SUCCESS] Model loaded on GPU (VRAM: {torch.cuda.memory_allocated(0) / 1e9:.2f} GB)")
        else:
            print("[SUCCESS] Model loaded on CPU")

        self.model_loaded = True

    def extract_params(self, params: Dict[str, str]) -> Dict:
        """
        Extract and validate parameters from job request.

        Casts string parameters to appropriate types and applies defaults.

        Args:
            params: Dictionary of string parameters from protobuf

        Returns:
            Dictionary with typed parameters for SDXL pipeline

        Raises:
            ValueError: If required parameters are missing or invalid
        """
        # Required parameter
        prompt = params.get("prompt", "").strip()
        if not prompt:
            raise ValueError("'prompt' parameter is required and cannot be empty")

        # Optional parameters with defaults
        try:
            width = int(params.get("width", "1024"))
            height = int(params.get("height", "1024"))
            num_inference_steps = int(params.get("num_inference_steps", "30"))
            guidance_scale = float(params.get("guidance_scale", "7.5"))
            num_images = int(params.get("num_images", "1"))
        except ValueError as e:
            raise ValueError(f"Invalid parameter type: {e}")

        # Validate dimensions (SDXL works best with multiples of 64)
        if width % 64 != 0 or height % 64 != 0:
            print(f"[WARNING] Dimensions {width}x{height} are not multiples of 64. May produce suboptimal results.")

        if width > 2048 or height > 2048:
            raise ValueError("Maximum resolution is 2048x2048 (VRAM constraint)")

        if num_inference_steps < 1 or num_inference_steps > 150:
            raise ValueError("num_inference_steps must be between 1 and 150")

        if num_images < 1 or num_images > 4:
            raise ValueError("num_images must be between 1 and 4")

        return {
            "prompt": prompt,
            "negative_prompt": params.get("negative_prompt", None),
            "width": width,
            "height": height,
            "num_inference_steps": num_inference_steps,
            "guidance_scale": guidance_scale,
            "num_images_per_prompt": num_images,
        }

    def generate_image(self, job_id: str, generation_params: Dict) -> str:
        """
        Run SDXL inference and save result.

        Args:
            job_id: Unique job identifier (used for filename)
            generation_params: Validated parameters for pipeline

        Returns:
            Path to saved image file

        Raises:
            RuntimeError: If generation fails
        """
        print(f"   [INFO] Generating: {generation_params['prompt'][:50]}...")
        print(f"   Size: {generation_params['width']}x{generation_params['height']}, Steps: {generation_params['num_inference_steps']}")

        start_time = time.time()

        # Generate image
        result = self.pipeline(**generation_params)
        images = result.images

        elapsed = time.time() - start_time
        print(f"   [TIMER] Generation took {elapsed:.2f}s")

        # Save first image (if multiple were generated, save only the first)
        output_path = OUTPUT_DIR / f"{job_id}.png"
        images[0].save(output_path)
        print(f"   [SAVE] Saved to: {output_path}")

        return str(output_path)

    def process_job(self, payload: bytes) -> None:
        """
        Main job processing pipeline.

        Steps:
        1. Deserialize protobuf message
        2. Extract and validate parameters
        3. Load model (if not already loaded)
        4. Generate image
        5. Log success

        Args:
            payload: Binary protobuf JobRequest message

        Raises:
            Exception: Various exceptions for different failure modes
        """
        # Deserialize
        job = worker_pb2.JobRequest()
        job.ParseFromString(payload)

        print(f"\n{'='*80}")
        print(f"[PROCESSING] Processing Job: {job.job_id}")
        print(f"   App ID: {job.app_id}")
        print(f"   Handler: {job.handler_type}")
        print(f"{'='*80}")

        try:
            # Validate app_id
            if job.app_id != "sdxl-local":
                raise ValueError(f"This worker only handles 'sdxl-local', got '{job.app_id}'")

            # Extract parameters
            generation_params = self.extract_params(dict(job.params))

            # Ensure model is loaded
            self.load_model()

            # Generate image
            output_path = self.generate_image(job.job_id, generation_params)

            print(f"[SUCCESS] Job {job.job_id} completed successfully!")
            print(f"   Result: {output_path}")

            # FUTURE: Update PostgreSQL with result
            # db.execute(
            #     "UPDATE jobs SET status='completed', result_path=?, completed_at=NOW() WHERE job_id=?",
            #     (output_path, job.job_id)
            # )

        except ValueError as e:
            print(f"[ERROR] Parameter Error in job {job.job_id}: {e}")
            # FUTURE: Mark job as failed in database

        except Exception as e:
            print(f"[ERROR] Processing Error in job {job.job_id}: {e}")
            # FUTURE: Mark job as failed, log stack trace


def main():
    """
    Main worker loop.

    Connects to Redis, joins consumer group, and processes jobs forever.
    """
    print("="*80)
    print("[STARTUP] SDXL Worker Starting")
    print("="*80)
    print(f"   Redis: {REDIS_HOST}")
    print(f"   Stream: {STREAM_KEY}")
    print(f"   Consumer: {CONSUMER_NAME} (Group: {GROUP_NAME})")
    print(f"   Output Dir: {OUTPUT_DIR}")
    print(f"   GPU Available: {GPU_AVAILABLE}")
    if GPU_AVAILABLE:
        print(f"   GPU: {torch.cuda.get_device_name(0)}")
        print(f"   CUDA Version: {torch.version.cuda}")
    print("="*80)

    # Check if ML libraries are available
    if not GPU_AVAILABLE or torch is None:
        print("\n[WARNING] RUNNING IN MOCK MODE")
        print("ML libraries not properly installed or GPU not available.")
        print("Worker will accept jobs but won't generate actual images.\n")
        # Fall back to mock worker
        from worker import main as old_main
        old_main.main()
        return

    # Initialize Redis client
    redis_client = RedisStreamClient(
        host=REDIS_HOST,
        stream_key=STREAM_KEY,
        group_name=GROUP_NAME,
        consumer_name=CONSUMER_NAME
    )

    # Ensure consumer group exists
    redis_client.ensure_consumer_group()

    # Initialize worker
    worker = SDXLWorker()

    print("\n[LISTENING] Listening for jobs...")
    print("   Press Ctrl+C to stop\n")

    # Main event loop
    while True:
        try:
            # Read messages (blocking call)
            entries = redis_client.read_messages(count=1, block_ms=2000)

            if entries:
                for stream, messages in entries:
                    for message_id, fields in messages:
                        # Process job
                        worker.process_job(fields[b'payload'])

                        # Acknowledge completion
                        redis_client.acknowledge(message_id)

        except KeyboardInterrupt:
            print("\n\n[SHUTDOWN] Worker shutting down gracefully...")
            break

        except Exception as e:
            print(f"\n[ERROR] Unexpected error: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(1)  # Avoid tight error loop

    print("✅ Worker stopped")


if __name__ == "__main__":
    main()
