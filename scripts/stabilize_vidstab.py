#!/usr/bin/env python3
import sys, os
sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
"""360-aware 2D optical flow stabilization and trajectory smoothing.

Algorithm:
  1. Track Shi-Tomasi features frame-to-frame with Lucas-Kanade.
  2. Estimate inter-frame 2D similarity transform (rotation + translation)
     using cv2.estimateAffinePartial2D with RANSAC -- RANSAC rejects parallax
     outliers from forward camera motion without manual thresholds.
  3. Extract pixel shifts (tx, ty) and convert to angular corrections:
       yaw_component  = -tx * 360 / W   (tx<0 = features moved left = camera yawed right)
       pitch_component = ty * 180 / H   (ty>0 = features moved down = camera tilted up)
       roll disabled (unreliable from equirect flow)
  4. Accumulate cumulative trajectory, Gaussian-smooth it.
  5. Correction[i] = smooth[i] - raw[i]  -- absolute per-frame correction.
  6. Write ABSOLUTE v360 yaw/pitch to FFmpeg sendcmd.
  7. Save .optical360motion for project archive.

Usage:
    python -B stabilize_vidstab.py --input video.mp4 --output sendcmd.txt
"""

import cv2
import numpy as np
import math
import sys
import os
import argparse
import json


def gaussian_smooth(signal, sigma):
    """Apply 1D Gaussian smoothing convolution with reflect padding at boundaries.

    Args:
        signal: 1D numpy array of trajectory or motion values.
        sigma: Standard deviation of Gaussian kernel in frames.

    Returns:
        np.ndarray: Smoothed 1D signal of the same length as input.
    """
    radius = int(math.ceil(3 * sigma))
    x = np.arange(-radius, radius + 1, dtype=np.float64)
    kernel = np.exp(-0.5 * (x / sigma) ** 2)
    kernel /= kernel.sum()
    padded = np.pad(signal, radius, mode='reflect')
    return np.convolve(padded, kernel, mode='valid')


def boxcar_smooth(signal, window_size):
    """Apply 1D simple moving average (boxcar) filter with reflect padding.

    Args:
        signal: 1D numpy array of trajectory values.
        window_size: Moving average window length in frames.

    Returns:
        np.ndarray: Smoothed 1D signal of the same length as input.
    """
    w = max(1, int(round(window_size)))
    if w <= 1 or len(signal) <= 1:
        return signal.copy()
    kernel = np.ones(w, dtype=np.float64) / float(w)
    pad_left = w // 2
    pad_right = w - 1 - pad_left
    padded = np.pad(signal, (pad_left, pad_right), mode='reflect')
    return np.convolve(padded, kernel, mode='valid')


def l1_l2_opt_smooth(signal, smoothing_val, max_iters=5, lambda_scale=10.0):
    """Optimize trajectory via iteratively reweighted L1/L2 trend filtering.

    Penalizes both 1st difference (velocity) and 2nd difference (acceleration)
    using sparse matrix optimization, falling back to Gaussian smoothing if sparse
    solver fails.

    Args:
        signal: 1D numpy array of rotation angles.
        smoothing_val: Smoothing strength parameter.
        max_iters: Number of iteratively reweighted optimization iterations.
        lambda_scale: Scaling factor for acceleration penalty.

    Returns:
        np.ndarray: Regularized and smoothed 1D trajectory array.
    """
    N = len(signal)
    if N < 4 or smoothing_val <= 0:
        return signal.copy()
    try:
        import scipy.sparse as sp
        import scipy.sparse.linalg as spla

        lam2 = float(smoothing_val) ** 2 * lambda_scale
        lam1 = float(smoothing_val) * (lambda_scale * 0.2)

        d1_data = np.ones(N - 1)
        D1 = sp.diags([-d1_data, d1_data], [0, 1], shape=(N - 1, N), format='csr')

        d2_main = -2.0 * np.ones(N - 2)
        d2_side = np.ones(N - 2)
        D2 = sp.diags([d2_side, d2_main, d2_side], [0, 1, 2], shape=(N - 2, N), format='csr')

        I = sp.eye(N, format='csr')
        A_base = I + lam1 * (D1.T @ D1)

        A = A_base + lam2 * (D2.T @ D2)
        P = spla.spsolve(A.tocsc(), signal)

        eps = 1e-4
        for _ in range(max_iters):
            d2_p = D2 @ P
            w = 1.0 / np.maximum(np.abs(d2_p), eps)
            w = w / np.mean(w)
            W = sp.diags(w, 0, shape=(N - 2, N - 2), format='csr')

            A_iter = A_base + lam2 * (D2.T @ W @ D2)
            P = spla.spsolve(A_iter.tocsc(), signal)

        return P
    except Exception as e:
        print(f"[360-Stab] Notice: l1_l2_opt_smooth fallback to Gaussian ({e})", flush=True)
        sigma = max(1.0, float(smoothing_val) / 3.0)
        return gaussian_smooth(signal, sigma)


