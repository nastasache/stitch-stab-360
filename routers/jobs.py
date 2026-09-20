import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

"""Background job lifecycle, pipeline orchestration, checkpoints, and reports routes."""

import re
import glob
import json
import time
import shutil
import asyncio
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
import psutil

from fastapi import APIRouter, Request, Response, Form, Query, HTTPException
from fastapi.responses import JSONResponse, FileResponse

from config.settings import BASE_DIR, PATHS, SERVER_CONFIG, PIPELINE_DEFAULTS, DEBUG
from routers.common import (
    MissingParameterError,
    require_param,
    resolve_input_file,
    resolve_output_file,
    resolve_nadir_logo,
    resolve_gpx_file,
    is_valid_video_file,
    is_valid_image_file,
    is_valid_media_file,
    resolve_video_version,
    kill_process_tree,
    run_async_subprocess,
    spawn_background_process,
    safe_number,
    safe_choice,
    sanitize_cmd_arg,
    _managed_pids,
    save_managed_pids,
    get_active_job,
    check_disk_space,
    safe_job_id,
    get_status_file_path,
    get_log_file_path,
    resolve_runtime_file,
    safe_runtime_write_path
)

router = APIRouter(tags=["jobs"])

# Async lock to protect sequential run counter increments
_counter_lock = asyncio.Lock()


@router.get("/api/v1/jobs/next-prefix")
async def api_get_next_prefix():
    """Atomically generate and advance the sequential run prefix counter.

    Returns:
        JSONResponse containing the next 4-digit prefix integer.
    """
    async with _counter_lock:
        counter_file = BASE_DIR / "config" / "run_counter.txt"
        val = 1001
        if counter_file.exists():
            try:
                content = counter_file.read_text(encoding="utf-8").strip()
                if content.isdigit():
                    val = int(content)
            except Exception:
                val = 1001
        if val < 1001 or val > 9999:
            val = 1001
        next_val = val + 1 if val < 9999 else 1001
        counter_file.write_text(str(next_val), encoding="utf-8")
    return JSONResponse({"status": "success", "prefix": val})



@router.get("/api/v1/jobs/check-test")
async def api_check_test_exists(test_num: str = Query(""), output: str = Query("")):
    """Verify whether a test ID exists in data/runtime/work/ on disk.

    Args:
        test_num: Numeric test identifier (e.g. '2863').
        output: Output filename or path (e.g. 'test_fisheye_ST_out_2863.MP4').

    Returns:
        JSONResponse with exists boolean, test_id, and matched file count.
    """
    clean_num = re.sub(r'[^0-9]', '', test_num) if test_num else ""
    if not clean_num and output:
        m = re.search(r'_out_([0-9]+)', output, re.IGNORECASE) or re.search(r'(?:^|_)(\d{4,})(?:\.[a-zA-Z0-9]+)?$', output)
        if m:
            clean_num = m.group(1)

    matches = glob.glob(f"data/runtime/work/*_{clean_num}*.*") if clean_num else []
    return JSONResponse({
        "status": "success",
        "exists": len(matches) > 0,
        "test_id": clean_num,
        "count": len(matches)
    })

# ── Route: Jobs — intermediates ───────────────────────────────────────────────


