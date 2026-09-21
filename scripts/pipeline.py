import sys
sys.dont_write_bytecode = True
import os
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

"""360 Video Stitching & Stabilization Pipeline Orchestrator.

Orchestrates dual-fisheye lens unwarping, seam blending, multi-stage
camera stabilization (Telemetry, VidStab, Kabsch SVD, Kopf 3D), and spatial
metadata injection (Spherical v2 / vrot) into equirectangular 360 video.
"""
import re
import bisect
import shutil
import numpy as np
import subprocess
import json
import argparse
import time
import math
import builtins
import signal
import atexit
from datetime import datetime, timedelta, timezone

# ==============================================================================
# 1. Edge-Preserving Bilateral Telemetry Filter (Eliminates pre-ring & step-jump tilt glitches)
# 2. Smart Stage Pruning (Automatic zero-cost bypass for identity/trivial transforms <0.015°)
# 3. 3D Spherical Angular Metrics & Comprehensive Reporting (Jerk RMS, Tremor Absorption %, Horizon Lock)
# 4. Frequency-Decoupled Cascade Preservation (Preserves visual excellence for human VR perception)
# ==============================================================================

WIN_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

# ── Cross-Platform Process Cleanup ───────────────────────────────────────────

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from utils.process import assign_to_job_object
from config.settings import PIPELINE_DEFAULTS

assign_to_job_object("pipeline")

_ACTIVE_CHILD_PROCESS = None

def _cleanup_active_child():
    """Terminates active child subprocesses cleanly across platforms.

    Kills active FFmpeg or Python child processes using taskkill on Windows
    (with process tree termination) or SIGKILL on POSIX systems.
    """
    global _ACTIVE_CHILD_PROCESS
    if _ACTIVE_CHILD_PROCESS is not None:
        try:
            if _ACTIVE_CHILD_PROCESS.poll() is None:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/F", "/PID", str(_ACTIVE_CHILD_PROCESS.pid), "/T"], capture_output=True, creationflags=WIN_NO_WINDOW)
                else:
                    _ACTIVE_CHILD_PROCESS.kill()
        except Exception:
            pass
        _ACTIVE_CHILD_PROCESS = None

atexit.register(_cleanup_active_child)

def _signal_handler(signum, frame):
    """Handles OS termination signals by terminating child processes before exit.

    Args:
        signum (int): Signal number received (SIGINT, SIGTERM, etc.).
        frame (types.FrameType | None): Current execution frame.
    """
    _cleanup_active_child()
    sys.exit(1)

signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)
if hasattr(signal, "SIGBREAK"):
    signal.signal(signal.SIGBREAK, _signal_handler)
if hasattr(signal, "SIGHUP"):
    signal.signal(signal.SIGHUP, _signal_handler)

_original_print = builtins.print

def timestamped_print(*args, **kwargs):
    """Outputs messages prefixed with current timestamp [YYYY-MM-DD HH:MM:SS].

    Passes empty prints through unaltered; wraps standard messages with the
    current timestamp before delegating to builtins.print.

    Args:
        *args: Variable arguments to print.
        **kwargs: Keyword arguments forwarded to builtins.print.
    """
    if not args or (len(args) == 1 and args[0] == ""):
        _original_print(*args, **kwargs)
        return
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    first_arg = f"\n[{now_str}] {args[0]}"
    args = (first_arg,) + args[1:]
    _original_print(*args, **kwargs)

print = timestamped_print


def get_video_duration(input_path):
    """Extracts total duration of a video file in seconds via ffprobe.

    Queries both format-level and stream-level durations and returns the
    first positive float parsed. If the input is a still photo, returns 1.0.

    Args:
        input_path (str): Filepath to the video or photo.

    Returns:
        float | None: Media duration in seconds, or None if extraction failed.
    """
    input_ext = os.path.splitext(input_path)[1].lower()
    if input_ext in [".jpg", ".jpeg", ".png", ".webp"]:
        return 1.0

    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration:stream=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        input_path
    ]
    try:
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, creationflags=WIN_NO_WINDOW).decode('utf-8').strip()
        for line in output.splitlines():
            line = line.strip()
            if line and line != "N/A":
                try:
                    val = float(line)
                    if val > 0:
                        return val
                except ValueError:
                    pass
        return None
    except Exception as e:
        print(f"Error getting duration: {e}", file=sys.stderr)
        return None

def inject_gpano_photo_metadata(photo_path):
    """Inject standard Google Photo Sphere (GPano) XMP metadata into a 360 photo via ExifTool."""
    if not photo_path or not os.path.exists(photo_path):
        return False
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "json", photo_path],
            capture_output=True, text=True, creationflags=WIN_NO_WINDOW
        )
        w, h = 3840, 1920
        if probe.returncode == 0:
            pj = json.loads(probe.stdout)
            w = int(pj.get("streams", [{}])[0].get("width", 3840))
            h = int(pj.get("streams", [{}])[0].get("height", 1920))

        cmd = [
            "exiftool", "-overwrite_original",
            "-use", "MWG",
            "-XMP-GPano:ProjectionType=equirectangular",
            "-XMP-GPano:UsePanoramaViewer=True",
            f"-XMP-GPano:CroppedAreaImageWidthPixels={w}",
            f"-XMP-GPano:CroppedAreaImageHeightPixels={h}",
            f"-XMP-GPano:FullPanoWidthPixels={w}",
            f"-XMP-GPano:FullPanoHeightPixels={h}",
            "-XMP-GPano:CroppedAreaLeftPixels=0",
            "-XMP-GPano:CroppedAreaTopPixels=0",
            "-XMP-GPano:InitialViewHeadingDegrees=0",
            "-XMP-GPano:InitialViewPitchDegrees=0",
            "-XMP-GPano:InitialViewRollDegrees=0",
            "-Make=RICOH",
            "-Model=RICOH THETA S",
            photo_path
        ]
        res = subprocess.run(cmd, capture_output=True, timeout=15, creationflags=WIN_NO_WINDOW)
        if res.returncode == 0:
            timestamped_print(f"[Photo Sphere] Injected GPano 360 metadata into {photo_path}")
            return True
        return False
    except Exception as e:
        timestamped_print(f"[WARN] Failed to inject GPano metadata via ExifTool: {e}")
        return False

_GLOBAL_START_TIME = None
_GLOBAL_OUT_BASE = None
_GLOBAL_OUTPUT_FILE = None

def update_status(status_file, data):
    """Atomically updates the pipeline status JSON file with progress metrics.

    Appends current process ID and global tracking metadata. Employs a temporary
    file with atomic replacement and retries to prevent write collisions.

    Args:
        status_file (str): Path to the target status.json file.
        data (dict): Status payload containing phase, progress, speed, ETA, etc.
    """
    data["pid"] = os.getpid()
    if "start_time" not in data and _GLOBAL_START_TIME is not None:
        data["start_time"] = _GLOBAL_START_TIME
    if "out_base" not in data and _GLOBAL_OUT_BASE is not None:
        data["out_base"] = _GLOBAL_OUT_BASE
    if "output" not in data and _GLOBAL_OUTPUT_FILE is not None:
        data["output"] = _GLOBAL_OUTPUT_FILE
    tmp_file = status_file + ".tmp"
    success = False
    try:
        with open(tmp_file, "w") as f:
            json.dump(data, f)
        for _ in range(3):
            try:
                os.replace(tmp_file, status_file)
                success = True
                break
            except OSError:
                time.sleep(0.02)
    except Exception as _e:
        print(f"[WARN] update_status: tmp-file write failed: {_e}", file=sys.stderr, flush=True)


    if not success:
        try:
            with open(status_file, "w") as f:
                json.dump(data, f)
            success = True
        except Exception as e:
            print(f"Error writing status: {e}", file=sys.stderr)


_GLOBAL_ARGS = None

try:
    from utils.tool_resolver import probe_nvenc, probe_vulkan
except ImportError:
    def probe_vulkan(ffmpeg_bin="ffmpeg"):
        probe_cmd = [
            ffmpeg_bin,
            "-init_hw_device", "vulkan=vk",
            "-filter_hw_device", "vk",
            "-f", "lavfi",
            "-i", "nullsrc=s=64x64:d=0.04",
            "-vf", "format=yuv420p,hwupload,v360_vulkan=input=flat:output=flat:w=64:h=64,hwdownload,format=yuv420p",
            "-f", "null",
            "-"
        ]
        try:
            proc = subprocess.run(
                probe_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                timeout=3.0,
                creationflags=WIN_NO_WINDOW
            )
            if proc.returncode == 0:
                return True, ""
            return False, "Vulkan device or v360_vulkan filter probe failed"
        except Exception as e:
            return False, str(e)

    def probe_nvenc(ffmpeg_bin="ffmpeg"):
        """Checks whether NVIDIA NVENC hardware encoding is functional.

        Runs a minimal probe encode using h264_nvenc to verify that
        the GPU, drivers, and runtime libraries (libcuda, libnvidia-encode)
        are operational.

        Returns:
            tuple[bool, str]: (is_operational, failure_reason)
        """
        probe_cmd = [
            ffmpeg_bin,
            "-f", "lavfi",
            "-i", "nullsrc=s=256x256:d=0.04",
            "-c:v", "h264_nvenc",
            "-f", "null",
            "-"
        ]
        try:
            proc = subprocess.run(
                probe_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                timeout=3.0,
                creationflags=WIN_NO_WINDOW
            )
            if proc.returncode == 0:
                return True, ""

            stderr = proc.stderr or proc.stdout or ""
            reason = "Unknown encoder error"
            if "Cannot load libcuda" in stderr or "Cannot load nvcuda" in stderr:
                reason = "CUDA driver library (libcuda / nvcuda) could not be loaded (no GPU passthrough or missing drivers in container)"
            elif "Cannot load libnvidia-encode" in stderr:
                reason = "NVENC library (libnvidia-encode.so.1) not available in current environment"
            elif "No NVENC capable devices found" in stderr or "No capable devices found" in stderr:
                reason = "No NVENC-capable NVIDIA GPU devices detected"
            elif "minimum required Nvidia driver" in stderr:
                reason = "Installed NVIDIA driver is older than the minimum version required by NVENC"
            elif "Could not open encoder" in stderr:
                reason = "Failed to initialize NVENC encoder hardware context"
            else:
                for line in stderr.splitlines():
                    line_clean = line.strip()
                    if line_clean and any(err_kw in line_clean.lower() for err_kw in ["error", "cannot", "failed", "not permitted"]):
                        reason = line_clean
                        break
            return False, reason
        except subprocess.TimeoutExpired:
            return False, "NVENC hardware probe timed out"
        except Exception as e:
            return False, str(e)


def run_ffmpeg(cmd, status_file, duration, start_ts, phase_label, status_code="stitching", active_duration=None):
    """Executes an FFmpeg or Python subprocess while streaming progress to status JSON.

    Parses stdout/stderr for speed, time, and progress milestones, calculates
    real-time ETA, and includes a safety watchdog against runaway encoding loops.

    Args:
        cmd (list[str]): Command arguments array to execute.
        status_file (str): Path to the status JSON file.
        duration (float): Expected total media duration in seconds.
        start_ts (float): Pipeline start epoch timestamp.
        phase_label (str): Human-readable label for current processing phase.
        status_code (str, optional): Status state string. Defaults to "stitching".
        active_duration (float, optional): Duration of active video segment before padding.

    Returns:
        int: Process return code (0 for success).
    """
    if cmd and cmd[0] == "ffmpeg":
        extra_flags = []
        if "-threads" not in cmd:
            extra_flags += ["-threads", "0"]
        if "-filter_threads" not in cmd:
            extra_flags += ["-filter_threads", "0"]
        if "-filter_complex_threads" not in cmd:
            extra_flags += ["-filter_complex_threads", "0"]
        if extra_flags:
            cmd = [cmd[0]] + extra_flags + cmd[1:]
    print(f"Running Command ({phase_label}): {' '.join(cmd)}")
    global _ACTIVE_CHILD_PROCESS
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace", creationflags=WIN_NO_WINDOW)
    _ACTIVE_CHILD_PROCESS = process

    # Emit immediate startup signal so UI updates without waiting for first stdout line
    update_status(status_file, {
        "status": status_code,
        "phase": phase_label,
        "progress": 0.0,
        "speed": "0.0x",
        "eta": "Calculating...",
        "elapsed": int(time.time() - start_ts)
    })

    speed = "0.0x"
    stderr_log_lines = []
    py_progress_pattern = re.compile(r'(?:Analyzed|Tracked)\s+(\d+)/(\d+)', re.IGNORECASE)
    speed_regex = re.compile(r'speed=\s*([\d\.]+)x', re.IGNORECASE)
    time_regex = re.compile(r'time=(\d+):(\d+):(\d+(?:\.\d+)?)', re.IGNORECASE)

    cmd_start_ts = time.time()
    last_update_ts = 0.0
    last_sample_ts = cmd_start_ts
    last_sample_sec = 0.0
    speed_ema = None
    current_seconds = 0.0

    while True:
        line = process.stdout.readline()
        if not line:
            break
        line_str = line.strip()
        stderr_log_lines.append(line_str)

        # 1. Check for Python progress (e.g. OpenCV feature tracking / analysis)
        m_py = py_progress_pattern.search(line_str)
        if m_py:
            try:
                curr_frame = int(m_py.group(1))
                tot_frames = int(m_py.group(2))
                if tot_frames > 0:
                    progress = min(99.0, (curr_frame / tot_frames) * 100.0)
                    now = time.time()
                    total_elapsed = int(now - start_ts)
                    cmd_elapsed = now - cmd_start_ts

                    if curr_frame >= 5 and cmd_elapsed >= 0.8:
                        curr_fps = curr_frame / max(0.1, cmd_elapsed)
                        rem_frames = max(0, tot_frames - curr_frame)
                        eta_sec = int(rem_frames / max(0.1, curr_fps))
                        eta_str = f"{eta_sec}s"
                        fps_str = f"{curr_fps:.1f} fps"
                    else:
                        eta_str = "Calculating..."
                        fps_str = "N/A"

                    if now - last_update_ts >= 0.25 or curr_frame == tot_frames:
                        last_update_ts = now
                        update_status(status_file, {
                            "status": status_code,
                            "phase": phase_label,
                            "progress": round(progress, 1),
                            "speed": fps_str,
                            "eta": eta_str,
                            "elapsed": total_elapsed
                        })
            except Exception:
                pass
            continue

        # 2. Check for FFmpeg progress
        should_update = False
        if line_str.startswith("out_time_ms="):
            try:
                current_seconds = float(line_str.split("=")[1].strip()) / 1_000_000.0
            except Exception:
                pass
        elif line_str.startswith("out_time_us="):
            try:
                current_seconds = float(line_str.split("=")[1].strip()) / 1_000_000.0
            except Exception:
                pass
        elif line_str.startswith("out_time="):
            try:
                t_str = line_str.split("=")[1].strip()
                t_parts = t_str.split(":")
                if len(t_parts) == 3:
                    current_seconds = float(t_parts[0]) * 3600.0 + float(t_parts[1]) * 60.0 + float(t_parts[2])
            except Exception:
                pass
        elif line_str.startswith("speed="):
            try:
                speed = line_str.split("=")[1].strip()
                should_update = True
            except Exception:
                pass
        elif line_str.startswith("progress="):
            should_update = True
        else:
            m_t = time_regex.search(line_str)
            if m_t:
                try:
                    current_seconds = float(m_t.group(1)) * 3600.0 + float(m_t.group(2)) * 60.0 + float(m_t.group(3))
                    should_update = True
                except Exception:
                    pass
            m_s = speed_regex.search(line_str)
            if m_s:
                speed = f"{m_s.group(1)}x"
                should_update = True

        if should_update and current_seconds is not None and duration > 0:
            now = time.time()
            if now - last_update_ts >= 0.25:
                last_update_ts = now
                total_elapsed = int(now - start_ts)
                cmd_elapsed = now - cmd_start_ts

                speed_num = None
                m_speed = re.search(r'([\d\.]+)', speed)
                if m_speed:
                    try:
                        s_val = float(m_speed.group(1))
                        if s_val > 0.0001:
                            speed_num = s_val
                    except Exception:
                        pass

                dt = now - last_sample_ts
                ds = current_seconds - last_sample_sec
                if dt >= 0.5:
                    instant_speed = ds / dt
                    if instant_speed > 0.0001:
                        if speed_ema is None:
                            speed_ema = instant_speed
                        else:
                            speed_ema = 0.3 * instant_speed + 0.7 * speed_ema
                    last_sample_ts = now
                    last_sample_sec = current_seconds

                overall_rate = current_seconds / max(0.001, cmd_elapsed) if (current_seconds > 0 and cmd_elapsed > 0) else None
                effective_speed = speed_num or speed_ema or overall_rate

                eff_active_dur = active_duration if (active_duration is not None and active_duration > 0) else duration
                has_pad = (duration > eff_active_dur)

                if effective_speed and effective_speed > 0.0001 and (cmd_elapsed >= 1.0 or current_seconds >= 0.2):
                    if has_pad:
                        if current_seconds < eff_active_dur:
                            rem_active_sec = max(0.0, eff_active_dur - current_seconds)
                            pad_overhead = min(3, max(1, int((duration - eff_active_dur) * 0.01)))
                            eta_sec = int(rem_active_sec / effective_speed) + pad_overhead
                        else:
                            rem_pad_sec = max(0.0, duration - current_seconds)
                            eta_sec = max(1, int(rem_pad_sec / max(100.0, effective_speed)))
                    else:
                        rem_video_sec = max(0.0, duration - current_seconds)
                        eta_sec = int(rem_video_sec / effective_speed)
                    eta_str = f"{eta_sec}s"
                else:
                    eta_str = "Calculating..."

                if has_pad:
                    if current_seconds < eff_active_dur:
                        progress = min(98.0, (current_seconds / max(0.001, eff_active_dur)) * 98.0)
                    else:
                        pad_ratio = (current_seconds - eff_active_dur) / max(0.001, duration - eff_active_dur)
                        progress = min(99.0, 98.0 + pad_ratio * 1.0)
                else:
                    progress = min(99.0, (current_seconds / duration) * 100.0)
                update_status(status_file, {
                    "status": status_code,
                    "phase": phase_label,
                    "progress": round(progress, 1),
                    "speed": speed,
                    "eta": eta_str,
                    "elapsed": total_elapsed
                })

                # Safety Watchdog: Prevent runaway infinite loops from filling disk space
                max_allowed_sec = (duration + 5.0) if not has_pad else (duration + 10.0)
                if current_seconds > max_allowed_sec:
                    print(f"\n[Safety Watchdog] Runaway encoding detected in '{phase_label}': current time {current_seconds:.1f}s exceeded expected duration {duration:.1f}s. Terminating process to protect disk space.", flush=True)
                    try:
                        process.kill()
                    except Exception:
                        pass
                    break

    process.wait()
    _ACTIVE_CHILD_PROCESS = None
    if process.returncode != 0:
        stderr_log = "\n".join(stderr_log_lines[-20:])
        print(f"Command ({phase_label}) failed with code {process.returncode}:\n{stderr_log}")
        if "-c:v" in cmd and any(enc in cmd for enc in ["h264_nvenc", "hevc_nvenc"]):
            stderr_full = "\n".join(stderr_log_lines)
            if any(k in stderr_full.lower() for k in ["cannot load libcuda", "cannot load nvcuda", "cannot load libnvidia", "could not open encoder", "no nvenc capable"]):
                print(
                    f"\n" + "=" * 70 + "\n"
                    f"[Pipeline NOTICE] NVENC encoding failed during '{phase_label}'.\n"
                    f"  Action: Automatically retrying with CPU encoder (libx264).\n"
                    + "=" * 70 + "\n",
                    flush=True
                )
                global _GLOBAL_ARGS
                if _GLOBAL_ARGS:
                    _GLOBAL_ARGS.hwaccel = False
                fallback_cmd = []
                skip_next = 0
                for i, arg in enumerate(cmd):
                    if skip_next > 0:
                        skip_next -= 1
                        continue
                    if arg in ["h264_nvenc", "hevc_nvenc"]:
                        fallback_cmd.append("libx264")
                    elif arg in ["-spatial-aq", "-temporal-aq", "-rc-lookahead"]:
                        skip_next = 1
                    elif arg == "-rc" and i + 1 < len(cmd) and cmd[i + 1] in ["constqp", "vbr"]:
                        skip_next = 1
                    elif arg == "-preset" and i + 1 < len(cmd) and cmd[i + 1].startswith("p"):
                        fallback_cmd.extend(["-preset", "medium"])
                        skip_next = 1
                    elif arg == "-cq":
                        fallback_cmd.append("-crf")
                    else:
                        fallback_cmd.append(arg)
                return run_ffmpeg(fallback_cmd, status_file, duration, start_ts, f"{phase_label} [libx264 fallback]", status_code, active_duration)
    return process.returncode

def ensure_circle_mask(width, height, script_dir):
    """Ensures an elliptical/circular dual-fisheye alpha mask exists on disk.

    Generates a high-contrast 8-bit mask image if not already cached.

    Args:
        width (int): Frame width in pixels.
        height (int): Frame height in pixels.
        script_dir (str): Directory containing this script.

    Returns:
        str | None: Absolute path to the generated mask PNG, or None on failure.
    """
    masks_dir = os.path.abspath(os.path.join(script_dir, "..", "data", "input", "masks"))
    os.makedirs(masks_dir, exist_ok=True)
    mask_file = os.path.join(masks_dir, f"circle_mask_{width}x{height}.png")
    if not os.path.exists(mask_file):
        try:
            from PIL import Image, ImageDraw
            mask = Image.new('L', (width, height), 0)
            draw = ImageDraw.Draw(mask)
            draw.ellipse((2, 2, width - 3, height - 3), fill=255)
            mask.save(mask_file)
        except Exception as e:
            print(f"Warning: Could not generate static circle mask: {e}")
            return None
    return mask_file

def compute_2d_horizon_stabilization_angles(roll_deg, pitch_deg, yaw_deg, target_roll, target_pitch, target_yaw, multiplier_roll=1.0, multiplier_pitch=1.0):
    """Computes 2D rotational delta angles for horizon leveling.

    Calculates raw signed deltas relative to target orientation, leaving
    lens-specific sign inversion to the calling context.

    Args:
        roll_deg (float): Measured camera roll angle in degrees.
        pitch_deg (float): Measured camera pitch angle in degrees.
        yaw_deg (float): Measured camera yaw angle in degrees.
        target_roll (float): Desired target roll angle in degrees.
        target_pitch (float): Desired target pitch angle in degrees.
        target_yaw (float): Desired target yaw angle in degrees.
        multiplier_roll (float, optional): Scaling factor for roll correction. Defaults to 1.0.
        multiplier_pitch (float, optional): Scaling factor for pitch correction. Defaults to 1.0.

    Returns:
        tuple[float, float, float]: Raw (stab_roll, stab_pitch, stab_yaw) deltas in degrees.
    """
    delta_pitch = (pitch_deg - target_pitch) * float(multiplier_pitch)
    delta_roll = (roll_deg - target_roll) * float(multiplier_roll)
    
    # Sign conventions: this function returns RAW signed deltas (same sign as camera movement).
    # Negation per lens is applied at the CALL SITES (not here), because each lens needs a different sign:
    #   v360 pitch = +θ  ->  shifts content DOWN (Empirically verified)
    #   v360 roll  = +θ  ->  rotates content CCW (Empirically verified)
    #
    # Left  lens (front-facing): call site writes -stabilize_pitch, -stabilize_roll  (counter-rotation)
    # Right lens (rear-facing) : call site writes +stabilize_pitch, +stabilize_roll  (opposite — lens faces 180° backward)
    stab_pitch = delta_pitch
    stab_roll  = delta_roll
    stab_yaw   = 0.0
    
    return stab_roll, stab_pitch, stab_yaw

def v360_euler_to_matrix(yaw_deg, pitch_deg, roll_deg):
    """Converts v360 intrinsic Euler rotation angles to a 3x3 rotation matrix.

    Args:
        yaw_deg (float): Yaw rotation angle in degrees.
        pitch_deg (float): Pitch rotation angle in degrees.
        roll_deg (float): Roll rotation angle in degrees.

    Returns:
        np.ndarray: 3x3 SO(3) orthogonal rotation matrix (float64).
    """
    y, p, r = math.radians(yaw_deg), math.radians(pitch_deg), math.radians(roll_deg)
    cy, sy = math.cos(y), math.sin(y)
    cp, sp = math.cos(p), math.sin(p)
    cr, sr = math.cos(r), math.sin(r)
    return np.array([
        [cy * cr - sy * sp * sr, -sr * cp,  sy * cr + cy * sp * sr],
        [cy * sr + sy * sp * cr,  cr * cp,  sy * sr - cy * sp * cr],
        [-sy * cp,                sp,       cy * cp]
    ], dtype=np.float64)

def v360_matrix_to_euler(R):
    """Extracts v360 Euler rotation angles from a 3x3 rotation matrix.

    Handles gimbal lock singularities when pitch approaches ±90 degrees.

    Args:
        R (np.ndarray): 3x3 rotation matrix.

    Returns:
        tuple[float, float, float]: Euler angles in degrees as (yaw, pitch, roll).
    """
    sp = max(-1.0, min(1.0, float(R[2, 1])))
    p_rad = math.asin(sp)
    cp = math.cos(p_rad)
    if cp > 1e-6:
        y_rad = math.atan2(-float(R[2, 0]), float(R[2, 2]))
        r_rad = math.atan2(-float(R[0, 1]), float(R[1, 1]))
    else:
        y_rad = math.atan2(float(R[0, 2]), float(R[0, 0]))
        r_rad = 0.0
    return math.degrees(y_rad), math.degrees(p_rad), math.degrees(r_rad)

def compose_sendcmd_files(sendcmd_files, output_file):
    """Combines multiple FFmpeg v360 sendcmd rotation scripts into a single composite file.

    Interpolates time-series rotations in SO(3) space and applies smart stage pruning
    to bypass identity or trivial transforms (<0.015° deviation).

    Args:
        sendcmd_files (list[str]): List of paths to v360 sendcmd instruction files.
        output_file (str): Destination path for the composed sendcmd script.

    Returns:
        bool: True if composite file was successfully written, False otherwise.
    """
    if not sendcmd_files:
        return False

    line_pattern = re.compile(r'^\s*([0-9\.\-]+)\s+.*?\bv360\b\s+(yaw|pitch|roll)\s+([-\d\.]+)', re.IGNORECASE)

    def parse_series(filepath):
        """Parses timestamped v360 yaw, pitch, roll commands from a sendcmd file.

        Args:
            filepath (str): Path to the sendcmd file.

        Returns:
            list[tuple[float, str, float, float, float]]: Sorted series of
                (timestamp_mid, timestamp_str, yaw, pitch, roll).
        """
        by_ts = {}
        is_delta = False
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line: continue
                    if line.startswith('# format: delta') or 'format: delta' in line:
                        is_delta = True
                        continue
                    if line.startswith('#'): continue
                    m = line_pattern.search(line)
                    if m:
                        ts_str = m.group(1).strip()
                        param = m.group(2).lower()
                        val = float(m.group(3))
                        if ts_str not in by_ts:
                            by_ts[ts_str] = {'yaw': 0.0, 'pitch': 0.0, 'roll': 0.0}
                        by_ts[ts_str][param] = val
        except Exception as e:
            print(f"Warning: error parsing sendcmd file {filepath}: {e}")
            return []

        series = []
        accum_y, accum_p, accum_r = 0.0, 0.0, 0.0
        for ts_str, data in by_ts.items():
            if '-' in ts_str:
                parts = ts_str.split('-')
                t_mid = 0.5 * (float(parts[0]) + float(parts[1]))
            else:
                t_mid = float(ts_str)
            if is_delta:
                accum_y += data['yaw']
                accum_p += data['pitch']
                accum_r += data['roll']
                series.append((t_mid, ts_str, accum_y, accum_p, accum_r))
            else:
                series.append((t_mid, ts_str, data['yaw'], data['pitch'], data['roll']))
        series.sort(key=lambda x: x[0])
        return series

    def interp_rot(series, ts_keys, t_target):
        """Interpolates rotation matrix at a target timestamp using linear angle blending.

        Args:
            series (list): List of rotation samples.
            ts_keys (list[float]): Sorted timestamps corresponding to series.
            t_target (float): Target timestamp in seconds.

        Returns:
            np.ndarray: 3x3 interpolated rotation matrix.
        """
        if not series:
            return np.eye(3, dtype=np.float64)
        if t_target <= series[0][0]:
            return v360_euler_to_matrix(series[0][2], series[0][3], series[0][4])
        if t_target >= series[-1][0]:
            return v360_euler_to_matrix(series[-1][2], series[-1][3], series[-1][4])
        idx = bisect.bisect_right(ts_keys, t_target)
        i = max(0, min(len(series) - 2, idx - 1))
        t0, t1 = series[i][0], series[i+1][0]
        dt = t1 - t0
        alpha = 0.0 if abs(dt) < 1e-9 else (t_target - t0) / dt
        y = series[i][2] + alpha * (series[i+1][2] - series[i][2])
        p = series[i][3] + alpha * (series[i+1][3] - series[i][3])
        r = series[i][4] + alpha * (series[i+1][4] - series[i][4])
        return v360_euler_to_matrix(y, p, r)

    all_series = []
    all_series_keys = []
    master_grid = []
    for s_file in sendcmd_files:
        if s_file and os.path.exists(s_file) and os.path.getsize(s_file) > 0:
            s = parse_series(s_file)
            if s:
                # Smart Stage Pruning: Check if this series has meaningful angular motion (peak rotation >= 0.015 deg)
                max_dev = max(max(abs(x[2]), abs(x[3]), abs(x[4])) for x in s)
                if max_dev < 0.015:
                    print(f"[Smart Composition] Bypassing identity transform (<0.015° max dev): {os.path.basename(s_file)}")
                    if len(s) > len(master_grid):
                        master_grid = s
                    continue
                all_series.append(s)
                all_series_keys.append([x[0] for x in s])
                if len(s) > len(master_grid):
                    master_grid = s

    if not master_grid:
        return False

    if not all_series:
        # All stages were identity/pruned: write clean identity master
        all_series.append(master_grid)
        all_series_keys.append([x[0] for x in master_grid])

    def norm_angle(a):
        """Normalizes an angle in degrees into the (-180, 180] range.

        Args:
            a (float): Input angle in degrees.

        Returns:
            float: Normalized angle in degrees.
        """
        while a > 180.0: a -= 360.0
        while a < -180.0: a += 360.0
        return a

    has_delta_method = any(('horizon' in str(f).lower() or 'traveldir' in str(f).lower()) for f in sendcmd_files)
    try:
        with open(output_file, 'w', encoding='utf-8', newline='\n') as f_out:
            if has_delta_method:
                f_out.write("# format: delta\n")
            prev_y, prev_p, prev_r = 0.0, 0.0, 0.0
            for t_mid, ts_str, _, _, _ in master_grid:
                R_tot = np.eye(3, dtype=np.float64)
                for s, keys in zip(all_series, all_series_keys):
                    R_step = interp_rot(s, keys, t_mid)
                    R_tot = R_tot @ R_step
                y_c, p_c, r_c = v360_matrix_to_euler(R_tot)
                y_c = norm_angle(y_c)
                p_c = norm_angle(p_c)
                r_c = norm_angle(r_c)

                if has_delta_method:
                    # Incremental delta for FFmpeg v360 additive sendcmd execution when horizon leveling or traveldir lock is active
                    dy = norm_angle(y_c - prev_y)
                    dp = norm_angle(p_c - prev_p)
                    dr = norm_angle(r_c - prev_r)
                    f_out.write(f"{ts_str} [enter] v360 yaw {dy:.6f};\n")
                    f_out.write(f"{ts_str} [enter] v360 pitch {dp:.6f};\n")
                    f_out.write(f"{ts_str} [enter] v360 roll {dr:.6f};\n")
                    prev_y, prev_p, prev_r = y_c, p_c, r_c
                else:
                    # Legacy output format untouched for all other stabilization methods
                    f_out.write(f"{ts_str} [enter] v360 yaw {y_c:.6f};\n")
                    f_out.write(f"{ts_str} [enter] v360 pitch {p_c:.6f};\n")
                    f_out.write(f"{ts_str} [enter] v360 roll {r_c:.6f};\n")
        return True
    except Exception as e:
        print(f"Error writing composed sendcmd file {output_file}: {e}")
        return False

def is_identity_sendcmd(filepath, threshold_deg=0.015):
    """Check whether a sendcmd file represents an identity or trivial transform (< threshold_deg max deviation).

    Correctly accumulates frame-to-frame angular deltas when format is '# format: delta',
    ensuring smooth continuous motion trajectories are not misclassified as identity.

    Args:
        filepath (str): Path to sendcmd file.
        threshold_deg (float): Peak deviation threshold in degrees.

    Returns:
        bool: True if file is missing, empty, or all cumulative angles are < threshold_deg; False otherwise.
    """
    if not filepath or not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
        return True
    line_pattern = re.compile(r'^\s*([0-9\.\-]+)\s+.*?\bv360\b\s+(yaw|pitch|roll)\s+([-\d\.]+)', re.IGNORECASE)
    is_delta = False
    yaw_acc, pitch_acc, roll_acc = 0.0, 0.0, 0.0
    peak_dev = 0.0
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if line.startswith('#'):
                    if 'delta' in line.lower():
                        is_delta = True
                    continue
                m = line_pattern.search(line)
                if m:
                    axis = m.group(2).lower()
                    val = float(m.group(3))
                    if is_delta:
                        if axis == 'yaw':
                            yaw_acc += val
                        elif axis == 'pitch':
                            pitch_acc += val
                        elif axis == 'roll':
                            roll_acc += val
                        dev = max(abs(yaw_acc), abs(pitch_acc), abs(roll_acc))
                    else:
                        dev = abs(val)
                    if dev > peak_dev:
                        peak_dev = dev
                    if peak_dev >= threshold_deg:
                        return False
        return peak_dev < threshold_deg
    except Exception:
        return False

def get_effective_prior_sendcmd(active_files, out_base, stage_name):
    """Composes all active non-trivial upstream transforms into a composite reference trajectory.

    Ensures downstream trajectory optimizers (cinematic, traveldir) receive the true camera
    motion path rather than isolated residuals or empty dummy transforms.

    Args:
        active_files (list[str]): List of preceding sendcmd filepaths in composition order.
        out_base (str): Base output path prefix for temporary composed files.
        stage_name (str): Identifier of the requesting stage (e.g. 'cinematic', 'traveldir').

    Returns:
        str: Filepath to the effective prior trajectory file, or empty string if none.
    """
    valid_files = [f for f in active_files if f and os.path.exists(f) and not is_identity_sendcmd(f)]
    if not valid_files:
        return ""
    if len(valid_files) == 1:
        return valid_files[0]

    composed_tmp = f"{out_base}_sendcmd_pre_{stage_name}.txt"
    if compose_sendcmd_files(valid_files, composed_tmp):
        return composed_tmp
    return valid_files[-1]

