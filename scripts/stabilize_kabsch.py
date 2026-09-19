#!/usr/bin/env python3
import sys, os
sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
"""
stabilize_kabsch.py -- 360-aware spherical stabilization via Kabsch SVD.

Algorithm (bigsh0t-inspired):
  1. Track Shi-Tomasi features frame-to-frame with Lucas-Kanade.
  2. Lift matched 2D equirect points onto the unit sphere (lon/lat → 3D).
  3. Estimate inter-frame rotation with Kabsch SVD (optimal SO3 fit).
  4. Convert rotation matrix → Euler yaw/pitch/roll.
  5. Accumulate cumulative trajectory, Gaussian-smooth it.
  6. Correction[i] = smooth[i] - raw[i].
  7. Write ABSOLUTE v360 yaw/pitch/roll to FFmpeg sendcmd.

Usage:
    python -B stabilize_kabsch.py --input video.mp4 --output sendcmd.txt
"""

import cv2
import numpy as np
import math
import sys
import os
import struct
import argparse


def pixels_to_spheres(pts, W, H):
    """Lifts 2D equirectangular pixel coordinates to 3D unit-sphere directional vectors.

    Converts image (x, y) coordinates into right-handed Cartesian directional vectors
    where +X is right, +Y is up, and +Z is forward.

    Args:
        pts (np.ndarray): Array of 2D coordinates of shape (N, 2).
        W (int): Frame width in pixels.
        H (int): Frame height in pixels.

    Returns:
        np.ndarray: Array of 3D unit vectors of shape (N, 3).
    """
    px = pts[:, 0].astype(np.float64)
    py = pts[:, 1].astype(np.float64)
    lon = 2.0 * np.pi * (px / W) - np.pi          # [-pi, +pi]
    lat = np.pi / 2.0 - np.pi * (py / H)          # [+pi/2 at top, -pi/2 at bottom]
    cos_lat = np.cos(lat)
    vx = cos_lat * np.sin(lon)
    vy = np.sin(lat)
    vz = cos_lat * np.cos(lon)
    return np.stack([vx, vy, vz], axis=1)


def kabsch(A, B):
    """Calculates the optimal 3x3 rotation matrix aligning two sets of 3D vectors via SVD.

    Finds orthogonal rotation matrix R minimizing Frobenius norm ||A - B @ R.T||_F.

    Args:
        A (np.ndarray): Target 3D vectors of shape (N, 3).
        B (np.ndarray): Source 3D vectors of shape (N, 3).

    Returns:
        np.ndarray: 3x3 SO(3) rotation matrix mapping B onto A.
    """
    H = B.T @ A  # 3 x 3
    U, _, Vt = np.linalg.svd(H)
    d = np.linalg.det(Vt.T @ U.T)
    D = np.diag([1.0, 1.0, d])
    return Vt.T @ D @ U.T


def kabsch_ransac(A, B, max_iters=100, threshold_deg=1.5):
    """Estimates optimal inter-frame rotation using RANSAC Kabsch SVD.

    Rejects parallax and non-rigid rolling-shutter feature outliers.

    Args:
        A (np.ndarray): Target vectors of shape (N, 3).
        B (np.ndarray): Source vectors of shape (N, 3).
        max_iters (int, optional): Number of RANSAC iterations. Defaults to 100.
        threshold_deg (float, optional): Angular inlier threshold in degrees. Defaults to 1.5.

    Returns:
        np.ndarray: Robust 3x3 rotation matrix.
    """
    N = len(A)
    if N < 8:
        return kabsch(A, B)

    threshold_rad = math.radians(threshold_deg)
    cos_threshold = math.cos(threshold_rad)
    rng = np.random.RandomState(42)

    best_count = -1
    best_inliers = None

    for _ in range(max_iters):
        idx = rng.choice(N, size=4, replace=False)
        try:
            R_cand = kabsch(A[idx], B[idx])
        except Exception:
            continue
        A_pred = B @ R_cand.T
        dots = np.sum(A * A_pred, axis=1)
        inliers = dots >= cos_threshold
        count = np.sum(inliers)
        if count > best_count:
            best_count = count
            best_inliers = inliers

    if best_inliers is not None and best_count >= 8:
        return kabsch(A[best_inliers], B[best_inliers])
    else:
        return kabsch(A, B)