@router.get("/api/v1/jobs/intermediates")
async def api_check_intermediates(
    input: str = Query(""),
    output: str = Query(""),
    test_num: str = Query(""),
    inject_intermediate_meta: str = Query("0"),
    job_id: str = Query("")
):
    """Scan and return intermediate pipeline artifacts, graphs, reports, and frames.

    Args:
        input: Input video filename.
        output: Expected final output video path.
        test_num: Run test prefix or identifier.
        inject_intermediate_meta: Flag indicating if VR metadata injected versions should be preferred.
        job_id: Job identifier to query status and outputs for.

    Returns:
        JSONResponse containing paths to available intermediate videos, graphs, and reports.
    """
    input_name = resolve_input_file(input)
    output_name = resolve_output_file(output)

    stitched_file = ""
    telemetry_file = ""
    kopf_file = ""
    kabsch_file = ""
    vidstab_file = ""
    cinematic_file = ""
    horizon_file = ""
    horizon_checkpoints_file = ""
    traveldir_file = ""
    final_file = ""
    target_base = ""

    # Check if there is an active job currently executing
    active_job_start_time = None
    active_status = ""
    active_phase = ""

    sf = get_status_file_path(job_id)
    sf_res = resolve_runtime_file(sf, ["data/runtime/temp"])

    if not test_num and sf_res and os.path.isfile(sf_res):
        try:
            with open(sf_res, "r", encoding="utf-8") as f_st:
                s_data = json.load(f_st)
            if isinstance(s_data, dict):
                st = s_data.get("status", "")
                is_active = False
                if st in ['stitching', 'stabilizing', 'nadiring', 'injecting', 'reporting', 'exporting', 'processing', 'awaiting_transforms']:
                    is_active = True
                    active_pid = s_data.get("pid")
                    if active_pid and isinstance(active_pid, int):
                        try:
                            import psutil
                            if not psutil.pid_exists(active_pid):
                                is_active = False
                        except Exception:
                            pass
                    s_out = s_data.get("output", "")
                    s_base = s_data.get("out_base", "")
                    if output_name:
                        out_stem = Path(output_name).stem.lower()
                        if s_out and (out_stem not in Path(s_out).stem.lower() and Path(s_out).stem.lower() not in out_stem):
                            is_active = False
                        if s_base and (s_base.lower() not in out_stem and out_stem not in s_base.lower()):
                            is_active = False
                if is_active:
                    active_status = st
                    active_phase = s_data.get("phase", "").lower()
                    active_job_start_time = s_data.get("start_time")
                    if active_job_start_time is None and s_data.get("elapsed") is not None:
                        try:
                            active_job_start_time = os.path.getmtime(sf_res) - float(s_data.get("elapsed", 0)) - 10.0
                        except Exception:
                            pass
                    if not target_base and not output_name and not input_name:
                        if s_data.get("out_base"):
                            target_base = s_data.get("out_base")
                        elif s_data.get("output"):
                            target_base = Path(s_data.get("output")).stem
        except Exception:
            pass

    if test_num:
        clean_num = re.sub(r'[^0-9]', '', test_num)
        if clean_num:
            num_matches = glob.glob(f"data/runtime/work/*out_{clean_num}*.*") or glob.glob(f"data/runtime/work/*_{clean_num}*.*")
            if num_matches:
                cand_name = Path(num_matches[0]).stem
                while True:
                    new_base = re.sub(r'(_(?:[1-4]_)?(?:stitched|stabilized|telemetry|vidstab|optical|kabsch|kopf|horizon|checkpoints|traveldir|cinematic|nadir|streetview|stabilization|sendcmd|overlay|flat|VR|graph|report|config|master|composed))$', '', cand_name, flags=re.IGNORECASE)
                    if new_base == cand_name:
                        break
                    cand_name = new_base
                target_base = cand_name
        if not target_base:
            return JSONResponse({
                "status": "success",
                "stitched": "",
                "telemetry": "",
                "kopf": "",
                "kabsch": "",
                "vidstab": "",
                "cinematic": "",
                "horizon": "",
                "traveldir": "",
                "final": "",
                "original": "",
                "preview": ""
            })

    if not target_base and output_name:
        out_p = Path(output_name)
        out_filename = out_p.stem
        cand_base = out_filename
        while True:
            new_base = re.sub(r'(_(?:[1-4]_)?(?:stitched|stabilized|telemetry|vidstab|optical|kabsch|kopf|horizon|checkpoints|traveldir|cinematic|nadir|streetview|stabilization|sendcmd|overlay|flat|VR|graph|report|config|master|composed))$', '', cand_base, flags=re.IGNORECASE)
            if new_base == cand_base:
                break
            cand_base = new_base
        if cand_base:
            target_base = cand_base

    if not target_base and input_name:
        target_base = Path(input_name).stem

    telemetry_graph_file = ""
    vidstab_graph_file = ""
    kabsch_graph_file = ""
    kopf_graph_file = ""
    cinematic_graph_file = ""
    horizon_graph_file = ""
    traveldir_graph_file = ""

    telemetry_report_file = ""
    vidstab_report_file = ""
    kabsch_report_file = ""
    kopf_report_file = ""
    cinematic_report_file = ""
    horizon_report_file = ""
    traveldir_report_file = ""

    if target_base:
        out_ext = Path(output_name).suffix.lstrip(".") if output_name else "MP4"

        resolved_raw_input = ""
        resolved_params = {}
        resolved_config = None

        cfg_file = f"data/runtime/work/{target_base}_config.json" if target_base else ""
        cfg_res = resolve_runtime_file(cfg_file, ["data/runtime/work"])
        if cfg_res and os.path.isfile(cfg_res):
            try:
                with open(cfg_res, "r", encoding="utf-8") as f_cfg:
                    cfg_data = json.load(f_cfg)
                if isinstance(cfg_data, dict):
                    resolved_config = cfg_data
                    resolved_raw_input = cfg_data.get("input", "")
                    for k in ["ih_fov", "iv_fov", "yaw", "pitch", "roll", "blend_width", "blend_seams", "left_y_offset"]:
                        if k in cfg_data:
                            resolved_params[k] = cfg_data[k]
            except Exception as e:
                print(f"[WARN] intermediates: failed reading pipeline config {cfg_file!r}: {e}", file=sys.stderr)

        # Determine configured stab_order and active methods
        stab_order = ["telemetry", "kopf", "kabsch", "vidstab", "cinematic", "horizon", "traveldir"]
        if isinstance(resolved_config, dict):
            stab_order = resolved_config.get("stab_order", stab_order)
            stab_methods_str = str(resolved_config.get("stabilize_methods", ",".join(stab_order)))
            active_methods = [m for m in stab_order if (m in stab_methods_str.split(",") or resolved_config.get(f"stab_{m}", False))]
        else:
            active_methods = list(stab_order)

        # Identify master composition stems from chosen_transforms to avoid confusing master render with intermediate steps
        master_stems = set()
        clean_target = re.sub(r'[^a-zA-Z0-9_\-]', '', target_base)
        clean_job = re.sub(r'[^a-zA-Z0-9_\-]', '', (job_id or (resolved_config.get("job_id", "") if isinstance(resolved_config, dict) else "")))
        for cf_raw in [
            f"data/runtime/temp/chosen_transforms_{clean_job}.json" if clean_job else None,
            f"data/runtime/temp/chosen_transforms_{clean_target}.json" if clean_target else None,
            "data/runtime/temp/chosen_transforms.json"
        ]:
            if not cf_raw:
                continue
            cf = resolve_runtime_file(cf_raw, ["data/runtime/temp"])
            if cf and os.path.isfile(cf) and os.path.getsize(cf) > 2:
                try:
                    with open(cf, "r", encoding="utf-8") as f_cf:
                        c_data = json.load(f_cf)
                    c_transforms = c_data.get("chosen_transforms", [])
                    if isinstance(c_transforms, list) and c_transforms:
                        master_stems.add(f"{target_base}_{'_'.join(c_transforms)}")
                except Exception as e:
                    print(f"[WARN] intermediates: failed reading chosen_transforms {cf!r}: {e}", file=sys.stderr)

        def _get_intermediate(*suffixes):
            for s in suffixes:
                # 1. Exact deterministic sequential chain if configured
                if s in active_methods:
                    s_idx = active_methods.index(s)
                    expected_chain = active_methods[:s_idx + 1]
                    chain_str = "_".join(expected_chain)
                    for ext in [f".{out_ext}", ".MP4", ".mp4"]:
                        cand_exact = resolve_runtime_file(f"data/runtime/work/{target_base}_{chain_str}{ext}", ["data/runtime/work"])
                        if cand_exact and is_valid_video_file(cand_exact):
                            if active_job_start_time is not None:
                                try:
                                    if os.path.getmtime(cand_exact) < (active_job_start_time - 5.0):
                                        continue
                                except Exception:
                                    continue
                            return cand_exact.replace("\\", "/")

                # 2. Direct simple candidate {target_base}_{s}{ext}
                for ext in [f".{out_ext}", ".MP4", ".mp4"]:
                    cand = resolve_runtime_file(f"data/runtime/work/{target_base}_{s}{ext}", ["data/runtime/work"])
                    if cand and is_valid_video_file(cand):
                        if active_job_start_time is not None:
                            try:
                                if os.path.getmtime(cand) < (active_job_start_time - 5.0):
                                    continue
                            except Exception:
                                continue
                        return cand.replace("\\", "/")

                # 3. Glob pattern fallback
                for ext in [f".{out_ext}", ".MP4", ".mp4"]:
                    clean_t_base = re.sub(r'[^a-zA-Z0-9_\-]', '', target_base)
                    pattern = f"data/runtime/work/{clean_t_base}_*_{s}{ext}"
                    matches = [
                        m.replace("\\", "/") for m in glob.glob(pattern)
                        if not m.endswith(f"_VR{ext}") and not m.endswith(f"_clean{ext}") and is_valid_video_file(m)
                    ]
                    valid_matches = []
                    for m in matches:
                        m_res = resolve_runtime_file(m, ["data/runtime/work"])
                        if not m_res:
                            continue
                        m_stem = Path(m_res).stem
                        if m_stem in master_stems or m_stem.endswith("_master") or m_stem.endswith("_composed"):
                            continue
                        if active_job_start_time is not None:
                            try:
                                if os.path.getmtime(m_res) < (active_job_start_time - 5.0):
                                    continue
                            except Exception:
                                continue
                        valid_matches.append(m_res)

                    if valid_matches:
                        def _match_rank(path_str):
                            stem = Path(path_str).stem
                            count = sum(1 for m_id in active_methods if f"_{m_id}" in stem)
                            return (count, os.path.getmtime(path_str))
                        return max(valid_matches, key=_match_rank)
            return ""

        cand_stitched = _get_intermediate("stitched", "2_stitched", "1_stitched")
        if cand_stitched and (active_status != "stitching" or "completed" in active_phase or active_status in ['stabilizing', 'nadiring', 'injecting', 'reporting', 'exporting', 'awaiting_transforms', 'completed']):
            stitched_file = cand_stitched
        elif not cand_stitched and input_name and ("_stitched" in Path(input_name).stem.lower() or "_equirect" in Path(input_name).stem.lower()) and is_valid_video_file(input_name):
            stitched_file = input_name

        cand_telemetry = _get_intermediate("telemetry")
        if cand_telemetry and (("telemetry" not in active_phase and "extracting telemetry" not in active_phase) or "completed" in active_phase or any(x in active_phase for x in ["kopf", "kabsch", "vidstab", "optical", "cinematic", "horizon", "checkpoint", "traveldir", "nadir", "master", "composition"]) or active_status in ['nadiring', 'injecting', 'reporting', 'exporting', 'awaiting_transforms', 'completed']):
            telemetry_file = cand_telemetry

        cand_kopf = _get_intermediate("kopf")
        if cand_kopf and ("kopf" not in active_phase or "completed" in active_phase or any(x in active_phase for x in ["kabsch", "vidstab", "optical", "cinematic", "horizon", "checkpoint", "traveldir", "nadir", "master", "composition"]) or active_status in ['nadiring', 'injecting', 'reporting', 'exporting', 'awaiting_transforms', 'completed']):
            kopf_file = cand_kopf

        cand_kabsch = _get_intermediate("kabsch")
        if cand_kabsch and ("kabsch" not in active_phase or "completed" in active_phase or any(x in active_phase for x in ["vidstab", "optical", "cinematic", "horizon", "checkpoint", "traveldir", "nadir", "master", "composition"]) or active_status in ['nadiring', 'injecting', 'reporting', 'exporting', 'awaiting_transforms', 'completed']):
            kabsch_file = cand_kabsch

        cand_vidstab = _get_intermediate("vidstab", "optical")
        if cand_vidstab and (("vidstab" not in active_phase and "optical" not in active_phase and "analysing motion" not in active_phase) or "completed" in active_phase or any(x in active_phase for x in ["cinematic", "horizon", "traveldir", "checkpoint", "nadir", "master", "composition"]) or active_status in ['nadiring', 'injecting', 'reporting', 'exporting', 'awaiting_transforms', 'completed']):
            vidstab_file = cand_vidstab

        cand_cinematic = _get_intermediate("cinematic")
        if cand_cinematic and (("cinematic" not in active_phase and "trajectory optimization" not in active_phase) or "completed" in active_phase or any(x in active_phase for x in ["horizon", "traveldir", "checkpoint", "nadir", "master", "composition"]) or active_status in ['nadiring', 'injecting', 'reporting', 'exporting', 'awaiting_transforms', 'completed']):
            cinematic_file = cand_cinematic

        cand_horizon = _get_intermediate("horizon", "checkpoints")
        if cand_horizon and (("horizon" not in active_phase and "checkpoint" not in active_phase) or "completed" in active_phase or any(x in active_phase for x in ["traveldir", "nadir", "master", "composition"]) or active_status in ['nadiring', 'injecting', 'reporting', 'exporting', 'awaiting_transforms', 'completed']):
            horizon_file = cand_horizon

        cand_traveldir = _get_intermediate("traveldir")
        if cand_traveldir and ("traveldir" not in active_phase or "completed" in active_phase or any(x in active_phase for x in ["nadir", "master", "composition"]) or active_status in ['nadiring', 'injecting', 'reporting', 'exporting', 'awaiting_transforms', 'completed']):
            traveldir_file = cand_traveldir

        if not active_status or active_status == "completed":
            for direct_raw in [
                f"data/runtime/work/{target_base}_nadir.{out_ext}",
                f"data/runtime/work/{target_base}_nadir.MP4",
                f"data/runtime/work/{target_base}_nadir.mp4",
                f"data/runtime/work/{target_base}_4_nadir.{out_ext}",
                f"data/runtime/work/{target_base}_4_nadir.MP4",
                f"data/runtime/work/{target_base}.{out_ext}",
                f"data/runtime/work/{target_base}.MP4",
                f"data/runtime/work/{target_base}.mp4"
            ]:
                direct = resolve_runtime_file(direct_raw, ["data/runtime/work"])
                if direct and is_valid_video_file(direct):
                    if active_job_start_time is not None:
                        try:
                            if os.path.getmtime(direct) < (active_job_start_time - 5.0):
                                continue
                        except Exception:
                            continue
                    final_file = direct.replace("\\", "/")
                    break

        def _is_valid_aux(aux_p):
            aux_res = resolve_runtime_file(aux_p, ["data/runtime/work"])
            if not aux_res or not os.path.isfile(aux_res): return False
            if active_job_start_time is not None:
                try:
                    if os.path.getmtime(aux_res) < (active_job_start_time - 5.0): return False
                except Exception:
                    return False
            return True

        t_g = f"data/runtime/work/{target_base}_telemetry_graph.png"
        if _is_valid_aux(t_g): telemetry_graph_file = t_g
        v_g = f"data/runtime/work/{target_base}_vidstab_graph.png"
        if _is_valid_aux(v_g): vidstab_graph_file = v_g
        k_g = f"data/runtime/work/{target_base}_kabsch_graph.png"
        if _is_valid_aux(k_g): kabsch_graph_file = k_g
        kp_g = f"data/runtime/work/{target_base}_kopf_graph.png"
        if _is_valid_aux(kp_g): kopf_graph_file = kp_g
        c_g = f"data/runtime/work/{target_base}_cinematic_graph.png"
        if _is_valid_aux(c_g): cinematic_graph_file = c_g
        h_g = f"data/runtime/work/{target_base}_horizon_graph.png"
        if _is_valid_aux(h_g): horizon_graph_file = h_g
        td_g = f"data/runtime/work/{target_base}_traveldir_graph.png"
        if _is_valid_aux(td_g): traveldir_graph_file = td_g

        t_r = f"data/runtime/work/{target_base}_telemetry_report.txt"
        if _is_valid_aux(t_r): telemetry_report_file = t_r
        v_r = f"data/runtime/work/{target_base}_vidstab_report.txt"
        if _is_valid_aux(v_r): vidstab_report_file = v_r
        k_r = f"data/runtime/work/{target_base}_kabsch_report.txt"
        if _is_valid_aux(k_r): kabsch_report_file = k_r
        kp_r = f"data/runtime/work/{target_base}_kopf_report.txt"
        if _is_valid_aux(kp_r): kopf_report_file = kp_r
        c_r = f"data/runtime/work/{target_base}_cinematic_report.txt"
        if _is_valid_aux(c_r): cinematic_report_file = c_r
        h_r = f"data/runtime/work/{target_base}_horizon_report.txt"
        if _is_valid_aux(h_r): horizon_report_file = h_r
        h_cp = f"data/runtime/work/{target_base}_horizon_checkpoints.json"
        if _is_valid_aux(h_cp): horizon_checkpoints_file = h_cp
        elif os.path.exists("data/runtime/work/horizon_checkpoints.json") and os.path.getsize("data/runtime/work/horizon_checkpoints.json") > 0:
            horizon_checkpoints_file = "data/runtime/work/horizon_checkpoints.json"
        td_r = f"data/runtime/work/{target_base}_traveldir_report.txt"
        if _is_valid_aux(td_r): traveldir_report_file = td_r

    want_vr = (inject_intermediate_meta == "1" or inject_intermediate_meta.lower() in ["true", "on"] or (bool(test_num) and isinstance(resolved_config, dict) and bool(resolved_config.get("inject_intermediate_meta", False))))

    stitched_file = resolve_video_version(stitched_file, want_vr)
    telemetry_file = resolve_video_version(telemetry_file, want_vr)
    kopf_file = resolve_video_version(kopf_file, want_vr)
    kabsch_file = resolve_video_version(kabsch_file, want_vr)
    vidstab_file = resolve_video_version(vidstab_file, want_vr)
    cinematic_file = resolve_video_version(cinematic_file, want_vr)
    horizon_file = resolve_video_version(horizon_file, want_vr)
    traveldir_file = resolve_video_version(traveldir_file, want_vr)
    final_file = resolve_video_version(final_file, want_vr)

    if resolved_raw_input:
        resolved_raw_input = resolve_input_file(resolved_raw_input)
    elif input_name:
        resolved_raw_input = resolve_input_file(input_name)

    orig_frame_url = ""
    if resolved_raw_input and os.path.exists(resolved_raw_input):
        orig_clean_stem = re.sub(r'[^a-zA-Z0-9_\-]', '', Path(resolved_raw_input).stem) or "input"
        orig_frame_file = safe_runtime_write_path(f"frame_{orig_clean_stem}_at_0_000.jpg", subdir="data/runtime/temp")
        if orig_frame_file:
            if not os.path.exists(orig_frame_file) or os.path.getsize(orig_frame_file) == 0:
                os.makedirs("data/runtime/temp", exist_ok=True)
                extract_cmd = ["ffmpeg", "-y", "-ss", "0.0", "-i", resolved_raw_input, "-vframes", "1", orig_frame_file]
                await run_async_subprocess(*extract_cmd)
            if os.path.exists(orig_frame_file) and os.path.getsize(orig_frame_file) > 0:
                orig_frame_url = orig_frame_file

    preview_frame_url = ""
    if test_num or stitched_file or final_file:
        stitch_source = stitched_file or final_file or telemetry_file or ""
        stitch_res = resolve_runtime_file(stitch_source, ["data/runtime/work", "data/input/videos"])
        if stitch_res and os.path.exists(stitch_res):
            preview_clean_base = re.sub(r'[^a-zA-Z0-9_\-]', '', target_base) or "preview"
            preview_frame_file = safe_runtime_write_path(f"preview_{preview_clean_base}.png", subdir="data/runtime/temp")
            if preview_frame_file:
                if not os.path.exists(preview_frame_file) or os.path.getsize(preview_frame_file) == 0:
                    os.makedirs("data/runtime/temp", exist_ok=True)
                    extract_prev_cmd = ["ffmpeg", "-y", "-ss", "0.0", "-i", stitch_res, "-vframes", "1", preview_frame_file]
                    await run_async_subprocess(*extract_prev_cmd)
                if os.path.exists(preview_frame_file) and os.path.getsize(preview_frame_file) > 0:
                    preview_frame_url = preview_frame_file

    matched_job_id = ""
    if resolved_config and resolved_config.get("job_id"):
        matched_job_id = resolved_config["job_id"]
    elif job_id:
        matched_job_id = job_id
    elif test_num:
        clean_num2 = re.sub(r'[^0-9]', '', test_num)
        if clean_num2:
            st_path = get_status_file_path(f"job_{clean_num2}")
            if os.path.exists(st_path):
                matched_job_id = f"job_{clean_num2}"

    graph_file = traveldir_graph_file or horizon_graph_file or cinematic_graph_file or vidstab_graph_file or kabsch_graph_file or kopf_graph_file or telemetry_graph_file

    return JSONResponse({
        "status": "success",
        "job_id": matched_job_id,
        "target_base": target_base,
        "raw_input": resolved_raw_input,
        "output_file": "" if (active_status and active_status != "completed") else (final_file if (final_file and is_valid_video_file(final_file)) else (output_name if (output_name and is_valid_video_file(output_name)) else "")),
        "stitched": stitched_file,
        "telemetry": telemetry_file,
        "kopf": kopf_file,
        "kabsch": kabsch_file,
        "vidstab": vidstab_file,
        "optical": vidstab_file,
        "cinematic": cinematic_file,
        "horizon": horizon_file,
        "checkpoints": horizon_file,
        "traveldir": traveldir_file,
        "final": final_file,
        "preview": preview_frame_url,
        "original": orig_frame_url,
        "params": resolved_params or None,
        "config": resolved_config,
        "graph": graph_file,
        "telemetry_graph": telemetry_graph_file,
        "vidstab_graph": vidstab_graph_file,
        "optical_graph": vidstab_graph_file,
        "kabsch_graph": kabsch_graph_file,
        "kopf_graph": kopf_graph_file,
        "cinematic_graph": cinematic_graph_file,
        "horizon_graph": horizon_graph_file,
        "checkpoints_graph": horizon_graph_file,
        "traveldir_graph": traveldir_graph_file,
        "telemetry_report": telemetry_report_file,
        "vidstab_report": vidstab_report_file,
        "optical_report": vidstab_report_file,
        "kabsch_report": kabsch_report_file,
        "kopf_report": kopf_report_file,
        "cinematic_report": cinematic_report_file,
        "horizon_report": horizon_report_file,
        "checkpoints_report": horizon_report_file,
        "horizon_checkpoints": horizon_checkpoints_file,
        "traveldir_report": traveldir_report_file
    })

