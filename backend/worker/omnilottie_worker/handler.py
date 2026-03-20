import torch
import logging
import base64
import io
import os
import gc
import sys
import re
import json
import time
import random
import tempfile
import threading
import numpy as np
from typing import Dict, Any

from PIL import Image as PILImage
from decord import VideoReader, cpu

from decoder import LottieDecoder
from transformers import AutoProcessor
from qwen_vl_utils import process_vision_info
from lottie.objects.lottie_tokenize import LottieTensor
from lottie.objects.lottie_param import (
    from_sequence, ShapeLayer, NullLayer, PreCompLayer, TextLayer,
    SolidColorLayer, Font, Chars,
    shape_layer_to_json, null_layer_to_json, precomp_layer_to_json,
    text_layer_to_json, solid_layer_to_json, font_to_json, char_to_json
)
from huggingface_hub import hf_hub_download

sys.path.append('/app')
from shared.gpu_lock import GPUMemoryLock

# Set CUDA memory allocation configuration
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = "You are a Lottie animation expert."
VIDEO_PROMPT = "Turn this video into Lottie code."
LOTTIE_BOS = 192398
LOTTIE_EOS = 192399
PAD_TOKEN = 151643

DEFAULT_MODEL_PATH = os.environ.get("MODEL_PATH", "")


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def simplify_to_animation_description(text):
    if not text or not isinstance(text, str):
        return text
    prefixes = [
        r'^The video features?\s+', r'^The scene shows?\s+',
        r'^An animation of\s+', r'^There is\s+', r'^It shows?\s+'
    ]
    for pattern in prefixes:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)
    if text:
        text = text[0].upper() + text[1:]
    return text.strip()


def add_random_background(img):
    if img.mode != 'RGBA':
        return img.convert('RGB')
    light_colors = [(255, 255, 255), (245, 245, 245), (250, 250, 250)]
    bg_color = random.choice(light_colors)
    background = PILImage.new('RGB', img.size, bg_color)
    background.paste(img, (0, 0), img)
    return background


def load_frames_from_video(video_path, num_frames=8, max_size=336):
    ext = os.path.splitext(video_path)[1].lower()
    frames = []

    if ext in ('.gif', '.webp'):
        img = PILImage.open(video_path)
        total_frames = getattr(img, 'n_frames', 1)
        if total_frames < 1:
            raise ValueError(f"No frames in {ext.upper()}: {video_path}")
        indices = np.linspace(0, total_frames - 1, min(num_frames, total_frames)).astype(int)
        for idx in indices:
            img.seek(idx)
            frame = img.convert('RGB')
            if max(frame.size) > max_size:
                frame.thumbnail((max_size, max_size), PILImage.LANCZOS)
            frames.append(frame)
        img.close()
    else:
        vr = VideoReader(video_path, ctx=cpu(0))
        total_frames = len(vr)
        if total_frames < 1:
            raise ValueError(f"Video has no frames: {video_path}")
        indices = np.linspace(0, total_frames - 1, num_frames).astype(int)
        frames_np = vr.get_batch(indices).asnumpy()
        for f in frames_np:
            img = PILImage.fromarray(f)
            if max(img.size) > max_size:
                img.thumbnail((max_size, max_size), PILImage.LANCZOS)
            frames.append(img)

    while len(frames) < num_frames:
        frames.append(frames[-1].copy())
    return frames


def build_messages(task_type, text_prompt=None, image=None, video_frames=None):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if task_type == "text":
        text = simplify_to_animation_description(text_prompt)
        messages.append({
            "role": "user",
            "content": [{"type": "text", "text": f"Generate Lottie code: {text}"}]
        })
    elif task_type == "image":
        text = simplify_to_animation_description(text_prompt)
        messages.append({
            "role": "user",
            "content": [
                {"type": "image", "image": image},
                {"type": "text", "text": f"Animate this image: {text}"}
            ]
        })
    elif task_type == "video":
        messages.append({
            "role": "user",
            "content": [
                {"type": "video", "video": video_frames, "fps": 8.0},
                {"type": "text", "text": VIDEO_PROMPT}
            ]
        })
    return messages


