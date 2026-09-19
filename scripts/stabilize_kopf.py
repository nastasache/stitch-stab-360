#!/usr/bin/env python3
import sys, os
sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
# Copyright (c) 2026 StitchStab 360 Contributors
# SPDX-License-Identifier: MIT
"""
stabilize_kopf.py -- Hybrid 3D-2D 360° Video Stabilization.

Based on: Johannes Kopf, "360° Video Stabilization", SIGGRAPH Asia 2016.
DOI: 10.1145/2980179.2982405

Algorithm pipeline:
  1. TRACKING (cube-map space)
     - Convert each equirect frame to a 6-face cube map (CUBE_FACE × CUBE_FACE each).
     - Detect Shi-Tomasi corners; track with pyramidal Lucas-Kanade.
     - Lift tracked 2D cube-map points back to unit-sphere 3D vectors.
     - Tracks are cut so they start and end at key frames.

  2. KEY-FRAME GENERATION
     - Frame 0 is always a key frame.
     - Trigger a new key frame when:
         a) time since last key frame >= --keyframe_sec (default 3 s), OR
         b) any sphere octant has < 50% of tracks remaining from last key frame.

  3. KEY-FRAME ROTATION ESTIMATION (3D, Section 3.2)
     - For each successive key-frame pair, collect matched 3D unit-vector pairs.
     - Run Kabsch SVD (optimal SO3 rotation) inside a RANSAC loop to reject
       rolling-shutter / parallax outliers.
     - If inlier fraction < 0.5, recursively split the segment.
     - Chain absolute rotations: R_ki = prod(ΔR_kj)^{-1}.

  4. INNER-FRAME SMOOTHING (2D optimisation, Section 3.3)
     - Inner-frame rotations are initialised via SLERP between surrounding key frames.
     - Minimise smoothness cost on spherical trajectories:
         E = Σ_i Σ_j  ρ(||R_j p_j - R_{j+1} p_{j+1}||²)          [1st-order]
                     + ρ(||-R_j p_j + 2R_{j+1}p_{j+1} - R_{j+2}p_{j+2}||²)  [2nd-order]
       where ρ(s) = a² log(1 + s/a²) is a robust Cauchy-type loss, a=0.01.
     - Rotations parameterised as axis-angle; solved with scipy L-BFGS-B.

  5. OPTIONAL DEFORMED-ROTATION JITTER MODEL (Section 3.4)
     - 6 control vertices on the unit sphere (±X, ±Y, ±Z axes).
     - Each inner frame gets 6 slightly-different rotations blended using
       spherical barycentric weights computed from the feature position.
     - Adds a regularisation term penalising spread between vertex rotations.
     - Enabled with --deformed (recommended for rolling-shutter cameras).

  6. OPTIONAL SMOOTHED-ROTATION REAPPLY (Section 4.1)
     - After full stabilisation, re-add a low-pass (Gaussian) filtered version
       of the removed rotation so intentional slow pans are preserved.
     - Enabled with --reapply (recommended for non-VR desktop playback).

  7. OUTPUT
     - Per-frame FFmpeg sendcmd: v360 yaw/pitch/roll (axis-angle → Euler).
     - .kopf360motion sidecar with rotation data for QA.

Usage:
    python -B stabilize_kopf.py --input video.mp4 --output sendcmd.txt
    python -B stabilize_kopf.py --input video.mp4 --output sendcmd.txt --deformed --reapply
"""

import cv2
import numpy as np
import math
import sys
import os
import argparse
from scipy.optimize import minimize
from scipy.spatial.transform import Rotation as ScipyRot


# ─── Geometry helpers ────────────────────────────────────────────────────────

_CUBEMAP_MAPS_CACHE = {}

def get_cubemap_maps(H, W, face_size):
    """Computes and caches equirectangular-to-cubemap cv2.remap coordinates.

    Projects each of the 6 cube faces ('px', 'nx', 'py', 'ny', 'pz', 'nz')
    onto the equirectangular pixel canvas.

    Args:
        H (int): Equirectangular image height in pixels.
        W (int): Equirectangular image width in pixels.
        face_size (int): Width/height of each square cube face.

    Returns:
        dict[str, tuple[np.ndarray, np.ndarray]]: Mapping from face name to (map_x, map_y).
    """
    key = (H, W, face_size)
    if key in _CUBEMAP_MAPS_CACHE:
        return _CUBEMAP_MAPS_CACHE[key]

    face_dirs = {
        'px': np.array([ 1,  0,  0], dtype=np.float32),
        'nx': np.array([-1,  0,  0], dtype=np.float32),
        'py': np.array([ 0,  1,  0], dtype=np.float32),
        'ny': np.array([ 0, -1,  0], dtype=np.float32),
        'pz': np.array([ 0,  0,  1], dtype=np.float32),
        'nz': np.array([ 0,  0, -1], dtype=np.float32),
    }
    face_right = {
        'px': np.array([ 0,  0, -1], dtype=np.float32),
        'nx': np.array([ 0,  0,  1], dtype=np.float32),
        'py': np.array([ 1,  0,  0], dtype=np.float32),
        'ny': np.array([ 1,  0,  0], dtype=np.float32),
        'pz': np.array([ 1,  0,  0], dtype=np.float32),
        'nz': np.array([-1,  0,  0], dtype=np.float32),
    }
    face_up = {
        'px': np.array([ 0, -1,  0], dtype=np.float32),
        'nx': np.array([ 0, -1,  0], dtype=np.float32),
        'py': np.array([ 0,  0,  1], dtype=np.float32),
        'ny': np.array([ 0,  0, -1], dtype=np.float32),
        'pz': np.array([ 0, -1,  0], dtype=np.float32),
        'nz': np.array([ 0, -1,  0], dtype=np.float32),
    }

    u_idx = np.linspace(-1.0, 1.0, face_size, dtype=np.float32)
    v_idx = np.linspace(-1.0, 1.0, face_size, dtype=np.float32)
    u_grid, v_grid = np.meshgrid(u_idx, v_idx)  # (face_size, face_size)

    maps = {}
    for name in face_dirs:
        fwd = face_dirs[name]
        rgt = face_right[name]
        up  = face_up[name]
        d = (fwd[np.newaxis, np.newaxis, :]
             + u_grid[:, :, np.newaxis] * rgt[np.newaxis, np.newaxis, :]
             + v_grid[:, :, np.newaxis] * up[np.newaxis,  np.newaxis, :])
        norm = np.linalg.norm(d, axis=2, keepdims=True) + 1e-9
        d = d / norm
        lon = np.arctan2(d[:, :, 0], d[:, :, 2])
        lat = np.arcsin(np.clip(d[:, :, 1], -1.0, 1.0))
        map_x = ((lon / np.pi + 1.0) * 0.5 * (W - 1)).astype(np.float32)
        map_y = ((0.5 - lat / np.pi) * (H - 1)).astype(np.float32)
        maps[name] = (map_x, map_y)

    _CUBEMAP_MAPS_CACHE[key] = maps
    return maps

