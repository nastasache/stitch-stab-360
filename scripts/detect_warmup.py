import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

"""Hardware Sensor Warm-up & Calibration Jump Detector.

Analyzes the initial frames of raw 360 video and internal IMU telemetry
(vrot atom) to identify uninitialized sensor frames, IMU gravity snaps,
black frames, or frozen frames during hardware boot.
"""

import struct
import json
import math
import argparse
import subprocess

WIN_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from utils.mp4_utils import get_video_properties, find_box, parse_vrot

def _angle_distance(r1, r2):
    """Compute angular distance between two (roll, pitch, yaw) orientation tuples in degrees.

    Args:
        r1: First (roll, pitch, yaw) sequence in degrees.
        r2: Second (roll, pitch, yaw) sequence in degrees.

    Returns:
        float: Angular Euclidean distance accounting for circular angle wrapping.
    """
    dr = ((r2[0] - r1[0] + 180) % 360) - 180
    dp = ((r2[1] - r1[1] + 180) % 360) - 180
    dy = ((r2[2] - r1[2] + 180) % 360) - 180
    return math.sqrt(dr * dr + dp * dp + dy * dy)

def inspect_telemetry_warmup(video_path, fps, max_inspect_sec=12.0):
    """Detects initial sensor boot anomalies in vrot IMU orientation data.

    Checks for uninitialized zero telemetry vectors (0, 0, 0), static
    frozen telemetry frames, and discrete hardware calibration step transitions
    occurring in the initial boot window.

    Args:
        video_path (str): Filepath to the video file.
        fps (float): Video framerate in frames per second.
        max_inspect_sec (float, optional): Maximum duration to inspect in seconds. Defaults to 12.0.

    Returns:
        dict | None: Dictionary with detected start_frame, reason, and type, or None.
    """
    if not os.path.exists(video_path):
        return None
    
    file_size = os.path.getsize(video_path)
    with open(video_path, "rb") as f:
        vrot_box = find_box(f, 0, file_size, ["moov", "udta", "vrot"])
        if not vrot_box:
            return None
        offset, size = vrot_box
        f.seek(offset + 12)
        vrot_bytes = f.read(size - 12)

    rot_data = parse_vrot(vrot_bytes)
    if not rot_data:
        return None

    # Camera hardware boot calibration occurs within the first 2.0s (~60 frames)
    max_boot_frames = min(len(rot_data), int(round(fps * 2.0)))

    # 1. Check for initial exact-zero uninitialized telemetry (0.0, 0.0, 0.0)
    zero_count = 0
    for i in range(max_boot_frames):
        s = rot_data[i]
        if abs(s[0]) < 1e-4 and abs(s[1]) < 1e-4 and abs(s[2]) < 1e-4:
            zero_count += 1
        else:
            break

    if zero_count >= 3:
        return {
            "start_frame": zero_count,
            "reason": f"Uninitialized zero telemetry boot frames (frames 0-{zero_count - 1})",
            "type": "telemetry_zero"
        }

    # 2. Check for initial static frozen telemetry frames (identical samples from frame 0)
    first_sample = rot_data[0]
    static_count = 0
    for i in range(max_boot_frames):
        s = rot_data[i]
        if abs(s[0] - first_sample[0]) < 1e-4 and abs(s[1] - first_sample[1]) < 1e-4 and abs(s[2] - first_sample[2]) < 1e-4:
            static_count += 1
        else:
            break

    if static_count >= 5:
        return {
            "start_frame": static_count,
            "reason": f"Static frozen telemetry initialization (frames 0-{static_count - 1})",
            "type": "telemetry_static"
        }

    # 3. Check for discrete hardware calibration step transition (level shift)
    # Generic change-point detector: captures persistent recalibration steps
    # (e.g. gravity convergence snaps at 0.5s-2.5s) while rejecting road bumps and vibrations.
    max_snap_frames = min(len(rot_data) - 4, int(round(fps * min(max_inspect_sec, 3.0))))
    min_snap_angle = 12.0

    for i in range(1, max_snap_frames):
        mag = _angle_distance(rot_data[i - 1], rot_data[i])
        if mag < min_snap_angle:
            continue

        # Post-step immediate settling: frame i+1 must not continue jumping or oscillating wildly
        mag_next = _angle_distance(rot_data[i], rot_data[i + 1])
        if mag_next > 2.5 or mag_next > mag * 0.30:
            continue

        # Adaptive window of pre-jump and post-jump frames (up to 8 frames)
        w_pre = min(i, 8)
        w_post = min(len(rot_data) - i, 8)
        if w_pre < 2 or w_post < 2:
            continue

        # Pre-step calmness: average delta before jump must be much smaller than the step
        deltas_pre = [_angle_distance(rot_data[k - 1], rot_data[k]) for k in range(i - w_pre + 1, i)]
        avg_delta_pre = sum(deltas_pre) / len(deltas_pre) if deltas_pre else 0
        if avg_delta_pre > mag * 0.25:
            continue

        # Mean orientations before and after the step
        mean_pre = (
            sum(rot_data[k][0] for k in range(i - w_pre, i)) / w_pre,
            sum(rot_data[k][1] for k in range(i - w_pre, i)) / w_pre,
            sum(rot_data[k][2] for k in range(i - w_pre, i)) / w_pre,
        )
        mean_post = (
            sum(rot_data[k][0] for k in range(i, i + w_post)) / w_post,
            sum(rot_data[k][1] for k in range(i, i + w_post)) / w_post,
            sum(rot_data[k][2] for k in range(i, i + w_post)) / w_post,
        )

        # Persistent baseline shift: must produce a substantial permanent level shift
        shift = _angle_distance(mean_pre, mean_post)
        if shift < mag * 0.65:
            continue

        # No immediate rebound: post-step frames must not bounce back to the pre-step baseline
        rebounded = any(_angle_distance(rot_data[k], mean_pre) < mag * 0.50 for k in range(i, i + w_post))
        if rebounded:
            continue

        return {
            "start_frame": i,
            "reason": f"IMU calibration switch point detected at frame {i} ({mag:.1f}° angle correction at {i / fps:.2f}s)",
            "type": "telemetry_snap",
        }

    return None

