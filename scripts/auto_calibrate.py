import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

"""Automated optical calibration and horizon leveling for dual-fisheye video/images.

Performs seam feature matching, field-of-view optimization, roll/y-offset fine-tuning,
and horizon leveling via visual contours, structural verticality, water surface fitting,
and IMU gravity fusion.
"""

import argparse
import json
import math
import subprocess
import numpy as np
import cv2

WIN_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from utils.global_calibrate import detect_circle_bounds, fisheye_to_equirect_strip, get_video_duration

def build_equirect_thumbnail(front_half, rear_half, fov_deg, left_y_offset=0.0, rear_roll_offset=0.0, out_w=512, out_h=256):
    """Render a fast low-resolution stitched equirectangular thumbnail for scene orientation analysis.

    Args:
        front_half: BGR image numpy array of front fisheye half.
        rear_half: BGR image numpy array of rear fisheye half.
        fov_deg: Assumed diagonal/circular field-of-view in degrees.
        left_y_offset: Vertical center shift in pixels for front lens.
        rear_roll_offset: Rotational roll adjustment in degrees for rear lens.
        out_w: Output thumbnail equirectangular width in pixels.
        out_h: Output thumbnail equirectangular height in pixels.

    Returns:
        np.ndarray: Blended BGR equirectangular thumbnail image.
    """
    hf, wf = front_half.shape[:2]
    cx_f, cy_f = wf / 2.0, hf / 2.0 + left_y_offset
    r_max_f = min(wf, hf) / 2.0
    
    hr, wr = rear_half.shape[:2]
    cx_r, cy_r = wr / 2.0, hr / 2.0
    r_max_r = min(wr, hr) / 2.0
    
    max_theta = np.radians(fov_deg / 2.0)
    
    # Equirectangular longitude [-pi, pi] and latitude [pi/2, -pi/2]
    u = np.linspace(-np.pi, np.pi, out_w, dtype=np.float32)
    v = np.linspace(np.pi / 2.0, -np.pi / 2.0, out_h, dtype=np.float32)
    u_grid, v_grid = np.meshgrid(u, v)
    
    # 3D Cartesian coordinates on unit sphere
    x = np.cos(v_grid) * np.sin(u_grid)
    y = np.sin(v_grid)
    z = np.cos(v_grid) * np.cos(u_grid)
    
    # Front lens mask (z >= 0)
    front_mask = z >= 0
    
    # Front lens mapping
    theta_f = np.arccos(np.clip(z, -1.0, 1.0))
    phi_f = np.arctan2(y, x)
    r_f = (theta_f / max_theta) * r_max_f
    map_x_f = cx_f + r_f * np.cos(phi_f)
    map_y_f = cy_f + r_f * np.sin(phi_f)
    
    # Rear lens mapping (facing -z, flip x and z, apply rear roll)
    x_r_raw = -x
    y_r_raw = y
    z_r_raw = -z
    if rear_roll_offset != 0.0:
        roll_rad = np.radians(-rear_roll_offset)
        cos_r = np.cos(roll_rad)
        sin_r = np.sin(roll_rad)
        x_r = cos_r * x_r_raw - sin_r * y_r_raw
        y_r = sin_r * x_r_raw + cos_r * y_r_raw
    else:
        x_r = x_r_raw
        y_r = y_r_raw
        
    theta_r = np.arccos(np.clip(z_r_raw, -1.0, 1.0))
    phi_r = np.arctan2(y_r, x_r)
    r_r = (theta_r / max_theta) * r_max_r
    map_x_r = cx_r + r_r * np.cos(phi_r)
    map_y_r = cy_r + r_r * np.sin(phi_r)
    
    warped_f = cv2.remap(front_half, map_x_f.astype(np.float32), map_y_f.astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    warped_r = cv2.remap(rear_half, map_x_r.astype(np.float32), map_y_r.astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    
    mask_3d = np.repeat(front_mask[:, :, np.newaxis], 3, axis=2)
    equirect = np.where(mask_3d, warped_f, warped_r)
    return equirect

def extract_telemetry_orientation(video_path):
    """Extract gravity-based pitch and roll angles from accompanying telemetry file.

    Args:
        video_path: Filesystem path to the input video file.

    Returns:
        tuple[float | None, float | None]: Tuple of (pitch_deg, roll_deg), or (None, None)
            if no valid accelerometer telemetry is detected.
    """
    if not video_path:
        return None, None
    base_no_ext = os.path.splitext(video_path)[0]
    candidates = [
        f"{base_no_ext}.gcsv",
        f"{video_path}.gcsv",
        f"{base_no_ext}_telemetry.gcsv",
        f"{base_no_ext}_telemetry.csv",
        f"{base_no_ext}.csv",
    ]
    for cfile in candidates:
        if os.path.exists(cfile) and os.path.getsize(cfile) > 0:
            try:
                # Read CSV and detect accelerometer/gravity columns
                import csv
                with open(cfile, 'r', encoding='utf-8', errors='ignore') as f:
                    reader = csv.reader(f)
                    header = [h.strip().lower() for h in next(reader, [])]
                    acc_cols = []
                    for name in ['acc_x', 'acc_y', 'acc_z', 'acx', 'acy', 'acz', 'ax', 'ay', 'az']:
                        if name in header:
                            acc_cols.append(header.index(name))
                    if len(acc_cols) == 3:
                        ax_list, ay_list, az_list = [], [], []
                        for row in reader:
                            if len(row) > max(acc_cols):
                                try:
                                    ax_list.append(float(row[acc_cols[0]]))
                                    ay_list.append(float(row[acc_cols[1]]))
                                    az_list.append(float(row[acc_cols[2]]))
                                except ValueError:
                                    continue
                        if len(ax_list) > 10:
                            mean_ax = np.median(ax_list)
                            mean_ay = np.median(ay_list)
                            mean_az = np.median(az_list)
                            pitch_deg = math.degrees(math.atan2(-mean_ay, math.sqrt(mean_ax**2 + mean_az**2)))
                            roll_deg = math.degrees(math.atan2(mean_ax, mean_az))
                            return float(np.clip(pitch_deg, -30.0, 30.0)), float(np.clip(roll_deg, -30.0, 30.0))
            except Exception:
                pass
    return None, None

def estimate_heading_yaw(frame_pairs):
    """Estimate dominant forward heading yaw offset from multi-frame motion expansion.

    Excludes the nadir region to avoid bias from mounting hardware or tripods.

    Args:
        frame_pairs: List of (front_half, rear_half) image tuple pairs.

    Returns:
        float: Estimated yaw rotation correction in degrees.
    """
    if not frame_pairs or len(frame_pairs) < 2:
        return 0.0
    try:
        f0 = cv2.resize(frame_pairs[0][0], (256, 256))
        f1 = cv2.resize(frame_pairs[1][0], (256, 256))
        g0 = cv2.cvtColor(f0, cv2.COLOR_BGR2GRAY)
        g1 = cv2.cvtColor(f1, cv2.COLOR_BGR2GRAY)
        
        flow = cv2.calcOpticalFlowFarneback(g0, g1, None, 0.5, 3, 15, 3, 5, 1.2, 0)
        u = flow[..., 0]
        # Active scene band excluding nadir (rows 50 to 175 out of 256)
        mid_u = np.median(u[50:175, 40:216])
        if abs(mid_u) > 0.8:
            yaw_deg = float(np.clip(-mid_u * 2.5, -45.0, 45.0))
            return round(yaw_deg, 2)
    except Exception:
        pass
    return 0.0

def detect_water_surface_horizon(equirect_bgr):
    """Detect horizontal water bodies and fit physical horizon sinusoid.

    Args:
        equirect_bgr: BGR equirectangular panorama image array.

    Returns:
        tuple[float | None, float | None, float]: Tuple of (pitch_deg, roll_deg,
            confidence_score), or (None, None, 0.0) if no water body is detected.
    """
    h, w = equirect_bgr.shape[:2]
    # Analyze equatorial to lower region (excluding nadir mounting area: rows 0.35*h to 0.70*h)
    r_start = int(0.35 * h)
    r_end = int(0.70 * h)
    roi = equirect_bgr[r_start:r_end, :]
    
    # Convert to HSV and compute local standard deviation (texture smoothness)
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    
    blur = cv2.blur(gray, (15, 15))
    blur_sq = cv2.blur(gray.astype(np.float32)**2, (15, 15))
    variance = np.maximum(0, blur_sq - blur.astype(np.float32)**2)
    std_dev = np.sqrt(variance)
    
    # Water characteristics: low texture variance + blue/cyan or reflective sky tones (Sat < 160, Val > 40)
    smooth_mask = std_dev < 18.0
    color_mask = ((hsv[:, :, 0] >= 80) & (hsv[:, :, 0] <= 135)) | (hsv[:, :, 1] < 70)
    water_candidates = smooth_mask & color_mask
    
    # Morphological closing to consolidate water regions
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 7))
    water_mask = cv2.morphologyEx(water_candidates.astype(np.uint8), cv2.MORPH_CLOSE, kernel)
    
    col_sums = np.sum(water_mask, axis=0)
    water_cols = np.where(col_sums > (0.15 * (r_end - r_start)))[0]
    
    if len(water_cols) < (0.25 * w):
        return None, None, 0.0
        
    horizon_pts = []
    for col in water_cols:
        col_water = np.where(water_mask[:, col] > 0)[0]
        if len(col_water) > 0:
            top_y = col_water[0] + r_start
            lon = (col / w - 0.5) * (2.0 * math.pi)
            horizon_pts.append((lon, top_y))
            
    if len(horizon_pts) < 40:
        return None, None, 0.0
        
    lons = np.array([p[0] for p in horizon_pts])
    ys = np.array([p[1] for p in horizon_pts])
    
    M = np.column_stack([np.ones_like(lons), np.cos(lons), np.sin(lons)])
    try:
        coeffs, _, _, _ = np.linalg.lstsq(M, ys, rcond=None)
        y0, A, B = coeffs
        deg_per_px = 180.0 / h
        pitch_est = -float(A * deg_per_px)
        roll_est  = float(B * deg_per_px)
        
        fitted_y = M @ coeffs
        rmse = np.sqrt(np.mean((ys - fitted_y)**2))
        confidence = float(np.clip(1.0 - (rmse / 15.0), 0.0, 1.0))
        if confidence > 0.50:
            return pitch_est, roll_est, confidence
    except Exception:
        pass
    return None, None, 0.0