def prepare_inference_input(processor, messages, device):
    text_input = processor.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )
    image_inputs, video_inputs = process_vision_info(messages)
    inputs = processor(
        text=[text_input],
        images=image_inputs if image_inputs else None,
        videos=video_inputs if video_inputs else None,
        padding=False,
        return_tensors="pt"
    )
    input_ids = inputs['input_ids']
    attention_mask = inputs['attention_mask']
    target_len = 1500
    if input_ids.shape[1] < target_len:
        pad_len = target_len - input_ids.shape[1]
        input_ids = torch.cat([
            torch.full((1, pad_len), PAD_TOKEN, dtype=torch.long),
            input_ids
        ], dim=1)
        attention_mask = torch.cat([
            torch.zeros((1, pad_len), dtype=torch.long),
            attention_mask
        ], dim=1)
    return {
        'input_ids': input_ids.to(device),
        'attention_mask': attention_mask.to(device),
        'pixel_values': inputs.get('pixel_values').to(device) if inputs.get('pixel_values') is not None else None,
        'image_grid_thw': inputs.get('image_grid_thw').to(device) if inputs.get('image_grid_thw') is not None else None,
        'pixel_values_videos': inputs.get('pixel_values_videos').to(device) if inputs.get('pixel_values_videos') is not None else None,
        'video_grid_thw': inputs.get('video_grid_thw').to(device) if inputs.get('video_grid_thw') is not None else None,
    }


def generate_lottie(model, inputs, max_tokens, device, use_sampling=False, temperature=0.95, top_p=0.25, top_k=5):
    model.transformer.rope_deltas = None
    position_ids, _ = model.transformer.get_rope_index(
        input_ids=inputs['input_ids'],
        attention_mask=inputs['attention_mask'],
        image_grid_thw=inputs.get('image_grid_thw'),
        video_grid_thw=inputs.get('video_grid_thw'),
    )
    position_ids = position_ids * inputs['attention_mask'][None, ]
    kwargs = {
        'input_ids': inputs['input_ids'],
        'attention_mask': inputs['attention_mask'],
        'pixel_values': inputs.get('pixel_values'),
        'image_grid_thw': inputs.get('image_grid_thw'),
        'pixel_values_videos': inputs.get('pixel_values_videos'),
        'video_grid_thw': inputs.get('video_grid_thw'),
        'position_ids': position_ids,
        'max_new_tokens': max_tokens,
        'eos_token_id': LOTTIE_EOS,
        'pad_token_id': PAD_TOKEN,
        'use_cache': True,
    }
    if use_sampling:
        kwargs.update({'do_sample': True, 'temperature': temperature, 'top_p': top_p, 'top_k': top_k})
    else:
        kwargs.update({'do_sample': False, 'num_beams': 1})
    with torch.no_grad():
        outputs = model.transformer.generate(**kwargs)
    input_len = inputs['input_ids'].shape[1]
    generated_ids = outputs[0][input_len:].tolist()
    del outputs, kwargs, position_ids
    if generated_ids and generated_ids[0] == LOTTIE_BOS:
        generated_ids = generated_ids[1:]
    if LOTTIE_EOS in generated_ids:
        generated_ids = generated_ids[:generated_ids.index(LOTTIE_EOS)]
    return generated_ids