# ── Route: Videos — inject 360 metadata ──────────────────────────────────────


@router.post("/api/v1/jobs")
async def api_start_job(request: Request):
    """Launch the background 360 stitching, stabilization, and encoding pipeline.

    Args:
        request: FastAPI HTTP request with comprehensive pipeline parameters.

    Returns:
        JSONResponse with job ID, output paths, and initial execution status.

    Raises:
        MissingParameterError: If mandatory input or output parameters are missing.
    """
    form = await request.form()
    form_data = dict(form)
    def gp(n, d=""): return form_data.get(n, d)

    raw_input  = require_param(form_data, "input")
    raw_output = require_param(form_data, "output")

    input_name = resolve_input_file(raw_input)
    if not input_name:
        return JSONResponse({"status": "error", "error": f"Input file does not exist: {raw_input}"}, status_code=404)

    base_dir_abs = os.path.realpath(os.path.abspath(str(BASE_DIR)))
    input_abs = os.path.realpath(os.path.abspath(os.path.join(base_dir_abs, input_name)))
    if not input_abs.startswith(base_dir_abs + os.sep):
        return JSONResponse({"status": "error", "error": "Unauthorized input path."}, status_code=400)
    if not os.path.isfile(input_abs):
        return JSONResponse({"status": "error", "error": f"Input file does not exist: {raw_input}"}, status_code=404)

    # 1. Backend Concurrency Guard & Mutex
    active_job = get_active_job()
    if active_job:
        active_jid, active_pid = active_job
        return JSONResponse({
            "status": "error",
            "error": f"A pipeline processing job ({active_jid}) is already in progress (PID: {active_pid}). Please wait or cancel the active job.",
            "job_id": active_jid,
            "pid": active_pid
        }, status_code=409)

    is_photo = is_valid_image_file(input_name)

    # 2. Pre-flight Storage Space Validation
    stab_methods = str(gp("stabilize_methods", "telemetry,kopf,kabsch,vidstab,cinematic,horizon,traveldir")).split(",") if (str(gp("stabilize", "0")) == "1" and not is_photo) else []
    active_stages = (1 if str(gp("skip_stitching", "0")) != "1" else 0) + len([m for m in stab_methods if m.strip()]) + 1
    has_space, space_err, req_gb, avail_gb = check_disk_space(input_name, num_stages=active_stages)
    if not has_space:
        return JSONResponse({
            "status": "error",
            "error": space_err,
            "required_gb": round(req_gb, 2),
            "available_gb": round(avail_gb, 2)
        }, status_code=400)

    if not is_photo and str(gp("streetview_enabled", "0")) == "1":
        sv_mode = str(gp("streetview_mode", "A"))
        if sv_mode == "A":
            sv_cps = str(gp("streetview_checkpoints", "")).strip()
            if not sv_cps:
                return JSONResponse({"status": "error", "error": "Street View Mode A requires checkpoints. Please enter coordinates in Map Checkpoints before starting."}, status_code=400)
        elif sv_mode == "B":
            raw_sv_gpx = resolve_gpx_file(str(gp("streetview_gpx_path", "")))
            if not raw_sv_gpx or not os.path.exists(raw_sv_gpx):
                return JSONResponse({"status": "error", "error": "Street View Mode B requires a valid GPX log file. Please select an existing GPX file before starting."}, status_code=400)

    job_id_raw = str(gp("job_id", ""))
    job_id     = safe_job_id(job_id_raw)
    if not re.fullmatch(r'[a-zA-Z0-9_\-]+', job_id):
        import uuid
        job_id = f"job_{uuid.uuid4().hex[:12]}"

    status_file = get_status_file_path(job_id)
    log_file    = get_log_file_path(job_id)

    os.makedirs("data/runtime/logs", exist_ok=True)
    os.makedirs("data/runtime/temp", exist_ok=True)

    for sf in [status_file, "data/runtime/temp/status.json"]:
        try:
            with open(sf, "w", encoding="utf-8") as _f_init:
                json.dump({"status": "starting", "phase": "Initializing pipeline...", "progress": 0.0}, _f_init)
        except Exception as e:
            print(f"[WARN] run_pipeline: failed writing initial status to {sf!r}: {e}", file=sys.stderr)

    output_name = resolve_output_file(raw_output)
    if not output_name:
        clean_out_base = re.sub(r'[^a-zA-Z0-9_\-]', '_', Path(os.path.basename(raw_output)).stem) or "output"
        output_name = f"data/runtime/work/{clean_out_base}.mp4"

    output_abs = os.path.realpath(os.path.abspath(os.path.join(base_dir_abs, output_name)))
    if not output_abs.startswith(base_dir_abs + os.sep):
        return JSONResponse({"status": "error", "error": "Unauthorized output path."}, status_code=400)

    blend_seams_val    = (str(gp("blend_seams", "1")) == "1" or str(gp("blend_seams", "")).lower() == "true")
    blend_width_raw    = int(float(gp("blend_width", "200")))
    actual_blend_width = blend_width_raw if blend_seams_val else 0

    try:
        os.makedirs("data/runtime/work", exist_ok=True)
        out_base = re.sub(r'[^a-zA-Z0-9_\-]', '_', Path(output_name).stem)
        pipeline_cfg_raw = gp("pipeline_config", "")
        cfg_obj = None
        if pipeline_cfg_raw:
            if isinstance(pipeline_cfg_raw, dict):
                cfg_obj = dict(pipeline_cfg_raw)
            elif isinstance(pipeline_cfg_raw, str):
                try:
                    cfg_obj = json.loads(pipeline_cfg_raw)
                except Exception:
                    pass

        if not cfg_obj or not isinstance(cfg_obj, dict):
            stab_methods_str = str(gp("stabilize_methods", "telemetry,kopf,kabsch,vidstab,cinematic,horizon,traveldir"))
            cfg_obj = {
                "type": "pipeline_config", "version": 1,
                "enable_stitching": str(gp("skip_stitching", "0")) != "1",
                "ih_fov": str(gp("ih_fov", PIPELINE_DEFAULTS["ih_fov"])), "iv_fov": str(gp("iv_fov", PIPELINE_DEFAULTS["iv_fov"])),
                "raw_rotation": str(gp("raw_rotation", PIPELINE_DEFAULTS["raw_rotation"])),
                "yaw": str(gp("yaw", PIPELINE_DEFAULTS["yaw"])), "pitch": str(gp("pitch", PIPELINE_DEFAULTS["pitch"])), "roll": str(gp("roll", PIPELINE_DEFAULTS["roll"])),
                "left_y_offset": str(gp("left_y_offset", PIPELINE_DEFAULTS["left_y_offset"])), "rear_roll_offset": str(gp("rear_roll_offset", PIPELINE_DEFAULTS["rear_roll_offset"])),
                "blend_seams": blend_seams_val, "blend_width": str(blend_width_raw),
                "anti_vignette": str(gp("anti_vignette", "0")) == "1",
                "anti_vignette_angle": str(gp("anti_vignette_angle", PIPELINE_DEFAULTS["anti_vignette_angle"])),
                "stabilize": str(gp("stabilize", "0")) == "1",
                "stab_telemetry": "telemetry" in stab_methods_str,
                "stab_kopf": "kopf" in stab_methods_str,
                "stab_kabsch": "kabsch" in stab_methods_str,
                "stab_vidstab": "vidstab" in stab_methods_str,
                "stab_cinematic": "cinematic" in stab_methods_str,
                "stab_horizon": "horizon" in stab_methods_str,
                "stab_traveldir": "traveldir" in stab_methods_str,
                "stabilize_methods": stab_methods_str,
                "stab_order": [m.strip() for m in stab_methods_str.split(",") if m.strip()],
                "horizon_autodetect": str(gp("horizon_autodetect", "0")) == "1",
                "horizon_autodetect_source": str(gp("horizon_autodetect_source", "vision")),
                "horizon_autodetect_density": str(gp("horizon_autodetect_density", "ultra_dense")),
                "horizon_pitch_prominence": float(gp("horizon_pitch_prominence", "0.8")),
                "horizon_roll_damping": float(gp("horizon_roll_damping", "0.70")),
                "horizon_apply_yaw": str(gp("horizon_apply_yaw", "0")) == "1",
                "stab_quality_mode": str(gp("stab_quality_mode", PIPELINE_DEFAULTS["stab_quality_mode"])),
                "prompt_transforms": str(gp("prompt_transforms", "1")) != "0",
                "fallback_unstabilized": str(gp("fallback_unstabilized", "1")) != "0",
                "kabsch_smoothing": str(gp("kabsch_smoothing", PIPELINE_DEFAULTS["kabsch_smoothing"])),
                "kopf_keyframe_sec": str(gp("kopf_keyframe_sec", PIPELINE_DEFAULTS["kopf_keyframe_sec"])),
                "kopf_cube_face": str(gp("kopf_cube_face", PIPELINE_DEFAULTS["kopf_cube_face"])),
                "kopf_max_features": str(gp("kopf_max_features", PIPELINE_DEFAULTS["kopf_max_features"])),
                "kopf_deformed": str(gp("kopf_deformed", "0")) == "1",
                "kopf_reapply": str(gp("kopf_reapply", "0")) == "1",
                "vidstab_visual": str(gp("vidstab_visual", "0")) == "1",
                "telemetry_mode": str(gp("telemetry_mode", PIPELINE_DEFAULTS["telemetry_mode"])),
                "telemetry_fusion": str(gp("telemetry_fusion", PIPELINE_DEFAULTS["telemetry_fusion"])),
                "telemetry_fusion_gain": str(gp("telemetry_fusion_gain", PIPELINE_DEFAULTS["telemetry_fusion_gain"])),
                "telemetry_source": str(gp("telemetry_source", PIPELINE_DEFAULTS["telemetry_source"])),
                "telemetry_ref_frame": str(gp("telemetry_ref_frame", PIPELINE_DEFAULTS["telemetry_ref_frame"])),
                "telemetry_smoothing": str(gp("telemetry_smoothing", PIPELINE_DEFAULTS["telemetry_smoothing"])),
                "telemetry_multiplier_roll": str(gp("telemetry_multiplier_roll", PIPELINE_DEFAULTS["telemetry_multiplier_roll"])),
                "telemetry_multiplier_pitch": str(gp("telemetry_multiplier_pitch", PIPELINE_DEFAULTS["telemetry_multiplier_pitch"])),
                "telemetry_multiplier_yaw": str(gp("telemetry_multiplier_yaw", PIPELINE_DEFAULTS["telemetry_multiplier_yaw"])),
                "l1_lambda_acc": str(gp("l1_lambda_acc", PIPELINE_DEFAULTS["l1_lambda_acc"])),
                "l1_lambda_vel": str(gp("l1_lambda_vel", PIPELINE_DEFAULTS["l1_lambda_vel"])),
                "cinematic_window": str(gp("cinematic_window", PIPELINE_DEFAULTS["cinematic_window"])),
                "traveldir_mode": str(gp("traveldir_mode", PIPELINE_DEFAULTS["traveldir_mode"])),
                "traveldir_target_yaw": str(gp("traveldir_target_yaw", PIPELINE_DEFAULTS["traveldir_target_yaw"])),
                "traveldir_damping": str(gp("traveldir_damping", PIPELINE_DEFAULTS["traveldir_damping"])),
                "traveldir_deadband": str(gp("traveldir_deadband", PIPELINE_DEFAULTS["traveldir_deadband"])),
                "vidstab_smoothing": str(gp("vidstab_smoothing", PIPELINE_DEFAULTS["vidstab_smoothing"])),
                "vidstab_shakiness": str(gp("vidstab_shakiness", PIPELINE_DEFAULTS["vidstab_shakiness"])),
                "vidstab_optalgo": str(gp("vidstab_optalgo", PIPELINE_DEFAULTS["vidstab_optalgo"])),
                "vidstab_tripod": str(gp("vidstab_tripod", "0")) == "1",
                "nadir_enabled": str(gp("nadir_enabled", "0")) == "1",
                "nadir_logo": str(gp("nadir_logo", PIPELINE_DEFAULTS["nadir_logo_default"])),
                "nadir_fov": str(gp("nadir_fov", PIPELINE_DEFAULTS["nadir_fov"])), "nadir_fov_v": str(gp("nadir_fov_v", PIPELINE_DEFAULTS["nadir_fov_v"])),
                "streetview_enabled": str(gp("streetview_enabled", "0")) == "1",
                "streetview_mode": str(gp("streetview_mode", PIPELINE_DEFAULTS["streetview_mode"])),
                "streetview_checkpoints": str(gp("streetview_checkpoints", "")),
                "streetview_gpx_path": str(gp("streetview_gpx_path", "")),
                "streetview_start_time": str(gp("streetview_start_time", "")),
                "streetview_time_offset": str(gp("streetview_time_offset", "0")),
                "streetview_auto_pad": str(gp("streetview_auto_pad", PIPELINE_DEFAULTS["streetview_auto_pad"])) != "0",
                "util_gcsv": str(gp("util_gcsv", "0")) == "1",
                "util_bigsh0t": str(gp("util_bigsh0t", "0")) == "1"
            }

        cfg_obj["saved_at"]    = datetime.now().isoformat()
        cfg_obj["job_id"]      = job_id
        cfg_obj["target_base"] = out_base
        cfg_obj["input"]       = input_name
        cfg_obj["output"]      = output_name

        cfg_json_str = json.dumps(cfg_obj, indent=2, ensure_ascii=False) + "\n"
        if out_base:
            base_dir_abs = os.path.realpath(os.path.abspath(str(BASE_DIR)))
            work_dir_abs = os.path.realpath(os.path.abspath(os.path.join(base_dir_abs, "data", "runtime", "work")))
            cfg_file_abs = os.path.realpath(os.path.abspath(os.path.join(work_dir_abs, f"{out_base}_config.json")))
            if cfg_file_abs.startswith(work_dir_abs + os.sep):
                with open(cfg_file_abs, "w", newline="\n", encoding="utf-8") as f_cfg:
                    f_cfg.write(cfg_json_str)
    except Exception as e_cfg:
        print(f"Warning: Failed to save config to work folder: {e_cfg}", flush=True)

    mask_file = f"data/runtime/temp/alpha_mask_{actual_blend_width}.png"
    if not os.path.exists(mask_file):
        await run_async_subprocess(sys.executable, "-B", "scripts/generate_alpha_mask.py", "--blend_width", str(actual_blend_width), "--output", mask_file)

    pipeline_cmd = [
        sys.executable, "-B", "-u", "scripts/pipeline.py",
        "--input", input_name, "--output", output_name,
        "--ih_fov", safe_number(gp("ih_fov"), PIPELINE_DEFAULTS["ih_fov"]),
        "--iv_fov", safe_number(gp("iv_fov"), PIPELINE_DEFAULTS["iv_fov"]),
        "--raw_rotation", safe_number(gp("raw_rotation"), PIPELINE_DEFAULTS["raw_rotation"]),
        "--yaw", safe_number(gp("yaw"), PIPELINE_DEFAULTS["yaw"]),
        "--pitch", safe_number(gp("pitch"), PIPELINE_DEFAULTS["pitch"]),
        "--roll", safe_number(gp("roll"), PIPELINE_DEFAULTS["roll"]),
        "--left_y_offset", safe_number(gp("left_y_offset"), PIPELINE_DEFAULTS["left_y_offset"]),
        "--rear_roll_offset", safe_number(gp("rear_roll_offset"), PIPELINE_DEFAULTS["rear_roll_offset"]),
        "--preset", safe_choice(gp("ffmpeg_preset", gp("preset")), ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow", "p1", "p2", "p3", "p4", "p5", "p6", "p7"], PIPELINE_DEFAULTS["ffmpeg_preset"]),
        "--crf", safe_number(gp("ffmpeg_crf", gp("crf")), PIPELINE_DEFAULTS["ffmpeg_crf"], cast_fn=int),
        "--blend_width", str(actual_blend_width), "--mask_file", mask_file,
        "--anti_vignette_angle", safe_number(gp("anti_vignette_angle"), PIPELINE_DEFAULTS["anti_vignette_angle"]),
        "--nadir_fov", safe_number(gp("nadir_fov"), PIPELINE_DEFAULTS["nadir_fov"]),
        "--nadir_fov_v", safe_number(gp("nadir_fov_v"), PIPELINE_DEFAULTS["nadir_fov_v"]),
        "--status_file", status_file
    ]

    if str(gp("ffmpeg_hwaccel", gp("hwaccel", "0"))) == "1": pipeline_cmd.append("--hwaccel")
    if blend_seams_val: pipeline_cmd.append("--blend_seams")
    if str(gp("anti_vignette", "0")) == "1": pipeline_cmd.append("--anti_vignette")
    if str(gp("inject_intermediate_meta", "0")) == "1": pipeline_cmd.append("--inject_intermediate_meta")
    if str(gp("inject_final_meta", "1")) == "1": pipeline_cmd.append("--inject_final_meta")
    if str(gp("util_gcsv", "0")) == "1": pipeline_cmd.append("--util_gcsv")
    if str(gp("util_bigsh0t", "0")) == "1": pipeline_cmd.append("--util_bigsh0t")
    if str(gp("skip_stitching", "0")) == "1": pipeline_cmd.append("--skip_stitching")

    for feat in ["stitched", "telemetry", "kopf", "kabsch", "vidstab", "cinematic", "horizon", "traveldir", "nadir"]:
        if str(gp(f"input_has_{feat}", "0")) == "1":
            pipeline_cmd.append(f"--input_has_{feat}")

    if str(gp("nadir_enabled", "0")) == "1":
        nadir_logo_raw = resolve_nadir_logo(str(gp("nadir_logo", PIPELINE_DEFAULTS["nadir_logo_default"])))
        if nadir_logo_raw and not nadir_logo_raw.startswith("-"):
            nadir_abs = os.path.realpath(os.path.abspath(os.path.join(base_dir_abs, nadir_logo_raw)))
            if nadir_abs.startswith(base_dir_abs + os.sep) and os.path.isfile(nadir_abs):
                pipeline_cmd.extend(["--nadir_logo", nadir_logo_raw])

    if str(gp("stabilize", "0")) == "1" and not is_photo:
        pipeline_cmd.append("--stabilize")
        ALLOWED_STAB_METHODS = ("telemetry", "kopf", "kabsch", "vidstab", "cinematic", "horizon", "traveldir")
        requested_methods = str(gp("stabilize_methods", "telemetry,kopf,kabsch,vidstab,cinematic,horizon,traveldir")).split(",")
        cleaned_methods = ",".join([m for m in ALLOWED_STAB_METHODS if any(m == req.strip() for req in requested_methods)])
        pipeline_cmd.extend(["--stabilize_methods", cleaned_methods or "telemetry,kopf,kabsch,vidstab,cinematic,horizon,traveldir"])
        pipeline_cmd.extend(["--stab_quality_mode", safe_choice(gp("stab_quality_mode"), ["draft", "standard", "high", "ultra"], PIPELINE_DEFAULTS["stab_quality_mode"])])
        pipeline_cmd.extend(["--telemetry_mode", safe_choice(gp("telemetry_mode"), ["fusion", "orientation", "gyro"], PIPELINE_DEFAULTS["telemetry_mode"])])
        pipeline_cmd.extend(["--telemetry_fusion", safe_choice(gp("telemetry_fusion"), ["madgwick", "mahony", "complementary", "ekf"], PIPELINE_DEFAULTS["telemetry_fusion"])])
        pipeline_cmd.extend(["--telemetry_fusion_gain", safe_number(gp("telemetry_fusion_gain"), PIPELINE_DEFAULTS["telemetry_fusion_gain"])])
        pipeline_cmd.extend(["--telemetry_source", safe_choice(gp("telemetry_source"), ["auto", "gcsv", "txt", "mp4"], PIPELINE_DEFAULTS["telemetry_source"])])
        pipeline_cmd.extend(["--telemetry_smoothing", safe_number(gp("telemetry_smoothing"), PIPELINE_DEFAULTS["telemetry_smoothing"])])
        pipeline_cmd.extend(["--telemetry_ref_frame", safe_choice(gp("telemetry_ref_frame"), ["world", "camera", "body"], PIPELINE_DEFAULTS["telemetry_ref_frame"])])
        pipeline_cmd.extend(["--telemetry_multiplier_roll", safe_number(gp("telemetry_multiplier_roll"), PIPELINE_DEFAULTS["telemetry_multiplier_roll"])])
        pipeline_cmd.extend(["--telemetry_multiplier_pitch", safe_number(gp("telemetry_multiplier_pitch"), PIPELINE_DEFAULTS["telemetry_multiplier_pitch"])])
        pipeline_cmd.extend(["--telemetry_multiplier_yaw", safe_number(gp("telemetry_multiplier_yaw"), PIPELINE_DEFAULTS["telemetry_multiplier_yaw"])])
        pipeline_cmd.extend(["--vidstab_smoothing", safe_number(gp("vidstab_smoothing"), PIPELINE_DEFAULTS["vidstab_smoothing"], cast_fn=int)])
        pipeline_cmd.extend(["--vidstab_shakiness", safe_number(gp("vidstab_shakiness"), PIPELINE_DEFAULTS["vidstab_shakiness"], cast_fn=int)])
        pipeline_cmd.extend(["--vidstab_optalgo", safe_choice(gp("vidstab_optalgo"), ["gauss", "avg"], PIPELINE_DEFAULTS["vidstab_optalgo"])])
        pipeline_cmd.extend(["--kabsch_smoothing", safe_number(gp("kabsch_smoothing"), PIPELINE_DEFAULTS["kabsch_smoothing"], cast_fn=int)])
        if str(gp("vidstab_tripod", "0")) == "1": pipeline_cmd.append("--vidstab_tripod")
        if str(gp("vidstab_visual", "0")) == "1": pipeline_cmd.append("--vidstab_visual")
        pipeline_cmd.extend(["--kopf_keyframe_sec", safe_number(gp("kopf_keyframe_sec"), PIPELINE_DEFAULTS["kopf_keyframe_sec"])])
        pipeline_cmd.extend(["--kopf_cube_face", safe_choice(gp("kopf_cube_face"), ["equi", "front", "back", "left", "right", "top", "bottom"], PIPELINE_DEFAULTS["kopf_cube_face"])])
        pipeline_cmd.extend(["--kopf_max_features", safe_number(gp("kopf_max_features"), PIPELINE_DEFAULTS["kopf_max_features"], cast_fn=int)])
        if str(gp("kopf_deformed", "0")) == "1": pipeline_cmd.append("--kopf_deformed")
        if str(gp("kopf_reapply", "0")) == "1": pipeline_cmd.append("--kopf_reapply")
        pipeline_cmd.extend(["--l1_lambda_acc", safe_number(gp("l1_lambda_acc"), PIPELINE_DEFAULTS["l1_lambda_acc"])])
        pipeline_cmd.extend(["--l1_lambda_vel", safe_number(gp("l1_lambda_vel"), PIPELINE_DEFAULTS["l1_lambda_vel"])])
        pipeline_cmd.extend(["--cinematic_window", safe_number(gp("cinematic_window"), PIPELINE_DEFAULTS["cinematic_window"])])
        pipeline_cmd.extend(["--traveldir_mode", safe_choice(gp("traveldir_mode"), ["auto", "forward", "backward"], PIPELINE_DEFAULTS["traveldir_mode"])])
        pipeline_cmd.extend(["--traveldir_target_yaw", safe_number(gp("traveldir_target_yaw"), PIPELINE_DEFAULTS["traveldir_target_yaw"])])
        pipeline_cmd.extend(["--traveldir_damping", safe_number(gp("traveldir_damping"), PIPELINE_DEFAULTS["traveldir_damping"])])
        pipeline_cmd.extend(["--traveldir_deadband", safe_number(gp("traveldir_deadband"), PIPELINE_DEFAULTS["traveldir_deadband"])])
        if str(gp("horizon_autodetect", "0")) == "1":
            pipeline_cmd.append("--horizon_autodetect")
            pipeline_cmd.extend(["--horizon_autodetect_source", safe_choice(gp("horizon_autodetect_source"), ["vision", "telemetry", "combined"], "vision")])
            pipeline_cmd.extend(["--horizon_autodetect_density", safe_choice(gp("horizon_autodetect_density"), ["sparse", "balanced", "dense", "ultra_dense"], "ultra_dense")])
            if gp("horizon_pitch_prominence"):
                pipeline_cmd.extend(["--horizon_pitch_prominence", safe_number(gp("horizon_pitch_prominence"), "1.5")])
            if gp("horizon_roll_damping"):
                pipeline_cmd.extend(["--horizon_roll_damping", safe_number(gp("horizon_roll_damping"), "0.7")])
            if str(gp("horizon_apply_yaw", "0")) == "1": pipeline_cmd.append("--horizon_apply_yaw")

    if str(gp("streetview_enabled", "0")) == "1":
        pipeline_cmd.append("--streetview_enabled")
        pipeline_cmd.extend(["--streetview_mode", safe_choice(gp("streetview_mode"), ["A", "B"], PIPELINE_DEFAULTS["streetview_mode"])])
        sv_cps = str(gp("streetview_checkpoints", "")).strip()
        if sv_cps and not sv_cps.startswith("-"):
            sv_cps_file = safe_runtime_write_path(f"cps_{job_id}.json", subdir="data/runtime/temp")
            if sv_cps_file:
                try:
                    with open(sv_cps_file, "w", encoding="utf-8") as f_cps:
                        f_cps.write(sv_cps)
                    pipeline_cmd.extend(["--streetview_checkpoints", sv_cps_file])
                except Exception:
                    pass
        raw_sv_gpx = resolve_gpx_file(str(gp("streetview_gpx_path", "")))
        if raw_sv_gpx and not raw_sv_gpx.startswith("-"):
            sv_gpx_abs = os.path.realpath(os.path.abspath(os.path.join(base_dir_abs, raw_sv_gpx)))
            if sv_gpx_abs.startswith(base_dir_abs + os.sep) and os.path.isfile(sv_gpx_abs):
                pipeline_cmd.extend(["--streetview_gpx_path", raw_sv_gpx])
        sv_st = re.sub(r'[^a-zA-Z0-9:\.\-+_]', '', str(gp("streetview_start_time", "")).strip())
        if sv_st and re.fullmatch(r'[a-zA-Z0-9:\.\-+_]+', sv_st):
            pipeline_cmd.extend(["--streetview_start_time", sv_st])
        pipeline_cmd.extend(["--streetview_time_offset", safe_number(gp("streetview_time_offset"), "0")])
        if str(gp("streetview_auto_pad", "1")) == "0": pipeline_cmd.append("--streetview_no_auto_pad")
        sv_bitrate = re.sub(r'[^0-9a-zA-Z]', '', str(gp("streetview_bitrate", PIPELINE_DEFAULTS["streetview_bitrate"])).strip())
        if sv_bitrate and re.fullmatch(r'[0-9]+[a-zA-Z]?', sv_bitrate):
            pipeline_cmd.extend(["--streetview_bitrate", sv_bitrate])
        sv_start_coord = re.sub(r'[^0-9\.,\- ]', '', str(gp("streetview_start_coord", "")).strip())
        if sv_start_coord and re.fullmatch(r'[-+]?[0-9]*\.?[0-9]+,\s*[-+]?[0-9]*\.?[0-9]+', sv_start_coord):
            pipeline_cmd.extend(["--streetview_start_coord", sv_start_coord])
        sv_end_coord = re.sub(r'[^0-9\.,\- ]', '', str(gp("streetview_end_coord", "")).strip())
        if sv_end_coord and re.fullmatch(r'[-+]?[0-9]*\.?[0-9]+,\s*[-+]?[0-9]*\.?[0-9]+', sv_end_coord):
            pipeline_cmd.extend(["--streetview_end_coord", sv_end_coord])
        if str(gp("streetview_smooth_gps", "1")) == "1": pipeline_cmd.append("--streetview_smooth_gps")
        if str(gp("streetview_strip_audio", "1")) == "0":
            pipeline_cmd.append("--streetview_keep_audio")
        else:
            pipeline_cmd.append("--streetview_strip_audio")

    video_bitrate = re.sub(r'[^0-9a-zA-Z]', '', str(gp("video_bitrate", "")).strip())
    if video_bitrate and re.fullmatch(r'[0-9]+[a-zA-Z]?', video_bitrate):
        pipeline_cmd.extend(["--video_bitrate", video_bitrate])
    if str(gp("remove_audio", "0")) == "1": pipeline_cmd.append("--remove_audio")
    if str(gp("prompt_transforms", "1")) == "0": pipeline_cmd.append("--no_prompt_transforms")
    if str(gp("fallback_unstabilized", "1")) == "0": pipeline_cmd.append("--no_fallback_unstabilized")
    if job_id: pipeline_cmd.extend(["--job_id", job_id])

    duration = int(float(gp("duration", "0")))
    if duration > 0: pipeline_cmd.extend(["--duration", str(duration)])

    cmd_text = subprocess.list2cmdline(pipeline_cmd)
    with open(log_file, "a", encoding="utf-8") as log_handle:
        proc = spawn_background_process(pipeline_cmd, stdout=log_handle, stderr=subprocess.STDOUT)

    _managed_pids[job_id] = proc.pid
    save_managed_pids()

    resp_payload = {"status": "success", "message": "Stitching started.", "job_id": job_id}
    if DEBUG:
        resp_payload["command"] = cmd_text
    return JSONResponse(resp_payload, status_code=202)

# ── Route: Jobs — status ──────────────────────────────────────────────────────


@router.get("/api/v1/jobs/{job_id}/status")
async def api_job_status(job_id: str):
    """Query progress, active phase, speed, ETA, and recent logs for a pipeline job.

    Args:
        job_id: Unique pipeline run identifier.

    Returns:
        JSONResponse containing job status dictionary and tailing log output.
    """
    clean_jid = safe_job_id(job_id)
    log_candidates = [
        f"data/runtime/logs/pipeline_{clean_jid}.log",
        f"data/runtime/logs/stitch_pipeline_{clean_jid}.log",
        "data/runtime/logs/pipeline.log",
        "data/runtime/logs/stitch_pipeline.log"
    ] if clean_jid and clean_jid != "default" else [
        "data/runtime/logs/pipeline.log",
        "data/runtime/logs/stitch_pipeline.log"
    ]
    log_file = ""
    for lc in log_candidates:
        valid_lc = resolve_runtime_file(lc, ["data/runtime/logs"])
        if valid_lc and os.path.isfile(valid_lc):
            log_file = valid_lc
            break

    log_content = ""
    if log_file and os.path.isfile(log_file):
        try:
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 32768))
                log_content = f.read(32768)
        except Exception:
            pass

    sf_target = get_status_file_path(clean_jid) if clean_jid and clean_jid != "default" else "data/runtime/temp/status.json"
    sf = resolve_runtime_file(sf_target, ["data/runtime/temp"])

    if sf and os.path.isfile(sf):
        try:
            with open(sf, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                if data.get("status") == "completed":
                    output_file = data.get("output", "")
                    if output_file and not os.path.exists(output_file):
                        return JSONResponse({"status": "idle", "progress": 0, "log": log_content})
                elif data.get("status") in ['stitching', 'stabilizing', 'nadiring', 'injecting', 'reporting', 'exporting', 'processing', 'awaiting_transforms']:
                    pid = data.get("pid")
                    if pid and isinstance(pid, int):
                        try:
                            import psutil
                            if not psutil.pid_exists(pid):
                                data["status"] = "failed"
                                data["error"] = f"Process PID {pid} was terminated unexpectedly."
                                try:
                                    with open(sf, "w", encoding="utf-8") as f_out:
                                        json.dump(data, f_out)
                                except Exception:
                                    pass
                        except Exception:
                            pass
                data["log"] = log_content
                return JSONResponse(data)
        except Exception:
            return JSONResponse({"status": "processing", "progress": 0, "log": log_content})

    return JSONResponse({"status": "idle", "progress": 0, "log": log_content})

# ── Route: Jobs — cancel ──────────────────────────────────────────────────────


@router.api_route("/api/v1/jobs/{job_id}/cancel", methods=["DELETE", "POST", "GET"])
async def api_cancel_job(job_id: str):
    """Cancel a running pipeline execution by terminating its process tree.

    Args:
        job_id: Job identifier whose child processes should be terminated.

    Returns:
        JSONResponse confirming cancellation.
    """
    clean_jid = safe_job_id(job_id) if job_id and job_id != "_" else ""

    candidates_to_kill = set()
    if clean_jid and clean_jid in _managed_pids:
        candidates_to_kill.add(_managed_pids[clean_jid])

    raw_status_files = [
        f"data/runtime/temp/status_{clean_jid}.json" if clean_jid and clean_jid != "default" else None,
        "data/runtime/temp/status.json"
    ]
    status_files = []
    for sf_raw in raw_status_files:
        if not sf_raw: continue
        sf_val = resolve_runtime_file(sf_raw, ["data/runtime/temp"])
        if sf_val and sf_val not in status_files:
            status_files.append(sf_val)

    for sf in status_files:
        if os.path.isfile(sf):
            try:
                with open(sf, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and "pid" in data:
                    pid = int(data["pid"])
                    if pid in _managed_pids.values() or (clean_jid and _managed_pids.get(clean_jid) == pid) or not _managed_pids:
                        candidates_to_kill.add(pid)
            except Exception:
                pass

    for pid in candidates_to_kill:
        try:
            kill_process_tree(pid)
        except Exception as e:
            print(f"[WARN] Failed to kill PID {pid}: {e}")

    for k, v in list(_managed_pids.items()):
        if v in candidates_to_kill or (clean_jid and k == clean_jid):
            _managed_pids.pop(k, None)
    save_managed_pids()

    for sf in status_files:
        try:
            with open(sf, "w", encoding="utf-8") as _f_cancel:
                json.dump({"status": "canceled", "phase": "Job canceled by user.", "progress": 0}, _f_cancel)
        except Exception:
            pass

    return JSONResponse({"status": "success", "message": "Stitching canceled."})

# ── Route: Jobs — clear log ───────────────────────────────────────────────────


@router.api_route("/api/v1/jobs/{job_id}/log", methods=["DELETE", "POST", "GET"])
async def api_clear_log(job_id: str):
    """Truncate and empty log files associated with a specified job.

    Args:
        job_id: Target job identifier.

    Returns:
        JSONResponse confirming the log file was emptied.
    """
    clean_jid = safe_job_id(job_id) if job_id and job_id != "_" else ""
    raw_targets = [
        f"data/runtime/logs/pipeline_{clean_jid}.log",
        f"data/runtime/logs/stitch_pipeline_{clean_jid}.log",
        f"data/runtime/logs/backend_stitch_{clean_jid}.log"
    ] if clean_jid and clean_jid != "default" else [
        "data/runtime/logs/pipeline.log",
        "data/runtime/logs/stitch_pipeline.log",
        "data/runtime/logs/backend_stitch.log"
    ]
    for t_raw in raw_targets:
        t = resolve_runtime_file(t_raw, ["data/runtime/logs"])
        if t and os.path.isfile(t):
            try:
                with open(t, "w", encoding="utf-8"): pass
            except Exception: pass
    return JSONResponse({"status": "success", "message": "Log file emptied."})

# ── Route: Jobs — reset ───────────────────────────────────────────────────────


@router.api_route("/api/v1/jobs/{job_id}/reset", methods=["DELETE", "POST", "GET"])
async def api_reset_job(job_id: str):
    """Remove status JSON tracking files, resetting pipeline state back to idle.

    Args:
        job_id: Target job identifier to reset.

    Returns:
        JSONResponse confirming state reset.
    """
    clean_jid = safe_job_id(job_id) if job_id and job_id != "_" else ""
    target_status_files = [f"data/runtime/temp/status_{clean_jid}.json"] if clean_jid and clean_jid != "default" else []
    target_status_files.append("data/runtime/temp/status.json")
    for sf_raw in target_status_files:
        sf = safe_runtime_write_path(os.path.basename(sf_raw), subdir="data/runtime/temp")
        if sf:
            try:
                with open(sf, "w", encoding="utf-8") as _f_reset:
                    json.dump({"status": "idle", "phase": "idle", "progress": 0}, _f_reset)
            except Exception:
                pass
    return JSONResponse({"status": "success", "message": "Status reset to idle."})

# ── Route: Jobs — resume pipeline ────────────────────────────────────────────


@router.api_route("/api/v1/jobs/{job_id}/resume", methods=["POST", "GET"])
async def api_resume_pipeline(job_id: str):
    """Signal a paused stabilization pipeline to resume by writing a resume flag file.

    Args:
        job_id: Target job or output base identifier.

    Returns:
        JSONResponse confirming resume flag creation.
    """
    out_base = re.sub(r'[^a-zA-Z0-9_\-]', '', job_id)
    if out_base == "_":
        out_base = ""
    os.makedirs("data/runtime/work", exist_ok=True)
    ts_str = str(int(time.time()))
    flag_name = f"{out_base}_resume_checkpoints.flag" if out_base else "resume_checkpoints.flag"
    flag_file = safe_runtime_write_path(flag_name, subdir="data/runtime/work")
    if flag_file:
        Path(flag_file).write_text(ts_str, encoding="utf-8")
    return JSONResponse({"status": "success", "message": "Pipeline resume flag created."})

# ── Route: Jobs — save horizon checkpoints ───────────────────────────────────


@router.post("/api/v1/jobs/{job_id}/checkpoints")
@router.post("/api/v1/jobs/checkpoints")
async def api_save_checkpoints(request: Request, job_id: str = ""):
    """Save user-edited horizon tilt checkpoints and resume the pipeline.

    Args:
        request: FastAPI HTTP request containing checkpoints JSON array.
        job_id: Target job identifier.

    Returns:
        JSONResponse confirming saved checkpoint count and target file path.
    """
    qp_base = request.query_params.get("out_base", "")
    out_base = re.sub(r'[^a-zA-Z0-9_\-]', '', job_id or qp_base)
    content_type = request.headers.get("content-type", "")
    parsed = None
    if "application/json" in content_type:
        try:
            parsed = await request.json()
        except Exception:
            parsed = None
    else:
        form = await request.form()
        form_data = dict(form)
        data_param = form_data.get("checkpoints_json", "")
        if data_param:
            try: parsed = json.loads(data_param) if isinstance(data_param, str) else data_param
            except Exception: pass

    os.makedirs("data/runtime/work", exist_ok=True)
    saved_files = []
    has_checkpoints = isinstance(parsed, dict) and "checkpoints" in parsed and isinstance(parsed["checkpoints"], list) and len(parsed["checkpoints"]) > 0

    if has_checkpoints:
        formatted_json = json.dumps(parsed, indent=2, ensure_ascii=False) + "\n"
        from scripts.stabilize_horizon import write_horizon_params_log
        target_name = f"{out_base}_horizon_checkpoints.json" if out_base else "horizon_checkpoints.json"
        target_file = safe_runtime_write_path(target_name, subdir="data/runtime/work")
        if target_file:
            Path(target_file).write_text(formatted_json, encoding="utf-8")
            saved_files.append(target_file)
            log_name = f"{out_base}_horizon_params.log" if out_base else "horizon_params.log"
            log_file = safe_runtime_write_path(log_name, subdir="data/runtime/work")
            if log_file:
                write_horizon_params_log(out_base or "default", parsed, [log_file])
                saved_files.append(log_file)

    ts_str = str(int(time.time()))
    flag_name = f"{out_base}_resume_checkpoints.flag" if out_base else "resume_checkpoints.flag"
    flag_file = safe_runtime_write_path(flag_name, subdir="data/runtime/work")
    if flag_file:
        Path(flag_file).write_text(ts_str, encoding="utf-8")

    return JSONResponse({"status": "success", "saved_files": saved_files, "out_base": out_base, "checkpoints_count": len(parsed["checkpoints"]) if has_checkpoints else 0}, status_code=201)

# ── Route: Jobs — sync horizon params log ──────────────────────────────────
@router.post("/api/v1/jobs/sync-horizon-log")
async def api_sync_horizon_log(request: Request):
    """Sync and write horizon parameter audit log to work directory without resuming pipeline.

    Args:
        request: FastAPI HTTP request containing out_base and log_text or data dict.

    Returns:
        JSONResponse confirming log file synchronization.
    """
    payload = {}
    try:
        body = await request.body()
        if body:
            payload = json.loads(body.decode("utf-8"))
    except Exception:
        pass

    out_base = str(request.query_params.get("out_base", "")).strip() or str(payload.get("out_base", "")).strip()
    log_text = payload.get("log_text", "")
    data = payload.get("data", {})
    video_name = payload.get("video_name", "")

    os.makedirs("data/runtime/work", exist_ok=True)
    saved_files = []
    clean_base = re.sub(r'[^a-zA-Z0-9_\-]', '', out_base)
    log_name = f"{clean_base}_horizon_params.log" if clean_base else "horizon_params.log"
    log_file = safe_runtime_write_path(log_name, subdir="data/runtime/work")

    if log_file:
        if log_text:
            Path(log_file).write_text(log_text, encoding="utf-8")
            saved_files.append(log_file)
        elif data:
            from scripts.stabilize_horizon import write_horizon_params_log
            write_horizon_params_log(clean_base or "default", data, [log_file], video_name=video_name)
            saved_files.append(log_file)

    return JSONResponse({"status": "success", "saved_files": saved_files, "out_base": out_base})

# ── Route: Jobs — auto detect horizon checkpoints ─────────────────────────────


@router.api_route("/api/v1/jobs/detect-checkpoints", methods=["GET", "POST"])
async def api_detect_checkpoints(request: Request):
    """Detect key horizon tilt inflection points via IMU telemetry or optical motion.

    Args:
        request: FastAPI HTTP request containing input paths and sensitivity parameters.

    Returns:
        JSONResponse containing detected checkpoints list and metadata.
    """
    if request.method == "GET":
        form_data = dict(request.query_params)
    else:
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            try: form_data = await request.json()
            except Exception: form_data = {}
        else:
            form = await request.form()
            form_data = dict(form)
    def gp(n, d=""): return form_data.get(n, d)

    out_base         = re.sub(r'[^a-zA-Z0-9_\-]', '', str(gp("out_base", "")))
    video_file_raw   = str(gp("video", ""))
    video_file       = resolve_input_file(video_file_raw)
    clean_video      = os.path.basename(video_file) if video_file else os.path.basename(video_file_raw)
    clean_video_no_ext = Path(clean_video).stem
    source_type      = str(gp("source", "auto"))
    density          = str(gp("density", "balanced"))
    fps              = float(gp("fps", "29.97"))
    baseline_pitch   = float(gp("baseline_pitch", "0.0"))
    baseline_roll    = float(gp("baseline_roll", "0.0"))
    lock_roll        = str(gp("lock_roll", "0")).lower() in ["1", "true", "yes"]
    optical_target   = str(gp("optical_target", "ground")).lower()
    roll_damping     = float(gp("roll_damping", "0.70"))
    max_cps          = int(gp("max_checkpoints")) if gp("max_checkpoints") else None

    is_post_stabilized = any(k in clean_video for k in ['_telemetry', '_kopf', '_kabsch', '_vidstab', '_horizon', '_3_'])

    epsilon = 2.5
    if gp("epsilon"):
        try:
            epsilon = float(gp("epsilon"))
        except (ValueError, TypeError):
            epsilon = 2.5
    elif density == "smooth": epsilon = 3.5
    elif density in ["fine", "detailed"]: epsilon = 1.5
    elif density in ["ultra", "ultra_dense", "ultradense"]: epsilon = 0.75

    found_telemetry = None
    telemetry_cands = []
    if out_base:
        telemetry_cands.append(f"data/runtime/work/{out_base}_telemetry.txt")
        telemetry_cands.append(f"data/runtime/work/{out_base}.gcsv")
    if clean_video_no_ext:
        telemetry_cands.append(f"data/runtime/work/{clean_video_no_ext}_telemetry.txt")
        telemetry_cands.append(f"data/runtime/work/{clean_video_no_ext}.gcsv")
        telemetry_cands.append(f"data/input/videos/{clean_video_no_ext}_telemetry.txt")
        telemetry_cands.append(f"data/input/videos/{clean_video_no_ext}.gcsv")
        telemetry_cands.append(f"data/input/videos/{clean_video_no_ext}.MP4.gcsv")
        telemetry_cands.append(f"data/input/videos/{clean_video_no_ext}.mp4.gcsv")
    if video_file:
        v_dir = os.path.dirname(video_file) or "."
        v_base = os.path.splitext(os.path.basename(video_file))[0]
        telemetry_cands.append(os.path.join(v_dir, f"{v_base}_telemetry.txt"))
        telemetry_cands.append(os.path.join(v_dir, f"{v_base}.gcsv"))
        telemetry_cands.append(os.path.join(v_dir, f"{os.path.basename(video_file)}.gcsv"))

    for tc_raw in telemetry_cands:
        tc = resolve_runtime_file(tc_raw, ["data/input/videos", "data/runtime/work", "data/input/gps"])
        if tc and os.path.isfile(tc) and os.path.getsize(tc) > 50:
            found_telemetry = tc
            break

    # On-demand telemetry extraction if not already on disk
    if not found_telemetry and video_file and os.path.exists(video_file):
        work_telem = safe_runtime_write_path(f"{clean_video_no_ext}_telemetry.txt", subdir="data/runtime/work")
        if work_telem and os.path.isfile(work_telem) and os.path.getsize(work_telem) > 50:
            found_telemetry = work_telem
        elif work_telem:
            try:
                os.makedirs("data/runtime/work", exist_ok=True)
                ext_cmd = [sys.executable, "-B", "scripts/extract_telemetry.py", video_file, work_telem, "--source", "auto"]
                await run_async_subprocess(*ext_cmd)
                if os.path.isfile(work_telem) and os.path.getsize(work_telem) > 50:
                    found_telemetry = work_telem
            except Exception:
                pass

    found_motion = None
    motion_cands = []
    if out_base:
        motion_cands.append(f"data/runtime/work/{out_base}.kopf360motion")
        motion_cands.append(f"data/runtime/work/{out_base}.kabsch360motion")
    if clean_video_no_ext:
        motion_cands.append(f"data/runtime/work/{clean_video_no_ext}.kopf360motion")
        motion_cands.append(f"data/runtime/work/{clean_video_no_ext}.kabsch360motion")
    for mc_raw in motion_cands:
        mc = resolve_runtime_file(mc_raw, ["data/runtime/work"])
        if mc and os.path.isfile(mc) and os.path.getsize(mc) > 50:
            found_motion = mc
            break

    found_video     = video_file if (video_file and os.path.exists(video_file) and os.path.getsize(video_file) > 1000) else None

    selected_source = None
    target_args = []
    neutral_flags = []

    if source_type == "cadence":
        try:
            from scripts.stabilize_horizon import seed_cadence_checkpoints, get_video_info
            total_f = 0
            if found_video:
                v_frames, v_fps = get_video_info(found_video)
                if v_frames: total_f = v_frames
                if v_fps > 0 and (not gp("fps") or float(gp("fps")) in (29.97, 30.0)):
                    fps = v_fps
            if not total_f:
                total_f = int(float(gp("total_frames", "300")))
            cadence_sec = {"smooth": 2.0, "balanced": 0.5, "fine": 0.33, "detailed": 0.25, "ultra": 0.15, "ultra_dense": 0.15}.get(density, 0.5)
            cps = seed_cadence_checkpoints(total_f, fps=fps, interval_sec=cadence_sec, max_checkpoints=max_cps, adaptive=(max_cps is not None),
                                           initial_angles=(baseline_pitch, baseline_roll, 0.0),
                                           telemetry_file=found_telemetry, motion_file=found_motion,
                                           roll_damping=roll_damping)
            params_meta = {
                "detection_type": "Time Interval Cadence",
                "source": "cadence",
                "density": density,
                "interval_sec": cadence_sec,
                "epsilon": epsilon,
                "roll_damping": roll_damping,
                "baseline_pitch": baseline_pitch,
                "baseline_roll": baseline_roll,
                "fps": fps,
                "total_frames": total_f,
                "video": clean_video,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            data = {"version": "1.0", "sourceType": "cadence", "fps": fps, "totalFrames": total_f, "ignoreYaw": True, "parameters": params_meta, "checkpoints": cps}
            if out_base:
                from scripts.stabilize_horizon import write_horizon_params_log
                write_horizon_params_log(out_base, data, [f"data/runtime/work/{out_base}_horizon_params.log"], video_name=clean_video)
            return JSONResponse({"status": "success", "source": "cadence", "is_post_stabilized": is_post_stabilized, "source_file": found_video or "cadence", "density": density, "epsilon": epsilon, "checkpoints_count": len(cps), "data": data})
        except Exception as e:
            print(f"[ERROR] Cadence generation error: {e}", file=sys.stderr)
            return JSONResponse({"status": "error", "error": "Cadence generation failed."}, status_code=500)
    if source_type in ["pitch_extrema", "pitch"] or (source_type == "auto" and (found_telemetry or found_motion) and not is_post_stabilized):
        try:
            from scripts.stabilize_horizon import detect_pitch_extrema_checkpoints, get_video_info
            total_f = 0
            if found_video:
                v_frames, v_fps = get_video_info(found_video)
                if v_frames: total_f = v_frames
                if v_fps > 0 and (not gp("fps") or float(gp("fps")) in (29.97, 30.0)):
                    fps = v_fps
            if not total_f:
                total_f = int(float(gp("total_frames", "300")))
            prominence = float(gp("min_prominence", gp("prominence", "0.8")))
            cps = detect_pitch_extrema_checkpoints(total_f, fps=fps, min_prominence_deg=prominence, max_checkpoints=max_cps,
                                                   telemetry_file=found_telemetry, motion_file=found_motion,
                                                   video_file=found_video, neutral_baseline=False,
                                                   baseline_pitch=baseline_pitch, baseline_roll=baseline_roll,
                                                   roll_damping=roll_damping)
            crests = sum(1 for c in cps if c.get("type") == "crest")
            troughs = sum(1 for c in cps if c.get("type") == "trough")
            params_meta = {
                "detection_type": "Smart Gait & Stride AI",
                "source": "pitch_extrema",
                "density": density,
                "min_prominence": prominence,
                "epsilon": epsilon,
                "roll_damping": roll_damping,
                "baseline_pitch": baseline_pitch,
                "baseline_roll": baseline_roll,
                "fps": fps,
                "total_frames": total_f,
                "video": clean_video,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            data = {"version": "1.0", "sourceType": "pitch_extrema", "fps": fps, "totalFrames": total_f, "ignoreYaw": True, "parameters": params_meta, "checkpoints": cps, "crests": crests, "troughs": troughs}
            if out_base:
                from scripts.stabilize_horizon import write_horizon_params_log
                write_horizon_params_log(out_base, data, [f"data/runtime/work/{out_base}_horizon_params.log"], video_name=clean_video)
            return JSONResponse({"status": "success", "source": "pitch_extrema", "is_post_stabilized": is_post_stabilized, "source_file": found_telemetry or found_motion or found_video or "pitch_extrema", "density": density, "epsilon": epsilon, "checkpoints_count": len(cps), "data": data})
        except Exception as e:
            print(f"[ERROR] Pitch extrema generation error: {e}", file=sys.stderr)
            return JSONResponse({"status": "error", "error": "Pitch extrema generation failed."}, status_code=500)

    if (source_type == "vision" or source_type == "auto") and found_video:
        selected_source = "vision"; target_args = ["--video-file", found_video]
    elif found_telemetry:
        selected_source = "telemetry"; target_args = ["--telemetry", found_telemetry]
    elif found_motion:
        selected_source = "motion"; target_args = ["--motion-file", found_motion]
    elif found_video:
        selected_source = "vision"; target_args = ["--video-file", found_video]

    if not selected_source or not target_args:
        return JSONResponse({"status": "error", "error": "No telemetry (*_telemetry.txt), motion (*.kopf360motion), or valid video file found for detection."}, status_code=422)

    extra_flags = ["--optical-target", optical_target]
    if lock_roll:
        extra_flags.append("--lock-roll")
    cmd = [sys.executable, "-B", "scripts/stabilize_horizon.py", "--auto-detect", *target_args, *neutral_flags, *extra_flags, "--fps", str(fps), "--baseline-pitch", str(baseline_pitch), "--baseline-roll", str(baseline_roll), "--epsilon", str(epsilon)]
    stdout, stderr = await run_async_subprocess(*cmd)
    output_str = stdout.decode("utf-8", errors="ignore")

    try:
        data = json.loads(output_str)
        if isinstance(data, dict) and "checkpoints" in data:
            if lock_roll:
                for cp in data.get("checkpoints", []):
                    cp["roll"] = 0.0
            src_file = found_telemetry or found_motion if selected_source in ["telemetry", "neutral"] else (found_motion if selected_source == "motion" else found_video)
            strat_name = "Visual Horizon AI (Pitch-Only)" if (selected_source == "vision" and lock_roll) else ("Visual Horizon AI" if selected_source == "vision" else f"IMU {selected_source.capitalize()}")
            params_meta = {
                "detection_type": strat_name,
                "source": selected_source,
                "density": density,
                "epsilon": epsilon,
                "optical_target": optical_target,
                "baseline_pitch": baseline_pitch,
                "baseline_roll": 0.0 if lock_roll else baseline_roll,
                "lock_roll": lock_roll,
                "fps": fps,
                "video": clean_video,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            data["parameters"] = params_meta
            if out_base:
                from scripts.stabilize_horizon import write_horizon_params_log
                write_horizon_params_log(out_base, data, [f"data/runtime/work/{out_base}_horizon_params.log"], video_name=clean_video)
            return JSONResponse({"status": "success", "source": selected_source, "is_post_stabilized": is_post_stabilized, "source_file": src_file, "density": density, "epsilon": epsilon, "checkpoints_count": len(data["checkpoints"]), "data": data})
    except Exception:
        pass

    return JSONResponse({"status": "error", "error": f"Failed running auto-detect: {output_str[:500] or stderr.decode('utf-8', errors='ignore')[:500]}"}, status_code=500)

# ── Route: Jobs — choose transforms ──────────────────────────────────────────


@router.post("/api/v1/jobs/{job_id}/transforms")
async def api_choose_transforms(job_id: str, request: Request):
    """Persist user-selected stabilization transforms to unblock interactive pipeline execution.

    Args:
        job_id: Unique pipeline execution identifier.
        request: FastAPI HTTP request containing list of chosen transform names.

    Returns:
        JSONResponse confirming recorded transform selections.
    """
    job_id = re.sub(r'[^a-zA-Z0-9_\-]', '', job_id)
    content_type = request.headers.get("content-type", "")
    chosen_list = []
    if "application/json" in content_type:
        try:
            body = await request.json()
            chosen_list = body.get("chosen_transforms", [])
        except Exception:
            pass
    else:
        form = await request.form()
        form_data = dict(form)
        chosen_raw = form_data.get("chosen_transforms", "")
        if isinstance(chosen_raw, str):
            try:
                chosen_list = json.loads(chosen_raw)
            except Exception:
                chosen_list = [s.strip() for s in chosen_raw.split(",") if s.strip()]

    os.makedirs("data/runtime/temp", exist_ok=True)
    payload = {"job_id": job_id, "chosen_transforms": chosen_list, "timestamp": time.time()}
    json_content = json.dumps(payload, indent=2) + "\n"
    if job_id:
        clean_jid = safe_job_id(job_id)
        base_dir_abs = os.path.realpath(os.path.abspath(str(BASE_DIR)))
        temp_dir_abs = os.path.realpath(os.path.abspath(os.path.join(base_dir_abs, "data", "runtime", "temp")))
        target_ct = os.path.realpath(os.path.abspath(os.path.join(temp_dir_abs, f"chosen_transforms_{clean_jid}.json")))
        if target_ct.startswith(temp_dir_abs + os.sep):
            with open(target_ct, "w", encoding="utf-8") as f:
                f.write(json_content)
    with open(os.path.join(str(BASE_DIR), "data", "runtime", "temp", "chosen_transforms.json"), "w", encoding="utf-8") as f:
        f.write(json_content)
    return JSONResponse({"status": "success", "message": "Chosen transforms saved.", "chosen_transforms": chosen_list})

# ── Route: Reports — get content ─────────────────────────────────────────────


@router.get("/api/v1/reports")
async def api_get_report(file: str = Query("")):
    """Retrieve the text content of a generated analysis report file.

    Args:
        file: Report filename to fetch from allowed runtime directories.

    Returns:
        JSONResponse containing report text content or error status.
    """
    if not file or str(file).strip().startswith("-") or "\0" in str(file):
        return JSONResponse({"status": "error", "error": "Report file not found or inaccessible."}, status_code=404)
    raw_path   = file.replace('\\', '/').strip()
    clean_name = os.path.basename(raw_path)
    if not clean_name or clean_name in (".", ".."):
        return JSONResponse({"status": "error", "error": "Report file not found or inaccessible."}, status_code=404)

    base_dir_abs = os.path.realpath(os.path.abspath(str(BASE_DIR)))
    target_path = None
    for base in ["data/output", "data/runtime/work", "data/runtime/temp", "data/runtime/logs"]:
        sub_dir_abs = os.path.realpath(os.path.abspath(os.path.join(base_dir_abs, base)))
        candidate = os.path.realpath(os.path.abspath(os.path.join(sub_dir_abs, clean_name)))
        if not candidate.startswith(sub_dir_abs + os.sep):
            continue
        if os.path.isfile(candidate):
            target_path = candidate
            break

    if target_path and os.path.isfile(target_path):
        try:
            with open(target_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(512 * 1024)
        except Exception as e:
            print(f"[ERROR] Failed to read report file {target_path}: {e}", file=sys.stderr)
            return JSONResponse({"status": "error", "error": "Failed to read report file."}, status_code=500)
        return JSONResponse({"status": "success", "file": clean_name, "content": content})
    return JSONResponse({"status": "error", "error": "Report file not found or inaccessible."}, status_code=404)