def filter_tree_and_pole_lines(equirect_bgr, lines_flat):
    """Extract and weight structural vertical line segments from detected lines.

    Filters and weights lines corresponding to poles, tree trunks, and pillars
    while excluding zenith and nadir regions.

    Args:
        equirect_bgr: BGR equirectangular image array.
        lines_flat: (N, 4) array of line endpoints [x1, y1, x2, y2].

    Returns:
        list[tuple[tuple[float, float, float, float], float]]: List of ((x1, y1, x2, y2), weight).
    """
    h, w = equirect_bgr.shape[:2]
    gray = cv2.cvtColor(equirect_bgr, cv2.COLOR_BGR2GRAY)
    
    sobel_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    sobel_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    edge_vert_ratio = np.abs(sobel_x) / (np.abs(sobel_y) + 1e-4)
    
    weighted_lines = []
    for line in lines_flat:
        x1, y1, x2, y2 = line
        min_y = min(y1, y2)
        max_y = max(y1, y2)
        
        # Strict Nadir (bottom 30%) and Zenith (top 12%) exclusion
        if max_y > 0.70 * h or min_y < 0.12 * h:
            continue
            
        dx = abs(x2 - x1)
        dy = abs(y2 - y1)
        len_px = math.hypot(dx, dy)
        if len_px < 15:
            continue
            
        # Vertical structural constraint (filter out diagonal wires and horizontal branches)
        if dy < 1.6 * dx:
            continue
            
        mid_x = int(np.clip((x1 + x2) / 2.0, 0, w - 1))
        mid_y = int(np.clip((y1 + y2) / 2.0, 0, h - 1))
        vr = edge_vert_ratio[mid_y, mid_x]
        
        # Weight multiplier: tree trunks & poles have strong vertical gradient transitions
        weight = len_px * (2.5 if vr > 1.8 else 1.0)
        weighted_lines.append(((x1, y1, x2, y2), weight))
        
    return weighted_lines

