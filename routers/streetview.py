import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

"""Google Street View CAMM export, GPX trajectory mapping, and Leaflet preview routes."""

import json
import time
import shutil
import subprocess
import traceback
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
import re

from fastapi import APIRouter, Request, Query
from fastapi.responses import JSONResponse

from config.settings import BASE_DIR
from routers.common import (
    resolve_input_file,
    resolve_gpx_file,
    run_async_subprocess
)

router = APIRouter(tags=["streetview"])

# ── Route: Street View — export ───────────────────────────────────────────────

@router.post("/api/v1/streetview/export")
async def api_export_streetview(request: Request):
    """Export 360 video with synced GPX track and interactive Leaflet HTML map.

    Args:
        request: FastAPI HTTP request with Street View export parameters.

    Returns:
        JSONResponse containing export status and output paths for video, GPX, and HTML map.
    """
    form = await request.form()
    form_data = dict(form)

    def gp(name, default=""):
        return form_data.get(name, default)

    raw_input = gp("input", "")
    if str(raw_input).strip().startswith("-"):
        return JSONResponse({"status": "error", "error": f"Invalid input video path: {raw_input}"}, status_code=400)
    input_name = resolve_input_file(raw_input)
    if not input_name:
        return JSONResponse({"status": "error", "error": f"Input video file does not exist: {raw_input}"}, status_code=404)

    additional_videos_raw = gp("additional_videos", "")
    input_videos_list = [input_name]
    if additional_videos_raw:
        raw_list = [v.strip() for v in str(additional_videos_raw).split(",") if v.strip()]
        for add_v in raw_list:
            res_v = resolve_input_file(add_v)
            if res_v and res_v not in input_videos_list:
                input_videos_list.append(res_v)

    p = Path(input_name)
    clean_stem = p.stem
    os.makedirs("data/runtime/work", exist_ok=True)
    os.makedirs("data/output", exist_ok=True)

    output_video = f"data/runtime/work/{clean_stem}_streetview{p.suffix}"
    output_gpx   = f"data/runtime/work/{clean_stem}_streetview.gpx"
    output_map   = f"data/runtime/work/{clean_stem}_streetview_map.html"

    sv_mode         = str(gp("streetview_mode", "A"))
    sv_checkpoints  = str(gp("streetview_checkpoints", ""))
    raw_sv_gpx      = resolve_gpx_file(str(gp("streetview_gpx_path", "")))
    sv_start_time   = str(gp("streetview_start_time", ""))
    sv_time_offset  = float(gp("streetview_time_offset", "0"))
    sv_auto_pad     = str(gp("streetview_auto_pad", "1"))
    sv_bitrate      = str(gp("streetview_bitrate", "45M"))
    sv_strip_audio  = str(gp("streetview_strip_audio", "1"))
    sv_start_coord  = str(gp("streetview_start_coord", ""))
    sv_end_coord    = str(gp("streetview_end_coord", ""))

    if sv_mode == "A":
        if not sv_checkpoints.strip():
            return JSONResponse({"status": "error", "error": "Street View Mode A requires checkpoints. Please enter coordinates in Map Checkpoints before starting."}, status_code=400)
    elif sv_mode == "B":
        if not raw_sv_gpx or not os.path.exists(raw_sv_gpx):
            return JSONResponse({"status": "error", "error": "Street View Mode B requires a valid GPX log file. Please select an existing GPX file before starting."}, status_code=400)

    job_id_raw = str(gp("job_id", ""))
    job_id = re.sub(r'[^a-zA-Z0-9_\-]', '', job_id_raw)
    status_file = f"data/runtime/temp/status_{job_id}.json" if job_id else "data/runtime/temp/status.json"
    os.makedirs("data/runtime/temp", exist_ok=True)
    os.makedirs("data/runtime/logs", exist_ok=True)

    init_status = {"status": "exporting", "phase": "Exporting Google Street View directly from input video...", "progress": 5.0, "speed": "N/A", "eta": "Calculating...", "elapsed": 0, "output": output_video}
    try:
        with open(status_file, "w", encoding="utf-8") as f_st:
            json.dump(init_status, f_st)
    except Exception as e:
        print(f"[WARN] streetview export: failed writing init status to {status_file!r}: {e}", file=sys.stderr)

    inputs_arg = ",".join(input_videos_list)
    cmd = [
        sys.executable, "-B", "scripts/streetview_gpx.py",
        "--input", inputs_arg, "--output", output_video, "--gpx-output", output_gpx,
        "--output-map", output_map, "--mode", sv_mode,
        "--time-offset", str(sv_time_offset), "--bitrate", sv_bitrate,
        "--status-file", status_file
    ]
    if sv_strip_audio == "0":
        cmd.append("--keep-audio")
    else:
        cmd.append("--strip-audio")
    if sv_mode == "A" and sv_checkpoints:
        cmd.extend(["--checkpoints", sv_checkpoints])
    elif sv_mode == "B" and raw_sv_gpx:
        cmd.extend(["--gpx-file", raw_sv_gpx])
    if sv_start_coord:
        cmd.extend(["--start-coord", sv_start_coord])
    if sv_end_coord:
        cmd.extend(["--end-coord", sv_end_coord])
    if str(gp("streetview_smooth_gps", "1")) == "1":
        cmd.append("--smooth-gps")
    if sv_start_time:
        cmd.extend(["--start-time", sv_start_time])
    if sv_auto_pad == "0":
        cmd.append("--no-auto-pad")

    cmd_text = subprocess.list2cmdline(cmd)
    stdout, stderr = await run_async_subprocess(*cmd)
    output_details = (stdout + stderr).decode("utf-8", errors="ignore")

    log_path = f"data/runtime/logs/pipeline_{job_id}.log" if job_id else "data/runtime/logs/pipeline.log"
    try:
        with open(log_path, "a", encoding="utf-8") as f_log:
            f_log.write(f"\n--- [StreetView Standalone Export] ---\n{output_details}\n")
    except Exception as e:
        print(f"[WARN] streetview export: failed writing log to {log_path!r}: {e}", file=sys.stderr)

    if os.path.exists(output_video) and os.path.getsize(output_video) > 0 and os.path.exists(output_gpx):
        done_video = f"data/output/{clean_stem}_streetview{p.suffix}"
        done_gpx   = f"data/output/{clean_stem}_streetview.gpx"
        done_map   = f"data/output/{clean_stem}_streetview_map.html"
        done_preview = "data/output/preview_map.html"
        try:
            if os.path.abspath(output_video) != os.path.abspath(done_video): shutil.copy2(output_video, done_video)
            if os.path.abspath(output_gpx)   != os.path.abspath(done_gpx):   shutil.copy2(output_gpx, done_gpx)
        except OSError:
            pass
        if os.path.exists(output_map):
            if os.path.abspath(output_map) != os.path.abspath(done_map):
                try: shutil.copy2(output_map, done_map)
                except OSError: pass
            try: shutil.copy2(output_map, done_preview)
            except Exception: pass
        return JSONResponse({
            "status": "success",
            "message": "Google Street View files generated successfully!",
            "output_video": done_video, "output_gpx": done_gpx,
            "output_map": done_map, "map_url": f"data/output/{clean_stem}_streetview_map.html",
            "details": output_details,
            "command": cmd_text
        }, status_code=201)

    err_status = {"status": "failed", "phase": "Google Street View export failed", "error": "Failed to generate Street View files.", "progress": 0.0, "speed": "N/A", "eta": "N/A", "output": output_video}
    try:
        with open(status_file, "w", encoding="utf-8") as f_st:
            json.dump(err_status, f_st)
    except Exception as e:
        print(f"[WARN] streetview export: failed writing error status to {status_file!r}: {e}", file=sys.stderr)
    return JSONResponse({"status": "error", "error": "Failed to generate Street View files.", "details": output_details}, status_code=500)