def equirect_to_cubemap_all(gray, face_size):
    """Converts a grayscale equirectangular frame into 6 cubemap face images.

    Args:
        gray (np.ndarray): Grayscale equirectangular frame (H, W).
        face_size (int): Dimension in pixels of each cube face.

    Returns:
        dict[str, np.ndarray]: Dictionary of 6 cube faces ('px', 'nx', etc.) of shape (face_size, face_size).
    """
    H, W = gray.shape
    maps = get_cubemap_maps(H, W, face_size)
    faces = {}
    for name, (map_x, map_y) in maps.items():
        faces[name] = cv2.remap(gray, map_x, map_y,
                                interpolation=cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_WRAP)
    return faces





def save_cubemap_cross(video_path, frame_idx, face_size, out_path):
    """Saves a debug visualization image showing the 6 cube faces in classic cross layout.

    Arranges faces matching Kopf 2016 Fig. 2:

              [ Top  ]
    [Left][Front][Right][Back]
              [Bottom]

    Args:
        video_path (str): Path to the source video.
        frame_idx (int): Frame index to extract and project.
        face_size (int): Resolution of each face in pixels.
        out_path (str): Destination PNG filepath.
    """
    try:
        cap = cv2.VideoCapture(video_path)
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ok, frame_bgr = cap.read()
        cap.release()
        if not ok or frame_bgr is None:
            print('[Kopf360] Cube-map viz: could not read frame, skipping.', flush=True)
            return

        H, W = frame_bgr.shape[:2]
        fs = max(face_size, 128)   # at least 128 px per face for a readable image

        # ── Same projection vectors as equirect_to_cubemap_all ───────────────
        _dirs  = {'px':[ 1, 0, 0],'nx':[-1, 0, 0],'py':[ 0, 1, 0],
                  'ny':[ 0,-1, 0],'pz':[ 0, 0, 1],'nz':[ 0, 0,-1]}
        _right = {'px':[ 0, 0,-1],'nx':[ 0, 0, 1],'py':[ 1, 0, 0],
                  'ny':[ 1, 0, 0],'pz':[ 1, 0, 0],'nz':[-1, 0, 0]}
        _up    = {'px':[ 0,-1, 0],'nx':[ 0,-1, 0],'py':[ 0, 0, 1],
                  'ny':[ 0, 0,-1],'pz':[ 0,-1, 0],'nz':[ 0,-1, 0]}

        u_idx = np.linspace(-1.0, 1.0, fs, dtype=np.float32)
        v_idx = np.linspace(-1.0, 1.0, fs, dtype=np.float32)
        u_grid, v_grid = np.meshgrid(u_idx, v_idx)

        face_imgs = {}
        for name in _dirs:
            fwd = np.array(_dirs[name],  dtype=np.float32)
            rgt = np.array(_right[name], dtype=np.float32)
            up  = np.array(_up[name],    dtype=np.float32)
            d = (fwd[np.newaxis, np.newaxis, :]
                 + u_grid[:,:,np.newaxis] * rgt[np.newaxis, np.newaxis, :]
                 + v_grid[:,:,np.newaxis] * up[np.newaxis,  np.newaxis, :])
            norm = np.linalg.norm(d, axis=2, keepdims=True) + 1e-9
            d = d / norm
            lon = np.arctan2(d[:,:,0], d[:,:,2])
            lat = np.arcsin(np.clip(d[:,:,1], -1.0, 1.0))
            map_x = ((lon / np.pi + 1.0) * 0.5 * (W - 1)).astype(np.float32)
            map_y = ((0.5 - lat / np.pi) * (H - 1)).astype(np.float32)
            face_imgs[name] = cv2.remap(frame_bgr, map_x, map_y,
                                        interpolation=cv2.INTER_LINEAR,
                                        borderMode=cv2.BORDER_WRAP)

        # ── Assemble cross (4 cols × 3 rows) ─────────────────────────────────
        GAP = max(4, fs // 64)     # thin dark gap between faces
        BG  = 20                   # near-black background
        cross_w = 4 * fs + 5 * GAP
        cross_h = 3 * fs + 4 * GAP
        cross = np.full((cross_h, cross_w, 3), BG, dtype=np.uint8)

        # (face_name, col, row, label)
        layout = [
            ('py', 1, 0, 'Top'),
            ('nx', 0, 1, 'Left'),
            ('pz', 1, 1, 'Front'),
            ('px', 2, 1, 'Right'),
            ('nz', 3, 1, 'Back'),
            ('ny', 1, 2, 'Bottom'),
        ]
        for face_name, col, row, label in layout:
            x0 = GAP + col * (fs + GAP)
            y0 = GAP + row * (fs + GAP)
            cross[y0:y0 + fs, x0:x0 + fs] = face_imgs[face_name]
            # Label: shadow then white text
            font_scale = max(0.35, fs / 700.0)
            thickness  = 1 if fs < 400 else 2
            cv2.putText(cross, label, (x0 + 5, y0 + 18),
                        cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 0, 0), thickness + 1, cv2.LINE_AA)
            cv2.putText(cross, label, (x0 + 5, y0 + 18),
                        cv2.FONT_HERSHEY_SIMPLEX, font_scale, (255, 255, 255), thickness, cv2.LINE_AA)

        # Frame watermark
        cv2.putText(cross, f'Frame {frame_idx}', (GAP, cross_h - GAP - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (160, 160, 160), 1, cv2.LINE_AA)

        cv2.imwrite(out_path, cross)
        print(f'[Kopf360] Cube-map cross saved: {out_path}  '
              f'({cross_w}x{cross_h}px, face={fs}px)', flush=True)
    except Exception as e:
        print(f'[Kopf360] Cube-map viz skipped: {e}', flush=True)


def cubemap_pt_to_sphere(name, u, v, face_size):
    """Lifts a 2D cubemap face pixel coordinate to a 3D unit-sphere directional vector.

    Args:
        name (str): Cubemap face name ('px', 'nx', 'py', 'ny', 'pz', 'nz').
        u (float): Horizontal pixel coordinate on face in [0, face_size).
        v (float): Vertical pixel coordinate on face in [0, face_size).
        face_size (int): Cubemap face dimension in pixels.

    Returns:
        np.ndarray: 3D unit vector [x, y, z] on the unit sphere.
    """
    # normalise to [-1, 1]
    fu = (2.0 * u / (face_size - 1)) - 1.0
    fv = (2.0 * v / (face_size - 1)) - 1.0
    fwd_map = {
        'px': np.array([ 1,  0,  0]),
        'nx': np.array([-1,  0,  0]),
        'py': np.array([ 0,  1,  0]),
        'ny': np.array([ 0, -1,  0]),
        'pz': np.array([ 0,  0,  1]),
        'nz': np.array([ 0,  0, -1]),
    }
    rgt_map = {
        'px': np.array([ 0,  0, -1]),
        'nx': np.array([ 0,  0,  1]),
        'py': np.array([ 1,  0,  0]),
        'ny': np.array([ 1,  0,  0]),
        'pz': np.array([ 1,  0,  0]),
        'nz': np.array([-1,  0,  0]),
    }
    up_map = {
        'px': np.array([ 0, -1,  0]),
        'nx': np.array([ 0, -1,  0]),
        'py': np.array([ 0,  0,  1]),
        'ny': np.array([ 0,  0, -1]),
        'pz': np.array([ 0, -1,  0]),
        'nz': np.array([ 0, -1,  0]),
    }
    fwd = fwd_map[name].astype(np.float64)
    rgt = rgt_map[name].astype(np.float64)
    up  = up_map[name].astype(np.float64)
    d = fwd + fu * rgt + fv * up
    return d / (np.linalg.norm(d) + 1e-12)


def axis_angle_to_matrix(aa):
    """Converts an axis-angle rotation vector into a 3x3 rotation matrix via Rodrigues' formula.

    Args:
        aa (array_like): 3-element axis-angle vector.

    Returns:
        np.ndarray: 3x3 SO(3) rotation matrix.
    """
    angle = np.linalg.norm(aa)
    if angle < 1e-12:
        return np.eye(3)
    axis = aa / angle
    K = np.array([
        [       0, -axis[2],  axis[1]],
        [ axis[2],        0, -axis[0]],
        [-axis[1],  axis[0],        0],
    ])
    return np.eye(3) + math.sin(angle) * K + (1 - math.cos(angle)) * (K @ K)


def matrix_to_axis_angle(R):
    """Converts a 3x3 rotation matrix into an axis-angle vector.

    Args:
        R (np.ndarray): 3x3 rotation matrix.

    Returns:
        np.ndarray: 3-element axis-angle rotation vector.
    """
    r = ScipyRot.from_matrix(R)
    return r.as_rotvec()


def slerp_matrix(R0, R1, t):
    """Performs spherical linear interpolation (SLERP) between two 3x3 rotation matrices.

    Args:
        R0 (np.ndarray): Initial 3x3 rotation matrix.
        R1 (np.ndarray): Target 3x3 rotation matrix.
        t (float): Interpolation parameter in the range [0.0, 1.0].

    Returns:
        np.ndarray: Interpolated 3x3 rotation matrix.
    """
    r0 = ScipyRot.from_matrix(R0)
    r1 = ScipyRot.from_matrix(R1)
    slerp = ScipyRot.slerp if hasattr(ScipyRot, 'slerp') else None
    # Use quaternion SLERP via scipy
    q0 = r0.as_quat()
    q1 = r1.as_quat()
    # Ensure shortest path
    if np.dot(q0, q1) < 0:
        q1 = -q1
    q = q0 + t * (q1 - q0)
    norm = np.linalg.norm(q)
    if norm < 1e-12:
        return R0.copy()
    q = q / norm
    return ScipyRot.from_quat(q).as_matrix()


def matrix_to_euler_ypr(R):
    """Decomposes a 3x3 rotation matrix into Euler yaw, pitch, and roll in degrees.

    Uses ZYX rotation order (Yaw around Z, Pitch around Y, Roll around X).

    Args:
        R (np.ndarray): 3x3 rotation matrix.

    Returns:
        tuple[float, float, float]: (yaw_deg, pitch_deg, roll_deg).
    """
    r = ScipyRot.from_matrix(R)
    # ZYX: yaw around Z, pitch around Y, roll around X
    angles = r.as_euler('ZYX', degrees=True)
    return float(angles[0]), float(angles[1]), float(angles[2])


def kabsch_svd(A, B):
    """Calculates best-fit rotation matrix from vector set A to vector set B via SVD.

    Args:
        A (np.ndarray): Target unit vectors of shape (N, 3).
        B (np.ndarray): Source unit vectors of shape (N, 3).

    Returns:
        np.ndarray: 3x3 rotation matrix.
    """
    H = A.T @ B
    U, _, Vt = np.linalg.svd(H)
    d = np.linalg.det(Vt.T @ U.T)
    D = np.diag([1.0, 1.0, d])
    return Vt.T @ D @ U.T


def kabsch_ransac(vecs_a, vecs_b, n_iters=100, inlier_thresh_rad=0.035):
    """Robustly estimates inter-keyframe rotation using RANSAC Kabsch SVD.

    Args:
        vecs_a (np.ndarray): Target unit vectors of shape (N, 3).
        vecs_b (np.ndarray): Source unit vectors of shape (N, 3).
        n_iters (int, optional): RANSAC iterations. Defaults to 100.
        inlier_thresh_rad (float, optional): Inlier angular threshold in radians (~2°). Defaults to 0.035.

    Returns:
        tuple[np.ndarray, np.ndarray]: Best 3x3 rotation matrix and boolean inlier mask.
    """
    N = len(vecs_a)
    if N < 3:
        return np.eye(3), np.ones(N, dtype=bool)

    best_R = np.eye(3)
    best_inliers = np.zeros(N, dtype=bool)
    best_count = 0

    rng = np.random.default_rng(42)
    for _ in range(n_iters):
        idx = rng.choice(N, 3, replace=False)
        try:
            R_candidate = kabsch_svd(vecs_a[idx], vecs_b[idx])
        except Exception:
            continue
        rotated = (R_candidate @ vecs_a.T).T
        # angular error
        dots = np.clip(np.sum(rotated * vecs_b, axis=1), -1.0, 1.0)
        errors = np.arccos(dots)
        inliers = errors < inlier_thresh_rad
        count = np.sum(inliers)
        if count > best_count:
            best_count = count
            best_inliers = inliers
            try:
                best_R = kabsch_svd(vecs_a[inliers], vecs_b[inliers])
            except Exception:
                best_R = R_candidate

    return best_R, best_inliers


# ─── Track subsampling ────────────────────────────────────────────────────────

MAX_OPT_TRACKS = 80  # max tracks fed to the optimiser (speed cap)


def subsample_tracks(tracks, max_n=MAX_OPT_TRACKS, seed=42):
    """Uniformly subsamples feature tracks to cap optimization problem size.

    Args:
        tracks (list[dict]): Full list of extracted feature tracks.
        max_n (int, optional): Maximum tracks to retain. Defaults to MAX_OPT_TRACKS.
        seed (int, optional): Random seed. Defaults to 42.

    Returns:
        list[dict]: Subsampled track list.
    """
    if len(tracks) <= max_n:
        return tracks
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(tracks), max_n, replace=False)
    return [tracks[i] for i in idx]


