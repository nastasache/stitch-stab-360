import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

"""Video operations, calibration, leveling, frame extraction, and preview rendering routes."""

import json
import time
import shutil
import subprocess
from pathlib import Path

from fastapi import APIRouter, Request, Query
from fastapi.responses import JSONResponse

from routers.common import (
    resolve_input_file,
    resolve_output_file,
    resolve_nadir_logo,
    is_valid_video_file,
    is_valid_image_file,
    is_valid_media_file,
    run_async_subprocess
)

router = APIRouter(tags=["videos"])

# ── Route: Videos — info ─────────────────────────────────────────────────────

@router.get("/api/v1/videos/info")
async def api_video_info(input: str = Query("")):
    """Probe video dimensions, frame count, FPS, and duration using ffprobe.

    Args:
        input: Video path or filename to inspect.

    Returns:
        JSONResponse containing extracted video stream metadata.
    """
    input_name = resolve_input_file(input)
    if not input_name or not os.path.exists(input_name):
        print(f"[WARN] video_info: file not found or rejected: {input!r}")
        return JSONResponse({"status": "error", "error": "Input file not found."}, status_code=404)

    if is_valid_image_file(input_name):
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "json",
            input_name
        ]
        stdout, _ = await run_async_subprocess(*cmd)
        try:
            data = json.loads(stdout.decode("utf-8", errors="ignore"))
            stream = data.get("streams", [{}])[0]
            w = int(stream.get("width", 3840))
            h = int(stream.get("height", 1920))
            is_vr_photo = bool(h > 0 and abs((w / h) - 2.0) <= 0.05)
            return JSONResponse({
                "status": "success",
                "duration": 0.0,
                "fps": 1.0,
                "total_frames": 1,
                "width": w,
                "height": h,
                "is_photo": True,
                "has_telemetry": False,
                "is_vr": is_vr_photo
            })
        except Exception:
            return JSONResponse({
                "status": "success",
                "duration": 0.0,
                "fps": 1.0,
                "total_frames": 1,
                "width": 3840,
                "height": 1920,
                "is_photo": True,
                "has_telemetry": False,
                "is_vr": True
            })

    cmd = [
        "ffprobe", "-v", "error",
        "-analyzeduration", "100000",
        "-probesize", "5000000",
        "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate,nb_frames,duration,width,height",
        "-of", "json",
        input_name
    ]
    stdout, _ = await run_async_subprocess(*cmd)
    try:
        data = json.loads(stdout.decode("utf-8", errors="ignore"))
        if data and "streams" in data and len(data["streams"]) > 0:
            stream = data["streams"][0]
            fps_parts = stream.get("r_frame_rate", "30/1").split("/")
            fps = float(fps_parts[0]) / float(fps_parts[1]) if len(fps_parts) == 2 and float(fps_parts[1]) > 0 else float(fps_parts[0])
            duration = float(stream.get("duration", 0.0))
            calc_frames = int(round(duration * fps))
            nb_f = int(stream.get("nb_frames", 0))
            total_frames = nb_f if nb_f > 0 else calc_frames
            if total_frames > calc_frames + 2:
                total_frames = calc_frames

            clean_base = Path(input_name).stem
            v_dir = os.path.dirname(input_name) or "."
            cand_files = [
                os.path.join(v_dir, f"{clean_base}_telemetry.txt"),
                os.path.join(v_dir, f"{clean_base}.gcsv"),
                f"data/runtime/work/{clean_base}_telemetry.txt",
                f"data/runtime/work/{clean_base}.gcsv"
            ]
            has_telemetry = any(os.path.exists(c) and os.path.getsize(c) > 50 for c in cand_files)
            if not has_telemetry and os.path.exists(input_name) and os.path.getsize(input_name) > 1000:
                try:
                    from utils.mp4_utils import find_box
                    file_sz = os.path.getsize(input_name)
                    with open(input_name, "rb") as f_in:
                        if find_box(f_in, 0, file_sz, ["moov", "udta", "vrot"]):
                            has_telemetry = True
                except Exception:
                    pass

            w_val = int(stream.get("width", 0))
            h_val = int(stream.get("height", 0))
            is_vr = False
            if w_val > 0 and h_val > 0 and abs((w_val / h_val) - 2.0) <= 0.05:
                is_vr = True
            if not is_vr and os.path.exists(input_name) and os.path.getsize(input_name) > 1000:
                try:
                    with open(input_name, "rb") as f_in:
                        head = f_in.read(250000)
                        if b"sv3d" in head or b"GSpherical" in head or b"st3d" in head or b"equirectangular" in head:
                            is_vr = True
                except Exception:
                    pass

            return JSONResponse({
                "status": "success",
                "duration": duration,
                "fps": fps,
                "total_frames": total_frames,
                "width": w_val,
                "height": h_val,
                "is_photo": False,
                "has_telemetry": has_telemetry,
                "is_vr": is_vr
            })
    except Exception as e:
        print(f"[WARN] video_info parse error: {str(e)}", file=sys.stderr)
        return JSONResponse({"status": "error", "error": "Failed to parse video metadata."}, status_code=500)

    return JSONResponse({"status": "error", "error": "Failed to retrieve video metadata."}, status_code=500)

