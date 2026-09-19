#!/usr/bin/env python3
import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

"""Travel-direction lock and heading steering for 360° video.

Locks or steers the 360° heading (yaw) toward the forward direction of travel
or a target subject, applying angular dampening and deadband filters while
preserving pitch/roll horizon stability.
"""

import argparse
import math
import subprocess
import re
import json
import numpy as np

WIN_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)


def extract_standalone_yaw(video_path: str, default_fps: float = 30.0) -> tuple[np.ndarray, np.ndarray, float]:
    """Extract camera yaw trajectory from standalone video using telemetry, optical flow, or ffprobe.

    Args:
        video_path: Path to the input video file.
        default_fps: Fallback framerate.

    Returns:
        tuple[np.ndarray, np.ndarray, float]: (times, angles, fps)
    """
    if not video_path or not os.path.exists(video_path):
        times = np.linspace(0, 10.0, 300)
        return times, np.zeros((300, 3)), default_fps

    duration = 10.0
    fps = default_fps
    try:
        from utils.mp4_utils import get_video_properties
        props = get_video_properties(video_path)
        if props:
            fps = float(props.get("fps", default_fps)) or default_fps
            duration = float(props.get("duration", 0.0)) or 10.0
    except Exception as e_props:
        print(f"[stabilize_traveldir.py] Notice: ffprobe container parse: {e_props}")

    # Visual Flow: Horizontal Equatorial Phase Correlation via OpenCV
    try:
        import cv2
        cap = cv2.VideoCapture(video_path)
        if cap.isOpened():
            times_list, angles_list = [], []
            accum_yaw = 0.0
            prev_eq = None
            f_idx = 0
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                h, w = frame.shape[:2]
                target_w = 480
                target_h = int(h * target_w / max(w, 1))
                small = cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_AREA)
                y1 = int(target_h * 0.40)
                y2 = int(target_h * 0.60)
                eq_strip = cv2.cvtColor(small[y1:y2, :], cv2.COLOR_BGR2GRAY)

                t_sec = f_idx / fps
                if prev_eq is not None:
                    shift, response = cv2.phaseCorrelate(prev_eq.astype(np.float32), eq_strip.astype(np.float32))
                    dx = shift[0]
                    if abs(dx) < (target_w * 0.35) and response > 0.08:
                        dyaw = -(dx / target_w) * 360.0
                        accum_yaw += dyaw
                prev_eq = eq_strip
                times_list.append(t_sec)
                angles_list.append([accum_yaw, 0.0, 0.0])
                f_idx += 1

            cap.release()
            if len(times_list) > 1:
                print(f"[stabilize_traveldir.py] Extracted visual yaw trajectory across {len(times_list)} frames via optical flow.")
                return np.array(times_list, dtype=np.float64), np.array(angles_list, dtype=np.float64), fps
    except Exception as e_cv:
        print(f"[stabilize_traveldir.py] Notice: Visual flow extraction: {e_cv}")

    n_frames = max(2, int(round(duration * fps)))
    times = np.linspace(0, duration, n_frames)
    angles = np.zeros((n_frames, 3))
    print(f"[stabilize_traveldir.py] Notice: Standalone run using video duration ({duration:.2f}s, {n_frames} frames at {fps:.2f} fps).")
    return times, angles, fps