def estimate_rotation_and_horizon(frame_pairs, fov_deg=190.00, left_y_offset=0.00, rear_roll_offset=0.00, video_path=None):
    """Estimate corrective Pitch, Roll, and Yaw angles matching FFmpeg v360 conventions.

    Fuses visual sky/ground horizon elevation, pole verticality consensus,
    water surface horizon, and telemetry gravity.

    Args:
        frame_pairs: List of (front_half, rear_half) image pairs.
        fov_deg: Field of view in degrees.
        left_y_offset: Vertical offset for left lens.
        rear_roll_offset: Rear lens roll offset in degrees.
        video_path: Optional path to source video for companion telemetry inspection.

    Returns:
        tuple[float, float, float]: Corrective (yaw, pitch, roll) angles in degrees.
    """
    pitch_corr = 0.0
    roll_corr = 0.0
    yaw_corr = 0.0
    
    # 1. Check telemetry gravity first if video_path is available
    if video_path:
        tel_pitch, tel_roll = extract_telemetry_orientation(video_path)
        if tel_pitch is not None and tel_roll is not None:
            return round(float(yaw_corr), 2), round(float(tel_pitch), 2), round(float(tel_roll), 2)
            
    if not frame_pairs:
        return 0.0, 0.0, 0.0
        
    # 2. Extract visual horizon elevations and 3D line segment endpoints across sample frames
    lsd = cv2.createLineSegmentDetector(0)
    all_pts = []
    sky_pitches = []
    water_pitch_list = []
    water_roll_list = []
    water_conf_list = []
    
    for front_half, rear_half in frame_pairs:
        eq_thumb = build_equirect_thumbnail(
            front_half, rear_half,
            fov_deg=fov_deg, left_y_offset=left_y_offset, rear_roll_offset=rear_roll_offset,
            out_w=512, out_h=256
        )
        h, w = eq_thumb.shape[:2]
        
        # A. Visual Sky-to-Ground Horizon Elevation
        hsv = cv2.cvtColor(eq_thumb, cv2.COLOR_BGR2HSV)
        b_ch, g_ch, r_ch = cv2.split(eq_thumb)
        sky_score = (hsv[:, :, 2].astype(np.float32) / 255.0) + 0.5 * ((b_ch.astype(np.float32) - r_ch.astype(np.float32)) / 255.0)
        
        horizon_ys = []
        search_start = int(0.20 * h)
        search_end = int(0.75 * h)
        for col in range(w):
            col_scores = sky_score[:, col]
            blurred_col = np.convolve(col_scores, np.ones(11)/11.0, mode='same')
            grad = np.gradient(blurred_col)
            best_y = search_start + np.argmin(grad[search_start:search_end])
            horizon_ys.append(best_y)
            
        median_horizon_y = float(np.median(horizon_ys))
        deg_per_px = 180.0 / h
        frame_sky_pitch = (median_horizon_y - (h / 2.0)) * deg_per_px
        sky_pitches.append(frame_sky_pitch)
        
        # B. Detect water surface horizon
        wp, wr, wconf = detect_water_surface_horizon(eq_thumb)
        if wp is not None and wconf > 0.50:
            water_pitch_list.append(wp)
            water_roll_list.append(wr)
            water_conf_list.append(wconf)
            
        # C. Detect upright structural lines (poles, tree trunks)
        gray = cv2.cvtColor(eq_thumb, cv2.COLOR_BGR2GRAY)
        lines, _, _, _ = lsd.detect(gray)
        
        if lines is not None and len(lines) >= 2:
            lines_flat = lines.reshape(-1, 4)
            for line in lines_flat:
                x1, y1, x2, y2 = line
                dx = abs(x2 - x1)
                dy = abs(y2 - y1)
                len_px = math.hypot(dx, dy)
                if len_px < 15:
                    continue
                min_y = min(y1, y2)
                max_y = max(y1, y2)
                if max_y > 0.70 * h or min_y < 0.20 * h:
                    continue
                if dy < 3.0 * dx: # Strict verticality (reject curved wires and sloped ground)
                    continue
                    
                lon1 = (x1 / w - 0.5) * (2.0 * math.pi)
                lat1 = (0.5 - y1 / h) * math.pi
                p1 = np.array([math.cos(lat1) * math.sin(lon1), math.sin(lat1), math.cos(lat1) * math.cos(lon1)], dtype=np.float32)
                
                lon2 = (x2 / w - 0.5) * (2.0 * math.pi)
                lat2 = (0.5 - y2 / h) * math.pi
                p2 = np.array([math.cos(lat2) * math.sin(lon2), math.sin(lat2), math.cos(lat2) * math.cos(lon2)], dtype=np.float32)
                
                all_pts.append((p1, p2, len_px))
                    
    # 3. Consensus Pitch & Roll Optimization
    base_pitch = float(np.median(sky_pitches)) if sky_pitches else 0.0
    best_p = base_pitch
    best_r = 0.0
    
    if all_pts and len(all_pts) >= 2:
        best_cost = 999.0
        p_cands = np.linspace(base_pitch - 6.0, base_pitch + 6.0, 61)
        r_cands = np.linspace(-15.0, 15.0, 31)
        
        for p_cand in p_cands:
            for r_cand in r_cands:
                pr = math.radians(p_cand)
                rr = math.radians(r_cand)
                # Aligned with FFmpeg v360 transformation matrix
                Rx = np.array([[1, 0, 0], [0, math.cos(pr), math.sin(pr)], [0, -math.sin(pr), math.cos(pr)]])
                Rz = np.array([[math.cos(rr), math.sin(rr), 0], [-math.sin(rr), math.cos(rr), 0], [0, 0, 1]])
                R = Rx @ Rz
                
                errs = []
                for p1, p2, w_len in all_pts:
                    p1_r = R @ p1
                    p2_r = R @ p2
                    lon1_r = math.atan2(p1_r[0], p1_r[2])
                    lon2_r = math.atan2(p2_r[0], p2_r[2])
                    d_lon = abs(lon2_r - lon1_r)
                    if d_lon > math.pi:
                        d_lon = 2.0 * math.pi - d_lon
                    errs.append(d_lon * w_len)
                    
                cost = float(np.mean(errs)) + 0.003 * (r_cand**2)
                if cost < best_cost:
                    best_cost = cost
                    best_p = float(p_cand)
                    best_r = float(r_cand)
                    
    pitch_corr = round(best_p, 2)
    roll_corr  = round(best_r, 2)
            
    # 4. Fuse with Water Surface Horizon if detected
    if water_pitch_list and len(water_pitch_list) > 0:
        mean_w_pitch = float(np.median(water_pitch_list))
        mean_w_roll = float(np.median(water_roll_list))
        mean_w_conf = float(np.mean(water_conf_list))
        if mean_w_conf >= 0.75:
            pitch_corr = float(np.clip(mean_w_pitch, -30.0, 30.0))
            roll_corr = float(np.clip(mean_w_roll, -30.0, 30.0))
        elif mean_w_conf >= 0.50:
            pitch_corr = float(np.clip(0.6 * mean_w_pitch + 0.4 * pitch_corr, -30.0, 30.0))
            roll_corr = float(np.clip(0.6 * mean_w_roll + 0.4 * roll_corr, -30.0, 30.0))
            
    # 5. Estimate Yaw from optical flow across multi-frames
    if frame_pairs and len(frame_pairs) >= 2:
        yaw_corr = estimate_heading_yaw(frame_pairs)
        
    return round(float(yaw_corr), 2), round(float(pitch_corr), 2), round(float(roll_corr), 2)