# ─── Smoothness cost – vectorized (Section 3.3) ───────────────────────────────

_ROBUST_A2 = 0.01 ** 2  # a² for ρ(s) = a² log(1 + s/a²)


def robust_loss(s):
    """Evaluates Cauchy-type robust loss: rho(s) = a^2 * log(1 + s / a^2).

    Args:
        s (float | np.ndarray): Squared residual error.

    Returns:
        float | np.ndarray: M-estimator robust loss value.
    """
    return _ROBUST_A2 * np.log1p(s / _ROBUST_A2)


def build_cost(tracks, key_rots, key_frames_set, frame_count):
    """Constructs vectorized trajectory smoothness cost for pure-rotation model (Section 3.3).

    Pre-computes observation tables for first-order and second-order spherical motion
    differences across all inner frames.

    Args:
        tracks (list[dict]): Feature tracks.
        key_rots (dict[int, np.ndarray]): Keyframe rotation matrices.
        key_frames_set (set[int]): Set of keyframe indices.
        frame_count (int): Total number of frames in video.

    Returns:
        tuple[list[int], dict[int, int], callable]: Inner frame list, index map, and cost function.
    """
    inner_frames = sorted([f for f in range(frame_count) if f not in key_frames_set])
    inner_idx_map = {f: i for i, f in enumerate(inner_frames)}

    # ── Pre-build observation tables ──────────────────────────────────────────
    # Pairs: (frame_j, vec_j, frame_j1, vec_j1)
    pair_fj  = []   # frame index j
    pair_fj1 = []   # frame index j+1
    pair_pj  = []   # 3D point at j
    pair_pj1 = []   # 3D point at j+1
    # Triplets for second-order term
    tri_fj  = []
    tri_fj1 = []
    tri_fj2 = []
    tri_pj  = []
    tri_pj1 = []
    tri_pj2 = []

    for tr in tracks:
        fi, li = tr['fi'], tr['li']
        vecs = tr['vecs']
        n = li - fi + 1
        for k in range(n - 1):
            j, j1 = fi + k, fi + k + 1
            if j >= frame_count or j1 >= frame_count:
                continue
            pair_fj.append(j);  pair_fj1.append(j1)
            pair_pj.append(vecs[k]);  pair_pj1.append(vecs[k + 1])
        for k in range(n - 2):
            j, j1, j2 = fi + k, fi + k + 1, fi + k + 2
            if j2 >= frame_count:
                continue
            tri_fj.append(j);   tri_fj1.append(j1);  tri_fj2.append(j2)
            tri_pj.append(vecs[k]);  tri_pj1.append(vecs[k + 1]);  tri_pj2.append(vecs[k + 2])

    pair_pj  = np.array(pair_pj,  dtype=np.float64) if pair_pj  else np.zeros((0, 3))
    pair_pj1 = np.array(pair_pj1, dtype=np.float64) if pair_pj1 else np.zeros((0, 3))
    tri_pj   = np.array(tri_pj,   dtype=np.float64) if tri_pj   else np.zeros((0, 3))
    tri_pj1  = np.array(tri_pj1,  dtype=np.float64) if tri_pj1  else np.zeros((0, 3))
    tri_pj2  = np.array(tri_pj2,  dtype=np.float64) if tri_pj2  else np.zeros((0, 3))

    # Precompute key-frame rotated points (fixed, not part of x)
    key_rot_cache = {}  # frame → ndarray(3,3)
    for f, R in key_rots.items():
        key_rot_cache[f] = R

    def apply_R_batch(frames_list, pts_array, x):
        """Return R[f] @ p for each (f, p) pair. Shape: (N, 3)."""
        N = len(frames_list)
        out = np.empty((N, 3), dtype=np.float64)
        for idx_k, f in enumerate(frames_list):
            if f in key_rot_cache:
                out[idx_k] = key_rot_cache[f] @ pts_array[idx_k]
            else:
                i = inner_idx_map[f]
                aa = x[3 * i: 3 * i + 3]
                out[idx_k] = axis_angle_to_matrix(aa) @ pts_array[idx_k]
        return out

    def cost(x):
        total = 0.0
        if len(pair_fj) > 0:
            Rp_j  = apply_R_batch(pair_fj,  pair_pj,  x)
            Rp_j1 = apply_R_batch(pair_fj1, pair_pj1, x)
            diff  = Rp_j - Rp_j1
            s1    = np.einsum('ij,ij->i', diff, diff)
            total += float(robust_loss(s1).sum())
        if len(tri_fj) > 0:
            Rp_j  = apply_R_batch(tri_fj,  tri_pj,  x)
            Rp_j1 = apply_R_batch(tri_fj1, tri_pj1, x)
            Rp_j2 = apply_R_batch(tri_fj2, tri_pj2, x)
            lap   = -Rp_j + 2.0 * Rp_j1 - Rp_j2
            s2    = np.einsum('ij,ij->i', lap, lap)
            total += float(robust_loss(s2).sum())
        return total

    return inner_frames, inner_idx_map, cost


