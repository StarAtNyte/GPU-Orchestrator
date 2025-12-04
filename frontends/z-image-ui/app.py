from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import requests
import os
from typing import Optional

app = FastAPI(title="Z-Image Generator")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://orchestrator:8080")
APP_ID = "z-image"

# Resolution choices matching Z-Image capabilities
RESOLUTIONS = [
    "1024x1024 ( 1:1 )",
    "576x1024 ( 9:16 )",
    "896x1200 ( 3:4 )",
]

# Example prompts with titles
EXAMPLE_PROMPTS = [
    {
        "title": "Modern Office with Carpet",
        "prompt": "Corner-view modern office with full floor visible. White walls, one desk with chair, a lounge chair, bookshelf, and floor lamp. Place a circular carpet with designer abstract patterns, including curved lines, color blocks, and gradients in gray, muted blue, and beige. The carpet should have realistic pile, shadows, and scale appropriate for the furniture."
    },
    {
        "title": "Modern Loft with Carpet",
        "prompt": "Corner-view modern loft showing the full open floor. Concrete walls, tall windows, one sofa, a small table, and a floor lamp. Place a rectangular carpet with bold abstract patterns, asymmetric shapes, and vibrant colors such as red, mustard, teal, and black. The carpet should be thick, textured, and visually dominant."
    },
    {
        "title": "Luxury Bedroom with Rug",
        "prompt": "Create a realistic wide-angle, landscape photo of a luxury modern loft bedroom from a corner, showing the whole room. A wool rug with abstract patterns, swirls, burgundy and earth colors, and small gold highlights lies at the foot of a sleek upholstered bed. The room has big windows, glass and metal furniture, and wood and concrete elements. Warm light from bedside lamps highlights the rug fibers. Softly blurred background shows the open space."
    },
    {
        "title": "Creative Loft Space",
        "prompt": "Full-room view creative loft with concrete walls, tall windows, sofa, small table, floor lamp, and bookshelf. Industrial style with modern décor. Natural and ambient lighting showing textures of concrete, wood, and fabrics. Furniture fully visible and well-arranged."
    },
    {
        "title": "Traditional Living Room",
        "prompt": "Full-room view traditional living room with wood-paneled walls, sofa, armchair, coffee table, and floor lamp. Warm ambient lighting. Furniture with realistic textures like polished wood, fabric upholstery, and soft lighting shadows. Show full floor and room layout."
    },
    {
        "title": "Large Hall Space",
        "prompt": "Full-room view large hall with neutral walls, long sofa, chairs, side tables, and lamps. Plenty of open floor space. Balanced lighting from ceiling lights and windows. Furniture should appear proportionate and realistic, with clear visibility of the entire room."
    },
    {
        "title": "Luxury Living Room",
        "prompt": "Full-room view modern luxury living room with floor-to-ceiling windows, gray textured walls, walnut wood flooring, a sleek L-shaped sofa, two velvet armchairs, glass coffee table, sculptural floor lamp, and a large abstract wall painting. Natural daylight fills the room while soft ambient lighting highlights furniture details. Include décor items like vases, books, and small sculptures. The layout should feel elegant, open, and perfectly balanced."
    },
    {
        "title": "Industrial Loft",
        "prompt": "Full-room view industrial loft with exposed brick walls, polished concrete flooring, large steel-framed windows, modern leather sofa, metal-frame lounge chair, reclaimed wood coffee table, tripod floor lamp, and a gallery wall of art. Include plants and textured textiles to soften the space. Lighting should mix natural daylight with subtle warm interior lights. The room should feel edgy, stylish, and inviting."
    },
    {
        "title": "Office Lounge",
        "prompt": "Full-room view contemporary office lounge with muted green accent walls, dark wood flooring, modular sofa, designer chairs, low coffee table, floor lamp with geometric design, and bookshelves with decorative objects. Include a tall potted plant and a wall-mounted artwork. Lighting combines daylight from a large window and warm overhead lighting. The space should feel modern, sophisticated, and professional yet welcoming."
    },
    {
        "title": "Creative Studio",
        "prompt": "Full-room view creative loft studio with exposed concrete walls, large industrial windows, polished wooden floors, modular sofa, low coffee table, artistic floor lamp, and an easel with artwork. Include abstract sculptures, books, and plants as décor. Lighting should blend natural daylight with subtle warm spotlights. The room should feel artistic, modern, and highly curated."
    },
    {
        "title": "Elegant Living Room",
        "prompt": "Full-room view traditional elegant living room with paneled walls, herringbone wood flooring, tufted sofa, two wingback chairs, wooden coffee table, floor lamp with warm light, and a large framed painting above the fireplace. Include decorative items such as vases, books, and candle holders. Lighting is warm and inviting, highlighting textures and patterns. The room should feel classic, stylish, and cozy."
    },
    {
        "title": "Spacious Modern Hall",
        "prompt": "Full-room view spacious modern hall with high ceilings, neutral textured walls, polished wooden flooring, minimalist seating area with contemporary chairs, console tables, floor lamps, and tall indoor plants. Add decorative vases and sculptures for visual interest. Soft daylight from tall windows combined with warm ambient lighting. The space should feel open, luxurious, and highly aesthetic."
    },
    {
        "title": "Minimalist Bedroom",
        "prompt": "Full-room view minimalist contemporary bedroom with matte white walls, light oak flooring, low-profile platform bed, matching bedside tables with sleek lamps, reading chair, and open shelving with decorative objects. Include subtle textures like a woven throw, cushions, and abstract wall art. Lighting blends natural daylight and soft ambient sources. The room should feel clean, stylish, and calming."
    },
    {
        "title": "Designer Living Room",
        "prompt": "Full-room view designer living room with soft gray walls, dark wood flooring, modular sofa, velvet armchair, glass coffee table, floor lamp with sculptural design, large abstract painting, and open shelves with decorative items. Include modern décor elements like plants, books, and sculptures. Lighting combines daylight and warm interior lights to highlight textures and create a balanced, luxurious look. The room should feel curated and visually stunning."
    }
]