def evaluate_alignment(front_half, rear_half, ih_fov, iv_fov, left_y_offset, rear_roll_offset, detector, matcher, mode="balanced"):
    """Compute seam alignment disparity score between front and rear lenses.

    Args:
        front_half: Front fisheye half image array.
        rear_half: Rear fisheye half image array.
        ih_fov: Horizontal / front lens FOV in degrees.
        iv_fov: Vertical / rear lens FOV in degrees.
        left_y_offset: Front lens vertical pixel shift.
        rear_roll_offset: Rear lens roll adjustment in degrees.
        detector: OpenCV 2D feature detector (e.g. ORB).
        matcher: OpenCV descriptor matcher (e.g. BFMatcher).
        mode: Feature selection mode ('foreground', 'balanced', 'infinity').

    Returns:
        tuple[float, int]: Mean disparity error across both seams and total matches found.
    """
    scores = []
    total_matches = 0
    
    # Configure mode-dependent crop windows and foreground weights
    if mode == "foreground":
        row_start, row_end = 100, 460
        ground_boost = 3.0
        pivot_y = 180.0
    elif mode == "infinity":
        row_start, row_end = 180, 480
        ground_boost = 0.0
        pivot_y = 150.0
    else:  # balanced
        row_start, row_end = 130, 470
        ground_boost = 1.2
        pivot_y = 180.0
    
    # Test both seams: Seam A at +90 deg, Seam B at -90 deg
    for seam_center in [90.0, -90.0]:
        kp_f, des_f = extract_strip_features(
            front_half, fov_deg=ih_fov, yaw_center_deg=seam_center,
            y_offset=left_y_offset, roll_deg=0.0,
            row_start=row_start, row_end=row_end, detector=detector
        )
        
        rear_seam_center = -seam_center
        kp_r, des_r = extract_strip_features(
            rear_half, fov_deg=iv_fov, yaw_center_deg=rear_seam_center,
            y_offset=0.0, roll_deg=rear_roll_offset,
            row_start=row_start, row_end=row_end, detector=detector
        )
        
        median_err, match_cnt = match_seam_descriptors(
            kp_f, des_f, kp_r, des_r, matcher, ground_boost=ground_boost, pivot_y=pivot_y
        )
        if median_err is not None:
            scores.append(median_err)
            total_matches += match_cnt
        
    if not scores:
        return 999.0, 0
    return float(np.mean(scores)), total_matches