# ── Route: Videos — check stitched ───────────────────────────────────────────

@router.get("/api/v1/videos/check-stitched")
async def api_check_stitched(input: str = Query(""), output: str = Query("")):
    """Verify whether a stitched equirectangular version of a video exists.

    Args:
        input: Original input video path.
        output: Destination output video path.

    Returns:
        JSONResponse indicating whether stitched media exists and its path.
    """
    input_name = resolve_input_file(input)
    output_name = resolve_output_file(output)
    found_file = ""

    if output_name:
        out_filename = Path(output_name).stem
        for ext in [Path(output_name).suffix.lstrip("."), "MP4", "mp4"]:
            stitched_target = f"data/runtime/work/{out_filename}_stitched.{ext}"
            if is_valid_video_file(stitched_target):
                found_file = stitched_target
                break
    if not found_file and input_name:
        in_filename = Path(input_name).stem
        for ext in [Path(input_name).suffix.lstrip("."), "MP4", "mp4"]:
            stitched_target = f"data/runtime/work/{in_filename}_stitched.{ext}"
            if is_valid_video_file(stitched_target):
                found_file = stitched_target
                break
        if not found_file and ("_stitched" in in_filename.lower() or "_equirect" in in_filename.lower()):
            if is_valid_video_file(input_name):
                found_file = input_name

    if found_file:
        return JSONResponse({"status": "success", "exists": True, "file": found_file})
    return JSONResponse({"status": "success", "exists": False})

# ── Route: Videos — inject metadata ──────────────────────────────────────────

@router.post("/api/v1/videos/inject-meta")
async def api_inject_meta(request: Request):
    """Inject spherical equirectangular 360 metadata into a video using spatialmedia.

    Args:
        request: FastAPI HTTP request with form data containing 'input' video path.

    Returns:
        JSONResponse indicating success status and path to output VR video.
    """
    form = await request.form()
    form_data = dict(form)
    raw_input = form_data.get("input", "")
    input_name = resolve_input_file(raw_input)
    if not input_name:
        return JSONResponse({"status": "error", "error": f"Input file does not exist: {raw_input}"}, status_code=404)

    p = Path(input_name)
    out_dir = f"{p.parent}/" if str(p.parent) != "." else ""
    output_file = f"{out_dir}{p.stem}_VR{p.suffix}"

    cmd = [sys.executable, "-B", "scripts/spatialmedia", "-i", "-p", "equirectangular", input_name, output_file]
    stdout, stderr = await run_async_subprocess(*cmd)
    output_details = (stdout + stderr).decode("utf-8", errors="ignore")

    if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
        os.makedirs("data/output", exist_ok=True)
        base_dir_abs = os.path.realpath(os.path.abspath(str(BASE_DIR)))
        out_dir_abs = os.path.realpath(os.path.abspath(os.path.join(base_dir_abs, "data", "output")))
        done_file_abs = os.path.realpath(os.path.abspath(os.path.join(out_dir_abs, os.path.basename(output_file))))
        if done_file_abs.startswith(out_dir_abs + os.sep) and os.path.abspath(output_file) != done_file_abs:
            try:
                shutil.copy2(output_file, done_file_abs)
            except OSError:
                pass
        return JSONResponse({
            "status": "success",
            "message": "360 metadata injected successfully.",
            "output": output_file,
            "details": output_details
        }, status_code=201)
    return JSONResponse({"status": "error", "error": "Metadata injection failed.", "details": output_details}, status_code=500)