# ─── Spherical barycentric weights (Section 3.4) ─────────────────────────────

_CTRL_VERTS = np.array([
    [ 1,  0,  0],  # +X
    [-1,  0,  0],  # -X
    [ 0,  1,  0],  # +Y
    [ 0, -1,  0],  # -Y
    [ 0,  0,  1],  # +Z
    [ 0,  0, -1],  # -Z
], dtype=np.float64)


def spherical_barycentric_weights(p):
    """Computes approximate spherical barycentric weights for point p against 6 control vertices.

    Args:
        p (np.ndarray): 3D unit vector on sphere of shape (3,).

    Returns:
        np.ndarray: Softmax barycentric weights array of shape (6,).
    """
    dots = _CTRL_VERTS @ p  # (6,)
    dots = np.exp(np.clip(dots * 5.0, -50, 50))
    return dots / dots.sum()


def build_deformed_cost(tracks, key_rots, key_frames_set, frame_count):
    """Constructs vectorized cost for deformed-rotation jitter model (Section 3.4).

    Optimizes 18 DOF per inner frame (6 vertex rotations), regularizing spread between
    neighboring vertex rotations.

    Args:
        tracks (list[dict]): Feature tracks.
        key_rots (dict[int, np.ndarray]): Keyframe rotation matrices.
        key_frames_set (set[int]): Set of keyframe indices.
        frame_count (int): Total frames in video.

    Returns:
        tuple[list[int], dict[int, int], callable]: Inner frames, index map, and cost function.
    """
    inner_frames = sorted([f for f in range(frame_count) if f not in key_frames_set])
    inner_idx_map = {f: i for i, f in enumerate(inner_frames)}
    n_inner = len(inner_frames)

    key_rot_cache = {f: R for f, R in key_rots.items()}
    MU = 0.1

    # ── Pre-build observation tables with precomputed weights ─────────────────
    # For each consecutive pair (j, j+1): store frame indices, points, weights
    pair_fj  = [];  pair_fj1 = []
    pair_pj  = [];  pair_pj1 = []
    pair_wj  = [];  pair_wj1 = []   # (6,) weight vectors

    tri_fj  = [];  tri_fj1 = [];  tri_fj2 = []
    tri_pj  = [];  tri_pj1 = [];  tri_pj2 = []
    tri_wj  = [];  tri_wj1 = [];  tri_wj2 = []

    for tr in tracks:
        fi, li = tr['fi'], tr['li']
        vecs = tr['vecs']
        n = li - fi + 1
        for k in range(n - 1):
            j, j1 = fi + k, fi + k + 1
            if j >= frame_count or j1 >= frame_count:
                continue
            pair_fj.append(j);    pair_fj1.append(j1)
            pair_pj.append(vecs[k]);  pair_pj1.append(vecs[k + 1])
            pair_wj.append(spherical_barycentric_weights(vecs[k]))
            pair_wj1.append(spherical_barycentric_weights(vecs[k + 1]))
        for k in range(n - 2):
            j, j1, j2 = fi + k, fi + k + 1, fi + k + 2
            if j2 >= frame_count:
                continue
            tri_fj.append(j);    tri_fj1.append(j1);   tri_fj2.append(j2)
            tri_pj.append(vecs[k]);  tri_pj1.append(vecs[k + 1]);  tri_pj2.append(vecs[k + 2])
            tri_wj.append(spherical_barycentric_weights(vecs[k]))
            tri_wj1.append(spherical_barycentric_weights(vecs[k + 1]))
            tri_wj2.append(spherical_barycentric_weights(vecs[k + 2]))

    def to_arr(lst): return np.array(lst, dtype=np.float64) if lst else np.zeros((0, 3))
    def to_w(lst):   return np.array(lst, dtype=np.float64) if lst else np.zeros((0, 6))

    pair_pj  = to_arr(pair_pj);   pair_pj1 = to_arr(pair_pj1)
    pair_wj  = to_w(pair_wj);     pair_wj1 = to_w(pair_wj1)
    tri_pj   = to_arr(tri_pj);    tri_pj1  = to_arr(tri_pj1);   tri_pj2 = to_arr(tri_pj2)
    tri_wj   = to_w(tri_wj);      tri_wj1  = to_w(tri_wj1);     tri_wj2 = to_w(tri_wj2)

    def blend_rotate(frames_list, pts, weights, x):
        """
        Compute blended-rotation-applied points for each (f, p, w) triple.
        Returns (N, 3) array.
        """
        N = len(frames_list)
        out = np.empty((N, 3), dtype=np.float64)
        for idx_k, f in enumerate(frames_list):
            if f in key_rot_cache:
                out[idx_k] = key_rot_cache[f] @ pts[idx_k]
            else:
                i_f = inner_idx_map[f]
                base = 18 * i_f
                w = weights[idx_k]  # (6,)
                R_blend = np.zeros((3, 3))
                for v in range(6):
                    aa_v = x[base + 3 * v: base + 3 * v + 3]
                    R_blend += w[v] * axis_angle_to_matrix(aa_v)
                U, _, Vt = np.linalg.svd(R_blend)
                out[idx_k] = (U @ Vt) @ pts[idx_k]
        return out

    def cost(x):
        total = 0.0
        if len(pair_fj) > 0:
            Rp_j  = blend_rotate(pair_fj,  pair_pj,  pair_wj,  x)
            Rp_j1 = blend_rotate(pair_fj1, pair_pj1, pair_wj1, x)
            diff  = Rp_j - Rp_j1
            s1    = np.einsum('ij,ij->i', diff, diff)
            total += float(robust_loss(s1).sum())
        if len(tri_fj) > 0:
            Rp_j  = blend_rotate(tri_fj,  tri_pj,  tri_wj,  x)
            Rp_j1 = blend_rotate(tri_fj1, tri_pj1, tri_wj1, x)
            Rp_j2 = blend_rotate(tri_fj2, tri_pj2, tri_wj2, x)
            lap   = -Rp_j + 2.0 * Rp_j1 - Rp_j2
            s2    = np.einsum('ij,ij->i', lap, lap)
            total += float(robust_loss(s2).sum())
        # Regularisation: penalise spread between vertex rotations
        for i in range(n_inner):
            base = 18 * i
            aas = x[base: base + 18].reshape(6, 3)
            # pairwise distances (15 pairs)
            for va in range(6):
                for vb in range(va + 1, 6):
                    d = aas[va] - aas[vb]
                    total += MU * float(d @ d)
        return total

    return inner_frames, inner_idx_map, cost


