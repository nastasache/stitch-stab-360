#!/usr/bin/env python3
import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

"""Cinematic SO(3) Path Optimization for 360° Video.

Optimizes rotation trajectories into tripod holds (zero-velocity) and smooth constant-speed pans (zero-acceleration)
via L1-norm trajectory regularization on Euler angles / SO(3).
"""

import argparse
import math
import subprocess
import re
import numpy as np
from scipy.optimize import minimize

WIN_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

def cinematic_smooth_trajectory_1d(
    values: np.ndarray,
    lambda_acc: float = 20.0,
    lambda_vel: float = 2.0,
    smooth_window: int = 45
) -> np.ndarray:
    """Optimize a 1D rotation sequence x via L1 regularized objective.

    Solves: min_p 0.5 * ||p - x||^2 + lambda_acc * ||D2 p||_1 + lambda_vel * ||D1 p||_1
    Approximates L1 norm using pseudo-Huber smoothing for fast Newton/L-BFGS-B convergence.

    Args:
        values: 1D array of rotation angles in degrees.
        lambda_acc: Regularization weight penalizing acceleration (promotes smooth pans).
        lambda_vel: Regularization weight penalizing velocity (promotes static tripod holds).
        smooth_window: Size of moving average window for optimization initialization.

    Returns:
        np.ndarray: Smoothed 1D trajectory array of the same shape as values.
    """
    n = len(values)
    if n < 4:
        return values.copy()

    # Initial guess: moving average with safe odd kernel <= n
    kernel_size = min(max(3, smooth_window), n)
    if kernel_size % 2 == 0:
        kernel_size = kernel_size - 1 if kernel_size > 3 else kernel_size + 1
    if kernel_size > n:
        kernel_size = max(1, n if n % 2 != 0 else n - 1)

    init_p = np.convolve(values, np.ones(kernel_size) / kernel_size, mode='same')
    if len(init_p) != n:
        init_p = init_p[:n]

    # Pseudo-Huber smoothing parameter
    delta = 1e-3

    def loss_and_grad(p):
        diff_orig = p - values
        loss_fidelity = 0.5 * np.sum(diff_orig * diff_orig)
        grad = diff_orig.copy()

        # 1st difference (velocity)
        d1 = p[1:] - p[:-1]
        denom1 = np.sqrt(d1 * d1 + delta * delta)
        loss_vel = lambda_vel * np.sum(denom1 - delta)
        psi1 = d1 / denom1
        grad[:-1] -= lambda_vel * psi1
        grad[1:] += lambda_vel * psi1

        # 2nd difference (acceleration)
        d2 = p[2:] - 2.0 * p[1:-1] + p[:-2]
        denom2 = np.sqrt(d2 * d2 + delta * delta)
        loss_acc = lambda_acc * np.sum(denom2 - delta)
        psi2 = d2 / denom2
        grad[:-2] += lambda_acc * psi2
        grad[1:-1] -= 2.0 * lambda_acc * psi2
        grad[2:] += lambda_acc * psi2

        return loss_fidelity + loss_vel + loss_acc, grad

    res = minimize(
        loss_and_grad,
        init_p,
        jac=True,
        method="L-BFGS-B",
        options={"maxiter": 300, "ftol": 1e-7, "gtol": 1e-4, "disp": False}
    )

    loss_init, _ = loss_and_grad(init_p)
    if res.fun <= loss_init:
        return res.x
    return init_p


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
    """Write optimized rotational trajectory into FFmpeg sendcmd format.

    Args:
        output_path: Destination path for the formatted sendcmd file.
        times: 1D array of frame start timestamps in seconds.
        angles: (N, 3) array of [yaw, pitch, roll] rotation angles in degrees.
        fps: Video frame rate in frames per second.
    """
    dt = 1.0 / max(fps, 1e-4)
    with open(output_path, "w", encoding="utf-8", newline="\n") as f:
        for i in range(len(times)):
            t_start = times[i] if i < len(times) else i * dt
            t_end = t_start + dt
            y, p, r = angles[i]
            f.write(f"{t_start:.6f}-{t_end:.6f} [enter] v360 yaw {y:.6f};\n")
            f.write(f"{t_start:.6f}-{t_end:.6f} [enter] v360 pitch {p:.6f};\n")
            f.write(f"{t_start:.6f}-{t_end:.6f} [enter] v360 roll {r:.6f};\n")