def extract_strip_features(img_half, fov_deg, yaw_center_deg, y_offset, roll_deg, row_start, row_end, detector):
    """Warp seam strip to equirectangular perspective and extract keypoint descriptors.

    Args:
        img_half: Fisheye half image array.
        fov_deg: Lens FOV in degrees.
        yaw_center_deg: Seam center azimuth in degrees (+90 or -90).
        y_offset: Vertical lens shift in pixels.
        roll_deg: Roll rotation in degrees.
        row_start: Starting row index for feature extraction ROI.
        row_end: Ending row index for feature extraction ROI.
        detector: OpenCV feature detector.

    Returns:
        tuple[list[cv2.KeyPoint], np.ndarray | None]: Extracted keypoints and descriptor matrix.
    """
    strip = fisheye_to_equirect_strip(
        img_half, fov_deg=fov_deg, yaw_center_deg=yaw_center_deg,
        yaw_span_deg=30.0, out_w=300, out_h=600, y_offset=y_offset, roll_deg=roll_deg
    )
    gray = cv2.cvtColor(strip[row_start:row_end, :], cv2.COLOR_BGR2GRAY) if len(strip.shape) == 3 else strip[row_start:row_end, :]
    kp, des = detector.detectAndCompute(gray, None)
    return kp, des

def match_seam_descriptors(kp_f, des_f, kp_r, des_r, matcher, ground_boost=1.2, pivot_y=180.0):
    """Calculate disparity matching score between front and rear seam descriptors.

    Args:
        kp_f: Front seam keypoints.
        des_f: Front seam descriptor array.
        kp_r: Rear seam keypoints.
        des_r: Rear seam descriptor array.
        matcher: OpenCV descriptor matcher.
        ground_boost: Multiplier weight favoring lower ground features.
        pivot_y: Y coordinate threshold for ground feature weighting.

    Returns:
        tuple[float | None, int]: Median disparity error (or None if insufficient matches)
            and count of top matches.
    """
    if des_f is None or des_r is None or len(kp_f) < 4 or len(kp_r) < 4:
        return None, 0
    matches = matcher.match(des_f, des_r)
    if len(matches) < 4:
        return None, 0
    matches = sorted(matches, key=lambda m: m.distance)
    top_matches = matches[:min(len(matches), 60)]
    pts_f = np.array([kp_f[m.queryIdx].pt for m in top_matches])
    pts_r = np.array([kp_r[m.trainIdx].pt for m in top_matches])
    diff = np.linalg.norm(pts_f - pts_r, axis=1)
    if ground_boost > 0.0:
        weights = 1.0 + ground_boost * np.maximum(0.0, (pivot_y - pts_f[:, 1]) / pivot_y)
        weighted_diff = diff * weights
        median_err = float(np.median(weighted_diff))
    else:
        median_err = float(np.median(diff))
    return median_err, len(top_matches)

def extract_video_calibration_frames(video_path, preview_time=None, num_frames=5, out_dir="data/runtime/temp/calib_frames"):
    """Extract representative sample frames across video duration for calibration.

    Args:
        video_path: Filesystem path to the input dual-fisheye video.
        preview_time: Optional playhead timestamp in seconds to prioritize.
        num_frames: Total number of frames to extract across the video timeline.
        out_dir: Directory where extracted JPEG frames are stored.

    Returns:
        list[str]: Sorted list of paths to extracted image frame files.
    """
    os.makedirs(out_dir, exist_ok=True)
    duration = get_video_duration(video_path)
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    
    # Determine sample timestamps
    timestamps = []
    if duration > 0.4:
        step = duration / (num_frames + 1)
        timestamps = [round(step * (i + 1), 2) for i in range(num_frames)]
    else:
        timestamps = [round(0.2 * (i + 1), 2) for i in range(num_frames)]
        
    if preview_time is not None and preview_time >= 0:
        preview_time = round(float(preview_time), 2)
        if preview_time not in timestamps:
            timestamps[0] = preview_time
            timestamps.sort()
            
    frame_files = []
    missing_timestamps = []
    
    for ts in timestamps:
        ts_str = str(ts).replace('.', '_')
        frame_file = os.path.join(out_dir, f"calib_{base_name}_{ts_str}.jpg")
        if os.path.exists(frame_file) and os.path.getsize(frame_file) > 0 and os.path.getmtime(frame_file) >= os.path.getmtime(video_path):
            frame_files.append(frame_file)
        else:
            missing_timestamps.append((ts, frame_file))

    # Fast in-memory frame extraction via OpenCV VideoCapture
    if missing_timestamps:
        cap = cv2.VideoCapture(video_path)
        if cap.isOpened():
            v_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            total_video_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            still_missing = []
            for ts, frame_file in missing_timestamps:
                frame_no = min(total_video_frames - 1, max(0, int(round(ts * v_fps)))) if total_video_frames > 0 else int(round(ts * v_fps))
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
                ret, frame = cap.read()
                if ret and frame is not None:
                    cv2.imwrite(frame_file, frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
                    frame_files.append(frame_file)
                else:
                    still_missing.append((ts, frame_file))
            cap.release()
            missing_timestamps = still_missing

    # FFmpeg fallback for any remaining frames
    for ts, frame_file in missing_timestamps:
        cmd = ["ffmpeg", "-y", "-ss", str(ts), "-i", video_path, "-vframes", "1", "-q:v", "2", frame_file]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=WIN_NO_WINDOW, timeout=15)
        except Exception:
            pass
        if os.path.exists(frame_file) and os.path.getsize(frame_file) > 0:
            frame_files.append(frame_file)
            
    return sorted(list(set(frame_files)))