# ── Route: Videos — crop ─────────────────────────────────────────────────────

@router.post("/api/v1/videos/crop")
async def api_crop_video(request: Request):
    """Trim a video by frame boundaries while preserving metadata and telemetry.

    Args:
        request: FastAPI HTTP request containing input, output, start_frame, and end_frame.

    Returns:
        JSONResponse confirming successful crop and output file path.
    """
    form = await request.form()
    form_data = dict(form)
    raw_input = form_data.get("input", "")
    input_name = resolve_input_file(raw_input)
    if not input_name:
        return JSONResponse({"status": "error", "error": f"Input file does not exist: {raw_input}"}, status_code=404)

    raw_output = form_data.get("output", "")
    if not raw_output:
        return JSONResponse({"status": "error", "error": "No output filename provided."}, status_code=400)

    base_output = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', os.path.basename(raw_output)).lstrip(".-")
    if not base_output:
        return JSONResponse({"status": "error", "error": "Invalid output filename."}, status_code=400)
    base_dir_abs = os.path.realpath(os.path.abspath(str(BASE_DIR)))
    in_vids_abs = os.path.realpath(os.path.abspath(os.path.join(base_dir_abs, "data", "input", "videos")))
    target_abs = os.path.realpath(os.path.abspath(os.path.join(in_vids_abs, base_output)))
    if not target_abs.startswith(in_vids_abs + os.sep):
        return JSONResponse({"status": "error", "error": "Invalid output destination."}, status_code=400)
    target_output = os.path.relpath(target_abs, base_dir_abs).replace("\\", "/")
    if os.path.abspath(target_output) == os.path.abspath(input_name):
        return JSONResponse({"status": "error", "error": "Output filename cannot overwrite source video."}, status_code=400)

    start_frame = int(form_data.get("start_frame", "0"))
    end_frame   = int(form_data.get("end_frame", "-1"))

    cmd = [sys.executable, "-B", "scripts/crop_camera_video.py", "--input", input_name, "--output", target_output, "--start_frame", str(start_frame), "--end_frame", str(end_frame)]
    cmd_text = subprocess.list2cmdline(cmd)
    stdout, stderr = await run_async_subprocess(*cmd)
    output_details = (stdout + stderr).decode("utf-8", errors="ignore")

    if os.path.exists(target_output) and os.path.getsize(target_output) > 0:
        return JSONResponse({"status": "success", "message": f"Cropped file saved successfully in data/input/videos/ as {base_output}", "output_file": target_output, "filename": base_output, "output": output_details, "command": cmd_text}, status_code=201)
    return JSONResponse({"status": "error", "error": "Cropping failed.", "details": output_details, "command": cmd_text}, status_code=500)

# ── Route: Videos — detect warmup ────────────────────────────────────────────

@router.post("/api/v1/videos/detect-warmup")
async def api_detect_warmup(request: Request):
    """Detect camera initialization/freeze warmup frames via optical flow analysis.

    Args:
        request: FastAPI HTTP request containing input video path and max_sec window.

    Returns:
        JSONResponse containing detected warmup frame count and duration.
    """
    form = await request.form()
    form_data = dict(form)
    raw_input = form_data.get("input", "")
    input_name = resolve_input_file(raw_input)
    if not input_name or not os.path.exists(input_name):
        return JSONResponse({"status": "error", "error": f"Input file does not exist: {raw_input}"}, status_code=404)

    max_sec = float(form_data.get("max_sec", "12.0"))
    if max_sec <= 0: max_sec = 12.0

    cmd = [sys.executable, "-B", "scripts/detect_warmup.py", "--input", input_name, "--max_sec", str(max_sec)]
    stdout, stderr = await run_async_subprocess(*cmd)
    output_str = stdout.decode("utf-8", errors="ignore").strip()

    try:
        data = json.loads(output_str)
        if isinstance(data, dict) and "status" in data:
            return JSONResponse(data)
    except Exception:
        pass
    return JSONResponse({"status": "error", "error": "Warm-up inspection failed.", "details": output_str or stderr.decode("utf-8", errors="ignore")}, status_code=500)

# ── Route: Videos — detect warmup (GET convenience) ──────────────────────────