def main():
    """Execute command-line interface for 360-aware optical feature stabilization."""
    parser = argparse.ArgumentParser(description='360-aware optical stabilization')
    parser.add_argument('--input',            required=True)
    parser.add_argument('--output',           required=True)
    parser.add_argument('--motion_file',      default='')
    parser.add_argument('--smoothing',        type=int,   default=2,   help='Gaussian sigma = smoothing/3 frames')
    parser.add_argument('--shakiness',        type=int,   default=1,   help='Shakiness parameter (1-10)')
    parser.add_argument('--optalgo',          choices=['gauss', 'opt', 'avg'], default='gauss', help='Smoothing algorithm: gauss (Gaussian), opt (Global L1/L2 optimization), avg (Boxcar moving average)')
    parser.add_argument('--fps',              type=float, default=0.0)
    parser.add_argument('--max_features',     type=int,   default=500)
    parser.add_argument('--refresh_interval', type=int,   default=1)
    parser.add_argument('--checkpoints',      default='', help='Path to horizon_checkpoints.json for hybrid anchor leveling')
    parser.add_argument('--tripod',           action='store_true', help='Virtual Tripod mode: lock camera position completely relative to frame 0')
    parser.add_argument('--visual_video',     default='', help='Path to output visual debug video with overlay feature points')
    args = parser.parse_args()

    try:
        cv2.setNumThreads(os.cpu_count() or 0)
    except Exception:
        pass

    cap = cv2.VideoCapture(args.input)
    if not cap.isOpened():
        print(f'[360-Stab] ERROR: cannot open {args.input}', file=sys.stderr, flush=True)
        sys.exit(1)

    W            = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H            = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps          = cap.get(cv2.CAP_PROP_FPS) if args.fps <= 0 else args.fps
    if fps <= 0:
        fps = 29.97
    time_step = 1.0 / fps

    print(f'[360-Stab] Input: {W}x{H}, ~{total_frames} frames @ {fps:.3f} fps (smooth={args.smoothing}, shakiness={args.shakiness})', flush=True)

    writer = None
    if args.visual_video:
        vdir = os.path.dirname(os.path.abspath(args.visual_video))
        if vdir: os.makedirs(vdir, exist_ok=True)
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        writer = cv2.VideoWriter(args.visual_video, fourcc, fps, (W, H))
        if not writer.isOpened():
            print(f'[360-Stab] Warning: cannot open visual video writer for {args.visual_video}', flush=True)
            writer = None

    feature_params = dict(maxCorners=args.max_features, qualityLevel=0.01,
                          minDistance=max(8, W // 80), blockSize=5)
    lk_params = dict(winSize=(21, 21), maxLevel=3,
                     criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01))

    # Exclusion mask: skip poles (top 25% sky, bottom 30% nadir operator body) and seam edges (5% each side).
    # Use the equatorial band where equirect geometry is most linear and operator-free.
    mask = np.zeros((H, W), dtype=np.uint8)
    mask[int(H * 0.25):int(H * 0.70), int(W * 0.05):int(W * 0.95)] = 255

    ret, prev_frame = cap.read()
    if not ret:
        print('[360-Stab] ERROR: cannot read first frame', file=sys.stderr, flush=True)
        sys.exit(1)
    prev_gray = cv2.cvtColor(prev_frame, cv2.COLOR_BGR2GRAY)
    prev_pts  = cv2.goodFeaturesToTrack(prev_gray, mask=mask, **feature_params)

    if writer is not None:
        f0_vis = prev_frame.copy()
        if prev_pts is not None:
            for p in prev_pts:
                x, y = p.ravel()
                cv2.circle(f0_vis, (int(x), int(y)), 4, (0, 255, 0), -1, cv2.LINE_AA)
        cv2.putText(f0_vis, f"[360 Flow] Frame 0/{total_frames} | Features: {len(prev_pts) if prev_pts is not None else 0}", (30, 45), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2, cv2.LINE_AA)
        writer.write(f0_vis)

    # Frame 0: no predecessor -> zero inter-frame shift
    inter_yaw_px   = [0.0]   # horizontal pixel shift (tx from affine)
    inter_pitch_px = [0.0]   # vertical pixel shift   (ty from affine)
    good_curr = np.zeros((0, 2), dtype=np.float32)

    frame_idx = 1
    while True:
        ret, curr_frame = cap.read()
        if not ret:
            break
        curr_gray = cv2.cvtColor(curr_frame, cv2.COLOR_BGR2GRAY)

        tx, ty = 0.0, 0.0
        good_curr = np.zeros((0, 2), dtype=np.float32)

        if prev_pts is not None and len(prev_pts) >= 8:
            curr_pts, status, _ = cv2.calcOpticalFlowPyrLK(
                prev_gray, curr_gray, prev_pts, None, **lk_params)
            ok = status.ravel() == 1
            gp = prev_pts[ok].reshape(-1, 2)
            gc = curr_pts[ok].reshape(-1, 2)

            if len(gp) >= 8:
                # estimateAffinePartial2D uses RANSAC internally.
                # RANSAC naturally rejects parallax outliers (far vs near objects
                # have inconsistent apparent motion for a translating camera).
                M, inlier_mask = cv2.estimateAffinePartial2D(
                    gp, gc,
                    method=cv2.RANSAC,
                    ransacReprojThreshold=2.5,
                    maxIters=500,
                    confidence=0.99
                )
                if M is not None:
                    tx = float(M[0, 2])   # horizontal pixel shift of features
                    ty = float(M[1, 2])   # vertical pixel shift of features
                    # Soft-clamp extreme values based on shakiness parameter
                    MAX_PX = max(1.0, float(args.shakiness) * 1.5)
                    tx = max(-MAX_PX, min(MAX_PX, tx))
                    ty = max(-MAX_PX, min(MAX_PX, ty))
                    if inlier_mask is not None:
                        good_curr = gc[inlier_mask.ravel() == 1]

        if writer is not None:
            curr_vis = curr_frame.copy()
            if prev_pts is not None and len(gp) > 0:
                inlier_tuples = set(map(tuple, good_curr.reshape(-1, 2))) if len(good_curr) > 0 else set()
                for p0, p1 in zip(gp, gc):
                    x0, y0 = int(p0[0]), int(p0[1])
                    x1, y1 = int(p1[0]), int(p1[1])
                    if tuple(p1) in inlier_tuples:
                        cv2.line(curr_vis, (x0, y0), (x1, y1), (255, 255, 0), 2, cv2.LINE_AA)
                        cv2.circle(curr_vis, (x1, y1), 4, (0, 255, 0), -1, cv2.LINE_AA)
                    else:
                        cv2.circle(curr_vis, (x1, y1), 2, (0, 0, 255), -1, cv2.LINE_AA)
            cv2.putText(curr_vis, f"[360 Flow] Frame {frame_idx}/{total_frames} | Inliers: {len(good_curr)} | Shift: tx={tx:+.1f}px ty={ty:+.1f}px", (30, 45), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2, cv2.LINE_AA)
            writer.write(curr_vis)

        inter_yaw_px.append(tx)
        inter_pitch_px.append(ty)

        if frame_idx % max(1, min(10, total_frames // 20)) == 0 or frame_idx == total_frames:
            print(f'[360-Stab] Analyzed {frame_idx}/{total_frames}', flush=True)

        prev_gray = curr_gray
        if frame_idx % args.refresh_interval == 0 or prev_pts is None or len(prev_pts) < 20:
            prev_pts = cv2.goodFeaturesToTrack(prev_gray, mask=mask, **feature_params)
        elif len(good_curr) >= 20:
            prev_pts = good_curr.reshape(-1, 1, 2)
        else:
            prev_pts = cv2.goodFeaturesToTrack(prev_gray, mask=mask, **feature_params)

        frame_idx += 1

    cap.release()
    if writer is not None:
        writer.release()
        print(f'[360-Stab] Visual debug video saved: {args.visual_video}', flush=True)
    N = len(inter_yaw_px)
    print(f'[360-Stab] Pass 1 complete: {N} frames analysed', flush=True)

    # --- Convert pixel shifts to angular corrections ---
    # tx < 0: features moved left -> camera yawed RIGHT   -> inter_yaw_deg positive
    # tx > 0: features moved right-> camera yawed LEFT    -> inter_yaw_deg negative
    # ty > 0: features moved down  -> camera pitched UP   -> inter_pitch_deg positive
    # ty < 0: features moved up    -> camera pitched DOWN -> inter_pitch_deg negative
    yaw_arr   = np.array([-x * 360.0 / W for x in inter_yaw_px],   dtype=np.float64)
    pitch_arr = np.array([ y * 180.0 / H for y in inter_pitch_px],  dtype=np.float64)

    sigma = max(1.0, args.smoothing / 3.0)

    # --- Trajectory-level Gaussian Detrending (Zero Frame-0 Offset & No Integration Drift) ---
    raw_pos_yaw   = np.cumsum(yaw_arr)
    raw_pos_pitch = np.cumsum(pitch_arr)

    if args.tripod:
        corr_yaw   = -raw_pos_yaw
        corr_pitch = -raw_pos_pitch
    else:
        if args.optalgo == 'avg':
            smooth_pos_yaw   = boxcar_smooth(raw_pos_yaw,   max(1, args.smoothing))
            smooth_pos_pitch = boxcar_smooth(raw_pos_pitch, max(1, args.smoothing))
        elif args.optalgo == 'opt':
            smooth_pos_yaw   = l1_l2_opt_smooth(raw_pos_yaw,   args.smoothing)
            smooth_pos_pitch = l1_l2_opt_smooth(raw_pos_pitch, args.smoothing)
        else:
            smooth_pos_yaw   = gaussian_smooth(raw_pos_yaw,   sigma)
            smooth_pos_pitch = gaussian_smooth(raw_pos_pitch, sigma)

        # Pure trajectory stabilization: smooth - raw
        corr_yaw   = smooth_pos_yaw   - raw_pos_yaw
        corr_pitch = smooth_pos_pitch - raw_pos_pitch

        # Safety cap: shakiness-dependent max correction cap
        CORR_CAP = min(5.0, max(1.0, float(args.shakiness) * 1.5))
        corr_yaw   = np.clip(corr_yaw,   -CORR_CAP, CORR_CAP)
        corr_pitch = np.clip(corr_pitch, -CORR_CAP, CORR_CAP)

    max_yaw   = float(np.max(np.abs(corr_yaw)))
    max_pitch = float(np.max(np.abs(corr_pitch)))
    mean_yaw  = float(np.mean(np.abs(corr_yaw)))
    mean_pitch= float(np.mean(np.abs(corr_pitch)))
    print(f'[360-Stab] Correction stats  yaw: max={max_yaw:.3f} mean={mean_yaw:.3f} deg', flush=True)
    print(f'[360-Stab] Correction stats  pitch: max={max_pitch:.3f} mean={mean_pitch:.3f} deg', flush=True)

    # --- Save .optical360motion file ---
    if args.motion_file:
        mdir = os.path.dirname(os.path.abspath(args.motion_file))
        if mdir:
            os.makedirs(mdir, exist_ok=True)
        with open(args.motion_file, 'w', newline='\n') as mf:
            mf.write('# optical360motion -- 360 spherical inter-frame shift data (RANSAC affine)\n')
            mf.write('# Format: frame tx_px ty_px yaw_deg pitch_deg  corr_yaw corr_pitch\n')
            mf.write(f'# VideoFPS: {fps:.6f}\n')
            mf.write(f'# VideoSize: {W}x{H}\n')
            mf.write(f'# TotalFrames: {N}\n')
            mf.write(f'# Sigma: {sigma:.2f}\n')
            mf.write(f'# MaxCorrYaw: {max_yaw:.4f}  MaxCorrPitch: {max_pitch:.4f}\n')
            for i in range(N):
                mf.write(f'{i} {inter_yaw_px[i]:.4f} {inter_pitch_px[i]:.4f} '
                         f'{yaw_arr[i]:.6f} {pitch_arr[i]:.6f} '
                         f'{corr_yaw[i]:.6f} {corr_pitch[i]:.6f}\n')
        print(f'[360-Stab] Motion file saved: {args.motion_file}', flush=True)

    # --- Checkpoint Fusion (if provided) ---
    cp_yaw = np.zeros(N, dtype=np.float64)
    cp_pitch = np.zeros(N, dtype=np.float64)
    cp_roll = np.zeros(N, dtype=np.float64)
    if args.checkpoints and os.path.exists(args.checkpoints):
        try:
            with open(args.checkpoints, 'r', encoding='utf-8') as cpf:
                cp_data = json.load(cpf)
            cps = cp_data.get('checkpoints', []) if isinstance(cp_data, dict) else cp_data
            if cps:
                from stabilize_horizon import generate_slerp_trajectory
                cp_traj = generate_slerp_trajectory(cps, N)
                cp_yaw = cp_traj[:, 0]
                cp_pitch = cp_traj[:, 1]
                cp_roll = cp_traj[:, 2]
                print(f'[360-Stab] Fused {len(cps)} horizon checkpoints with optical flow.', flush=True)
        except Exception as e:
            print(f'[360-Stab] Warning: Checkpoint fusion failed: {e}', flush=True)

    # --- Write FFmpeg sendcmd (ABSOLUTE v360 yaw/pitch per frame) ---
    # Sign rationale (derived from pixel convention above):
    #   corr_yaw > 0  -> camera deviated LEFT  -> v360 yaw positive = look right  = correct
    #   corr_pitch > 0 -> camera deviated DOWN -> v360 pitch positive = look up    = correct
    outdir = os.path.dirname(os.path.abspath(args.output))
    if outdir:
        os.makedirs(outdir, exist_ok=True)
    with open(args.output, 'w', newline='\n') as f:
        for i in range(N):
            st = i * time_step
            et = (i + 1) * time_step
            tot_yaw = corr_yaw[i] + cp_yaw[i]
            tot_pitch = corr_pitch[i] + cp_pitch[i]
            tot_roll = cp_roll[i]
            f.write(f'{st:.6f}-{et:.6f} [enter] v360 yaw {tot_yaw:.6f};\n')
            f.write(f'{st:.6f}-{et:.6f} [enter] v360 pitch {tot_pitch:.6f};\n')
            f.write(f'{st:.6f}-{et:.6f} [enter] v360 roll {tot_roll:.6f};\n')

    print(f'[360-Stab] Sendcmd written: {args.output} ({N} frames)', flush=True)


if __name__ == '__main__':
    main()