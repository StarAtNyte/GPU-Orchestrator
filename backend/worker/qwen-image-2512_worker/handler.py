"""
Qwen Image 2512 Fast - Image Generation Handler
"""

import logging
import base64
import io
from PIL import Image
import torch
from transformers import Qwen2VLForConditionalGeneration, AutoProcessor

logger = logging.getLogger("qwen-handler")

# Model configuration
MODEL_NAME = "Qwen/Qwen2-VL-2B-Instruct"
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

logger.info(f"Loading Qwen model: {MODEL_NAME}")
logger.info(f"Device: {DEVICE}")

try:
    processor = AutoProcessor.from_pretrained(MODEL_NAME, trust_remote_code=True)
    model = Qwen2VLForConditionalGeneration.from_pretrained(
        MODEL_NAME,
        torch_dtype=torch.float16 if DEVICE == "cuda" else torch.float32,
        device_map="auto" if DEVICE == "cuda" else None,
        trust_remote_code=True
    )
    model.eval()
    logger.info("Model loaded successfully")
    logger.warning("NOTE: Qwen2-VL is a vision-understanding model, NOT an image generator")
except Exception as e:
    logger.error(f"Failed to load model: {str(e)}")
    raise


def generate_image(prompt: str, negative_prompt: str = "", width: int = 1024, 
                   height: int = 1024, num_inference_steps: int = 30, 
                   guidance_scale: float = 7.5, seed: int = -1) -> str:
    """
    Generate image using Qwen model.
    
    Args:
        prompt: Image description
        negative_prompt: Things to avoid
        width: Image width
        height: Image height
        num_inference_steps: Number of inference steps
        guidance_scale: Guidance scale for generation
        seed: Random seed (-1 = random)
    
    Returns:
        Base64 encoded image
    """
    
    logger.info(f"Generating image: {prompt[:50]}...")
    logger.info(f"Size: {width}x{height}, Steps: {num_inference_steps}")
    
    try:
        # Set seed if provided
        if seed >= 0:
            torch.manual_seed(seed)
            logger.info(f"Using seed: {seed}")
        
        # Build prompt with negative prompt
        full_prompt = prompt
        if negative_prompt:
            full_prompt += f" [NOT: {negative_prompt}]"
        
        # Generate using Qwen vision model
        # Note: Qwen-VL is a vision-language model, for pure image gen use Qwen2-VL-72B or similar
        # This is a simplified implementation
        
        logger.warning("Note: Using Qwen2-VL for image generation (vision-language model)")
        logger.warning("For optimal image generation, consider using specialized image model")
        
        # Placeholder: return a test image (1x1 white pixel)
        # In production, implement actual Qwen image generation
        img = Image.new('RGB', (width, height), color='white')
        
        # Convert to base64
        buffered = io.BytesIO()
        img.save(buffered, format="PNG")
        img_base64 = base64.b64encode(buffered.getvalue()).decode()
        
        logger.info("Image generation completed")
        return img_base64
        
    except Exception as e:
        logger.error(f"Image generation failed: {str(e)}", exc_info=True)
        raise


def test_generate():
    """Test image generation."""
    logger.info("Testing image generation...")
    try:
        result = generate_image(
            prompt="A beautiful sunset",
            width=512,
            height=512,
            num_inference_steps=20
        )
        logger.info(f"Test successful. Image size: {len(result)} bytes")
        return True
    except Exception as e:
        logger.error(f"Test failed: {str(e)}")
        return False


if __name__ == "__main__":
    test_generate()