def calibrate_frames_dataset(frame_pairs, mode="balanced", video_path=None, auto_level_only=False, init_fov=190.00, init_y_offset=0.00, init_rear_roll=0.00):
    """Execute multi-frame consensus calibration optimizer and horizon evaluator.

    Args:
        frame_pairs: List of (front_half, rear_half) image tuple pairs.
        mode: Calibration weighting mode ('foreground', 'balanced', 'infinity').
        video_path: Optional source video path for telemetry orientation fusion.
        auto_level_only: When True, skips optical seam optimization and only levels horizon.
        init_fov: Initial FOV estimate in degrees.
        init_y_offset: Initial front lens vertical shift in pixels.
        init_rear_roll: Initial rear lens roll adjustment in degrees.

    Returns:
        dict: Calibration result dictionary containing FOVs, offsets, horizon corrections,
            confidence rating, and execution status.
    """
    if not frame_pairs:
        return {"status": "error", "message": "No valid frames available for calibration."}
        
    # Standalone quick horizon/rotation leveling mode
    if auto_level_only:
        yaw_corr, pitch_corr, roll_corr = estimate_rotation_and_horizon(
            frame_pairs, fov_deg=init_fov, left_y_offset=init_y_offset, rear_roll_offset=init_rear_roll, video_path=video_path
        )
        return {
            "status": "success",
            "yaw": yaw_corr,
            "pitch": pitch_corr,
            "roll": roll_corr,
            "message": "Done"
        }

    # 1. Circle Bounds Detection across all frames
    cx1_list, cy1_list, r1_list = [], [], []
    cx2_list, cy2_list, r2_list = [], [], []
    
    for front_half, rear_half in frame_pairs:
        cx1, cy1, r1 = detect_circle_bounds(front_half)
        cx2, cy2, r2 = detect_circle_bounds(rear_half)
        cx1_list.append(cx1); cy1_list.append(cy1); r1_list.append(r1)
        cx2_list.append(cx2); cy2_list.append(cy2); r2_list.append(r2)
        
    mean_cy1 = np.mean(cy1_list)
    mean_cy2 = np.mean(cy2_list)
    mean_y_diff = mean_cy1 - mean_cy2
    init_left_y = float(np.clip(round(mean_y_diff), -10, 10))
    
    best_params = {
        "ih_fov": 191.1,
        "iv_fov": 191.1,
        "left_y_offset": int(init_left_y),
        "rear_roll_offset": 1.4
    }
    
    detector = cv2.ORB_create(nfeatures=900, fastThreshold=13)
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    
    # 2. Joint FOV Coarse Search
    fov_samples = [189.0, 189.8, 190.4, 190.8, 191.1, 191.6, 192.2, 192.8]
    fov_scores = {}
    fov_total_matches = {}
    
    for fov_val in fov_samples:
        frame_errs = []
        matches_sum = 0
        for front_half, rear_half in frame_pairs:
            score, cnt = evaluate_alignment(front_half, rear_half, fov_val, fov_val, 0, 1.0, detector, matcher, mode=mode)
            if cnt >= 4:
                frame_errs.append(score)
                matches_sum += cnt
        if frame_errs:
            fov_scores[fov_val] = float(np.mean(frame_errs))
            fov_total_matches[fov_val] = matches_sum
            
    if fov_scores:
        sorted_fovs = sorted(fov_scores.items(), key=lambda item: item[1])
        best_coarse_fov = sorted_fovs[0][0]
        
        # Fine FOV search in 0.1 deg steps
        fine_candidates = [
            round(best_coarse_fov + delta, 1) for delta in [-0.3, -0.2, -0.1, 0.0, 0.1, 0.2, 0.3]
        ]
        best_fov = best_coarse_fov
        best_fov_score = fov_scores[best_coarse_fov]
        best_cnt = fov_total_matches.get(best_coarse_fov, 0)
        
        for fine_fov in fine_candidates:
            if fine_fov not in fov_scores:
                frame_errs = []
                matches_sum = 0
                for front_half, rear_half in frame_pairs:
                    score, cnt = evaluate_alignment(front_half, rear_half, fine_fov, fine_fov, 0, 1.0, detector, matcher, mode=mode)
                    if cnt >= 4:
                        frame_errs.append(score)
                        matches_sum += cnt
                if frame_errs:
                    mean_score = float(np.mean(frame_errs))
                    if mean_score < best_fov_score:
                        best_fov_score = mean_score
                        best_fov = fine_fov
                        best_cnt = matches_sum
                        
        best_params["ih_fov"] = round(float(best_fov), 1)
        best_params["iv_fov"] = round(float(best_fov), 1)
        
        # 3. Joint Fine-tuning for Left Y-Offset & Rear Roll Offset
        opt_fov = best_params["ih_fov"]
        best_offset_score = 999.0
        
        if mode == "foreground":
            row_start, row_end = 100, 460
            ground_boost = 3.0
            pivot_y = 180.0
        elif mode == "infinity":
            row_start, row_end = 180, 480
            ground_boost = 0.0
            pivot_y = 150.0
        else:  # balanced
            row_start, row_end = 130, 470
            ground_boost = 1.2
            pivot_y = 180.0

        test_rolls = [-1.5, -1.0, -0.5, 0.0, 0.5, 0.9, 1.2, 1.4, 1.6, 1.9, 2.2]
        test_ys = [-3, -2, -1, 0, 1, 2, 3]

        # Precompute front strip features across test_ys
        front_strip_cache = {}
        for f_idx, (front_half, _) in enumerate(frame_pairs):
            for seam_center in [90.0, -90.0]:
                for ry in test_ys:
                    kp, des = extract_strip_features(
                        front_half, fov_deg=opt_fov, yaw_center_deg=seam_center,
                        y_offset=ry, roll_deg=0.0,
                        row_start=row_start, row_end=row_end, detector=detector
                    )
                    front_strip_cache[(f_idx, seam_center, ry)] = (kp, des)

        # Precompute rear strip features across test_rolls
        rear_strip_cache = {}
        for f_idx, (_, rear_half) in enumerate(frame_pairs):
            for seam_center in [90.0, -90.0]:
                rear_seam_center = -seam_center
                for r_roll in test_rolls:
                    kp, des = extract_strip_features(
                        rear_half, fov_deg=opt_fov, yaw_center_deg=rear_seam_center,
                        y_offset=0.0, roll_deg=r_roll,
                        row_start=row_start, row_end=row_end, detector=detector
                    )
                    rear_strip_cache[(f_idx, seam_center, r_roll)] = (kp, des)

        for ry in test_ys:
            for r_roll in test_rolls:
                frame_errs = []
                matches_sum = 0
                for f_idx in range(len(frame_pairs)):
                    seam_scores = []
                    seam_matches = 0
                    for seam_center in [90.0, -90.0]:
                        kp_f, des_f = front_strip_cache[(f_idx, seam_center, ry)]
                        kp_r, des_r = rear_strip_cache[(f_idx, seam_center, r_roll)]
                        med_err, match_cnt = match_seam_descriptors(
                            kp_f, des_f, kp_r, des_r, matcher, ground_boost=ground_boost, pivot_y=pivot_y
                        )
                        if med_err is not None:
                            seam_scores.append(med_err)
                            seam_matches += match_cnt
                    if seam_scores:
                        frame_errs.append(float(np.mean(seam_scores)))
                        matches_sum += seam_matches
                if frame_errs:
                    mean_score = float(np.mean(frame_errs))
                    if mean_score < best_offset_score:
                        best_offset_score = mean_score
                        best_params["left_y_offset"] = int(ry)
                        best_params["rear_roll_offset"] = round(float(r_roll), 2)
                        best_cnt = matches_sum
                        
        # 4. Micro-tune roll offset in 0.05 deg increments
        opt_roll = best_params["rear_roll_offset"]
        opt_y = best_params["left_y_offset"]
        fine_rolls = [round(opt_roll + d, 2) for d in [-0.20, -0.15, -0.10, -0.05, 0.05, 0.10, 0.15, 0.20]]
        for fine_r in fine_rolls:
            frame_errs = []
            matches_sum = 0
            for f_idx, (_, rear_half) in enumerate(frame_pairs):
                seam_scores = []
                seam_matches = 0
                for seam_center in [90.0, -90.0]:
                    rear_seam_center = -seam_center
                    if (f_idx, seam_center, fine_r) in rear_strip_cache:
                        kp_r, des_r = rear_strip_cache[(f_idx, seam_center, fine_r)]
                    else:
                        kp_r, des_r = extract_strip_features(
                            rear_half, fov_deg=opt_fov, yaw_center_deg=rear_seam_center,
                            y_offset=0.0, roll_deg=fine_r,
                            row_start=row_start, row_end=row_end, detector=detector
                        )
                    kp_f, des_f = front_strip_cache.get((f_idx, seam_center, opt_y), (None, None))
                    med_err, match_cnt = match_seam_descriptors(
                        kp_f, des_f, kp_r, des_r, matcher, ground_boost=ground_boost, pivot_y=pivot_y
                    )
                    if med_err is not None:
                        seam_scores.append(med_err)
                        seam_matches += match_cnt
                if seam_scores:
                    frame_errs.append(float(np.mean(seam_scores)))
                    matches_sum += seam_matches
            if frame_errs:
                mean_score = float(np.mean(frame_errs))
                if mean_score < best_offset_score:
                    best_offset_score = mean_score
                    best_params["rear_roll_offset"] = round(float(fine_r), 2)
                    best_cnt = matches_sum
                    
        confidence = "High" if best_cnt >= (15 * len(frame_pairs)) else "Medium"
        detail_msg = "Done"
    else:
        best_cnt = 0
        confidence = "Geometric Estimation"
        detail_msg = "Done"
        
    # 5. Evaluate Multi-Frame Stitched Horizon and Rotation Corrections
    yaw_corr, pitch_corr, roll_corr = estimate_rotation_and_horizon(
        frame_pairs,
        fov_deg=best_params["ih_fov"],
        left_y_offset=best_params["left_y_offset"],
        rear_roll_offset=best_params["rear_roll_offset"],
        video_path=video_path
    )
        
    return {
        "status": "success",
        "ih_fov": best_params["ih_fov"],
        "iv_fov": best_params["iv_fov"],
        "left_y_offset": best_params["left_y_offset"],
        "rear_roll_offset": best_params["rear_roll_offset"],
        "yaw": yaw_corr,
        "pitch": pitch_corr,
        "roll": roll_corr,
        "frames_analyzed": len(frame_pairs),
        "confidence": confidence,
        "matches": best_cnt,
        "message": detail_msg
    }