def inspect_visual_warmup(video_path, fps, max_inspect_sec=4.0):
    """Detects optical sensor startup anomalies using OpenCV frame inspection.

    Identifies completely black sensor initialization frames and frozen startup
    frames by monitoring inter-frame luminance and pixel differences.

    Args:
        video_path (str): Filepath to the video file.
        fps (float): Video framerate.
        max_inspect_sec (float, optional): Inspection window limit in seconds. Defaults to 4.0.

    Returns:
        dict | None: Dictionary with detected start_frame, reason, and type, or None.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None

    max_frames = min(int(math.ceil(max_inspect_sec * fps)), int(round(fps * 2.0)))
    frame_idx = 0
    prev_gray = None
    
    black_count = 0
    frozen_count = 0
    
    while frame_idx < max_frames:
        ret, frame = cap.read()
        if not ret or frame is None:
            break
            
        small = cv2.resize(frame, (160, 80), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        
        mean_lum = float(np.mean(gray))
        if frame_idx == black_count and mean_lum < 6.0:
            black_count += 1
            frame_idx += 1
            prev_gray = gray
            continue
            
        if prev_gray is not None:
            diff = float(np.mean(cv2.absdiff(gray, prev_gray)))
            if frame_idx == black_count + frozen_count and diff < 0.05:
                frozen_count += 1
        
        prev_gray = gray
        frame_idx += 1

    cap.release()

    if black_count >= 3:
        return {
            "start_frame": black_count,
            "reason": f"Black sensor initialization frames detected (frames 0-{black_count - 1})",
            "type": "visual_black"
        }

    if frozen_count >= 5:
        total_initial = black_count + frozen_count
        return {
            "start_frame": total_initial,
            "reason": f"Frozen video startup frames detected (frames 0-{total_initial - 1})",
            "type": "visual_frozen"
        }

    return None

def inspect_warmup(input_path, max_inspect_sec=12.0):
    """Orchestrates combined telemetry and optical sensor warm-up detection.

    Args:
        input_path (str): Path to input video.
        max_inspect_sec (float, optional): Maximum window to inspect in seconds. Defaults to 12.0.

    Returns:
        dict: Standardized result dictionary containing status, has_warmup flag,
            start_frame, start_time, and detection reasoning.
    """
    if not os.path.exists(input_path):
        return {
            "status": "error",
            "error": f"Input file not found: {input_path}"
        }

    props = get_video_properties(input_path)
    if not props:
        return {
            "status": "error",
            "error": "Failed to read video properties."
        }

    fps = props["fps"]
    total_frames = props["total_frames"]
    duration = props["duration"]

    telemetry_result = inspect_telemetry_warmup(input_path, fps, max_inspect_sec)
    visual_result = inspect_visual_warmup(input_path, fps, max_inspect_sec)

    chosen_result = None

    if telemetry_result and visual_result:
        t_frame = telemetry_result["start_frame"]
        v_frame = visual_result["start_frame"]
        if visual_result["type"] in ["visual_black", "visual_frozen"] and v_frame > t_frame:
            chosen_result = visual_result
        else:
            chosen_result = telemetry_result
    elif telemetry_result:
        chosen_result = telemetry_result
    elif visual_result:
        chosen_result = visual_result

    if chosen_result and chosen_result["start_frame"] > 0:
        start_frame = min(chosen_result["start_frame"], total_frames - 1)
        start_time = start_frame / fps
        return {
            "status": "success",
            "has_warmup": True,
            "start_frame": start_frame,
            "start_time": round(start_time, 3),
            "fps": round(fps, 3),
            "total_frames": total_frames,
            "duration": round(duration, 3),
            "reason": chosen_result["reason"],
            "detection_type": chosen_result["type"]
        }
    else:
        return {
            "status": "success",
            "has_warmup": False,
            "start_frame": 0,
            "start_time": 0.0,
            "fps": round(fps, 3),
            "total_frames": total_frames,
            "duration": round(duration, 3),
            "reason": "No calibration or warm-up period detected (video starts immediately).",
            "detection_type": "none"
        }

def main():
    """CLI entry point for video sensor warm-up detection."""
    parser = argparse.ArgumentParser(description="Inspect 360 video for initial calibration or warm-up frames.")
    parser.add_argument("--input", required=True, help="Path to input video")
    parser.add_argument("--max_sec", type=float, default=12.0, help="Maximum initial seconds to inspect")
    args = parser.parse_args()

    result = inspect_warmup(args.input, args.max_sec)
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