def rot_to_euler(R):
    """Converts a 3x3 rotation matrix into Euler yaw, pitch, and roll angles in degrees.

    Maps curr_frame -> prev_frame using convention R = R_y(yaw) @ R_x(pitch) @ R_z(roll):
      - Yaw   (rot around Y=Up)      : +Yaw turns camera right
      - Pitch (rot around X=Right)   : +Pitch tilts camera up
      - Roll  (rot around Z=Forward) : +Roll rotates camera clockwise

    Args:
        R (np.ndarray): 3x3 rotation matrix.

    Returns:
        tuple[float, float, float]: Euler angles in degrees as (yaw, pitch, roll).
    """
    sy = math.sqrt(R[0, 2] ** 2 + R[2, 2] ** 2)
    if sy > 1e-6:
        yaw   = math.degrees(math.atan2(R[0, 2], R[2, 2]))
        pitch = math.degrees(math.atan2(R[1, 2], sy))
        roll  = -math.degrees(math.atan2(R[1, 0], R[1, 1]))  # Negated to match frei0r bigsh0t CW convention
    else:
        yaw   = math.degrees(math.atan2(-R[2, 0], R[0, 0]))
        pitch = math.degrees(math.atan2(R[1, 2], sy))
        roll  = 0.0
    return yaw, pitch, roll


def gaussian_smooth(signal, sigma):
    """Applies 1D Gaussian kernel smoothing with edge padding.

    Pads boundaries to prevent edge-attenuation artifacts near start and end of trajectory.

    Args:
        signal (np.ndarray): 1D array of trajectory signal values.
        sigma (float): Gaussian smoothing kernel standard deviation in frames.

    Returns:
        np.ndarray: Convolved smoothed 1D trajectory array.
    """
    N = len(signal)
    if N == 0:
        return signal
    effective_sigma = max(1.0, float(sigma))
    radius = int(math.ceil(3.0 * effective_sigma))
    x = np.arange(-radius, radius + 1, dtype=np.float64)
    kernel = np.exp(-0.5 * (x / effective_sigma) ** 2)
    kernel /= kernel.sum()
    padded = np.pad(signal, radius, mode='edge')
    return np.convolve(padded, kernel, mode='valid')


def read_bigsh0t360motion(file_path):
    """Parses binary .bigsh0t360motion tracking files from Kdenlive frei0r plugins.

    Args:
        file_path (str): Filepath to the binary motion file.

    Returns:
        list[tuple[float, float, float, float, float]] | None: List of
            (t_start, t_end, yaw, pitch, roll) records, or None on error.
    """
    if not os.path.exists(file_path):
        return None
    try:
        data = open(file_path, 'rb').read()
        if len(data) < 8:
            return None
        n_records = struct.unpack('<q', data[:8])[0]
        rem = data[8:]
        rec_len = 40
        if len(rem) < n_records * rec_len:
            return None
        records = []
        fmt = '<ddddd'
        for i in range(n_records):
            t_start, t_end, y, p, r = struct.unpack(fmt, rem[i * rec_len:(i + 1) * rec_len])
            records.append((t_start, t_end, y, p, r))
        return records
    except Exception:
        return None


def kdenlive_horizon_features_to_track(gray, mask=None, grid_step=64):
    """Samples a regular grid of tracking points across the equatorial horizon band.

    Excludes nadir and zenith polar areas to reduce optical distortion errors.

    Args:
        gray (np.ndarray): Grayscale image frame.
        mask (np.ndarray, optional): Binary mask. Defaults to None.
        grid_step (int, optional): Grid step spacing in pixels. Defaults to 64.

    Returns:
        np.ndarray: Array of feature points of shape (N, 1, 2) in float32.
    """
    H, W = gray.shape
    # Nadir/Operator Masking: Exclude top sky (25%) and bottom nadir operator body (30%)
    y_coords = np.arange(int(H * 0.25), int(H * 0.70), grid_step)
    x_coords = np.arange(int(W * 0.05), int(W * 0.95), grid_step)
    xx, yy = np.meshgrid(x_coords, y_coords)
    pts = np.column_stack([xx.ravel(), yy.ravel()]).astype(np.float32)
    return pts.reshape(-1, 1, 2)