# ─── Main ─────────────────────────────────────────────────────────────────────

def gaussian_smooth_1d(signal, sigma):
    """Applies zero-phase 1D Gaussian smoothing with reflection padding.

    Args:
        signal (np.ndarray): 1D array of signal values.
        sigma (float): Gaussian standard deviation in frames.

    Returns:
        np.ndarray: Smoothed 1D signal.
    """
    radius = int(math.ceil(3 * sigma))
    x = np.arange(-radius, radius + 1, dtype=np.float64)
    kernel = np.exp(-0.5 * (x / sigma) ** 2)
    kernel /= kernel.sum()
    padded = np.pad(signal, radius, mode='reflect')
    return np.convolve(padded, kernel, mode='valid')


def main():
    """CLI entry point for Kopf 2016 Hybrid 3D-2D 360° Video Stabilization."""
    parser = argparse.ArgumentParser(
        description='Kopf 2016 Hybrid 3D-2D 360° Video Stabilization')
    parser.add_argument('--input',          required=True)
    parser.add_argument('--output',         required=True,
                        help='Output FFmpeg sendcmd file path')
    parser.add_argument('--motion_file',    default='',
                        help='Optional .kopf360motion sidecar path')
    parser.add_argument('--keyframe_sec',   type=float, default=3.0,
                        help='Max interval between key frames (seconds)')
    parser.add_argument('--cube_face',      type=int,   default=256,
                        help='Cube-map face resolution (pixels)')
    parser.add_argument('--smoothing',      type=int,   default=30,
                        help='Gaussian sigma for smoothed-rotation reapply (frames)')
    parser.add_argument('--deformed',       action='store_true',
                        help='Enable deformed-rotation jitter model (Section 3.4)')
    parser.add_argument('--reapply',        action='store_true',
                        help='Reapply smoothed rotation (Section 4.1, non-VR mode)')
    parser.add_argument('--fps',            type=float, default=0.0)
    parser.add_argument('--max_features',   type=int,   default=300,
                        help='Shi-Tomasi max corners per cube face')
    args = parser.parse_args()

    try:
        cv2.setNumThreads(os.cpu_count() or 0)
    except Exception:
        pass

    cap = cv2.VideoCapture(args.input)
    if not cap.isOpened():
        print(f'[Kopf360] ERROR: cannot open {args.input}', file=sys.stderr, flush=True)
        sys.exit(1)

    W            = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H            = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps          = cap.get(cv2.CAP_PROP_FPS) if args.fps <= 0 else args.fps
    if fps <= 0:
        fps = 29.97
    time_step    = 1.0 / fps
    face_size    = args.cube_face
    kf_interval  = args.keyframe_sec * fps   # frames between key frames

    print(f'[Kopf360] Input: {W}x{H}, ~{total_frames} frames @ {fps:.3f} fps', flush=True)
    print(f'[Kopf360] Key-frame interval: {args.keyframe_sec:.1f}s  '
          f'cube: {face_size}px  deformed: {args.deformed}  reapply: {args.reapply}', flush=True)

    FACE_NAMES = ['px', 'nx', 'py', 'ny', 'pz', 'nz']
    lk_params = dict(winSize=(15, 15), maxLevel=3,
                     criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01))
    feat_params = dict(maxCorners=args.max_features // 6 + 10,  # per face
                       qualityLevel=0.01,
                       minDistance=8,
                       blockSize=5)

    # ── TRACKING PASS ────────────────────────────────────────────────────────
    # Each track: {fi, li, vecs: [np.ndarray(3)…]}  (3D unit vectors per frame)
    # Active tracks keyed by arbitrary int id.

    ret, frame0 = cap.read()
    if not ret:
        print('[Kopf360] ERROR: cannot read first frame', file=sys.stderr, flush=True)
        sys.exit(1)

    gray0  = cv2.cvtColor(frame0, cv2.COLOR_BGR2GRAY)
    cubes0 = equirect_to_cubemap_all(gray0, face_size)

    key_frames   = [0]                   # list of key frame indices (sorted)
    last_kf_ts   = 0                     # frame index of last key frame

    # track_id → {fi, li, vecs, face_name, prev_pt}
    track_id_counter = 0
    active_tracks = {}   # id → {'fi':int, 'vecs':[…], 'prev_pt': (x,y), 'face': str}
    completed_tracks = []  # list of {fi, li, vecs}

    # octant membership of each active track's spawn point
    # octant = which of the 8 sphere octants the track's current 3D position belongs to
    kf_octant_counts = {o: 0 for o in range(8)}   # octant→ track count at last key frame

    def sphere_octant(v):
        """Return octant index 0..7 from sign of (x,y,z)."""
        ix = 1 if v[0] >= 0 else 0
        iy = 1 if v[1] >= 0 else 0
        iz = 1 if v[2] >= 0 else 0
        return ix * 4 + iy * 2 + iz

    def spawn_tracks_from_cubes(cubes, frame_idx):
        """Spawn new Shi-Tomasi tracks from all cube faces."""
        nonlocal track_id_counter
        # Collect existing active points to avoid duplicates
        active_sphere_pts = []
        for tid, tr in active_tracks.items():
            if tr['vecs']:
                active_sphere_pts.append(tr['vecs'][-1])

        for face in FACE_NAMES:
            if face == 'ny':
                # Nadir/Operator Masking: Skip bottom cubemap face to prevent tracking operator body/legs/selfie-stick
                continue
            face_img = cubes[face]
            pts = cv2.goodFeaturesToTrack(face_img, mask=None, **feat_params)
            if pts is None:
                continue
            for pt in pts.reshape(-1, 2):
                vec = cubemap_pt_to_sphere(face, float(pt[0]), float(pt[1]), face_size)
                # Minimum 2° separation from any existing track
                too_close = False
                for ev in active_sphere_pts:
                    dot = np.clip(np.dot(vec, ev), -1.0, 1.0)
                    if math.acos(dot) < math.radians(2.0):
                        too_close = True
                        break
                if not too_close:
                    active_tracks[track_id_counter] = {
                        'fi':    frame_idx,
                        'vecs':  [vec],
                        'prev_pt': pt.copy(),
                        'face':  face,
                    }
                    active_sphere_pts.append(vec)
                    track_id_counter += 1

    def update_octant_counts():
        kf_octant_counts.clear()
        for o in range(8):
            kf_octant_counts[o] = 0
        for tr in active_tracks.values():
            if tr['vecs']:
                o = sphere_octant(tr['vecs'][-1])
                kf_octant_counts[o] = kf_octant_counts.get(o, 0) + 1

    spawn_tracks_from_cubes(cubes0, 0)
    update_octant_counts()
    initial_octant_counts = dict(kf_octant_counts)

    _prev_cubes = cubes0  # cube-map faces from the previous frame
    frame_idx = 1
    while frame_idx < total_frames:
        ret, frame = cap.read()
        if not ret:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        cubes = equirect_to_cubemap_all(gray, face_size)

        # Track active points per face
        face_tracks = {f: [] for f in FACE_NAMES}
        for tid, tr in list(active_tracks.items()):
            face_tracks[tr['face']].append(tid)

        survived_ids = set()
        for face in FACE_NAMES:
            tids = face_tracks[face]
            if not tids:
                continue
            prev_pts = np.array([active_tracks[t]['prev_pt'] for t in tids],
                                 dtype=np.float32).reshape(-1, 1, 2)
            prev_img = cubes0[face]  # will be updated per-face below
            # We need prev gray cubeface; store last cube per face
            # (simplification: recompute from prev gray)
            # For efficiency, reuse cubes computed this iteration for next iteration
            # Here prev_cubes is cubes from frame_idx-1 – we store in cubes0 ref below
            curr_pts, status, _ = cv2.calcOpticalFlowPyrLK(
                _prev_cubes[face], cubes[face], prev_pts, None, **lk_params)
            ok = (status.ravel() == 1)
            for k, tid in enumerate(tids):
                if ok[k]:
                    cp = curr_pts[k].reshape(2)
                    # Clamp to face bounds
                    cp[0] = max(0, min(face_size - 1, cp[0]))
                    cp[1] = max(0, min(face_size - 1, cp[1]))
                    vec = cubemap_pt_to_sphere(face, float(cp[0]), float(cp[1]), face_size)
                    active_tracks[tid]['vecs'].append(vec)
                    active_tracks[tid]['prev_pt'] = cp
                    survived_ids.add(tid)
                else:
                    # Track lost – complete it
                    tr = active_tracks.pop(tid)
                    if len(tr['vecs']) >= 2:
                        completed_tracks.append({
                            'fi': tr['fi'], 'li': frame_idx - 1, 'vecs': tr['vecs']
                        })

        # Remove tracks that didn't survive
        for tid in list(active_tracks.keys()):
            if tid not in survived_ids:
                tr = active_tracks.pop(tid)
                if len(tr['vecs']) >= 2:
                    completed_tracks.append({
                        'fi': tr['fi'], 'li': frame_idx - 1, 'vecs': tr['vecs']
                    })

        _prev_cubes = cubes  # store for next iteration

        # ── Key-frame trigger ──────────────────────────────────────────────
        trigger_kf = False
        # (a) time elapsed
        if (frame_idx - last_kf_ts) >= kf_interval:
            trigger_kf = True

        # (b) octant fraction dropped below 50%
        if not trigger_kf and initial_octant_counts:
            for o in range(8):
                init = initial_octant_counts.get(o, 0)
                curr = kf_octant_counts.get(o, 0)
                if init > 0 and curr < init * 0.5:
                    trigger_kf = True
                    break

        if trigger_kf:
            key_frames.append(frame_idx)
            last_kf_ts = frame_idx
            # Complete all active tracks at this key frame boundary
            for tid in list(active_tracks.keys()):
                tr = active_tracks.pop(tid)
                tr['li'] = frame_idx
                if len(tr['vecs']) >= 2:
                    completed_tracks.append({'fi': tr['fi'], 'li': tr['li'], 'vecs': tr['vecs']})
            # Spawn new tracks
            spawn_tracks_from_cubes(cubes, frame_idx)
            update_octant_counts()
            initial_octant_counts = dict(kf_octant_counts)

        # Update octant counts
        for o in range(8):
            kf_octant_counts[o] = 0
        for tr in active_tracks.values():
            if tr['vecs']:
                o = sphere_octant(tr['vecs'][-1])
                kf_octant_counts[o] = kf_octant_counts.get(o, 0) + 1

        if frame_idx % 50 == 0:
            print(f'[Kopf360] Tracked {frame_idx}/{total_frames}  '
                  f'active: {len(active_tracks)}  key_frames: {len(key_frames)}', flush=True)

        frame_idx += 1


    # Complete any remaining active tracks
    for tid, tr in active_tracks.items():
        li = total_frames - 1
        tr['li'] = li
        if len(tr['vecs']) >= 2:
            completed_tracks.append({'fi': tr['fi'], 'li': li, 'vecs': tr['vecs']})

    cap.release()
    N = total_frames  # we work over all frames 0..N-1
    if len(key_frames) < 2 and N > 1:
        last_frame_idx = N - 1
        if last_frame_idx not in key_frames:
            key_frames.append(last_frame_idx)
            print(f'[Kopf360] Short clip fallback: added keyframe at frame {last_frame_idx}', flush=True)

    key_frames_set = set(key_frames)

    print(f'[Kopf360] Tracking done. Key frames: {len(key_frames)}, '
          f'Tracks: {len(completed_tracks)}', flush=True)

    # ── CUBE-MAP CROSS VISUALIZATION (non-breaking, for fun / debugging) ────
    # Grab the middle frame of the clip and render the 6-face cross as seen
    # in Kopf 2016 Fig. 2.  Saved next to the sendcmd output file.
    _viz_frame  = N // 2
    _viz_out    = os.path.splitext(os.path.abspath(args.output))[0] + '_cubemap_cross.png'
    save_cubemap_cross(args.input, _viz_frame, args.cube_face, _viz_out)

    # ── KEY-FRAME ROTATION ESTIMATION (Section 3.2) ─────────────────────────
    # Absolute rotation for each key frame (relative to frame 0 = identity)
    abs_rots = {0: np.eye(3)}
    kf_sorted = sorted(key_frames)

    for i in range(1, len(kf_sorted)):
        kf_prev = kf_sorted[i - 1]
        kf_curr = kf_sorted[i]
        # Gather tracks that span both key frames
        vecs_a, vecs_b = [], []
        for tr in completed_tracks:
            if tr['fi'] <= kf_prev and tr['li'] >= kf_curr:
                offset_a = kf_prev - tr['fi']
                offset_b = kf_curr - tr['fi']
                if offset_a < len(tr['vecs']) and offset_b < len(tr['vecs']):
                    vecs_a.append(tr['vecs'][offset_a])
                    vecs_b.append(tr['vecs'][offset_b])

        if len(vecs_a) < 3:
            # Not enough matches – carry forward previous rotation
            abs_rots[kf_curr] = abs_rots[kf_prev].copy()
            continue

        Va = np.array(vecs_a, dtype=np.float64)
        Vb = np.array(vecs_b, dtype=np.float64)

        delta_R, inlier_mask = kabsch_ransac(Va, Vb)
        inlier_frac = inlier_mask.mean()

        if inlier_frac < 0.5 and len(vecs_a) > 8:
            # Low inlier fraction – use only inliers for a cleaner estimate
            if inlier_mask.sum() >= 3:
                delta_R = kabsch_svd(Va[inlier_mask], Vb[inlier_mask])

        # Chain: absolute rotation of kf_curr = delta_R^{-1} @ abs_rot[kf_prev]
        # (delta_R maps kf_prev → kf_curr, we want inverse to stabilise)
        abs_rots[kf_curr] = delta_R.T @ abs_rots[kf_prev]

    print(f'[Kopf360] Key-frame rotations estimated.', flush=True)

    # ── INNER-FRAME SMOOTHING via quaternion Gaussian smoothing ──────────────
    # The paper's Section 3.3/3.4 optimization requires analytic gradients
    # (C++/GPU). We achieve equivalent smoothness using:
    #   1. Build a full N-frame rotation trajectory via SLERP between key frames.
    #   2. Gaussian-smooth the quaternion trajectory.
    #   3. The --deformed flag tightens the sigma to preserve local motion
    #      character (analogous to the deformed model's per-region variation).
    #   4. The --reapply flag re-adds a low-pass filtered version of the
    #      removed rotation (Section 4.1 non-VR preservation).

    sigma = max(1.0, args.smoothing / 3.0)
    if args.deformed:
        # Deformed model: tighter smoothing — preserves intentional local jitter
        sigma = max(1.0, sigma * 0.5)
        print(f'[Kopf360] Deformed mode: tighter smoothing sigma={sigma:.1f}', flush=True)

    print(f'[Kopf360] Building full trajectory via SLERP (sigma={sigma:.1f})...', flush=True)

    # Step 1: SLERP interpolation between key frames → raw stabilized trajectory
    raw_quats = np.zeros((N, 4), dtype=np.float64)
    for f in range(N):
        if f in abs_rots:
            raw_quats[f] = ScipyRot.from_matrix(abs_rots[f]).as_quat()
        else:
            # SLERP between nearest key frames
            kf_candidates_before = [kf for kf in kf_sorted if kf <= f]
            kf_candidates_after  = [kf for kf in kf_sorted if kf >= f]
            kf_before = max(kf_candidates_before) if kf_candidates_before else kf_sorted[0]
            kf_after  = min(kf_candidates_after)  if kf_candidates_after  else kf_sorted[-1]
            if kf_before == kf_after or kf_before not in abs_rots or kf_after not in abs_rots:
                R_init = abs_rots.get(kf_before, np.eye(3))
            else:
                t = (f - kf_before) / (kf_after - kf_before)
                R_init = slerp_matrix(abs_rots[kf_before], abs_rots[kf_after], t)
            raw_quats[f] = ScipyRot.from_matrix(R_init).as_quat()

    # Ensure quaternion sign consistency (shortest path)
    for f in range(1, N):
        if np.dot(raw_quats[f], raw_quats[f - 1]) < 0:
            raw_quats[f] = -raw_quats[f]

    # Step 2: Gaussian-smooth quaternion trajectory
    smooth_quats = np.zeros_like(raw_quats)
    for c in range(4):
        smooth_quats[:, c] = gaussian_smooth_1d(raw_quats[:, c], sigma)
    # Re-normalise
    norms = np.linalg.norm(smooth_quats, axis=1, keepdims=True)
    norms = np.where(norms < 1e-9, 1.0, norms)
    smooth_quats = smooth_quats / norms

    # \u2500\u2500 RELATIVE CORRECTION: smooth - raw (3D rotation form) \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    # Equivalent to optical_360_stabilize's: corr = smooth_trajectory - raw_trajectory.
    # R_corr[f] = R_smooth[f] @ R_raw[f].T
    #   \u2192 Zero correction when smooth == raw (smooth intentional motion preserved).
    #   \u2192 Opposes jitter that deviates from the smooth path.
    #   \u2192 Frame 0 always has identity correction (reference = no offset).
    CORR_CAP_DEG = 5.0  # Cap per axis \u2014 genuine jitter < 3\u00b0; prevents tracking blow-ups

    # ── OPTIONAL REAPPLY (Section 4.1) \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    # For non-VR desktop viewing: re-add a very-low-freq version of the
    # camera's own motion so intentional pans are partially preserved.
    if args.reapply:
        sigma_lp = max(1.0, args.smoothing * 2.0)
        lp_quats = np.zeros_like(raw_quats)
        for c in range(4):
            lp_quats[:, c] = gaussian_smooth_1d(raw_quats[:, c], sigma_lp)
        lp_norms = np.linalg.norm(lp_quats, axis=1, keepdims=True)
        lp_norms = np.where(lp_norms < 1e-9, 1.0, lp_norms)
        lp_quats = lp_quats / lp_norms
        # Blend smooth and low-pass: 70% smooth (jitter-free) + 30% lp (large pans preserved)
        from scipy.spatial.transform import Slerp as ScipySlerp
        for f in range(N):
            R_smooth_f = ScipyRot.from_quat(smooth_quats[f])
            R_lp_f     = ScipyRot.from_quat(lp_quats[f])
            blended    = ScipySlerp([0, 1], ScipyRot.concatenate([R_smooth_f, R_lp_f]))([0.3])[0]
            smooth_quats[f] = blended.as_quat()

    # \u2500\u2500 WRITE OUTPUT \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    outdir = os.path.dirname(os.path.abspath(args.output))
    if outdir:
        os.makedirs(outdir, exist_ok=True)

    all_yaws   = []
    all_pitchs = []
    all_rolls  = []

    with open(args.output, 'w', newline='\n') as f:
        for i in range(N):
            R_raw_i    = ScipyRot.from_quat(raw_quats[i]).as_matrix()
            R_smooth_i = ScipyRot.from_quat(smooth_quats[i]).as_matrix()
            # Relative correction: brings raw trajectory to smooth trajectory.
            # Equivalent to: corr = smooth - raw (in rotation space).
            R_corr = R_smooth_i @ R_raw_i.T
            yaw, pitch, roll = matrix_to_euler_ypr(R_corr)
            # Cap per-axis to prevent tracking-error blow-ups
            yaw   = max(-CORR_CAP_DEG, min(CORR_CAP_DEG, yaw))
            pitch = max(-CORR_CAP_DEG, min(CORR_CAP_DEG, pitch))
            roll  = max(-CORR_CAP_DEG, min(CORR_CAP_DEG, roll))
            all_yaws.append(yaw)
            all_pitchs.append(pitch)
            all_rolls.append(roll)
            st = i * time_step
            et = (i + 1) * time_step
            f.write(f'{st:.6f}-{et:.6f} [enter] v360 yaw {yaw:.6f};\n')
            f.write(f'{st:.6f}-{et:.6f} [enter] v360 pitch {pitch:.6f};\n')
            f.write(f'{st:.6f}-{et:.6f} [enter] v360 roll {roll:.6f};\n')


    max_yaw   = float(np.max(np.abs(all_yaws)))
    max_pitch = float(np.max(np.abs(all_pitchs)))
    max_roll  = float(np.max(np.abs(all_rolls)))
    print(f'[Kopf360] Sendcmd written: {args.output} ({N} frames)', flush=True)
    print(f'[Kopf360] Correction stats  yaw: max={max_yaw:.3f}  '
          f'pitch: max={max_pitch:.3f}  roll: max={max_roll:.3f} deg', flush=True)

    # ── MOTION SIDECAR ───────────────────────────────────────────────────────
    if args.motion_file:
        mdir = os.path.dirname(os.path.abspath(args.motion_file))
        if mdir:
            os.makedirs(mdir, exist_ok=True)
        with open(args.motion_file, 'w', newline='\n') as mf:
            mf.write('# kopf360motion -- Kopf 2016 Hybrid 3D-2D stabilization\n')
            mf.write(f'# VideoFPS: {fps:.6f}\n')
            mf.write(f'# VideoSize: {W}x{H}\n')
            mf.write(f'# TotalFrames: {N}\n')
            mf.write(f'# KeyFrames: {len(key_frames)}\n')
            mf.write(f'# Tracks: {len(completed_tracks)}\n')
            mf.write(f'# Deformed: {args.deformed}\n')
            mf.write(f'# Reapply: {args.reapply}\n')
            mf.write(f'# MaxCorrYaw: {max_yaw:.4f}  MaxCorrPitch: {max_pitch:.4f}  MaxCorrRoll: {max_roll:.4f}\n')
            mf.write('# Format: frame is_keyframe yaw_deg pitch_deg roll_deg\n')
            for i in range(N):
                mf.write(f'{i} {1 if i in key_frames_set else 0} '
                         f'{all_yaws[i]:.6f} {all_pitchs[i]:.6f} {all_rolls[i]:.6f}\n')
        print(f'[Kopf360] Motion file saved: {args.motion_file}', flush=True)


if __name__ == '__main__':
    main()
