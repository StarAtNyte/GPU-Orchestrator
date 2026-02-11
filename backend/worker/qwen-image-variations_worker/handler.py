"""
Qwen Image Variations Handler - Generate random variations of a person's image.
Uses the same Qwen-Image-Edit model with a randomly selected prompt from variationsPrompts.json.
"""

import torch
import math
import logging
import base64
import gc
import time
import threading
import os
import json
import random
import sys
from io import BytesIO
from typing import Dict, Any, List
from PIL import Image

sys.path.append('/app')
from shared.gpu_lock import GPUMemoryLock

os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

logger = logging.getLogger(__name__)

PROMPTS_PATH = os.path.join(os.path.dirname(__file__), "variationsPrompts.json")


def _collect_prompts(data) -> List[Dict[str, str]]:
    """Recursively collect all dicts containing a 'prompt' key from nested JSON."""
    results = []
    if isinstance(data, dict):
        if "prompt" in data:
            results.append(data)
        else:
            for value in data.values():
                results.extend(_collect_prompts(value))
    return results


class QwenImageVariationsHandler:
    def __init__(self, redis_client=None, worker_id="qwen-variations-worker"):
        self.pipe = None
        self.device = "cuda"
        self.last_used = None
        self.cleanup_timer = None
        self.cleanup_delay = 300  # 5 minutes
        self.prompts = []
        self.negative_prompt = ""
        self.redis_client = redis_client
        self.worker_id = worker_id
        self.gpu_lock = GPUMemoryLock(redis_client, worker_id) if redis_client else None
        self._load_prompts()

    def _load_prompts(self):
        """Load and flatten all variation prompts from the JSON file."""
        with open(PROMPTS_PATH) as f:
            data = json.load(f)

        self.prompts = _collect_prompts(data.get("variation_prompts", {}))

        # Combine standard and portrait-focused negative prompts
        neg = data.get("negative_prompt", {})
        self.negative_prompt = f"{neg.get('standard', '')}. {neg.get('portrait_focused', '')}"

        logger.info(f"Loaded {len(self.prompts)} variation prompts")

    def load_model(self):
        """Load Qwen Image Edit model with 4-bit quantization."""
        if self.pipe is not None:
            logger.info("Qwen model already loaded, reusing...")
            return

        # Acquire GPU memory lock before loading
        if self.gpu_lock:
            logger.info("[GPU_LOCK] Acquiring lock before loading model...")
            try:
                with self.gpu_lock.locked(timeout=180, required_memory_mb=12000):
                    self._load_model_internal()
            except TimeoutError as e:
                logger.error(f"[GPU_LOCK] Failed to acquire lock: {e}")
                raise Exception("GPU memory not available - another worker is loading. Please retry in a few moments.")
        else:
            logger.warning("[GPU_LOCK] No Redis client - loading without coordination")
            self._load_model_internal()

    def _load_model_internal(self):
        """Internal method to actually load the model (called within lock)."""
        logger.info("Loading Qwen model with 4-bit quantization...")
        logger.info("This may take several minutes on first run to download ~20GB of model files...")

        from diffusers import (
            QwenImageEditPipeline,
            QwenImageTransformer2DModel,
            FlowMatchEulerDiscreteScheduler,
            BitsAndBytesConfig as TransformersBitsAndBytesConfig
        )

        scheduler_config = {
            "base_image_seq_len": 256,
            "base_shift": math.log(3),
            "invert_sigmas": False,
            "max_image_seq_len": 8192,
            "max_shift": math.log(3),
            "num_train_timesteps": 1000,
            "shift": 1.0,
            "shift_terminal": None,
            "stochastic_sampling": False,
            "time_shift_type": "exponential",
            "use_beta_sigmas": False,
            "use_dynamic_shifting": True,
            "use_exponential_sigmas": False,
            "use_karras_sigmas": False,
        }
        scheduler = FlowMatchEulerDiscreteScheduler.from_config(scheduler_config)

        quant_config = TransformersBitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.bfloat16
        )

        try:
            transformer = QwenImageTransformer2DModel.from_pretrained(
                "Qwen/Qwen-Image-Edit",
                subfolder="transformer",
                quantization_config=quant_config,
                torch_dtype=torch.bfloat16,
                resume_download=True,  # Resume interrupted downloads
                force_download=False,  # Use cached files if available
            )
        except Exception as e:
            logger.error(f"Failed to load transformer: {e}")
            logger.error("This usually means model files are incomplete or corrupted.")
            logger.error("Try: docker volume rm backend_qwen_models && docker compose restart qwen-image-variations-worker")
            # Clean up any partial GPU allocations
            torch.cuda.empty_cache()
            gc.collect()
            raise

        try:
            self.pipe = QwenImageEditPipeline.from_pretrained(
                "Qwen/Qwen-Image-Edit",
                transformer=transformer,
                scheduler=scheduler,
                torch_dtype=torch.bfloat16
            )

            self.pipe.load_lora_weights(
                "lightx2v/Qwen-Image-Lightning",
                weight_name="Qwen-Image-Edit-Lightning-4steps-V1.0-bf16.safetensors"
            )

            self.pipe.enable_model_cpu_offload()

            logger.info("Qwen model loaded successfully")

        except Exception as e:
            logger.error(f"Failed to load pipeline or LoRA weights: {e}")
            # Clean up partial model if OOM
            if "out of memory" in str(e).lower() or "OutOfMemoryError" in str(type(e).__name__):
                logger.warning("OOM during model loading - cleaning up GPU memory")
                if hasattr(self, 'pipe') and self.pipe is not None:
                    del self.pipe
                    self.pipe = None
                torch.cuda.empty_cache()
                gc.collect()
            raise

    def offload_model(self):
        """Remove model from GPU to free memory."""
        if self.pipe is None:
            logger.info("No model loaded, nothing to offload")
            return

        logger.info("Removing Qwen model from GPU...")

        if self.cleanup_timer:
            self.cleanup_timer.cancel()
            self.cleanup_timer = None

        del self.pipe
        self.pipe = None
        self.last_used = None

        torch.cuda.empty_cache()
        torch.cuda.synchronize()
        gc.collect()

        logger.info("Qwen model removed from GPU - memory freed")

    def _schedule_cleanup(self):
        """Schedule model cleanup after delay."""
        if self.cleanup_timer:
            self.cleanup_timer.cancel()

        self.cleanup_timer = threading.Timer(self.cleanup_delay, self.offload_model)
        self.cleanup_timer.daemon = True
        self.cleanup_timer.start()
        logger.info(f"Scheduled GPU cleanup in {self.cleanup_delay} seconds")

    def cancel_cleanup(self):
        """Cancel scheduled cleanup."""
        if self.cleanup_timer:
            self.cleanup_timer.cancel()
            self.cleanup_timer = None
            logger.info("Cancelled scheduled GPU cleanup")

    def process(self, job_id: str, params: Dict[str, str]) -> Dict[str, Any]:
        """Process a variation job with a randomly selected prompt."""
        try:
            self.cancel_cleanup()
            self.load_model()

            image_b64 = params.get("image_base64", "")
            steps = int(params.get("steps", "4"))
            cfg_scale = float(params.get("cfg_scale", "1.0"))

            if not image_b64:
                return {
                    "success": False,
                    "error": "No input image provided (image_base64 required)"
                }

            # Pick a random variation prompt
            selected = random.choice(self.prompts)
            prompt = selected["prompt"]
            logger.info(f"[JOB {job_id}] Selected prompt: {prompt[:80]}...")

            image_data = base64.b64decode(image_b64)
            input_image = Image.open(BytesIO(image_data)).convert("RGB")

            torch.cuda.empty_cache()

            result_image = self.pipe(
                image=input_image,
                prompt=prompt,
                negative_prompt=self.negative_prompt,
                num_inference_steps=steps,
                true_cfg_scale=cfg_scale
            ).images[0]

            buffered = BytesIO()
            result_image.save(buffered, format="PNG")
            result_b64 = base64.b64encode(buffered.getvalue()).decode()

            del input_image
            del result_image
            del buffered
            torch.cuda.empty_cache()
            gc.collect()

            self.last_used = time.time()
            self._schedule_cleanup()

            return {
                "success": True,
                "output": {
                    "image_base64": result_b64,
                    "prompt_used": prompt,
                    "steps": steps,
                    "cfg_scale": cfg_scale
                }
            }

        except Exception as e:
            logger.error(f"Error generating variation: {e}", exc_info=True)

            # If OOM error, offload immediately instead of waiting 5 minutes
            if "out of memory" in str(e).lower() or "OutOfMemoryError" in str(type(e).__name__):
                logger.warning("OOM error detected - offloading model immediately")
                self.offload_model()
            else:
                self._schedule_cleanup()

            return {
                "success": False,
                "error": str(e)
            }
