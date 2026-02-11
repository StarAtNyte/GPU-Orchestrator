#!/usr/bin/env python3
"""
Local script to generate saree images for all designs in '4 set color combination/' directory
Uses Qwen Image Edit Plus 2511 for high-quality two-image style transfer
Applies design patterns from reference images to the saree in the source image
Runs on your local GPU without requiring Modal
"""

import os
import math
import json
import argparse
from pathlib import Path
from datetime import datetime
from typing import List, Dict

# Set PyTorch memory allocation config to avoid fragmentation
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

import torch
from diffusers import QwenImageEditPlusPipeline
from PIL import Image, ImageOps

# ============================================================================
# CONFIGURATION
# ============================================================================

SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

# ============================================================================
# PROMPT VARIATION SYSTEM
# ============================================================================

# Settings / backgrounds - cycles through for visual variety
SETTINGS = [
    "a desi setting with warm lighting",
    "a traditional Indian courtyard with ornate pillars",
    "a royal palace garden with fountains",
    "a heritage haveli with painted walls",
    "a festive celebration with decorations",
    "a temple courtyard with stone carvings",
    "a lush green garden with flowers",
    "a rooftop terrace overlooking a city skyline",
    "a serene lake with mountains in the background",
    "an ornate doorway with carved wooden doors",
    "a vibrant Indian bazaar with colorful fabrics",
    "a blooming flower field under clear sky",
]

TEXTURES = [
    "sheer",
    "silk",
    "georgette",
    "cotton handloom",
    "satin",
    "raw silk",
    "organza",
    "banarasi brocade",
    "linen",
    "catalogue",
]


def get_varied_prompt(category: str, index: int) -> str:
    """Return a photorealistic saree prompt varied by category and image index."""
    setting = SETTINGS[index % len(SETTINGS)]
    texture = TEXTURES[index % len(TEXTURES)]
    return (
        f"The same female from the 2nd image is wearing a saree with the design from the first image. "
        f"All the designs and patterns in the first image should be preserved in the saree. "
        f"Keep her face, features, and identity exactly the same. "
        f"The lady is {setting}. The saree should have {texture} texture. "
        f"The image should be photorealistic."
    )


NEGATIVE_PROMPT = " "


# ============================================================================
# HELPERS
# ============================================================================

def scan_designs(designs_path: str, category_filter: str = "all") -> List[Dict]:
    """Walk directory recursively, return list of design files."""
    designs = []
    for root, _, files in os.walk(designs_path):
        for fname in sorted(files):
            if Path(fname).suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            rel_category = os.path.relpath(root, designs_path)
            category = rel_category if rel_category != "." else "uncategorized"

            # Filter by category if specified
            if category_filter.lower() != "all" and category_filter.lower() not in category.lower():
                continue

            designs.append({
                "path": os.path.join(root, fname),
                "name": Path(fname).stem,
                "category": category,
            })

    designs.sort(key=lambda d: (d["category"], d["name"]))
    return designs


def safe_filename(text: str) -> str:
    """Strip invalid filename characters."""
    for ch in '/\\:*?"<>|()':
        text = text.replace(ch, "_")
    return text.replace("  ", "_").strip("_ ")


