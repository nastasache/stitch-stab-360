#!/usr/bin/env python3
import sys, os
sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'

"""
stabilize_horizon.py -- Horizon Stabilization & Leveling.

Applies keyframe-interpolated horizon leveling checkpoints to 360° equirectangular video
by generating smooth continuous quaternion SLERP or cubic spline camera trajectories
and rendering with FFmpeg v360 filter.

Usage:
    python -B stabilize_horizon.py --input video.mp4 --checkpoints horizon_checkpoints.json --output video_leveled.mp4
    python -B stabilize_horizon.py --checkpoints horizon_checkpoints.json --dry-run
"""

import math
import json
import argparse
import subprocess
import numpy as np

WIN_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def euler_to_quaternion(yaw_deg, pitch_deg, roll_deg):
    """Converts Euler angles to a normalized unit quaternion [w, x, y, z].

    Follows YXZ intrinsic rotation order (Yaw around Y, Pitch around X, Roll around Z).

    Args:
        yaw_deg (float): Yaw angle in degrees.
        pitch_deg (float): Pitch angle in degrees.
        roll_deg (float): Roll angle in degrees.

    Returns:
        np.ndarray: Unit quaternion [w, x, y, z] as a 1D float64 array.
    """
    yaw = math.radians(yaw_deg)
    pitch = math.radians(pitch_deg)
    roll = math.radians(roll_deg)

    cy = math.cos(yaw * 0.5)
    sy = math.sin(yaw * 0.5)
    cp = math.cos(pitch * 0.5)
    sp = math.sin(pitch * 0.5)
    cr = math.cos(roll * 0.5)
    sr = math.sin(roll * 0.5)

    w = cy * cp * cr + sy * sp * sr
    x = cy * sp * cr + sy * cp * sr
    y = sy * cp * cr - cy * sp * sr
    z = cy * cp * sr - sy * sp * cr

    norm = math.sqrt(w*w + x*x + y*y + z*z) or 1.0
    return np.array([w / norm, x / norm, y / norm, z / norm], dtype=np.float64)


def quaternion_to_euler(q):
    """Converts a unit quaternion [w, x, y, z] to Euler angles in degrees.

    Args:
        q (array_like): 4-element quaternion [w, x, y, z].

    Returns:
        tuple[float, float, float]: Euler angles in degrees as (yaw, pitch, roll).
    """
    w, x, y, z = q
    # Normalize
    norm = math.sqrt(w*w + x*x + y*y + z*z) or 1.0
    w, x, y, z = w/norm, x/norm, y/norm, z/norm

    # Pitch (around X)
    sin_p = 2.0 * (w * x - y * z)
    sin_p = max(-1.0, min(1.0, sin_p))
    pitch = math.asin(sin_p)

    # Yaw (around Y)
    sin_y = 2.0 * (w * y + x * z)
    cos_y = 1.0 - 2.0 * (x * x + y * y)
    yaw = math.atan2(sin_y, cos_y)

    # Roll (around Z)
    sin_r = 2.0 * (w * z + x * y)
    cos_r = 1.0 - 2.0 * (x * x + z * z)
    roll = math.atan2(sin_r, cos_r)

    return math.degrees(yaw), math.degrees(pitch), math.degrees(roll)


def quaternion_slerp(q0, q1, t):
    """Computes spherical linear interpolation (SLERP) between two unit quaternions.

    Picks the shortest geodesic path on the 3-sphere and falls back to normalized
    linear interpolation when angles are infinitesimally small.

    Args:
        q0 (np.ndarray): Start unit quaternion [w, x, y, z].
        q1 (np.ndarray): Target unit quaternion [w, x, y, z].
        t (float): Interpolation factor in the range [0.0, 1.0].

    Returns:
        np.ndarray: Interpolated unit quaternion [w, x, y, z].
    """
    dot = np.dot(q0, q1)

    # If dot is negative, flip one quaternion to take the shortest path
    if dot < 0.0:
        q1 = -q1
        dot = -dot

    # If inputs are very close, linearly interpolate to avoid division by zero
    if dot > 0.9995:
        result = q0 + t * (q1 - q0)
        return result / np.linalg.norm(result)

    theta_0 = math.acos(dot)
    sin_theta_0 = math.sin(theta_0)

    theta = theta_0 * t
    sin_theta = math.sin(theta)

    s0 = math.cos(theta) - dot * sin_theta / sin_theta_0
    s1 = sin_theta / sin_theta_0

    return (s0 * q0) + (s1 * q1)


def generate_slerp_trajectory(checkpoints, total_frames, ignore_yaw=True, fps=29.97):
    """Generates continuous per-frame Euler angles [yaw, pitch, roll] using cosine easing.

    Interpolates between horizon checkpoints without gimbal flips, matching
    the WebGL Horizon Editor preview coordinates.

    Args:
        checkpoints (list[dict]): List of checkpoint dicts with 'frame', 'pitch', 'roll', 'yaw'.
        total_frames (int): Total frame count of the video.
        ignore_yaw (bool, optional): Locks yaw to 0.0 for pure pitch/roll leveling. Defaults to True.
        fps (float, optional): Framerate in frames per second. Defaults to 29.97.

    Returns:
        np.ndarray: Array of shape (total_frames, 3) containing [yaw, pitch, roll] per frame.
    """
    if not checkpoints or total_frames <= 0:
        return np.zeros((max(1, total_frames), 3), dtype=np.float64)

    # Sanitize checkpoints
    sanitized_cps = []
    for c in checkpoints:
        if not isinstance(c, dict):
            continue
        if 'frame' in c and c['frame'] is not None:
            f = int(round(float(c['frame'])))
        elif 'time' in c and c['time'] is not None:
            f = int(round(float(c['time']) * fps))
        else:
            continue
        p = float(c.get('pitch', 0.0))
        r = float(c.get('roll', 0.0))
        y = 0.0 if ignore_yaw else float(c.get('yaw', 0.0))
        sanitized_cps.append({'frame': f, 'pitch': p, 'roll': r, 'yaw': y})

    sorted_cps = sorted(sanitized_cps, key=lambda c: c['frame'])
    if not sorted_cps:
        return np.zeros((total_frames, 3), dtype=np.float64)

    trajectory = np.zeros((total_frames, 3), dtype=np.float64)
    cp_idx = 0
    num_cps = len(sorted_cps)

    for f in range(total_frames):
        if f <= sorted_cps[0]['frame']:
            c0 = sorted_cps[0]
            trajectory[f] = [c0['yaw'], c0['pitch'], c0['roll']]
        elif f >= sorted_cps[-1]['frame']:
            c_last = sorted_cps[-1]
            trajectory[f] = [c_last['yaw'], c_last['pitch'], c_last['roll']]
        else:
            # Advance pointer to bounding segment in O(1) amortized
            while cp_idx < num_cps - 1 and sorted_cps[cp_idx + 1]['frame'] <= f:
                cp_idx += 1
            cpA = sorted_cps[cp_idx]
            cpB = sorted_cps[cp_idx + 1]

            span = max(1, cpB['frame'] - cpA['frame'])
            t = (f - cpA['frame']) / span
            # Smooth cosine easing
            ease_t = 0.5 * (1.0 - math.cos(t * math.pi))

            yaw_val = 0.0 if ignore_yaw else (cpA['yaw'] + (cpB['yaw'] - cpA['yaw']) * ease_t)
            pitch_val = cpA['pitch'] + (cpB['pitch'] - cpA['pitch']) * ease_t
            roll_val = cpA['roll'] + (cpB['roll'] - cpA['roll']) * ease_t

            trajectory[f] = [yaw_val, pitch_val, roll_val]

    return trajectory


def get_video_info(video_path):
    """Extracts total frame count and framerate from a video using ffprobe.

    Args:
        video_path (str): Filepath to the video.

    Returns:
        tuple[int, float]: Total frame count and framerate (fps).
    """
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate,nb_frames,duration",
        "-of", "json",
        video_path
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True, creationflags=WIN_NO_WINDOW)
        data = json.loads(res.stdout)
        stream = data['streams'][0]

        # FPS
        num, den = map(float, stream['r_frame_rate'].split('/'))
        fps = num / den if den > 0 else 29.97

        # Frame count
        nb_frames = stream.get('nb_frames')
        if nb_frames and nb_frames.isdigit():
            total_frames = int(nb_frames)
        else:
            duration = float(stream.get('duration', 10.0))
            total_frames = int(round(duration * fps))

        return total_frames, fps
    except Exception as e:
        print(f"[Warning] ffprobe probe failed: {e}. Defaulting to 300 frames @ 29.97 fps.")
        return 300, 29.970