def grid_good_features_to_track(gray, mask, num_grid_x=12, num_grid_y=4, points_per_cell=20):
    """Detects Shi-Tomasi corners uniformly distributed across a spherical grid.

    Divides the equirectangular projection into grid cells to guarantee
    omnidirectional feature distribution.

    Args:
        gray (np.ndarray): Grayscale image frame.
        mask (np.ndarray): Detection validity mask.
        num_grid_x (int, optional): Number of horizontal columns. Defaults to 12.
        num_grid_y (int, optional): Number of vertical rows. Defaults to 4.
        points_per_cell (int, optional): Max points per cell. Defaults to 20.

    Returns:
        np.ndarray: Array of detected feature points of shape (N, 1, 2).
    """
    H, W = gray.shape
    all_pts = []
    cell_w = W // num_grid_x
    cell_h = H // num_grid_y

    for gy in range(num_grid_y):
        y0 = gy * cell_h
        y1 = (gy + 1) * cell_h if gy < num_grid_y - 1 else H
        for gx in range(num_grid_x):
            x0 = gx * cell_w
            x1 = (gx + 1) * cell_w if gx < num_grid_x - 1 else W

            cell_mask = mask[y0:y1, x0:x1]
            if np.count_nonzero(cell_mask) < 20:
                continue

            cell_gray = gray[y0:y1, x0:x1]
            pts = cv2.goodFeaturesToTrack(
                cell_gray,
                maxCorners=points_per_cell,
                qualityLevel=0.005,
                minDistance=5,
                mask=cell_mask,
                blockSize=5
            )
            if pts is not None:
                pts[:, 0, 0] += x0
                pts[:, 0, 1] += y0
                all_pts.append(pts)

    if len(all_pts) > 0:
        return np.vstack(all_pts)
    else:
        return kdenlive_horizon_features_to_track(gray, mask)