def parse_sendcmd_file(sendcmd_path: str) -> tuple[np.ndarray, np.ndarray, float]:
    """Parse an existing FFmpeg sendcmd.txt file into timestamps and Euler angles.

    Args:
        sendcmd_path: Filesystem path to the FFmpeg sendcmd text file.

    Returns:
        tuple[np.ndarray, np.ndarray, float]: A 3-tuple containing:
            - times_arr: 1D array of frame start timestamps in seconds.
            - angles_arr: (N, 3) array of [yaw, pitch, roll] rotation angles in degrees.
            - fps: Inferred frame rate in frames per second.
    """
    by_time = {}
    is_delta = False
    with open(sendcmd_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith("# format: delta") or "format: delta" in line:
                is_delta = True
                continue
            if line.startswith("#") or "[enter]" not in line:
                continue
            parts = line.split("[enter]")
            time_part = parts[0].strip()
            cmd_part = parts[1].strip()
            if "-" in time_part:
                t_start = float(time_part.split("-")[0].strip())
            else:
                t_start = float(time_part.strip())
            t_key = round(t_start, 6)
            if t_key not in by_time:
                by_time[t_key] = {"yaw": 0.0, "pitch": 0.0, "roll": 0.0}

            for match in re.finditer(r"(yaw|pitch|roll)\s+([-\d.]+)", cmd_part):
                by_time[t_key][match.group(1)] = float(match.group(2))

    if not by_time:
        return np.empty((0,)), np.empty((0, 3)), 30.0

    sorted_keys = sorted(by_time.keys())
    times = [k for k in sorted_keys]
    if is_delta:
        accum_y, accum_p, accum_r = 0.0, 0.0, 0.0
        angles = []
        for k in sorted_keys:
            accum_y += by_time[k]["yaw"]
            accum_p += by_time[k]["pitch"]
            accum_r += by_time[k]["roll"]
            angles.append([accum_y, accum_p, accum_r])
    else:
        angles = [[by_time[k]["yaw"], by_time[k]["pitch"], by_time[k]["roll"]] for k in sorted_keys]

    times_arr = np.array(times, dtype=np.float64)
    angles_arr = np.array(angles, dtype=np.float64)
    dt = np.median(np.diff(times_arr)) if len(times_arr) > 1 else 1.0 / 30.0
    fps = 1.0 / max(dt, 1e-4)
    return times_arr, angles_arr, fps


def write_sendcmd_file(output_path: str, times: np.ndarray, angles: np.ndarray, fps: float):
    """Write directional-lock rotational trajectory into FFmpeg sendcmd format.

    Args:
        output_path: Destination path for the formatted sendcmd file.
        times: 1D array of frame start timestamps in seconds.
        angles: (N, 3) array of [yaw, pitch, roll] rotation angles in degrees.
        fps: Video frame rate in frames per second.
    """
    dt = 1.0 / max(fps, 1e-4)
    with open(output_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("# format: delta\n")
        prev_y = 0.0
        prev_p = 0.0
        prev_r = 0.0
        for i in range(len(times)):
            t_start = times[i] if i < len(times) else i * dt
            t_end = t_start + dt
            y, p, r = angles[i]
            # FFmpeg v360 sendcmd accumulates rotation commands without reset_rot.
            # Output incremental frame-to-frame deltas (curr - prev) to track absolute trajectory.
            # Invert sign to map from right-handed trajectory space to FFmpeg v360 camera rotation space.
            dy = -((y - prev_y + 180.0) % 360.0 - 180.0)
            dp = -((p - prev_p + 180.0) % 360.0 - 180.0)
            dr = -((r - prev_r + 180.0) % 360.0 - 180.0)
            f.write(f"{t_start:.6f}-{t_end:.6f} [enter] v360 yaw {dy:.6f};\n")
            f.write(f"{t_start:.6f}-{t_end:.6f} [enter] v360 pitch {dp:.6f};\n")
            f.write(f"{t_start:.6f}-{t_end:.6f} [enter] v360 roll {dr:.6f};\n")
            prev_y, prev_p, prev_r = y, p, r


def compute_traveldir(
    angles: np.ndarray,
    mode: str = "travel_direction",
    target_yaw: float = 0.0,
    damping: float = 0.90,
    deadband_deg: float = 1.5,
    return_diagnostics: bool = False
) -> np.ndarray | tuple[np.ndarray, dict]:
    """Apply direction locking and yaw steering to a rotational trajectory.

    Adjusts yaw to keep viewing heading aligned with travel direction or target
    subject using angular dampening and deadband filters, leaving pitch and roll
    intact (zeroed delta) to preserve horizon leveling.

    Args:
        angles: (N, 3) array of [yaw, pitch, roll] rotation angles in degrees.
        mode: Steering mode ('travel_direction', 'target_lock', or 'damped_follow').
        target_yaw: Target heading yaw in degrees (used in 'target_lock' mode).
        damping: Angular smoothing and dampening factor in [0.0, 0.99].
        deadband_deg: Angular threshold in degrees below which minor jitters are ignored.
        return_diagnostics: If True, returns a tuple of (locked_angles, diag_dict).

    Returns:
        np.ndarray | tuple[np.ndarray, dict]: Array of direction-steering deltas,
            or (deltas, diagnostics dictionary) if return_diagnostics is True.
    """
    n = len(angles)
    if n == 0:
        empty_res = np.zeros((0, 3))
        if return_diagnostics:
            return empty_res, {"raw_yaw": [], "trend_yaw": [], "locked_yaw": [], "steer_corr": [], "deadband_deg": deadband_deg, "damping": damping, "mode": mode}
        return empty_res

    locked = np.zeros((n, 3))

    # Unwrap yaw for smooth tracking across [-180, 180] boundary
    yaw_rad = np.unwrap(np.radians(angles[:, 0]))
    yaw_deg = np.degrees(yaw_rad)

    current_steer = 0.0
    trend_deg = np.zeros(n)

    if mode == "target_lock":
        # Steer yaw so that result equals target_yaw
        trend_deg.fill(target_yaw)
        for i in range(n):
            desired_corr = target_yaw - yaw_deg[i]
            diff = desired_corr - current_steer
            if abs(diff) > deadband_deg:
                current_steer += (1.0 - damping) * diff
            locked[i, 0] = current_steer

    elif mode == "damped_follow":
        # Heavy low-pass dampening on yaw turns
        accum = yaw_deg[0]
        for i in range(n):
            err = yaw_deg[i] - accum
            if abs(err) > deadband_deg:
                accum += (1.0 - damping) * err
            trend_deg[i] = accum
            # Counter-rotation delta needed to stabilize heading
            locked[i, 0] = accum - yaw_deg[i]

    else: # "travel_direction" (default)
        # Moving window direction estimator with edge padding
        win_size = min(59, n if n % 2 != 0 else n - 1)
        win_size = max(3, win_size)
        pad = win_size // 2
        padded = np.pad(yaw_deg, pad, mode='edge')
        trend = np.convolve(padded, np.ones(win_size) / win_size, mode='valid')
        if len(trend) != n:
            trend = trend[:n]
        trend_deg = trend.copy()

        for i in range(n):
            desired_corr = trend[i] - yaw_deg[i]
            diff = desired_corr - current_steer
            if abs(diff) > deadband_deg:
                current_steer += (1.0 - damping) * diff
            locked[i, 0] = current_steer

    effective_viewer_yaw = yaw_deg + locked[:, 0]
    steer_corr_raw = locked[:, 0].copy()

    # Normalize yaw correction into [-180, 180]
    locked[:, 0] = (locked[:, 0] + 180.0) % 360.0 - 180.0

    if return_diagnostics:
        diag = {
            "raw_yaw": [float(v) for v in yaw_deg],
            "trend_yaw": [float(v) for v in trend_deg],
            "locked_yaw": [float(v) for v in effective_viewer_yaw],
            "steer_corr": [float(v) for v in steer_corr_raw],
            "deadband_deg": float(deadband_deg),
            "damping": float(damping),
            "mode": mode
        }
        return locked, diag

    return locked


def main():
    """Execute command-line interface for travel-direction lock and video rendering."""
    parser = argparse.ArgumentParser(description="Travel-Direction Lock for 360° Video.")
    parser.add_argument("--input", "-i", default="", help="Input video file")
    parser.add_argument("--sendcmd-in", default="", help="Input sendcmd.txt trajectory file to lock")
    parser.add_argument("--output", "-o", default="sendcmd_traveldir.txt", help="Output sendcmd file")
    parser.add_argument("--meta-out", default="", help="Optional output path for diagnostics metadata JSON")
    parser.add_argument("--output-video", default="", help="Optional output locked video")
    parser.add_argument("--mode", default="travel_direction", choices=["travel_direction", "target_lock", "damped_follow"], help="Direction locking mode")
    parser.add_argument("--target-yaw", type=float, default=0.0, help="Target heading yaw in degrees")
    parser.add_argument("--damping", type=float, default=0.90, help="Smoothing/damping factor (0.0 to 0.99)")
    parser.add_argument("--deadband", type=float, default=1.5, help="Angular deadband threshold in degrees")
    parser.add_argument("--fps", type=float, default=30.0, help="Frames per second")
    parser.add_argument("--test-synthetic", action="store_true", help="Run synthetic verification suite")

    args = parser.parse_args()

    if args.test_synthetic:
        print("[stabilize_traveldir.py] Running synthetic validation...")
        n = 90
        # Simulating a camera weaving in yaw between -30 and +30 deg
        t = np.linspace(0, 3.0, n)
        yaw_weaving = 30.0 * np.sin(2.0 * np.pi * 0.5 * t)
        angles = np.zeros((n, 3))
        angles[:, 0] = yaw_weaving

        steer_corr, diag = compute_traveldir(angles, mode="target_lock", target_yaw=0.0, damping=0.85, deadband_deg=2.0, return_diagnostics=True)
        assert "raw_yaw" in diag and "locked_yaw" in diag and "trend_yaw" in diag, "Missing diagnostic keys!"
        eff_yaw = np.array(diag["locked_yaw"])
        orig_spread = np.max(angles[:, 0]) - np.min(angles[:, 0])
        locked_spread = np.max(eff_yaw) - np.min(eff_yaw)
        print(f"Yaw Oscillation Range (Original): {orig_spread:.2f}°")
        print(f"Yaw Oscillation Range (Locked):   {locked_spread:.2f}°")
        assert locked_spread < orig_spread, "Travel direction lock failed to damp yaw oscillations!"
        print("[stabilize_traveldir.py] Synthetic test PASSED.")
        sys.exit(0)

    if args.sendcmd_in and os.path.exists(args.sendcmd_in):
        times, angles, detected_fps = parse_sendcmd_file(args.sendcmd_in)
        fps = args.fps if args.fps > 0 else detected_fps
    else:
        times, angles, fps = extract_standalone_yaw(args.input, default_fps=args.fps)

    locked_angles, diag_meta = compute_traveldir(
        angles,
        mode=args.mode,
        target_yaw=args.target_yaw,
        damping=args.damping,
        deadband_deg=args.deadband,
        return_diagnostics=True
    )

    write_sendcmd_file(args.output, times, locked_angles, fps)
    print(f"[stabilize_traveldir.py] Wrote direction lock trajectory to: {args.output}")

    meta_path = args.meta_out
    if not meta_path and args.output:
        if "_sendcmd_traveldir.txt" in args.output:
            meta_path = args.output.replace("_sendcmd_traveldir.txt", "_traveldir_meta.json")
        elif args.output.endswith(".txt"):
            meta_path = args.output[:-4] + "_meta.json"
        else:
            meta_path = args.output + "_meta.json"

    if meta_path:
        diag_meta["times"] = [float(t) for t in times]
        diag_meta["fps"] = float(fps)
        try:
            with open(meta_path, "w", encoding="utf-8", newline="\n") as f_meta:
                json.dump(diag_meta, f_meta, indent=2)
            print(f"[stabilize_traveldir.py] Wrote traveldir diagnostic metadata to: {meta_path}")
        except Exception as e_meta:
            print(f"Warning: Failed to write {meta_path}: {e_meta}")

    if args.output_video and args.input and os.path.exists(args.input):
        sendcmd_escaped = args.output.replace('\\', '/')
        cmd = [
            "ffmpeg", "-y", "-i", args.input,
            "-vf", f"sendcmd=f='{sendcmd_escaped}',v360=input=equirect:output=equirect",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-c:a", "copy",
            args.output_video
        ]
        subprocess.run(cmd, check=True, creationflags=WIN_NO_WINDOW)
        print(f"[stabilize_traveldir.py] Rendered locked video to: {args.output_video}")


if __name__ == "__main__":
    main()