def load_pipeline(device: str, dtype: torch.dtype, use_quantization: bool = False, use_fp8: bool = False):
    """Load Qwen Image Edit Plus 2511 pipeline for two-image style transfer.

    For RTX 4090 (24GB VRAM), the recommended approach is:
      --quantize  : uses pre-quantized NF4 model (ovedrive/Qwen-Image-Edit-2511-4bit, ~20GB VRAM)
      --fp8       : uses FP8 model (1038lab/Qwen-Image-Edit-2511-FP8, ~22GB VRAM)
    Both support multi-image input via QwenImageEditPlusPipeline.
    """
    print("\n" + "="*70)
    print("Loading Qwen Image Edit Plus 2511")
    print("="*70)
    print("This model supports two-image style transfer (design pattern application)")

    if use_fp8:
        print("\n⚡ Loading FP8 quantized model (~22GB VRAM)")
        print("   Model: 1038lab/Qwen-Image-Edit-2511-FP8")
        print("   First run will download model files...")
        pipe = QwenImageEditPlusPipeline.from_pretrained(
            "1038lab/Qwen-Image-Edit-2511-FP8",
            torch_dtype=torch.bfloat16,
        )
    elif use_quantization:
        print("\n⚙️  Loading pre-quantized NF4 model (~20GB VRAM)")
        print("   Model: ovedrive/Qwen-Image-Edit-2511-4bit")
        print("   First run will download model files...")
        pipe = QwenImageEditPlusPipeline.from_pretrained(
            "ovedrive/Qwen-Image-Edit-2511-4bit",
            torch_dtype=torch.bfloat16,
        )
    else:
        print("\nLoading standard Qwen-Image-Edit-2511 (bfloat16, ~40GB VRAM)")
        print("First run will download model files (~20-30GB)")
        pipe = QwenImageEditPlusPipeline.from_pretrained(
            "Qwen/Qwen-Image-Edit-2511",
            torch_dtype=dtype,
        )

    if device == "cuda":
        print("\nEnabling model CPU offload for memory management...")
        pipe.enable_model_cpu_offload()
        print("✓ Model CPU offload enabled (moves components to GPU only when needed)")

        if hasattr(pipe, 'vae') and hasattr(pipe.vae, 'enable_tiling'):
            pipe.vae.enable_tiling()
            print("✓ VAE tiling enabled")
    else:
        pipe = pipe.to(device)

    print("\n✓ Model loaded successfully")
    return pipe


def prepare_image(image_path: str, target_width: int = None, target_height: int = None) -> Image.Image:
    """Load and prepare image."""
    img = Image.open(image_path).convert("RGB")

    # Handle EXIF orientation
    try:
        img = ImageOps.exif_transpose(img)
    except (AttributeError, OSError):
        pass

    # Resize if target dimensions provided
    if target_width and target_height:
        img = img.resize((target_width, target_height), Image.Resampling.LANCZOS)

    return img