def write_sendcmd_file(trajectory, fps, output_file):
    """Writes FFmpeg sendcmd script containing incremental Euler angle deltas for the v360 filter.

    Args:
        trajectory (np.ndarray): Array of shape (N, 3) containing [yaw, pitch, roll] per frame.
        fps (float): Framerate in frames per second.
        output_file (str): Destination path for the generated sendcmd text file.
    """
    time_step = 1.0 / fps
    total_frames = len(trajectory)

    with open(output_file, 'w', encoding='utf-8', newline='\n') as f:
        f.write("# format: delta\n")
        prev_yaw = 0.0
        prev_pitch = 0.0
        prev_roll = 0.0
        for frame, (yaw, pitch, roll) in enumerate(trajectory):
            start_t = frame * time_step
            end_t = (frame + 1) * time_step

            # FFmpeg v360 sendcmd accumulates rotation commands without reset_rot.
            # Output incremental frame-to-frame deltas (curr - prev) to track absolute trajectory.
            dy = yaw - prev_yaw
            dp = pitch - prev_pitch
            dr = roll - prev_roll

            f.write(f"{start_t:.6f}-{end_t:.6f} [enter] v360 yaw {dy:.6f};\n")
            f.write(f"{start_t:.6f}-{end_t:.6f} [enter] v360 pitch {dp:.6f};\n")
            f.write(f"{start_t:.6f}-{end_t:.6f} [enter] v360 roll {dr:.6f};\n")

            prev_yaw = yaw
            prev_pitch = pitch
            prev_roll = roll

    print(f"[Success] Generated sendcmd file with {total_frames} frames: {output_file}")


