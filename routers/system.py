import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

"""System diagnostics, dashboard serving, and media input enumeration routes."""

import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

from config.settings import BASE_DIR
from routers.common import (
    render_dashboard_html,
    get_input_videos_list,
    get_nadir_logos_list,
    get_gpx_files_list
)
from utils.tool_resolver import (
    resolve_ffmpeg,
    resolve_ffprobe,
    resolve_exiftool,
    check_python_environment
)

router = APIRouter(tags=["system"])

# ── Route: Dashboard Index ───────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
@router.get("/index.html", response_class=HTMLResponse)
async def api_dashboard():
    """Serve the primary interactive web dashboard UI.

    Returns:
        HTMLResponse containing the dynamically populated index.html.
    """
    return HTMLResponse(content=render_dashboard_html(), status_code=200)

# ── Route: System Health Check ───────────────────────────────────────────────

@router.get("/api/v1/system/health")
async def api_system_health():
    """Perform diagnostic health check of external dependencies, storage, and runtime.

    Returns:
        JSONResponse with tool availability, versions, warnings, and remediation commands.
    """
    python_info = check_python_environment()
    ffmpeg_info = resolve_ffmpeg()
    ffprobe_info = resolve_ffprobe(ffmpeg_info.get("path"))
    exiftool_info = resolve_exiftool()

    health: Dict[str, Any] = {
        "status": "ready",
        "platform": sys.platform,
        "python": python_info,
        "ffmpeg": ffmpeg_info,
        "ffprobe": ffprobe_info,
        "exiftool": exiftool_info,
        "disk": {
            "path": str(BASE_DIR),
            "free_gb": 0.0,
            "total_gb": 0.0,
            "status": "ok",
            "message": ""
        },
        "recommendations": []
    }

    # Collect recommendations for failed or sub-optimal dependencies
    if python_info["status"] != "ok":
        if python_info.get("missing_packages"):
            health["recommendations"].append(f"Install Python dependencies: python -B -m pip install -r requirements.txt")
        else:
            health["recommendations"].append("Upgrade Python to 3.10+ (Python 3.12 recommended)")

    if ffmpeg_info["status"] != "ok":
        if sys.platform.startswith("win"):
            health["recommendations"].append("Install FFmpeg Full Build: winget install Gyan.FFmpeg")
        elif sys.platform == "darwin":
            health["recommendations"].append("Install FFmpeg: brew install ffmpeg")
        else:
            health["recommendations"].append("Install FFmpeg: sudo apt install ffmpeg")

    if ffprobe_info["status"] != "ok":
        if sys.platform.startswith("win"):
            health["recommendations"].append("Install FFprobe (bundled with Gyan.FFmpeg)")

    if exiftool_info["status"] != "ok":
        if sys.platform.startswith("win"):
            health["recommendations"].append("Install ExifTool: winget install OliverBetz.ExifTool")
        elif sys.platform == "darwin":
            health["recommendations"].append("Install ExifTool: brew install exiftool")
        else:
            health["recommendations"].append("Install ExifTool: sudo apt install libimage-exiftool-perl")

    # 4. Check Disk Space
    try:
        data_dir = BASE_DIR / "data"
        if not data_dir.exists():
            data_dir = BASE_DIR
        usage = shutil.disk_usage(data_dir)
        free_gb = round(usage.free / (1024 ** 3), 1)
        total_gb = round(usage.total / (1024 ** 3), 1)
        health["disk"]["free_gb"] = free_gb
        health["disk"]["total_gb"] = total_gb
        if free_gb < 5.0:
            health["disk"]["status"] = "error"
            health["disk"]["message"] = f"Critical storage: only {free_gb} GB free of {total_gb} GB."
            health["recommendations"].append("Free disk space on project drive (under 5 GB remaining).")
        elif free_gb < 15.0:
            health["disk"]["status"] = "warning"
            health["disk"]["message"] = f"Low storage: {free_gb} GB free of {total_gb} GB (20+ GB recommended)."
        else:
            health["disk"]["status"] = "ok"
            health["disk"]["message"] = f"{free_gb} GB free of {total_gb} GB."
    except Exception as e:
        health["disk"]["status"] = "warning"
        health["disk"]["message"] = f"Could not check disk usage: {e}"

    # Overall aggregate status
    statuses = [
        health["python"]["status"],
        health["ffmpeg"]["status"],
        health["ffprobe"]["status"],
        health["exiftool"]["status"],
        health["disk"]["status"]
    ]
    if "error" in statuses:
        health["status"] = "error"
    elif "warning" in statuses:
        health["status"] = "warning"
    else:
        health["status"] = "ready"

    return JSONResponse(health)

# ── Route: Inputs / Logos / GPX listing ──────────────────────────────────────

@router.get("/api/v1/inputs")
async def api_list_inputs():
    """Retrieve the list of available input video files in data/input/videos/.

    Returns:
        JSONResponse containing a list of video file objects.
    """
    return JSONResponse({"status": "success", "items": get_input_videos_list()})

@router.get("/api/v1/logos")
async def api_list_logos():
    """Retrieve the list of available nadir patch logos in data/input/nadir/.

    Returns:
        JSONResponse containing a list of logo file objects.
    """
    return JSONResponse({"status": "success", "items": get_nadir_logos_list()})

@router.get("/api/v1/gpx")
async def api_list_gpx():
    """Retrieve the list of available GPX GPS track files in data/input/gps/.

    Returns:
        JSONResponse containing a list of GPX file objects.
    """
    return JSONResponse({"status": "success", "items": get_gpx_files_list()})
