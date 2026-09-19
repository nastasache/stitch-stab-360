import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
"""Multi-frame joint calibration optimizer for dual-fisheye 360° camera rigs.

Extracts representative frame samples across multiple source video files,
detects circular optical boundaries and centroids, warps overlapping seam
strips into equirectangular space, and optimizes field of view (FOV),
vertical Y-offset, and rear roll alignment via feature matching disparity.
"""

import argparse
import json
import glob
import subprocess
import numpy as np
import cv2

WIN_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

def get_video_duration(video_path):
    """Probe video duration in seconds via ffprobe.

    Args:
        video_path: Filepath to the video file to probe.

    Returns:
        Video duration in seconds as float, or 0.0 if probing fails.
    """
    try:
        cmd = [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", video_path
        ]
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, creationflags=WIN_NO_WINDOW).decode().strip()
        return float(out)
    except Exception:
        return 0.0

def extract_frames_from_video(video_path, num_frames, out_dir):
    """Extract representative test frames evenly distributed across a video timeline.

    Args:
        video_path: Filepath to the source video file.
        num_frames: Number of evenly spaced frames to extract.
        out_dir: Target directory where extracted JPEG frames are stored.

    Returns:
        List of tuples (timestamp_seconds, frame_image_path) for extracted frames.
    """
    os.makedirs(out_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    frame_paths = []
    
    duration = get_video_duration(video_path)
    if duration > 2.0:
        step = duration / (num_frames + 1)
        timestamps = [round(step * (i + 1), 2) for i in range(num_frames)]
    else:
        timestamps = [0.5, 1.0, 1.5]
    
    for idx, ts in enumerate(timestamps):
        ts_str = str(ts).replace('.', '_')
        out_path = os.path.join(out_dir, f"{base_name}_ts{ts_str}.jpg")
        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            cmd = [
                "ffmpeg", "-y", "-ss", str(ts), "-i", video_path,
                "-vframes", "1", "-q:v", "2", out_path
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=WIN_NO_WINDOW)
            
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            frame_paths.append((ts, out_path))
            
    return frame_paths

def detect_circle_bounds(img_half):
    """Detect circular fisheye boundary and optical centroid of a single lens half.

    Args:
        img_half: Single-lens image array (BGR or grayscale).

    Returns:
        Tuple of (cx, cy, radius) indicating the optical center and circle radius in pixels.
    """
    h, w = img_half.shape[:2]
    gray = cv2.cvtColor(img_half, cv2.COLOR_BGR2GRAY) if len(img_half.shape) == 3 else img_half
    blurred = cv2.GaussianBlur(gray, (9, 9), 2)
    _, thresh = cv2.threshold(blurred, 15, 255, cv2.THRESH_BINARY)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    if not contours:
        return w / 2.0, h / 2.0, min(w, h) / 2.0
        
    largest = max(contours, key=cv2.contourArea)
    (cx, cy), radius = cv2.minEnclosingCircle(largest)
    
    min_radius = min(w, h) * 0.35
    max_radius = min(w, h) * 0.55
    if radius < min_radius or radius > max_radius:
        radius = min(w, h) / 2.0
        cx, cy = w / 2.0, h / 2.0

    return float(cx), float(cy), float(radius)

def fisheye_to_equirect_strip(img_half, fov_deg, yaw_center_deg, yaw_span_deg=35.0, out_w=300, out_h=600, y_offset=0.0, roll_deg=0.0):
    """Warp a specific longitude strip of a fisheye half into equirectangular projection.

    Args:
        img_half: Input fisheye lens image half.
        fov_deg: Assumed camera lens field of view in degrees.
        yaw_center_deg: Center longitude/yaw of the target strip in degrees.
        yaw_span_deg: Angular width of the longitude strip in degrees.
        out_w: Width of output remapped strip in pixels.
        out_h: Height of output remapped strip in pixels.
        y_offset: Vertical center shift adjustment in pixels.
        roll_deg: Angular roll rotation adjustment in degrees.

    Returns:
        Remapped equirectangular strip image as a numpy array.
    """
    h_in, w_in = img_half.shape[:2]
    cx_in = w_in / 2.0
    cy_in = h_in / 2.0 + y_offset
    r_max = min(w_in, h_in) / 2.0
    
    u = np.linspace(-yaw_span_deg / 2.0, yaw_span_deg / 2.0, out_w, dtype=np.float32) + yaw_center_deg
    v = np.linspace(-90.0, 90.0, out_h, dtype=np.float32)
    u_grid, v_grid = np.meshgrid(u, v)
    
    lon_rad = np.radians(u_grid)
    lat_rad = np.radians(v_grid)
    
    x = np.cos(lat_rad) * np.sin(lon_rad)
    y = np.sin(lat_rad)
    z = np.cos(lat_rad) * np.cos(lon_rad)
    
    if roll_deg != 0.0:
        roll_rad = np.radians(-roll_deg)
        cos_r = np.cos(roll_rad)
        sin_r = np.sin(roll_rad)
        x_rot = cos_r * x - sin_r * y
        y_rot = sin_r * x + cos_r * y
        x, y = x_rot, y_rot
    
    theta = np.arccos(np.clip(z, -1.0, 1.0))
    phi = np.arctan2(y, x)
    
    max_theta = np.radians(fov_deg / 2.0)
    r = (theta / max_theta) * r_max
    
    map_x = (cx_in + r * np.cos(phi)).astype(np.float32)
    map_y = (cy_in + r * np.sin(phi)).astype(np.float32)
    
    return cv2.remap(img_half, map_x, map_y, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))

