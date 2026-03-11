"""
Qwen Image Edit Handler - Image-to-Image Editing with QwenImageEditPipeline
Uses 4-bit quantization with LoRA Lightning for fast inference (~14 seconds)
"""

import torch
import math
import logging
import base64
import gc
import time
import threading
import os
import sys
from io import BytesIO
from typing import Dict, Any
from PIL import Image

sys.path.append('/app')
from shared.gpu_lock import GPUMemoryLock

os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

logger = logging.getLogger(__name__)


class QwenImageEditHandler:
    def __init__(self, redis_client=None, worker_id="qwen-edit-worker", state_callback=None):
        self.pipe = None
        self.device = "cuda"
        self.last_used = None
        self.cleanup_timer = None
        self.cleanup_delay = 300  # 5 minutes
        self.redis_client = redis_client
        self.worker_id = worker_id
        self.gpu_lock = GPUMemoryLock(redis_client, worker_id) if redis_client else None
        self._state_cb = state_callback

    def _publish_state(self, state: str) -> None:
        """Fire the state callback if one was provided (safe to call from any thread)."""
        if self._state_cb is not None:
            try:
                self._state_cb(state)
            except Exception as exc:
                logger.warning(f"[STATE_CB] Failed to publish state={state}: {exc}")

    def load_model(self):
        """Load Qwen Image Edit model with 4-bit quantization."""
        if self.pipe is not None:
            logger.info("Qwen Image Edit model already loaded, reusing...")
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
        logger.info("Loading Qwen Image Edit model with 4-bit quantization...")

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
                resume_download=True,
                force_download=False
            )
        except Exception as e:
            logger.error(f"Failed to load transformer: {e}")
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

            logger.info("Qwen Image Edit model loaded successfully")

        except Exception as e:
            logger.error(f"Failed to load pipeline or LoRA weights: {e}")
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

        logger.info("Removing Qwen Image Edit model from GPU...")

        if self.cleanup_timer:
            self.cleanup_timer.cancel()
            self.cleanup_timer = None

        del self.pipe
        self.pipe = None
        self.last_used = None

        torch.cuda.empty_cache()
        torch.cuda.synchronize()
        gc.collect()

        logger.info("Qwen Image Edit model removed from GPU - memory freed")
        self._publish_state("IDLE")

    def _schedule_cleanup(self):
        """Schedule model cleanup after delay."""
        if self.cleanup_timer:
            self.cleanup_timer.cancel()

        self.cleanup_timer = threading.Timer(self.cleanup_delay, self.offload_model)
        self.cleanup_timer.daemon = True
        self.cleanup_timer.start()
        logger.info(f"Scheduled GPU cleanup in {self.cleanup_delay} seconds")
        self._publish_state("WARM")

    def cancel_cleanup(self):
        """Cancel scheduled cleanup."""
        if self.cleanup_timer:
            self.cleanup_timer.cancel()
            self.cleanup_timer = None
            logger.info("Cancelled scheduled GPU cleanup")

    def process(self, job_id: str, params: Dict[str, str]) -> Dict[str, Any]:
        """Process a single image editing job."""
        try:
            self.cancel_cleanup()
            self._publish_state("PROCESSING")
            self.load_model()

            prompt = params.get("prompt", "")
            negative_prompt = params.get("negative_prompt", " ")
            image_b64 = params.get("image_base64", "")
            steps = int(params.get("steps", "4"))
            cfg_scale = float(params.get("cfg_scale", "1.0"))

            if not image_b64:
                return {
                    "success": False,
                    "error": "No input image provided (image_base64 required)"
                }

            logger.info(f"Processing image edit: {prompt[:50]}...")

            image_data = base64.b64decode(image_b64)
            input_image = Image.open(BytesIO(image_data)).convert("RGB")

            torch.cuda.empty_cache()

            result_image = self.pipe(
                image=input_image,
                prompt=prompt,
                negative_prompt=negative_prompt,
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
                    "steps": steps,
                    "cfg_scale": cfg_scale
                }
            }

        except Exception as e:
            logger.error(f"Error editing image: {e}", exc_info=True)

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