@router.get("/api/v1/videos/detect-warmup")
async def api_detect_warmup_get(input: str = Query(""), max_sec: float = Query(12.0)):
    """GET convenience endpoint to inspect video warmup frames.

    Args:
        input: Video path or filename query parameter.
        max_sec: Maximum duration in seconds to scan from start.

    Returns:
        JSONResponse containing warmup detection results.
    """
    input_name = resolve_input_file(input)
    if not input_name or not os.path.exists(input_name):
        return JSONResponse({"status": "error", "error": f"Input file does not exist: {input}"}, status_code=404)
    if max_sec <= 0: max_sec = 12.0
    cmd = [sys.executable, "-B", "scripts/detect_warmup.py", "--input", input_name, "--max_sec", str(max_sec)]
    stdout, stderr = await run_async_subprocess(*cmd)
    output_str = stdout.decode("utf-8", errors="ignore").strip()
    try:
        data = json.loads(output_str)
        if isinstance(data, dict) and "status" in data:
            return JSONResponse(data)
    except Exception:
        pass
    return JSONResponse({"status": "error", "error": "Warm-up inspection failed.", "details": output_str or stderr.decode("utf-8", errors="ignore")}, status_code=500)

# ── Route: Videos — auto calibrate ───────────────────────────────────────────

@router.post("/api/v1/videos/auto-calibrate")
async def api_auto_calibrate(request: Request):
    """Automatically optimize dual-fisheye FOV, offset, and alignment parameters.

    Args:
        request: FastAPI HTTP request with calibration options.

    Returns:
        JSONResponse containing optimized calibration metrics.
    """
    return await _calibrate_handler(request, mode="auto_calibrate")

@router.post("/api/v1/videos/auto-level")
async def api_auto_level(request: Request):
    """Auto-level pitch and roll horizon tilt on dual-fisheye frames.

    Args:
        request: FastAPI HTTP request with video and timing parameters.

    Returns:
        JSONResponse containing leveled pitch, roll, and alignment offsets.
    """
    return await _calibrate_handler(request, mode="auto_level")

async def _calibrate_handler(request: Request, mode: str):
    """Internal delegator to execute auto_calibrate.py in auto_calibrate or auto_level mode.

    Args:
        request: FastAPI HTTP request containing form options.
        mode: Calibration operation mode ('auto_calibrate' or 'auto_level').

    Returns:
        JSONResponse with calibration results or failure details.
    """
    form = await request.form()
    form_data = dict(form)
    def gp(n, d=""): return form_data.get(n, d)

    raw_input = gp("input", "")
    input_name = resolve_input_file(raw_input)
    if not input_name or not os.path.exists(input_name):
        return JSONResponse({"status": "error", "error": f"Input file does not exist: {raw_input}"}, status_code=404)

    preview_time = max(0.0, float(gp("preview_time", "2.0")))
    os.makedirs("data/runtime/temp", exist_ok=True)
    input_base = Path(input_name).stem
    time_suffix = f"{preview_time:.3f}".replace(".", "_")
    frame_file  = f"data/runtime/temp/frame_{input_base}_at_{time_suffix}.jpg"

    if is_valid_image_file(input_name):
        frame_file = input_name
    else:
        if not os.path.exists(frame_file) or (os.path.exists(input_name) and os.path.getmtime(frame_file) < os.path.getmtime(input_name)) or os.path.getsize(frame_file) == 0:
            extract_cmd = ["ffmpeg", "-y", "-ss", str(preview_time), "-i", input_name, "-vframes", "1", frame_file]
            await run_async_subprocess(*extract_cmd)
            if not os.path.exists(frame_file) or os.path.getsize(frame_file) == 0:
                retry_time = max(0.0, preview_time - 0.15)
                await run_async_subprocess("ffmpeg", "-y", "-ss", str(retry_time), "-i", input_name, "-vframes", "1", frame_file)

        if not os.path.exists(frame_file) or os.path.getsize(frame_file) == 0:
            return JSONResponse({"status": "error", "error": "Failed to extract frame for calibration."}, status_code=500)

    calib_mode  = str(gp("calib_mode", gp("auto_calibrate_mode", "balanced")))
    if calib_mode not in ["foreground", "balanced", "infinity"]: calib_mode = "balanced"
    num_frames  = max(1, min(200, int(gp("num_frames", gp("auto_calibrate_frames", "5")))))
    ih_fov      = float(gp("ih_fov", "190.00"))
    left_y      = float(gp("left_y_offset", "0.00"))
    rear_roll   = float(gp("rear_roll_offset", "0.00"))

    ext = Path(input_name).suffix.lower().lstrip(".")
    is_video = ext in ["mp4", "mov", "mkv", "avi", "webm", "m4v"]

    if mode == "auto_level":
        if is_video:
            calib_cmd = [sys.executable, "-B", "scripts/auto_calibrate.py", "--video", input_name, "--preview_time", str(preview_time), "--num_frames", "1", "--auto_level", "--init_fov", str(ih_fov), "--init_left_y", str(left_y), "--init_rear_roll", str(rear_roll), "--json"]
        else:
            calib_cmd = [sys.executable, "-B", "scripts/auto_calibrate.py", "--image", frame_file, "--auto_level", "--init_fov", str(ih_fov), "--init_left_y", str(left_y), "--init_rear_roll", str(rear_roll), "--json"]
    else:
        if is_video:
            calib_cmd = [sys.executable, "-B", "scripts/auto_calibrate.py", "--video", input_name, "--preview_time", str(preview_time), "--num_frames", str(num_frames), "--mode", calib_mode, "--json"]
        else:
            calib_cmd = [sys.executable, "-B", "scripts/auto_calibrate.py", "--image", frame_file, "--mode", calib_mode, "--json"]

    stdout, stderr = await run_async_subprocess(*calib_cmd)
    output_str = stdout.decode("utf-8", errors="ignore")

    decoded = None
    first_brace = output_str.find("{")
    last_brace  = output_str.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace >= first_brace:
        try:
            decoded = json.loads(output_str[first_brace:last_brace+1])
        except Exception:
            pass

    if decoded and "status" in decoded:
        return JSONResponse(decoded)
    clean_output = output_str.strip() or stderr.decode("utf-8", errors="ignore") or "Unknown Python error"
    return JSONResponse({"status": "error", "error": f"Calibration failed: {clean_output}"}, status_code=500)