def generate_checkpoints_graph(checkpoints, trajectory, fps, output_png):
    """Generates a dark-mode visualization plot of horizon trajectory curves and checkpoints.

    Args:
        checkpoints (list[dict]): List of user/auto checkpoint dictionaries.
        trajectory (np.ndarray): Interpolated Euler trajectory array.
        fps (float): Video framerate.
        output_png (str): Destination image filepath for PNG graph.

    Returns:
        bool: True if graph was generated and saved, False otherwise.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.ticker as ticker
        import numpy as np
    except ImportError:
        print("[Warning] matplotlib not installed. Checkpoints graph skipped.")
        return False

    total_frames = len(trajectory)
    if total_frames == 0:
        return False

    times = np.arange(total_frames) / fps
    yaws = trajectory[:, 0]
    pitches = trajectory[:, 1]
    rolls = trajectory[:, 2]

    # Check if yaw has any non-zero movement
    has_yaw = bool(np.any(np.abs(yaws) > 0.001))

    nrows = 3 if has_yaw else 2
    fig, axes = plt.subplots(nrows, 1, figsize=(11, 4.2 * nrows), sharex=True, facecolor="#1a1a2e")
    if nrows == 1:
        axes = [axes]

    for ax in axes:
        ax.set_facecolor("#16213e")
        ax.tick_params(colors="#cccccc")
        for spine in ax.spines.values():
            spine.set_color("#444444")
        ax.grid(True, alpha=0.15, color="#888888")

    fig.suptitle("Horizon Leveling: Checkpoints SLERP Interpolated Trajectory", color="#ffffff", fontsize=13, fontweight="bold", y=0.98)

    # Top secondary X axis for Frame Numbers
    secax = axes[0].secondary_xaxis('top', functions=(lambda t: t * fps, lambda f: f / fps if fps else f))
    secax.set_xlabel(f"Frame Number (at {fps:.2f} fps)", color="#38bdf8", fontsize=9, fontweight="bold", labelpad=8)
    secax.tick_params(colors="#38bdf8")
    secax.xaxis.set_major_locator(ticker.MaxNLocator(nbins=20, steps=[1, 2, 2.5, 5, 10]))

    n_cp = len(checkpoints)
    cp_times = np.zeros(n_cp, dtype=np.float64)
    cp_frames = np.zeros(n_cp, dtype=np.int64)
    cp_pitches = np.zeros(n_cp, dtype=np.float64)
    cp_rolls = np.zeros(n_cp, dtype=np.float64)
    cp_yaws = np.zeros(n_cp, dtype=np.float64)

    for i, cp in enumerate(checkpoints):
        f_num = int(round(float(cp.get('frame', cp.get('time', 0.0) * fps))))
        t_val = float(cp.get('time', f_num / fps if fps else 0.0))
        cp_frames[i] = f_num
        cp_times[i] = t_val
        cp_pitches[i] = float(cp.get('pitch', 0.0))
        cp_rolls[i] = float(cp.get('roll', 0.0))
        cp_yaws[i] = float(cp.get('yaw', 0.0))

    # Adaptive marker styling based on checkpoint count
    if n_cp <= 25:
        m_size = 50
        m_marker = "D"
        m_alpha = 1.0
        m_edge = "#ffffff"
        m_lw = 0.5
    elif n_cp <= 75:
        m_size = 28
        m_marker = "D"
        m_alpha = 0.90
        m_edge = "none"
        m_lw = 0.0
    elif n_cp <= 150:
        m_size = 18
        m_marker = "D"
        m_alpha = 0.85
        m_edge = "none"
        m_lw = 0.0
    else:
        m_size = 12
        m_marker = "o"
        m_alpha = 0.75
        m_edge = "none"
        m_lw = 0.0

    # Smart selection of key checkpoints to annotate (prevent visual clutter & heavy plot)
    def get_annot_indices(values_arr, max_labels=12):
        if n_cp == 0:
            return []
        if n_cp <= 20:
            return list(range(n_cp))

        total_time_span = max(times[-1] if len(times) > 0 else 1.0, 1.0)
        min_dt = total_time_span / float(max_labels + 2)

        # High-priority points: endpoints and extrema
        critical_pts = [0, n_cp - 1]
        arg_min = int(np.argmin(values_arr))
        arg_max = int(np.argmax(values_arr))
        if arg_min not in critical_pts:
            critical_pts.append(arg_min)
        if arg_max not in critical_pts:
            critical_pts.append(arg_max)

        # Build list starting with critical points that aren't too close to each other
        critical_pts.sort(key=lambda idx: cp_times[idx])
        chosen = []
        for idx in critical_pts:
            t = cp_times[idx]
            if not any(abs(t - cp_times[c]) < (min_dt * 0.7) for c in chosen):
                chosen.append(idx)

        # Evenly spaced stride candidates across timeline
        step = max(1, n_cp // max(max_labels, 1))
        for c in range(0, n_cp, step):
            t_cand = cp_times[c]
            if not any(abs(t_cand - cp_times[p]) < min_dt for p in chosen):
                chosen.append(c)

        chosen.sort(key=lambda idx: cp_times[idx])

        # Safety clamp on maximum annotations per axis
        if len(chosen) > max_labels + 2:
            stride = max(1, len(chosen) // max_labels)
            chosen = [chosen[k] for k in range(0, len(chosen), stride)]
            if 0 not in chosen:
                chosen.insert(0, 0)
            if (n_cp - 1) not in chosen:
                chosen.append(n_cp - 1)

        return sorted(list(set(chosen)), key=lambda idx: cp_times[idx])

    def draw_annotations(ax, values_arr, annot_indices):
        ymin_ax, ymax_ax = ax.get_ylim()
        span_ax = max(ymax_ax - ymin_ax, 1e-3)
        total_span_t = max(times[-1] if len(times) > 0 else 1.0, 1.0)

        for k, idx in enumerate(annot_indices):
            f_num = cp_frames[idx]
            t_val = cp_times[idx]
            val = values_arr[idx]
            txt = f"CP{idx+1}\nF#{f_num}: {val:+.1f}°" if n_cp <= 40 else f"CP{idx+1}: {val:+.1f}°"

            # Determine whether to annotate above or below the marker
            # Avoid top axis / legend collision: if high up or near top-right, point downwards
            is_top_heavy = (val > ymin_ax + 0.70 * span_ax) or (t_val > total_span_t * 0.80 and val > ymin_ax + 0.40 * span_ax)
            is_bottom_heavy = (val < ymin_ax + 0.22 * span_ax)

            if is_top_heavy:
                base_offset = -22
            elif is_bottom_heavy:
                base_offset = 16
            else:
                base_offset = 16 if val >= 0 else -22

            # Stagger alternating annotations to avoid any vertical label collisions
            stagger = (5 if base_offset > 0 else -5) if (k % 2 == 1 and n_cp > 20) else 0
            ax.annotate(
                txt, xy=(t_val, val),
                xytext=(0, base_offset + stagger), textcoords="offset points",
                fontsize=7.0 if n_cp > 20 else 7.5, color="#ffea00", fontweight="bold", ha="center",
                arrowprops=dict(arrowstyle="->", color="#ffea00", lw=0.7, alpha=0.85)
            )

    # Subplot 0: Pitch
    axes[0].plot(times, pitches, color="#00e5ff", linewidth=1.8, label="Pitch Trajectory (deg)", alpha=0.95)
    if n_cp > 0:
        axes[0].scatter(cp_times, cp_pitches, color="#ffea00", s=m_size, marker=m_marker,
                        alpha=m_alpha, edgecolors=m_edge, linewidths=m_lw, zorder=6,
                        label=f"Checkpoints ({n_cp})")
    axes[0].axhline(0, color="#ffffff", linewidth=0.8, linestyle=":", alpha=0.6)
    axes[0].set_ylabel("Pitch Angle (deg)", color="#00e5ff", fontweight="bold")
    axes[0].legend(fontsize=8, facecolor="#0f3460", labelcolor="#cccccc", edgecolor="#444", loc="upper right")

    # Subplot 1: Roll
    axes[1].plot(times, rolls, color="#e040fb", linewidth=1.8, label="Roll Trajectory (deg)", alpha=0.95)
    if n_cp > 0:
        axes[1].scatter(cp_times, cp_rolls, color="#ffea00", s=m_size, marker=m_marker,
                        alpha=m_alpha, edgecolors=m_edge, linewidths=m_lw, zorder=6,
                        label=f"Checkpoints ({n_cp})")
    axes[1].axhline(0, color="#ffffff", linewidth=0.8, linestyle=":", alpha=0.6)
    axes[1].set_ylabel("Roll Angle (deg)", color="#e040fb", fontweight="bold")
    axes[1].legend(fontsize=8, facecolor="#0f3460", labelcolor="#cccccc", edgecolor="#444", loc="upper right")

    # Subplot 2: Yaw (if present)
    if has_yaw:
        axes[2].plot(times, yaws, color="#00e676", linewidth=1.8, label="Yaw Trajectory (deg)", alpha=0.95)
        if n_cp > 0:
            axes[2].scatter(cp_times, cp_yaws, color="#ffea00", s=m_size, marker=m_marker,
                            alpha=m_alpha, edgecolors=m_edge, linewidths=m_lw, zorder=6,
                            label=f"Checkpoints ({n_cp})")
        axes[2].axhline(0, color="#ffffff", linewidth=0.8, linestyle=":", alpha=0.6)
        axes[2].set_ylabel("Yaw Angle (deg)", color="#00e676", fontweight="bold")
        axes[2].legend(fontsize=8, facecolor="#0f3460", labelcolor="#cccccc", edgecolor="#444", loc="upper right")
        axes[2].set_xlabel("Time (seconds)", color="#cccccc", fontsize=10)
    else:
        axes[1].set_xlabel("Time (seconds)", color="#cccccc", fontsize=10)

    # Set y-limits with padding first so annotation placement can respect axis bounds
    for ax in axes:
        ymin, ymax = ax.get_ylim()
        span = ymax - ymin
        if span < 2.0:
            mid = (ymax + ymin) / 2.0
            ax.set_ylim(mid - 1.5, mid + 1.5)
        else:
            pad = span * 0.10
            ax.set_ylim(ymin - pad, ymax + pad)

    # Draw smart annotations after axis limits are set
    if n_cp > 0:
        annot_pitch = get_annot_indices(cp_pitches)
        draw_annotations(axes[0], cp_pitches, annot_pitch)

        annot_roll = get_annot_indices(cp_rolls)
        draw_annotations(axes[1], cp_rolls, annot_roll)

        if has_yaw:
            annot_yaw = get_annot_indices(cp_yaws)
            draw_annotations(axes[2], cp_yaws, annot_yaw)

    try:
        fig.subplots_adjust(top=0.91, bottom=0.08, left=0.08, right=0.95, hspace=0.35)
        plt.savefig(output_png, dpi=120, facecolor=fig.get_facecolor(), edgecolor='none')
        plt.close(fig)
        print(f"[OK] Checkpoints graph saved: {output_png}", flush=True)
        return True
    except Exception as e_plot:
        print(f"[Warning] Failed saving checkpoints graph: {e_plot}", flush=True)
        plt.close(fig)
        return False


def rdp_2d(p_arr, r_arr, f_start, f_end, epsilon):
    """Performs 2D Ramer-Douglas-Peucker curve simplification on pitch/roll trajectories.

    Args:
        p_arr (np.ndarray): Array of pitch angles.
        r_arr (np.ndarray): Array of roll angles.
        f_start (int): Start index in array.
        f_end (int): End index in array.
        epsilon (float): Maximum perpendicular distance tolerance in degrees.

    Returns:
        list[int]: Keyframe indices retained after simplification.
    """
    if f_end - f_start <= 1:
        return [f_start, f_end]
    dmax = 0.0
    idx = f_start
    p0, p1 = p_arr[f_start], p_arr[f_end]
    r0, r1 = r_arr[f_start], r_arr[f_end]
    span = max(1, f_end - f_start)
    for i in range(f_start + 1, f_end):
        t = (i - f_start) / span
        p_est = p0 + t * (p1 - p0)
        r_est = r0 + t * (r1 - r0)
        d = math.sqrt((p_arr[i] - p_est)**2 + (r_arr[i] - r_est)**2)
        if d > dmax:
            idx = i
            dmax = d
    if dmax > epsilon:
        left = rdp_2d(p_arr, r_arr, f_start, idx, epsilon)
        right = rdp_2d(p_arr, r_arr, idx, f_end, epsilon)
        return left[:-1] + right
    else:
        return [f_start, f_end]


def detect_visual_horizon_tilt(img_bgr, max_tilt_deg=15.0, optical_target="ground"):
    """Detects visual pitch and roll tilt angles from an equirectangular image.

    Combines:
      1. 3D spherical vertical gravity line projection (OpenCV LSD) on poles, building walls,
         straight tree trunks with physical azimuth projection:
         alpha_i = -cos(phi_i)*roll_corr + sin(phi_i)*pitch_corr.
      2. Water surface detection (omnidirectional 360° horizontal plane).
      3. RANSAC sinusoidal sky-ground/boundary edge fitting with full inlier consensus refits
         and azimuthal span validation.

    Args:
        img_bgr (np.ndarray): Equirectangular BGR image frame.
        max_tilt_deg (float, optional): Maximum allowable tilt threshold in degrees. Defaults to 15.0.

    Returns:
        tuple[float, float]: Estimated corrective (pitch, roll) angles in degrees.
    """
    try:
        import cv2
        small = cv2.resize(img_bgr, (960, 480))
        h, w = small.shape[:2]
        deg_per_px = 180.0 / float(h)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)

        # 1. Vertical Gravity Structures (OpenCV LSD with 3D Spherical Projection)
        # In equirectangular projection, structures parallel to gravity (poles, building walls,
        # vertical trunks) appear along meridians.
        # At azimuth phi_i = 2*pi*(x/W - 0.5), small camera rotation (roll, pitch) tilts the line by:
        #   alpha_i = -cos(phi_i) * roll_corr + sin(phi_i) * pitch_corr
        mid_gray = gray[int(0.15 * h):int(0.85 * h), :]
        lsd = cv2.createLineSegmentDetector(0)
        lines, _, _, _ = lsd.detect(mid_gray)

        vert_roll, vert_pitch = None, None
        if lines is not None:
            A_rows, b_vals, weights = [], [], []
            for line in lines:
                pts = line.flatten()
                x1, y1_, x2, y2_ = pts[0], pts[1], pts[2], pts[3]
                length = float(np.hypot(x2 - x1, y2_ - y1_))
                if length < 25:
                    continue
                dx = x2 - x1
                dy = y2_ - y1_
                if dy < 0:
                    dx, dy = -dx, -dy
                ang_deg = np.degrees(np.arctan2(dx, dy))
                # Strict plumb threshold: true vertical structures are within +/- 4.5 deg
                # Discards slanted tree trunks, branches, wires, and hillside cuts
                if abs(ang_deg) > 4.5:
                    continue
                mid_x = 0.5 * (x1 + x2)
                phi = 2.0 * math.pi * (mid_x / float(w) - 0.5)
                A_rows.append([-math.cos(phi), math.sin(phi)])
                b_vals.append(ang_deg)
                weights.append(length)

            if len(A_rows) >= 3:
                A_mat = np.array(A_rows)
                b_vec = np.array(b_vals)
                w_vec = np.array(weights)
                Aw = A_mat * w_vec[:, None]
                bw = b_vec * w_vec
                # Soft Bayesian prior towards 0.0 deg (prevents isolated trees from pulling roll past realistic limits)
                lam = 25.0
                sol1 = np.linalg.solve(np.dot(Aw.T, Aw) + lam * np.eye(2), np.dot(Aw.T, bw))
                resids = np.abs(np.dot(A_mat, sol1) - b_vec)
                inliers = np.where(resids < 2.0)[0]
                if len(inliers) >= 3:
                    Aw2 = A_mat[inliers] * w_vec[inliers, None]
                    bw2 = b_vec[inliers] * w_vec[inliers]
                    sol = np.linalg.solve(np.dot(Aw2.T, Aw2) + lam * np.eye(2), np.dot(Aw2.T, bw2))
                else:
                    sol = sol1

                # Soft perceptual damping (0.6x) to match human visual leveling preference
                vert_roll = float(np.clip(sol[0] * 0.6, -3.0, 3.0))
                vert_pitch = float(np.clip(sol[1] * 0.5, -2.5, 2.5))

        # 2. Water Surface Detection (Omnidirectional 360° Horizontal Plane)
        is_water = (hsv[:, :, 0] > 80) & (hsv[:, :, 0] < 135) & (hsv[:, :, 1] > 30) & (np.arange(h)[:, None] > 0.45 * h)
        water_coverage = np.sum(is_water) / float(w * h * 0.5)
        has_water = water_coverage > 0.25

        # 3. Horizontal Edge Contour Fitting (Sky-Ground or Sky-Water)
        blurred = cv2.GaussianBlur(gray, (7, 7), 0)
        grad_y = cv2.Sobel(blurred, cv2.CV_64F, 0, 1, ksize=5)

        max_dy = int(round(max_tilt_deg / deg_per_px))
        y_min = max(0, int(h / 2 - max_dy * 1.5))
        y_max = min(h, int(h / 2 + max_dy * 1.5))

        col_points_x, col_points_y = [], []
        target_mode = str(optical_target).lower()
        for x in range(0, w, 4):
            col = np.abs(grad_y[y_min:y_max, x])
            if len(col) >= 3 and col.max() > 18:
                thresh = max(16.0, 0.25 * float(col.max()))
                is_peak = (col[1:-1] > col[:-2]) & (col[1:-1] >= col[2:]) & (col[1:-1] >= thresh)
                candidate_indices = np.where(is_peak)[0] + 1
                if len(candidate_indices) > 0:
                    if target_mode == "ground":
                        # Ground/Road contact line: pick the candidate closest to the true geometric horizon equator (h/2)
                        candidate_ys = y_min + candidate_indices
                        best_k = int(np.argmin(np.abs(candidate_ys - (h / 2.0))))
                        selected_idx = candidate_indices[best_k]
                    else:
                        # Skyline/Canopy: pick the topmost peak in column (sky-treetop/building roof boundary)
                        selected_idx = candidate_indices[0]
                else:
                    selected_idx = int(np.argmax(col))

                col_points_x.append(x)
                col_points_y.append(y_min + int(selected_idx))

        edge_pitch, edge_roll = None, None
        if len(col_points_x) >= 30:
            xa = np.array(col_points_x, dtype=np.float64)
            ya = np.array(col_points_y, dtype=np.float64)
            phi = 2.0 * math.pi * (xa / float(w) - 0.5)
            span = float(phi.max() - phi.min())
            if span > 2.5:  # Wide azimuthal span required (>= 145 deg)
                M = np.column_stack([np.ones_like(phi), np.cos(phi), np.sin(phi)])
                best_inl = []
                rng = np.random.RandomState(42)
                for _ in range(40):
                    idx = rng.choice(len(xa), size=3, replace=False)
                    try:
                        s, _, _, _ = np.linalg.lstsq(M[idx], ya[idx], rcond=None)
                    except Exception:
                        continue
                    pred = np.dot(M, s)
                    inl = np.where(np.abs(ya - pred) <= 0.035 * h)[0]
                    if len(inl) > len(best_inl):
                        best_inl = inl
                if len(best_inl) >= 25:
                    sol_refit, _, _, _ = np.linalg.lstsq(M[best_inl], ya[best_inl], rcond=None)
                    y0, A, B = sol_refit
                    if abs(y0 - (h / 2.0)) <= 0.15 * h:
                        # In equirectangular projection: y(phi) = y0 + A*cos(phi) + B*sin(phi)
                        # Corrective counter-rotation: pitch = -A * deg_per_px, roll = -B * deg_per_px
                        edge_pitch = float(np.clip(-A * deg_per_px, -max_tilt_deg, max_tilt_deg))
                        edge_roll = float(np.clip(-B * deg_per_px, -max_tilt_deg, max_tilt_deg))

        # 4. Multi-cue Fusion
        if has_water and edge_roll is not None:
            final_roll = edge_roll
            final_pitch = edge_pitch if edge_pitch is not None else 0.0
        elif vert_roll is not None:
            final_roll = vert_roll
            if target_mode == "ground" and edge_pitch is not None:
                final_pitch = edge_pitch
            else:
                final_pitch = vert_pitch
        elif edge_roll is not None:
            if target_mode == "ground":
                final_roll = edge_roll
                final_pitch = edge_pitch if edge_pitch is not None else 0.0
            else:
                # Dampen terrestrial edges (treetops/hills) towards neutral 0
                final_roll = float(np.clip(0.3 * edge_roll, -2.0, 2.0))
                final_pitch = float(np.clip(0.3 * (edge_pitch if edge_pitch is not None else 0.0), -2.0, 2.0))
        else:
            final_roll = 0.0
            final_pitch = 0.0

        return round(float(final_pitch), 2), round(float(final_roll), 2)
    except Exception:
        return 0.0, 0.0



def auto_detect_from_video(video_path, fps=29.97, sample_interval_sec=1.5, epsilon=1.5, lock_roll=False, optical_target="ground"):
    """Extracts visual horizon checkpoints directly from sampled equirectangular video frames.

    Applies temporal median filtering and rate-of-change clamping before RDP curve
    simplification to eliminate high-frequency see-saw pitch balancing.

    Args:
        video_path (str): Filepath to the equirectangular video.
        fps (float, optional): Video framerate. Defaults to 29.97.
        sample_interval_sec (float, optional): Sampling interval in seconds. Defaults to 1.5.
        epsilon (float, optional): RDP simplification tolerance in degrees. Defaults to 1.5.
        lock_roll (bool, optional): Whether to lock roll angles to 0.0. Defaults to False.
        optical_target (str, optional): 'ground' (contact line) or 'skyline' (canopy). Defaults to 'ground'.

    Returns:
        list[dict]: Simplified horizon checkpoint dicts with 'frame', 'pitch', 'roll', 'yaw'.
    """
    if not os.path.exists(video_path):
        return []
    try:
        import cv2
        cap = cv2.VideoCapture(video_path)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0:
            cap.release()
            return []

        # Adaptive sampling: on short clips (<= 3 seconds), sample densely (every 4-5 frames)
        # On longer clips, sample at min(sample_interval_sec, 0.75s) to capture subtle dynamics
        if total_frames <= int(round(fps * 3.0)):
            step_frames = max(1, min(5, total_frames // 6))
        else:
            step_frames = max(5, int(round(fps * min(sample_interval_sec, 0.75))))
        sample_frames = list(range(0, total_frames, step_frames))
        if sample_frames[-1] != total_frames - 1:
            sample_frames.append(total_frames - 1)

        frames, pitches, rolls = [], [], []
        for f_idx in sample_frames:
            cap.set(cv2.CAP_PROP_POS_FRAMES, f_idx)
            ret, frame = cap.read()
            if not ret:
                continue
            p, r = detect_visual_horizon_tilt(frame, optical_target=optical_target)
            frames.append(f_idx)
            pitches.append(p)
            rolls.append(r)
        cap.release()

        if not frames:
            return []

        # 1. Temporal Median Filter: reject isolated frame fitting glitches
        p_arr = np.array(pitches, dtype=np.float64)
        r_arr = np.array(rolls, dtype=np.float64)
        if len(frames) >= 3:
            try:
                from scipy.signal import medfilt
                p_arr = medfilt(p_arr, kernel_size=3)
                r_arr = medfilt(r_arr, kernel_size=3)
            except Exception:
                p_smooth = p_arr.copy()
                r_smooth = r_arr.copy()
                for i in range(1, len(frames) - 1):
                    p_smooth[i] = float(np.median(p_arr[i-1:i+2]))
                    r_smooth[i] = float(np.median(r_arr[i-1:i+2]))
                p_arr, r_arr = p_smooth, r_smooth

        # 2. Anti-Balancing Rate Limiter: clamp max inter-sample angle acceleration (<= 4.0 deg/sec)
        max_rate_deg_per_sec = 4.0
        for i in range(1, len(frames)):
            dt = max(0.01, (frames[i] - frames[i - 1]) / fps)
            max_delta = max_rate_deg_per_sec * dt
            dp = p_arr[i] - p_arr[i - 1]
            dr = r_arr[i] - r_arr[i - 1]
            if abs(dp) > max_delta:
                p_arr[i] = p_arr[i - 1] + math.copysign(max_delta, dp)
            if abs(dr) > max_delta:
                r_arr[i] = r_arr[i - 1] + math.copysign(max_delta, dr)

        # 3. RDP Simplification
        if lock_roll:
            r_arr = np.zeros_like(r_arr)
        kf_indices = rdp_2d(p_arr, r_arr, 0, len(frames) - 1, epsilon)
        checkpoints = []
        for idx in kf_indices:
            checkpoints.append({
                'frame': frames[idx],
                'pitch': round(float(p_arr[idx]), 2),
                'roll': round(float(r_arr[idx]), 2),
                'yaw': 0.0
            })
        return checkpoints
    except Exception as e:
        print(f"[Warning] Video auto-detect failed: {e}")
        return []


def auto_detect_from_telemetry(telemetry_file, fps=29.97, baseline_pitch=0.0, baseline_roll=0.0, epsilon=2.5, neutral_baseline=False):
    """Extracts natural horizon checkpoints from Samsung Gear 360 telemetry IMU logs.

    Args:
        telemetry_file (str): Path to raw telemetry text file.
        fps (float, optional): Target video framerate. Defaults to 29.97.
        baseline_pitch (float, optional): Static pitch baseline calibration. Defaults to 0.0.
        baseline_roll (float, optional): Static roll baseline calibration. Defaults to 0.0.
        epsilon (float, optional): RDP simplification tolerance. Defaults to 2.5.
        neutral_baseline (bool, optional): Whether to zero out baseline corrections. Defaults to False.

    Returns:
        list[dict]: Checkpoint dicts extracted from telemetry.
    """
def parse_raw_telemetry_angles(telemetry_file):
    """Extracts raw (roll, pitch, yaw) tuples from .gcsv or .txt telemetry file."""
    if not os.path.exists(telemetry_file):
        return []

    angles = []
    if telemetry_file.lower().endswith('.gcsv'):
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from extract_telemetry import parse_companion_file
            rot_data, _ = parse_companion_file(telemetry_file)
            if rot_data:
                angles = rot_data
        except Exception:
            pass

    lines = []
    if not angles:
        with open(telemetry_file, 'r', encoding='utf-8', errors='ignore') as f:
            lines = [l.strip() for l in f if l.strip()]
        if not lines:
            return []

        if 'GYROFLOW' in lines[0] or (',' in lines[0] and '\t' not in lines[0]):
            try:
                sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                from extract_telemetry import parse_companion_file
                rot_data, _ = parse_companion_file(telemetry_file)
                if rot_data:
                    angles = rot_data
            except Exception:
                pass

    if not angles and lines:
        headers = [h.strip() for h in lines[0].split('\t')]
        try:
            roll_idx = headers.index('AngleX(deg)') if 'AngleX(deg)' in headers else None
            pitch_idx = headers.index('AngleY(deg)') if 'AngleY(deg)' in headers else None
            yaw_idx = headers.index('AngleZ(deg)') if 'AngleZ(deg)' in headers else None
        except ValueError:
            return []

        if roll_idx is None or pitch_idx is None:
            return []

        for line in lines[1:]:
            parts = line.split('\t')
            if len(parts) > max(roll_idx, pitch_idx):
                try:
                    r = float(parts[roll_idx])
                    p = float(parts[pitch_idx])
                    y = float(parts[yaw_idx]) if yaw_idx is not None and len(parts) > yaw_idx else 0.0
                    angles.append((r, p, y))
                except ValueError:
                    continue

    return angles


def auto_detect_from_telemetry(telemetry_file, fps=29.97, baseline_pitch=0.0, baseline_roll=0.0, epsilon=2.5, neutral_baseline=False):
    """Extracts natural horizon checkpoints from Samsung Gear 360 telemetry IMU logs.

    Args:
        telemetry_file (str): Path to raw telemetry text file.
        fps (float, optional): Target video framerate. Defaults to 29.97.
        baseline_pitch (float, optional): Static pitch baseline calibration. Defaults to 0.0.
        baseline_roll (float, optional): Static roll baseline calibration. Defaults to 0.0.
        epsilon (float, optional): RDP simplification tolerance. Defaults to 2.5.
        neutral_baseline (bool, optional): Whether to zero out baseline corrections. Defaults to False.

    Returns:
        list[dict]: Checkpoint dicts extracted from telemetry.
    """
    angles = parse_raw_telemetry_angles(telemetry_file)
    if not angles:
        return []

    n = len(angles)
    w_sec = 0.50 if epsilon <= 2.5 else 1.5
    w_size = max(3, int(round(fps * w_sec)))
    roll_raw = np.array([a[0] for a in angles], dtype=np.float64)
    pitch_raw = np.array([a[1] for a in angles], dtype=np.float64)

    kernel = np.ones(w_size) / w_size
    roll_s = np.convolve(roll_raw, kernel, mode='same')
    pitch_s = np.convolve(pitch_raw, kernel, mode='same')

    p_base = baseline_pitch if abs(baseline_pitch) > 0.01 else float(np.median(pitch_raw))
    r_base = baseline_roll if abs(baseline_roll) > 0.01 else float(np.median(roll_raw))

    pitch_corr = -(pitch_s - p_base)
    roll_corr = -(roll_s - r_base)

    kf_indices = rdp_2d(pitch_corr, roll_corr, 0, n - 1, epsilon)

    checkpoints = []
    for f in kf_indices:
        checkpoints.append({
            'frame': int(f),
            'pitch': 0.0 if neutral_baseline else round(float(pitch_corr[f]), 2),
            'roll': 0.0 if neutral_baseline else round(float(roll_corr[f]), 2),
            'yaw': 0.0
        })

    return checkpoints


def auto_detect_from_motion(motion_file, fps=29.97, epsilon=2.5, neutral_baseline=False):
    """Extracts natural horizon checkpoints from .kopf360motion or .kabsch360motion files.

    Args:
        motion_file (str): Path to motion tracking data file.
        fps (float, optional): Video framerate. Defaults to 29.97.
        epsilon (float, optional): RDP simplification tolerance in degrees. Defaults to 2.5.
        neutral_baseline (bool, optional): Whether to zero out baseline. Defaults to False.

    Returns:
        list[dict]: Checkpoint dicts extracted from motion data.
    """
    if not os.path.exists(motion_file):
        return []

    frames, pitches, rolls, yaws = [], [], [], []
    with open(motion_file, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split()
            if len(parts) >= 5:
                try:
                    f_num = int(parts[0])
                    y_deg = float(parts[2])
                    p_deg = float(parts[3])
                    r_deg = float(parts[4])
                    frames.append(f_num)
                    yaws.append(y_deg)
                    pitches.append(p_deg)
                    rolls.append(r_deg)
                except ValueError:
                    continue

    if not frames:
        return []

    n = len(frames)
    p_arr = np.array(pitches, dtype=np.float64)
    r_arr = np.array(rolls, dtype=np.float64)

    kf_indices = rdp_2d(p_arr, r_arr, 0, n - 1, epsilon)
    checkpoints = []
    for idx in kf_indices:
        f_num = frames[idx]
        checkpoints.append({
            'frame': f_num,
            'pitch': 0.0 if neutral_baseline else round(float(p_arr[idx]), 2),
            'roll': 0.0 if neutral_baseline else round(float(r_arr[idx]), 2),
            'yaw': 0.0 if neutral_baseline else round(float(yaws[idx]), 2)
        })

    return checkpoints


def seed_cadence_checkpoints(total_frames, fps=29.97, interval_sec=0.5, max_checkpoints=None, adaptive=False,
                            start_frame=0, end_frame=None, initial_angles=(0.0, 0.0, 0.0),
                            telemetry_file=None, motion_file=None, roll_damping=0.70):
    """Generates regular cadence keyframes guarded against explosion on long videos.

    Args:
        total_frames (int): Total number of frames in the video.
        fps (float, optional): Framerate in frames per second. Defaults to 29.97.
        interval_sec (float, optional): Desired cadence interval in seconds. Defaults to 0.5.
        max_checkpoints (int | None, optional): Optional maximum keyframes budget. Defaults to None (unconstrained).
        adaptive (bool, optional): Whether to dynamically scale interval if max_checkpoints is set. Defaults to False.
        start_frame (int, optional): Starting frame index. Defaults to 0.
        end_frame (int | None, optional): Ending frame index (inclusive). Defaults to None (total_frames - 1).
        initial_angles (tuple[float, float, float], optional): Default (pitch, roll, yaw) values. Defaults to (0.0, 0.0, 0.0).
        telemetry_file (str | None, optional): Path to telemetry file to sample angles from. Defaults to None.
        motion_file (str | None, optional): Path to motion tracker file to sample angles from. Defaults to None.
        roll_damping (float, optional): Lateral body sway damping multiplier (0.0 to 1.0). Defaults to 0.70.

    Returns:
        list[dict]: List of checkpoint dictionaries with 'frame', 'time', 'pitch', 'roll', 'yaw'.
    """
    if total_frames <= 0:
        return []

    start_f = max(0, int(start_frame))
    end_f = int(end_frame) if end_frame is not None else (total_frames - 1)
    end_f = max(start_f, min(total_frames - 1, end_f))
    span_frames = max(1, end_f - start_f)
    duration_sec = span_frames / max(0.1, fps)

    step_sec = max(0.05, float(interval_sec))
    est_count = int(math.ceil(duration_sec / step_sec)) + 1

    if adaptive and max_checkpoints is not None and max_checkpoints > 0 and est_count > max_checkpoints:
        step_sec = duration_sec / float(max(2, int(max_checkpoints) - 1))
        print(f"[Horizon Cadence] Video duration {duration_sec:.1f}s exceeds limit for {interval_sec:.2f}s cadence. "
              f"Adaptively scaled interval to {step_sec:.2f}s (target: <= {max_checkpoints} keyframes).", flush=True)

    step_frames = max(1, int(round(fps * step_sec)))
    frame_indices = list(range(start_f, end_f, step_frames))
    if not frame_indices or frame_indices[-1] != end_f:
        frame_indices.append(end_f)

    # Attempt to sample real pitch and roll curves if motion or telemetry is provided
    p_arr = None
    r_arr = None
    if motion_file and os.path.exists(motion_file) and os.path.getsize(motion_file) > 50:
        frames_m, pitches_m, rolls_m = [], [], []
        with open(motion_file, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'): continue
                parts = line.split()
                if len(parts) >= 5:
                    try:
                        frames_m.append(int(parts[0]))
                        pitches_m.append(float(parts[3]))
                        rolls_m.append(float(parts[4]))
                    except ValueError:
                        continue
        if frames_m:
            grid = np.arange(total_frames)
            p_interp = np.interp(grid, frames_m, pitches_m)
            r_interp = np.interp(grid, frames_m, rolls_m)
            p_arr = -(p_interp - float(np.median(p_interp)))
            r_arr = -(r_interp - float(np.median(r_interp)))
    elif telemetry_file and os.path.exists(telemetry_file) and os.path.getsize(telemetry_file) > 50:
        raw_angles = parse_raw_telemetry_angles(telemetry_file)
        if raw_angles:
            r_raw = np.array([a[0] for a in raw_angles], dtype=np.float64)
            p_raw = np.array([a[1] for a in raw_angles], dtype=np.float64)
            if len(p_raw) != total_frames:
                orig_grid = np.linspace(0, total_frames - 1, len(p_raw))
                target_grid = np.arange(total_frames)
                p_raw = np.interp(target_grid, orig_grid, p_raw)
                r_raw = np.interp(target_grid, orig_grid, r_raw)
            p_base = float(np.median(p_raw))
            r_base = float(np.median(r_raw))
            p_arr = -(p_raw - p_base)
            r_arr = -(r_raw - r_base)

    p0, r0, y0 = initial_angles
    damp = max(0.0, min(1.0, float(roll_damping)))
    checkpoints = []
    for f in frame_indices:
        t = f / max(0.1, fps)
        p_val = float(p_arr[f]) if p_arr is not None and f < len(p_arr) else p0
        r_val = (float(r_arr[f]) * damp) if r_arr is not None and f < len(r_arr) else r0
        checkpoints.append({
            'frame': int(f),
            'time': round(float(t), 2),
            'pitch': round(float(p_val), 2),
            'roll': round(float(r_val), 2),
            'yaw': round(float(y0), 2)
        })

    return checkpoints


def detect_pitch_extrema_checkpoints(total_frames, fps=29.97, min_prominence_deg=0.8, max_checkpoints=None,
                                      pitch_curve=None, roll_curve=None, telemetry_file=None, motion_file=None,
                                      video_file=None, neutral_baseline=False, start_frame=0, end_frame=None,
                                      baseline_pitch=0.0, baseline_roll=0.0, roll_damping=0.70):
    """Identifies natural cadence keyframes at vertical pitch turning points (crests and troughs).

    Extracts local pitch extrema where d(pitch)/dt = 0 with swing prominence >= min_prominence_deg.
    Guarantees that flat camera motion generates zero redundant keyframes, while periodic gait
    nodding produces keyframes precisely aligned with stride apices. Applies roll_damping
    to filter lateral hip/body sway while preserving genuine horizon leveling.

    Args:
        total_frames (int): Total number of video frames.
        fps (float, optional): Framerate in frames per second. Defaults to 29.97.
        min_prominence_deg (float, optional): Minimum pitch swing (prominence) in degrees. Defaults to 0.8.
        max_checkpoints (int | None, optional): Optional maximum keyframes budget. Defaults to None (unconstrained).
        pitch_curve (np.ndarray | list[float] | None, optional): Explicit per-frame pitch angles. Defaults to None.
        roll_curve (np.ndarray | list[float] | None, optional): Explicit per-frame roll angles. Defaults to None.
        telemetry_file (str | None, optional): Path to IMU log (.txt / .gcsv). Defaults to None.
        motion_file (str | None, optional): Path to motion tracker file (.kopf360motion). Defaults to None.
        video_file (str | None, optional): Path to video file for visual horizon tracking. Defaults to None.
        neutral_baseline (bool, optional): If True, zeros pitch/roll values. Defaults to False.
        start_frame (int, optional): Range start frame. Defaults to 0.
        end_frame (int | None, optional): Range end frame. Defaults to None (total_frames - 1).
        baseline_pitch (float, optional): Static baseline pitch offset in degrees. Defaults to 0.0 (auto-centers if 0).
        baseline_roll (float, optional): Static baseline roll offset in degrees. Defaults to 0.0 (auto-centers if 0).
        roll_damping (float, optional): Lateral body sway damping multiplier (0.0 to 1.0). Defaults to 0.70.

    Returns:
        list[dict]: Checkpoint dicts with 'frame', 'time', 'pitch', 'roll', 'yaw', and 'type'.
    """
    if total_frames <= 0:
        return []

    start_f = max(0, int(start_frame))
    end_f = int(end_frame) if end_frame is not None else (total_frames - 1)
    end_f = max(start_f, min(total_frames - 1, end_f))

    # Resolve pitch and roll curves
    p_arr = None
    r_arr = None

    if pitch_curve is not None and len(pitch_curve) > 0:
        p_arr = np.array(pitch_curve, dtype=np.float64)
        if roll_curve is not None and len(roll_curve) == len(p_arr):
            r_arr = np.array(roll_curve, dtype=np.float64)
        else:
            r_arr = np.zeros_like(p_arr)
    elif telemetry_file and os.path.exists(telemetry_file) and os.path.getsize(telemetry_file) > 50:
        raw_angles = parse_raw_telemetry_angles(telemetry_file)
        if raw_angles:
            r_raw = np.array([a[0] for a in raw_angles], dtype=np.float64)
            p_raw = np.array([a[1] for a in raw_angles], dtype=np.float64)
            if len(p_raw) != total_frames:
                orig_grid = np.linspace(0, total_frames - 1, len(p_raw))
                target_grid = np.arange(total_frames)
                p_raw = np.interp(target_grid, orig_grid, p_raw)
                r_raw = np.interp(target_grid, orig_grid, r_raw)
            p_base = float(np.median(p_raw)) if neutral_baseline or abs(baseline_pitch) < 0.01 else baseline_pitch
            r_base = float(np.median(r_raw)) if neutral_baseline or abs(baseline_roll) < 0.01 else baseline_roll
            p_arr = -(p_raw - p_base)
            r_arr = -(r_raw - r_base)
    elif motion_file and os.path.exists(motion_file) and os.path.getsize(motion_file) > 50:
        frames_m, pitches_m, rolls_m = [], [], []
        with open(motion_file, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'): continue
                parts = line.split()
                if len(parts) >= 5:
                    try:
                        frames_m.append(int(parts[0]))
                        pitches_m.append(float(parts[3]))
                        rolls_m.append(float(parts[4]))
                    except ValueError:
                        continue
        if frames_m:
            grid = np.arange(total_frames)
            p_interp = np.interp(grid, frames_m, pitches_m)
            r_interp = np.interp(grid, frames_m, rolls_m)
            p_base = float(np.median(p_interp)) if neutral_baseline or abs(baseline_pitch) < 0.01 else baseline_pitch
            r_base = float(np.median(r_interp)) if neutral_baseline or abs(baseline_roll) < 0.01 else baseline_roll
            p_arr = -(p_interp - p_base)
            r_arr = -(r_interp - r_base)
    elif video_file and os.path.exists(video_file):
        try:
            cps_v = auto_detect_from_video(video_file, fps=fps, sample_interval_sec=0.5, epsilon=1.0)
            if cps_v:
                f_v = [c['frame'] for c in cps_v]
                p_v = [c['pitch'] for c in cps_v]
                r_v = [c['roll'] for c in cps_v]
                grid = np.arange(total_frames)
                p_arr = np.interp(grid, f_v, p_v)
                r_arr = np.interp(grid, f_v, r_v)
        except Exception:
            pass

    # Fallback to cadence if no continuous pitch trajectory can be extracted
    if p_arr is None or len(p_arr) < 5:
        print("[Horizon Cadence] No pitch motion data found for extrema detection; falling back to cadence.", flush=True)
        return seed_cadence_checkpoints(total_frames, fps=fps, interval_sec=0.5, max_checkpoints=max_checkpoints,
                                        adaptive=(max_checkpoints is not None and max_checkpoints > 0),
                                        start_frame=start_f, end_frame=end_f, roll_damping=roll_damping)

    # Sub-slice to requested range
    p_sub = p_arr[start_f:end_f + 1]
    r_sub = r_arr[start_f:end_f + 1] if r_arr is not None else np.zeros_like(p_sub)

    if len(p_sub) < 3:
        return seed_cadence_checkpoints(total_frames, fps=fps, interval_sec=0.5, max_checkpoints=max_checkpoints,
                                        adaptive=(max_checkpoints is not None and max_checkpoints > 0),
                                        start_frame=start_f, end_frame=end_f, roll_damping=roll_damping)

    # 1. Smooth pitch curve with rolling window (~5 frames / 0.15s) to eliminate sensor micro-vibrations
    w_pitch = max(3, min(15, int(round(fps * 0.20))))
    if w_pitch % 2 == 0:
        w_pitch += 1
    kernel_pitch = np.ones(w_pitch) / float(w_pitch)
    p_smooth = np.convolve(p_sub, kernel_pitch, mode='same')

    # Filter lateral hip sway on roll with a wider window (~0.60s) and roll_damping factor
    damp = max(0.0, min(1.0, float(roll_damping)))
    w_roll = max(5, min(31, int(round(fps * 0.60))))
    if w_roll % 2 == 0:
        w_roll += 1
    kernel_roll = np.ones(w_roll) / float(w_roll)
    r_smooth = np.convolve(r_sub, kernel_roll, mode='same') * damp

    # 2. Find local extrema (zero-crossings of first difference)
    dp = np.diff(p_smooth)
    extrema = []
    for i in range(1, len(dp)):
        if dp[i-1] > 0 and dp[i] <= 0:
            extrema.append((i, 'crest', float(p_smooth[i]), float(r_smooth[i])))
        elif dp[i-1] < 0 and dp[i] >= 0:
            extrema.append((i, 'trough', float(p_smooth[i]), float(r_smooth[i])))

    # 3. Calculate prominence for each extremum
    prominent = []
    prom_thresh = max(0.2, float(min_prominence_deg))
    for idx, (f_loc, ext_type, p_val, r_val) in enumerate(extrema):
        left_opp = [e[2] for e in extrema[:idx] if e[1] != ext_type]
        right_opp = [e[2] for e in extrema[idx+1:] if e[1] != ext_type]
        left_diff = abs(p_val - left_opp[-1]) if left_opp else abs(p_val - p_smooth[0])
        right_diff = abs(p_val - right_opp[0]) if right_opp else abs(p_val - p_smooth[-1])
        prominence = min(left_diff, right_diff)
        if prominence >= prom_thresh:
            prominent.append((f_loc, ext_type, p_val, r_val, prominence))

    # 4. Optional keyframe budget guard (only applied when max_checkpoints is explicitly set)
    if max_checkpoints is not None and max_checkpoints > 0:
        budget = max(2, int(max_checkpoints) - 2)
        if len(prominent) > budget:
            prominent.sort(key=lambda x: x[4], reverse=True)
            prominent = prominent[:budget]
            prominent.sort(key=lambda x: x[0])

    # 5. Assemble final checkpoints including boundary frames start_f and end_f
    selected_frames = {start_f: ('boundary', float(p_sub[0]), float(r_sub[0]) * damp)}
    for f_loc, ext_type, p_val, r_val, _ in prominent:
        abs_f = start_f + f_loc
        selected_frames[abs_f] = (ext_type, p_val, r_val)
    selected_frames[end_f] = ('boundary', float(p_sub[-1]), float(r_sub[-1]) * damp)

    checkpoints = []
    for f in sorted(selected_frames.keys()):
        ext_type, p_val, r_val = selected_frames[f]
        t = f / max(0.1, fps)
        checkpoints.append({
            'frame': int(f),
            'time': round(float(t), 2),
            'pitch': 0.0 if neutral_baseline else round(float(p_val), 2),
            'roll': 0.0 if neutral_baseline else round(float(r_val), 2),
            'yaw': 0.0,
            'type': ext_type
        })

    return checkpoints


def load_checkpoints(json_file):
    """Loads checkpoints, ignoreYaw flag, and framerate from a JSON checkpoint file.

    Args:
        json_file (str): Filepath to horizon_checkpoints.json.

    Returns:
        tuple[list[dict], bool, float]: Checkpoints list, ignore_yaw flag, and framerate.
    """
    if not os.path.exists(json_file):
        return [], True, 29.97
    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    checkpoints = data.get('checkpoints', []) if isinstance(data, dict) else data
    fps = data.get('fps', 29.97) if isinstance(data, dict) else 29.97
    ignore_yaw = data.get('ignoreYaw', True) if isinstance(data, dict) else True
    return checkpoints, ignore_yaw, fps


def load_checkpoints_with_metadata(json_file):
    """Loads checkpoints, metadata/parameters, ignoreYaw flag, and framerate from a JSON checkpoint file."""
    if not os.path.exists(json_file):
        return [], True, 29.97, {}
    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    checkpoints = data.get('checkpoints', []) if isinstance(data, dict) else data
    fps = data.get('fps', 29.97) if isinstance(data, dict) else 29.97
    ignore_yaw = data.get('ignoreYaw', True) if isinstance(data, dict) else True
    parameters = data.get('parameters', {}) if (isinstance(data, dict) and isinstance(data.get('parameters'), dict)) else {}
    return checkpoints, ignore_yaw, fps, parameters


def format_horizon_params_log(out_base, data, video_name=""):
    """Generate a clean, structured human-readable audit log of horizon stabilization parameters.

    Args:
        out_base (str): Test or target run base identifier.
        data (dict): Checkpoint dictionary containing checkpoints and parameters/metadata.
        video_name (str, optional): Target video filename or path.

    Returns:
        str: Formatted audit log text.
    """
    cps = data.get("checkpoints", []) if isinstance(data, dict) else []
    params = data.get("parameters", {}) if (isinstance(data, dict) and isinstance(data.get("parameters"), dict)) else {}
    import time
    ts_now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

    strategy = params.get("detection_type") or data.get("sourceType") or "Manual / Custom"
    source = params.get("source") or data.get("source") or "N/A"
    density = params.get("density") or ("epsilon=" + str(params.get("epsilon", "N/A")))
    is_vision = (source == "vision" or "vision" in str(strategy).lower())
    prom_val = params.get("min_prominence") or params.get("prominence")
    if is_vision or prom_val is None:
        prominence = "N/A"
    else:
        prominence = f"{prom_val}°"

    damp_val = params.get("roll_damping")
    if is_vision or (damp_val is None and "cadence" in str(strategy).lower()):
        roll_damp = "N/A"
    else:
        roll_damp = f"{damp_val}x" if damp_val is not None else "0.70x"
    b_pitch = params.get("baseline_pitch", 0.0)
    b_roll = params.get("baseline_roll", 0.0)
    ignore_yaw = data.get("ignoreYaw", True)
    fps = data.get("fps", 29.97)
    total_f = data.get("totalFrames", 0)
    range_mode = params.get("range_mode", "Full Video")
    video_src = video_name or params.get("video") or "N/A"

    lines = [
        "================================================================================",
        "360° HORIZON STABILIZATION PARAMETER AUDIT LOG",
        "================================================================================",
        f"Test / Target Base   : {out_base or 'default'}",
        f"Timestamp            : {ts_now}",
        f"Video Source         : {video_src}",
        f"Framerate & Frames   : {fps} fps | Total Frames: {total_f}",
        "",
        "STABILIZATION & DETECTION CONFIGURATION:",
        "--------------------------------------------------------------------------------",
        f"Strategy / Mode      : {strategy}",
        f"Detection Source     : {source}",
        f"Keyframe Density     : {density}",
        *( [f"Optical Reference    : {'Ground / Road Contact Line' if params.get('optical_target', 'ground') == 'ground' else 'Skyline / Canopy'}"] if is_vision else [] ),
        f"Pitch Prominence     : {prominence}",
        f"Roll Sway Damping    : {roll_damp}",
        f"Baseline Pitch / Roll: Pitch: {b_pitch}°, Roll: {b_roll}°",
        f"Ignore Yaw           : {ignore_yaw} (Pitch & Roll level only)",
        f"Effective Range      : {range_mode}",
        "",
        f"KEYFRAME SUMMARY ({len(cps)} Checkpoints):",
        "--------------------------------------------------------------------------------",
    ]
    if cps:
        for idx, cp in enumerate(cps):
            f_num = cp.get("frame", 0)
            t_sec = f_num / float(fps) if fps > 0 else 0.0
            p = float(cp.get("pitch", 0.0))
            r = float(cp.get("roll", 0.0))
            y = float(cp.get("yaw", 0.0))
            cp_type = cp.get("type", "")
            type_str = f" [{cp_type}]" if cp_type else ""
            lines.append(f"  [#{idx+1:02d}] Frame {f_num:6d} ({t_sec:6.2f}s) | Pitch: {p:+6.2f}° | Roll: {r:+6.2f}° | Yaw: {y:+6.2f}°{type_str}")
    else:
        lines.append("  (No keyframes recorded)")
    lines.append("================================================================================\n")
    return "\n".join(lines)


def write_horizon_params_log(out_base, data, dest_paths, video_name=""):
    """Writes the formatted horizon parameters audit log to one or more destination file paths.

    Args:
        out_base (str): Test or target run base identifier.
        data (dict): Checkpoint dictionary containing checkpoints and parameters/metadata.
        dest_paths (list[str]): List of filepaths where the log should be saved.
        video_name (str, optional): Target video filename or path.

    Returns:
        list[str]: Paths successfully written.
    """
    text = format_horizon_params_log(out_base, data, video_name=video_name)
    written = []
    for p in dest_paths:
        if not p:
            continue
        p_dir = os.path.dirname(p)
        if p_dir:
            os.makedirs(p_dir, exist_ok=True)
        with open(p, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
        written.append(p)
    return written



def main():
    """CLI entry point for 360° horizon checkpoint stabilization and leveling."""
    parser = argparse.ArgumentParser(description="360° Horizon Checkpoint Stabilizer & Reorienter")
    parser.add_argument('--input', help="Path to input stitched/stabilized 360 MP4")
    parser.add_argument('--checkpoints', help="Path to horizon_checkpoints.json")
    parser.add_argument('--output', help="Path to output stabilized MP4")
    parser.add_argument('--sendcmd_output', default='sendcmd_horizon_stabilize.txt', help="Path to output sendcmd.txt")
    parser.add_argument('--fps', type=float, default=0.0, help="Override video FPS")
    parser.add_argument('--total_frames', type=int, default=0, help="Override total video frames")
    parser.add_argument('--apply_yaw', action='store_true', help="Allow Yaw rotations from checkpoints (default: False / ignored)")
    parser.add_argument('--dry-run', action='store_true', help="Only compute trajectory & sendcmd, do not run FFmpeg")
    
    # Auto-detect flags
    parser.add_argument('--auto-detect', action='store_true', help="Automatically extract inflection checkpoints from telemetry/motion/video")
    parser.add_argument('--telemetry', help="Path to telemetry .txt/.gcsv file for auto-detect")
    parser.add_argument('--motion-file', help="Path to .kopf360motion/.kabsch360motion file for auto-detect")
    parser.add_argument('--video-file', help="Path to video file for computer vision horizon detection")
    parser.add_argument('--neutral-baseline', action='store_true', help="Seed keyframes at 0.0° neutral baseline (for post-stabilized videos)")
    parser.add_argument('--baseline-pitch', type=float, default=0.0, help="Baseline stitch pitch offset (default: 0.0)")
    parser.add_argument('--baseline-roll', type=float, default=0.0, help="Baseline stitch roll offset (default: 0.0)")
    parser.add_argument('--lock-roll', action='store_true', help="Force roll angles to 0.0° (pitch-only detection)")
    parser.add_argument('--optical-target', choices=['ground', 'skyline'], default='ground', help="Optical horizon reference: 'ground' (contact line) or 'skyline' (canopy)")
    parser.add_argument('--epsilon', type=float, default=2.5, help="RDP simplification tolerance in degrees (default: 2.5)")
    parser.add_argument('--output-json', help="Output path to save auto-detected checkpoints JSON")
    args = parser.parse_args()

    # Mode A: Auto-Detect Checkpoints
    if args.auto_detect:
        fps = args.fps if args.fps > 0 else 29.97
        cps = []
        source_type = "none"

        if args.video_file and os.path.exists(args.video_file):
            cps = auto_detect_from_video(args.video_file, fps=fps, epsilon=args.epsilon, lock_roll=args.lock_roll, optical_target=args.optical_target)
            source_type = "vision_pitch_only" if args.lock_roll else "vision"
        elif args.telemetry and os.path.exists(args.telemetry):
            cps = auto_detect_from_telemetry(args.telemetry, fps=fps, baseline_pitch=args.baseline_pitch, baseline_roll=args.baseline_roll, epsilon=args.epsilon, neutral_baseline=args.neutral_baseline)
            source_type = "telemetry_neutral" if args.neutral_baseline else "telemetry"
        elif args.motion_file and os.path.exists(args.motion_file):
            cps = auto_detect_from_motion(args.motion_file, fps=fps, epsilon=args.epsilon, neutral_baseline=args.neutral_baseline)
            source_type = "motion_neutral" if args.neutral_baseline else "motion"
        else:
            print("Error: --auto-detect requires --video-file, --telemetry, or --motion-file with a valid file path.")
            sys.exit(1)

        result_data = {
            "version": "1.0",
            "sourceType": source_type,
            "fps": fps,
            "totalFrames": args.total_frames or (cps[-1]['frame'] + 1 if cps else 0),
            "ignoreYaw": not args.apply_yaw,
            "checkpoints": cps
        }

        if args.output_json:
            os.makedirs(os.path.dirname(os.path.abspath(args.output_json)), exist_ok=True)
            with open(args.output_json, 'w', encoding='utf-8', newline='\n') as out_f:
                json.dump(result_data, out_f, indent=2)
            print(f"[Success] Auto-detected {len(cps)} checkpoints saved to: {args.output_json}")
        else:
            print(json.dumps(result_data, indent=2))
        return

    # Mode B: Standard Horizon Checkpoint Stabilization
    if not args.checkpoints:
        print("Error: --checkpoints is required when not in --auto-detect mode.")
        sys.exit(1)

    if not os.path.exists(args.checkpoints):
        print(f"Error: Checkpoints file '{args.checkpoints}' not found.")
        sys.exit(1)

    checkpoints, ignore_yaw_loaded, json_fps = load_checkpoints(args.checkpoints)
    ignore_yaw = not args.apply_yaw if args.apply_yaw else ignore_yaw_loaded
    if not checkpoints:
        print("Error: No checkpoints found in JSON file.")
        sys.exit(1)

    print(f"[Info] Loaded {len(checkpoints)} horizon checkpoints (Ignore Yaw: {ignore_yaw}).")

    # 2. Determine FPS and Frame Count
    fps = args.fps or json_fps or 29.97
    total_frames = args.total_frames or 0

    if args.input and os.path.exists(args.input):
        detected_frames, detected_fps = get_video_info(args.input)
        if not args.fps:
            fps = detected_fps
        if not args.total_frames:
            total_frames = detected_frames

    if total_frames <= 0:
        max_cp_frame = max(cp['frame'] for cp in checkpoints)
        total_frames = max_cp_frame + 1

    print(f"[Info] Processing trajectory: {total_frames} frames @ {fps:.3f} fps")

    # 3. Generate smooth continuous SLERP trajectory
    trajectory = generate_slerp_trajectory(checkpoints, total_frames, ignore_yaw=ignore_yaw)

    # 4. Write sendcmd file
    sendcmd_path = args.sendcmd_output
    write_sendcmd_file(trajectory, fps, sendcmd_path)

    # 5. Execute FFmpeg if requested
    if args.dry_run or not args.input:
        print("[Info] Dry-run complete. Trajectory ready for rendering.")
        return

    if not os.path.exists(args.input):
        print(f"Error: Input video '{args.input}' not found.")
        sys.exit(1)

    output_file = args.output or os.path.splitext(args.input)[0] + "_horizon_leveled.mp4"
    print(f"[Info] Rendering horizon-stabilized 360° video to: {output_file}")

    # Use FFmpeg with sendcmd and v360 filter
    ffmpeg_cmd = [
        "ffmpeg", "-y",
        "-i", args.input,
        "-vf", f"sendcmd=f='{sendcmd_path}',v360=input=equirect:output=equirect",
        "-c:v", "libx264",
        "-crf", "17",
        "-preset", "fast",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        output_file
    ]

    print(f"[Exec] {' '.join(ffmpeg_cmd)}")
    subprocess.run(ffmpeg_cmd, check=True, creationflags=WIN_NO_WINDOW)
    print(f"[Success] Leveling complete: {output_file}")


if __name__ == '__main__':
    main()