class GenerateRequest(BaseModel):
    prompt: str
    resolution: str = "1024x1024"
    seed: int = 42
    steps: int = 9
    shift: float = 3.0
    random_seed: bool = True

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Serve the Z-Image UI."""
    return templates.TemplateResponse("index.html", {
        "request": request,
        "resolutions": RESOLUTIONS,
        "example_prompts": EXAMPLE_PROMPTS
    })

@app.get("/health")
async def health():
    """Health check endpoint."""
    try:
        response = requests.get(f"{ORCHESTRATOR_URL}/workers", timeout=5)
        orchestrator_healthy = response.status_code == 200
    except:
        orchestrator_healthy = False

    return {
        "status": "healthy",
        "orchestrator": "connected" if orchestrator_healthy else "disconnected"
    }

@app.post("/api/generate")
async def generate(request: GenerateRequest):
    """Submit Z-Image generation job."""
    import random

    # Handle random seed
    if request.random_seed:
        seed = random.randint(1, 1000000)
    else:
        seed = request.seed

    # Extract resolution (remove aspect ratio suffix if present)
    resolution = request.resolution.split(' ')[0] if ' ' in request.resolution else request.resolution

    try:
        response = requests.post(
            f"{ORCHESTRATOR_URL}/submit",
            json={
                "app_id": APP_ID,
                "params": {
                    "prompt": request.prompt,
                    "resolution": resolution,
                    "seed": str(seed),
                    "steps": str(request.steps),
                    "shift": str(request.shift)
                }
            },
            timeout=240  # 4 minutes to allow for model loading on cold start
        )

        if response.status_code == 200:
            data = response.json()
            return {
                "success": True,
                "job_id": data["job_id"],
                "status": data.get("status", "queued"),
                "seed": seed
            }
        else:
            return {
                "success": False,
                "error": "Orchestrator error"
            }

    except requests.exceptions.RequestException as e:
        return {
            "success": False,
            "error": f"Cannot connect to orchestrator: {str(e)}"
        }

@app.get("/api/status/{job_id}")
async def get_status(job_id: str):
    """Get job status from orchestrator."""
    try:
        response = requests.get(
            f"{ORCHESTRATOR_URL}/status/{job_id}",
            timeout=5
        )

        if response.status_code == 200:
            data = response.json()
            return {
                "success": True,
                "job_id": job_id,
                "status": data.get("status", "UNKNOWN"),
                "result": data.get("result"),
                "error": data.get("error_log"),
                "created_at": data.get("created_at"),
                "completed_at": data.get("completed_at")
            }
        else:
            return {
                "success": False,
                "error": "Job not found"
            }

    except requests.exceptions.RequestException as e:
        return {
            "success": False,
            "error": f"Cannot connect to orchestrator: {str(e)}"
        }

@app.get("/api/workers")
async def get_workers():
    """Get active workers from orchestrator."""
    try:
        response = requests.get(f"{ORCHESTRATOR_URL}/workers", timeout=5)
        if response.status_code == 200:
            return response.json()
        else:
            return {"count": 0, "workers": []}
    except:
        return {"count": 0, "workers": []}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7861)