# ── Route: Videos — preview ───────────────────────────────────────────────────

@router.post("/api/v1/videos/preview")
async def api_preview(request: Request):
    """Render a stitched equirectangular still-frame preview with real-time warp parameters.

    Args:
        request: FastAPI HTTP request containing stitch geometry and alignment parameters.

    Returns:
        JSONResponse containing URLs to rendered stitched preview and extracted source frame.
    """
    form = await request.form()
    form_data = dict(form)
    def gp(n, d=""): return form_data.get(n, d)

    raw_input = gp("input", "")
    input_name = resolve_input_file(raw_input)
    if not input_name or not os.path.exists(input_name):
        return JSONResponse({"status": "error", "error": f"Input file does not exist: {raw_input}"}, status_code=404)

    ih_fov          = float(gp("ih_fov", "190.00"))
    iv_fov          = float(gp("iv_fov", "190.00"))
    raw_rotation    = float(gp("raw_rotation", "0"))
    yaw             = float(gp("yaw", "0"))
    pitch           = float(gp("pitch", "0"))
    roll            = float(gp("roll", "0"))
    left_y_offset   = int(float(gp("left_y_offset", "0")))
    rear_roll_offset= float(gp("rear_roll_offset", "0"))
    blend_seams     = (str(gp("blend_seams", "1")) == "1" or str(gp("blend_seams", "")).lower() == "true")
    anti_vignette   = (str(gp("anti_vignette", "0")) == "1" or str(gp("anti_vignette", "")).lower() == "true")
    anti_vignette_angle = float(gp("anti_vignette_angle", "0.785"))
    preview_time    = max(0.0, float(gp("preview_time", "2.0")))

    os.makedirs("data/runtime/temp", exist_ok=True)
    input_base  = Path(input_name).stem
    time_suffix = f"{preview_time:.3f}".replace(".", "_")
    frame_file  = f"data/runtime/temp/frame_{input_base}_at_{time_suffix}.jpg"

    if is_valid_image_file(input_name):
        frame_file = input_name
    else:
        if not os.path.exists(frame_file) or (os.path.exists(input_name) and os.path.getmtime(frame_file) < os.path.getmtime(input_name)) or os.path.getsize(frame_file) == 0:
            await run_async_subprocess("ffmpeg", "-y", "-ss", str(preview_time), "-i", input_name, "-vframes", "1", frame_file)

        if not os.path.exists(frame_file):
            retry_time = max(0.0, preview_time - 0.15)
            await run_async_subprocess("ffmpeg", "-y", "-ss", str(retry_time), "-i", input_name, "-vframes", "1", frame_file)

        if not os.path.exists(frame_file):
            await run_async_subprocess("ffmpeg", "-y", "-sseof", "-1", "-i", input_name, "-update", "1", "-vframes", "1", frame_file)

        if not os.path.exists(frame_file):
            return JSONResponse({"status": "error", "error": f"Failed to extract test frame from {input_name}"}, status_code=500)

    effective_yaw = yaw + raw_rotation
    while effective_yaw > 180: effective_yaw -= 360
    while effective_yaw < -180: effective_yaw += 360

    skip_stitching = (str(gp("skip_stitching", "0")) == "1" or str(gp("skip_stitching", "")).lower() == "true")
    preview_file   = "data/runtime/temp/preview.png"
    temp_preview   = f"data/runtime/temp/prev_{os.getpid()}_{int(time.time()*1000)%100000}.png"

    stitch_cmd = []
    if skip_stitching:
        if effective_yaw != 0.0 or pitch != 0.0 or roll != 0.0:
            filter_complex = f"[0:v]v360=input=equirect:output=equirect:yaw={effective_yaw}:pitch={pitch}:roll={roll}[final]"
            stitch_cmd = ["ffmpeg", "-y", "-i", frame_file, "-filter_complex", filter_complex, "-map", "[final]", "-vframes", "1", temp_preview]
        else:
            stitch_cmd = ["ffmpeg", "-y", "-i", frame_file, "-vframes", "1", temp_preview]
    else:
        raw_blend_width  = int(float(gp("blend_width", "200")))
        max_overlap_deg  = max(0.0, ih_fov - 180.0)
        max_overlap_px   = int(max_overlap_deg * 3840.0 / 360.0)
        blend_width_raw  = min(raw_blend_width, max_overlap_px)
        use_split_lenses = blend_seams or (abs(rear_roll_offset) > 0.001)

        if use_split_lenses:
            actual_blend_width = blend_width_raw if blend_seams else 0
            mask_file = f"data/runtime/temp/alpha_mask_{actual_blend_width}.png"
            if not os.path.exists(mask_file):
                await run_async_subprocess(sys.executable, "-B", "scripts/generate_alpha_mask.py", "--blend_width", str(actual_blend_width), "--output", mask_file)

            if os.path.exists(mask_file):
                yaw_l = float(effective_yaw)
                yaw_r = float(effective_yaw) + 180.0
                while yaw_l > 180: yaw_l -= 360
                while yaw_l < -180: yaw_l += 360
                while yaw_r > 180: yaw_r -= 360
                while yaw_r < -180: yaw_r += 360

                filter_complex = "[0:v]split[raw_l][raw_r];"
                if left_y_offset != 0:
                    abs_offset    = abs(left_y_offset)
                    pad_h         = 1920 + abs_offset
                    left_pad_y    = max(0, -left_y_offset)
                    left_recrop_y = max(0, left_y_offset)
                    filter_complex += f"[raw_l]crop=1920:1920:0:0,pad=1920:{pad_h}:0:{left_pad_y}:black,crop=1920:1920:0:{left_recrop_y}"
                else:
                    filter_complex += "[raw_l]crop=1920:1920:0:0"
                if anti_vignette:
                    filter_complex += f",vignette=mode=backward:angle={anti_vignette_angle},format=yuv420p"
                filter_complex += "[left];"
                filter_complex += "[raw_r]crop=1920:1920:1920:0"
                if anti_vignette:
                    filter_complex += f",vignette=mode=backward:angle={anti_vignette_angle},format=yuv420p"
                filter_complex += "[right];"
                filter_complex += f"[left]v360=input=fisheye:output=equirect:ih_fov={ih_fov}:iv_fov={iv_fov}:yaw={yaw_l}:pitch={pitch}:roll={roll}:w=3840:h=1920[eq_left];"
                roll_r = roll + rear_roll_offset
                filter_complex += f"[right]v360=input=fisheye:output=equirect:ih_fov={ih_fov}:iv_fov={iv_fov}:yaw={yaw_r}:pitch={pitch}:roll={roll_r}:w=3840:h=1920[eq_right];"
                filter_complex += f"[1:v]v360=input=equirect:output=equirect:yaw={effective_yaw}:pitch={pitch}:roll={roll},format=gray[mask_eq];"
                filter_complex += "[eq_right][mask_eq]alphamerge[right_alpha];"
                filter_complex += "[eq_left][right_alpha]overlay=format=yuv420[final]"
                stitch_cmd = ["ffmpeg", "-y", "-i", frame_file, "-i", mask_file, "-filter_complex", filter_complex, "-map", "[final]", "-vframes", "1", temp_preview]
            else:
                use_split_lenses = False

        if not use_split_lenses:
            yaw_dfisheye = float(effective_yaw) + 180.0
            while yaw_dfisheye > 180:  yaw_dfisheye -= 360
            while yaw_dfisheye < -180: yaw_dfisheye += 360

            if left_y_offset != 0:
                abs_offset    = abs(left_y_offset)
                pad_h         = 1920 + abs_offset
                left_pad_y    = max(0, -left_y_offset)
                left_recrop_y = max(0, left_y_offset)
                filter_complex = f"[0:v]split[a][b];[a]crop=1920:1920:0:0,pad=1920:{pad_h}:0:{left_pad_y}:black,crop=1920:1920:0:{left_recrop_y}[left];[b]crop=1920:1920:1920:0[right];[left][right]hstack[combined];[combined]v360=input=dfisheye:output=equirect:ih_fov={ih_fov}:iv_fov={iv_fov}:yaw={yaw_dfisheye}:pitch={pitch}:roll={roll}[eq]"
            else:
                filter_complex = f"[0:v]v360=input=dfisheye:output=equirect:ih_fov={ih_fov}:iv_fov={iv_fov}:yaw={yaw_dfisheye}:pitch={pitch}:roll={roll}[eq]"
            stitch_cmd = ["ffmpeg", "-y", "-i", frame_file, "-filter_complex", filter_complex, "-map", "[eq]", "-vframes", "1", temp_preview]

    cmd_text = subprocess.list2cmdline(stitch_cmd) if stitch_cmd else ""
    stdout, stderr = await run_async_subprocess(*stitch_cmd)

    nadir_enabled = (str(gp("nadir_enabled", "0")) == "1" or str(gp("nadir_enabled", "")).lower() == "true")
    if nadir_enabled and os.path.exists(temp_preview):
        nadir_logo_raw = resolve_nadir_logo(str(gp("nadir_logo", "logo_stei_circle.png")))
        if nadir_logo_raw and os.path.exists(nadir_logo_raw):
            nadir_fov   = float(gp("nadir_fov", "75"))
            nadir_fov_v = float(gp("nadir_fov_v", "75"))
            nadir_temp_out = f"data/runtime/temp/nadir_{os.getpid()}_{int(time.time()*1000)%100000}.png"
            nadir_filter   = f"[1:v]format=rgba[logo_rgba];[logo_rgba]v360=input=flat:output=equirect:ih_fov={nadir_fov}:iv_fov={nadir_fov_v}:pitch=90:yaw=0:roll=0:w=3840:h=1920[logo_eq];[0:v][logo_eq]overlay=0:0:format=auto"
            nadir_cmd = ["ffmpeg", "-y", "-i", temp_preview, "-i", nadir_logo_raw, "-filter_complex", nadir_filter, "-vframes", "1", nadir_temp_out]
            cmd_text = f"{cmd_text}\n{subprocess.list2cmdline(nadir_cmd)}" if cmd_text else subprocess.list2cmdline(nadir_cmd)
            await run_async_subprocess(*nadir_cmd)
            if os.path.exists(nadir_temp_out):
                temp_preview = nadir_temp_out

    if os.path.exists(temp_preview) and os.path.getsize(temp_preview) > 0:
        shutil.copy2(temp_preview, preview_file)
        cb = int(time.time() * 1000)
        return JSONResponse({"status": "success", "preview": f"{preview_file}?t={cb}", "original": f"{frame_file}?t={cb}", "command": cmd_text})
    clean_err = (stdout + stderr).decode("utf-8", errors="ignore")
    return JSONResponse({"status": "error", "error": "FFmpeg failed to stitch preview frame.", "details": clean_err, "command": cmd_text}, status_code=500)