def compute_frame_disparity(front_half, rear_half, fov_deg, y_offset, roll_deg, detector, matcher):
    """Measure feature disparity error between front and rear lenses at seam boundaries.

    Extracts overlapping longitude strips at +/-90 degrees azimuth, detects ORB
    keypoints, matches descriptors between front and rear projections, and computes
    median Euclidean distance error.

    Args:
        front_half: Front lens image array.
        rear_half: Rear lens image array.
        fov_deg: Candidate field of view in degrees.
        y_offset: Candidate vertical offset in pixels.
        roll_deg: Candidate rear roll rotation in degrees.
        detector: OpenCV 2D feature detector instance (e.g., ORB).
        matcher: OpenCV descriptor matcher instance (e.g., BFMatcher).

    Returns:
        Tuple of (mean_disparity_error_px, total_match_count). Returns (999.0, 0)
        if insufficient matches are detected.
    """
    row_start, row_end = 120, 480
    errors = []
    match_count = 0
    
    for seam_center in [90.0, -90.0]:
        strip_f = fisheye_to_equirect_strip(
            front_half, fov_deg=fov_deg, yaw_center_deg=seam_center,
            yaw_span_deg=30.0, out_w=300, out_h=600, y_offset=y_offset, roll_deg=0.0
        )
        strip_r = fisheye_to_equirect_strip(
            rear_half, fov_deg=fov_deg, yaw_center_deg=-seam_center,
            yaw_span_deg=30.0, out_w=300, out_h=600, y_offset=0.0, roll_deg=roll_deg
        )
        
        gray_f = cv2.cvtColor(strip_f[row_start:row_end, :], cv2.COLOR_BGR2GRAY)
        gray_r = cv2.cvtColor(strip_r[row_start:row_end, :], cv2.COLOR_BGR2GRAY)
        
        kp_f, des_f = detector.detectAndCompute(gray_f, None)
        kp_r, des_r = detector.detectAndCompute(gray_r, None)
        
        if des_f is None or des_r is None or len(kp_f) < 4 or len(kp_r) < 4:
            continue
            
        matches = matcher.match(des_f, des_r)
        if len(matches) < 4:
            continue
            
        matches = sorted(matches, key=lambda m: m.distance)[:min(len(matches), 50)]
        pts_f = np.array([kp_f[m.queryIdx].pt for m in matches])
        pts_r = np.array([kp_r[m.trainIdx].pt for m in matches])
        
        diff = np.linalg.norm(pts_f - pts_r, axis=1)
        errors.append(float(np.median(diff)))
        match_count += len(matches)
        
    if not errors:
        return 999.0, 0
    return float(np.mean(errors)), match_count

