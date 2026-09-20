import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

"""Preset management routes for listing, loading, and saving configuration profiles."""

import glob
import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from config.settings import BASE_DIR
from routers.common import sanitize_preset_filename

router = APIRouter(tags=["presets"])

# ── Route: Presets (/api/v1/presets) ─────────────────────────────────────────

def _presets_dirs():
    """Ensure and return the user presets storage directory.

    Returns:
        Tuple containing (user_presets_dir, None).
    """
    user_presets_dir = BASE_DIR / "data" / "input" / "presets"
    user_presets_dir.mkdir(parents=True, exist_ok=True)
    return user_presets_dir, None

@router.get("/api/v1/presets")
async def api_list_presets():
    """List all saved configuration presets in data/input/presets/.

    Returns:
        JSONResponse containing filenames and metadata of saved presets.
    """
    user_presets_dir, _ = _presets_dirs()
    user_files = glob.glob(str(user_presets_dir / "*.json"))
    seen_names = set()
    preset_list = []
    for file_path in user_files:
        base = os.path.basename(file_path)
        if base in seen_names:
            continue
        seen_names.add(base)
        is_pipeline = False
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                if data.get("type") in ["pipeline_config", "gear360_pipeline_config"]:
                    is_pipeline = True
                elif any(k in data for k in ["ih_fov", "enable_stitching", "stabilize"]):
                    is_pipeline = True
        except Exception:
            pass
        preset_list.append({
            "filename": base,
            "is_pipeline": is_pipeline,
            "modified": os.path.getmtime(file_path) if os.path.exists(file_path) else 0
        })
    return JSONResponse({"success": True, "presets": preset_list})

@router.get("/api/v1/presets/{filename}")
async def api_load_preset(filename: str):
    """Load configuration dictionary from a specified preset JSON file.

    Args:
        filename: Name of the preset file to read.

    Returns:
        JSONResponse containing the parsed JSON configuration data.
    """
    user_presets_dir, _ = _presets_dirs()
    safe_name = sanitize_preset_filename(filename)
    base_presets_abs = os.path.realpath(os.path.abspath(str(user_presets_dir)))
    target_abs = os.path.realpath(os.path.abspath(os.path.join(base_presets_abs, safe_name)))
    if not target_abs.startswith(base_presets_abs + os.sep) and target_abs != base_presets_abs:
        return JSONResponse({"success": False, "error": f"Invalid preset filename: {safe_name}"}, status_code=400)
    if not os.path.exists(target_abs) or not os.path.isfile(target_abs):
        return JSONResponse({"success": False, "error": f"File not found: {safe_name}"}, status_code=404)
    try:
        with open(target_abs, "r", encoding="utf-8") as f:
            data = json.load(f)
        return JSONResponse({"success": True, "filename": safe_name, "data": data})
    except Exception as e:
        print(f"[WARN] preset load error: {str(e)}", file=sys.stderr)
        return JSONResponse({"success": False, "error": "Failed to read preset file."}, status_code=500)

@router.post("/api/v1/presets")
async def api_save_preset(request: Request):
    """Save or update a pipeline configuration preset JSON file.

    Args:
        request: FastAPI HTTP request carrying JSON or form preset data.

    Returns:
        JSONResponse confirming preset file creation.
    """
    user_presets_dir, _ = _presets_dirs()
    content_type = request.headers.get("content-type", "")
    json_body = {}
    form_data = {}
    if "application/json" in content_type:
        try:
            json_body = await request.json()
        except Exception:
            json_body = {}
    else:
        try:
            form = await request.form()
            form_data = dict(form)
        except Exception:
            form_data = {}

    filename = json_body.get("filename") or json_body.get("name") or form_data.get("filename") or form_data.get("name") or ""
    config_data = json_body.get("config") or json_body.get("data") or form_data.get("config") or form_data.get("data")

    if isinstance(config_data, str):
        try:
            config_data = json.loads(config_data)
        except Exception:
            pass

    if not filename:
        return JSONResponse({"success": False, "error": "No filename provided"}, status_code=400)
    if not config_data:
        return JSONResponse({"success": False, "error": "No configuration data provided"}, status_code=400)

    safe_name = sanitize_preset_filename(filename)
    base_presets_abs = os.path.realpath(os.path.abspath(str(user_presets_dir)))
    target_abs = os.path.realpath(os.path.abspath(os.path.join(base_presets_abs, safe_name)))
    if not target_abs.startswith(base_presets_abs + os.sep) and target_abs != base_presets_abs:
        return JSONResponse({"success": False, "error": "Invalid preset filename"}, status_code=400)

    if isinstance(config_data, dict):
        config_data["type"] = "pipeline_config"
        config_data["saved_at"] = datetime.now().isoformat()

    json_str = json.dumps(config_data, indent=2, ensure_ascii=False) + "\n"
    with open(target_abs, "w", newline="\n", encoding="utf-8") as f:
        f.write(json_str)

    return JSONResponse({"success": True, "filename": safe_name, "message": f"Saved {safe_name} successfully"}, status_code=201)