def calibrate_image(img_path, mode="balanced", auto_level_only=False, init_fov=190.00, init_y_offset=0.00, init_rear_roll=0.00):
    """Calibrate stitching parameters from a single dual-fisheye image file.

    Args:
        img_path: Filesystem path to the dual-fisheye image.
        mode: Calibration weighting mode ('foreground', 'balanced', 'infinity').
        auto_level_only: When True, performs quick horizon leveling only.
        init_fov: Initial FOV estimate in degrees.
        init_y_offset: Initial left lens vertical shift in pixels.
        init_rear_roll: Initial rear lens roll adjustment in degrees.

    Returns:
        dict: Calibration result dictionary.
    """
    if not os.path.exists(img_path):
        return {"status": "error", "message": f"Image file not found: {img_path}"}
    img = cv2.imread(img_path)
    if img is None:
        return {"status": "error", "message": f"Failed to decode image: {img_path}"}
    h, w = img.shape[:2]
    half_w = w // 2
    return calibrate_frames_dataset(
        [(img[:, :half_w], img[:, half_w:])],
        mode=mode, auto_level_only=auto_level_only,
        init_fov=init_fov, init_y_offset=init_y_offset, init_rear_roll=init_rear_roll
    )

def calibrate_video(video_path, preview_time=None, num_frames=5, mode="balanced", auto_level_only=False, init_fov=190.00, init_y_offset=0.00, init_rear_roll=0.00):
    """Extract representative frames and perform joint multi-frame auto-calibration.

    Args:
        video_path: Filesystem path to the input dual-fisheye video.
        preview_time: Optional playhead preview timestamp in seconds.
        num_frames: Number of sample frames to evaluate.
        mode: Calibration weighting mode ('foreground', 'balanced', 'infinity').
        auto_level_only: When True, performs quick horizon leveling only.
        init_fov: Initial FOV estimate in degrees.
        init_y_offset: Initial left lens vertical shift in pixels.
        init_rear_roll: Initial rear lens roll adjustment in degrees.

    Returns:
        dict: Calibration result dictionary.
    """
    if not os.path.exists(video_path):
        return {"status": "error", "message": f"Video file not found: {video_path}"}
        
    frame_files = extract_video_calibration_frames(video_path, preview_time=preview_time, num_frames=num_frames)
    if not frame_files:
        return {"status": "error", "message": "Failed to extract calibration frames from video."}
        
    frame_pairs = []
    for ff in frame_files:
        img = cv2.imread(ff)
        if img is not None:
            h, w = img.shape[:2]
            half_w = w // 2
            frame_pairs.append((img[:, :half_w], img[:, half_w:]))
            
    if not frame_pairs:
        return {"status": "error", "message": "Failed to decode extracted video frames."}
        
    return calibrate_frames_dataset(
        frame_pairs, mode=mode, video_path=video_path, auto_level_only=auto_level_only,
        init_fov=init_fov, init_y_offset=init_y_offset, init_rear_roll=init_rear_roll
    )