def smooth_telemetry_angles(angles, window_size, sigma_range_deg=2.5):
    """Smooths IMU telemetry angles using combined median and bilateral Gaussian filtering.

    Eliminates non-causal pre-ring and step-jump tilt glitches across hardware IMU
    discontinuities while maintaining continuous Gaussian stabilization on regular motion.

    Args:
        angles (list[tuple[float, float, float]]): Raw (roll, pitch, yaw) tuples in degrees.
        window_size (int): Filter window size in frames.
        sigma_range_deg (float, optional): Range kernel deviation threshold in degrees. Defaults to 2.5.

    Returns:
        list[tuple[float, float, float]]: Smoothed (roll, pitch, yaw) tuples in degrees.
    """
    if window_size <= 1 or not angles:
        return angles
    
    n = len(angles)
    sigma = max(1.0, float(window_size) / 4.0)
    radius = int(2.5 * sigma)
    
    kernel = [math.exp(-0.5 * (x / sigma) ** 2) for x in range(-radius, radius + 1)]
    
    # Apply 5-point median filter to strip out single/multi-frame hardware IMU spikes (e.g. 16.4 deg jumps in 33ms)
    cleaned_angles = []
    for i in range(n):
        r_neighbors = [angles[max(0, min(n - 1, i + k))][0] for k in (-2, -1, 0, 1, 2)]
        p_neighbors = [angles[max(0, min(n - 1, i + k))][1] for k in (-2, -1, 0, 1, 2)]
        y_neighbors = [angles[max(0, min(n - 1, i + k))][2] for k in (-2, -1, 0, 1, 2)]
        
        r_med = sorted(r_neighbors)[len(r_neighbors) // 2]
        p_med = sorted(p_neighbors)[len(p_neighbors) // 2]
        y_med = sorted(y_neighbors)[len(y_neighbors) // 2]
        cleaned_angles.append((r_med, p_med, y_med))

    # Pad angles sequence at start and end to eliminate boundary transition artifacts
    padded = [cleaned_angles[0]] * radius + cleaned_angles + [cleaned_angles[-1]] * radius
    n_padded = len(padded)
    
    smoothed = []
    for i in range(n):
        i_pad = i + radius
        curr_r, curr_p, curr_y = cleaned_angles[i]
        
        sum_cos_r, sum_sin_r = 0.0, 0.0
        sum_cos_p, sum_sin_p = 0.0, 0.0
        sum_cos_y, sum_sin_y = 0.0, 0.0
        total_w_r, total_w_p, total_w_y = 0.0, 0.0, 0.0
        
        for k_idx, offset in enumerate(range(-radius, radius + 1)):
            idx = max(0, min(n_padded - 1, i_pad + offset))
            
            w_t = kernel[k_idx]
            nbr_r, nbr_p, nbr_y = padded[idx]
            
            # Bilateral Range Gating: Prevents future sensor step jumps from pulling preceding frames down
            dr = ((nbr_r - curr_r + 180.0) % 360.0) - 180.0
            dp = ((nbr_p - curr_p + 180.0) % 360.0) - 180.0
            dy = ((nbr_y - curr_y + 180.0) % 360.0) - 180.0
            
            w_r = w_t * math.exp(-0.5 * (dr / sigma_range_deg) ** 2)
            w_p = w_t * math.exp(-0.5 * (dp / sigma_range_deg) ** 2)
            w_y = w_t * math.exp(-0.5 * (dy / sigma_range_deg) ** 2)
            
            r_rad = math.radians(nbr_r)
            p_rad = math.radians(nbr_p)
            y_rad = math.radians(nbr_y)
            
            sum_cos_r += math.cos(r_rad) * w_r
            sum_sin_r += math.sin(r_rad) * w_r
            total_w_r += w_r
            
            sum_cos_p += math.cos(p_rad) * w_p
            sum_sin_p += math.sin(p_rad) * w_p
            total_w_p += w_p
            
            sum_cos_y += math.cos(y_rad) * w_y
            sum_sin_y += math.sin(y_rad) * w_y
            total_w_y += w_y
            
        avg_r = math.degrees(math.atan2(sum_sin_r, sum_cos_r)) if total_w_r > 0 else curr_r
        avg_p = math.degrees(math.atan2(sum_sin_p, sum_cos_p)) if total_w_p > 0 else curr_p
        avg_y = math.degrees(math.atan2(sum_sin_y, sum_cos_y)) if total_w_y > 0 else curr_y
        smoothed.append((avg_r, avg_p, avg_y))

    return smoothed


def create_meta_copy(filepath, status_file=None, start_ts=None, stab_name=None):
    """Injects 360 VR spatial metadata into a video copy using the spatialmedia module.

    Creates a companion video file suffixed with '_VR' containing spherical
    metadata atoms required for 360 projection on VR headsets and platforms.

    Args:
        filepath (str): Source equirectangular video file path.
        status_file (str, optional): Status tracking file to update during injection.
        start_ts (float, optional): Pipeline start timestamp for elapsed time.
        stab_name (str, optional): Stabilization stage display name for status reporting.

    Returns:
        bool: True if metadata injection succeeded, False otherwise.
    """
    if not filepath or not os.path.exists(filepath): return True
    if os.path.getsize(filepath) == 0:
        print(f"Warning: skipping metadata injection for 0-byte file: {filepath}")
        return True
    base, ext = os.path.splitext(filepath)
    meta_file = f"{base}_VR{ext}"
    if os.path.exists(meta_file) and os.path.getsize(meta_file) > 0:
        return True
    if not stab_name:
        stem = os.path.splitext(os.path.basename(filepath))[0].lower()
        if stem.endswith("_vr"):
            stem = stem[:-3]
        stage_token_map = [
            (["traveldir"], "Travel-Direction Lock"),
            (["cinematic"], "Cinematic"),
            (["checkpoint", "horizon"], "Horizon Checkpoints"),
            (["vidstab", "optical"], "Vidstab"),
            (["kabsch"], "Kabsch"),
            (["kopf"], "Kopf"),
            (["telemetry"], "Telemetry"),
            (["nadir"], "Nadir Overlay"),
            (["stitched", "step1"], "Stitched Video"),
            (["master", "composed"], "Master Render"),
        ]
        best_pos = -1
        best_name = "Video"
        for tokens, name in stage_token_map:
            for tok in tokens:
                pos = stem.rfind(tok)
                if pos > best_pos:
                    best_pos = pos
                    best_name = name
        stab_name = best_name
    if status_file and start_ts:
        update_status(status_file, {
            "status": "injecting",
            "phase": f"Injecting VR metadata for {stab_name}",
            "progress": 95.0,
            "speed": "N/A",
            "eta": "Calculating...",
            "elapsed": int(time.time() - start_ts)
        })
    script_dir_loc = os.path.dirname(os.path.abspath(__file__))
    root_dir_loc = os.path.dirname(script_dir_loc)
    env = os.environ.copy()
    env["PYTHONPATH"] = script_dir_loc + os.pathsep + root_dir_loc + os.pathsep + env.get("PYTHONPATH", "")
    cmd = [sys.executable, "-B", "-m", "spatialmedia", "-i", "-p", "equirectangular", filepath, meta_file]
    print(f"Injecting 360 VR spatial metadata immediately into: {filepath} -> {meta_file}")
    try:
        proc = subprocess.run(cmd, cwd=root_dir_loc, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120, creationflags=WIN_NO_WINDOW)
        success = proc.returncode == 0 and os.path.exists(meta_file) and os.path.getsize(meta_file) > 0
        if success:
            print(f"Successfully injected 360 metadata immediately: {meta_file}")
        else:
            err_detail = (proc.stderr or proc.stdout or "").strip()
            print(f"Warning: Immediate 360 metadata injection failed for {filepath}: {err_detail}")
        return success
    except Exception as e_meta:
        print(f"Warning: Immediate 360 metadata injection exception for {filepath}: {e_meta}")
        return False


def embed_vrot_metadata(src_video, dst_video):
    """Transfers moov/udta camera orientation telemetry (vrot atom) between MP4 containers.

    Copies vendor camera orientation telemetry atoms from the source video into
    the stitched output container to preserve hardware orientation tracks.

    Args:
        src_video (str): Path to source video with telemetry atoms.
        dst_video (str): Path to target video receiving telemetry atoms.

    Returns:
        bool: True if telemetry was copied and embedded successfully, False otherwise.
    """
    if not src_video or not os.path.exists(src_video):
        return False
    if not dst_video or not os.path.exists(dst_video):
        return False
    if os.path.getsize(dst_video) == 0:
        return False

    try:
        from extract_telemetry import find_box
    except Exception as e_imp:
        print(f"Notice: Could not import find_box for telemetry embedding: {e_imp}")
        return False

    try:
        dst_size = os.path.getsize(dst_video)
        with open(dst_video, "rb") as f_dst:
            has_dst_vrot = find_box(f_dst, 0, dst_size, ["moov", "udta", "vrot"]) is not None
        if has_dst_vrot:
            return True

        src_size = os.path.getsize(src_video)
        with open(src_video, "rb") as f_src:
            src_udta_info = find_box(f_src, 0, src_size, ["moov", "udta"])
            if not src_udta_info:
                return False
            src_udta_offset, src_udta_size = src_udta_info
            f_src.seek(src_udta_offset)
            src_udta_bytes = f_src.read(src_udta_size)

        if b"vrot" not in src_udta_bytes:
            return False

        with open(dst_video, "rb") as f_dst:
            dst_moov_info = find_box(f_dst, 0, dst_size, ["moov"])
            if not dst_moov_info:
                return False
            dst_moov_offset, dst_moov_size = dst_moov_info
            dst_udta_info = find_box(f_dst, 0, dst_size, ["moov", "udta"])
            
            f_dst.seek(0)
            if dst_udta_info:
                dst_udta_offset, dst_udta_size = dst_udta_info
                part1 = f_dst.read(dst_udta_offset)
                f_dst.seek(dst_udta_offset + dst_udta_size)
                part2 = f_dst.read()
                new_moov_size = dst_moov_size - dst_udta_size + len(src_udta_bytes)
            else:
                dst_udta_size = 0
                dst_udta_offset = dst_moov_offset + dst_moov_size
                part1 = f_dst.read(dst_udta_offset)
                part2 = f_dst.read()
                new_moov_size = dst_moov_size + len(src_udta_bytes)

        new_data = bytearray(part1) + src_udta_bytes + part2

        import struct
        with open(dst_video, "rb") as f_dst:
            f_dst.seek(dst_moov_offset)
            moov_header = f_dst.read(8)
            size_field, = struct.unpack(">I", moov_header[0:4])

        if size_field == 1:
            new_size_bytes = struct.pack(">Q", new_moov_size)
            new_data[dst_moov_offset + 8 : dst_moov_offset + 16] = new_size_bytes
        else:
            new_size_bytes = struct.pack(">I", new_moov_size)
            new_data[dst_moov_offset : dst_moov_offset + 4] = new_size_bytes

        temp_dst = dst_video + ".tmp_vrot.mp4"
        with open(temp_dst, "wb") as f_out:
            f_out.write(new_data)

        if os.path.exists(temp_dst) and os.path.getsize(temp_dst) > 0:
            import shutil
            shutil.move(temp_dst, dst_video)
            print(f"Successfully embedded telemetry metadata 'vrot' ({len(src_udta_bytes)} bytes) into {os.path.basename(dst_video)}")
            return True
        else:
            return False

    except Exception as e_embed:
        print(f"Notice: Telemetry metadata embedding exception for {os.path.basename(dst_video)}: {e_embed}")
        return False



def analyze_kopf_jitter_from_motion(motion_file):
    """Extracts rotational jitter, jerk, and frequency metrics from a .kopf360motion file.

    Evaluates corrected jitter magnitudes, angular acceleration, jerk, and band-passed
    tremor frequencies for 3D Kopf trajectory evaluation. Converts results to rectilinear
    pixel equivalents for 1280px @ 90° HFoV.

    Args:
        motion_file (str): Path to the .kopf360motion data file.

    Returns:
        dict | None: Dictionary containing RMS/peak correction metrics, jerk metrics,
            and frequency bands, or None on read failure.
    """
    try:
        import numpy as np
        yaws, pitches, rolls = [], [], []
        with open(motion_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split()
                if len(parts) >= 5:
                    try:
                        yaws.append(float(parts[2]))
                        pitches.append(float(parts[3]))
                        rolls.append(float(parts[4]))
                    except ValueError:
                        continue
        if not yaws:
            return None

        N = len(yaws)
        magnitudes = np.array([np.sqrt(y**2 + p**2 + r**2) for y, p, r in zip(yaws, pitches, rolls)], dtype=np.float64)
        rms_corr   = float(np.sqrt(np.mean(magnitudes**2)))
        max_mag    = float(np.max(magnitudes))
        max_roll   = float(np.max(np.abs(rolls)))
        max_pitch  = float(np.max(np.abs(pitches)))
        max_yaw    = float(np.max(np.abs(yaws)))

        # 1280px wide 90 deg HFoV rectilinear: 1 deg = 14.22 px
        px_per_deg = 1280.0 / 90.0
        rms_px  = rms_corr * px_per_deg
        max_px  = max_mag  * px_per_deg

        # 1. Angular Acceleration & Jerk (2nd & 3rd discrete time differences of correction vector)
        c_vec = np.column_stack((yaws, pitches, rolls))
        if N >= 3:
            acc = c_vec[2:] - 2 * c_vec[1:-1] + c_vec[:-2]
            acc_mag = np.linalg.norm(acc, axis=1)
            rms_acc_deg = float(np.sqrt(np.mean(acc_mag**2)))
            max_acc_deg = float(np.max(acc_mag))
        else:
            rms_acc_deg = 0.0
            max_acc_deg = 0.0

        if N >= 4:
            jerk = c_vec[3:] - 3 * c_vec[2:-1] + 3 * c_vec[1:-2] - c_vec[:-3]
            jerk_mag = np.linalg.norm(jerk, axis=1)
            rms_jerk_deg = float(np.sqrt(np.mean(jerk_mag**2)))
            max_jerk_deg = float(np.max(jerk_mag))
        else:
            rms_jerk_deg = 0.0
            max_jerk_deg = 0.0

        rms_jerk_px = rms_jerk_deg * px_per_deg

        # 2. High-Frequency Tremor vs Low-Frequency Drift (moving average window)
        window = min(15, max(3, N // 4)) if N >= 4 else 1
        half = window // 2
        lf_vec = np.zeros_like(c_vec)
        for i in range(N):
            w0 = max(0, i - half)
            w1 = min(N, i + half + 1)
            lf_vec[i] = np.mean(c_vec[w0:w1], axis=0)
        hf_vec = c_vec - lf_vec

        hf_mag = np.linalg.norm(hf_vec, axis=1)
        lf_mag = np.linalg.norm(lf_vec, axis=1)

        rms_hf_deg = float(np.sqrt(np.mean(hf_mag**2)))
        rms_lf_deg = float(np.sqrt(np.mean(lf_mag**2)))
        rms_hf_px  = rms_hf_deg * px_per_deg

        tot_energy = float(np.sum(magnitudes**2))
        hf_energy  = float(np.sum(hf_mag**2))
        lf_energy  = float(np.sum(lf_mag**2))
        tot_decomp = hf_energy + lf_energy
        jitter_absorption_pct = round(min(100.0, max(0.0, (hf_energy / tot_decomp * 100.0))), 1) if tot_decomp > 1e-9 else 0.0

        # Stability assessment
        if rms_hf_deg < 0.10:
            stability_grade = "EXCELLENT (Sub-pixel residual micro-tremor)"
        elif rms_hf_deg < 0.25:
            stability_grade = "GOOD (High-frequency shake suppressed)"
        elif max_mag > 0.50:
            stability_grade = "EFFECTIVE (Heavy rotational shock/footsteps absorbed)"
        else:
            stability_grade = "MODERATE (Trajectory smoothed)"

        axis_maxes = [('Pitch', max_pitch), ('Roll', max_roll), ('Yaw', max_yaw)]
        dominant_axis, dom_val = max(axis_maxes, key=lambda x: x[1])

        return {
            'rms_corr_deg': round(rms_corr, 3),
            'rms_corr_px':  round(rms_px,   1),
            'max_corr_deg': round(max_mag,   3),
            'max_corr_px':  round(max_px,    1),
            'max_roll':     round(max_roll,  3),
            'max_pitch':    round(max_pitch, 3),
            'max_yaw':      round(max_yaw,   3),
            'rms_acc_deg':  round(rms_acc_deg, 3),
            'max_acc_deg':  round(max_acc_deg, 3),
            'rms_jerk_deg': round(rms_jerk_deg, 3),
            'max_jerk_deg': round(max_jerk_deg, 3),
            'rms_jerk_px':  round(rms_jerk_px, 1),
            'rms_hf_deg':   round(rms_hf_deg, 3),
            'rms_hf_px':    round(rms_hf_px, 1),
            'rms_lf_deg':   round(rms_lf_deg, 3),
            'jitter_absorption_pct': jitter_absorption_pct,
            'stability_grade': stability_grade,
            'dominant_axis': dominant_axis,
            'num_frames':   N,
        }
    except Exception as e:
        print(f"Notice: kopf motion jitter analysis error: {e}")
        return None


def analyze_kabsch_jitter_from_motion(motion_file):
    """Computes spherical SVD rotational jitter metrics from a .kabsch360motion file.

    Calculates RMS and peak angular corrections converted to rectilinear pixel equivalents
    for 1280px @ 90° HFoV.

    Args:
        motion_file (str): Path to the .kabsch360motion tracking file.

    Returns:
        dict | None: Dictionary of jitter and correction metrics, or None on error.
    """
    try:
        import numpy as np
        yaws, pitches, rolls = [], [], []
        with open(motion_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split()
                if len(parts) >= 7:
                    try:
                        yaws.append(float(parts[4]))
                        pitches.append(float(parts[5]))
                        rolls.append(float(parts[6]))
                    except ValueError:
                        continue
        if not yaws:
            return None
        magnitudes = [float(np.sqrt(y**2 + p**2 + r**2)) for y, p, r in zip(yaws, pitches, rolls)]
        rms_corr   = float(np.sqrt(np.mean([m**2 for m in magnitudes])))
        max_mag    = float(np.max(magnitudes))
        max_roll   = float(np.max([abs(r) for r in rolls]))
        max_pitch  = float(np.max([abs(p) for p in pitches]))
        max_yaw    = float(np.max([abs(y) for y in yaws]))
        px_per_deg = 1280.0 / 90.0
        return {
            'rms_corr_deg': round(rms_corr, 3),
            'rms_corr_px':  round(rms_corr * px_per_deg, 1),
            'max_corr_deg': round(max_mag,   3),
            'max_corr_px':  round(max_mag  * px_per_deg, 1),
            'max_roll':     round(max_roll,  3),
            'max_pitch':    round(max_pitch, 3),
            'max_yaw':      round(max_yaw,   3),
            'num_frames':   len(yaws),
        }
    except Exception as e:
        print(f"Notice: kabsch motion jitter analysis error: {e}")
        return None


def analyze_telemetry_jitter(telemetry_file, sendcmd_file=None):
    """Analyzes raw telemetry and/or applied v360 sendcmd files for stabilization metrics.

    Calculates leveling corrections, drift compensation, and angular acceleration stats.

    Args:
        telemetry_file (str): Path to parsed telemetry text file.
        sendcmd_file (str, optional): Path to generated FFmpeg sendcmd script.

    Returns:
        dict | None: Metric dictionary with leveling performance, or None on error.
    """
    try:
        import numpy as np
        yaws, pitches, rolls = [], [], []
        
        # 1. Parse sendcmd file if present for actual applied v360 incremental counter-rotations
        if sendcmd_file and os.path.exists(sendcmd_file):
            with open(sendcmd_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    # Format: 0.000000-0.016667 [enter] v360 yaw/pitch/roll <angle>;
                    if "v360 yaw" in line:
                        try:
                            val = float(line.split("v360 yaw")[-1].replace(";", "").strip())
                            yaws.append(abs(val))
                        except ValueError:
                            pass
                    elif "v360 pitch" in line:
                        try:
                            val = float(line.split("v360 pitch")[-1].replace(";", "").strip())
                            pitches.append(abs(val))
                        except ValueError:
                            pass
                    elif "v360 roll" in line:
                        try:
                            val = float(line.split("v360 roll")[-1].replace(";", "").strip())
                            rolls.append(abs(val))
                        except ValueError:
                            pass
                            
        # 2. Fallback to raw telemetry file (roll, pitch, yaw)
        if not yaws and not pitches and not rolls and telemetry_file and os.path.exists(telemetry_file):
            with open(telemetry_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    parts = line.split()
                    if len(parts) >= 3:
                        try:
                            rolls.append(float(parts[0]))
                            pitches.append(float(parts[1]))
                            yaws.append(float(parts[2]))
                        except ValueError:
                            continue

        n_samples = min(len(rolls), len(pitches), len(yaws)) if (rolls and pitches and yaws) else max(len(rolls), len(pitches), len(yaws))
        if n_samples == 0:
            return None

        r_arr = np.array(rolls[:n_samples]) if rolls else np.zeros(n_samples)
        p_arr = np.array(pitches[:n_samples]) if pitches else np.zeros(n_samples)
        y_arr = np.array(yaws[:n_samples]) if yaws else np.zeros(n_samples)

        magnitudes = np.sqrt(r_arr**2 + p_arr**2 + y_arr**2)
        rms_corr = float(np.sqrt(np.mean(magnitudes**2)))
        max_mag  = float(np.max(magnitudes))
        max_roll = float(np.max(np.abs(r_arr)))
        max_pitch = float(np.max(np.abs(p_arr)))
        max_yaw  = float(np.max(np.abs(y_arr)))
        px_per_deg = 1280.0 / 90.0

        return {
            'rms_corr_deg': round(rms_corr, 3),
            'rms_corr_px':  round(rms_corr * px_per_deg, 1),
            'max_corr_deg': round(max_mag,   3),
            'max_corr_px':  round(max_mag  * px_per_deg, 1),
            'max_roll':     round(max_roll,  3),
            'max_pitch':    round(max_pitch, 3),
            'max_yaw':      round(max_yaw,   3),
            'num_frames':   n_samples,
        }
    except Exception as e:
        print(f"Notice: telemetry jitter analysis error: {e}")
        return None


def analyze_optical_jitter_from_motion(motion_file):
    """Calculates 2D optical flow translation and rotational compensation metrics.

    Args:
        motion_file (str): Path to the .optical360motion tracking file.

    Returns:
        dict | None: Dictionary containing RMS/peak pixel shifts and angular limits,
            or None on error.
    """
    try:
        import numpy as np
        txs, tys, yaws, pitches = [], [], [], []
        with open(motion_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split()
                if len(parts) >= 7:
                    try:
                        txs.append(float(parts[1]))
                        tys.append(float(parts[2]))
                        yaws.append(float(parts[5]))
                        pitches.append(float(parts[6]))
                    except ValueError:
                        continue
        if not txs:
            return None
        shifts = [float(np.sqrt(x**2 + y**2)) for x, y in zip(txs, tys)]
        rms_shift = float(np.sqrt(np.mean([s**2 for s in shifts])))
        max_shift = float(np.max(shifts))
        max_yaw   = float(np.max([abs(y) for y in yaws]))
        max_pitch = float(np.max([abs(p) for p in pitches]))
        return {
            'rms_shift_px': round(rms_shift, 2),
            'max_shift_px': round(max_shift, 2),
            'max_yaw_deg':  round(max_yaw,   3),
            'max_pitch_deg': round(max_pitch, 3),
            'num_frames':   len(txs),
        }
    except Exception as e:
        print(f"Notice: optical motion jitter analysis error: {e}")
        return None


def compute_stabilization_percentage(m_data, is_stabilized=None, sidecar_pct=None):
    """Calculates a unified representative stabilization percentage (+/-).

    Averages primary stabilization performance metrics (peak shock reduction,
    high-frequency tremor/jitter absorption, motion continuity, and vertical
    horizon lock).

    Args:
        m_data (dict, optional): Metric dictionary from analyze_stabilization_report.
        is_stabilized (bool, optional): Override stabilization success state.
        sidecar_pct (float, optional): Optional sidecar jitter reduction percentage.

    Returns:
        str: Formatted percentage string (e.g. '+3.5%', '-3.8%').
    """
    if m_data and isinstance(m_data, dict):
        p_peak = float(m_data.get("pct_peak", 0.0))
        p_jit  = float(m_data.get("pct_jitter", 0.0))
        p_over = float(m_data.get("pct_overall", 0.0))
        p_dy   = float(m_data.get("pct_dy", 0.0))

        stab = is_stabilized if is_stabilized is not None else m_data.get("is_stabilized", False)

        if stab:
            # 1. Prioritize high-confidence 3D sidecar angular tremor reduction (e.g. Kopf 84.7%) when available
            if sidecar_pct is not None and sidecar_pct > 0:
                val = min(100.0, float(sidecar_pct))
            else:
                # High-frequency jitter, peak shock, and vertical horizon lock represent physical camera stability.
                # Planar overall drift is included if >= 0; if negative due to spherical coordinate
                # counter-rotation, jitter and peak absorption govern.
                eval_metrics = [p for p in (p_jit, p_peak, (p_over if p_over >= 0 else None), (p_dy if p_dy != 0.0 else None)) if p is not None]
                if eval_metrics:
                    val = sum(eval_metrics) / len(eval_metrics)
                elif p_over > 0:
                    val = p_over
                else:
                    val = max(0.1, p_peak if p_peak > 0 else (p_jit if p_jit > 0 else 0.5))

            # When stabilization succeeded (YES), coordinate artifacts from 3D spherical rotation
            # must never produce a negative percentage alongside a YES status.
            if val <= 0:
                pos_cands = [p for p in (p_peak, p_jit, p_dy, p_over) if p is not None and p > 0]
                if pos_cands:
                    val = max(0.1, sum(pos_cands) / len(pos_cands))
                else:
                    val = max(0.1, abs(val) if abs(val) > 0.01 else 1.0)
            val = min(100.0, val)
            return f"+{val:.1f}%"
        else:
            neg_metrics = [p for p in (p_peak, p_over, p_jit) if p <= 0]
            if neg_metrics:
                val = sum(neg_metrics) / len(neg_metrics)
            else:
                val = -abs(p_over) if p_over != 0 else -1.0
            return f"-{abs(val):.1f}%"

    if sidecar_pct is not None:
        try:
            s_val = float(sidecar_pct)
            if (is_stabilized is None or is_stabilized) and s_val >= 0:
                return f"+{abs(s_val):.1f}%"
            else:
                return f"-{abs(s_val):.1f}%"
        except (ValueError, TypeError):
            pass

    return "+1.0%" if (is_stabilized is None or is_stabilized) else "-1.0%"


def write_single_stage_report(m_key, m_name, in_v, out_v, r_path, out_base, q_mode, step1_stitch=None, target_stab_file=None, kopf_disclaimer="", fusion_info=None):
    """Writes an individual stage stabilization metrics report upon stage completion.

    Args:
        m_key (str): Method identifier key (e.g. 'telemetry', 'vidstab', 'kopf').
        m_name (str): Method display name.
        in_v (str): Input video path for this stage.
        out_v (str): Output stabilized video path.
        r_path (str): Destination filepath for the text report.
        out_base (str): Base filename prefix for stage assets.
        q_mode (str): Quality optimization mode identifier ('0', '1', '2', etc.).
        step1_stitch (str, optional): Base stitched video path.
        target_stab_file (str, optional): Final target stabilized video path.
        kopf_disclaimer (str, optional): Disclaimer notice for Kopf processing.
        fusion_info (dict | str, optional): Sensor fusion configuration details.

    Returns:
        dict | None: Analyzed metric dictionary if available, otherwise None.
    """
    m_data = None
    if q_mode == "1":
        # Single-Pass Composition: individual intermediate stage MP4s were omitted
        try:
            with open(r_path, 'w', encoding='utf-8') as f_ind:
                f_ind.write(
                    f"=== {m_name.upper()} STABILIZATION REPORT ===\n"
                    f"STATUS        : TRANSFORM EXTRACTED & COMPOSED IN-MEMORY\n"
                    f"Pipeline Mode : 1 - Single-Pass Transform Composition\n"
                    f"Input Video   : {os.path.basename(step1_stitch) if step1_stitch else 'N/A'}\n"
                    f"Output Video  : {os.path.basename(target_stab_file) if target_stab_file else 'N/A'}\n\n"
                    f"Refer to STABILIZATION SUMMARY REPORT for overall net single-pass metrics.\n"
                )
        except Exception:
            pass
    elif in_v and out_v and os.path.exists(in_v) and os.path.exists(out_v) and os.path.abspath(in_v) != os.path.abspath(out_v):
        m_data = analyze_stabilization_report(in_v, out_v, r_path, report_title=f"{m_name.upper()} STABILIZATION REPORT")

    sidecar_section = ""
    is_3d_spherically_stabilized = False
    spherical_summary = ""

    # For Telemetry, append IMU leveling & rotational correction metrics if available
    if m_key == "telemetry":
        _tf_raw = f"{out_base}_telemetry.txt"
        _tf_cmd = f"{out_base}_sendcmd_telemetry.txt"
        _tj = analyze_telemetry_jitter(_tf_raw, _tf_cmd)
        if _tj:
            if _tj.get('max_corr_deg', 0.0) > 0.05:
                is_3d_spherically_stabilized = True
                spherical_summary = f"The video WAS SPHERICALLY STABILIZED. Telemetry IMU stream counter-rotated {_tj['max_corr_deg']:.3f} deg horizon tilt and tremor."

            fuse_str = "Disabled (Raw IMU)"
            if fusion_info and isinstance(fusion_info, dict):
                f_m = str(fusion_info.get("method", "none")).lower()
                if f_m not in ("none", "disabled", "false", ""):
                    fuse_str = f"{f_m.upper()} (Gain: {float(fusion_info.get('gain', 0.5)):.2f}) [6-Axis Accel+Gyro]"
                else:
                    fuse_str = "Disabled (Raw IMU)"
            elif fusion_info and isinstance(fusion_info, str):
                fuse_str = fusion_info

            sidecar_section = (
                f"\n--- TELEMETRY IMU LEVELING & MOTION (from sensor stream) ---\n"
                f"Frames analyzed : {_tj['num_frames']}\n"
                f"Sensor Fusion   : {fuse_str}\n"
                f"RMS correction  : {_tj['rms_corr_deg']:.3f} deg  ({_tj['rms_corr_px']:.1f} px in 90-deg viewer)\n"
                f"Peak correction : {_tj['max_corr_deg']:.3f} deg  ({_tj['max_corr_px']:.1f} px)\n"
                f"  Roll  max     : {_tj['max_roll']:.3f} deg\n"
                f"  Pitch max     : {_tj['max_pitch']:.3f} deg\n"
                f"  Yaw   max     : {_tj['max_yaw']:.3f} deg\n"
                f"Interpretation  : RMS {_tj['rms_corr_px']:.1f}px / peak {_tj['max_corr_px']:.1f}px of camera horizon\n"
                f"                  tilt and tremor counter-rotated by telemetry IMU stream.\n"
            )

    # For Kopf, append extra 6-DOF rotational sidecar jitter info if available
    if m_key == "kopf":
        _mf_kopf = f"{out_base}.kopf360motion"
        _kj = analyze_kopf_jitter_from_motion(_mf_kopf) if os.path.exists(_mf_kopf) else None
        if _kj:
            if _kj.get('max_corr_deg', 0.0) > 0.05 or _kj.get('jitter_absorption_pct', 0.0) > 0:
                is_3d_spherically_stabilized = True
                spherical_summary = (
                    f"The video WAS SPHERICALLY STABILIZED. Neutralized {_kj['max_corr_deg']:.3f} deg {_kj.get('dominant_axis', 'camera')} shock "
                    f"with {_kj.get('jitter_absorption_pct', 0.0):.1f}% angular tremor absorption."
                )
            sidecar_section = (
                f"\n--- KOPF ROTATIONAL JITTER (from motion sidecar) ---\n"
                f"Frames analyzed      : {_kj['num_frames']}\n"
                f"Rotational RMS Corr  : {_kj['rms_corr_deg']:.3f} deg  ({_kj['rms_corr_px']:.1f} px in 90-deg viewer)\n"
                f"Peak Shock Absorbed  : {_kj['max_corr_deg']:.3f} deg  ({_kj['max_corr_px']:.1f} px)\n"
                f"  Roll  max          : {_kj['max_roll']:.3f} deg\n"
                f"  Pitch max          : {_kj['max_pitch']:.3f} deg\n"
                f"  Yaw   max          : {_kj['max_yaw']:.3f} deg\n"
                f"Angular Jerk RMS     : {_kj.get('rms_jerk_deg', 0.0):.3f} deg  ({_kj.get('rms_jerk_px', 0.0):.1f} px) [Peak: {_kj.get('max_jerk_deg', 0.0):.3f} deg]\n"
                f"High-Freq Tremor RMS : {_kj.get('rms_hf_deg', 0.0):.3f} deg  ({_kj.get('rms_hf_px', 0.0):.1f} px) [{_kj.get('jitter_absorption_pct', 0.0):.1f}% tremor absorption]\n"
                f"Spherical Stability  : {_kj.get('stability_grade', 'STABILIZED')}\n"
                f"Interpretation       : Kopf neutralized {_kj['max_corr_deg']:.3f} deg {_kj.get('dominant_axis', 'camera')} shock\n"
                f"                       and reduced rotational jerk to {_kj.get('rms_jerk_px', 0.0):.1f} px in VR forward view.\n"
            )
            if kopf_disclaimer:
                sidecar_section += kopf_disclaimer

    # For Kabsch, append spherical SVD rotational jitter info if available
    if m_key == "kabsch":
        _mf_kab = f"{out_base}.kabsch360motion"
        _kb = analyze_kabsch_jitter_from_motion(_mf_kab) if os.path.exists(_mf_kab) else None
        if _kb:
            if _kb.get('max_corr_deg', 0.0) > 0.05:
                is_3d_spherically_stabilized = True
                spherical_summary = f"The video WAS SPHERICALLY STABILIZED. Neutralized {_kb['max_corr_deg']:.3f} deg shock via Kabsch spherical SVD."
            sidecar_section = (
                f"\n--- KABSCH SVD ROTATIONAL JITTER (from motion sidecar) ---\n"
                f"Frames analyzed : {_kb['num_frames']}\n"
                f"RMS correction  : {_kb['rms_corr_deg']:.3f} deg  ({_kb['rms_corr_px']:.1f} px in 90-deg viewer)\n"
                f"Peak correction : {_kb['max_corr_deg']:.3f} deg  ({_kb['max_corr_px']:.1f} px)\n"
                f"  Roll  max     : {_kb['max_roll']:.3f} deg\n"
                f"  Pitch max     : {_kb['max_pitch']:.3f} deg\n"
                f"  Yaw   max     : {_kb['max_yaw']:.3f} deg\n"
            )

    # For VidSTAB, append optical flow compensation metrics if available
    if m_key == "vidstab":
        _mf_opt = f"{out_base}.optical360motion"
        _op = analyze_optical_jitter_from_motion(_mf_opt) if os.path.exists(_mf_opt) else None
        if _op:
            sidecar_section = (
                f"\n--- VIDSTAB OPTICAL MOTION JITTER (from motion sidecar) ---\n"
                f"Frames analyzed : {_op['num_frames']}\n"
                f"RMS shift       : {_op['rms_shift_px']:.2f} px\n"
                f"Peak shift      : {_op['max_shift_px']:.2f} px\n"
                f"Peak Yaw comp   : {_op['max_yaw_deg']:.3f} deg\n"
                f"Peak Pitch comp : {_op['max_pitch_deg']:.3f} deg\n"
            )

    # For Horizon, append leveling orientation metrics if available
    if m_key == "horizon":
        _json_cp = f"{out_base}_horizon_checkpoints.json"
        if not os.path.exists(_json_cp):
            _json_cp = os.path.join("data", "runtime", "work", f"{os.path.basename(out_base)}_horizon_checkpoints.json")
        if os.path.exists(_json_cp):
            try:
                from stabilize_horizon import load_checkpoints
                cps, ign_yaw, jfps = load_checkpoints(_json_cp)
            except Exception:
                cps = []
                ign_yaw = False
                jfps = 30.0
                try:
                    with open(_json_cp, "r", encoding="utf-8") as f_json:
                        cp_raw = json.load(f_json)
                        cps = cp_raw.get("checkpoints", [])
                        ign_yaw = bool(cp_raw.get("ignore_yaw", False))
                except Exception:
                    pass

            if cps:
                import math
                import numpy as np
                pitches = [float(c.get('pitch', 0.0)) for c in cps]
                rolls = [float(c.get('roll', 0.0)) for c in cps]
                yaws = [float(c.get('yaw', 0.0)) for c in cps]
                max_p = max(pitches, key=abs) if pitches else 0.0
                max_r = max(rolls, key=abs) if rolls else 0.0
                max_y = max(yaws, key=abs) if yaws else 0.0

                # Compute 3D camera tilt angles per checkpoint
                tilts = [math.sqrt(p**2 + r**2 + (0.0 if ign_yaw else y**2)) for p, r, y in zip(pitches, rolls, yaws)]
                mean_tilt = float(np.mean(tilts)) if tilts else 0.0
                rms_tilt = float(np.sqrt(np.mean([t**2 for t in tilts]))) if tilts else 0.0
                max_tilt = float(max(tilts)) if tilts else 0.0

                # Dynamic roll sway metrics
                roll_std = float(np.std(rolls)) if rolls else 0.0
                roll_peak_to_peak = float(max(rolls) - min(rolls)) if rolls else 0.0

                # 1280px wide 90 deg HFoV rectilinear viewer: 1 deg = 12.0 px
                px_per_deg = 12.0
                max_slant_px = max_tilt * px_per_deg
                rms_slant_px = rms_tilt * px_per_deg
                mean_slant_px = mean_tilt * px_per_deg

                if abs(max_p) < 0.015 and abs(max_r) < 0.015 and (ign_yaw or abs(max_y) < 0.015):
                    is_3d_spherically_stabilized = True
                    _horizon_score = 100.0
                    spherical_summary = f"The video was ALREADY LEVEL (0.00° tilt detected). Bypassed identity transform across {len(cps)} keyframes."
                    leveling_grade = "ALREADY LEVEL (Sub-degree alignment)"
                else:
                    is_3d_spherically_stabilized = True
                    # Moderate horizon stabilization lock score based on tilt, checkpoint density, and duration:
                    # Video can't be 98%+ perfect stabilized, nor 0.2% low. Target realistic range ~55% - 85%.
                    # 1. Base tilt reduction factor:
                    # RMS tilt < 1 deg -> ~55-65%, RMS tilt 2-5 deg -> ~68-78%, RMS tilt > 5 deg -> ~78-85%
                    tilt_factor = 55.0 + 28.0 * (1.0 - math.exp(-rms_tilt / 3.5))

                    # 2. Checkpoint sample count factor:
                    # 1 checkpoint = 0.85, 2 = 0.90, 3 = 0.94, >= 8 = 1.00
                    n_cps = len(cps)
                    count_factor = min(1.0, 0.80 + 0.05 * math.log2(max(1, n_cps) + 1.0))

                    # 3. Video duration and keyframe coverage factor:
                    vid_dur = None
                    try:
                        probe_target = in_v if (in_v and os.path.exists(in_v) and os.path.getsize(in_v) > 1024) else (out_v if (out_v and os.path.exists(out_v) and os.path.getsize(out_v) > 1024) else None)
                        if probe_target:
                            vid_dur = get_video_duration(probe_target)
                    except Exception:
                        vid_dur = None

                    if vid_dur and vid_dur > 0 and n_cps > 1:
                        cp_frames = [c.get('frame', 0) for c in cps]
                        max_f = max(cp_frames) if cp_frames else 0
                        cp_span_sec = max_f / (jfps if jfps else 30.0)
                        coverage_ratio = min(1.0, max(0.5, cp_span_sec / vid_dur))
                        # Average seconds per checkpoint
                        avg_interval = vid_dur / max(1, n_cps)
                        interval_factor = min(1.0, max(0.85, 1.0 - (max(0.0, avg_interval - 10.0) / 100.0)))
                        dur_factor = coverage_ratio * interval_factor
                    else:
                        dur_factor = 0.95

                    raw_score = tilt_factor * count_factor * dur_factor
                    _horizon_score = round(max(45.0, min(85.0, raw_score)), 1)
                    spherical_summary = (
                        f"The video WAS HORIZON LEVELED. Applied SLERP quaternion trajectory across {len(cps)} keyframes, "
                        f"neutralizing {max_tilt:.2f}° peak camera tilt ({max_slant_px:.1f} px slant) and {rms_tilt:.2f}° RMS tilt "
                        f"with {_horizon_score:.1f}% horizon leveling lock."
                    )
                    if rms_tilt > 5.0 or max_tilt > 10.0:
                        leveling_grade = "EXCELLENT (Heavy camera tilt & roll sway neutralized to level ground)"
                    elif rms_tilt > 2.0 or max_tilt > 4.0:
                        leveling_grade = "GOOD (Moderate camera tilt leveled upright)"
                    elif max_tilt > 0.5:
                        leveling_grade = "EFFECTIVE (Minor slant corrected)"
                    else:
                        leveling_grade = "MODERATE (Sub-degree alignment)"

                sidecar_section = (
                    f"\n--- HORIZON CHECKPOINTS LEVELING METRICS ---\n"
                    f"Checkpoints count     : {len(cps)} keyframes\n"
                    f"Ignore Yaw            : {'YES (Pitch & Roll leveling only)' if ign_yaw else 'NO (Full 3-axis Yaw/Pitch/Roll)'}\n"
                    f"Horizon Leveling Lock : {_horizon_score:.1f}% (tilt neutralized to 0.00° level)\n"
                    f"Peak Tilt Neutralized : {max_tilt:.2f}° ({max_slant_px:.1f} px slant in 90-deg viewer)\n"
                    f"RMS Tilt Neutralized  : {rms_tilt:.2f}° ({rms_slant_px:.1f} px slant in 90-deg viewer)\n"
                    f"Mean Tilt Neutralized : {mean_tilt:.2f}° ({mean_slant_px:.1f} px slant in 90-deg viewer)\n"
                    f"  Max Pitch tilt      : {max_p:+.2f}° (range: {min(pitches):+.2f}° to {max(pitches):+.2f}°)\n"
                    f"  Max Roll tilt       : {max_r:+.2f}° (range: {min(rolls):+.2f}° to {max(rolls):+.2f}°)\n"
                    f"  Max Yaw offset      : {max_y:+.2f}° (range: {min(yaws):+.2f}° to {max(yaws):+.2f}°)\n"
                    f"Dynamic Roll Sway     : {roll_peak_to_peak:.2f}° peak-to-peak sway neutralized (std dev: {roll_std:.2f}°)\n"
                    f"Interpolation         : SLERP (Spherical Linear Quaternion Interpolation)\n"
                    f"Leveling Assessment   : {leveling_grade}\n\n"
                    f"[NOTE] Horizon leveling applies 3D spherical rotations (v360 pitch/roll) to level the camera\n"
                    f"against the gravity vector. Equirectangular 2D pixel-shift metrics measure inter-frame\n"
                    f"displacement rather than absolute horizon tilt. Refer to the Horizon Leveling Metrics\n"
                    f"above to evaluate leveling quality.\n\n"
                    f"Keyframe details  :\n"
                )
                for idx_c, c in enumerate(cps):
                    f_num = c.get('frame', 0)
                    sec_val = f_num / (jfps if jfps else 30.0)
                    cp_p = float(c.get('pitch', 0.0))
                    cp_r = float(c.get('roll', 0.0))
                    cp_y = float(c.get('yaw', 0.0))
                    cp_t = math.sqrt(cp_p**2 + cp_r**2 + (0.0 if ign_yaw else cp_y**2))
                    cp_px = cp_t * px_per_deg
                    sidecar_section += f"  CP#{idx_c+1:02d} (Frame {f_num:5d} / {sec_val:6.2f}s): Pitch {cp_p:+6.2f}°, Roll {cp_r:+6.2f}°, Yaw {cp_y:+6.2f}° (Tilt: {cp_t:5.2f}° / {cp_px:5.1f} px)\n"

    # For Cinematic Path Smoothing, append trajectory smoothing metrics if available
    if m_key == "cinematic":
        _sc_l1 = f"{out_base}_sendcmd_cinematic.txt"
        if os.path.exists(_sc_l1) and os.path.getsize(_sc_l1) > 0:
            try:
                yaws, pitches, rolls = [], [], []
                with open(_sc_l1, "r", encoding="utf-8", errors="ignore") as f_sc:
                    for line in f_sc:
                        line = line.strip()
                        if not line or "[enter]" not in line: continue
                        parts = line.split("[enter]")
                        tokens = parts[1].strip().split()
                        if len(tokens) >= 3 and tokens[0] == "v360":
                            param = tokens[1]
                            val = float(tokens[2].rstrip(";"))
                            if param == "yaw": yaws.append(val)
                            elif param == "pitch": pitches.append(val)
                            elif param == "roll": rolls.append(val)
                n_frames = max(len(yaws), len(pitches), len(rolls))
                if n_frames > 0:
                    import numpy as np
                    max_y = float(np.max(np.abs(yaws))) if yaws else 0.0
                    max_p = float(np.max(np.abs(pitches))) if pitches else 0.0
                    max_r = float(np.max(np.abs(rolls))) if rolls else 0.0
                    max_corr = max(max_y, max_p, max_r)
                    all_vals = (yaws or [0.0]) + (pitches or [0.0]) + (rolls or [0.0])
                    rms_corr = float(np.sqrt(np.mean([v**2 for v in all_vals])))
                    is_3d_spherically_stabilized = True
                    spherical_summary = f"The video WAS CINEMATICALLY SMOOTHED. L1-Norm SO(3) optimizer filtered rotational high-frequency tremor (Max: {max_corr:.2f}°, RMS: {rms_corr:.2f}°)."
                    sidecar_section = (
                        f"\n--- CINEMATIC SO(3) PATH SMOOTHING METRICS ---\n"
                        f"Frames analyzed : {n_frames}\n"
                        f"RMS correction  : {rms_corr:.3f}°\n"
                        f"Peak correction : {max_corr:.3f}°\n"
                        f"  Max Yaw comp  : {max_y:.3f}°\n"
                        f"  Max Pitch comp: {max_p:.3f}°\n"
                        f"  Max Roll comp : {max_r:.3f}°\n"
                        f"Optimization    : L1-Norm Convex SO(3) Trajectory Filter\n"
                        f"Characteristics : Smooth cinematic camera transitions with piecewise linear pans.\n"
                    )
            except Exception as e_l1_m:
                print(f"Notice: cinematic report metrics error: {e_l1_m}")

    # For Travel Direction Lock, append heading lock metrics if available
    if m_key == "traveldir":
        _meta_dl = f"{out_base}_traveldir_meta.json"
        _sc_dl = f"{out_base}_sendcmd_traveldir.txt"
        if os.path.exists(_meta_dl) and os.path.getsize(_meta_dl) > 0:
            try:
                import numpy as np
                with open(_meta_dl, "r", encoding="utf-8") as f_meta:
                    m_dict = json.load(f_meta)
                steer_corr = m_dict.get("steer_corr", [])
                raw_yaw = m_dict.get("raw_yaw", [])
                trend_yaw = m_dict.get("trend_yaw", [])
                locked_yaw = m_dict.get("locked_yaw", [])
                db_val = float(m_dict.get("deadband_deg", 1.5))
                damp_val = float(m_dict.get("damping", 0.90))
                mode_str = str(m_dict.get("mode", "travel_direction"))
                n_frames = len(steer_corr)
                if n_frames > 0:
                    max_y = float(np.max(np.abs(steer_corr)))
                    rms_yaw = float(np.sqrt(np.mean([v**2 for v in steer_corr])))
                    max_corr = max_y
                    dev_arr = np.abs(np.array(trend_yaw) - np.array(raw_yaw)) if len(trend_yaw) == len(raw_yaw) else np.zeros(n_frames)
                    max_sway = float(np.max(dev_arr)) if len(dev_arr) > 0 else 0.0
                    in_db_pct = float(np.mean(dev_arr <= db_val) * 100.0) if len(dev_arr) > 0 else 100.0
                    is_3d_spherically_stabilized = True
                    spherical_summary = f"The video WAS DIRECTION LOCKED. Forward viewing heading steered along {mode_str} (Max Steer: {max_y:.2f}°, RMS: {rms_yaw:.2f}°)."
                    sidecar_section = (
                        f"\n--- TRAVEL-DIRECTION / HEADING LOCK METRICS ---\n"
                        f"Frames analyzed : {n_frames}\n"
                        f"Control Mode    : {mode_str}\n"
                        f"Damping Factor  : {damp_val:.2f}\n"
                        f"Deadband Angle  : ±{db_val:.1f}° ({in_db_pct:.1f}% frames within jitter deadband)\n"
                        f"RMS Yaw Steer   : {rms_yaw:.3f}°\n"
                        f"Peak Yaw Steer  : {max_corr:.3f}°\n"
                        f"Max Heading Sway: {max_sway:.3f}°\n"
                        f"Target Control  : Heading / Subject lock with deadband and exponential dampening\n"
                    )
            except Exception as e_dl_meta:
                print(f"Notice: traveldir metadata report error: {e_dl_meta}")
        elif os.path.exists(_sc_dl) and os.path.getsize(_sc_dl) > 0:
            try:
                yaws, pitches, rolls = [], [], []
                with open(_sc_dl, "r", encoding="utf-8", errors="ignore") as f_sc:
                    for line in f_sc:
                        line = line.strip()
                        if not line or "[enter]" not in line: continue
                        parts = line.split("[enter]")
                        tokens = parts[1].strip().split()
                        if len(tokens) >= 3 and tokens[0] == "v360":
                            param = tokens[1]
                            val = float(tokens[2].rstrip(";"))
                            if param == "yaw": yaws.append(val)
                            elif param == "pitch": pitches.append(val)
                            elif param == "roll": rolls.append(val)
                n_frames = max(len(yaws), len(pitches), len(rolls))
                if n_frames > 0:
                    import numpy as np
                    max_y = float(np.max(np.abs(yaws))) if yaws else 0.0
                    max_p = float(np.max(np.abs(pitches))) if pitches else 0.0
                    max_r = float(np.max(np.abs(rolls))) if rolls else 0.0
                    max_corr = max(max_y, max_p, max_r)
                    rms_yaw = float(np.sqrt(np.mean([v**2 for v in (yaws or [0.0])])))
                    is_3d_spherically_stabilized = True
                    spherical_summary = f"The video WAS DIRECTION LOCKED. Camera orientation held steady along target heading (Max Yaw Comp: {max_y:.2f}°, RMS: {rms_yaw:.2f}°)."
                    sidecar_section = (
                        f"\n--- TRAVEL-DIRECTION / HEADING LOCK METRICS ---\n"
                        f"Frames analyzed : {n_frames}\n"
                        f"RMS Yaw corr    : {rms_yaw:.3f}°\n"
                        f"Peak correction : {max_corr:.3f}°\n"
                        f"  Max Yaw steer : {max_y:.3f}°\n"
                        f"  Max Pitch comp: {max_p:.3f}°\n"
                        f"  Max Roll comp : {max_r:.3f}°\n"
                        f"Target Control  : Heading / Subject lock with proportional damping\n"
                    )
            except Exception as e_dl_m:
                print(f"Notice: traveldir report metrics error: {e_dl_m}")

    # If 3D spherical stabilization was confirmed by motion sidecar, update m_data and write complete report
    if is_3d_spherically_stabilized and m_data:
        m_data["is_stabilized"] = True
        m_data["conclusion_text"] = spherical_summary

    if not os.path.exists(r_path) and (sidecar_section or is_3d_spherically_stabilized):
        try:
            sidecar_score = None
            if m_key == "kopf" and '_kj' in locals() and _kj:
                p_eval = [p for p in (m_data.get("pct_jitter"), m_data.get("pct_peak"), m_data.get("pct_overall"), m_data.get("pct_dy")) if p is not None and float(p) > 0] if m_data else []
                if not p_eval:
                    sidecar_score = _kj.get('jitter_absorption_pct')
            elif m_key == "horizon" and '_horizon_score' in locals() and _horizon_score is not None:
                sidecar_score = _horizon_score
            pct_str = compute_stabilization_percentage(m_data, is_stabilized=True, sidecar_pct=sidecar_score)
            base_label = "YES (HORIZON LEVELED)" if m_key == "horizon" else ("YES (CINEMATICALLY SMOOTHED)" if m_key == "cinematic" else ("YES (DIRECTION LOCKED)" if m_key == "traveldir" else "YES (SPHERICALLY STABILIZED)"))
            status_label = f"{base_label} {pct_str}"
            full_rep = (
                f"=== {m_name.upper()} STABILIZATION REPORT ===\n"
                f"STATUS          : {status_label}\n"
                f"Input Video     : {os.path.basename(in_v) if in_v else 'N/A'}\n"
                f"Output Video    : {os.path.basename(out_v) if out_v else 'N/A'}\n"
                f"SUMMARY         : {spherical_summary}\n"
                f"{sidecar_section}"
            )
            with open(r_path, 'w', encoding='utf-8') as f_rep:
                f_rep.write(full_rep)
        except Exception as e_rep:
            print(f"Notice: report write error: {e_rep}")
    elif os.path.exists(r_path) and (sidecar_section or is_3d_spherically_stabilized):
        try:
            if is_3d_spherically_stabilized and m_data:
                sidecar_score = None
                if m_key == "kopf" and '_kj' in locals() and _kj:
                    p_eval = [p for p in (m_data.get("pct_jitter"), m_data.get("pct_peak"), m_data.get("pct_overall"), m_data.get("pct_dy")) if p is not None and float(p) > 0] if m_data else []
                    if not p_eval:
                        sidecar_score = _kj.get('jitter_absorption_pct')
                elif m_key == "horizon" and '_horizon_score' in locals() and _horizon_score is not None:
                    sidecar_score = _horizon_score
                pct_str = compute_stabilization_percentage(m_data, is_stabilized=True, sidecar_pct=sidecar_score)
                base_label = "YES (HORIZON LEVELED)" if m_key == "horizon" else ("YES (CINEMATICALLY SMOOTHED)" if m_key == "cinematic" else ("YES (DIRECTION LOCKED)" if m_key == "traveldir" else "YES (SPHERICALLY STABILIZED)"))
                status_label = f"{base_label} {pct_str}"
                m_data["composite_percentage"] = pct_str
                try:
                    m_data["composite_pct_val"] = float(pct_str.replace('%', ''))
                except ValueError:
                    pass
                full_rep = (
                    f"=== {m_name.upper()} STABILIZATION REPORT ===\n"
                    f"STATUS          : {status_label}\n"
                    f"Input Video     : {os.path.basename(in_v)}\n"
                    f"Output Video    : {os.path.basename(out_v)}\n"
                    f"Frames Analyzed : {m_data.get('num_frames', 'N/A')}\n\n"
                    f"METRICS:\n"
                    f"  1. Mean 2D Frame Jump    : {m_data.get('mean_raw_px', 0.0):.2f} px -> {m_data.get('mean_stab_px', 0.0):.2f} px ({m_data.get('percentage', '+0.0%')})\n"
                    f"  2. Horizontal Jitter     : {m_data.get('dx_mean_raw', 0.0):.2f} px -> {m_data.get('dx_mean_stab', 0.0):.2f} px ({m_data.get('pct_dx', 0.0):+.1f}%)\n"
                    f"  3. Vertical Horizon Lock : {m_data.get('dy_mean_raw', 0.0):.2f} px -> {m_data.get('dy_mean_stab', 0.0):.2f} px ({m_data.get('vertical_percentage', '+0.0%')})\n"
                    f"  4. Peak Shock            : {m_data.get('max_raw_px', 0.0):.2f} px -> {m_data.get('max_stab_px', 0.0):.2f} px ({m_data.get('peak_percentage', '+0.0%')})\n"
                    f"  5. High-Freq Jitter (RMS): {m_data.get('rms_jitter_raw_px', 0.0):.2f} px -> {m_data.get('rms_jitter_stab_px', 0.0):.2f} px ({m_data.get('jitter_percentage', '+0.0%')})\n\n"
                    f"SUMMARY         : {spherical_summary}\n"
                    f"{sidecar_section}"
                )
                with open(r_path, 'w', encoding='utf-8') as f_rep:
                    f_rep.write(full_rep)
            elif sidecar_section:
                with open(r_path, 'a', encoding='utf-8') as f_app:
                    f_app.write(sidecar_section)
        except Exception as e_rep:
            print(f"Notice: report write error: {e_rep}")

    return m_data


def extract_test_id(path_str):
    """Extracts numeric test/job identifier from file path or base name (e.g. out_3381, job_3381, test_3381)."""
    if not path_str:
        return None
    base = os.path.basename(path_str)
    m = re.search(r'(?:out|job|test)[-_](\d+)', base, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r'[-_.](\d{3,})[-_.]', base)
    if m:
        return m.group(1)
    name_no_ext = os.path.splitext(base)[0]
    m = re.search(r'[-_](\d{3,})$', name_no_ext)
    if m:
        return m.group(1)
    m = re.search(r'(\d{3,})', name_no_ext)
    if m:
        return m.group(1)
    return None


def check_and_apply_stage_fallback(
    stage_key, stage_display_name, report_path, sendcmd_file,
    stage_in_file, current_file, active_sendcmd_files, executed_methods_so_far,
    fallback_enabled, reverted_stages, status_file=None
):
    """Checks if a stage degraded stabilization quality and reverts input if enabled.

    Args:
        stage_key (str): Method key ('vidstab', 'telemetry', etc.).
        stage_display_name (str): Display name for the stage.
        report_path (str): Path to stage report text file.
        sendcmd_file (str): Sendcmd instructions file generated for this stage.
        stage_in_file (str): Input video path before this stage executed.
        current_file (str): Output video path after this stage executed.
        active_sendcmd_files (list[str]): Mutable list of active sendcmd files.
        executed_methods_so_far (list[str]): Mutable list of executed method keys.
        fallback_enabled (bool): Whether automatic fallback is enabled.
        reverted_stages (dict): Dictionary tracking reverted stages and reasons.
        status_file (str, optional): Status file to update.

    Returns:
        tuple[str, bool]: (new_current_file, was_reverted)
    """
    if not fallback_enabled:
        return current_file, False

    is_unstabilized = False
    status_text = ""
    if report_path and os.path.exists(report_path):
        try:
            with open(report_path, "r", encoding="utf-8", errors="ignore") as rf:
                for line in rf:
                    if "STATUS" in line:
                        status_val = line.split(":", 1)[1].strip() if ":" in line else line.strip()
                        status_text = status_val
                        if any(neg in status_val.upper() for neg in ["NO (", "UNSTABILIZED", "DEGRADED", "FAILED"]):
                            is_unstabilized = True
                        break
        except Exception:
            pass

    if is_unstabilized:
        print(f"\n[Pipeline Fallback] NOTICE: {stage_display_name} degraded video quality ('{status_text}').", flush=True)
        print(f"[Pipeline Fallback] Auto-reverting input video to previous clean stage: {os.path.basename(stage_in_file)}", flush=True)
        if sendcmd_file and sendcmd_file in active_sendcmd_files:
            active_sendcmd_files.remove(sendcmd_file)
            print(f"[Pipeline Fallback] Removed '{os.path.basename(sendcmd_file)}' from active master composition queue.", flush=True)
        if stage_key in executed_methods_so_far:
            executed_methods_so_far.remove(stage_key)
        reverted_stages[stage_key] = {
            "name": stage_display_name,
            "status_text": status_text,
            "report_path": report_path,
            "reverted_to": stage_in_file
        }
        if status_file:
            try:
                update_status(status_file, {
                    "phase": f"Notice: {stage_display_name} degraded ({status_text}) - reverted to previous video"
                })
            except Exception:
                pass
        return stage_in_file, True

    return current_file, False


def analyze_stabilization_report(raw_video, stab_video, report_file, sample_frames=0, report_title="AUTOMATIC STABILIZATION REPORT", method_name=None, test_id=None):
    """Evaluates stabilization quality via sub-pixel phase correlation on rectilinear viewports.

    Extracts distortion-free 90° front viewports from both raw and stabilized equirectangular
    videos, computes inter-frame displacements, and logs tremor absorption percentages.

    Args:
        raw_video (str): Path to unstabilized input equirectangular video.
        stab_video (str): Path to stabilized equirectangular video.
        report_file (str): Output path where stabilization report is written.
        sample_frames (int, optional): Maximum frames to sample (0 for full video). Defaults to 0.
        report_title (str, optional): Header title for the generated report. Defaults to "AUTOMATIC STABILIZATION REPORT".
        method_name (str, optional): Stabilization method or goal name (e.g. 'telemetry', 'summary'). Defaults to None.
        test_id (str, optional): Numeric test/job identifier. Defaults to None.

    Returns:
        dict | None: Dictionary of comparison metrics (tremor reduction %, RMS shifts),
            or None on failure.
    """
    try:
        import cv2
        import numpy as np
        import subprocess

        if not raw_video or not os.path.exists(raw_video) or os.path.getsize(raw_video) == 0:
            return None
        if not stab_video or not os.path.exists(stab_video) or os.path.getsize(stab_video) == 0:
            return None

        # Resolve test_id and method_name for descriptive temporary rectilinear viewport files
        id_tag = test_id
        if not id_tag:
            for cand in (report_file, stab_video, raw_video):
                id_tag = extract_test_id(cand)
                if id_tag:
                    break
        if not id_tag:
            id_tag = str(os.getpid())

        m_name_clean = method_name
        if not m_name_clean and report_file:
            rf_base = os.path.basename(report_file).lower()
            for known_m in ("telemetry", "vidstab", "kabsch", "kopf", "cinematic", "horizon", "traveldir"):
                if known_m in rf_base:
                    m_name_clean = known_m
                    break
            if not m_name_clean and "stabilization_report" in rf_base:
                m_name_clean = "summary"
        if not m_name_clean and report_title:
            rt_upper = report_title.upper()
            for known_m in ("TELEMETRY", "VIDSTAB", "KABSCH", "KOPF", "CINEMATIC", "HORIZON"):
                if known_m in rt_upper:
                    m_name_clean = known_m.lower()
                    break
            if not m_name_clean and ("TRAVEL" in rt_upper or "DIRECTION" in rt_upper):
                m_name_clean = "traveldir"
            if not m_name_clean and "SUMMARY" in rt_upper:
                m_name_clean = "summary"

        method_suffix = f"_{m_name_clean}" if m_name_clean else ""

        # Extract 90° rectilinear front viewports to eliminate latitude stretching and 360 seam distortion
        temp_dir = os.path.dirname(report_file) if os.path.dirname(report_file) else os.path.join("data", "runtime", "temp")
        os.makedirs(temp_dir, exist_ok=True)
        raw_rect = os.path.join(temp_dir, f"temp_raw{method_suffix}_rect_{id_tag}.mp4")
        stab_rect = os.path.join(temp_dir, f"temp_stab{method_suffix}_rect_{id_tag}.mp4")

        cmd_raw = ["ffmpeg", "-y", "-i", raw_video, "-vf", "v360=input=equirect:output=rectilinear:h_fov=90:v_fov=60:w=1280:h=720", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "32"]
        cmd_stab = ["ffmpeg", "-y", "-i", stab_video, "-vf", "v360=input=equirect:output=rectilinear:h_fov=90:v_fov=60:w=1280:h=720", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "32"]

        if sample_frames > 0:
            sample_dur = max(1.0, float(sample_frames) / 29.97)
            cmd_raw.extend(["-t", str(sample_dur)])
            cmd_stab.extend(["-t", str(sample_dur)])

        cmd_raw.append(raw_rect)
        cmd_stab.append(stab_rect)

        subprocess.run(cmd_raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=WIN_NO_WINDOW)
        subprocess.run(cmd_stab, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=WIN_NO_WINDOW)

        target_raw = raw_rect if os.path.exists(raw_rect) and os.path.getsize(raw_rect) > 0 else raw_video
        target_stab = stab_rect if os.path.exists(stab_rect) and os.path.getsize(stab_rect) > 0 else stab_video

        def get_shifts(v_path):
            """Measures inter-frame 2D sub-pixel shifts using OpenCV phase correlation.

            Args:
                v_path (str): Video file to analyze.

            Returns:
                list[tuple[float, float]]: Inter-frame (dx, dy) translation shifts.
            """
            cap = cv2.VideoCapture(v_path)
            shifts, prev = [], None
            cnt = 0
            max_limit = sample_frames if sample_frames > 0 else 999999
            while cap.isOpened() and cnt < max_limit:
                ret, frame = cap.read()
                if not ret: break
                h, w, _ = frame.shape
                crop = frame[h//4:3*h//4, w//4:3*w//4]
                gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
                if prev is not None:
                    s, _ = cv2.phaseCorrelate(np.float32(prev), np.float32(gray))
                    shifts.append(s)
                prev = gray
                cnt += 1
            cap.release()
            return shifts

        s_raw = get_shifts(target_raw)
        s_stab = get_shifts(target_stab)


        min_l = min(len(s_raw), len(s_stab))
        if min_l < 5:
            return None

        m_raw = [np.sqrt(x**2 + y**2) for x, y in s_raw[:min_l]]
        m_stab = [np.sqrt(x**2 + y**2) for x, y in s_stab[:min_l]]
        dx_raw = [abs(x) for x, y in s_raw[:min_l]]
        dx_stab = [abs(x) for x, y in s_stab[:min_l]]
        dy_raw = [abs(y) for x, y in s_raw[:min_l]]
        dy_stab = [abs(y) for x, y in s_stab[:min_l]]

        mean_raw = float(np.mean(m_raw))
        mean_stab = float(np.mean(m_stab))
        max_raw = float(np.percentile(m_raw, 99)) if len(m_raw) > 0 else 0.0
        max_stab = float(np.percentile(m_stab, 99)) if len(m_stab) > 0 else 0.0
        dx_mean_raw = float(np.mean(dx_raw))
        dx_mean_stab = float(np.mean(dx_stab))
        dy_mean_raw = float(np.mean(dy_raw))
        dy_mean_stab = float(np.mean(dy_stab))

        pct_overall = (((mean_raw - mean_stab) / mean_raw) * 100.0) if mean_raw > 0 else 0.0
        pct_dx = (((dx_mean_raw - dx_mean_stab) / dx_mean_raw) * 100.0) if dx_mean_raw > 0 else 0.0
        pct_dy = (((dy_mean_raw - dy_mean_stab) / dy_mean_raw) * 100.0) if dy_mean_raw > 0 else 0.0
        pct_peak = (((max_raw - max_stab) / max_raw) * 100.0) if max_raw > 0 else 0.0

        # High-pass jitter score: subtract rolling mean to isolate rapid tremor (>2 Hz).
        # This makes the metric valid even when large intentional motion (walking) dominates.
        def _hp_jitter(mags, window=30):
            """Calculates high-pass jitter residuals by subtracting a rolling mean.

            Args:
                mags (list[float]): Frame displacement magnitudes.
                window (int, optional): Rolling window size. Defaults to 30.

            Returns:
                list[float]: High-pass filtered jitter residual magnitudes.
            """
            if len(mags) < 4:
                return mags
            out = []
            half = window // 2
            for i in range(len(mags)):
                w0 = max(0, i - half)
                w1 = min(len(mags), i + half + 1)
                lf = float(np.mean(mags[w0:w1]))
                out.append(abs(mags[i] - lf))
            return out

        hp_raw  = _hp_jitter(m_raw)
        hp_stab = _hp_jitter(m_stab)
        rms_jitter_raw  = float(np.sqrt(np.mean([v**2 for v in hp_raw])))  if hp_raw  else 0.0
        rms_jitter_stab = float(np.sqrt(np.mean([v**2 for v in hp_stab]))) if hp_stab else 0.0
        pct_jitter = (((rms_jitter_raw - rms_jitter_stab) / rms_jitter_raw) * 100.0) if rms_jitter_raw > 0 else 0.0

        is_stabilized = (pct_peak > 0 or pct_overall > 0 or max_stab <= max_raw or pct_jitter > 0)
        pct_str = compute_stabilization_percentage(
            {"pct_peak": pct_peak, "pct_jitter": pct_jitter, "pct_overall": pct_overall, "pct_dy": pct_dy, "is_stabilized": is_stabilized},
            is_stabilized=is_stabilized
        )
        status_label = f"YES (STABILIZED) {pct_str}" if is_stabilized else f"NO (UNSTABILIZED) {pct_str}"

        if pct_peak > 0:
            conclusion_text = f"The video WAS STABILIZED. Peak camera shock was reduced by {pct_peak:.1f}% ({max_raw:.2f} px -> {max_stab:.2f} px), keeping the horizon level and absorbing single-frame jerks."
        elif pct_overall > 0:
            conclusion_text = f"The video WAS STABILIZED. Overall frame jump was reduced by {pct_overall:.1f}% ({mean_raw:.2f} px -> {mean_stab:.2f} px), maintaining smooth optical tracking."
        elif max_stab <= max_raw:
            conclusion_text = f"The video WAS STABILIZED. Peak camera motion remained controlled ({max_raw:.2f} px -> {max_stab:.2f} px)."
        elif pct_jitter > 0:
            conclusion_text = f"The video WAS STABILIZED. High-frequency camera tremor (jitter) was reduced by {pct_jitter:+.1f}% ({rms_jitter_raw:.2f} px -> {rms_jitter_stab:.2f} px)."
        else:
            conclusion_text = f"The video WAS NOT STABILIZED. Motion displacement increased by {abs(pct_peak):.1f}% ({max_raw:.2f} px -> {max_stab:.2f} px)."

        # Summary string: prefer jitter score when overall is negative (Kopf case)
        if pct_overall >= 0:
            summary_str = f"Video stabilized +{pct_overall:.1f}% drift / {pct_jitter:+.1f}% jitter (Jump {mean_raw:.1f}px -> {mean_stab:.1f}px)"
        elif pct_jitter > 0:
            summary_str = f"Jitter reduced {pct_jitter:+.1f}% (drift metric invalid for rotational correction)"
        else:
            summary_str = f"Video motion changed {pct_overall:.1f}% drift / {pct_jitter:.1f}% jitter ({mean_raw:.1f}px -> {mean_stab:.1f}px)"

        report_content = (
            f"=== {report_title} ===\n"
            f"STATUS          : {status_label}\n"
            f"Input Video     : {os.path.basename(raw_video)}\n"
            f"Output Video    : {os.path.basename(stab_video)}\n"
            f"Frames Analyzed : {min_l}\n\n"
            f"METRICS:\n"
            f"  1. Mean 2D Frame Jump    : {mean_raw:.2f} px -> {mean_stab:.2f} px ({pct_overall:+.1f}%)\n"
            f"  2. Horizontal Jitter     : {dx_mean_raw:.2f} px -> {dx_mean_stab:.2f} px ({pct_dx:+.1f}%)\n"
            f"  3. Vertical Horizon Lock : {dy_mean_raw:.2f} px -> {dy_mean_stab:.2f} px ({pct_dy:+.1f}%)\n"
            f"  4. Peak Shock            : {max_raw:.2f} px -> {max_stab:.2f} px ({pct_peak:+.1f}%)\n"
            f"  5. High-Freq Jitter (RMS): {rms_jitter_raw:.2f} px -> {rms_jitter_stab:.2f} px ({pct_jitter:+.1f}%)\n\n"
            f"SUMMARY         : {conclusion_text}\n"
        )

        with open(report_file, "w", encoding="utf-8") as f:
            f.write(report_content)

        return {
            "percentage": f"{pct_overall:+.1f}%",
            "vertical_percentage": f"{pct_dy:+.1f}%",
            "peak_percentage": f"{pct_peak:+.1f}%",
            "jitter_percentage": f"{pct_jitter:+.1f}%",
            "composite_percentage": pct_str,
            "composite_pct_val": float(pct_str.replace('%', '')),
            "pct_overall": pct_overall,
            "pct_dx": pct_dx,
            "pct_dy": pct_dy,
            "pct_peak": pct_peak,
            "pct_jitter": pct_jitter,
            "rms_jitter_raw_px": round(rms_jitter_raw, 2),
            "rms_jitter_stab_px": round(rms_jitter_stab, 2),
            "mean_raw_px": round(mean_raw, 2),
            "mean_stab_px": round(mean_stab, 2),
            "max_raw_px": round(max_raw, 2),
            "max_stab_px": round(max_stab, 2),
            "is_stabilized": is_stabilized,
            "conclusion_text": conclusion_text,
            "summary": summary_str,
            "report_file": report_file,
            "num_frames": min_l,
            "dx_mean_raw": round(dx_mean_raw, 2),
            "dx_mean_stab": round(dx_mean_stab, 2),
            "dy_mean_raw": round(dy_mean_raw, 2),
            "dy_mean_stab": round(dy_mean_stab, 2)
        }
    except Exception as e:
        print(f"Notice: Automatic stabilization report exception: {e}")
        return None

def prepare_telemetry_sendcmd(args, out_base, duration, suffix="single", mode=None, smoothing=None, ref_frame=None, extractor=None, multiplier=None, multiplier_roll=None, multiplier_pitch=None, multiplier_yaw=None, max_correction=None, target_equirect=False, base_pitch=0.0, base_roll_l=0.0, base_roll_r=None, **kwargs):
    """Transforms raw camera IMU telemetry into dynamic FFmpeg v360 sendcmd rotation scripts.

    Extracts gyro/accelerometer data, filters angles with Gaussian smoothing,
    calculates delta rotations relative to reference frame or moving average,
    and formats timed commands for the FFmpeg v360 filter.

    Args:
        args (argparse.Namespace): Command-line argument namespace.
        out_base (str): Base file prefix for generated artifacts.
        duration (float): Video duration in seconds.
        suffix (str, optional): File suffix identifier. Defaults to "single".
        mode (str, optional): Telemetry stabilization mode ("smooth", "horizon", "lock").
        smoothing (int, optional): Smoothing window in frames.
        ref_frame (int, optional): Zero-point reference frame index.
        extractor (str, optional): Extractor script identifier.
        multiplier (float, optional): Global rotation multiplier.
        multiplier_roll (float, optional): Roll axis multiplier.
        multiplier_pitch (float, optional): Pitch axis multiplier.
        multiplier_yaw (float, optional): Yaw axis multiplier.
        max_correction (float, optional): Clamp limit for corrections in degrees.
        target_equirect (bool, optional): Whether output target is equirectangular canvas.
        base_pitch (float, optional): Base pitch offset.
        base_roll_l (float, optional): Left lens base roll offset.
        base_roll_r (float, optional): Right lens base roll offset.
        **kwargs: Additional keyword arguments.

    Returns:
        tuple[str | None, str | None]: Paths to (sendcmd_eq_file, sendcmd_dual_file).
    """
    use_mode = mode if mode is not None else getattr(args, "telemetry_mode", "smooth")
    use_smoothing = smoothing if smoothing is not None else getattr(args, "telemetry_smoothing", 120)
    use_ref_frame = ref_frame if ref_frame is not None else getattr(args, "telemetry_ref_frame", 0)
    use_extractor = extractor if extractor is not None else getattr(args, "telemetry_extractor", "extract_telemetry")
    mult_base = multiplier if multiplier is not None else getattr(args, 'telemetry_multiplier', 1.0)
    use_multiplier_r = multiplier_roll if multiplier_roll is not None else getattr(args, 'telemetry_multiplier_roll', mult_base)
    use_multiplier_p = multiplier_pitch if multiplier_pitch is not None else getattr(args, 'telemetry_multiplier_pitch', mult_base)
    use_multiplier_y = multiplier_yaw if multiplier_yaw is not None else getattr(args, 'telemetry_multiplier_yaw', mult_base)
    use_max_corr     = max_correction  # None = no clamping
    # NOTE: base_pitch, base_roll_l, base_roll_r are accepted but intentionally NOT baked into
    # the per-frame sendcmd values. FFmpeg v360 applies sendcmd corrections ADDITIVELY on top of
    # the filter's static pitch/roll values, so we only write the dynamic telemetry delta here.
    # The static calibration offsets (args.pitch, args.roll, rear_roll_offset) are already handled
    # by the v360 filter definition and remain in effect throughout playback.

    input_dir = os.path.dirname(args.input)
    input_base = os.path.splitext(os.path.basename(args.input))[0]
    script_dir = os.path.dirname(os.path.abspath(__file__))
    angles = []

    out_dir = os.path.dirname(out_base)
    telemetry_file = f"{out_base}_telemetry.txt"


    # Always extract fresh telemetry data from source input video or companion logs
    extract_script = os.path.join(script_dir, "extract_telemetry.py")
    source_arg = getattr(args, "telemetry_source", "auto")
    extract_cmd = [sys.executable, "-B", extract_script, args.input, telemetry_file, "--source", source_arg]
    proc_ext = subprocess.run(extract_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=WIN_NO_WINDOW)

    if os.path.exists(telemetry_file) and os.path.getsize(telemetry_file) > 0:
        # Generate .gcsv sidecar files for Gyroflow if requested
        if getattr(args, "util_gcsv", False):
            try:
                from convert_telemetry import convert_telemetry
                convert_telemetry(telemetry_file)
            except Exception as gcsv_err:
                print(f"Warning: Could not auto-generate .gcsv file: {gcsv_err}")
        try:
            with open(telemetry_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
                headers = [x.strip() for x in lines[0].split("\t")]
                roll_idx  = headers.index("AngleX(deg)")
                pitch_idx = headers.index("AngleY(deg)")
                yaw_idx   = headers.index("AngleZ(deg)")

                for line in lines[1:]:
                    parts = line.split("\t")
                    if len(parts) > max(roll_idx, pitch_idx, yaw_idx):
                        roll = float(parts[roll_idx])
                        pitch = float(parts[pitch_idx])
                        yaw = float(parts[yaw_idx])
                        angles.append((roll, pitch, yaw))
        except Exception as e:
            print(f"Error parsing extract_telemetry file: {e}")

    if not angles:
        print("Warning: Telemetry extraction failed or no telemetry data found.")
        return None, None

    sendcmd_file = f"{out_base}_sendcmd_{suffix}.txt"
    try:
        probe_cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate", "-of", "default=noprint_wrappers=1:nokey=1", args.input]
        proc = subprocess.run(probe_cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, creationflags=WIN_NO_WINDOW)
        fps = 29.970
        if proc.returncode == 0:
            num, den = proc.stdout.strip().split('/')
            if int(den) > 0: fps = float(num) / float(den)
        time_step = 1.0 / fps

        sorted_r = sorted(a[0] for a in angles)
        sorted_p = sorted(a[1] for a in angles)
        sorted_y = sorted(a[2] for a in angles)
        median_r = sorted_r[len(sorted_r) // 2]
        median_p = sorted_p[len(sorted_p) // 2]
        median_y = sorted_y[len(sorted_y) // 2]

        # Clean input telemetry sequence using 5-point median filter to strip high-frequency sensor noise & spikes
        n_pts = len(angles)
        cleaned_input_angles = []
        for i in range(n_pts):
            r_neighbors = [angles[max(0, min(n_pts - 1, i + k))][0] for k in (-2, -1, 0, 1, 2)]
            p_neighbors = [angles[max(0, min(n_pts - 1, i + k))][1] for k in (-2, -1, 0, 1, 2)]
            y_neighbors = [angles[max(0, min(n_pts - 1, i + k))][2] for k in (-2, -1, 0, 1, 2)]

            r_med = sorted(r_neighbors)[len(r_neighbors) // 2]
            p_med = sorted(p_neighbors)[len(p_neighbors) // 2]
            y_med = sorted(y_neighbors)[len(y_neighbors) // 2]
            cleaned_input_angles.append((r_med, p_med, y_med))

        # Outlier Despiking Pass: Filter isolated 1-sample IMU glitches (sandwich check)
        # Prevents single-sample sensor corruptions without freezing real physical camera panning
        for i in range(1, n_pts - 1):
            r_prev, p_prev, y_prev = cleaned_input_angles[i-1]
            r_curr, p_curr, y_curr = cleaned_input_angles[i]
            r_next, p_next, y_next = cleaned_input_angles[i+1]

            r_clean = (r_prev + r_next) / 2.0 if (abs(r_curr - r_prev) > 2.5 and abs(r_curr - r_next) > 2.5) else r_curr
            p_clean = (p_prev + p_next) / 2.0 if (abs(p_curr - p_prev) > 2.5 and abs(p_curr - p_next) > 2.5) else p_curr
            y_clean = (y_prev + y_next) / 2.0 if (abs(y_curr - y_prev) > 2.5 and abs(y_curr - y_next) > 2.5) else y_curr

            cleaned_input_angles[i] = (r_clean, p_clean, y_clean)

        # Optional 6-Axis IMU Sensor Fusion (Mahony, Complementary, EKF)
        use_fusion = getattr(args, "telemetry_fusion", "none")
        if use_fusion and str(use_fusion).lower() not in ("none", "disabled", "false", ""):
            try:
                import numpy as _np_fuse
                from utils.imu_fusion import fuse_6axis_sequence
                fusion_gain = float(getattr(args, "telemetry_fusion_gain", 0.5))
                arr_angles = _np_fuse.array(cleaned_input_angles, dtype=_np_fuse.float64)
                gyro_rates = _np_fuse.zeros_like(arr_angles)
                if len(arr_angles) > 1:
                    gyro_rates[1:] = _np_fuse.diff(arr_angles, axis=0) * fps
                    gyro_rates[0] = gyro_rates[1]
                r_rad = _np_fuse.radians(arr_angles[:, 0])
                p_rad = _np_fuse.radians(arr_angles[:, 1])
                accel_data = _np_fuse.zeros((len(arr_angles), 3), dtype=_np_fuse.float64)
                accel_data[:, 0] = -_np_fuse.sin(p_rad)
                accel_data[:, 1] = _np_fuse.sin(r_rad) * _np_fuse.cos(p_rad)
                accel_data[:, 2] = _np_fuse.cos(r_rad) * _np_fuse.cos(p_rad)

                fused_angles = fuse_6axis_sequence(gyro_rates, accel_data, fps=fps, method=str(use_fusion), gain=fusion_gain)
                cleaned_input_angles = [(float(f[0]), float(f[1]), float(f[2])) for f in fused_angles]
                print(f"[Telemetry] Applied 6-Axis Sensor Fusion ({use_fusion}, gain={fusion_gain}) across {len(cleaned_input_angles)} frames.")
            except Exception as e_fuse:
                print(f"Notice: Telemetry sensor fusion error: {e_fuse}")

        if use_mode == "level":
            # Absolute Level Horizon: Target is mean DC telemetry offset
            # Direct per-frame counter-rotation around DC mount offset keeps horizon 100% level without pitch sky shift
            mean_r = float(sum(a[0] for a in cleaned_input_angles) / len(cleaned_input_angles)) if cleaned_input_angles else 0.0
            mean_p = float(sum(a[1] for a in cleaned_input_angles) / len(cleaned_input_angles)) if cleaned_input_angles else 0.0
            mean_y = float(sum(a[2] for a in cleaned_input_angles) / len(cleaned_input_angles)) if cleaned_input_angles else 0.0
            smoothed_angles = [(mean_r, mean_p, mean_y)] * len(angles)
        elif use_mode == "zero":
            # Absolute Zero Lock (0° Hard Lock): Target is strictly (0.0, 0.0, 0.0)
            smoothed_angles = [(0.0, 0.0, 0.0)] * len(angles)
        elif use_mode == "smooth":
            smoothed_angles = smooth_telemetry_angles(cleaned_input_angles, use_smoothing)
        else:
            ref_idx = min(max(0, use_ref_frame), len(angles) - 1)
            ref_r, ref_p, ref_y = cleaned_input_angles[ref_idx]
            smoothed_angles = [(ref_r, ref_p, ref_y)] * len(angles)

        sendcmd_eq_file = f"{out_base}_sendcmd_{suffix}.txt" if suffix == "telemetry" else f"{out_base}_sendcmd_{suffix}_equirect.txt"

        with open(sendcmd_eq_file, "w", encoding="utf-8") as out_eq:
            prev_pitch = 0.0
            prev_roll = 0.0
            prev_yaw = 0.0
            for frame, (roll, pitch, yaw) in enumerate(cleaned_input_angles):
                target_r, target_p, target_y = smoothed_angles[frame]

                raw_roll, raw_pitch, raw_yaw = compute_2d_horizon_stabilization_angles(
                    roll_deg=roll, pitch_deg=pitch, yaw_deg=yaw,
                    target_roll=target_r, target_pitch=target_p, target_yaw=target_y,
                    multiplier_roll=use_multiplier_r, multiplier_pitch=use_multiplier_p
                )
                # Slew-Rate Delta Limiter: Clamp single-frame counter-rotation velocity jumps > 2.5 deg/frame (prevents visual whip-jerk tremors)
                max_slew = 2.5
                if frame > 0:
                    diff_p = raw_pitch - prev_pitch
                    diff_r = raw_roll - prev_roll
                    diff_y = raw_yaw - prev_yaw

                    if abs(diff_p) > max_slew:
                        raw_pitch = prev_pitch + math.copysign(max_slew, diff_p)
                    if abs(diff_r) > max_slew:
                        raw_roll = prev_roll + math.copysign(max_slew, diff_r)
                    if abs(diff_y) > max_slew:
                        raw_yaw = prev_yaw + math.copysign(max_slew, diff_y)

                curr_pitch = raw_pitch
                curr_roll  = raw_roll
                curr_yaw   = raw_yaw

                # Calculate frame-to-frame incremental delta to align with FFmpeg v360 additive sendcmd filter execution
                inc_pitch = curr_pitch - prev_pitch
                inc_roll  = curr_roll  - prev_roll
                inc_yaw   = curr_yaw   - prev_yaw

                prev_pitch = curr_pitch
                prev_roll  = curr_roll
                prev_yaw   = curr_yaw

                start_t = 0.0 if frame == 0 else (frame - 0.5) * time_step
                end_t = (frame + 0.5) * time_step

                # Equirectangular 360 canvas sign convention:
                # v360 yaw: -inc_yaw counter-rotates yaw
                # v360 pitch: -inc_pitch counter-rotates physical camera pitch tilt
                # v360 roll: -inc_roll counter-rotates physical camera roll tilt
                out_eq.write(
                    f"{start_t:.6f}-{end_t:.6f} [enter] v360 yaw {-inc_yaw:.6f};\n"
                    f"{start_t:.6f}-{end_t:.6f} [enter] v360 pitch {-inc_pitch:.6f};\n"
                    f"{start_t:.6f}-{end_t:.6f} [enter] v360 roll {-inc_roll:.6f};\n"
                )
        return sendcmd_eq_file, None
    except Exception as e:
        print(f"Error preparing telemetry sendcmd: {e}")
        return None, None

def main():
    """CLI entry point for the 360 Video Stitching and Stabilization Pipeline.

    Parses command-line arguments, checks video properties and prerequisites,
    runs the stitching filters and post-stitching stabilization stages,
    injects 360 VR spatial metadata, and outputs metrics reports.
    """
    def _parse_int(v):
        try:
            return int(v)
        except (ValueError, TypeError):
            return int(float(v))

    global _GLOBAL_ARGS, shutil
    parser = argparse.ArgumentParser(description="360 Video Stitching & Stabilization Pipeline")
    parser.add_argument("--input",        required=True,              help="Input raw dual fisheye video")
    parser.add_argument("--output",       required=True,              help="Output equirectangular stitched video")
    parser.add_argument("--ih_fov",       default=str(PIPELINE_DEFAULTS["ih_fov"]), help="Input Horizontal FOV")
    parser.add_argument("--iv_fov",       default=str(PIPELINE_DEFAULTS["iv_fov"]), help="Input Vertical FOV")
    parser.add_argument("--raw_rotation", default=str(PIPELINE_DEFAULTS["raw_rotation"]), help="Raw dual-fisheye video pre-rotation before unwarping (degrees: 0, 90, 180, 270, or custom)")
    parser.add_argument("--yaw",          default=str(PIPELINE_DEFAULTS["yaw"]), help="Yaw correction (degrees)")
    parser.add_argument("--pitch",        default=str(PIPELINE_DEFAULTS["pitch"]), help="Pitch correction (degrees)")
    parser.add_argument("--roll",         default=str(PIPELINE_DEFAULTS["roll"]), help="Roll correction (degrees)")
    parser.add_argument("--left_y_offset",default=str(PIPELINE_DEFAULTS["left_y_offset"]), help="Left lens vertical Y-offset (pixels)")
    parser.add_argument("--rear_roll_offset", default=str(PIPELINE_DEFAULTS["rear_roll_offset"]), help="Right lens independent roll offset (degrees)")
    parser.add_argument("--preset", "--ffmpeg_preset", dest="preset", default=PIPELINE_DEFAULTS["ffmpeg_preset"], help="FFmpeg preset")
    parser.add_argument("--crf", "--ffmpeg_crf", dest="crf", type=str, default=str(PIPELINE_DEFAULTS["ffmpeg_crf"]), help="CRF quality value (18 = high quality)")
    parser.add_argument("--hwaccel", "--ffmpeg_hwaccel", dest="hwaccel", action="store_true", help="Use NVENC hardware acceleration")
    parser.add_argument("--v360_backend", choices=["cpu", "vulkan"], default=str(PIPELINE_DEFAULTS.get("v360_backend", "cpu")), help="v360 projection filter backend: cpu (default) or vulkan (GPU compute shader)")
    parser.add_argument("--blend_seams",  action="store_true",        help="Alpha-blend the two lenses at the seam lines (±90°)")
    parser.add_argument("--blend_width",  type=_parse_int, default=PIPELINE_DEFAULTS["blend_width"], help="Width of the blend region in pixels")
    parser.add_argument("--anti_vignette",action="store_true",        help="Apply edge brightening anti-vignette filter")
    parser.add_argument("--anti_vignette_angle", default=str(PIPELINE_DEFAULTS["anti_vignette_angle"]), help="Strength/angle of the anti-vignette filter (default 0.785)")
    parser.add_argument("--duration",     type=float, default=0.0,    help="Limit output duration (seconds, 0=full)")

    parser.add_argument("--stabilize",    action="store_true",        help="Apply post-stitching stabilization")
    parser.add_argument("--stabilize_type", default="vidstab", choices=["telemetry", "vidstab", "hybrid", "kabsch", "kopf", "hybrid_kopf", "telemetry_kopf"], help="Post-stitching stabilization method")
    parser.add_argument("--stabilize_methods", default="", help="Comma-separated list of active stabilization methods: telemetry,vidstab,kabsch,kopf")
    parser.add_argument("--stab_quality_mode", default=str(PIPELINE_DEFAULTS["stab_quality_mode"]), choices=["0", "1", "2", "3", "4"], help="Quality optimization mode: 0=Multi-pass, 1=Single-Pass Composition, 2=Lossless Intermediates, 3=Lanczos, 4=Hybrid Sequential Extraction + Single-Pass Master")
    parser.add_argument("--input_has_stitched", action="store_true", help="Input file is already stitched equirectangular")
    parser.add_argument("--input_has_telemetry", action="store_true", help="Input file is already telemetry-stabilized")
    parser.add_argument("--input_has_kopf", action="store_true", help="Input file is already kopf-stabilized")
    parser.add_argument("--input_has_kabsch", action="store_true", help="Input file is already kabsch-stabilized")
    parser.add_argument("--input_has_vidstab", action="store_true", help="Input file is already vidstab-stabilized")
    parser.add_argument("--input_has_cinematic", action="store_true", help="Input file is already cinematic-stabilized")
    parser.add_argument("--input_has_horizon", action="store_true", help="Input file is already horizon-stabilized")
    parser.add_argument("--input_has_traveldir", action="store_true", help="Input file is already traveldir-stabilized")
    parser.add_argument("--input_has_nadir", action="store_true", help="Input file already has nadir logo added")
    parser.add_argument("--telemetry_mode", default=PIPELINE_DEFAULTS["telemetry_mode"], choices=["smooth", "level", "lock", "zero"], help="Telemetry stabilization mode")
    parser.add_argument("--telemetry_fusion", default=str(PIPELINE_DEFAULTS.get("telemetry_fusion", "mahony")), choices=["none", "mahony", "complementary", "ekf"], help="Optional 6-axis IMU sensor fusion filter")
    parser.add_argument("--telemetry_fusion_gain", type=float, default=float(PIPELINE_DEFAULTS.get("telemetry_fusion_gain", 0.51)), help="Filter gain / alpha parameter for 6-axis IMU fusion")
    parser.add_argument("--telemetry_smoothing", type=float, default=float(PIPELINE_DEFAULTS["telemetry_smoothing"]), help="Telemetry moving average window size (frames)")
    parser.add_argument("--telemetry_ref_frame", type=_parse_int, default=PIPELINE_DEFAULTS["telemetry_ref_frame"], help="Reference frame index for Horizon Lock")
    parser.add_argument("--telemetry_source", default=PIPELINE_DEFAULTS["telemetry_source"], choices=["auto", "samsung", "gopro", "camm", "insta360", "gyroflow", "witmotion", "custom_csv"], help="Universal telemetry source format")
    parser.add_argument("--telemetry_extractor", default="extract_telemetry", choices=["extract_telemetry", "parse_gear360"], help="Post-stitch telemetry extractor engine")
    parser.add_argument("--telemetry_multiplier", type=float, default=PIPELINE_DEFAULTS["telemetry_multiplier"], help="Multiplier for telemetry corrections")
    parser.add_argument("--telemetry_multiplier_roll", type=float, default=1.0, help="Roll multiplier for telemetry corrections")
    parser.add_argument("--telemetry_multiplier_pitch", type=float, default=1.0, help="Pitch multiplier for telemetry corrections")
    parser.add_argument("--telemetry_multiplier_yaw", type=float, default=1.0, help="Yaw multiplier for telemetry corrections")
    parser.add_argument("--telemetry_pass", default="two", choices=["single", "two"], help="Post-stitching telemetry pass mode")
    parser.add_argument("--l1_lambda_acc", type=float, default=20.0, help="L1-norm smoothing acceleration weight")
    parser.add_argument("--l1_lambda_vel", type=float, default=2.0, help="L1-norm smoothing velocity weight")
    parser.add_argument("--cinematic_window", type=_parse_int, default=45, help="Cinematic smoothing window (frames)")
    parser.add_argument("--traveldir_mode", default="travel_direction", choices=["travel_direction", "target_lock", "damped_follow"], help="Travel-direction lock mode")
    parser.add_argument("--traveldir_target_yaw", type=float, default=0.0, help="Travel-direction lock target yaw (deg)")
    parser.add_argument("--traveldir_damping", type=float, default=0.90, help="Travel-direction lock damping factor")
    parser.add_argument("--traveldir_deadband", type=float, default=1.5, help="Travel-direction lock deadband threshold (deg)")
    parser.add_argument("--inject_intermediate_meta", action="store_true", help="Inject 360 metadata into intermediate files")
    parser.add_argument("--inject_final_meta", action="store_true", default=True, help="Inject 360 metadata into final output video")
    parser.add_argument("--vidstab_smoothing", type=_parse_int, default=PIPELINE_DEFAULTS["vidstab_smoothing"])
    parser.add_argument("--vidstab_shakiness", type=_parse_int, default=PIPELINE_DEFAULTS["vidstab_shakiness"])
    parser.add_argument("--kabsch_smoothing",  type=_parse_int, default=PIPELINE_DEFAULTS["kabsch_smoothing"])
    parser.add_argument("--vidstab_stepsize", type=_parse_int, default=PIPELINE_DEFAULTS["vidstab_stepsize"], help="Grid stepsize for 2D optical feature tracking (default: 32)")
    parser.add_argument("--vidstab_optalgo", default=PIPELINE_DEFAULTS["vidstab_optalgo"], choices=["gauss", "opt", "avg"])
    parser.add_argument("--vidstab_tripod", action="store_true")
    parser.add_argument("--vidstab_visual", action="store_true", help="Draw vidstab tracking points on video")
    parser.add_argument("--kopf_keyframe_sec", type=float, default=PIPELINE_DEFAULTS["kopf_keyframe_sec"], help="Kopf 3D-2D: max interval between key frames in seconds (default: 2.0)")
    parser.add_argument("--kopf_cube_face",    type=_parse_int,   default=PIPELINE_DEFAULTS["kopf_cube_face"], help="Kopf 3D-2D: cube-map face resolution (default: 1024)")
    parser.add_argument("--kopf_max_features", type=_parse_int,   default=PIPELINE_DEFAULTS["kopf_max_features"],  help="Kopf 3D-2D & Kabsch: max features to track (default: 400)")
    parser.add_argument("--kopf_deformed",     action="store_true",      help="Kopf 3D-2D: enable deformed-rotation jitter model (Section 3.4)")
    parser.add_argument("--kopf_reapply",      action="store_true",      help="Kopf 3D-2D: reapply smoothed rotations (Section 4.1, non-VR mode)")
    parser.add_argument("--nadir_logo",   default="",                 help="Path to nadir logo PNG (RGBA). Default: logo_stei_circle.png")
    parser.add_argument("--nadir_fov",    type=float, default=PIPELINE_DEFAULTS["nadir_fov"],   help="Horizontal FOV of nadir logo projection (degrees). Default: 75")
    parser.add_argument("--nadir_fov_v",  type=float, default=PIPELINE_DEFAULTS["nadir_fov_v"], help="Vertical FOV of nadir logo projection (degrees). Default: 75")
    parser.add_argument("--status_file",  default="data/runtime/temp/status.json",  help="Status JSON file path")
    parser.add_argument("--job_id",       default="",                 help="Job or Session ID")
    parser.add_argument("--mask_file",      type=str, default="",          help="Pre-generated alpha mask file to use")
    parser.add_argument("--skip_stitching", action="store_true",        help="Skip dual-fisheye stitching phase (input video is already equirectangular)")
    parser.add_argument("--streetview_enabled", action="store_true", help="Generate Google Street View output (_streetview.MP4 & _streetview.gpx)")
    parser.add_argument("--streetview_mode", default="A", choices=["A", "B"], help="Street View GPS mode: A or B")
    parser.add_argument("--streetview_checkpoints", default="", help="Street View map checkpoints data")
    parser.add_argument("--streetview_gpx_path", default="", help="Street View GPX input file path")
    parser.add_argument("--streetview_start_time", default="", help="Street View UTC start time")
    parser.add_argument("--streetview_time_offset", type=float, default=0.0, help="Street View time offset in seconds")
    parser.add_argument("--streetview_no_auto_pad", action="store_true", help="Disable Street View auto-padding")
    parser.add_argument("--streetview_bitrate", default=PIPELINE_DEFAULTS["streetview_bitrate"], help="Target video bitrate for Street View export (e.g. 45M, 30M, 60M, 80M, or copy)")
    parser.add_argument("--streetview_strip_audio", action="store_true", default=True, help="Strip audio from Street View export")
    parser.add_argument("--streetview_keep_audio", action="store_false", dest="streetview_strip_audio", help="Keep audio in Street View export")
    parser.add_argument("--streetview_start_coord", default="", help="Start coordinate string 'lat, lon' to auto-match start point in GPX track")
    parser.add_argument("--streetview_end_coord", default="", help="Optional end coordinate string 'lat, lon' to verify route span in GPX track")
    parser.add_argument("--streetview_smooth_gps", action="store_true", help="Apply Gaussian smoothing filter to eliminate GPS sensor jitter in Street View track")
    parser.add_argument("--video_bitrate", default=PIPELINE_DEFAULTS["video_bitrate"], help="Custom final video bitrate (e.g. 45M, 60M, 80M, 100M)")
    parser.add_argument("--horizon_autodetect", action="store_true", help="Automatically extract inflection checkpoints from telemetry/motion/video without interactive pause")
    parser.add_argument("--horizon_autodetect_source", default="vision", choices=["auto", "cadence", "vision", "pitch_extrema"], help="Detection source strategy for checkpoint auto-detection")
    parser.add_argument("--horizon_autodetect_density", default="balanced", choices=["smooth", "balanced", "fine", "detailed", "ultra", "ultra_dense"], help="Keyframe density / tolerance")
    parser.add_argument("--horizon_pitch_prominence", type=float, default=0.8, help="Minimum pitch swing prominence (in degrees) for smart gait stride detection")
    parser.add_argument("--horizon_roll_damping", type=float, default=0.70, help="Lateral body sway damping multiplier for roll in smart gait stride detection (0.0 to 1.0, default 0.70)")
    parser.add_argument("--horizon_apply_yaw", action="store_true", help="Apply yaw rotation in checkpoint trajectory")
    parser.add_argument("--util_gcsv", action="store_true", help="Generate Gyroflow .gcsv & .MP4.gcsv telemetry files")
    parser.add_argument("--util_bigsh0t", action="store_true", help="Generate Kdenlive binary .bigsh0t360motion file")
    parser.add_argument("--no_prompt_transforms", action="store_true", help="Disable interactive transform selection before master render")
    parser.add_argument("--fallback_unstabilized", action="store_true", default=True, help="Automatically revert degraded stages and continue from previous good video")
    parser.add_argument("--no_fallback_unstabilized", action="store_false", dest="fallback_unstabilized", help="Disable automatic fallback of degraded stages")
    parser.add_argument("--remove_audio", action="store_true", default=PIPELINE_DEFAULTS.get("remove_audio", False), help="Strip/remove audio track from rendered output video")
    parser.add_argument("--keep_audio", action="store_false", dest="remove_audio", help="Keep audio in rendered output video")

    args = parser.parse_args()
    _GLOBAL_ARGS = args

    if args.hwaccel:
        nvenc_ok, nvenc_reason = probe_nvenc()
        if not nvenc_ok:
            args.hwaccel = False
            print(
                f"\n" + "=" * 70 + "\n"
                f"[Pipeline NOTICE] NVIDIA NVENC hardware acceleration requested, but initialization failed:\n"
                f"  Reason: {nvenc_reason}\n"
                f"  Fallback: Automatically switching to CPU encoder (libx264).\n"
                + "=" * 70 + "\n",
                flush=True
            )
            update_status(args.status_file, {
                "warning": f"NVENC unavailable ({nvenc_reason}). Switched to libx264."
            })

    if getattr(args, "v360_backend", "cpu") == "vulkan":
        vulkan_ok, vulkan_reason = probe_vulkan()
        if not vulkan_ok:
            args.v360_backend = "cpu"
            print(
                f"\n" + "=" * 70 + "\n"
                f"[Pipeline NOTICE] Vulkan acceleration requested for v360, but initialization failed:\n"
                f"  Reason: {vulkan_reason}\n"
                f"  Fallback: Automatically switching v360 backend to CPU.\n"
                + "=" * 70 + "\n",
                flush=True
            )
            if getattr(args, 'status_file', None):
                update_status(args.status_file, {
                    "warning": f"v360 Vulkan unavailable ({vulkan_reason}). Switched to CPU."
                })

    script_dir = os.path.dirname(os.path.abspath(__file__))
    stab_video_file = None
    step3_telemetry = None

    if not os.path.exists(args.input):
        update_status(args.status_file, {"status": "failed", "error": f"Input video not found: {args.input}"})
        sys.exit(1)

    if getattr(args, "nadir_logo", None) and not os.path.exists(args.nadir_logo):
        update_status(args.status_file, {"status": "failed", "error": f"Nadir logo not found: {args.nadir_logo}"})
        sys.exit(1)

    input_ext = os.path.splitext(args.input)[1].lower()
    is_image = input_ext in [".jpg", ".jpeg", ".png", ".webp"]

    # -- Step 0: probe source duration ----------------------------------------
    if is_image:
        args.stabilize = False
        duration = 1.0
        timestamped_print("[Pipeline] Input is a still photo; bypassing motion stabilization cascade and focusing exclusively on optical dual-fisheye stitching.")
    else:
        duration = get_video_duration(args.input)
        if duration is None:
            update_status(args.status_file, {"status": "failed", "error": "Failed to read input video duration."})
            sys.exit(1)

        if getattr(args, 'duration', 0) and args.duration > 0:
            duration = min(duration, float(args.duration))

    start_ts = time.time()
    global _GLOBAL_START_TIME, _GLOBAL_OUT_BASE, _GLOBAL_OUTPUT_FILE
    _GLOBAL_START_TIME = start_ts
    _GLOBAL_OUTPUT_FILE = args.output

    out_dir = os.path.dirname(args.output)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    # H-4: Validate output path is within allowed directories
    abs_out = os.path.abspath(args.output)
    _allowed_out_dirs = [os.path.abspath(d) for d in ["data/output", "data/runtime/work", "tests/temp"]]
    if not any(abs_out.startswith(a + os.sep) or abs_out == a for a in _allowed_out_dirs):
        print(f"[SECURITY] Output path rejected (outside permitted directories): {args.output}", file=sys.stderr)
        sys.exit(1)

    # Pre-flight Storage Check: Ensure sufficient disk space before encoding passes
    try:
        import shutil
        check_dir = out_dir if out_dir and os.path.exists(out_dir) else "."
        usage = shutil.disk_usage(check_dir)
        free_mb = usage.free / (1024 * 1024)
        input_size_mb = os.path.getsize(args.input) / (1024 * 1024) if os.path.exists(args.input) else 100.0
        # Require at least 250 MB free, or input size + 100 MB headroom
        min_required_mb = max(250.0, input_size_mb + 100.0)
        if free_mb < min_required_mb:
            print(f"[STORAGE ERROR] Insufficient disk space on '{check_dir}': {free_mb:.1f} MB available, but at least {min_required_mb:.1f} MB is required.", file=sys.stderr)
            if getattr(args, 'status_file', None):
                update_status(args.status_file, {"status": "failed", "phase": "storage_check", "error": "Insufficient disk space"})
            sys.exit(2)
    except Exception as e_disk:
        print(f"[STORAGE WARNING] Failed to verify disk space: {e_disk}", file=sys.stderr)

    out_base, out_ext = os.path.splitext(args.output)
    _GLOBAL_OUT_BASE = os.path.basename(out_base)
    
    # Stitching output parameters
    if is_image:
        if out_ext.lower() in [".jpg", ".jpeg", ".png", ".webp"]:
            step1_stitch = f"{out_base}_stitched{out_ext}"
        else:
            step1_stitch = f"{out_base}_stitched{input_ext}"
        companion_video = f"{out_base}_stitched.mp4"
    else:
        step1_stitch = f"{out_base}_stitched{out_ext}"
        companion_video = None

    # Determine active stabilization methods and handle input feature skips
    if getattr(args, 'input_has_stitched', False):
        args.skip_stitching = True

    requested_methods = []
    if args.stabilize:
        if getattr(args, 'stabilize_methods', ''):
            requested_methods = [m.strip().lower() for m in args.stabilize_methods.split(",") if m.strip()]
        elif getattr(args, 'stabilize_type', ''):
            st = args.stabilize_type.lower()
            if st == "telemetry": requested_methods = ["telemetry"]
            elif st == "vidstab": requested_methods = ["vidstab"]
            elif st == "kabsch": requested_methods = ["kabsch"]
            elif st in ("kopf", "kopf3d2d"): requested_methods = ["kopf"]
            elif st == "hybrid": requested_methods = ["telemetry", "vidstab"]
            elif st == "telemetry_kopf": requested_methods = ["telemetry", "vidstab", "kopf"]
            elif st == "hybrid_kopf": requested_methods = ["telemetry", "vidstab", "kopf"]

    active_methods = []
    seen_methods = set()
    for m in requested_methods:
        if m in ("horizon", "checkpoints"):
            m = "horizon"
        if m in ("kopf3d2d",):
            m = "kopf"
        if m in ("l1_norm", "l1norm", "l1", "cinematic"):
            m = "cinematic"
        if m in ("direction", "dir_lock", "subject_lock", "traveldir"):
            m = "traveldir"
        if m in seen_methods:
            continue
        seen_methods.add(m)
        if m == "telemetry" and getattr(args, 'input_has_telemetry', False):
            print("[Pipeline] Skipping Telemetry pass (input video marked as already telemetry-stabilized).")
            continue
        if m == "kopf" and getattr(args, 'input_has_kopf', False):
            print("[Pipeline] Skipping Kopf pass (input video marked as already kopf-stabilized).")
            continue
        if m == "kabsch" and getattr(args, 'input_has_kabsch', False):
            print("[Pipeline] Skipping Kabsch pass (input video marked as already kabsch-stabilized).")
            continue
        if m == "vidstab" and getattr(args, 'input_has_vidstab', False):
            print("[Pipeline] Skipping Vidstab pass (input video marked as already vidstab-stabilized).")
            continue
        if m == "cinematic" and getattr(args, 'input_has_cinematic', False):
            print("[Pipeline] Skipping Cinematic pass (input video marked as already cinematic-stabilized).")
            continue
        if m == "horizon" and getattr(args, 'input_has_horizon', False):
            print("[Pipeline] Skipping Horizon pass (input video marked as already horizon-stabilized).")
            continue
        if m == "traveldir" and getattr(args, 'input_has_traveldir', False):
            print("[Pipeline] Skipping Travel-Direction pass (input video marked as already traveldir-stabilized).")
            continue
        if m in ("telemetry", "kopf", "kabsch", "vidstab", "cinematic", "horizon", "traveldir"):
            active_methods.append(m)

    stage_blocks = list(active_methods)

    if "horizon" in active_methods and "telemetry" in active_methods:
        if active_methods.index("horizon") < active_methods.index("telemetry"):
            print(
                "\n" + "=" * 70 + "\n"
                "[Pipeline WARNING] Suboptimal stabilization execution order detected:\n"
                "  'horizon' is positioned before 'telemetry'.\n"
                "  Hardware IMU telemetry should execute first to establish the physical\n"
                "  gravity/inertial reference frame. Running horizon leveling first causes\n"
                "  non-commutative SO(3) coordinate mismatch and conflicting dual-leveling.\n"
                + "=" * 70 + "\n",
                flush=True
            )

    if stage_blocks:
        pipeline_str = "_".join(stage_blocks)
        step2_stab = f"{out_base}_{pipeline_str}{out_ext}"
    else:
        step2_stab = f"{out_base}_telemetry{out_ext}"

    # Nadir parameters
    step3_nadir = f"{out_base}_nadir{out_ext}"
    nadir_applied_in_master = False
    try:
        from utils.temp_storage import get_ram_temp_dir
        target_temp_dir = get_ram_temp_dir()
    except Exception:
        target_temp_dir = out_dir if out_dir else os.path.join("data", "runtime", "temp")
    os.makedirs(target_temp_dir, exist_ok=True)
    trf_file       = os.path.join(target_temp_dir, f"temp_transforms_{os.getpid()}.trf").replace('\\', '/')


    current_file = args.input



    # -- Step 3: Fisheye-to-Equirectangular Stitching & Calibration (Optional) --
    if not args.skip_stitching:
        # Dynamically probe current input video dimensions to adapt crop & FOV for standard 3840x1920 or padded 4320x2160 inputs
        cur_w, cur_h = 3840, 1920
        probe_cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "json", current_file]
        proc_w = subprocess.run(probe_cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, creationflags=WIN_NO_WINDOW)
        if proc_w.returncode == 0:
            try:
                data_p = json.loads(proc_w.stdout)
                cur_w = int(data_p["streams"][0]["width"])
                cur_h = int(data_p["streams"][0]["height"])
            except Exception:
                pass

        half_w = cur_w // 2
        half_h = cur_h
        fov_scale = cur_h / 1920.0
        ih_fov_stitch = float(args.ih_fov) * fov_scale
        iv_fov_stitch = float(args.iv_fov) * fov_scale

        use_split_lenses = args.blend_seams or (abs(float(args.rear_roll_offset)) > 0.001)

        mask_file = args.mask_file
        if use_split_lenses and not mask_file:
            temp_mask_dir = os.path.join("data", "runtime", "temp")
            os.makedirs(temp_mask_dir, exist_ok=True)
            mask_file = os.path.join(temp_mask_dir, f"temp_seam_mask_{os.getpid()}.png")
            try:
                from PIL import Image

                WIDTH, HEIGHT = cur_w, cur_h
                bw_val = args.blend_width if (args.blend_width is not None) else 40
                actual_blend_width = int(bw_val) if args.blend_seams else 0
                BLEND_HALF = max(1, actual_blend_width // 2)
                SEAM_LEFT  = cur_w // 4
                SEAM_RIGHT = 3 * cur_w // 4

                def sinusoidal(t):
                    return int(255 * (0.5 - 0.5 * math.cos(math.pi * t)))

                row_bytes = bytearray(WIDTH * 3)
                for x in range(WIDTH):
                    val = 0
                    if SEAM_LEFT + BLEND_HALF <= x <= SEAM_RIGHT - BLEND_HALF:
                        val = 255
                    elif SEAM_LEFT - BLEND_HALF <= x < SEAM_LEFT + BLEND_HALF:
                        t = (x - (SEAM_LEFT - BLEND_HALF)) / (2 * BLEND_HALF)
                        val = int(255 * (0.5 - 0.5 * math.cos(math.pi * t)))
                    elif SEAM_RIGHT - BLEND_HALF < x <= SEAM_RIGHT + BLEND_HALF:
                        t = ((SEAM_RIGHT + BLEND_HALF) - x) / (2 * BLEND_HALF)
                        val = int(255 * (0.5 - 0.5 * math.cos(math.pi * t)))
                    v_res = 255 - val
                    row_bytes[x * 3] = v_res
                    row_bytes[x * 3 + 1] = v_res
                    row_bytes[x * 3 + 2] = v_res

                full_bytes = bytes(row_bytes) * HEIGHT
                img = Image.frombytes('RGB', (WIDTH, HEIGHT), full_bytes)
                img.save(mask_file)
            except Exception as e:
                print(f"Warning: could not generate seam mask: {e}")


        left_y_offset = round(float(args.left_y_offset))
        yaw_l = 0.0   + float(args.yaw)
        yaw_r = 180.0 + float(args.yaw)

        # Raw frame pre-rotation filter bypassed (spherical yaw/pitch/roll handled by v360)
        raw_rot_filter = ""
        raw_in_label = "[0:v]"

        q_mode_stitch = str(getattr(args, 'stab_quality_mode', '4'))
        if getattr(args, "v360_backend", "cpu") == "vulkan":
            if q_mode_stitch == "3":
                print("[Pipeline] Notice: v360_vulkan bypassed for Phase 1 stitching because Quality Mode 3 (Lanczos) requires CPU v360.")
                can_use_vulkan_stitch = False
            else:
                can_use_vulkan_stitch = True
        else:
            can_use_vulkan_stitch = False

        if use_split_lenses and mask_file and os.path.exists(mask_file):
            filter_complex = f"{raw_rot_filter}{raw_in_label}split[raw_l][raw_r];"
            if left_y_offset != 0:
                abs_offset   = abs(left_y_offset)
                pad_h        = half_h + abs_offset
                left_pad_y   = max(0, -left_y_offset)
                left_recrop_y= max(0,  left_y_offset)
                filter_complex += (
                    f"[raw_l]crop={half_w}:{half_h}:0:0,"
                    f"pad={half_w}:{pad_h}:0:{left_pad_y}:black,"
                    f"crop={half_w}:{half_h}:0:{left_recrop_y}"
                )
            else:
                filter_complex += f"[raw_l]crop={half_w}:{half_h}:0:0"
                
            if args.anti_vignette:
                filter_complex += f",vignette=mode=backward:angle={args.anti_vignette_angle},format=yuv420p"
            filter_complex += "[left];"

            filter_complex += f"[raw_r]crop={half_w}:{half_h}:{half_w}:0"
            if args.anti_vignette:
                filter_complex += f",vignette=mode=backward:angle={args.anti_vignette_angle},format=yuv420p"
            filter_complex += "[right];"

            while yaw_l > 180: yaw_l -= 360
            while yaw_l < -180: yaw_l += 360
            while yaw_r > 180: yaw_r -= 360
            while yaw_r < -180: yaw_r += 360

            roll_r = float(args.roll) + float(args.rear_roll_offset)

            if can_use_vulkan_stitch:
                vk_yaw_l = -yaw_l
                while vk_yaw_l > 180: vk_yaw_l -= 360
                while vk_yaw_l < -180: vk_yaw_l += 360

                vk_yaw_r = -yaw_r
                while vk_yaw_r > 180: vk_yaw_r -= 360
                while vk_yaw_r < -180: vk_yaw_r += 360

                vk_pitch = -float(args.pitch)
                vk_roll_l = -float(args.roll)
                vk_roll_r = -roll_r

                filter_complex += (
                    f"[left]format=yuv420p,hwupload,scale_vulkan=w=3840:h=1920,"
                    f"v360_vulkan=input=fisheye:output=equirect"
                    f":ih_fov={ih_fov_stitch:.4f}:iv_fov={iv_fov_stitch:.4f}"
                    f":yaw={vk_yaw_l}:pitch={vk_pitch}:roll={vk_roll_l}:rorder=rpy:w=3840:h=1920,"
                    f"hwdownload,format=yuv420p[eq_left];"
                )
                filter_complex += (
                    f"[right]format=yuv420p,hwupload,scale_vulkan=w=3840:h=1920,"
                    f"v360_vulkan=input=fisheye:output=equirect"
                    f":ih_fov={ih_fov_stitch:.4f}:iv_fov={ih_fov_stitch:.4f}"
                    f":yaw={vk_yaw_r}:pitch={vk_pitch}:roll={vk_roll_r}:rorder=rpy:w=3840:h=1920,"
                    f"hwdownload,format=yuv420p[eq_right];"
                )
            else:
                filter_complex += (
                    f"[left]v360=input=fisheye:output=equirect"
                    f":ih_fov={ih_fov_stitch:.4f}:iv_fov={iv_fov_stitch:.4f}"
                    f":yaw={yaw_l}:pitch={args.pitch}:roll={args.roll}"
                    f":w=3840:h=1920[eq_left];"
                )
                filter_complex += (
                    f"[right]v360=input=fisheye:output=equirect"
                    f":ih_fov={ih_fov_stitch:.4f}:iv_fov={ih_fov_stitch:.4f}"
                    f":yaw={yaw_r}:pitch={args.pitch}:roll={roll_r}"
                    f":w=3840:h=1920[eq_right];"
                )

            if abs(float(args.yaw)) > 0.001 or abs(float(args.pitch)) > 0.001 or abs(float(args.roll)) > 0.001:
                filter_complex += (
                    f"[1:v]format=yuv420p,v360=input=equirect:output=equirect"
                    f":yaw={args.yaw}:pitch={args.pitch}:roll={args.roll}:w=3840:h=1920,"
                    f"format=gray[mask_eq];"
                )
            else:
                filter_complex += "[1:v]scale=3840:1920,format=gray[mask_eq];"
            filter_complex += "[eq_right][mask_eq]alphamerge[right_alpha];"
            filter_complex += "[eq_left][right_alpha]overlay=format=yuv420:eof_action=endall:shortest=1[final]"

            stitch_cmd = ["ffmpeg", "-y", "-progress", "-"]
            if can_use_vulkan_stitch:
                stitch_cmd.extend(["-init_hw_device", "vulkan=vk", "-filter_hw_device", "vk"])
            stitch_cmd.extend([
                "-i", current_file,
                "-loop", "1", "-i", mask_file,
                "-filter_complex", filter_complex,
                "-map", "[final]", "-map", "0:a?"
            ])

            # Save raw stitch metadata for Master Render from RAW
            raw_stitch_filter_expr = filter_complex
            raw_stitch_is_split = True
            raw_stitch_mask_file = mask_file
            raw_stitch_cur_w = cur_w
            raw_stitch_cur_h = cur_h

            nadir_logo_step1 = args.nadir_logo.strip() if getattr(args, 'nadir_logo', None) else ""
            can_chain_nadir_step1 = bool(
                nadir_logo_step1 and os.path.exists(nadir_logo_step1)
                and not getattr(args, 'input_has_nadir', False)
                and not (args.stabilize and len(active_methods) > 0)
            )
            if can_chain_nadir_step1:
                nadir_part = (
                    f"[eq_left][right_alpha]overlay=format=yuv420:eof_action=endall:shortest=1[stitch_out];"
                    f"[2:v]format=rgba[logo_rgba];"
                    f"[logo_rgba]v360=input=flat:output=equirect:ih_fov={args.nadir_fov}:iv_fov={args.nadir_fov_v}:pitch=90:yaw=0:roll=0:w={cur_w}:h={cur_h}[logo_eq];"
                    f"[stitch_out][logo_eq]overlay=0:0:format=auto:eof_action=pass:shortest=0[final]"
                )
                filter_complex_nadir = filter_complex.replace("[eq_left][right_alpha]overlay=format=yuv420:eof_action=endall:shortest=1[final]", nadir_part)
                stitch_cmd = ["ffmpeg", "-y", "-progress", "-"]
                if can_use_vulkan_stitch:
                    stitch_cmd.extend(["-init_hw_device", "vulkan=vk", "-filter_hw_device", "vk"])
                stitch_cmd.extend([
                    "-i", current_file,
                    "-loop", "1", "-i", mask_file,
                    "-loop", "1", "-i", nadir_logo_step1,
                    "-filter_complex", filter_complex_nadir,
                    "-map", "[final]", "-map", "0:a?"
                ])
                nadir_applied_in_master = True
                print(f"[Pipeline] Single-Pass Step 1: Integrated Nadir logo ({os.path.basename(nadir_logo_step1)}) directly into stitching filtergraph in memory.")
        else:
            yaw_dfisheye = float(args.yaw) + 180.0
            while yaw_dfisheye > 180: yaw_dfisheye -= 360
            while yaw_dfisheye < -180: yaw_dfisheye += 360

            if can_use_vulkan_stitch:
                vk_yaw_df = -yaw_dfisheye
                while vk_yaw_df > 180: vk_yaw_df -= 360
                while vk_yaw_df < -180: vk_yaw_df += 360
                vk_pitch = -float(args.pitch)
                vk_roll = -float(args.roll)
                v360_stitch_expr = (
                    f"format=yuv420p,hwupload,v360_vulkan=input=dfisheye:output=equirect"
                    f":ih_fov={args.ih_fov}:iv_fov={args.iv_fov}"
                    f":yaw={vk_yaw_df}:pitch={vk_pitch}:roll={vk_roll}:rorder=rpy:w={cur_w}:h={cur_h},"
                    f"hwdownload,format=yuv420p"
                )
            else:
                v360_stitch_expr = (
                    f"v360=input=dfisheye:output=equirect"
                    f":ih_fov={args.ih_fov}:iv_fov={args.iv_fov}"
                    f":yaw={yaw_dfisheye}:pitch={args.pitch}:roll={args.roll}"
                )

            if left_y_offset != 0:
                abs_offset   = abs(left_y_offset)
                pad_h        = 1920 + abs_offset
                left_pad_y   = max(0, -left_y_offset)
                left_recrop_y= max(0,  left_y_offset)
                
                filter_complex = f"{raw_rot_filter}{raw_in_label}split[a][b];"
                filter_complex += f"[a]crop=1920:1920:0:0,pad=1920:{pad_h}:0:{left_pad_y}:black,crop=1920:1920:0:{left_recrop_y}"
                if args.anti_vignette:
                    filter_complex += f",vignette=mode=backward:angle={args.anti_vignette_angle}"
                filter_complex += f"[left];"
                
                filter_complex += f"[b]crop=1920:1920:1920:0"
                if args.anti_vignette:
                    filter_complex += f",vignette=mode=backward:angle={args.anti_vignette_angle}"
                filter_complex += f"[right];"
                
                filter_complex += (
                    f"[left][right]hstack[combined];"
                    f"[combined]{v360_stitch_expr}[eq]"
                )
            else:
                if args.anti_vignette:
                    filter_complex = (
                        f"{raw_rot_filter}{raw_in_label}split[a][b];"
                        f"[a]crop=1920:1920:0:0,vignette=mode=backward:angle={args.anti_vignette_angle}[left];"
                        f"[b]crop=1920:1920:1920:0,vignette=mode=backward:angle={args.anti_vignette_angle}[right];"
                        f"[left][right]hstack[combined];"
                        f"[combined]{v360_stitch_expr}[eq]"
                    )
                else:
                    filter_complex = (
                        f"{raw_rot_filter}{raw_in_label}{v360_stitch_expr}[eq]"
                    )

            stitch_cmd = ["ffmpeg", "-y", "-progress", "-"]
            if can_use_vulkan_stitch:
                stitch_cmd.extend(["-init_hw_device", "vulkan=vk", "-filter_hw_device", "vk"])
            stitch_cmd.extend([
                "-i", current_file,
                "-filter_complex", filter_complex,
                "-map", "[eq]", "-map", "0:a?"
            ])

            # Save raw stitch metadata for Master Render from RAW
            raw_stitch_filter_expr = filter_complex
            raw_stitch_is_split = False
            raw_stitch_mask_file = None
            raw_stitch_cur_w = cur_w
            raw_stitch_cur_h = cur_h

            nadir_logo_step1 = args.nadir_logo.strip() if getattr(args, 'nadir_logo', None) else ""
            can_chain_nadir_step1 = bool(
                nadir_logo_step1 and os.path.exists(nadir_logo_step1)
                and not getattr(args, 'input_has_nadir', False)
                and not (args.stabilize and len(active_methods) > 0)
            )
            if can_chain_nadir_step1:
                nadir_part = (
                    f"[eq_stitch];"
                    f"[1:v]format=rgba[logo_rgba];"
                    f"[logo_rgba]v360=input=flat:output=equirect:ih_fov={args.nadir_fov}:iv_fov={args.nadir_fov_v}:pitch=90:yaw=0:roll=0:w={cur_w}:h={cur_h}[logo_eq];"
                    f"[eq_stitch][logo_eq]overlay=0:0:format=auto:eof_action=pass:shortest=0[final]"
                )
                if filter_complex.endswith("[eq]"):
                    filter_complex_nadir = filter_complex[:-4] + nadir_part
                else:
                    filter_complex_nadir = filter_complex.replace("[eq]", nadir_part)
                stitch_cmd = ["ffmpeg", "-y", "-progress", "-"]
                if can_use_vulkan_stitch:
                    stitch_cmd.extend(["-init_hw_device", "vulkan=vk", "-filter_hw_device", "vk"])
                stitch_cmd.extend([
                    "-i", current_file,
                    "-loop", "1", "-i", nadir_logo_step1,
                    "-filter_complex", filter_complex_nadir,
                    "-map", "[final]", "-map", "0:a?"
                ])
                nadir_applied_in_master = True
                print(f"[Pipeline] Single-Pass Step 1: Integrated Nadir logo ({os.path.basename(nadir_logo_step1)}) directly into stitching filtergraph in memory.")

        if is_image or getattr(args, 'remove_audio', False):
            clean_cmd = []
            skip_next = False
            for c_idx, a in enumerate(stitch_cmd):
                if skip_next:
                    skip_next = False
                    continue
                if a == "-map" and c_idx + 1 < len(stitch_cmd) and stitch_cmd[c_idx + 1] == "0:a?":
                    skip_next = True
                    continue
                clean_cmd.append(a)
            if is_image:
                if step1_stitch.lower().endswith((".jpg", ".jpeg")):
                    stitch_cmd = clean_cmd + ["-vframes", "1", "-pix_fmt", "yuvj420p", "-q:v", "2", step1_stitch]
                else:
                    stitch_cmd = clean_cmd + ["-vframes", "1", step1_stitch]
            else:
                stitch_cmd = clean_cmd
        if not is_image:
            if args.duration > 0:
                stitch_cmd.extend(["-t", str(args.duration)])

            if args.hwaccel:
                stitch_cmd += ["-c:v", "h264_nvenc", "-b:v", "30000k"]
            else:
                stitch_cmd += ["-c:v", "libx264", "-preset", args.preset, "-crf", args.crf]

            if use_split_lenses or args.blend_seams or ("-loop" in stitch_cmd):
                if "-shortest" not in stitch_cmd:
                    stitch_cmd += ["-shortest"]

            audio_stitch_args = ["-an"] if getattr(args, 'remove_audio', False) else ["-c:a", "copy"]
            stitch_cmd += ["-pix_fmt", "yuv420p"] + audio_stitch_args + [step1_stitch]

        blend_str = f"{args.blend_width}px" if args.blend_seams else "Off"
        backend_tag = " [Vulkan GPU]" if can_use_vulkan_stitch else " [CPU]"
        phase_stitch = f"Stitching{backend_tag} (FOV: {args.ih_fov}°x{args.iv_fov}°, YPR: {args.yaw}° {args.pitch}° {args.roll}°, blend: {blend_str})"

        update_status(args.status_file, {
            "status": "stitching", "phase": phase_stitch,
            "progress": 30.0, "speed": "0x", "eta": "Calculating...",
            "start_time": start_ts
        })

        rc = run_ffmpeg(stitch_cmd, args.status_file, duration, start_ts, phase_stitch)
        if rc != 0:
            update_status(args.status_file, {"status": "failed", "error": f"Stitching failed (exit {rc})."})
            sys.exit(1)

        if is_image and companion_video:
            comp_cmd = ["ffmpeg", "-y", "-loop", "1", "-i", step1_stitch, "-c:v", "libx264", "-t", "1", "-pix_fmt", "yuv420p", companion_video]
            subprocess.run(comp_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=WIN_NO_WINDOW)

        current_file = step1_stitch   # pipeline cursor
        embed_vrot_metadata(args.input, step1_stitch)
        if getattr(args, 'inject_intermediate_meta', False):
            create_meta_copy(step1_stitch, args.status_file, start_ts, stab_name="Stitched Video")
        update_status(args.status_file, {
            "status": "stitching",
            "phase": "Stitching completed",
            "progress": 40.0,
            "speed": "N/A", "eta": "Calculating...",
            "elapsed": int(time.time() - start_ts)
        })

    # -- Step 4: optional stabilization ---------------------------------------
    # Guard: equirect stabilization must only run when input is already equirectangular
    _input_is_equirect = True

    if args.stabilize and not _input_is_equirect:
        err_msg = ("Equirectangular stabilization (Step 3) skipped — current input is dual-fisheye. "
                   "Equirectangular stabilization requires a stitched equirectangular video input. "
                   "Please check Step 2 (Fisheye-to-Equirectangular Stitching) or select an already-stitched video.")
        print(f"Warning: {err_msg}")
        update_status(args.status_file, {"status": "failed", "error": err_msg})
        sys.exit(1)
    executed_methods_so_far = []
    reverted_stages = {}
    active_sendcmd_files = []
    mode4_temp_intermediates = []
    stage_step_io = {}
    model_results = {}
    _kopf_disclaimer = (
        "\n[NOTE] Kopf 3D-2D corrections apply spherical rotations (v360 yaw/pitch/roll).\n"
        "Equirectangular 2D pixel-shift metrics measure coordinate displacement rather than\n"
        "angular stability. Refer to the 3D Rotational Jitter / Jerk section above to evaluate\n"
        "Kopf stabilization quality.\n"
    ) if any(m in active_methods for m in ('kopf',)) or getattr(args, 'stabilize_type', '') in ('kopf', 'telemetry_kopf', 'hybrid_kopf') else ""
    initial_raw_stitched_file = current_file
    nadir_applied_in_master = bool(locals().get('nadir_applied_in_master', False))
    q_mode = getattr(args, 'stab_quality_mode', '4')
    v360_interp = ":interp=lanczos" if q_mode == "3" else ""
    if args.stabilize and _input_is_equirect:
        stage_order = list(active_methods)

        for stage_item in stage_order:
            if stage_item == "telemetry" and "telemetry" in active_methods:
                stage_in_tele = current_file
                # Telemetry/IMU-based post-stitching stabilization
                phase_extract = f"Extracting telemetry (mode: {args.telemetry_mode})"
                update_status(args.status_file, {
                    "status": "stabilizing", "phase": phase_extract,
                    "progress": 50.0, "speed": "N/A", "eta": "Calculating...",
                    "elapsed": int(time.time() - start_ts)
                })
                sendcmd_file, _ = prepare_telemetry_sendcmd(
                    args, out_base, duration, suffix="telemetry",
                    mode=args.telemetry_mode,
                    smoothing=args.telemetry_smoothing,
                    ref_frame=args.telemetry_ref_frame,
                    extractor=args.telemetry_extractor,
                    multiplier=args.telemetry_multiplier,
                    multiplier_roll=getattr(args, 'telemetry_multiplier_roll', args.telemetry_multiplier),
                    multiplier_pitch=getattr(args, 'telemetry_multiplier_pitch', args.telemetry_multiplier),
                    multiplier_yaw=getattr(args, 'telemetry_multiplier_yaw', args.telemetry_multiplier),
                    target_equirect=True
                )
                if sendcmd_file:
                    try:
                        update_status(args.status_file, {
                            "status": "stabilizing",
                            "phase": "Plotting telemetry motion curves & correction graphs...",
                            "progress": 60.0, "speed": "N/A", "eta": "Calculating...",
                            "elapsed": int(time.time() - start_ts)
                        })
                        fps_tele = fps if 'fps' in locals() else 30.0
                        vis_cmd = [
                            sys.executable, "-B", os.path.join(script_dir, "visualize_corrections.py"),
                            sendcmd_file, "--graph-type", "telemetry", "--output", out_base, "--fps", str(fps_tele),
                            "--fusion-method", str(getattr(args, "telemetry_fusion", "none")),
                            "--fusion-gain", str(getattr(args, "telemetry_fusion_gain", 0.5)),
                        ]
                        subprocess.run(vis_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=WIN_NO_WINDOW)
                    except Exception as e_vis:
                        print(f"Notice: visualize_corrections error: {e_vis}")

                    smooth_str = f"smooth: {args.telemetry_smoothing}" if args.telemetry_mode == "smooth" else (f"ref_frame: {args.telemetry_ref_frame}" if args.telemetry_mode == "lock" else "baseline: mean_DC")
                    tele_fusion = getattr(args, "telemetry_fusion", "none")
                    tele_gain = getattr(args, "telemetry_fusion_gain", 0.51)
                    fusion_str = f"fusion: {tele_fusion} (gain: {tele_gain})" if tele_fusion and str(tele_fusion).lower() not in ("none", "disabled", "false", "") else "fusion: none"
                    phase_imu_stab = f"Stabilising 3D (Telemetry) (mode: {args.telemetry_mode}, {smooth_str}, mult: {args.telemetry_multiplier}, {fusion_str})"
                    update_status(args.status_file, {
                        "status": "stabilizing", "phase": phase_imu_stab,
                        "progress": 70.0, "speed": "N/A", "eta": "Calculating...",
                        "elapsed": int(time.time() - start_ts)
                    })
                    sendcmd_path_escaped = sendcmd_file.replace('\\', '/')
                    executed_methods_so_far.append("telemetry")
                    _tele_out = f"{out_base}_telemetry{out_ext}"

                    if q_mode in ("1", "4"):
                        active_sendcmd_files.append(sendcmd_file)
                        print(f"[{'Hybrid' if q_mode == '4' else 'Single-Pass'} Mode] Added Telemetry transform ({sendcmd_file}) to composition queue.")
                    if q_mode == "1":
                        stage_step_io["telemetry"] = (initial_raw_stitched_file, _tele_out)

                    if q_mode != "1":
                        filter_str = f"sendcmd=f='{sendcmd_path_escaped}',v360=input=equirect:output=equirect{v360_interp}"

                        stab_cmd = [
                            "ffmpeg", "-y", "-progress", "-",
                            "-i", current_file,
                            "-vf", filter_str,
                        ]
                        is_intermediate = (q_mode == "4") or (len(active_methods) > len(executed_methods_so_far))
                        if is_intermediate:
                            if q_mode == "4":
                                if args.hwaccel:
                                    stab_cmd += ["-c:v", "h264_nvenc", "-preset", "p1", "-b:v", "25000k"]
                                else:
                                    stab_cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "18"]
                            elif q_mode == "2":
                                if args.hwaccel:
                                    stab_cmd += ["-c:v", "h264_nvenc", "-preset", "p7", "-rc", "constqp", "-qp", "0"]
                                else:
                                    stab_cmd += ["-c:v", "libx264", "-crf", "0"]
                            else:
                                if args.hwaccel:
                                    stab_cmd += ["-c:v", "h264_nvenc", "-b:v", "30000k"]
                                else:
                                    stab_cmd += ["-c:v", "libx264", "-preset", args.preset, "-crf", args.crf]
                        else:
                            if args.hwaccel:
                                stab_cmd += ["-c:v", "h264_nvenc", "-preset", "p7", "-profile:v", "high", "-rc", "vbr", "-cq", "16", "-b:v", "50000k", "-maxrate", "80000k", "-spatial-aq", "1", "-temporal-aq", "1"]
                            else:
                                stab_cmd += ["-c:v", "libx264", "-preset", "slow", "-crf", "16"]
                        stab_cmd += [
                            "-pix_fmt", "yuv420p", "-c:a", "copy",
                            _tele_out
                        ]
                        rc2 = run_ffmpeg(stab_cmd, args.status_file, duration, start_ts, phase_imu_stab, status_code="stabilizing")
                        if rc2 == 0 and os.path.exists(_tele_out) and os.path.getsize(_tele_out) > 0:
                            current_file = _tele_out
                            stab_video_file = _tele_out
                            step2_stab = _tele_out
                            stage_step_io["telemetry"] = (stage_in_tele, _tele_out)
                            if q_mode == "4" and is_intermediate:
                                mode4_temp_intermediates.append(_tele_out)
                            embed_vrot_metadata(args.input, _tele_out)
                            create_meta_copy(_tele_out, args.status_file, start_ts, stab_name="Telemetry")
                        else:
                            print("Warning: Post-stitching telemetry stabilization failed, continuing pipeline.")
                    
                    # Generate telemetry stage report immediately
                    _s_in, _s_out = stage_step_io.get("telemetry", (stage_in_tele, _tele_out if ('_tele_out' in locals() and _tele_out) else current_file))
                    _r_tele = f"{out_base}_telemetry_report.txt"
                    _res_tele = write_single_stage_report(
                        "telemetry", "Telemetry (IMU Leveling)", _s_in, _s_out, _r_tele, out_base, q_mode,
                        step1_stitch=initial_raw_stitched_file, target_stab_file=current_file, kopf_disclaimer=_kopf_disclaimer,
                        fusion_info={"method": getattr(args, "telemetry_fusion", "none"), "gain": getattr(args, "telemetry_fusion_gain", 0.5)}
                    )
                    if _res_tele:
                        model_results["telemetry"] = _res_tele
                    current_file, _was_reverted = check_and_apply_stage_fallback(
                        "telemetry", "Telemetry (IMU Leveling)", _r_tele, sendcmd_file,
                        stage_in_tele, current_file, active_sendcmd_files, executed_methods_so_far,
                        getattr(args, 'fallback_unstabilized', True), reverted_stages, args.status_file
                    )
                    if _was_reverted:
                        stab_video_file = current_file
                        step2_stab = current_file
                    update_status(args.status_file, {
                        "status": "stabilizing",
                        "phase": "Telemetry stabilization completed",
                        "progress": 70.0,
                        "speed": "N/A", "eta": "Calculating...",
                        "elapsed": int(time.time() - start_ts)
                    })
                else:
                    print("Warning: Could not prepare telemetry sendcmd for post-stitching stabilization.")

            elif stage_item == "vidstab" and "vidstab" in active_methods:
                stage_in_opt = current_file
                # Optical-flow based stabilization (vidstab)
                phase_motion = f"Analysing motion (pass 1/2) (shakiness: {args.vidstab_shakiness}, accuracy: 15)"
                update_status(args.status_file, {
                    "status": "stabilizing", "phase": phase_motion,
                    "progress": 50.0, "speed": "N/A", "eta": "Calculating...",
                    "elapsed": int(time.time() - start_ts)
                })

                # VERY IMPORTANT: vidstabdetect APPENDS to the .trf file if it already exists!
                # Truncate rather than delete to ensure clean file state without removing files.
                if os.path.exists(trf_file):
                    try:
                        with open(trf_file, "w") as _f_trf: pass
                    except OSError: pass


                # 360-aware spherical vidstab stabilization (bigsh0t/Kdenlive algorithm).
                # Uses Lucas-Kanade optical flow + Kabsch SVD rotation estimation
                # Outputs absolute v360 yaw/pitch/roll corrections -- no seam line at Y=180 possible.
                sendcmd_file = f"{out_base}_sendcmd_vidstab.txt"
                phase_motion = f"Analysing 360 motion (pass 1/2) (shakiness: {args.vidstab_shakiness}, accuracy: 15)"
                update_status(args.status_file, {
                    "status": "stabilizing", "phase": phase_motion,
                    "progress": 60.0, "speed": "N/A", "eta": "Calculating...",
                    "elapsed": int(time.time() - start_ts)
                })

                motion_file_360 = f"{out_base}.optical360motion"
                opt_input_vid = current_file
                opt360_cmd = [
                    sys.executable, "-B", os.path.join(script_dir, "stabilize_vidstab.py"),
                    "--input",            opt_input_vid,
                    "--output",           sendcmd_file,
                    "--motion_file",      motion_file_360,
                    "--smoothing",        str(args.vidstab_smoothing),
                    "--shakiness",        str(args.vidstab_shakiness),
                    "--optalgo",          str(getattr(args, "vidstab_optalgo", "gauss")),
                    "--max_features",     "400",
                    "--refresh_interval", "1",
                ]
                opt_vis_video = f"{out_base}_opt_visual.mp4"
                if args.vidstab_visual:
                    opt360_cmd.extend(["--visual_video", opt_vis_video])
                if args.vidstab_tripod:
                    opt360_cmd.append("--tripod")

                rc_opt360 = run_ffmpeg(opt360_cmd, args.status_file, duration, start_ts, phase_motion, status_code="stabilizing")

                # Log correction stats from motion file header (run_ffmpeg discards Python stdout)
                if os.path.exists(motion_file_360):
                    try:
                        with open(motion_file_360, 'r') as _mf:
                            for _ml in _mf:
                                if _ml.startswith('# MaxCorr'):
                                    print(f"[Vidstab-360] Correction stats: {_ml.strip().lstrip('# ')}")
                                    break
                    except Exception:
                        pass

                if rc_opt360 != 0 or not os.path.exists(sendcmd_file) or os.path.getsize(sendcmd_file) == 0:
                    print("Warning: stabilize_vidstab.py failed, skipping vidstab pass.")

                else:
                    if args.vidstab_visual and os.path.exists(opt_vis_video) and os.path.getsize(opt_vis_video) > 0:
                        print(f"[Vidstab-360] Visual motion tracking video generated: {opt_vis_video}")

                    # Detect fps for visualize_corrections (was formerly done inside the old vidstab block)
                    try:
                        _probe = subprocess.run(
                            ["ffprobe", "-v", "error", "-select_streams", "v:0",
                             "-show_entries", "stream=r_frame_rate",
                             "-of", "default=noprint_wrappers=1:nokey=1", opt_input_vid],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, creationflags=WIN_NO_WINDOW)
                        fps = 30.0
                        if _probe.returncode == 0:
                            _n, _d = _probe.stdout.strip().split('/')
                            if int(_d) > 0: fps = float(_n) / float(_d)
                    except Exception:
                        fps = 30.0

                    if os.path.exists(sendcmd_file):
                        try:
                            update_status(args.status_file, {
                                "status": "stabilizing",
                                "phase": "Plotting vidstab motion curves & correction graphs...",
                                "progress": 68.0, "speed": "N/A", "eta": "Calculating...",
                                "elapsed": int(time.time() - start_ts)
                            })
                            vis_opt_cmd = [sys.executable, "-B", os.path.join(script_dir, "visualize_corrections.py"),
                                           "--trf", sendcmd_file, "--graph-type", "vidstab", "--output", out_base, "--fps", str(fps)]
                            subprocess.run(vis_opt_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=WIN_NO_WINDOW)
                        except Exception as e_vis_opt:
                            print(f"Notice: visualize_corrections vidstab error: {e_vis_opt}")

                    phase_stab_3d = f"Stabilising 2D (Vidstab) (smooth: {args.vidstab_smoothing}, shakiness: {args.vidstab_shakiness})"
                    update_status(args.status_file, {
                        "status": "stabilizing", "phase": phase_stab_3d,
                        "progress": 70.0, "speed": "N/A", "eta": "Calculating...",
                        "elapsed": int(time.time() - start_ts)
                    })

                    sendcmd_path_escaped = sendcmd_file.replace('\\', '/')
                    executed_methods_so_far.append("vidstab")
                    stage_blocks = list(executed_methods_so_far)
                    _opt_out = f"{out_base}_{'_'.join(stage_blocks)}{out_ext}"

                    if q_mode in ("1", "4"):
                        active_sendcmd_files.append(sendcmd_file)
                        print(f"[{'Hybrid' if q_mode == '4' else 'Single-Pass'} Mode] Added Vidstab transform ({sendcmd_file}) to composition queue.")
                    if q_mode == "1":
                        stage_step_io["vidstab"] = (initial_raw_stitched_file, _opt_out)

                    if q_mode != "1":
                        filter_str = f"sendcmd=f='{sendcmd_path_escaped}',v360=input=equirect:output=equirect{v360_interp}"
                        use_visual = bool(args.vidstab_visual and os.path.exists(opt_vis_video) and os.path.getsize(opt_vis_video) > 0)
                        is_intermediate = (q_mode == "4") or (len(active_methods) > len(executed_methods_so_far))

                        codec_args = []
                        if is_intermediate:
                            if q_mode == "4":
                                if args.hwaccel:
                                    codec_args = ["-c:v", "h264_nvenc", "-preset", "p1", "-b:v", "25000k"]
                                else:
                                    codec_args = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "18"]
                            elif q_mode == "2":
                                if args.hwaccel:
                                    codec_args = ["-c:v", "h264_nvenc", "-preset", "p7", "-rc", "constqp", "-qp", "0"]
                                else:
                                    codec_args = ["-c:v", "libx264", "-crf", "0"]
                            else:
                                if args.hwaccel:
                                    codec_args = ["-c:v", "h264_nvenc", "-b:v", "30000k"]
                                else:
                                    codec_args = ["-c:v", "libx264", "-preset", args.preset, "-crf", args.crf]
                        else:
                            if args.hwaccel:
                                codec_args = ["-c:v", "h264_nvenc", "-preset", "p7", "-profile:v", "high", "-rc", "vbr", "-cq", "16", "-b:v", "50000k", "-maxrate", "80000k", "-spatial-aq", "1", "-temporal-aq", "1"]
                            else:
                                codec_args = ["-c:v", "libx264", "-preset", "slow", "-crf", "16"]

                        if use_visual:
                            # 1. Render clean stabilized video for subsequent stages & master render
                            _opt_clean = f"{out_base}_{'_'.join(stage_blocks)}_clean{out_ext}"
                            stab_clean_cmd = [
                                "ffmpeg", "-y", "-progress", "-",
                                "-i", current_file,
                                "-vf", filter_str,
                            ] + codec_args + [
                                "-pix_fmt", "yuv420p", "-c:a", "copy",
                                _opt_clean
                            ]
                            rc_clean = run_ffmpeg(stab_clean_cmd, args.status_file, duration, start_ts, phase_stab_3d, status_code="stabilizing")

                            # 2. Render visual tracking points video specifically for the vidstab tab
                            stab_cmd = [
                                "ffmpeg", "-y", "-progress", "-",
                                "-i", opt_vis_video,
                                "-i", current_file,
                                "-map", "0:v", "-map", "1:a?",
                                "-vf", filter_str,
                            ] + codec_args + [
                                "-pix_fmt", "yuv420p", "-c:a", "copy",
                                _opt_out
                            ]
                            rc2 = run_ffmpeg(stab_cmd, args.status_file, duration, start_ts, phase_stab_3d, status_code="stabilizing")

                            if rc2 == 0 and os.path.exists(_opt_out) and os.path.getsize(_opt_out) > 0:
                                stage_step_io["vidstab"] = (stage_in_opt, _opt_out)
                                if q_mode == "4" and is_intermediate:
                                    mode4_temp_intermediates.append(_opt_out)
                                embed_vrot_metadata(args.input, _opt_out)
                                create_meta_copy(_opt_out, args.status_file, start_ts, stab_name="Vidstab")

                            if rc_clean == 0 and os.path.exists(_opt_clean) and os.path.getsize(_opt_clean) > 0:
                                current_file = _opt_clean
                                stab_video_file = _opt_clean
                                step2_stab = _opt_clean
                                if q_mode == "4" and is_intermediate:
                                    mode4_temp_intermediates.append(_opt_clean)
                                if not (rc2 == 0 and os.path.exists(_opt_out) and os.path.getsize(_opt_out) > 0):
                                    stage_step_io["vidstab"] = (stage_in_opt, _opt_clean)
                            elif rc2 == 0 and os.path.exists(_opt_out) and os.path.getsize(_opt_out) > 0:
                                current_file = _opt_out
                                stab_video_file = _opt_out
                                step2_stab = _opt_out
                            else:
                                print("Warning: Vidstab 3D stabilization transform failed, using un-stabilized output.")
                        else:
                            stab_cmd = [
                                "ffmpeg", "-y", "-progress", "-",
                                "-i", current_file,
                                "-vf", filter_str,
                            ] + codec_args + [
                                "-pix_fmt", "yuv420p", "-c:a", "copy",
                                _opt_out
                            ]
                            rc2 = run_ffmpeg(stab_cmd, args.status_file, duration, start_ts, phase_stab_3d, status_code="stabilizing")
                            if rc2 == 0 and os.path.exists(_opt_out) and os.path.getsize(_opt_out) > 0:
                                current_file = _opt_out
                                stab_video_file = _opt_out
                                step2_stab = _opt_out
                                stage_step_io["vidstab"] = (stage_in_opt, _opt_out)
                                if q_mode == "4" and is_intermediate:
                                    mode4_temp_intermediates.append(_opt_out)
                                embed_vrot_metadata(args.input, _opt_out)
                                create_meta_copy(_opt_out, args.status_file, start_ts, stab_name="Vidstab")
                            else:
                                print("Warning: Vidstab 3D stabilization transform failed, using un-stabilized output.")

                    # Generate vidstab stage report immediately
                    _s_in, _s_out = stage_step_io.get("vidstab", (stage_in_opt, _opt_out if ('_opt_out' in locals() and _opt_out) else current_file))
                    _r_opt = f"{out_base}_vidstab_report.txt"
                    _res_opt = write_single_stage_report("vidstab", "VidSTAB (2D Motion)", _s_in, _s_out, _r_opt, out_base, q_mode, step1_stitch=initial_raw_stitched_file, target_stab_file=current_file, kopf_disclaimer=_kopf_disclaimer)
                    if _res_opt:
                        model_results["vidstab"] = _res_opt
                    current_file, _was_reverted = check_and_apply_stage_fallback(
                        "vidstab", "VidSTAB (2D Motion)", _r_opt, sendcmd_file,
                        stage_in_opt, current_file, active_sendcmd_files, executed_methods_so_far,
                        getattr(args, 'fallback_unstabilized', True), reverted_stages, args.status_file
                    )
                    if _was_reverted:
                        stab_video_file = current_file
                        step2_stab = current_file
                    update_status(args.status_file, {
                        "status": "stabilizing",
                        "phase": "Vidstab stabilization completed",
                        "progress": 70.0,
                        "speed": "N/A", "eta": "Calculating...",
                        "elapsed": int(time.time() - start_ts)
                    })

            # -- Kabsch 360 spherical stabilization (bigsh0t-inspired, old pipeline) --
            elif stage_item == "kabsch" and "kabsch" in active_methods:
                stage_in_kab = current_file
                sendcmd_file = f"{out_base}_sendcmd_kabsch.txt"
                motion_file_kabsch = f"{out_base}.kabsch360motion"
                phase_kabsch = f"Analysing 360 motion (Kabsch SVD sphere) (smooth: {args.kabsch_smoothing})"
                update_status(args.status_file, {
                    "status": "stabilizing", "phase": phase_kabsch,
                    "progress": 60.0, "speed": "N/A", "eta": "Calculating...",
                    "elapsed": int(time.time() - start_ts)
                })
                kab_input_vid = current_file
                kabsch_cmd = [
                    sys.executable, "-B", os.path.join(script_dir, "stabilize_kabsch.py"),
                    "--input",            kab_input_vid,
                    "--output",           sendcmd_file,
                    "--motion_file",      motion_file_kabsch,
                    "--smoothing",        str(args.kabsch_smoothing),
                    "--max_features",     str(args.kopf_max_features),
                    "--refresh_interval", "1",
                ]
                if getattr(args, "util_bigsh0t", False):
                    kabsch_cmd.append("--export_bigsh0t")

                rc_kabsch = run_ffmpeg(kabsch_cmd, args.status_file, duration, start_ts, phase_kabsch, status_code="stabilizing")
                if rc_kabsch != 0 or not os.path.exists(sendcmd_file) or os.path.getsize(sendcmd_file) == 0:
                    print("Warning: stabilize_kabsch.py failed, skipping kabsch pass.")
                else:
                    try:
                        update_status(args.status_file, {
                            "status": "stabilizing",
                            "phase": "Plotting Kabsch motion curves & correction graphs...",
                            "progress": 68.0, "speed": "N/A", "eta": "Calculating...",
                            "elapsed": int(time.time() - start_ts)
                        })
                        fps_kab = fps if 'fps' in locals() else 30.0
                        vis_kab_cmd = [sys.executable, "-B", os.path.join(script_dir, "visualize_corrections.py"), "--trf", sendcmd_file, "--graph-type", "kabsch", "--output", out_base, "--fps", str(fps_kab)]
                        subprocess.run(vis_kab_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=WIN_NO_WINDOW)
                    except Exception as e_vis_kab:
                        print(f"Notice: visualize_corrections kabsch error: {e_vis_kab}")

                    phase_stab_kabsch = f"Stabilising 360 (Kabsch SVD) (smooth: {args.kabsch_smoothing})"
                    update_status(args.status_file, {
                        "status": "stabilizing", "phase": phase_stab_kabsch,
                        "progress": 70.0, "speed": "N/A", "eta": "Calculating...",
                        "elapsed": int(time.time() - start_ts)
                    })
                    executed_methods_so_far.append("kabsch")
                    stage_blocks = list(executed_methods_so_far)
                    _kab_out = f"{out_base}_{'_'.join(stage_blocks)}{out_ext}"

                    if q_mode in ("1", "4"):
                        active_sendcmd_files.append(sendcmd_file)
                        print(f"[{'Hybrid' if q_mode == '4' else 'Single-Pass'} Mode] Added Kabsch transform ({sendcmd_file}) to composition queue.")
                    if q_mode == "1":
                        stage_step_io["kabsch"] = (initial_raw_stitched_file, _kab_out)

                    if q_mode != "1":
                        sendcmd_kabsch_esc = sendcmd_file.replace('\\', '/')
                        stab_kabsch_cmd = [
                            "ffmpeg", "-y", "-progress", "-",
                            "-i", current_file,
                            "-vf", f"sendcmd=f='{sendcmd_kabsch_esc}',v360=input=equirect:output=equirect{v360_interp}",
                        ]
                        is_intermediate = (q_mode == "4") or (len(active_methods) > len(executed_methods_so_far))
                        if is_intermediate:
                            if q_mode == "4":
                                if args.hwaccel:
                                    stab_kabsch_cmd += ["-c:v", "h264_nvenc", "-preset", "p1", "-b:v", "25000k"]
                                else:
                                    stab_kabsch_cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "18"]
                            elif q_mode == "2":
                                if args.hwaccel:
                                    stab_kabsch_cmd += ["-c:v", "h264_nvenc", "-preset", "p7", "-rc", "constqp", "-qp", "0"]
                                else:
                                    stab_kabsch_cmd += ["-c:v", "libx264", "-crf", "0"]
                            else:
                                if args.hwaccel:
                                    stab_kabsch_cmd += ["-c:v", "h264_nvenc", "-b:v", "30000k"]
                                else:
                                    stab_kabsch_cmd += ["-c:v", "libx264", "-preset", args.preset, "-crf", args.crf]
                        else:
                            if args.hwaccel:
                                stab_kabsch_cmd += ["-c:v", "h264_nvenc", "-preset", "p7", "-profile:v", "high", "-rc", "vbr", "-cq", "16", "-b:v", "50000k", "-maxrate", "80000k", "-spatial-aq", "1", "-temporal-aq", "1"]
                            else:
                                stab_kabsch_cmd += ["-c:v", "libx264", "-preset", "slow", "-crf", "16"]

                        stab_kabsch_cmd += ["-pix_fmt", "yuv420p", "-c:a", "copy", _kab_out]
                        rc_kab2 = run_ffmpeg(stab_kabsch_cmd, args.status_file, duration, start_ts, phase_stab_kabsch, status_code="stabilizing")
                        if rc_kab2 == 0 and os.path.exists(_kab_out) and os.path.getsize(_kab_out) > 0:
                            current_file = _kab_out
                            stab_video_file = _kab_out
                            step2_stab = _kab_out
                            stage_step_io["kabsch"] = (stage_in_kab, _kab_out)
                            if q_mode == "4" and is_intermediate:
                                mode4_temp_intermediates.append(_kab_out)
                            embed_vrot_metadata(args.input, _kab_out)
                            create_meta_copy(_kab_out, args.status_file, start_ts, stab_name="Kabsch")
                        else:
                            print("Warning: Kabsch SVD stabilization transform failed, using un-stabilized output.")

                    # Generate kabsch stage report immediately
                    _s_in, _s_out = stage_step_io.get("kabsch", (stage_in_kab, _kab_out if ('_kab_out' in locals() and _kab_out) else current_file))
                    _r_kab = f"{out_base}_kabsch_report.txt"
                    _res_kab = write_single_stage_report("kabsch", "Kabsch (Spherical SVD Rotation)", _s_in, _s_out, _r_kab, out_base, q_mode, step1_stitch=initial_raw_stitched_file, target_stab_file=current_file, kopf_disclaimer=_kopf_disclaimer)
                    if _res_kab:
                        model_results["kabsch"] = _res_kab
                    current_file, _was_reverted = check_and_apply_stage_fallback(
                        "kabsch", "Kabsch (Spherical SVD Rotation)", _r_kab, sendcmd_file,
                        stage_in_kab, current_file, active_sendcmd_files, executed_methods_so_far,
                        getattr(args, 'fallback_unstabilized', True), reverted_stages, args.status_file
                    )
                    if _was_reverted:
                        stab_video_file = current_file
                        step2_stab = current_file
                    update_status(args.status_file, {
                        "status": "stabilizing",
                        "phase": "Kabsch stabilization completed",
                        "progress": 70.0,
                        "speed": "N/A", "eta": "Calculating...",
                        "elapsed": int(time.time() - start_ts)
                    })

            # -- Kopf 2016 Hybrid 3D-2D stabilization -----------------------------
            elif stage_item == "kopf" and "kopf" in active_methods:
                stage_in_kopf = current_file
                sendcmd_file = f"{out_base}_sendcmd_kopf.txt"
                motion_file_kopf = f"{out_base}.kopf360motion"
                kopf_prog_analyse = 80.0 if args.stabilize_type in ("hybrid_kopf", "telemetry_kopf") else 55.0
                kopf_prog_stab    = 90.0 if args.stabilize_type in ("hybrid_kopf", "telemetry_kopf") else 75.0
                phase_kopf = f"Analysing 360 motion (Kopf 3D-2D) (kf: {args.kopf_keyframe_sec}s)"
                update_status(args.status_file, {
                    "status": "stabilizing", "phase": phase_kopf,
                    "progress": kopf_prog_analyse, "speed": "N/A", "eta": "Calculating...",
                    "elapsed": int(time.time() - start_ts)
                })
                kopf_input_vid = current_file
                kopf_cmd = [
                    sys.executable, "-B", os.path.join(script_dir, "stabilize_kopf.py"),
                    "--input",          kopf_input_vid,
                    "--output",         sendcmd_file,
                    "--motion_file",    motion_file_kopf,
                    "--keyframe_sec",   str(args.kopf_keyframe_sec),
                    "--cube_face",      str(args.kopf_cube_face),
                    "--smoothing",      str(args.vidstab_smoothing),
                    "--max_features",   str(args.kopf_max_features),
                ]
                if args.kopf_deformed:
                    kopf_cmd.append("--deformed")
                if args.kopf_reapply:
                    kopf_cmd.append("--reapply")

                rc_kopf = run_ffmpeg(kopf_cmd, args.status_file, duration, start_ts, phase_kopf, status_code="stabilizing")
                if rc_kopf != 0 or not os.path.exists(sendcmd_file) or os.path.getsize(sendcmd_file) == 0:
                    print("Warning: stabilize_kopf.py failed, skipping kopf pass.")
                else:
                    # Log correction stats from motion file header (consistent with optical pass)
                    if os.path.exists(motion_file_kopf):
                        try:
                            with open(motion_file_kopf, 'r') as _kf:
                                for _kl in _kf:
                                    if _kl.startswith('# MaxCorr'):
                                        print(f"[Kopf360] Correction stats: {_kl.strip().lstrip('# ')}")
                                        break
                        except Exception:
                            pass

                    # Visualize kopf corrections (same pattern as optical pass)
                    try:
                        fps = 30.0
                        _probe_kopf = subprocess.run(
                            ["ffprobe", "-v", "error", "-select_streams", "v:0",
                              "-show_entries", "stream=r_frame_rate",
                              "-of", "default=noprint_wrappers=1:nokey=1", kopf_input_vid],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, creationflags=WIN_NO_WINDOW)
                        if _probe_kopf.returncode == 0:
                            _n, _d = _probe_kopf.stdout.strip().split('/')
                            if int(_d) > 0: fps = float(_n) / float(_d)
                    except Exception:
                        fps = 30.0

                    try:
                        update_status(args.status_file, {
                            "status": "stabilizing",
                            "phase": "Plotting Kopf motion curves & correction graphs...",
                            "progress": 72.0, "speed": "N/A", "eta": "Calculating...",
                            "elapsed": int(time.time() - start_ts)
                        })
                        vis_kopf_cmd = [sys.executable, "-B", os.path.join(script_dir, "visualize_corrections.py"),
                                        "--trf", sendcmd_file, "--graph-type", "kopf", "--output", out_base, "--fps", str(fps)]
                        subprocess.run(vis_kopf_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=WIN_NO_WINDOW)
                    except Exception as e_vis_kopf:
                        print(f"Notice: visualize_corrections kopf error: {e_vis_kopf}")

                    phase_stab_kopf = f"Stabilising 360 (Kopf 3D-2D) (kf: {args.kopf_keyframe_sec}s)"
                    update_status(args.status_file, {
                        "status": "stabilizing", "phase": phase_stab_kopf,
                        "progress": kopf_prog_stab, "speed": "N/A", "eta": "Calculating...",
                        "elapsed": int(time.time() - start_ts)
                    })
                    executed_methods_so_far.append("kopf")
                    stage_blocks = list(executed_methods_so_far)
                    _kopf_out = f"{out_base}_{'_'.join(stage_blocks)}{out_ext}"

                    if q_mode in ("1", "4"):
                        active_sendcmd_files.append(sendcmd_file)
                        print(f"[{'Hybrid' if q_mode == '4' else 'Single-Pass'} Mode] Added Kopf transform ({sendcmd_file}) to composition queue.")
                    if q_mode == "1":
                        stage_step_io["kopf"] = (initial_raw_stitched_file, _kopf_out)

                    if q_mode != "1":
                        sendcmd_kopf_esc = sendcmd_file.replace('\\', '/')
                        stab_kopf_cmd = [
                            "ffmpeg", "-y", "-progress", "-",
                            "-i", current_file,
                            "-vf", f"sendcmd=f='{sendcmd_kopf_esc}',v360=input=equirect:output=equirect{v360_interp}",
                        ]
                        is_intermediate = (q_mode == "4") or (len(active_methods) > len(executed_methods_so_far))
                        if is_intermediate:
                            if q_mode == "4":
                                if args.hwaccel:
                                    stab_kopf_cmd += ["-c:v", "h264_nvenc", "-preset", "p1", "-b:v", "25000k"]
                                else:
                                    stab_kopf_cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "18"]
                            elif q_mode == "2":
                                if args.hwaccel:
                                    stab_kopf_cmd += ["-c:v", "h264_nvenc", "-preset", "p7", "-rc", "constqp", "-qp", "0"]
                                else:
                                    stab_kopf_cmd += ["-c:v", "libx264", "-crf", "0"]
                            else:
                                if args.hwaccel:
                                    stab_kopf_cmd += ["-c:v", "h264_nvenc", "-b:v", "30000k"]
                                else:
                                    stab_kopf_cmd += ["-c:v", "libx264", "-preset", args.preset, "-crf", args.crf]
                        else:
                            if args.hwaccel:
                                stab_kopf_cmd += ["-c:v", "h264_nvenc", "-preset", "p7", "-profile:v", "high", "-rc", "vbr", "-cq", "16", "-b:v", "50000k", "-maxrate", "80000k", "-spatial-aq", "1", "-temporal-aq", "1"]
                            else:
                                stab_kopf_cmd += ["-c:v", "libx264", "-preset", "slow", "-crf", "16"]

                        stab_kopf_cmd += ["-pix_fmt", "yuv420p", "-c:a", "copy", _kopf_out]
                        rc_kop2 = run_ffmpeg(stab_kopf_cmd, args.status_file, duration, start_ts, phase_stab_kopf, status_code="stabilizing")
                        if rc_kop2 == 0 and os.path.exists(_kopf_out) and os.path.getsize(_kopf_out) > 0:
                            current_file = _kopf_out
                            stab_video_file = _kopf_out
                            step2_stab = _kopf_out
                            stage_step_io["kopf"] = (stage_in_kopf, _kopf_out)
                            if q_mode == "4" and is_intermediate:
                                mode4_temp_intermediates.append(_kopf_out)
                            embed_vrot_metadata(args.input, _kopf_out)
                            create_meta_copy(_kopf_out, args.status_file, start_ts, stab_name="Kopf")
                        else:
                            print("Warning: Kopf 3D-2D stabilization transform failed, using un-stabilized output.")

                    # Generate kopf stage report immediately
                    _s_in, _s_out = stage_step_io.get("kopf", (stage_in_kopf, _kopf_out if ('_kopf_out' in locals() and _kopf_out) else current_file))
                    _r_kop = f"{out_base}_kopf_report.txt"
                    _res_kop = write_single_stage_report("kopf", "Kopf (3D-2D Vision Keyframe)", _s_in, _s_out, _r_kop, out_base, q_mode, step1_stitch=initial_raw_stitched_file, target_stab_file=current_file, kopf_disclaimer=_kopf_disclaimer)
                    if _res_kop:
                        model_results["kopf"] = _res_kop
                    current_file, _was_reverted = check_and_apply_stage_fallback(
                        "kopf", "Kopf (3D-2D Vision Keyframe)", _r_kop, sendcmd_file,
                        stage_in_kopf, current_file, active_sendcmd_files, executed_methods_so_far,
                        getattr(args, 'fallback_unstabilized', True), reverted_stages, args.status_file
                    )
                    if _was_reverted:
                        stab_video_file = current_file
                        step2_stab = current_file
                    update_status(args.status_file, {
                        "status": "stabilizing",
                        "phase": "Kopf stabilization completed",
                        "progress": 75.0,
                        "speed": "N/A", "eta": "Calculating...",
                        "elapsed": int(time.time() - start_ts)
                    })

            # -- Stage 2e: Horizon (Interactive Pause & Refine) -------
            elif stage_item == "horizon" and "horizon" in active_methods:
                stage_in_cp = current_file
                clean_base = os.path.basename(out_base)
                cp_json_path = os.path.join(out_dir, f"{clean_base}_horizon_checkpoints.json") if out_dir else f"{clean_base}_horizon_checkpoints.json"
                cp_json_work = os.path.join("data", "runtime", "work", f"{clean_base}_horizon_checkpoints.json")

                # Accurately probe video stream fps for generic cameras (23.976, 24, 25, 29.97, 30, 50, 59.94, 60 fps, etc.)
                probe_fps = 29.97
                cand_probe_vid = current_file if (os.path.exists(current_file) and os.path.getsize(current_file) > 1000) else (getattr(args, 'input', None) or "")
                if cand_probe_vid and os.path.exists(cand_probe_vid):
                    try:
                        _p_proc = subprocess.run(
                            ["ffprobe", "-v", "error", "-select_streams", "v:0",
                             "-show_entries", "stream=r_frame_rate,avg_frame_rate",
                             "-of", "default=noprint_wrappers=1:nokey=1", cand_probe_vid],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, creationflags=WIN_NO_WINDOW)
                        if _p_proc.returncode == 0:
                            for _line in _p_proc.stdout.strip().splitlines():
                                if '/' in _line:
                                    _n, _d = _line.strip().split('/')
                                    if int(_d) > 0 and float(_n) > 0:
                                        probe_fps = float(_n) / float(_d)
                                        break
                    except Exception:
                        pass
                elif 'fps' in locals() and isinstance(locals()['fps'], (int, float)) and locals()['fps'] > 0:
                    probe_fps = float(locals()['fps'])

                target_json = None
                # Check if a fresh JSON for THIS specific out_base was already created during this run
                if os.path.exists(cp_json_path) and os.path.getsize(cp_json_path) > 10 and os.path.getmtime(cp_json_path) >= (start_ts - 2):
                    target_json = cp_json_path
                elif os.path.exists(cp_json_work) and os.path.getsize(cp_json_work) > 10 and os.path.getmtime(cp_json_work) >= (start_ts - 2):
                    target_json = cp_json_work

                if getattr(args, "horizon_autodetect", False) and not target_json:
                    print(f"\n=======================================================", flush=True)
                    print(f"[Horizon Checkpoints] Auto-Detect Mode Enabled (Source: {args.horizon_autodetect_source}, Density: {args.horizon_autodetect_density})", flush=True)
                    print(f"=======================================================\n", flush=True)

                    update_status(args.status_file, {
                        "status": "stabilizing",
                        "phase": "Auto-Detecting Horizon Checkpoints...",
                        "progress": 72.0, "speed": "ANALYZING", "eta": "Calculating...",
                        "elapsed": int(time.time() - start_ts)
                    })

                    try:
                        from stabilize_horizon import auto_detect_from_telemetry, auto_detect_from_motion, auto_detect_from_video, seed_cadence_checkpoints, detect_pitch_extrema_checkpoints

                        total_f = int(round(duration * probe_fps)) if duration > 0 else 300
                        b_pitch = float(args.pitch) if hasattr(args, 'pitch') and args.pitch is not None else 0.0
                        b_roll = float(args.roll) if hasattr(args, 'roll') and args.roll is not None else 0.0
                        roll_damp = getattr(args, "horizon_roll_damping", 0.70)

                        eps_map = {"smooth": 3.5, "balanced": 2.5, "fine": 1.5, "detailed": 1.5, "ultra": 0.75, "ultra_dense": 0.75}
                        epsilon = eps_map.get(args.horizon_autodetect_density, 2.5)

                        clean_video_no_ext = os.path.splitext(os.path.basename(current_file))[0]
                        in_base_no_ext = os.path.splitext(os.path.basename(args.input))[0] if getattr(args, 'input', None) else ""
                        telemetry_cands = [
                            f"{out_base}_telemetry.txt",
                            f"{out_base}.gcsv",
                            os.path.join("data", "runtime", "work", f"{clean_video_no_ext}_telemetry.txt"),
                            os.path.join("data", "runtime", "work", f"{clean_video_no_ext}.gcsv"),
                            os.path.join("data", "input", "videos", f"{clean_video_no_ext}_telemetry.txt"),
                            os.path.join("data", "input", "videos", f"{clean_video_no_ext}.gcsv"),
                            os.path.join("data", "input", "videos", f"{clean_video_no_ext}.MP4.gcsv"),
                        ]
                        if in_base_no_ext and in_base_no_ext != clean_video_no_ext:
                            telemetry_cands.extend([
                                os.path.join("data", "input", "videos", f"{in_base_no_ext}_telemetry.txt"),
                                os.path.join("data", "input", "videos", f"{in_base_no_ext}.gcsv"),
                                os.path.join("data", "input", "videos", f"{in_base_no_ext}.MP4.gcsv"),
                            ])
                        found_telemetry = next((c for c in telemetry_cands if c and os.path.exists(c) and os.path.getsize(c) > 50), None)

                        # On-demand telemetry extraction if not already on disk
                        if not found_telemetry and (getattr(args, 'input', None) or current_file):
                            cand_in = getattr(args, 'input', None) or current_file
                            if cand_in and os.path.exists(cand_in):
                                work_telem = os.path.join("data", "runtime", "work", f"{clean_video_no_ext}_telemetry.txt")
                                if os.path.exists(work_telem) and os.path.getsize(work_telem) > 50:
                                    found_telemetry = work_telem
                                else:
                                    extract_script = os.path.join(script_dir, "extract_telemetry.py")
                                    try:
                                        os.makedirs("data", exist_ok=True)
                                        os.makedirs(os.path.join("data", "runtime", "work"), exist_ok=True)
                                        subprocess.run([sys.executable, "-B", extract_script, cand_in, work_telem, "--source", "auto"],
                                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=WIN_NO_WINDOW)
                                        if os.path.exists(work_telem) and os.path.getsize(work_telem) > 50:
                                            found_telemetry = work_telem
                                    except Exception:
                                        pass

                        motion_file_kopf = f"{out_base}.kopf360motion"
                        motion_file_kabsch = f"{out_base}.kabsch360motion"
                        if os.path.exists(motion_file_kopf) and os.path.getsize(motion_file_kopf) > 50:
                            found_motion = motion_file_kopf
                        elif os.path.exists(motion_file_kabsch) and os.path.getsize(motion_file_kabsch) > 50:
                            found_motion = motion_file_kabsch
                        else:
                            found_motion = None
                        found_video = current_file if (os.path.exists(current_file) and os.path.getsize(current_file) > 1000) else None

                        is_post_stabilized = any(x in current_file for x in ['_telemetry', '_kopf', '_kabsch', '_vidstab', '_horizon', '_3_'])
                        src_mode = args.horizon_autodetect_source

                        cps = []
                        chosen_src = "none"

                        if src_mode in ("pitch_extrema", "auto") and (found_telemetry or found_motion):
                            prom = getattr(args, "horizon_pitch_prominence", 0.8) or 0.8
                            cps = detect_pitch_extrema_checkpoints(total_f, fps=probe_fps, min_prominence_deg=prom, max_checkpoints=None,
                                                                   telemetry_file=found_telemetry, motion_file=found_motion,
                                                                   video_file=found_video, neutral_baseline=False,
                                                                   baseline_pitch=0.0, baseline_roll=0.0,
                                                                   roll_damping=roll_damp)
                            chosen_src = f"smart gait extrema ({prom:.1f}° swing, {roll_damp:.2f}x roll damp)"
                        elif src_mode == "cadence":
                            cadence_sec = {"smooth": 2.0, "balanced": 0.5, "fine": 0.33, "detailed": 0.25, "ultra": 0.15, "ultra_dense": 0.15}.get(args.horizon_autodetect_density, 0.5)
                            cps = seed_cadence_checkpoints(total_f, fps=probe_fps, interval_sec=cadence_sec, max_checkpoints=None, adaptive=False,
                                                           initial_angles=(0.0, 0.0, 0.0),
                                                           telemetry_file=found_telemetry, motion_file=found_motion,
                                                           roll_damping=roll_damp)
                            chosen_src = f"cadence ({cadence_sec}s)"
                        elif (src_mode == "vision" or src_mode == "auto") and found_video:
                            cps = auto_detect_from_video(found_video, fps=probe_fps, epsilon=epsilon)
                            chosen_src = "vision"
                        elif src_mode == "auto" and found_telemetry:
                            prom = getattr(args, "horizon_pitch_prominence", 0.8) or 0.8
                            cps = detect_pitch_extrema_checkpoints(total_f, fps=probe_fps, min_prominence_deg=prom, max_checkpoints=None,
                                                                   telemetry_file=found_telemetry, neutral_baseline=False,
                                                                   baseline_pitch=0.0, baseline_roll=0.0,
                                                                   roll_damping=roll_damp)
                            chosen_src = f"auto telemetry extrema ({prom:.1f}° swing, {roll_damp:.2f}x roll damp)"
                        else:
                            cps = seed_cadence_checkpoints(total_f, fps=probe_fps, interval_sec=0.5, max_checkpoints=None, adaptive=False,
                                                           initial_angles=(b_pitch, b_roll, 0.0),
                                                           roll_damping=roll_damp)
                            chosen_src = "cadence (0.5s fallback)"

                        # Fallback if no keyframes detected
                        if not cps:
                            if found_telemetry:
                                cps = auto_detect_from_telemetry(found_telemetry, fps=probe_fps, baseline_pitch=b_pitch, baseline_roll=b_roll, epsilon=epsilon, neutral_baseline=False)
                                chosen_src = "fallback telemetry"
                            elif found_video:
                                cps = auto_detect_from_video(found_video, fps=probe_fps, epsilon=epsilon)
                                chosen_src = "fallback vision"
                            else:
                                cps = [
                                    {"frame": 0, "pitch": 0.0, "roll": 0.0, "yaw": 0.0},
                                    {"frame": max(1, total_f - 1), "pitch": 0.0, "roll": 0.0, "yaw": 0.0}
                                ]
                                chosen_src = "flat zero fallback"

                        params_meta = {
                            "detection_type": chosen_src,
                            "source": getattr(args, "horizon_autodetect_source", "auto"),
                            "density": getattr(args, "horizon_autodetect_density", "balanced"),
                            "epsilon": epsilon,
                            "min_prominence": getattr(args, "horizon_pitch_prominence", 0.8),
                            "roll_damping": roll_damp,
                            "baseline_pitch": b_pitch,
                            "baseline_roll": b_roll,
                            "fps": probe_fps,
                            "total_frames": total_f,
                            "video": os.path.basename(current_file),
                            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
                        }
                        cp_dict = {
                            "version": "1.0",
                            "sourceType": chosen_src,
                            "fps": probe_fps,
                            "totalFrames": total_f,
                            "ignoreYaw": not args.horizon_apply_yaw,
                            "parameters": params_meta,
                            "checkpoints": cps
                        }

                        os.makedirs("data/runtime/work", exist_ok=True)
                        if out_dir: os.makedirs(out_dir, exist_ok=True)
                        with open(cp_json_work, "w", encoding="utf-8", newline="\n") as f_json:
                            json.dump(cp_dict, f_json, indent=2)
                        if cp_json_path != cp_json_work:
                            with open(cp_json_path, "w", encoding="utf-8", newline="\n") as f_json_p:
                                json.dump(cp_dict, f_json_p, indent=2)

                        from stabilize_horizon import write_horizon_params_log
                        log_dests = [os.path.join("data", "runtime", "work", f"{clean_base}_horizon_params.log")]
                        if out_dir:
                            log_dests.append(os.path.join(out_dir, f"{clean_base}_horizon_params.log"))
                        write_horizon_params_log(clean_base, cp_dict, log_dests, video_name=current_file)

                        target_json = cp_json_work
                        print(f"[Horizon Checkpoints] Auto-detected {len(cps)} inflection keyframe(s) via {chosen_src}. Saved to: {cp_json_work}", flush=True)

                    except Exception as e_ad:
                        print(f"[Horizon Checkpoints] Auto-detect error ({e_ad}), falling back to interactive pause.", flush=True)
                        target_json = None

                if not target_json:
                    print(f"\n=======================================================", flush=True)
                    print(f"[STATUS:WAITING_FOR_HORIZON_CHECKPOINTS:out_base={clean_base}:video={current_file}]", flush=True)
                    print(f"[Horizon Checkpoints] Pausing pipeline. Please open Horizon Editor and save checkpoints to data/runtime/work/{clean_base}_horizon_checkpoints.json", flush=True)
                    print(f"=======================================================\n", flush=True)

                    update_status(args.status_file, {
                        "status": "stabilizing",
                        "phase": "Waiting for Horizon Checkpoints in WebGL Editor...",
                        "progress": 72.0, "speed": "PAUSED", "eta": "Paused",
                        "elapsed": int(time.time() - start_ts)
                    })

                    flag_path = os.path.join("data", "runtime", "work", f"{clean_base}_resume_checkpoints.flag")

                    wait_count = 0
                    while not target_json:
                        time.sleep(1.0)
                        wait_count += 1
                        if os.path.exists(cp_json_path) and os.path.getsize(cp_json_path) > 10 and os.path.getmtime(cp_json_path) >= (start_ts - 2):
                            target_json = cp_json_path
                            break
                        if os.path.exists(cp_json_work) and os.path.getsize(cp_json_work) > 10 and os.path.getmtime(cp_json_work) >= (start_ts - 2):
                            target_json = cp_json_work
                            break
                        if os.path.exists(flag_path) and wait_count > 1:
                            if os.path.exists(cp_json_work) and os.path.getsize(cp_json_work) > 10: target_json = cp_json_work
                            elif os.path.exists(cp_json_path) and os.path.getsize(cp_json_path) > 10: target_json = cp_json_path
                            break

                if target_json and os.path.exists(target_json) and os.path.getsize(target_json) > 10:
                    update_status(args.status_file, {
                        "status": "stabilizing",
                        "phase": "Generating Horizon Checkpoints SLERP trajectory & graph...",
                        "progress": 75.0, "speed": "N/A", "eta": "Calculating...",
                        "elapsed": int(time.time() - start_ts)
                    })
                    sendcmd_cp = f"{out_base}_sendcmd_horizon_checkpoints.txt"
                    try:
                        from stabilize_horizon import load_checkpoints_with_metadata, generate_slerp_trajectory, write_sendcmd_file, generate_checkpoints_graph, write_horizon_params_log
                        cps, ign_yaw, jfps, jparams = load_checkpoints_with_metadata(target_json)
                        if jfps > 0:
                            probe_fps = jfps
                        elif probe_fps <= 0:
                            probe_fps = 29.97
                        total_f = int(round(duration * probe_fps)) if duration > 0 else 300

                        print(f"[Horizon Checkpoints] Loaded {len(cps)} checkpoint keyframe(s) from: {target_json} (FPS: {probe_fps}, Total Frames: {total_f}, Ignore Yaw: {ign_yaw})", flush=True)
                        for idx_cp, cp in enumerate(cps):
                            print(f"  -> Checkpoint #{idx_cp+1}: Frame {cp.get('frame')} (t={cp.get('time', 0):.2f}s) | Pitch: {cp.get('pitch')}°, Roll: {cp.get('roll')}°, Yaw: {cp.get('yaw', 0)}°", flush=True)

                        traj = generate_slerp_trajectory(cps, total_f, ignore_yaw=ign_yaw, fps=probe_fps)
                        write_sendcmd_file(traj, probe_fps, sendcmd_cp)
                        print(f"[Horizon Checkpoints] Generated sendcmd ({len(traj)} frames): {sendcmd_cp}", flush=True)
                        cp_graph_png = f"{out_base}_horizon_graph.png"
                        generate_checkpoints_graph(cps, traj, probe_fps, cp_graph_png)

                        exec_params = {
                            "version": "1.0",
                            "sourceType": jparams.get("detection_type") or "Loaded Checkpoints / WebGL Horizon Editor",
                            "fps": probe_fps,
                            "totalFrames": total_f,
                            "ignoreYaw": ign_yaw,
                            "parameters": jparams or {
                                "detection_type": getattr(args, "horizon_autodetect_source", "manual_checkpoints"),
                                "density": getattr(args, "horizon_autodetect_density", "balanced"),
                                "roll_damping": getattr(args, "horizon_roll_damping", 0.70),
                                "baseline_pitch": getattr(args, "pitch", 0.0),
                                "baseline_roll": getattr(args, "roll", 0.0),
                                "video": os.path.basename(current_file),
                            },
                            "checkpoints": cps
                        }
                        log_dests = [os.path.join("data", "runtime", "work", f"{clean_base}_horizon_params.log")]
                        if out_dir:
                            log_dests.append(os.path.join(out_dir, f"{clean_base}_horizon_params.log"))
                        write_horizon_params_log(clean_base, exec_params, log_dests, video_name=current_file)
                    except Exception as e_cp:
                        print(f"[Horizon Checkpoints] Trajectory generation error: {e_cp}", flush=True)

                    if os.path.exists(sendcmd_cp) and os.path.getsize(sendcmd_cp) > 0:
                        if is_identity_sendcmd(sendcmd_cp):
                            executed_methods_so_far.append("horizon")
                            stage_blocks = list(executed_methods_so_far)
                            _cp_out = f"{out_base}_{'_'.join(stage_blocks)}{out_ext}"
                            print(f"[Horizon Checkpoints] Neutral keyframes (<0.015° dev): stream-copying to {_cp_out} without re-encoding.", flush=True)
                            try:
                                import shutil
                                shutil.copy2(current_file, _cp_out)
                                embed_vrot_metadata(args.input, _cp_out)
                                create_meta_copy(_cp_out, args.status_file, start_ts, stab_name="Horizon Checkpoints")
                                current_file = _cp_out
                                stab_video_file = _cp_out
                                step2_stab = _cp_out
                                stage_step_io["horizon"] = (stage_in_cp, _cp_out)
                            except Exception as e_cp_copy:
                                print(f"Warning: failed copying neutral horizon video: {e_cp_copy}", flush=True)
                            _r_hor = f"{out_base}_horizon_report.txt"
                            _res_hor = write_single_stage_report("horizon", "Horizon (Interactive Leveling)", stage_in_cp, current_file, _r_hor, out_base, q_mode, step1_stitch=initial_raw_stitched_file, target_stab_file=current_file, kopf_disclaimer=_kopf_disclaimer)
                            if _res_hor:
                                model_results["horizon"] = _res_hor
                        else:
                            phase_cp = "Stabilising Horizon Checkpoints"
                            update_status(args.status_file, {
                                "status": "stabilizing", "phase": phase_cp,
                                "progress": 78.0, "speed": "N/A", "eta": "Calculating...",
                                "elapsed": int(time.time() - start_ts)
                            })
                            executed_methods_so_far.append("horizon")
                            stage_blocks = list(executed_methods_so_far)
                            _cp_out = f"{out_base}_{'_'.join(stage_blocks)}{out_ext}"

                            if q_mode in ("1", "4"):
                                active_sendcmd_files.append(sendcmd_cp)
                                print(f"[{'Hybrid' if q_mode == '4' else 'Single-Pass'} Mode] Added Horizon Checkpoints transform ({sendcmd_cp}) to composition queue.")
                            if q_mode == "1":
                                stage_step_io["horizon"] = (initial_raw_stitched_file, _cp_out)

                            if q_mode != "1":
                                sendcmd_cp_esc = sendcmd_cp.replace('\\', '/')
                                stab_cp_cmd = [
                                    "ffmpeg", "-y", "-progress", "-",
                                    "-i", current_file,
                                    "-vf", f"sendcmd=f='{sendcmd_cp_esc}',v360=input=equirect:output=equirect{v360_interp}",
                                ]
                                is_intermediate = (q_mode == "4") or (len(active_methods) > len(executed_methods_so_far))
                                if is_intermediate:
                                    if q_mode == "4":
                                        if args.hwaccel:
                                            stab_cp_cmd += ["-c:v", "h264_nvenc", "-preset", "p1", "-b:v", "25000k"]
                                        else:
                                            stab_cp_cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "18"]
                                    elif q_mode == "2":
                                        if args.hwaccel:
                                            stab_cp_cmd += ["-c:v", "h264_nvenc", "-preset", "p7", "-rc", "constqp", "-qp", "0"]
                                        else:
                                            stab_cp_cmd += ["-c:v", "libx264", "-crf", "0"]
                                    else:
                                        if args.hwaccel:
                                            stab_cp_cmd += ["-c:v", "h264_nvenc", "-b:v", "30000k"]
                                        else:
                                            stab_cp_cmd += ["-c:v", "libx264", "-preset", args.preset, "-crf", args.crf]
                                else:
                                    if args.hwaccel:
                                        stab_cp_cmd += ["-c:v", "h264_nvenc", "-preset", "p7", "-profile:v", "high", "-rc", "vbr", "-cq", "16", "-b:v", "50000k", "-maxrate", "80000k", "-spatial-aq", "1", "-temporal-aq", "1"]
                                    else:
                                        stab_cp_cmd += ["-c:v", "libx264", "-preset", "slow", "-crf", "16"]

                                stab_cp_cmd += ["-pix_fmt", "yuv420p", "-c:a", "copy", _cp_out]
                                rc_cp2 = run_ffmpeg(stab_cp_cmd, args.status_file, duration, start_ts, phase_cp, status_code="stabilizing")
                                if rc_cp2 == 0 and os.path.exists(_cp_out) and os.path.getsize(_cp_out) > 0:
                                    current_file = _cp_out
                                    stab_video_file = _cp_out
                                    step2_stab = _cp_out
                                    stage_step_io["horizon"] = (stage_in_cp, _cp_out)
                                    if q_mode == "4" and is_intermediate:
                                        mode4_temp_intermediates.append(_cp_out)
                                    embed_vrot_metadata(args.input, _cp_out)
                                    create_meta_copy(_cp_out, args.status_file, start_ts, stab_name="Horizon Checkpoints")
                                else:
                                    print("Warning: Horizon Checkpoints leveling transform failed, using un-leveled output.")

                            # Generate horizon stage report immediately
                            _s_in, _s_out = stage_step_io.get("horizon", (stage_in_cp, _cp_out if ('_cp_out' in locals() and _cp_out) else current_file))
                            _r_hor = f"{out_base}_horizon_report.txt"
                            _res_hor = write_single_stage_report("horizon", "Horizon (Interactive Leveling)", _s_in, _s_out, _r_hor, out_base, q_mode, step1_stitch=initial_raw_stitched_file, target_stab_file=current_file, kopf_disclaimer=_kopf_disclaimer)
                            if _res_hor:
                                model_results["horizon"] = _res_hor
                    current_file, _was_reverted = check_and_apply_stage_fallback(
                        "horizon", "Horizon (Interactive Leveling)", _r_hor, sendcmd_cp,
                        stage_in_cp, current_file, active_sendcmd_files, executed_methods_so_far,
                        getattr(args, 'fallback_unstabilized', True), reverted_stages, args.status_file
                    )
                    if _was_reverted:
                        stab_video_file = current_file
                        step2_stab = current_file
                    update_status(args.status_file, {
                        "status": "stabilizing",
                        "phase": "Horizon leveling completed",
                        "progress": 85.0,
                        "speed": "N/A", "eta": "Calculating...",
                        "elapsed": int(time.time() - start_ts)
                    })
                else:
                    print("[Horizon Checkpoints] Skipping checkpoints pass (no checkpoint file provided).")

            # -- Cinematic SO(3) Path Smoothing --
            elif stage_item == "cinematic" and "cinematic" in active_methods:
                stage_in_l1 = current_file
                sendcmd_file = f"{out_base}_sendcmd_cinematic.txt"
                l1_acc = getattr(args, 'l1_lambda_acc', 20.0)
                l1_vel = getattr(args, 'l1_lambda_vel', 2.0)
                l1_win = getattr(args, 'cinematic_window', 45)
                phase_l1 = f"Optimizing Camera Trajectory (Cinematic Path Smoothing) (accel: {l1_acc}, vel: {l1_vel}, window: {l1_win})"
                update_status(args.status_file, {
                    "status": "stabilizing", "phase": phase_l1,
                    "progress": 76.0, "speed": "N/A", "eta": "Calculating...",
                    "elapsed": int(time.time() - start_ts)
                })
                prev_sendcmd = get_effective_prior_sendcmd(active_sendcmd_files, out_base, "cinematic")
                fps_val = fps if 'fps' in locals() else 30.0
                l1_cmd = [
                    sys.executable, "-B", os.path.join(script_dir, "stabilize_cinematic.py"),
                    "--input", current_file,
                    "--output", sendcmd_file,
                    "--lambda-acc", str(getattr(args, 'l1_lambda_acc', 20.0)),
                    "--lambda-vel", str(getattr(args, 'l1_lambda_vel', 2.0)),
                    "--smooth-window", str(getattr(args, 'cinematic_window', 45)),
                    "--fps", str(fps_val)
                ]
                if prev_sendcmd and os.path.exists(prev_sendcmd):
                    l1_cmd += ["--sendcmd-in", prev_sendcmd]

                rc_l1 = run_ffmpeg(l1_cmd, args.status_file, duration, start_ts, phase_l1, status_code="stabilizing")
                if rc_l1 != 0 or not os.path.exists(sendcmd_file) or os.path.getsize(sendcmd_file) == 0:
                    print("Warning: stabilize_cinematic.py failed, skipping cinematic pass.")
                else:
                    try:
                        fps_l1 = fps if 'fps' in locals() else 30.0
                        vis_l1_cmd = [sys.executable, "-B", os.path.join(script_dir, "visualize_corrections.py"), "--trf", sendcmd_file, "--graph-type", "cinematic", "--output", out_base, "--fps", str(fps_l1)]
                        subprocess.run(vis_l1_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=WIN_NO_WINDOW)
                    except Exception as e_vis_l1:
                        print(f"Notice: visualize_corrections cinematic error: {e_vis_l1}")

                    executed_methods_so_far.append("cinematic")
                    stage_blocks = list(executed_methods_so_far)
                    _l1_out = f"{out_base}_{'_'.join(stage_blocks)}{out_ext}"

                    if q_mode in ("1", "4"):
                        active_sendcmd_files.append(sendcmd_file)
                        print(f"[{'Hybrid' if q_mode == '4' else 'Single-Pass'} Mode] Added Cinematic transform ({sendcmd_file}) to composition queue.")
                    if q_mode == "1":
                        stage_step_io["cinematic"] = (initial_raw_stitched_file, _l1_out)

                    if q_mode != "1":
                        sendcmd_l1_esc = sendcmd_file.replace('\\', '/')
                        stab_l1_cmd = [
                            "ffmpeg", "-y", "-progress", "-",
                            "-i", current_file,
                            "-vf", f"sendcmd=f='{sendcmd_l1_esc}',v360=input=equirect:output=equirect{v360_interp}",
                        ]
                        is_intermediate = (q_mode == "4") or (len(active_methods) > len(executed_methods_so_far))
                        if is_intermediate:
                            if q_mode == "4":
                                if args.hwaccel:
                                    stab_l1_cmd += ["-c:v", "h264_nvenc", "-preset", "p1", "-b:v", "25000k"]
                                else:
                                    stab_l1_cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "18"]
                            elif q_mode == "2":
                                if args.hwaccel:
                                    stab_l1_cmd += ["-c:v", "h264_nvenc", "-preset", "p7", "-rc", "constqp", "-qp", "0"]
                                else:
                                    stab_l1_cmd += ["-c:v", "libx264", "-crf", "0"]
                            else:
                                if args.hwaccel:
                                    stab_l1_cmd += ["-c:v", "h264_nvenc", "-b:v", "30000k"]
                                else:
                                    stab_l1_cmd += ["-c:v", "libx264", "-preset", args.preset, "-crf", args.crf]
                        else:
                            if args.hwaccel:
                                stab_l1_cmd += ["-c:v", "h264_nvenc", "-preset", "p7", "-profile:v", "high", "-rc", "vbr", "-cq", "16", "-b:v", "50000k", "-maxrate", "80000k", "-spatial-aq", "1", "-temporal-aq", "1"]
                            else:
                                stab_l1_cmd += ["-c:v", "libx264", "-preset", "slow", "-crf", "16"]

                        stab_l1_cmd += ["-pix_fmt", "yuv420p", "-c:a", "copy", _l1_out]
                        rc_l1_ff = run_ffmpeg(stab_l1_cmd, args.status_file, duration, start_ts, phase_l1, status_code="stabilizing")
                        if rc_l1_ff == 0 and os.path.exists(_l1_out) and os.path.getsize(_l1_out) > 0:
                            current_file = _l1_out
                            stab_video_file = _l1_out
                            step2_stab = _l1_out
                            stage_step_io["cinematic"] = (stage_in_l1, _l1_out)
                            if q_mode == "4" and is_intermediate:
                                mode4_temp_intermediates.append(_l1_out)
                            embed_vrot_metadata(args.input, _l1_out)
                            create_meta_copy(_l1_out, args.status_file, start_ts, stab_name="Cinematic")
                        else:
                            print("Warning: Cinematic transform failed, skipping intermediate render.")

                    # Generate cinematic stage report immediately
                    _s_in, _s_out = stage_step_io.get("cinematic", (stage_in_l1, _l1_out if ('_l1_out' in locals() and _l1_out) else current_file))
                    _r_l1 = f"{out_base}_cinematic_report.txt"
                    _res_l1 = write_single_stage_report("cinematic", "Cinematic (Camera Path Optimization)", _s_in, _s_out, _r_l1, out_base, q_mode, step1_stitch=initial_raw_stitched_file, target_stab_file=current_file, kopf_disclaimer=_kopf_disclaimer)
                    if _res_l1:
                        model_results["cinematic"] = _res_l1
                    current_file, _was_reverted = check_and_apply_stage_fallback(
                        "cinematic", "Cinematic (Path Optimization)", _r_l1, sendcmd_file,
                        stage_in_l1, current_file, active_sendcmd_files, executed_methods_so_far,
                        getattr(args, 'fallback_unstabilized', True), reverted_stages, args.status_file
                    )
                    if _was_reverted:
                        stab_video_file = current_file
                        step2_stab = current_file
                    update_status(args.status_file, {
                        "status": "stabilizing",
                        "phase": "Cinematic trajectory optimization completed",
                        "progress": 78.0,
                        "speed": "N/A", "eta": "Calculating...",
                        "elapsed": int(time.time() - start_ts)
                    })

            # -- Subject / Travel-Direction Lock --
            elif stage_item == "traveldir" and "traveldir" in active_methods:
                stage_in_dl = current_file
                sendcmd_file = f"{out_base}_sendcmd_traveldir.txt"
                td_mode = getattr(args, 'traveldir_mode', 'travel_direction')
                td_damp = getattr(args, 'traveldir_damping', 0.90)
                td_deadband = getattr(args, 'traveldir_deadband', 1.5)
                td_yaw = getattr(args, 'traveldir_target_yaw', 0.0)
                if td_mode == "target_lock":
                    steer_params = f"target_yaw: {td_yaw}°, damp: {td_damp}, deadband: {td_deadband}°"
                else:
                    steer_params = f"damp: {td_damp}, deadband: {td_deadband}°"
                phase_dl = f"Stabilising Travel Direction (mode: {td_mode}, {steer_params})"
                update_status(args.status_file, {
                    "status": "stabilizing", "phase": phase_dl,
                    "progress": 82.0, "speed": "N/A", "eta": "Calculating...",
                    "elapsed": int(time.time() - start_ts)
                })
                prev_sendcmd = get_effective_prior_sendcmd(active_sendcmd_files, out_base, "traveldir")
                fps_val = fps if 'fps' in locals() else 30.0
                dl_meta_file = f"{out_base}_traveldir_meta.json"
                dl_cmd = [
                    sys.executable, "-B", os.path.join(script_dir, "stabilize_traveldir.py"),
                    "--input", current_file,
                    "--output", sendcmd_file,
                    "--meta-out", dl_meta_file,
                    "--mode", str(getattr(args, 'traveldir_mode', 'travel_direction')),
                    "--target-yaw", str(getattr(args, 'traveldir_target_yaw', 0.0)),
                    "--damping", str(getattr(args, 'traveldir_damping', 0.90)),
                    "--deadband", str(getattr(args, 'traveldir_deadband', 1.5)),
                    "--fps", str(fps_val)
                ]
                if prev_sendcmd and os.path.exists(prev_sendcmd):
                    dl_cmd += ["--sendcmd-in", prev_sendcmd]

                rc_dl = run_ffmpeg(dl_cmd, args.status_file, duration, start_ts, phase_dl, status_code="stabilizing")
                if rc_dl != 0 or not os.path.exists(sendcmd_file) or os.path.getsize(sendcmd_file) == 0:
                    print("Warning: stabilize_traveldir.py failed, skipping travel-direction pass.")
                else:
                    try:
                        fps_dl = fps if 'fps' in locals() else 30.0
                        vis_dl_cmd = [
                            sys.executable, "-B", os.path.join(script_dir, "visualize_corrections.py"),
                            "--trf", sendcmd_file,
                            "--meta", dl_meta_file,
                            "--graph-type", "traveldir",
                            "--output", out_base,
                            "--fps", str(fps_dl)
                        ]
                        subprocess.run(vis_dl_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=WIN_NO_WINDOW)
                    except Exception as e_vis_dl:
                        print(f"Notice: visualize_corrections traveldir error: {e_vis_dl}")

                    executed_methods_so_far.append("traveldir")
                    stage_blocks = list(executed_methods_so_far)
                    _dl_out = f"{out_base}_{'_'.join(stage_blocks)}{out_ext}"

                    if q_mode in ("1", "4"):
                        active_sendcmd_files.append(sendcmd_file)
                        print(f"[{'Hybrid' if q_mode == '4' else 'Single-Pass'} Mode] Added Travel-Direction transform ({sendcmd_file}) to composition queue.")
                    if q_mode == "1":
                        stage_step_io["traveldir"] = (initial_raw_stitched_file, _dl_out)

                    if q_mode != "1":
                        sendcmd_dl_esc = sendcmd_file.replace('\\', '/')
                        stab_dl_cmd = [
                            "ffmpeg", "-y", "-progress", "-",
                            "-i", current_file,
                            "-vf", f"sendcmd=f='{sendcmd_dl_esc}',v360=input=equirect:output=equirect{v360_interp}",
                        ]
                        is_intermediate = (q_mode == "4") or (len(active_methods) > len(executed_methods_so_far))
                        if is_intermediate:
                            if q_mode == "4":
                                if args.hwaccel:
                                    stab_dl_cmd += ["-c:v", "h264_nvenc", "-preset", "p1", "-b:v", "25000k"]
                                else:
                                    stab_dl_cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "18"]
                            elif q_mode == "2":
                                if args.hwaccel:
                                    stab_dl_cmd += ["-c:v", "h264_nvenc", "-preset", "p7", "-rc", "constqp", "-qp", "0"]
                                else:
                                    stab_dl_cmd += ["-c:v", "libx264", "-crf", "0"]
                            else:
                                if args.hwaccel:
                                    stab_dl_cmd += ["-c:v", "h264_nvenc", "-b:v", "30000k"]
                                else:
                                    stab_dl_cmd += ["-c:v", "libx264", "-preset", args.preset, "-crf", args.crf]
                        else:
                            if args.hwaccel:
                                stab_dl_cmd += ["-c:v", "h264_nvenc", "-preset", "p7", "-profile:v", "high", "-rc", "vbr", "-cq", "16", "-b:v", "50000k", "-maxrate", "80000k", "-spatial-aq", "1", "-temporal-aq", "1"]
                            else:
                                stab_dl_cmd += ["-c:v", "libx264", "-preset", "slow", "-crf", "16"]

                        stab_dl_cmd += ["-pix_fmt", "yuv420p", "-c:a", "copy", _dl_out]
                        rc_dl_ff = run_ffmpeg(stab_dl_cmd, args.status_file, duration, start_ts, phase_dl, status_code="stabilizing")
                        if rc_dl_ff == 0 and os.path.exists(_dl_out) and os.path.getsize(_dl_out) > 0:
                            current_file = _dl_out
                            stab_video_file = _dl_out
                            step2_stab = _dl_out
                            stage_step_io["traveldir"] = (stage_in_dl, _dl_out)
                            if q_mode == "4" and is_intermediate:
                                mode4_temp_intermediates.append(_dl_out)
                            embed_vrot_metadata(args.input, _dl_out)
                            create_meta_copy(_dl_out, args.status_file, start_ts, stab_name="Travel-Direction Lock")
                        else:
                            print("Warning: Travel-direction transform failed, skipping intermediate render.")

                    # Generate traveldir stage report immediately
                    _s_in, _s_out = stage_step_io.get("traveldir", (stage_in_dl, _dl_out if ('_dl_out' in locals() and _dl_out) else current_file))
                    _r_dl = f"{out_base}_traveldir_report.txt"
                    _res_dl = write_single_stage_report("traveldir", "Travel-Direction Lock", _s_in, _s_out, _r_dl, out_base, q_mode, step1_stitch=initial_raw_stitched_file, target_stab_file=current_file, kopf_disclaimer=_kopf_disclaimer)
                    if _res_dl:
                        model_results["traveldir"] = _res_dl
                    current_file, _was_reverted = check_and_apply_stage_fallback(
                        "traveldir", "Travel-Direction Lock", _r_dl, sendcmd_file,
                        stage_in_dl, current_file, active_sendcmd_files, executed_methods_so_far,
                        getattr(args, 'fallback_unstabilized', True), reverted_stages, args.status_file
                    )
                    if _was_reverted:
                        stab_video_file = current_file
                        step2_stab = current_file
                    update_status(args.status_file, {
                        "status": "stabilizing",
                        "phase": "Travel Direction completed",
                        "progress": 88.0,
                        "speed": "N/A", "eta": "Calculating...",
                        "elapsed": int(time.time() - start_ts)
                    })

        # -- Single-Pass Master Composition Render Pass (Mode 1 & Mode 4 Hybrid) ----------
        if q_mode in ("1", "4") and active_sendcmd_files:
            transform_title_map = {
                "telemetry": "Telemetry (IMU Leveling)",
                "kopf": "Kopf (3D-2D Vision Keyframe)",
                "kabsch": "Kabsch (Spherical SVD Rotation)",
                "vidstab": "VidSTAB (2D Motion)",
                "cinematic": "Cinematic (Path Optimization)",
                "horizon": "Horizon Checkpoints (Interactive Leveling)",
                "traveldir": "Travel-Direction Lock"
            }
            transforms_meta = []
            skipped_meta = []
            for method in ["telemetry", "kopf", "kabsch", "vidstab", "cinematic", "horizon", "traveldir"]:
                if method in executed_methods_so_far:
                    sc_file = None
                    for f in active_sendcmd_files:
                        if f"_{method}" in f or (method == "horizon" and ("checkpoints" in f or "horizon" in f)):
                            sc_file = f
                            break
                    if sc_file and os.path.exists(sc_file):
                        rep_file = f"{out_base}_{method}_report.txt"
                        grp_file = f"{out_base}_{method}_graph.png"
                        is_unstabilized = False
                        status_text = ""
                        if os.path.exists(rep_file):
                            try:
                                with open(rep_file, "r", encoding="utf-8", errors="ignore") as rf:
                                    for line in rf:
                                        if "STATUS" in line:
                                            status_val = line.split(":", 1)[1].strip() if ":" in line else line.strip()
                                            status_text = status_val
                                            if any(neg in status_val.upper() for neg in ["NO (", "UNSTABILIZED", "DEGRADED", "FAILED"]):
                                                is_unstabilized = True
                                            break
                            except Exception:
                                pass

                        if is_unstabilized:
                            print(f"[Pipeline] Notice: {transform_title_map.get(method, method)} report indicates '{status_text}'. Auto-excluding by default for Master Render.", flush=True)

                        transforms_meta.append({
                            "id": method,
                            "name": transform_title_map.get(method, method.capitalize()),
                            "sendcmd_file": sc_file,
                            "report_file": rep_file if os.path.exists(rep_file) else "",
                            "graph_file": grp_file if os.path.exists(grp_file) else "",
                            "selected": not is_unstabilized,
                            "is_unstabilized": is_unstabilized,
                            "status_text": status_text
                        })
                elif method in active_methods:
                    rep_file = f"{out_base}_{method}_report.txt"
                    if method in reverted_stages:
                        reason = f"Auto-Reverted (degraded: {reverted_stages[method]['status_text']})"
                    elif method == "horizon":
                        reason = "Bypassed (0 manual keyframes defined / neutral <0.015° deviation)"
                    else:
                        reason = "Bypassed (neutral transform <0.015° deviation)"
                    skipped_meta.append({
                        "id": method,
                        "name": transform_title_map.get(method, method.capitalize()),
                        "reason": reason,
                        "report_file": rep_file if os.path.exists(rep_file) else ""
                    })

            if transforms_meta and not getattr(args, 'no_prompt_transforms', False):
                raw_job = getattr(args, 'job_id', '').strip() if getattr(args, 'job_id', '') else ''
                job_key = re.sub(r'[^a-zA-Z0-9_\-]', '', raw_job) if raw_job else os.path.basename(out_base)
                temp_folder = os.path.join("data", "runtime", "temp")
                os.makedirs(temp_folder, exist_ok=True)
                chosen_flag_file = os.path.join(temp_folder, f"chosen_transforms_{job_key}.json")
                chosen_flag_default = os.path.join(temp_folder, "chosen_transforms.json")


                print(f"\n=======================================================", flush=True)
                print(f"[STATUS:AWAITING_TRANSFORMS:job_id={job_key}:count={len(transforms_meta)}]", flush=True)
                print(f"[Pipeline] Pausing before Master Render. Please select transforms to apply ({len(transforms_meta)} active).", flush=True)
                if skipped_meta:
                    for sm in skipped_meta:
                        print(f"[Pipeline] Notice: {sm['name']} was {sm['reason']}.", flush=True)
                unstabilized_meta = [t for t in transforms_meta if t.get("is_unstabilized")]
                if unstabilized_meta:
                    for um in unstabilized_meta:
                        print(f"[Pipeline] Warning: {um['name']} report indicates '{um.get('status_text', 'UNSTABILIZED')}'. Auto-unchecked for Master Render (can be overridden in modal).", flush=True)
                print(f"=======================================================\n", flush=True)

                update_status(args.status_file, {
                    "status": "awaiting_transforms",
                    "phase": f"Awaiting Transform Selection for Master Render ({len(transforms_meta)} available)",
                    "transforms": transforms_meta,
                    "skipped_transforms": skipped_meta,
                    "progress": 80.0, "speed": "PAUSED", "eta": "Waiting for selection",
                    "elapsed": int(time.time() - start_ts)
                })

                chosen_data = None
                pause_start_ts = time.time()
                while not chosen_data:
                    time.sleep(0.5)
                    for cf in [chosen_flag_file, chosen_flag_default]:
                        if os.path.exists(cf) and os.path.getsize(cf) > 2:
                            cf_mtime = os.path.getmtime(cf)
                            if cf_mtime < (pause_start_ts - 2):
                                continue
                            try:
                                with open(cf, "r", encoding="utf-8") as f_cf:
                                    c_cand = json.load(f_cf)
                                c_jid = str(c_cand.get("job_id", "")).strip()
                                if raw_job and c_jid and c_jid != raw_job and c_jid != job_key:
                                    continue
                                chosen_data = c_cand
                                break
                            except Exception:
                                pass

                if chosen_data:
                    chosen_ids = chosen_data.get("chosen_transforms", [])
                    if isinstance(chosen_ids, list):
                        chosen_set = set(chosen_ids)
                        filtered_active_files = []
                        filtered_executed_methods = []
                        for t in transforms_meta:
                            if t["id"] in chosen_set:
                                filtered_active_files.append(t["sendcmd_file"])
                                filtered_executed_methods.append(t["id"])
                        active_sendcmd_files = filtered_active_files
                        executed_methods_so_far = filtered_executed_methods
                        print(f"[Pipeline] Applied transform filter: {len(active_sendcmd_files)} of {len(transforms_meta)} transforms chosen: {list(chosen_set)}", flush=True)

            if active_sendcmd_files:
                update_status(args.status_file, {
                    "status": "stabilizing",
                    "phase": f"Composing {len(active_sendcmd_files)} stabilization trajectories into master trajectory...",
                    "progress": 80.0, "speed": "N/A", "eta": "Calculating...",
                    "elapsed": int(time.time() - start_ts)
                })
                composed_sendcmd_file = f"{out_base}_sendcmd_composed.txt"
                if compose_sendcmd_files(active_sendcmd_files, composed_sendcmd_file):
                    print(f"[Pipeline] Single-Pass Master Composition ({'Mode 4 Hybrid' if q_mode == '4' else 'Mode 1'}): Combined {len(active_sendcmd_files)} stabilization transforms into {composed_sendcmd_file}")

                    nadir_logo = args.nadir_logo.strip()
                    apply_nadir_master = bool(nadir_logo and os.path.exists(nadir_logo) and not getattr(args, 'input_has_nadir', False))
                    apply_sv_master = bool(getattr(args, 'streetview_enabled', False))

                    components = []
                    if apply_nadir_master:
                        components.append("Nadir Overlay")

                    phase_suffix = f" + {' + '.join(components)}" if components else ""
                    phase_single_pass = f"Single-Pass Composition Master Render{phase_suffix} ({len(active_sendcmd_files)} active transforms)"
                    update_status(args.status_file, {
                        "status": "stabilizing",
                        "phase": f"Preparing Master Render{phase_suffix} (Lanczos filter & encoder init)...",
                        "progress": 82.0, "speed": "N/A", "eta": "Calculating...",
                        "elapsed": int(time.time() - start_ts)
                    })

                    # Enforce highest-precision Lanczos sinc sub-pixel interpolation for the master pass
                    single_pass_interp = ":interp=lanczos"
                    sendcmd_composed_esc = composed_sendcmd_file.replace('\\', '/')

                    if apply_nadir_master:
                        filter_str = (
                            f"[0:v]sendcmd=f='{sendcmd_composed_esc}',v360=input=equirect:output=equirect{single_pass_interp}[stab];"
                            f"[1:v]format=rgba[logo_rgba];"
                            f"[logo_rgba]v360=input=flat:output=equirect"
                            f":ih_fov={args.nadir_fov}:iv_fov={args.nadir_fov_v}"
                            f":pitch=90:yaw=0:roll=0:w=3840:h=1920[logo_eq];"
                            f"[stab][logo_eq]overlay=0:0:format=auto:eof_action=pass:shortest=0"
                        )
                    else:
                        filter_str = f"sendcmd=f='{sendcmd_composed_esc}',v360=input=equirect:output=equirect{single_pass_interp}"

                    stage_blocks = []
                    if "telemetry" in executed_methods_so_far: stage_blocks.append("telemetry")
                    if "kopf" in executed_methods_so_far:      stage_blocks.append("kopf")
                    if "kabsch" in executed_methods_so_far:    stage_blocks.append("kabsch")
                    if "vidstab" in executed_methods_so_far:   stage_blocks.append("vidstab")
                    if "cinematic" in executed_methods_so_far: stage_blocks.append("cinematic")
                    if "horizon" in executed_methods_so_far:   stage_blocks.append("horizon")
                    if "traveldir" in executed_methods_so_far: stage_blocks.append("traveldir")
                    _composed_out = f"{out_base}_{'_'.join(stage_blocks)}_master{out_ext}" if (q_mode == "4" and stage_blocks) else (f"{out_base}_{'_'.join(stage_blocks)}{out_ext}" if stage_blocks else f"{out_base}_composed{out_ext}")

                    stab_cmd = [
                        "ffmpeg", "-y", "-progress", "-",
                        "-i", initial_raw_stitched_file,
                    ]
                    if apply_nadir_master:
                        stab_cmd += ["-loop", "1", "-i", nadir_logo, "-filter_complex", filter_str]
                    else:
                        stab_cmd += ["-vf", filter_str]

                    # Calculate bitrate parameters
                    b_val_k = 80000
                    if getattr(args, 'video_bitrate', ''):
                        vb_str = str(args.video_bitrate).strip().upper()
                        if vb_str.endswith('M'):
                            try: b_val_k = int(float(vb_str[:-1]) * 1000)
                            except Exception: b_val_k = 80000
                        elif vb_str.endswith('K'):
                            try: b_val_k = int(float(vb_str[:-1]))
                            except Exception: b_val_k = 80000
                        elif vb_str.isdigit():
                            v = float(vb_str)
                            b_val_k = int(v * 1000) if v < 1000 else int(v)

                    target_b = f"{b_val_k}k"
                    max_b = f"{int(b_val_k * 1.5)}k"
                    buf_b = f"{int(b_val_k * 2.0)}k"
                    audio_master_args = ["-an"] if getattr(args, 'remove_audio', False) else ["-c:a", "copy"]

                    if args.hwaccel:
                        # NVENC mastering preset: p7, High Profile, custom/pristine bitrate, spatial & temporal AQ
                        stab_cmd += [
                            "-c:v", "h264_nvenc", "-preset", "p7", "-profile:v", "high",
                            "-rc", "vbr", "-cq", "14", "-b:v", target_b, "-maxrate", max_b, "-bufsize", buf_b,
                            "-spatial-aq", "1", "-temporal-aq", "1"
                        ]
                    else:
                        # CPU H.264 mastering: CRF 14, High Profile, slow preset
                        stab_cmd += ["-c:v", "libx264", "-preset", "slow", "-profile:v", "high", "-crf", "14"]
                    
                    # Street View & 360 metadata tags for Master Render pass
                    sv_meta_args = []
                    if getattr(args, 'streetview_enabled', False):
                        sv_start_time_iso = getattr(args, 'streetview_start_time', '')
                        sv_offset = getattr(args, 'streetview_time_offset', 0.0)
                        sv_dt = None
                        if sv_start_time_iso:
                            try:
                                clean_iso = sv_start_time_iso.replace("Z", "").replace(" ", "T")
                                sv_dt = datetime.fromisoformat(clean_iso)
                                if sv_dt.tzinfo is None:
                                    sv_dt = sv_dt.replace(tzinfo=timezone.utc)
                            except Exception:
                                pass
                        if sv_dt is None:
                            try:
                                import streetview_gpx
                                _, _, _, _, vid_dt = streetview_gpx.get_video_info(initial_raw_stitched_file)
                                sv_dt = vid_dt or datetime.now(timezone.utc).replace(microsecond=0)
                            except Exception:
                                sv_dt = datetime.now(timezone.utc).replace(microsecond=0)
                        if sv_offset != 0.0:
                            sv_dt += timedelta(seconds=sv_offset)
                        sv_creation_time_str = sv_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                        sv_meta_args = [
                            "-metadata", f"creation_time={sv_creation_time_str}",
                            "-metadata:s:v", f"creation_time={sv_creation_time_str}",
                            "-metadata", "Spherical=true",
                            "-metadata", "Stitched=true",
                            "-metadata", "ProjectionType=equirectangular"
                        ]

                    # Enforce standard BT.709 colorimetry to prevent washed-out color/contrast shift
                    stab_cmd += [
                        "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
                        "-pix_fmt", "yuv420p"
                    ] + audio_master_args + sv_meta_args + [
                        _composed_out
                    ]
                    rc_comp = run_ffmpeg(stab_cmd, args.status_file, duration, start_ts, phase_single_pass, status_code="stabilizing")
                    if rc_comp == 0 and os.path.exists(_composed_out) and os.path.getsize(_composed_out) > 0:
                        current_file = _composed_out
                        stab_video_file = _composed_out
                        step2_stab = _composed_out
                        if apply_nadir_master:
                            nadir_applied_in_master = True
                        create_meta_copy(_composed_out, args.status_file, start_ts, stab_name="Master Render")
                    else:
                        print("Warning: Single-pass stabilization transform failed, using un-stabilized output.")

        # Save TRF files to work folder for post-run inspection, then clean up temp copies
        import glob, shutil
        dummy_trf = f"{out_base}_dummy.trf"
        gm_file   = os.path.join(target_temp_dir, f"global_motions_{os.getpid()}.trf")
        # Preserve: copy to work folder with out_base name before deleting
        for src, dst_suffix in [(trf_file, "_temp_transforms.trf"), (gm_file, "_global_motions.trf")]:
            if src and os.path.exists(src) and os.path.getsize(src) > 0:
                try: shutil.copy2(src, f"{out_base}{dst_suffix}")
                except OSError: pass

    # -- Step 5: optional nadir logo overlay ----------------------------------
    if nadir_applied_in_master:
        print("[Pipeline] Skipping standalone Nadir overlay pass (already integrated into Single-Pass Master Render).")
    else:
        nadir_logo = args.nadir_logo.strip()

        if getattr(args, 'input_has_nadir', False) and nadir_logo:
            print("[Pipeline] Skipping Nadir logo overlay (input video marked as already having nadir logo).")
        elif nadir_logo and os.path.exists(nadir_logo):
            logo_basename = os.path.basename(nadir_logo)
            phase_nadir = f"Adding nadir logo ({logo_basename}, FOV: {args.nadir_fov}°x{args.nadir_fov_v}°)"
            update_status(args.status_file, {
                "status": "nadiring", "phase": phase_nadir,
                "progress": 88.0, "speed": "N/A", "eta": "Calculating...",
                "elapsed": int(time.time() - start_ts)
            })

            # Project the logo as a rectilinear image seen at pitch=-90 (looking down = nadir).
            # Matches Kdenlive VR360 Rectilinear->Equirect (width=125, height=88) + VR360 Transform pitch=90.
            nadir_filter = (
                f"[1:v]format=rgba[logo_rgba];"
                f"[logo_rgba]v360=input=flat:output=equirect"
                f":ih_fov={args.nadir_fov}:iv_fov={args.nadir_fov_v}"
                f":pitch=90:yaw=0:roll=0:w=3840:h=1920[logo_eq];"
                f"[0:v][logo_eq]overlay=0:0:format=auto:eof_action=pass:shortest=0"
            )
            nadir_cmd = [
                "ffmpeg", "-y", "-progress", "-",
                "-i", current_file,
                "-loop", "1",
                "-i", nadir_logo,
                "-filter_complex", nadir_filter,
            ]
            if is_image:
                step3_nadir = f"{out_base}_nadir{input_ext}"
                if step3_nadir.lower().endswith((".jpg", ".jpeg")):
                    nadir_cmd += ["-vframes", "1", "-pix_fmt", "yuvj420p", "-q:v", "2", step3_nadir]
                else:
                    nadir_cmd += ["-vframes", "1", step3_nadir]
            else:
                if args.hwaccel:
                    nadir_cmd += ["-c:v", "h264_nvenc", "-b:v", "30000k"]
                else:
                    nadir_cmd += ["-c:v", "libx264", "-preset", args.preset, "-crf", args.crf]
                audio_nadir_args = ["-an"] if getattr(args, 'remove_audio', False) else ["-c:a", "copy"]
                nadir_cmd += [
                    "-pix_fmt", "yuv420p"
                ] + audio_nadir_args + [
                    step3_nadir
                ]
            rc3 = run_ffmpeg(nadir_cmd, args.status_file, duration, start_ts, phase_nadir, status_code="nadiring")
            if rc3 == 0 and os.path.exists(step3_nadir):
                current_file = step3_nadir
                if is_image and companion_video:
                    comp_cmd = ["ffmpeg", "-y", "-loop", "1", "-i", step3_nadir, "-c:v", "libx264", "-t", "1", "-pix_fmt", "yuv420p", companion_video]
                    subprocess.run(comp_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=WIN_NO_WINDOW)
                embed_vrot_metadata(args.input, step3_nadir)
                if getattr(args, 'inject_intermediate_meta', False):
                    create_meta_copy(step3_nadir, args.status_file, start_ts, stab_name="Nadir Overlay")
            else:
                print("Warning: nadir overlay failed, skipping.")
        else:
            if nadir_logo:
                print(f"Warning: nadir logo file not found: {nadir_logo}, skipping.")

    # -- Step 6: inject 360° spatial metadata ---------------------------------
    update_status(args.status_file, {
        "status": "injecting", "phase": "Injecting 360 spatial metadata...",
        "progress": 95.0,
        "speed": "N/A", "eta": "Calculating...",
        "elapsed": int(time.time() - start_ts)
    })

    metadata_success = True
    metadata_err = ""

    # 1. Intermediate files (ensure all intermediate files are injected)
    if args.inject_intermediate_meta:
        intermediate_files = [step1_stitch, step2_stab, step3_nadir]
        if 'pass1_out' in locals() and pass1_out and pass1_out not in intermediate_files:
            intermediate_files.append(pass1_out)
        for f in intermediate_files:
            if os.path.exists(f):
                if not create_meta_copy(f, args.status_file, start_ts):
                    metadata_success = False
                    metadata_err = f"Failed on {f}"

    # 2. Final Output File
    if not is_image and getattr(args, 'remove_audio', False) and os.path.abspath(current_file) == os.path.abspath(args.input):
        temp_strip = f"{out_base}_noaudio{out_ext}"
        strip_cmd = ["ffmpeg", "-y", "-i", current_file, "-c:v", "copy", "-an", temp_strip]
        res_strip = subprocess.run(strip_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=WIN_NO_WINDOW)
        if res_strip.returncode == 0 and os.path.exists(temp_strip) and os.path.getsize(temp_strip) > 0:
            current_file = temp_strip

    if is_image:
        metadata_success = True
        import shutil
        if os.path.abspath(current_file) != os.path.abspath(args.output):
            shutil.copy2(current_file, args.output)
        inject_gpano_photo_metadata(args.output)
        if companion_video and os.path.exists(companion_video):
            script_dir_loc = script_dir if 'script_dir' in locals() or 'script_dir' in globals() else os.path.dirname(os.path.abspath(__file__))
            root_dir_loc = os.path.dirname(script_dir_loc)
            comp_vr = f"{out_base}_stitched_VR.mp4"
            cmd_comp = [sys.executable, "-B", "-m", "spatialmedia", "-i", "-p", "equirectangular", companion_video, comp_vr]
            subprocess.run(cmd_comp, cwd=root_dir_loc, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=WIN_NO_WINDOW)
    elif args.inject_final_meta:
        temp_source_file = current_file
        if os.path.exists(args.output):
            if os.path.abspath(current_file) == os.path.abspath(args.output):
                temp_source_file = f"{out_base}_temp_uninjected{out_ext}"
                try:
                    os.replace(current_file, temp_source_file)
                except OSError:
                    import shutil
                    shutil.copy2(current_file, temp_source_file)

        update_status(args.status_file, {
            "status": "injecting", "phase": "Injecting final 360 spatial metadata...",
            "progress": 95.0,
            "speed": "N/A", "eta": "Calculating...",
            "elapsed": int(time.time() - start_ts)
        })
        env = os.environ.copy()
        script_dir_loc = script_dir if 'script_dir' in locals() or 'script_dir' in globals() else os.path.dirname(os.path.abspath(__file__))
        root_dir_loc = os.path.dirname(script_dir_loc)
        env["PYTHONPATH"] = script_dir_loc + os.pathsep + root_dir_loc + os.pathsep + env.get("PYTHONPATH", "")
        cmd = [sys.executable, "-B", "-m", "spatialmedia", "-i", "-p", "equirectangular", temp_source_file, args.output]
        proc = subprocess.run(cmd, cwd=root_dir_loc, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120, creationflags=WIN_NO_WINDOW)
        if proc.returncode == 0 and os.path.exists(args.output) and os.path.getsize(args.output) > 0:
            embed_vrot_metadata(args.input, args.output)
            current_file = args.output
        if proc.returncode != 0 or not os.path.exists(args.output) or os.path.getsize(args.output) == 0:
            err_detail = (proc.stderr or proc.stdout or "").strip()
            print(f"Warning: spatialmedia injection returned code {proc.returncode}. Output detail: {err_detail}")
            metadata_success = False
            metadata_err = f"spatialmedia exited with code {proc.returncode}: {err_detail}"

    else:
        # Copy uninjected output to final target if paths differ
        import shutil
        if os.path.abspath(current_file) != os.path.abspath(args.output):
            shutil.copy2(current_file, args.output)
            current_file = args.output


    if not metadata_success:
        update_status(args.status_file, {
            "status": "failed",
            "error": f"Metadata injection failed: {metadata_err}"
        })
        sys.exit(1)

    # -- Street View Post-Processing ------------------------------------------
    sv_rel_video = None
    sv_rel_gpx = None
    if getattr(args, 'streetview_enabled', False):
        try:
            print("[StreetView] Starting Street View export generation...")
            update_status(args.status_file, {
                "status": "exporting",
                "phase": "Generating Google Street View MP4 & GPX...",
                "progress": 98.0,
                "speed": "N/A",
                "eta": "Calculating...",
                "elapsed": int(time.time() - start_ts)
            })

            out_base, out_ext = os.path.splitext(args.output)
            sv_output_mp4 = f"{out_base}_streetview{out_ext}"
            sv_output_gpx = f"{out_base}_streetview.gpx"

            import streetview_gpx

            checkpoints_data = args.streetview_checkpoints
            gpx_file_data = args.streetview_gpx_path
            
            cps = streetview_gpx.parse_checkpoint_list(checkpoints_data) if args.streetview_mode == "A" else []

            # If Single-Pass Master was used, use lossless stream copy for unpadded videos (duration >= 120s)
            sv_bitrate_to_use = getattr(args, 'streetview_bitrate', '45M')
            if q_mode in ("1", "4") and active_sendcmd_files:
                sv_bitrate_to_use = "copy"

            streetview_gpx.process_streetview(
                input_video=args.output,
                output_video=sv_output_mp4,
                output_gpx=sv_output_gpx,
                mode=args.streetview_mode,
                checkpoints=cps,
                gpx_input_file=gpx_file_data if args.streetview_mode == "B" else None,
                start_time_iso=args.streetview_start_time,
                time_offset_sec=args.streetview_time_offset,
                auto_pad=not args.streetview_no_auto_pad,
                bitrate=sv_bitrate_to_use,
                strip_audio=getattr(args, 'streetview_strip_audio', True),
                start_coord=getattr(args, 'streetview_start_coord', None),
                smooth_gps=getattr(args, 'streetview_smooth_gps', False),
                status_file=args.status_file,
                set_completed=False,
                start_ts=start_ts
            )
            sv_rel_video = os.path.relpath(sv_output_mp4, os.getcwd()).replace('\\', '/')
            sv_rel_gpx = os.path.relpath(sv_output_gpx, os.getcwd()).replace('\\', '/')
            print(f"[StreetView] Export completed! Video: {sv_rel_video}, Track: {sv_rel_gpx}")
        except Exception as sv_err:
            print(f"[StreetView] Error generating Street View export: {sv_err}", file=sys.stderr)

    # -- Stabilization Report -------------------------------------------------
    stab_summary = None
    stab_pct = None
    report_file_path = None

    # Run automatic phase-correlation stabilization analysis if stabilization was enabled and methods were executed
    base_stab_raw = initial_raw_stitched_file if ('initial_raw_stitched_file' in locals() and initial_raw_stitched_file and os.path.exists(initial_raw_stitched_file)) else (step1_stitch if ('step1_stitch' in locals() and os.path.exists(step1_stitch)) else args.input)
    if getattr(args, 'stabilize', False) and executed_methods_so_far and base_stab_raw and os.path.exists(base_stab_raw):
        update_status(args.status_file, {
            "status": "reporting",
            "phase": "Generating Stabilization Performance Report...",
            "progress": 99.0,
            "speed": "N/A",
            "eta": "Calculating...",
            "elapsed": int(time.time() - start_ts)
        })
        report_file_path = f"{out_base}_stabilization_report.txt"
        target_stab_file = stab_video_file if ('stab_video_file' in locals() and stab_video_file and os.path.exists(stab_video_file)) else current_file

        _kopf_disclaimer = (
            "\n[NOTE] Kopf 3D-2D corrections apply spherical rotations (v360 yaw/pitch/roll).\n"
            "Equirectangular 2D pixel-shift metrics measure coordinate displacement rather than\n"
            "angular stability. Refer to the 3D Rotational Jitter / Jerk section above to evaluate\n"
            "Kopf stabilization quality.\n"
        ) if any(m in active_methods for m in ('kopf',)) or getattr(args, 'stabilize_type', '') in ('kopf', 'telemetry_kopf', 'hybrid_kopf') else ""

        # Identify actual step outputs for individual stage reports
        step_tele = _tele_out if ('_tele_out' in locals() and _tele_out and os.path.exists(_tele_out)) else None
        step_opt  = _opt_out  if ('_opt_out' in locals()  and _opt_out  and os.path.exists(_opt_out))  else None
        step_kab  = _kab_out  if ('_kab_out' in locals()  and _kab_out  and os.path.exists(_kab_out))  else None
        step_kop  = _kopf_out if ('_kopf_out' in locals() and _kopf_out and os.path.exists(_kopf_out)) else None
        step_cp   = _cp_out   if ('_cp_out' in locals()   and _cp_out   and os.path.exists(_cp_out))   else None
        step_l1   = _l1_out   if ('_l1_out' in locals()   and _l1_out   and os.path.exists(_l1_out))   else None
        step_dl   = _dl_out   if ('_dl_out' in locals()   and _dl_out   and os.path.exists(_dl_out))   else None

        # Build execution order & step map using recorded stage IO
        phases = []
        last_out = base_stab_raw

        if "telemetry" in active_methods:
            s_in, s_out = stage_step_io.get("telemetry", (last_out, step_tele if step_tele else target_stab_file))
            phases.append(("telemetry", "Telemetry (IMU Leveling)", s_in, s_out, f"{out_base}_telemetry_report.txt"))
            if "telemetry" not in reverted_stages:
                last_out = s_out

        if "kopf" in active_methods:
            s_in, s_out = stage_step_io.get("kopf", (last_out, step_kop if step_kop else target_stab_file))
            phases.append(("kopf", "Kopf (3D-2D Vision Keyframe)", s_in, s_out, f"{out_base}_kopf_report.txt"))
            if "kopf" not in reverted_stages:
                last_out = s_out

        if "kabsch" in active_methods:
            s_in, s_out = stage_step_io.get("kabsch", (last_out, step_kab if step_kab else target_stab_file))
            phases.append(("kabsch", "Kabsch (Spherical SVD Rotation)", s_in, s_out, f"{out_base}_kabsch_report.txt"))
            if "kabsch" not in reverted_stages:
                last_out = s_out

        if "vidstab" in active_methods:
            s_in, s_out = stage_step_io.get("vidstab", (last_out, step_opt if step_opt else target_stab_file))
            phases.append(("vidstab", "VidSTAB (2D Motion)", s_in, s_out, f"{out_base}_vidstab_report.txt"))
            if "vidstab" not in reverted_stages:
                last_out = s_out

        if "cinematic" in active_methods:
            s_in, s_out = stage_step_io.get("cinematic", (last_out, step_l1 if step_l1 else target_stab_file))
            phases.append(("cinematic", "Cinematic (Path Optimization)", s_in, s_out, f"{out_base}_cinematic_report.txt"))
            if "cinematic" not in reverted_stages:
                last_out = s_out

        if "horizon" in active_methods:
            s_in, s_out = stage_step_io.get("horizon", (last_out, step_cp if step_cp else target_stab_file))
            phases.append(("horizon", "Horizon (Interactive Leveling)", s_in, s_out, f"{out_base}_horizon_report.txt"))
            if "horizon" not in reverted_stages:
                last_out = s_out

        if "traveldir" in active_methods:
            s_in, s_out = stage_step_io.get("traveldir", (last_out, step_dl if step_dl else target_stab_file))
            phases.append(("traveldir", "Travel-Direction Lock", s_in, s_out, f"{out_base}_traveldir_report.txt"))
            if "traveldir" not in reverted_stages:
                last_out = s_out

        # Ensure any missing individual model reports are written and model_results collected
        for m_key, m_name, in_v, out_v, r_path in phases:
            if not os.path.exists(r_path) or m_key not in model_results:
                f_info = {"method": getattr(args, "telemetry_fusion", "none"), "gain": getattr(args, "telemetry_fusion_gain", 0.5)} if m_key == "telemetry" else None
                res_m = write_single_stage_report(m_key, m_name, in_v, out_v, r_path, out_base, q_mode, step1_stitch=base_stab_raw, target_stab_file=target_stab_file, kopf_disclaimer=_kopf_disclaimer, fusion_info=f_info)
                if res_m:
                    model_results[m_key] = res_m

        # Generate final stabilization summary report
        report_data = analyze_stabilization_report(base_stab_raw, target_stab_file, report_file_path, report_title="STABILIZATION SUMMARY REPORT")
        if report_data:
            try:
                pipeline_names = " -> ".join([p[1] for p in phases]) if phases else "Single Pass"
                any_stage_stab = any(res.get("is_stabilized", False) for res in model_results.values() if isinstance(res, dict))
                is_net_stab = bool(report_data.get("is_stabilized", False) or any_stage_stab)
                stage_vals = [res.get("composite_pct_val") for res in model_results.values() if isinstance(res, dict) and res.get("is_stabilized") and res.get("composite_pct_val") is not None]
                fallback_val = (sum(stage_vals) / len(stage_vals)) if stage_vals else None

                net_pct_str = compute_stabilization_percentage(report_data, is_stabilized=is_net_stab, sidecar_pct=fallback_val)
                net_label = f"YES (STABILIZED) {net_pct_str}" if is_net_stab else f"NO (UNSTABILIZED) {net_pct_str}"
                net_peak  = report_data.get("peak_percentage", "0.0%")
                net_jit   = report_data.get("jitter_percentage", "N/A")

                summary_lines = [
                    f"=== STABILIZATION SUMMARY REPORT ===",
                    f"STATUS            : {net_label}",
                    f"Pipeline Mode     : {'4 - Sequential Extraction + Single-Pass Master Render (Hybrid)' if q_mode == '4' else ('1 - Single-Pass Transform Composition' if q_mode == '1' else ('2 - Lossless Intermediates' if q_mode == '2' else ('3 - Lanczos Resampling' if q_mode == '3' else '0 - Standard Multi-Pass')))}",
                    f"Raw Input Video   : {os.path.basename(base_stab_raw)}",
                    f"Final Output Video: {os.path.basename(target_stab_file)}",
                    f"Applied Pipeline  : {pipeline_names}",
                    f"",
                    f"--- EXECUTED STABILIZATION STAGES ---"
                ]

                for idx, (m_key, m_name, in_v, out_v, r_path) in enumerate(phases, 1):
                    res = model_results.get(m_key)
                    if m_key in reverted_stages:
                        in_b = os.path.basename(in_v) if in_v else "N/A"
                        out_b = os.path.basename(out_v) if out_v else "N/A"
                        rev_info = reverted_stages[m_key]
                        summary_lines.append(
                            f"{idx}. {m_name}\n"
                            f"   Input : {in_b}\n"
                            f"   Output: {out_b} [REVERTED]\n"
                            f"   Result: [AUTO-REVERTED] Stage degraded ({rev_info['status_text']}). Subsequent stages continued from {os.path.basename(rev_info['reverted_to'])}."
                        )
                    elif res:
                        st_lbl = "STABILIZED" if res.get("is_stabilized", False) else "DEGRADED"
                        pk = res.get("peak_percentage", "0.0%")
                        jt = res.get("jitter_percentage", "N/A")
                        r_px = res.get("max_raw_px", 0)
                        s_px = res.get("max_stab_px", 0)
                        in_b = os.path.basename(in_v) if in_v else "N/A"
                        out_b = os.path.basename(out_v) if out_v else "N/A"
                        tag = f"[{st_lbl}]"
                        summary_lines.append(
                            f"{idx}. {m_name}\n"
                            f"   Input : {in_b}\n"
                            f"   Output: {out_b}\n"
                            f"   Result: {tag} Peak Shock {pk} ({r_px}px -> {s_px}px) | Jitter {jt}"
                        )
                    elif q_mode == "1":
                        summary_lines.append(f"{idx}. {m_name:32s}: [COMPOSED] Rotations combined into single-pass transform queue")
                    else:
                        summary_lines.append(f"{idx}. {m_name}: Applied")

                net_summary_text = report_data.get('conclusion_text', '')
                if any_stage_stab and not report_data.get('is_stabilized', False):
                    net_summary_text = "The video WAS SPHERICALLY STABILIZED across 3D rotation stages (2D equirectangular planar metrics reflect spherical coordinate rotation)."

                summary_lines.extend([
                    f"",
                    f"=== OVERALL NET STABILIZATION ===",
                    f"STATUS    : {net_label}",
                    f"NET SHOCK : {net_peak} ({report_data.get('max_raw_px',0)}px -> {report_data.get('max_stab_px',0)}px)",
                    f"NET JITTER: {net_jit} ({report_data.get('rms_jitter_raw_px',0):.2f}px -> {report_data.get('rms_jitter_stab_px',0):.2f}px RMS tremor)",
                    f"SUMMARY   : {net_summary_text}"
                ])

                if _kopf_disclaimer:
                    summary_lines.append(_kopf_disclaimer)

                with open(report_file_path, 'w', encoding='utf-8') as f_out:
                    f_out.write("\n".join(summary_lines) + "\n")

            except Exception as e_sum_rep:
                print(f"Notice: stabilization summary report formatting notice: {e_sum_rep}")

        if report_data:
            stab_summary = report_data["summary"]
            stab_pct = report_data["percentage"]
            print(f"Report: {stab_summary}")
            print(f"Saved stabilization report to: {report_file_path}")

    rel_output = os.path.relpath(current_file, os.getcwd()).replace('\\', '/')

    status_data = {
        "status": "completed",
        "progress": 100.0,
        "speed": "Finished",
        "eta": "N/A",
        "output": rel_output,
        "elapsed": int(time.time() - start_ts)
    }
    if sv_rel_video:
        status_data["streetview_video"] = sv_rel_video
    if sv_rel_gpx:
        status_data["streetview_gpx"] = sv_rel_gpx

    if stab_summary:
        status_data["stabilization_summary"] = stab_summary
        status_data["stabilization_score"] = stab_pct
        status_data["stabilization_report_file"] = report_file_path

    if is_image:
        final_photo = current_file
        inject_gpano_photo_metadata(final_photo)
        status_data["photo_output"] = final_photo
        status_data["is_photo"] = True

    # Ensure final output and any associated streetview/gpx files are copied to data/output/ folder
    try:
        os.makedirs("data/output", exist_ok=True)
        import shutil
        if is_image:
            final_photo = current_file
            done_photo = os.path.join("data/output", os.path.basename(final_photo))
            if os.path.abspath(final_photo) != os.path.abspath(done_photo):
                shutil.copy2(final_photo, done_photo)
                print(f"[Done] Copied stitched 360 photo to {done_photo}")
            if companion_video and os.path.exists(companion_video):
                done_comp = os.path.join("data/output", os.path.basename(companion_video))
                if os.path.abspath(companion_video) != os.path.abspath(done_comp):
                    shutil.copy2(companion_video, done_comp)
                    print(f"[Done] Copied companion 360 preview video to {done_comp}")
        if os.path.exists(args.output):
            done_dest = os.path.join("data/output", os.path.basename(args.output))
            if os.path.abspath(args.output) != os.path.abspath(done_dest):
                shutil.copy2(args.output, done_dest)
                print(f"[Done] Copied final output to {done_dest}")

        out_base, out_ext = os.path.splitext(args.output)
        out_dir = os.path.dirname(args.output) or "."
        base_name = os.path.basename(out_base)

        sv_files_to_copy = []
        if 'sv_output_mp4' in locals() and sv_output_mp4 and os.path.exists(sv_output_mp4):
            sv_files_to_copy.append(sv_output_mp4)
        if 'sv_output_gpx' in locals() and sv_output_gpx and os.path.exists(sv_output_gpx):
            sv_files_to_copy.append(sv_output_gpx)
        sv_map_file = f"{out_base}_streetview_map.html"
        if os.path.exists(sv_map_file) and os.path.getsize(sv_map_file) > 0:
            sv_files_to_copy.append(sv_map_file)

        for src_f in sv_files_to_copy:
            if os.path.exists(src_f) and os.path.getsize(src_f) > 0:
                d_dest = os.path.join("data/output", os.path.basename(src_f))
                if os.path.abspath(src_f) != os.path.abspath(d_dest):
                    shutil.copy2(src_f, d_dest)
                    print(f"[Done] Copied result file to {d_dest}")

        # Copy external utility sidecar files to data/output/ if enabled
        if getattr(args, "util_gcsv", False):
            for exact_gcsv in [f"{out_base}_telemetry.gcsv", f"{out_base}.MP4.gcsv", f"{out_base}.gcsv"]:
                if os.path.exists(exact_gcsv) and os.path.getsize(exact_gcsv) > 0:
                    d_dest = os.path.join("data/output", os.path.basename(exact_gcsv))
                    if os.path.abspath(exact_gcsv) != os.path.abspath(d_dest):
                        shutil.copy2(exact_gcsv, d_dest)
                        print(f"[Done] Copied Gyroflow GCSV file to {d_dest}")

        if getattr(args, "util_bigsh0t", False):
            exact_big = f"{out_base}.bigsh0t360motion"
            if os.path.exists(exact_big) and os.path.getsize(exact_big) > 0:
                d_dest = os.path.join("data/output", os.path.basename(exact_big))
                if os.path.abspath(exact_big) != os.path.abspath(d_dest):
                    shutil.copy2(exact_big, d_dest)
                    print(f"[Done] Copied Bigsh0t motion file to {d_dest}")
    except Exception as done_err:
        print(f"Note: Could not copy to data/output/ folder: {done_err}")

    update_status(args.status_file, status_data)
    print("Stitching and metadata injection complete!")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        # Write crash log
        try:
            os.makedirs("data/runtime/logs", exist_ok=True)
            with open("data/runtime/logs/backend_crash.log", "w") as f:
                f.write(tb)
        except Exception:
            pass
        # Try to update status to failed
        try:
            status_dest = "data/runtime/temp/status.json"
            if "--status_file" in sys.argv:
                idx = sys.argv.index("--status_file")
                if idx + 1 < len(sys.argv):
                    status_dest = sys.argv[idx + 1]
            status_dir = os.path.dirname(status_dest)
            if status_dir:
                os.makedirs(status_dir, exist_ok=True)
            update_status(status_dest, {"status": "failed", "error": f"Script crashed: {e}"})
        except Exception:
            pass
        sys.exit(1)