def run_global_calibration(video_files, num_frames_per_video=10):
    """Run multi-video global joint calibration to determine optimal stitching geometry.

    Performs 4-stage optimization:
      1. Timeline frame extraction across source videos.
      2. Circle boundary and optical centroid detection.
      3. Coarse and fine grid search for optimal lens FOV.
      4. Joint grid search and micro-tuning for vertical Y-offset and rear roll.

    Args:
        video_files: Sequence of video file paths for dataset sampling.
        num_frames_per_video: Number of frames to extract per video file.

    Returns:
        Dictionary containing optimal calibration parameters ('ih_fov', 'iv_fov',
        'left_y_offset', 'rear_roll_offset', 'front_center', 'rear_center', etc.),
        or None if no valid frames were extracted.
    """
    out_dir = os.path.join("data", "runtime", "work", "calib_dataset")
    os.makedirs(out_dir, exist_ok=True)
    
    extracted_frames = []
    print(f"[1/4] Extracting up to {num_frames_per_video} frames from each of {len(video_files)} video(s)...")
    for v in video_files:
        if not os.path.exists(v):
            print(f"  [!] Video not found: {v}")
            continue
        frames = extract_frames_from_video(v, num_frames_per_video, out_dir)
        print(f"  -> Extracted {len(frames)} frame(s) from {os.path.basename(v)}")
        for ts, fpath in frames:
            img = cv2.imread(fpath)
            if img is not None:
                h, w = img.shape[:2]
                half_w = w // 2
                extracted_frames.append({
                    "video": os.path.basename(v),
                    "timestamp": ts,
                    "path": fpath,
                    "front": img[:, :half_w],
                    "rear": img[:, half_w:]
                })
                
    if not extracted_frames:
        print("[!] No frames could be extracted or decoded.")
        return None
        
    print(f"\n[2/4] Measuring optical circle boundaries across all {len(extracted_frames)} frames...")
    cx1_list, cy1_list, r1_list = [], [], []
    cx2_list, cy2_list, r2_list = [], [], []
    
    for f in extracted_frames:
        cx1, cy1, r1 = detect_circle_bounds(f["front"])
        cx2, cy2, r2 = detect_circle_bounds(f["rear"])
        cx1_list.append(cx1); cy1_list.append(cy1); r1_list.append(r1)
        cx2_list.append(cx2); cy2_list.append(cy2); r2_list.append(r2)
        
    mean_cx1, mean_cy1, mean_r1 = float(np.mean(cx1_list)), float(np.mean(cy1_list)), float(np.mean(r1_list))
    mean_cx2, mean_cy2, mean_r2 = float(np.mean(cx2_list)), float(np.mean(cy2_list)), float(np.mean(r2_list))
    mean_y_diff = mean_cy1 - mean_cy2
    
    print(f"  Front Optical Center: ({mean_cx1:.1f}, {mean_cy1:.1f}), Radius: {mean_r1:.1f}px")
    print(f"  Rear Optical Center:  ({mean_cx2:.1f}, {mean_cy2:.1f}), Radius: {mean_r2:.1f}px")
    print(f"  Geometric Y-Center Offset: {mean_y_diff:+.2f}px")
    
    detector = cv2.ORB_create(nfeatures=900, fastThreshold=13)
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    
    print(f"\n[3/4] Optimizing Joint Multi-Frame FOV on {len(extracted_frames)} frames...")
    coarse_fov_range = np.arange(188.5, 193.5, 0.4)
    fov_joint_scores = {}
    
    for fov_val in coarse_fov_range:
        fov_val = round(float(fov_val), 2)
        frame_errors = []
        total_m = 0
        for f in extracted_frames:
            err, m = compute_frame_disparity(f["front"], f["rear"], fov_val, 0.0, 1.0, detector, matcher)
            if m >= 4:
                frame_errors.append(err)
                total_m += m
        if frame_errors:
            fov_joint_scores[fov_val] = np.mean(frame_errors)
            
    best_coarse_fov = min(fov_joint_scores, key=fov_joint_scores.get)
    print(f"  Coarse best FOV: {best_coarse_fov:.2f} deg (Mean Disparity: {fov_joint_scores[best_coarse_fov]:.2f}px)")
    
    # Fine FOV search in 0.05 increments
    fine_fov_range = np.arange(best_coarse_fov - 0.5, best_coarse_fov + 0.55, 0.05)
    fine_fov_scores = {}
    for fov_val in fine_fov_range:
        fov_val = round(float(fov_val), 2)
        frame_errors = []
        for f in extracted_frames:
            err, m = compute_frame_disparity(f["front"], f["rear"], fov_val, 0.0, 1.0, detector, matcher)
            if m >= 4:
                frame_errors.append(err)
        if frame_errors:
            fine_fov_scores[fov_val] = np.mean(frame_errors)
            
    global_opt_fov = min(fine_fov_scores, key=fine_fov_scores.get)
    print(f"  Fine Global Best FOV: {global_opt_fov:.2f} deg (Disparity: {fine_fov_scores[global_opt_fov]:.2f}px)")
    
    print(f"\n[4/4] Jointly Optimizing Left Y-Offset & Rear Roll Offset across {len(extracted_frames)} frames...")
    test_ys = [-3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0]
    test_rolls = np.arange(-1.0, 2.5, 0.25)
    
    offset_grid = {}
    for y_off in test_ys:
        for r_off in test_rolls:
            r_off = round(float(r_off), 2)
            frame_errors = []
            for f in extracted_frames:
                err, m = compute_frame_disparity(f["front"], f["rear"], global_opt_fov, y_off, r_off, detector, matcher)
                if m >= 4:
                    frame_errors.append(err)
            if frame_errors:
                offset_grid[(y_off, r_off)] = np.mean(frame_errors)
                
    best_y, best_roll = min(offset_grid, key=offset_grid.get)
    
    # Micro-tune roll offset in 0.02 deg steps
    micro_rolls = np.arange(best_roll - 0.20, best_roll + 0.22, 0.02)
    micro_scores = {}
    for mr in micro_rolls:
        mr = round(float(mr), 2)
        frame_errors = []
        for f in extracted_frames:
            err, m = compute_frame_disparity(f["front"], f["rear"], global_opt_fov, best_y, mr, detector, matcher)
            if m >= 4:
                frame_errors.append(err)
        if frame_errors:
            micro_scores[mr] = np.mean(frame_errors)
            
    global_opt_roll = min(micro_scores, key=micro_scores.get)
    global_opt_y = int(round(best_y))
    
    print("\n========================================================")
    print("  STATISTICAL GLOBAL CAMERA CALIBRATION RESULTS")
    print("========================================================")
    print(f"  Dataset: {len(extracted_frames)} frames across {len(video_files)} video file(s)")
    print(f"  ih_fov (Horizontal FOV):   {global_opt_fov:.2f} deg  (Standard: {round(global_opt_fov, 1)})")
    print(f"  iv_fov (Vertical FOV):     {global_opt_fov:.2f} deg  (Standard: {round(global_opt_fov, 1)})")
    print(f"  left_y_offset:             {global_opt_y} px")
    print(f"  rear_roll_offset:          {global_opt_roll:+.2f} deg")
    print(f"  Global Seam Disparity:     {micro_scores[global_opt_roll]:.2f} px")
    print("========================================================\n")
    
    # Per-video breakdown
    print("--- Per-Video Fit Analysis ---")
    for v in video_files:
        v_name = os.path.basename(v)
        v_frames = [f for f in extracted_frames if f["video"] == v_name]
        v_errs = []
        for vf in v_frames:
            e, _ = compute_frame_disparity(vf["front"], vf["rear"], global_opt_fov, global_opt_y, global_opt_roll, detector, matcher)
            v_errs.append(e)
        mean_v_err = np.mean(v_errs) if v_errs else 0.0
        print(f"  * {v_name:20s}: {len(v_frames):2d} frames -> Disparity: {mean_v_err:.2f}px")
        
    res_dict = {
        "ih_fov": round(float(global_opt_fov), 1),
        "iv_fov": round(float(global_opt_fov), 1),
        "ih_fov_precise": float(global_opt_fov),
        "iv_fov_precise": float(global_opt_fov),
        "left_y_offset": int(global_opt_y),
        "rear_roll_offset": round(float(global_opt_roll), 2),
        "front_center": [mean_cx1, mean_cy1],
        "rear_center": [mean_cx2, mean_cy2],
        "front_radius": mean_r1,
        "rear_radius": mean_r2,
        "dataset_videos": len(video_files),
        "dataset_frames": len(extracted_frames),
        "mean_disparity_px": round(float(micro_scores[global_opt_roll]), 2)
    }
    
    out_json = os.path.join("others", "camera_hardware_calibration.json")
    os.makedirs("others", exist_ok=True)
    with open(out_json, "w", encoding="utf-8", newline="\n") as f:
        json.dump(res_dict, f, indent=4)
    print(f"\n[+] Master calibration saved to: {out_json}")
    
    return res_dict

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Multi-frame global joint camera calibration.")
    parser.add_argument("--videos", nargs="+", help="List of video files to include")
    parser.add_argument("--folder", default="360_originals", help="Folder to search for 360 videos recursively")
    parser.add_argument("--num_frames", type=int, default=10, help="Number of frames to extract per video")
    args = parser.parse_args()
    
    if args.videos:
        video_files = args.videos
    else:
        patterns = [os.path.join(args.folder, "**", "*.mp4"), os.path.join(args.folder, "**", "*.MP4")]
        video_files = []
        for p in patterns:
            video_files.extend(glob.glob(p, recursive=True))
        video_files = sorted(list(set(video_files)))
        
    print(f"Found {len(video_files)} video file(s) for calibration dataset.")
    run_global_calibration(video_files, num_frames_per_video=args.num_frames)