def format_time(seconds: float) -> str:
    """Format seconds to human readable string."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds/60:.1f}m"
    else:
        return f"{seconds/3600:.1f}h"


# ============================================================================
# MAIN GENERATION FUNCTION
# ============================================================================

def generate_sarees(
    female_image_path: str,
    designs_dir: str,
    output_dir: str,
    category_filter: str = "all",
    max_designs: int = 0,
    steps: int = 50,
    cfg_scale: float = 3.0,
    resume: bool = False,
    use_quantization: bool = False,
    use_fp8: bool = False,
    max_resolution: int = 768,
    save_freq: int = 10,
):
    """Generate saree images for all designs."""

    # Check inputs
    if not os.path.exists(female_image_path):
        raise FileNotFoundError(f"Female image not found: {female_image_path}")

    if not os.path.exists(designs_dir):
        raise FileNotFoundError(f"Designs directory not found: {designs_dir}")

    # Scan designs
    designs = scan_designs(designs_dir, category_filter)
    if not designs:
        raise ValueError(f"No valid design images found in {designs_dir}")

    if max_designs > 0:
        designs = designs[:max_designs]

    print("\n" + "="*70)
    print("4-SET SAREE GENERATOR (Qwen 2511)")
    print("="*70)
    print(f"Female image  : {female_image_path}")
    print(f"Designs dir   : {designs_dir}")
    print(f"Output dir    : {output_dir}")
    print(f"Category      : {category_filter}")
    print(f"Total designs : {len(designs)}")
    print(f"Steps         : {steps} ({'fast' if steps < 30 else 'quality' if steps <= 50 else 'ultra'})")
    print(f"CFG scale     : {cfg_scale}")
    print(f"Model         : Qwen-Image-Edit-2511 (two-image style transfer)")
    print(f"Resume mode   : {'Yes' if resume else 'No'}")

    # Setup device
    if not torch.cuda.is_available():
        print("\n⚠ WARNING: CUDA not available! Running on CPU will be VERY slow.")
        response = input("Continue anyway? (y/n): ")
        if response.lower() != 'y':
            return
        device = "cpu"
        dtype = torch.float32
    else:
        device = "cuda"
        dtype = torch.bfloat16
        gpu_name = torch.cuda.get_device_name(0)
        vram_gb = torch.cuda.get_device_properties(0).total_memory / 1e9
        print(f"\n✓ GPU: {gpu_name}")
        print(f"✓ VRAM: {vram_gb:.1f} GB")

        if vram_gb < 24 and not use_quantization and not use_fp8:
            print("\n⚠ WARNING: Less than 24GB VRAM detected without quantization.")
            print("  Use --quantize (NF4, ~20GB) or --fp8 (~22GB) for RTX 4090")

    # Create output directory
    os.makedirs(output_dir, exist_ok=True)

    # Load pipeline
    pipe = load_pipeline(device, dtype, use_quantization, use_fp8)

    # Prepare source image - preserve original dimensions
    print(f"\nPreparing source image: {female_image_path}")
    source = prepare_image(female_image_path)
    w, h = source.size
    print(f"Original image dimensions: {w}x{h}")

    # Only limit resolution if max_resolution is explicitly set (not 0)
    if max_resolution > 0 and max(w, h) > max_resolution:
        scale = max_resolution / max(w, h)
        w, h = int(w * scale), int(h * scale)
        print(f"⚠ Scaling down to fit {max_resolution}px limit: {source.size} -> {w}x{h}")
        print(f"  (Use --max-resolution 0 to disable scaling)")

    # Round to multiples of 16 (Qwen works best with multiples of 16)
    tw, th = (w // 16) * 16, (h // 16) * 16
    if tw != w or th != h:
        print(f"Rounding to multiples of 16: {w}x{h} -> {tw}x{th}")
    if tw != source.width or th != source.height:
        source = source.resize((tw, th), Image.Resampling.LANCZOS)
    print(f"✓ Final resolution: {tw}x{th}")

    # Generate
    print("\n" + "="*70)
    print("Starting generation...")
    print("="*70)

    results = []
    start_time = datetime.now()
    last_save_time = start_time

    for i, design in enumerate(designs):
        cat_safe = safe_filename(design["category"])
        name_safe = safe_filename(design["name"])

        out_dir = os.path.join(output_dir, cat_safe)
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"{name_safe}.png")

        # Resume: skip already generated
        if resume and os.path.exists(out_path):
            print(f"[{i+1}/{len(designs)}] SKIP (exists): {cat_safe}/{name_safe}")
            results.append({
                "design": design["name"],
                "category": design["category"],
                "status": "skipped",
                "output_path": out_path,
            })
            continue

        iter_start = datetime.now()
        print(f"\n[{i+1}/{len(designs)}] {design['category']} / {design['name']}")

        try:
            # Load design at native size (pipeline handles resizing internally)
            design_img = prepare_image(design["path"])

            # Build varied prompt for this design
            prompt = get_varied_prompt(design["category"], i)
            print(f"  Prompt: {prompt}")

            # Generate (using Qwen Image Edit Plus 2511)
            with torch.inference_mode():
                out = pipe(
                    image=[design_img, source],
                    prompt=prompt,
                    negative_prompt=NEGATIVE_PROMPT,
                    width=tw,
                    height=th,
                    num_inference_steps=steps,
                    guidance_scale=1.0,
                    true_cfg_scale=cfg_scale,
                    generator=torch.Generator(device=device).manual_seed(7919 * (i + 1) + 31),
                    num_images_per_prompt=1,
                )

            result_img = out.images[0]
            if result_img.mode != "RGB":
                result_img = result_img.convert("RGB")

            # Save
            result_img.save(out_path, format="PNG", compress_level=1)

            # Stats
            iter_time = (datetime.now() - iter_start).total_seconds()
            elapsed = (datetime.now() - start_time).total_seconds()
            completed = i + 1 - sum(1 for r in results if r["status"] == "skipped")
            avg_time = elapsed / completed if completed > 0 else 0
            remaining_count = len(designs) - i - 1
            eta = avg_time * remaining_count

            print(f"  ✓ Saved: {out_path}")
            print(f"  ⏱  Time: {iter_time:.1f}s | Avg: {avg_time:.1f}s/img | ETA: {format_time(eta)}")

            # Memory info
            if device == "cuda":
                mem_allocated = torch.cuda.memory_allocated() / 1e9
                mem_reserved = torch.cuda.memory_reserved() / 1e9
                print(f"  💾 VRAM: {mem_allocated:.1f}GB allocated, {mem_reserved:.1f}GB reserved")

            results.append({
                "design": design["name"],
                "category": design["category"],
                "status": "success",
                "output_path": out_path,
                "time_seconds": iter_time,
            })

            # Aggressive cleanup to free memory
            del design_img, result_img, out
            if device == "cuda":
                torch.cuda.empty_cache()
                torch.cuda.synchronize()

        except Exception as e:
            print(f"  ✗ FAILED: {e}")
            import traceback
            traceback.print_exc()

            results.append({
                "design": design["name"],
                "category": design["category"],
                "status": "failed",
                "error": str(e),
            })

            # Aggressive cleanup on error
            if 'design_img' in locals():
                del design_img
            if 'result_img' in locals():
                del result_img
            if 'out' in locals():
                del out
            if device == "cuda":
                torch.cuda.empty_cache()
                torch.cuda.synchronize()

        # Periodic save of summary
        if (i + 1) % save_freq == 0:
            time_since_save = (datetime.now() - last_save_time).total_seconds()
            print(f"\n[CHECKPOINT] Saving progress... ({time_since_save:.0f}s since last save)")
            save_summary(output_dir, results, start_time, female_image_path, designs_dir, steps, cfg_scale)
            last_save_time = datetime.now()

    # Final summary
    total_time = (datetime.now() - start_time).total_seconds()
    save_summary(output_dir, results, start_time, female_image_path, designs_dir, steps, cfg_scale)

    # Print final stats
    success_count = sum(1 for r in results if r["status"] == "success")
    failed_count = sum(1 for r in results if r["status"] == "failed")
    skipped_count = sum(1 for r in results if r["status"] == "skipped")

    print("\n" + "="*70)
    print("GENERATION COMPLETE")
    print("="*70)
    print(f"Success : {success_count}")
    print(f"Failed  : {failed_count}")
    print(f"Skipped : {skipped_count}")
    print(f"Total   : {len(designs)}")
    print(f"Time    : {format_time(total_time)}")
    if success_count > 0:
        print(f"Avg/img : {total_time/success_count:.1f}s")
    print(f"\nResults saved to: {output_dir}")
    print(f"Summary: {os.path.join(output_dir, 'summary.json')}")

    return results


def save_summary(output_dir, results, start_time, female_path, designs_dir, steps, cfg_scale):
    """Save generation summary to JSON."""
    total_time = (datetime.now() - start_time).total_seconds()
    success_count = sum(1 for r in results if r["status"] == "success")

    summary = {
        "timestamp": datetime.now().isoformat(),
        "source_image": female_path,
        "designs_directory": designs_dir,
        "model": "Qwen/Qwen-Image-Edit-2511",
        "total_designs": len(results),
        "success_count": success_count,
        "failed_count": sum(1 for r in results if r["status"] == "failed"),
        "skipped_count": sum(1 for r in results if r["status"] == "skipped"),
        "total_time_seconds": total_time,
        "avg_time_per_image": total_time / success_count if success_count > 0 else 0,
        "steps": steps,
        "cfg_scale": cfg_scale,
        "results": results,
    }

    summary_path = os.path.join(output_dir, "summary.json")
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Generate saree images for all designs in '4 set color combination/' directory",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # RECOMMENDED for RTX 4090 (24GB) - NF4 quantized, all 1196 designs
  python generate_4set_local.py --female female.jpg --designs "4 set color combination" --quantize

  # Test run first with 5 designs to verify output quality
  python generate_4set_local.py --female female.jpg --designs "4 set color combination" --quantize --max-designs 5

  # FP8 option (slightly better quality, ~22GB VRAM)
  python generate_4set_local.py --female female.jpg --designs "4 set color combination" --fp8

  # Generate only Pashmina designs
  python generate_4set_local.py --female female.jpg --designs "4 set color combination" --quantize --category "Pashmina"

  # Generate only Waves designs
  python generate_4set_local.py --female female.jpg --designs "4 set color combination" --quantize --category "Waves"

  # Faster generation (28 steps instead of 40)
  python generate_4set_local.py --female female.jpg --designs "4 set color combination" --quantize --steps 28 --cfg-scale 3.5

  # Best quality (50 steps)
  python generate_4set_local.py --female female.jpg --designs "4 set color combination" --quantize --steps 50 --cfg-scale 5.0

  # Resume interrupted run
  python generate_4set_local.py --female female.jpg --designs "4 set color combination" --quantize --resume

  # Constrained VRAM: limit resolution
  python generate_4set_local.py --female female.jpg --designs "4 set color combination" --quantize --max-resolution 768
        """
    )

    parser.add_argument("--female", required=True, help="Path to female model image")
    parser.add_argument("--designs", required=True, help="Path to designs directory")
    parser.add_argument("--output", default="outputs", help="Output directory (default: outputs)")
    parser.add_argument("--category", default="all", help="Filter by category (default: all)")
    parser.add_argument("--max-designs", type=int, default=0, help="Limit number of designs (0=all)")
    parser.add_argument("--steps", type=int, default=50, help="Inference steps (default: 50, use 28 for faster)")
    parser.add_argument("--cfg-scale", type=float, default=3.0, help="CFG scale (default: 3.0)")
    parser.add_argument("--resume", action="store_true", help="Resume: skip existing images")
    parser.add_argument("--quantize", action="store_true", help="Use pre-quantized NF4 model (~20GB VRAM, recommended for RTX 4090)")
    parser.add_argument("--fp8", action="store_true", help="Use FP8 quantized model (~22GB VRAM, faster but needs more VRAM)")
    parser.add_argument("--max-resolution", type=int, default=0, help="Max resolution limit (0=no limit, preserves original dimensions)")
    parser.add_argument("--save-freq", type=int, default=10, help="Save summary every N images (default: 10)")

    args = parser.parse_args()

    # Create timestamped output directory
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = os.path.join(args.output, f"4set_run_{timestamp}")

    # If resume, use most recent directory instead
    if args.resume:
        if os.path.exists(args.output):
            runs = sorted([d for d in os.listdir(args.output) if d.startswith("4set_run_")])
            if runs:
                output_dir = os.path.join(args.output, runs[-1])
                print(f"Resuming previous run: {runs[-1]}")

    try:
        generate_sarees(
            female_image_path=args.female,
            designs_dir=args.designs,
            output_dir=output_dir,
            category_filter=args.category,
            max_designs=args.max_designs,
            steps=args.steps,
            cfg_scale=args.cfg_scale,
            resume=args.resume,
            use_quantization=args.quantize,
            use_fp8=args.fp8,
            max_resolution=args.max_resolution,
            save_freq=args.save_freq,
        )
    except KeyboardInterrupt:
        print("\n\n⚠ Interrupted by user. Progress saved to summary.json")
        print(f"Resume with: python {__file__} --female {args.female} --designs {args.designs} --resume")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