def main():
    """CLI entry point for 360-aware spherical stabilization via Kabsch SVD."""
    parser = argparse.ArgumentParser(
        description='360-aware spherical stabilization (Kabsch SVD / bigsh0t-inspired)')
    parser.add_argument('--input',            required=True)
    parser.add_argument('--output',           required=True)
    parser.add_argument('--motion_file',      default='')
    parser.add_argument('--analyze_file',     default='',
                        help='Path to existing Kdenlive .bigsh0t360motion analysis file')
    parser.add_argument('--export_bigsh0t',   action='store_true',
                        help='Save binary Kdenlive .bigsh0t360motion file')
    parser.add_argument('--smoothing',        type=int,   default=5,
                        help='Gaussian sigma = smoothing/3 frames (default 5 for fast tremor rejection)')
    parser.add_argument('--shakiness',        type=int,   default=2,
                        help='Shakiness (1-10): controls pixel clamp for parallax rejection')
    parser.add_argument('--fps',              type=float, default=0.0)
    parser.add_argument('--max_features',     type=int,   default=500)
    parser.add_argument('--refresh_interval', type=int,   default=1)
    args = parser.parse_args()

    try:
        cv2.setNumThreads(os.cpu_count() or 0)
    except Exception:
        pass

    cap = cv2.VideoCapture(args.input)
    if not cap.isOpened():
        print(f'[Kabsch360] ERROR: cannot open {args.input}', file=sys.stderr, flush=True)
        sys.exit(1)

    W            = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H            = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps          = cap.get(cv2.CAP_PROP_FPS) if args.fps <= 0 else args.fps
    if fps <= 0:
        fps = 29.97
    time_step = 1.0 / fps

    print(f'[Kabsch360] Input: {W}x{H}, ~{total_frames} frames @ {fps:.3f} fps (smooth={args.smoothing})', flush=True)

    # Load optional Kdenlive .bigsh0t360motion file ONLY if explicitly provided via --analyze_file
    bigsh0t_file = args.analyze_file
    kdenlive_recs = read_bigsh0t360motion(bigsh0t_file) if bigsh0t_file else None

    if kdenlive_recs:
        print(f'[Kabsch360] Loaded {len(kdenlive_recs)} motion records from Kdenlive binary file: {bigsh0t_file}', flush=True)
        # Unpack Kdenlive keyframe interval motion records into per-frame angular velocities
        inter_yaws_a   = np.zeros(total_frames, dtype=np.float64)
        inter_pitches_a = np.zeros(total_frames, dtype=np.float64)
        inter_rolls_a   = np.zeros(total_frames, dtype=np.float64)

        for r in kdenlive_recs:
            t_start, t_end, y, p, r_val = r
            f_start = max(0, min(total_frames - 1, int(round(t_start / time_step))))
            f_end   = max(f_start + 1, min(total_frames, int(round(t_end / time_step))))
            n_f = max(1, f_end - f_start)
            inter_yaws_a[f_start:f_end]   = y / n_f
            inter_pitches_a[f_start:f_end] = p / n_f
            inter_rolls_a[f_start:f_end]   = r_val / n_f

        inter_yaws   = list(inter_yaws_a)
        inter_pitches = list(inter_pitches_a)
        inter_rolls   = list(inter_rolls_a)
    else:
        lk_params = dict(winSize=(21, 21), maxLevel=3,
                         criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01))

        # Same mask as optical_360_stabilize.py: skip top/bottom 30% (poles) and 5% seam
        mask = np.zeros((H, W), dtype=np.uint8)
        mask[int(H * 0.30):int(H * 0.70), int(W * 0.05):int(W * 0.95)] = 255

        feature_params = dict(maxCorners=args.max_features, qualityLevel=0.01,
                              minDistance=max(8, W // 80), blockSize=5)

        ret, prev_frame = cap.read()
        if not ret:
            print('[Kabsch360] ERROR: cannot read first frame', file=sys.stderr, flush=True)
            sys.exit(1)
        prev_gray = cv2.cvtColor(prev_frame, cv2.COLOR_BGR2GRAY)
        prev_pts  = kdenlive_horizon_features_to_track(prev_gray, mask=mask)

    if not kdenlive_recs:
        inter_yaws      = [0.0]
        inter_pitches   = [0.0]
        inter_rolls     = [0.0]
        good_curr = np.zeros((0, 2), dtype=np.float32)

        frame_idx = 1
        while True:
            ret, curr_frame = cap.read()
            if not ret:
                break
            curr_gray = cv2.cvtColor(curr_frame, cv2.COLOR_BGR2GRAY)

            yaw, pitch, roll = 0.0, 0.0, 0.0
            good_curr = np.zeros((0, 2), dtype=np.float32)

            if prev_pts is not None and len(prev_pts) >= 8:
                curr_pts, status, _ = cv2.calcOpticalFlowPyrLK(
                    prev_gray, curr_gray, prev_pts, None, **lk_params)
                ok = status.ravel() == 1
                gp = prev_pts[ok].reshape(-1, 2)
                gc = curr_pts[ok].reshape(-1, 2)

                if len(gp) >= 8:
                    A = pixels_to_spheres(gp, W, H)
                    B = pixels_to_spheres(gc, W, H)
                    try:
                        R = kabsch_ransac(A, B, max_iters=100, threshold_deg=1.5)
                        yaw, pitch, roll = rot_to_euler(R)
                    except Exception:
                        yaw, pitch, roll = 0.0, 0.0, 0.0
                    good_curr = gc

            inter_yaws.append(yaw)
            inter_pitches.append(pitch)
            inter_rolls.append(roll)

            if frame_idx % max(1, min(10, total_frames // 20)) == 0 or frame_idx == total_frames:
                print(f'[Kabsch360] Analyzed {frame_idx}/{total_frames}', flush=True)

            prev_gray = curr_gray
            if len(good_curr) >= 20 and (frame_idx % args.refresh_interval != 0):
                prev_pts = good_curr.reshape(-1, 1, 2)
            else:
                prev_pts = kdenlive_horizon_features_to_track(prev_gray, mask=mask)
            frame_idx += 1


    cap.release()
    N = len(inter_yaws)
    print(f'[Kabsch360] Pass 1 complete: {N} frames analyzed', flush=True)

    yaws_a    = np.array(inter_yaws,    dtype=np.float64)
    pitches_a = np.array(inter_pitches, dtype=np.float64)
    rolls_a   = np.array(inter_rolls,   dtype=np.float64)

    # --- Pure Kdenlive bigsh0t Trajectory Stabilization (smooth_pos - pos) ---
    # Construct raw cumulative orientation trajectory
    pos_yaw   = np.cumsum(yaws_a)
    pos_pitch = np.cumsum(pitches_a)
    pos_roll  = np.cumsum(rolls_a)

    # Gaussian smooth the cumulative trajectory (smooth low-pass camera path)
    sigma = max(1.0, args.smoothing / 3.0)
    smooth_pos_yaw   = gaussian_smooth(pos_yaw,   sigma)
    smooth_pos_pitch = gaussian_smooth(pos_pitch, sigma)
    smooth_pos_roll  = gaussian_smooth(pos_roll,  sigma)

    # Pure Kdenlive bigsh0t correction formula: smooth trajectory - raw trajectory
    corr_yaws   = smooth_pos_yaw   - pos_yaw
    corr_pitches = smooth_pos_pitch - pos_pitch
    corr_rolls   = smooth_pos_roll  - pos_roll

    # Safety clamp on final correction
    CORR_CAP = 5.0
    corr_yaws    = np.clip(corr_yaws,    -CORR_CAP, CORR_CAP)
    corr_pitches = np.clip(corr_pitches, -CORR_CAP, CORR_CAP)
    corr_rolls   = np.clip(corr_rolls,   -CORR_CAP, CORR_CAP)

    max_yaw   = float(np.max(np.abs(corr_yaws)))
    max_pitch = float(np.max(np.abs(corr_pitches)))
    max_roll  = float(np.max(np.abs(corr_rolls)))
    print(f'[Kabsch360] Correction stats  yaw: max={max_yaw:.3f}  pitch: max={max_pitch:.3f}  roll: max={max_roll:.3f}  deg', flush=True)

    # --- Save Motion File (.bigsh0t360motion binary or .kabsch360motion text) ---
    if args.motion_file:
        mdir = os.path.dirname(os.path.abspath(args.motion_file))
        if mdir:
            os.makedirs(mdir, exist_ok=True)
        # 1. Save text motion file (.kabsch360motion)
        text_motion_file = args.motion_file if args.motion_file.endswith('.kabsch360motion') else args.motion_file.replace('.bigsh0t360motion', '.kabsch360motion')
        with open(text_motion_file, 'w', newline='\n') as mf:
            mf.write('# kabsch360motion -- 360 spherical inter-frame rotation (Kabsch SVD SO3)\n')
            mf.write('# Format: frame yaw_deg pitch_deg roll_deg  corr_yaw corr_pitch corr_roll\n')
            mf.write(f'# VideoFPS: {fps:.6f}\n')
            mf.write(f'# VideoSize: {W}x{H}\n')
            mf.write(f'# TotalFrames: {N}\n')
            mf.write(f'# Sigma: {sigma:.2f}\n')
            mf.write(f'# MaxCorrYaw: {max_yaw:.4f}  MaxCorrPitch: {max_pitch:.4f}  MaxCorrRoll: {max_roll:.4f}\n')
            for i in range(N):
                mf.write(f'{i} {yaws_a[i]:.6f} {pitches_a[i]:.6f} {rolls_a[i]:.6f} '
                         f'{corr_yaws[i]:.6f} {corr_pitches[i]:.6f} {corr_rolls[i]:.6f}\n')
        print(f'[Kabsch360] Text motion file saved: {text_motion_file}', flush=True)

        # 2. Save binary Kdenlive bigsh0t motion file (.bigsh0t360motion) only if requested
        if getattr(args, 'export_bigsh0t', False):
            bin_motion_file = args.motion_file if args.motion_file.endswith('.bigsh0t360motion') else args.motion_file.replace('.kabsch360motion', '.bigsh0t360motion')
            with open(bin_motion_file, 'wb') as bmf:
                bmf.write(struct.pack('<q', N))
                for i in range(N):
                    st = i * time_step
                    et = (i + 1) * time_step
                    bmf.write(struct.pack('<ddddd', st, et, float(yaws_a[i]), float(pitches_a[i]), float(rolls_a[i])))
            print(f'[Kabsch360] Binary Kdenlive motion file saved: {bin_motion_file}', flush=True)

    # --- Write FFmpeg sendcmd (ABSOLUTE v360 yaw/pitch/roll per frame) ---
    outdir = os.path.dirname(os.path.abspath(args.output))
    if outdir:
        os.makedirs(outdir, exist_ok=True)
    with open(args.output, 'w', newline='\n') as f:
        for i in range(N):
            st = i * time_step
            et = (i + 1) * time_step
            f.write(f'{st:.6f}-{et:.6f} [enter] v360 yaw {corr_yaws[i]:.6f};\n')
            f.write(f'{st:.6f}-{et:.6f} [enter] v360 pitch {corr_pitches[i]:.6f};\n')
            f.write(f'{st:.6f}-{et:.6f} [enter] v360 roll {corr_rolls[i]:.6f};\n')

    print(f'[Kabsch360] Sendcmd written: {args.output} ({N} frames)', flush=True)


if __name__ == '__main__':
    main()