def main():
    """Execute command-line interface for dual-fisheye auto-calibration and leveling."""
    parser = argparse.ArgumentParser(description="Auto-calibrate dual fisheye stitching parameters and rotation corrections.")
    parser.add_argument("--image", help="Path to single input dual-fisheye image frame")
    parser.add_argument("--images", nargs="+", help="Paths to multiple input dual-fisheye images")
    parser.add_argument("--video", help="Path to input dual-fisheye video file")
    parser.add_argument("--preview_time", type=float, default=None, help="Current playhead preview time in seconds")
    parser.add_argument("--num_frames", type=int, default=5, help="Number of multi-frames to sample from video")
    parser.add_argument("--mode", default="balanced", choices=["foreground", "balanced", "infinity"], help="Calibration weighting mode")
    parser.add_argument("--auto_level", action="store_true", help="Quick horizon leveling only (Yaw, Pitch, Roll)")
    parser.add_argument("--init_fov", type=float, default=190.00, help="Initial FOV for quick horizon leveling")
    parser.add_argument("--init_left_y", type=float, default=0.00, help="Initial left lens Y offset for quick horizon leveling")
    parser.add_argument("--init_rear_roll", type=float, default=0.00, help="Initial rear roll offset for quick horizon leveling")
    parser.add_argument("--json", action="store_true", help="Output results strictly as JSON")
    args = parser.parse_args()
    
    if args.video:
        result = calibrate_video(
            args.video, preview_time=args.preview_time, num_frames=args.num_frames, mode=args.mode,
            auto_level_only=args.auto_level, init_fov=args.init_fov, init_y_offset=args.init_left_y, init_rear_roll=args.init_rear_roll
        )
    elif args.images:
        frame_pairs = []
        for img_p in args.images:
            img = cv2.imread(img_p)
            if img is not None:
                h, w = img.shape[:2]
                half_w = w // 2
                frame_pairs.append((img[:, :half_w], img[:, half_w:]))
        result = calibrate_frames_dataset(
            frame_pairs, mode=args.mode, auto_level_only=args.auto_level,
            init_fov=args.init_fov, init_y_offset=args.init_left_y, init_rear_roll=args.init_rear_roll
        )
    elif args.image:
        result = calibrate_image(
            args.image, mode=args.mode, auto_level_only=args.auto_level,
            init_fov=args.init_fov, init_y_offset=args.init_left_y, init_rear_roll=args.init_rear_roll
        )
    else:
        result = {"status": "error", "message": "Specify --video, --images, or --image"}
        
    if args.json:
        print(json.dumps(result))
    else:
        print(f"Calibration Result: {result}")

if __name__ == "__main__":
    main()