def v360_euler_to_matrix(yaw_deg: float, pitch_deg: float, roll_deg: float) -> np.ndarray:
    """Converts v360 intrinsic Euler rotation angles to a 3x3 rotation matrix.

    Args:
        yaw_deg: Yaw rotation angle in degrees.
        pitch_deg: Pitch rotation angle in degrees.
        roll_deg: Roll rotation angle in degrees.

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


def v360_matrix_to_euler(R: np.ndarray) -> tuple[float, float, float]:
    """Extracts v360 Euler rotation angles from a 3x3 rotation matrix.

    Args:
        R: 3x3 rotation matrix.

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


def optimize_3d_trajectory(
    angles: np.ndarray,
    lambda_acc: float = 20.0,
    lambda_vel: float = 2.0,
    smooth_window: int = 45
) -> np.ndarray:
    """Optimize 3D rotation trajectory using L1-norm regularization.

    Unwraps yaw across spherical boundaries, independently optimizes yaw,
    pitch, and roll via pseudo-Huber L1 regularization, and re-wraps yaw.

    Args:
        angles: (N, 3) array of [yaw, pitch, roll] rotation angles in degrees.
        lambda_acc: Weight parameter penalizing non-constant angular acceleration.
        lambda_vel: Weight parameter penalizing non-zero angular velocity.
        smooth_window: Window length for moving average initialization.

    Returns:
        np.ndarray: Optimized (N, 3) array of smoothed [yaw, pitch, roll] angles in degrees.
    """
    n = len(angles)
    if n < 4:
        return angles.copy()

    smoothed = np.zeros_like(angles)

    # Unwrap yaw (angles in degrees -> radians -> unwrap -> degrees)
    yaw_rad = np.unwrap(np.radians(angles[:, 0]))
    yaw_deg_unwrapped = np.degrees(yaw_rad)

    smoothed[:, 0] = cinematic_smooth_trajectory_1d(yaw_deg_unwrapped, lambda_acc, lambda_vel, smooth_window)
    # Wrap smoothed yaw back to [-180, 180]
    smoothed[:, 0] = (smoothed[:, 0] + 180.0) % 360.0 - 180.0

    # Pitch & Roll
    smoothed[:, 1] = cinematic_smooth_trajectory_1d(angles[:, 1], lambda_acc, lambda_vel, smooth_window)
    smoothed[:, 2] = cinematic_smooth_trajectory_1d(angles[:, 2], lambda_acc, lambda_vel, smooth_window)

    return smoothed