# ── Route: GPX — match coords ─────────────────────────────────────────────────

@router.post("/api/v1/gpx/match-coords")
async def api_match_gpx_coords(request: Request):
    """Match start and end latitude/longitude coordinates against GPX trackpoints.

    Args:
        request: FastAPI HTTP request containing GPX path and coordinate strings.

    Returns:
        JSONResponse containing matched point indices, timestamps, and offset durations.
    """
    form = await request.form()
    form_data = dict(form)
    raw_gpx = resolve_gpx_file(str(form_data.get("gpx_path", "")))
    if not raw_gpx or not os.path.exists(raw_gpx):
        return JSONResponse({"status": "error", "error": "GPX file not found."}, status_code=404)

    start_coord_str = str(form_data.get("start_coord", ""))
    end_coord_str   = str(form_data.get("end_coord", ""))

    scripts_dir = str(BASE_DIR / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from streetview_gpx import parse_gpx_file, find_closest_gpx_point, parse_coord_str

    pts = parse_gpx_file(raw_gpx)
    if not pts:
        return JSONResponse({"status": "error", "error": "No valid trackpoints found in GPX file."}, status_code=422)

    gpx_min_t = pts[0].get('dt')
    res_payload = {"status": "success", "total_points": len(pts), "gpx_start_time": pts[0].get('time_str')}

    start_pair = parse_coord_str(start_coord_str)
    if start_pair:
        p_start, idx_start, dist_start = find_closest_gpx_point(pts, start_pair[0], start_pair[1])
        if p_start and p_start.get('dt'):
            offset_sec = (p_start['dt'] - gpx_min_t).total_seconds() if gpx_min_t else 0.0
            res_payload["matched_start"] = {"index": idx_start, "time": p_start.get('time_str'), "offset_sec": round(offset_sec, 1), "distance_m": round(dist_start, 2), "lat": p_start['lat'], "lon": p_start['lon']}

    end_pair = parse_coord_str(end_coord_str)
    if end_pair:
        p_end, idx_end, dist_end = find_closest_gpx_point(pts, end_pair[0], end_pair[1])
        if p_end and p_end.get('dt'):
            res_payload["matched_end"] = {"index": idx_end, "time": p_end.get('time_str'), "distance_m": round(dist_end, 2), "lat": p_end['lat'], "lon": p_end['lon']}
            if start_pair and p_start and p_start.get('dt'):
                res_payload["span_duration_sec"] = round((p_end['dt'] - p_start['dt']).total_seconds(), 1)

    return JSONResponse(res_payload)

# ── Route: Street View — preview map ─────────────────────────────────────────

@router.post("/api/v1/streetview/preview-map")
async def api_preview_streetview_map(request: Request):
    """Generate an instant interactive HTML preview map of the Street View route.

    Args:
        request: FastAPI HTTP request containing Mode A/B route configuration.

    Returns:
        JSONResponse with preview map URL, point count, duration, and start timestamp.
    """
    try:
        form = await request.form()
        form_data = dict(form)
        def gp(n, d=""): return form_data.get(n, d)

        scripts_dir = str(BASE_DIR / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import streetview_gpx

        raw_input = gp("input", "")
        if str(raw_input).strip().startswith("-"):
            return JSONResponse({"status": "error", "error": f"Invalid input file: {raw_input}"}, status_code=400)
        input_name = resolve_input_file(raw_input) if raw_input else ""

        sv_mode         = str(gp("streetview_mode", "A"))
        sv_checkpoints  = str(gp("streetview_checkpoints", ""))
        sv_gpx_path     = resolve_gpx_file(str(gp("streetview_gpx_path", "")))
        sv_start_coord  = str(gp("streetview_start_coord", ""))
        sv_end_coord    = str(gp("streetview_end_coord", ""))
        sv_time_offset  = float(gp("streetview_time_offset", "0"))
        sv_start_time   = str(gp("streetview_start_time", ""))
        sv_smooth_gps   = str(gp("streetview_smooth_gps", "0")) in ["1", "true", "True"]
        sv_auto_pad     = str(gp("streetview_auto_pad", "1")) in ["1", "true", "True"]

        video_dur = 0.0
        if input_name and not input_name.startswith("-") and os.path.exists(input_name):
            try:
                probe_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", "--", os.path.abspath(input_name)]
                stdout, stderr = await asyncio.wait_for(run_async_subprocess(*probe_cmd), timeout=5.0)
                stdout_str = stdout.decode("utf-8", errors="ignore").strip()
                if stdout_str:
                    video_dur = float(stdout_str)
            except Exception as e:
                print(f"[WARN] route preview: ffprobe duration probe failed for {input_name!r}: {e}", file=sys.stderr)

        start_utc_dt = None
        if sv_start_time and sv_start_time != "auto":
            start_utc_dt = streetview_gpx.parse_iso_or_utc(sv_start_time)
        if not start_utc_dt and input_name and not input_name.startswith("-") and os.path.exists(input_name):
            start_utc_dt = streetview_gpx.extract_video_creation_time_utc(input_name)
        if not start_utc_dt:
            start_utc_dt = datetime.now(timezone.utc)

        pts = []
        if sv_mode == "A":
            cps = streetview_gpx.parse_checkpoint_list(sv_checkpoints)
            if not cps:
                return JSONResponse({"status": "error", "error": "No valid checkpoints found in Mode A text."}, status_code=422)
            max_cp_time = max((cp.get('time', 0.0) for cp in cps), default=0.0)
            dur = video_dur if video_dur > 0 else (max_cp_time if max_cp_time > 0 else 120.0)
            if sv_auto_pad and dur < 120.0: dur = 126.0
            pts = streetview_gpx.interpolate_checkpoints(cps, int(dur))
        else:
            if not sv_gpx_path or not os.path.exists(sv_gpx_path):
                return JSONResponse({"status": "error", "error": "Please select a valid GPX Log File for Mode B."}, status_code=422)
            gpx_points = streetview_gpx.parse_gpx_file(sv_gpx_path)
            if not gpx_points:
                return JSONResponse({"status": "error", "error": f"Failed to parse trackpoints from GPX file: {sv_gpx_path}"}, status_code=422)

            valid_dt_pts = [p for p in gpx_points if p.get('dt') is not None]
            gpx_dur = (valid_dt_pts[-1]['dt'] - valid_dt_pts[0]['dt']).total_seconds() if len(valid_dt_pts) >= 2 else (len(gpx_points) - 1 if len(gpx_points) > 1 else 120.0)
            dur = video_dur if video_dur > 0 else gpx_dur
            if sv_auto_pad and dur < 120.0: dur = 126.0

            start_pair = streetview_gpx.parse_coord_str(sv_start_coord) if sv_start_coord else None
            if start_pair:
                match_p, match_idx, _ = streetview_gpx.find_closest_gpx_point(gpx_points, start_pair[0], start_pair[1])
                if match_p and match_p.get('dt'):
                    start_utc_dt = match_p['dt']
                    if sv_time_offset != 0.0:
                        start_utc_dt += timedelta(seconds=sv_time_offset)
            elif not (sv_start_time and sv_start_time != "auto") and valid_dt_pts:
                gpx_min_t = valid_dt_pts[0]['dt']
                gpx_max_t = valid_dt_pts[-1]['dt']
                if start_utc_dt < gpx_min_t or start_utc_dt > gpx_max_t:
                    start_utc_dt = gpx_min_t
                    if sv_time_offset != 0.0:
                        start_utc_dt += timedelta(seconds=sv_time_offset)

            pts = streetview_gpx.sync_gpx_by_timestamps(gpx_points, start_utc_dt, int(dur))

        if not pts:
            return JSONResponse({"status": "error", "error": "Could not generate trackpoints from provided inputs."}, status_code=422)

        if sv_smooth_gps:
            pts = streetview_gpx.smooth_track_points(pts)

        os.makedirs("data/output", exist_ok=True)
        preview_map_file = "data/output/preview_map.html"
        streetview_gpx.build_map_html(pts, start_utc_dt, preview_map_file, title="Instant Route Preview")

        return JSONResponse({"status": "success", "map_url": preview_map_file, "points_count": len(pts), "duration_sec": len(pts) - 1, "start_utc": start_utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")})
    except Exception as e:
        print(f"[WARN] route preview error: {str(e)}", file=sys.stderr)
        return JSONResponse({"status": "error", "error": "Failed to generate route preview."}, status_code=500)

# ── Route: Street View — trace trajectory ─────────────────────────────────────

@router.post("/api/v1/streetview/trace-trajectory")
async def api_trace_trajectory(request: Request):
    """Extract dead-reckoning trajectory checkpoints via visual odometry.

    Args:
        request: FastAPI HTTP request with video input and geographic parameters.

    Returns:
        JSONResponse containing generated checkpoints, distance, and duration metrics.
    """
    try:
        form = await request.form()
        form_data = dict(form)
        def gp(n, d=""): return form_data.get(n, d)

        scripts_dir = str(BASE_DIR / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import visual_odometry

        raw_input = gp("input", "")
        input_name = resolve_input_file(raw_input) if raw_input else ""
        if not input_name or not os.path.exists(input_name):
            return JSONResponse({"status": "error", "error": f"Please select a valid input video file: {raw_input}"}, status_code=404)

        res = await asyncio.to_thread(
            visual_odometry.extract_video_trajectory,
            video_path=input_name,
            start_lat=float(gp("start_lat", "0.0")),
            start_lon=float(gp("start_lon", "0.0")),
            initial_heading_deg=float(gp("initial_heading", "0.0")),
            walking_speed_mps=float(gp("walking_speed", "1.15")),
            checkpoint_interval_sec=int(gp("checkpoint_interval", "10")),
            start_ele=float(gp("start_ele", "315.0"))
        )
        return JSONResponse({"status": "success", "checkpoints": res["checkpoints"], "total_distance_m": res["total_distance_m"], "net_displacement_m": res["net_displacement_m"], "duration_sec": res["duration_sec"], "points_count": res["points_count"]})
    except Exception as e:
        traceback.print_exc()
        return JSONResponse({"status": "error", "error": "Visual odometry processing failed."}, status_code=500)

# ── Route: Street View — find map ─────────────────────────────────────────────

@router.get("/api/v1/streetview/find-map")
async def api_find_streetview_map(input: str = Query("")):
    """Check if an exported Street View HTML map exists for the given video input.

    Args:
        input: Video filename or path query parameter.

    Returns:
        JSONResponse indicating whether the map file exists and its URL.
    """
    base_stem = Path(input).stem if input else ""
    if not base_stem:
        return JSONResponse({"status": "error", "error": "No input provided"}, status_code=400)

    target_map = f"data/output/{base_stem}_streetview_map.html"
    if os.path.exists(target_map):
        return JSONResponse({"status": "success", "map_url": target_map})

    return JSONResponse({"status": "not_found", "map_url": None, "message": f"Map not found for input: {base_stem}"}, status_code=200)