def fix_lottie_json(anim):
    anim_ip = int(round(anim.get("ip", 0)))
    anim_op = int(round(anim.get("op", 16)))
    anim["ip"] = anim_ip
    anim["op"] = anim_op
    anim["fr"] = int(round(anim.get("fr", 8)))
    anim["ddd"] = int(anim.get("ddd", 0))

    def fix_t_recursive(obj):
        if isinstance(obj, dict):
            if obj.get("a") == 1 and isinstance(obj.get("k"), list):
                for kf in obj["k"]:
                    if isinstance(kf, dict) and "t" in kf:
                        kf["t"] = int(round(kf["t"]))
            for v in obj.values():
                fix_t_recursive(v)
        elif isinstance(obj, list):
            for item in obj:
                fix_t_recursive(item)

    fix_t_recursive(anim)

    max_x = float(anim.get("w", 512))
    max_y = float(anim.get("h", 512))

    def collect_pos(layer):
        nonlocal max_x, max_y
        p = layer.get("ks", {}).get("p", {})
        if isinstance(p, dict):
            if p.get("a", 0) == 0:
                pv = p.get("k", [0, 0])
                if isinstance(pv, list) and len(pv) >= 2:
                    max_x = max(max_x, float(pv[0]))
                    max_y = max(max_y, float(pv[1]))
            else:
                for kf in p.get("k", []):
                    if isinstance(kf, dict):
                        for sv in (kf.get("s", []), kf.get("e", [])):
                            if isinstance(sv, list) and len(sv) >= 2:
                                max_x = max(max_x, float(sv[0]))
                                max_y = max(max_y, float(sv[1]))
        for sub in layer.get("layers", []):
            collect_pos(sub)

    for layer in anim.get("layers", []):
        collect_pos(layer)

    anim["w"] = max(512, int((max_x * 1.1 + 15) // 16 * 16))
    anim["h"] = max(512, int((max_y * 1.1 + 15) // 16 * 16))

    valid_inds = set()
    for layer in anim.get("layers", []):
        if "ind" in layer:
            valid_inds.add(int(layer["ind"]))

    def clean_shapes(shapes):
        if not isinstance(shapes, list):
            return shapes
        cleaned = []
        for sh in shapes:
            if not isinstance(sh, dict):
                continue
            if sh.get("ty") == "gr":
                sh["it"] = clean_shapes(sh.get("it", []))
                if not sh["it"]:
                    continue
                has_tr = any(item.get("ty") == "tr" for item in sh["it"] if isinstance(item, dict))
                if not has_tr:
                    sh["it"].append({
                        "ty": "tr", "nm": "",
                        "a": {"a": 0, "k": [0, 0], "ix": 1},
                        "p": {"a": 0, "k": [0, 0], "ix": 2},
                        "s": {"a": 0, "k": [100, 100], "ix": 3},
                        "r": {"a": 0, "k": 0, "ix": 6},
                        "o": {"a": 0, "k": 100, "ix": 7},
                        "sk": {"a": 0, "k": 0, "ix": 4},
                        "sa": {"a": 0, "k": 0, "ix": 5},
                        "hd": False
                    })
            cleaned.append(sh)
        return cleaned

    def fix_layer(layer):
        ip = int(round(layer.get("ip", anim_ip)))
        op = int(round(layer.get("op", anim_op)))
        layer["ip"] = max(anim_ip, ip)
        layer["op"] = min(anim_op, max(layer["ip"] + 1, op))
        layer["st"] = int(round(layer.get("st", anim_ip)))
        if "ind" in layer:
            layer["ind"] = int(layer["ind"])
        if "parent" in layer:
            p = int(layer["parent"])
            if p in valid_inds:
                layer["parent"] = p
            else:
                del layer["parent"]
        layer.pop("ct", None)
        if "shapes" in layer:
            layer["shapes"] = clean_shapes(layer["shapes"])
        for sub in layer.get("layers", []):
            fix_layer(sub)
        return layer

    fixed_layers = []
    for l in anim.get("layers", []):
        fix_layer(l)
        if l.get("ty") == 4 and not l.get("shapes", []):
            continue
        fixed_layers.append(l)
    anim["layers"] = fixed_layers

    for asset in anim.get("assets", []):
        if "layers" in asset:
            fixed = []
            for l in asset["layers"]:
                fix_layer(l)
                if l.get("ty") == 4 and not l.get("shapes"):
                    continue
                fixed.append(l)
            asset["layers"] = fixed

    return anim


def tokens_to_lottie_json(generated_ids):
    reconstructed_tensor = LottieTensor.from_list(generated_ids)
    reconstructed_sequence = reconstructed_tensor.to_sequence()
    reconstructed = from_sequence(reconstructed_sequence)

    json_animation = {
        "v": reconstructed.get("v", "5.5.2"),
        "fr": reconstructed.get("fr", 8),
        "ip": reconstructed.get("ip", 0),
        "op": reconstructed.get("op", 16),
        "w": reconstructed.get("w", 512),
        "h": reconstructed.get("h", 512),
        "nm": reconstructed.get("nm", "Animation"),
        "ddd": reconstructed.get("ddd", 0),
        "assets": [],
        "layers": [],
    }

    if "fonts" in reconstructed and reconstructed["fonts"]:
        fonts_data = reconstructed["fonts"]
        if isinstance(fonts_data, dict) and "list" in fonts_data:
            fonts_json = {"list": []}
            for font in fonts_data["list"]:
                fonts_json["list"].append(font_to_json(font) if isinstance(font, Font) else font)
            json_animation["fonts"] = fonts_json

    if "chars" in reconstructed and reconstructed["chars"]:
        chars_json = []
        for char in reconstructed["chars"]:
            chars_json.append(char_to_json(char) if isinstance(char, Chars) else char)
        json_animation["chars"] = chars_json

    layer_converters = {
        ShapeLayer: shape_layer_to_json,
        NullLayer: null_layer_to_json,
        PreCompLayer: precomp_layer_to_json,
        TextLayer: text_layer_to_json,
        SolidColorLayer: solid_layer_to_json,
    }

    def convert_layer(layer):
        for cls, fn in layer_converters.items():
            if isinstance(layer, cls):
                return fn(layer)
        return layer

    for asset in reconstructed.get("assets", []):
        asset_json = dict(asset)
        if "layers" in asset:
            asset_json["layers"] = [convert_layer(l) for l in asset["layers"]]
        json_animation["assets"].append(asset_json)

    json_animation["layers"] = [convert_layer(l) for l in reconstructed.get("layers", [])]
    json_animation = fix_lottie_json(json_animation)
    return json_animation


# ---------------------------------------------------------------------------
# Handler class
# ---------------------------------------------------------------------------

class OmnilottieHandler:
    def __init__(self, redis_client=None, worker_id="omnilottie-worker", state_callback=None):
        self._state_cb = state_callback
        self.model = None
        self.processor = None
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
        """Load OmniLottie model, keeping it cached for reuse."""
        if self.model is not None:
            logger.info("OmniLottie model already loaded, reusing...")
            try:
                if not next(self.model.parameters()).is_cuda:
                    logger.warning("Model was on CPU, moving back to GPU...")
                    self.model = self.model.to(self.device)
            except Exception as e:
                logger.warning(f"Could not verify device, reloading model: {e}")
                self.model = None
                self.processor = None
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
        logger.info("Loading OmniLottie model to GPU...")

        device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        self.device = device

        model_path = DEFAULT_MODEL_PATH
        if not model_path or not os.path.exists(model_path):
            logger.info("pytorch_model.bin not found locally — downloading from HuggingFace...")
            model_path = hf_hub_download(
                repo_id="OmniLottie/OmniLottie",
                filename="pytorch_model.bin",
                resume_download=True,
            )

        logger.info("Initializing LottieDecoder...")
        self.model = LottieDecoder(pix_len=4560, text_len=1500)

        logger.info(f"Loading weights from {model_path}...")
        self.model.load_state_dict(torch.load(model_path, map_location="cpu"))
        self.model = self.model.to(self.device).eval()

        logger.info("Loading processor...")
        self.processor = AutoProcessor.from_pretrained(
            "Qwen/Qwen2.5-VL-3B-Instruct",
            padding_side="left"
        )

        logger.info(f"OmniLottie loaded successfully on {self.device}")

    def offload_model(self):
        """Completely remove model from GPU to free memory."""
        if self.model is None:
            logger.info("No model loaded, nothing to offload")
            return

        logger.info("Removing OmniLottie model from GPU...")

        if self.cleanup_timer:
            self.cleanup_timer.cancel()
            self.cleanup_timer = None

        del self.model
        del self.processor
        self.model = None
        self.processor = None
        self.last_used = None

        torch.cuda.empty_cache()
        torch.cuda.synchronize()
        gc.collect()

        logger.info("OmniLottie model removed from GPU - memory freed")
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
        """Cancel scheduled cleanup (called when new job arrives)."""
        if self.cleanup_timer:
            self.cleanup_timer.cancel()
            self.cleanup_timer = None
            logger.info("Cancelled scheduled GPU cleanup")

    def process(self, job_id: str, params: Dict[str, str]) -> Dict[str, Any]:
        """Process a single Lottie generation job."""
        try:
            self.cancel_cleanup()
            self._publish_state("PROCESSING")

            self.load_model()

            # Extract parameters
            task_type = params.get("task_type", "text")
            prompt = params.get("prompt", "")
            image_base64 = params.get("image_base64")
            video_base64 = params.get("video_base64")
            max_tokens = int(params.get("max_tokens", "5556"))
            use_sampling = params.get("use_sampling", "true").lower() == "true"
            temperature = float(params.get("temperature", "0.9"))
            top_p = float(params.get("top_p", "0.25"))
            top_k = int(params.get("top_k", "5"))

            logger.info(f"Generating Lottie: task_type={task_type}, prompt={prompt[:50]}...")

            torch.cuda.empty_cache()

            start_time = time.time()

            image = None
            video_frames = None
            tmp_video_path = None

            try:
                if task_type == "image" and image_base64:
                    image_bytes = base64.b64decode(image_base64)
                    pil_image = PILImage.open(io.BytesIO(image_bytes))
                    if pil_image.mode == 'RGBA':
                        pil_image = add_random_background(pil_image)
                    else:
                        pil_image = pil_image.convert('RGB')
                    image = pil_image.resize((448, 448), PILImage.LANCZOS)

                elif task_type == "video" and video_base64:
                    video_bytes = base64.b64decode(video_base64)
                    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
                    tmp.write(video_bytes)
                    tmp.close()
                    tmp_video_path = tmp.name
                    video_frames = load_frames_from_video(tmp_video_path, num_frames=8)

                messages = build_messages(task_type, text_prompt=prompt, image=image, video_frames=video_frames)
                inputs = prepare_inference_input(self.processor, messages, self.device)
                generated_ids = generate_lottie(
                    self.model, inputs, max_tokens, self.device,
                    use_sampling, temperature, top_p, top_k
                )

                del inputs
                torch.cuda.empty_cache()

                lottie_json = tokens_to_lottie_json(generated_ids)
                elapsed = time.time() - start_time

            finally:
                if tmp_video_path and os.path.exists(tmp_video_path):
                    os.unlink(tmp_video_path)
                del image, video_frames
                torch.cuda.empty_cache()
                gc.collect()

            self.last_used = time.time()
            self._schedule_cleanup()

            return {
                "success": True,
                "output": {
                    "animation": lottie_json,
                    "tokens_generated": len(generated_ids),
                    "layers": len(lottie_json.get("layers", [])),
                    "elapsed_seconds": round(elapsed, 2),
                }
            }

        except Exception as e:
            logger.error(f"Error generating Lottie: {e}", exc_info=True)
            self._schedule_cleanup()
            return {
                "success": False,
                "error": str(e)
            }