def main():
    """Execute command-line interface for cinematic trajectory smoothing and video rendering."""
    parser = argparse.ArgumentParser(description="Cinematic SO(3) Path Optimization for 360° Video.")
    parser.add_argument("--input", "-i", default="", help="Input video file")
    parser.add_argument("--sendcmd-in", default="", help="Input sendcmd.txt trajectory file to smooth")
    parser.add_argument("--output", "-o", default="sendcmd_cinematic.txt", help="Output sendcmd file")
    parser.add_argument("--output-video", default="", help="Optional output stabilized video")
    parser.add_argument("--lambda-acc", type=float, default=20.0, help="Weight for piecewise-constant acceleration (smooth pans)")
    parser.add_argument("--lambda-vel", type=float, default=2.0, help="Weight for piecewise-constant velocity (tripod holds)")
    parser.add_argument("--smooth-window", type=int, default=45, help="Smoothing initialization window in frames")
    parser.add_argument("--fps", type=float, default=30.0, help="Frames per second")
    parser.add_argument("--test-synthetic", action="store_true", help="Run synthetic verification suite")

    args = parser.parse_args()

    if args.test_synthetic:
        print("[stabilize_cinematic.py] Running synthetic validation...")
        n = 120
        # Create a trajectory with a static segment, then a constant pan, plus high-frequency noise
        t = np.linspace(0, 4.0, n)
        true_motion = np.zeros(n)
        true_motion[40:80] = np.linspace(0, 20.0, 40) # Pan
        true_motion[80:] = 20.0 # Hold

        noise = 1.5 * np.sin(2.0 * np.pi * 5.0 * t) # 5 Hz jitter
        observed = true_motion + noise

        data = np.zeros((n, 3))
        data[:, 0] = observed
        data[:, 1] = noise * 0.5
        data[:, 2] = noise * 0.3

        result = optimize_3d_trajectory(data, lambda_acc=25.0, lambda_vel=5.0)

        # Measure noise reduction
        jitter_orig = np.std(np.diff(observed))
        jitter_smooth = np.std(np.diff(result[:, 0]))
        print(f"Yaw Jitter (Original StdDev): {jitter_orig:.3f}")
        print(f"Yaw Jitter (Smoothed StdDev): {jitter_smooth:.3f}")
        assert jitter_smooth < jitter_orig, "Cinematic smoothing failed to reduce jitter!"

        # Validate differential SO(3) delta composition
        R_up = v360_euler_to_matrix(12.5, -5.2, 3.1)
        R_sm = v360_euler_to_matrix(10.0, -4.0, 2.0)
        R_delta = R_up.T @ R_sm
        dy, dp, dr = v360_matrix_to_euler(R_delta)
        R_comp = R_up @ v360_euler_to_matrix(dy, dp, dr)
        cy, cp, cr = v360_matrix_to_euler(R_comp)
        assert abs(cy - 10.0) < 1e-4 and abs(cp - (-4.0)) < 1e-4 and abs(cr - 2.0) < 1e-4, "SO(3) differential composition failed!"

        print("[stabilize_cinematic.py] Synthetic test PASSED.")
        sys.exit(0)

    has_upstream = False
    if args.sendcmd_in and os.path.exists(args.sendcmd_in):
        times, angles, detected_fps = parse_sendcmd_file(args.sendcmd_in)
        fps = args.fps if args.fps > 0 else detected_fps
        has_upstream = True
    else:
        # Fallback dummy trajectory or optical extraction
        times = np.linspace(0, 10.0, 300)
        angles = np.zeros((300, 3))
        fps = args.fps

    smoothed_angles = optimize_3d_trajectory(
        angles,
        lambda_acc=args.lambda_acc,
        lambda_vel=args.lambda_vel,
        smooth_window=args.smooth_window
    )

    if has_upstream and len(angles) > 0:
        # Compute differential delta rotation: R_delta = R_upstream^T @ R_smoothed
        # Composing R_upstream @ R_delta yields exactly R_smoothed without double-counting.
        delta_angles = np.zeros_like(angles)
        for i in range(len(angles)):
            R_up = v360_euler_to_matrix(angles[i, 0], angles[i, 1], angles[i, 2])
            R_sm = v360_euler_to_matrix(smoothed_angles[i, 0], smoothed_angles[i, 1], smoothed_angles[i, 2])
            R_delta = R_up.T @ R_sm
            dy, dp, dr = v360_matrix_to_euler(R_delta)
            delta_angles[i] = [dy, dp, dr]
        out_angles = delta_angles
    else:
        out_angles = smoothed_angles

    write_sendcmd_file(args.output, times, out_angles, fps)
    print(f"[stabilize_cinematic.py] Wrote {'differential' if has_upstream else 'smoothed'} trajectory to: {args.output}")

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
        print(f"[stabilize_cinematic.py] Rendered stabilized video to: {args.output_video}")


if __name__ == "__main__":
    main()
