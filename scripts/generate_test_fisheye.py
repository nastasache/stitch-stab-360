#!/usr/bin/env python3
import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
"""Synthetic raw dual-fisheye 360° video and telemetry test pattern generator.

Synthesizes side-by-side (SBS 3840x1920 @ 30fps) dual-fisheye video frames from a
high-resolution spherical environment map, introducing controlled camera wobble,
optical FOV mismatch, optical center shifts, and rear-lens mechanical misalignment
while generating synchronized 9-column WitMotion text telemetry and embedded MP4
'vrot' metadata atoms for stabilization benchmark verification.
"""

import math
import argparse
import subprocess
import time
import struct
import numpy as np

try:
    import cv2
    HAS_OPENCV = True
except ImportError:
    HAS_OPENCV = False


def draw_seamless_circle(img, center, radius, color, thickness=-1):
    """Draw a circle on an equirectangular image wrapping across horizontal 360° boundaries.

    Args:
        img: Equirectangular canvas image array.
        center: Circle center coordinate tuple (cx, cy) in pixels.
        radius: Circle radius in pixels.
        color: BGR color tuple.
        thickness: Line thickness in pixels (-1 for filled circle).
    """
    cx, cy = center
    w = img.shape[1]
    for offset_x in (0, -w, w):
        x = cx + offset_x
        if -radius <= x <= w + radius:
            cv2.circle(img, (x, cy), radius, color, thickness)


def draw_seamless_line(img, pt1, pt2, color, thickness=1):
    """Draw a line on an equirectangular image wrapping across horizontal 360° boundaries.

    Args:
        img: Equirectangular canvas image array.
        pt1: Start coordinate tuple (x1, y1) in pixels.
        pt2: End coordinate tuple (x2, y2) in pixels.
        color: BGR color tuple.
        thickness: Line thickness in pixels.
    """
    x1, y1 = pt1
    x2, y2 = pt2
    w = img.shape[1]
    for offset_x in (0, -w, w):
        cv2.line(img, (x1 + offset_x, y1), (x2 + offset_x, y2), color, thickness)


def draw_seamless_text(img, text, center_x, baseline_y, font, scale, color, thickness, outline_color=None, outline_thickness=None):
    """Draw centered text on an equirectangular image wrapping across 360° boundaries.

    Args:
        img: Equirectangular canvas image array.
        text: Text string to render.
        center_x: Horizontal center position in pixels.
        baseline_y: Vertical baseline position in pixels.
        font: OpenCV font type identifier.
        scale: Font scale factor.
        color: Primary text BGR color tuple.
        thickness: Font line thickness in pixels.
        outline_color: Optional outline BGR color tuple for contrast.
        outline_thickness: Optional outline line thickness in pixels.
    """
    (tw, th), _ = cv2.getTextSize(text, font, scale, thickness)
    tx = center_x - tw // 2
    w = img.shape[1]
    for offset_x in (0, -w, w):
        curr_tx = tx + offset_x
        if curr_tx + tw >= 0 and curr_tx < w:
            if outline_color is not None and outline_thickness is not None:
                cv2.putText(img, text, (curr_tx, baseline_y), font, scale, outline_color, outline_thickness, cv2.LINE_AA)
            cv2.putText(img, text, (curr_tx, baseline_y), font, scale, color, thickness, cv2.LINE_AA)


def create_spherical_environment(width=4096, height=2048):
    """Render a high-resolution 360° equirectangular base environment map.

    Generates sky gradient with procedural clouds, ground textures (pavers,
    checkerboards, cobblestones), latitude/longitude grid lines, horizon line,
    cardinal direction labels (N, S, E, W), and visual landmarks.

    Args:
        width: Output environment width in pixels.
        height: Output environment height in pixels.

    Returns:
        Equirectangular environment image as a uint8 BGR numpy array.
    """
    env = np.zeros((height, width, 3), dtype=np.uint8)

    lat_arr = np.linspace(math.pi / 2, -math.pi / 2, height, endpoint=False)
    lon_arr = np.linspace(-math.pi, math.pi, width, endpoint=False)
    Lon, Lat = np.meshgrid(lon_arr, lat_arr)

    # 1. Sky Gradient (Lat > 0) with Soft Azure Blue and Clouds
    sky_mask = Lat >= 0
    sky_factor = np.clip(Lat / (math.pi / 2), 0.0, 1.0)
    # Deep rich azure sky blue at zenith (B=235, G=130, R=25) to soft cyan horizon haze (B=245, G=215, R=165)
    sky_b_f = (245.0 + (235.0 - 245.0) * sky_factor).astype(np.float32)
    sky_g_f = (215.0 + (130.0 - 215.0) * sky_factor).astype(np.float32)
    sky_r_f = (165.0 + (25.0 - 165.0) * sky_factor).astype(np.float32)

    # Procedural cumulus cloud layer across sky
    cloud_noise = (
        0.45 * np.sin(Lon * 5.0 + 0.5 * np.cos(Lat * 4.0)) +
        0.30 * np.sin(Lon * 10.0 - Lat * 7.0 + 1.2) +
        0.15 * np.cos(Lon * 18.0 + Lat * 11.0) +
        0.10 * np.sin(Lon * 32.0 - Lat * 20.0)
    )
    cloud_elevation_envelope = np.clip(np.sin(Lat * 2.0), 0.0, 1.0)
    cloud_density = np.clip((cloud_noise - 0.08) * 2.8, 0.0, 1.0) * cloud_elevation_envelope

    c_b = (245.0 + cloud_noise * 10.0).clip(0, 255)
    c_g = (250.0 + cloud_noise * 5.0).clip(0, 255)
    c_r = 255.0

    sky_b = (sky_b_f * (1.0 - cloud_density * 0.88) + c_b * (cloud_density * 0.88)).clip(0, 255).astype(np.uint8)
    sky_g = (sky_g_f * (1.0 - cloud_density * 0.88) + c_g * (cloud_density * 0.88)).clip(0, 255).astype(np.uint8)
    sky_r = (sky_r_f * (1.0 - cloud_density * 0.88) + c_r * (cloud_density * 0.88)).clip(0, 255).astype(np.uint8)

    # 2. Ground / Earth Pattern (Lat < 0) with Multi-Scale High-Texture Geometry
    ground_factor = np.clip(-Lat / (math.pi / 2), 0.0, 1.0)
    deg_lon = (Lon * 180.0 / math.pi)
    deg_lat = (-Lat * 180.0 / math.pi)

    # Coarse 20-deg blocks
    checker_coarse = (((deg_lon % 20.0) < 10.0) ^ ((deg_lat % 20.0) < 10.0)).astype(np.uint8)

    # Fine 2.5-deg paver stones
    paver_fine = (((deg_lon % 2.5) < 1.25) ^ ((deg_lat % 2.5) < 1.25)).astype(np.uint8)

    # Micro 0.8-deg cobblestone texture
    cobble_micro = (((deg_lon % 0.8) < 0.4) ^ ((deg_lat % 0.8) < 0.4)).astype(np.uint8)

    # High-contrast stone grain
    ground_grain = (
        0.35 * np.sin(Lon * 120.0 + 0.5 * np.cos(Lat * 90.0)) +
        0.25 * np.sin(Lon * 240.0 - Lat * 180.0) +
        0.20 * np.cos(Lon * 480.0 + Lat * 360.0)
    )
    grain_factor = np.clip((ground_grain + 0.8) * 0.6, 0.0, 1.0)

    g_b = (25 + checker_coarse * 18 + paver_fine * 28 + cobble_micro * 18 + ground_factor * 12).astype(np.float32) * grain_factor
    g_g = (65 + checker_coarse * 30 + paver_fine * 42 + cobble_micro * 24 + ground_factor * 18).astype(np.float32) * grain_factor
    g_r = (35 + checker_coarse * 16 + paver_fine * 25 + cobble_micro * 15 + ground_factor * 8).astype(np.float32) * grain_factor

    env[sky_mask, 0] = sky_b[sky_mask]
    env[sky_mask, 1] = sky_g[sky_mask]
    env[sky_mask, 2] = sky_r[sky_mask]

    env[~sky_mask, 0] = g_b[~sky_mask].clip(0, 255).astype(np.uint8)
    env[~sky_mask, 1] = g_g[~sky_mask].clip(0, 255).astype(np.uint8)
    env[~sky_mask, 2] = g_r[~sky_mask].clip(0, 255).astype(np.uint8)

    # 3. Lat / Long Grid lines (Every 15 degrees = pi/12)
    grid_lat = np.abs(Lat % (math.pi / 12)) < (1.5 * math.pi / height)
    grid_lon = np.abs(Lon % (math.pi / 12)) < (1.5 * 2 * math.pi / width)
    grid = grid_lat | grid_lon
    env[grid] = [220, 220, 220]

    # 4. Horizon Highlight Line (Lat = 0)
    horizon_mask = np.abs(Lat) < (3.0 * math.pi / height)
    env[horizon_mask] = [0, 69, 255]  # Bright Orange/Red in BGR

    # Draw Landmarks, Architectural Features and High-visibility Markers
    if HAS_OPENCV:
        # Periodic subtle architectural pillars across the horizon (away from primary seam lines)
        azimuth_deg_list = [-150, -120, -60, -30, 30, 60, 120, 150]
        for az in azimuth_deg_list:
            az_rad = math.radians(az)
            px = int(((az_rad + math.pi) / (2 * math.pi)) * width) % width
            draw_seamless_line(env, (px, int(height * 0.44)), (px, int(height * 0.60)), (130, 130, 140), 6)
            draw_seamless_line(env, (px - 8, int(height * 0.46)), (px - 8, int(height * 0.58)), (80, 80, 90), 2)
            draw_seamless_line(env, (px + 8, int(height * 0.46)), (px + 8, int(height * 0.58)), (80, 80, 90), 2)

        landmarks = [
            ("FRONT [0 deg]", 0.0, 0.15, (0, 0, 255), (0, 0, 200)),
            ("RIGHT [+90 deg]", math.pi / 2, 0.15, (0, 255, 255), (0, 200, 200)),
            ("BACK [180 deg]", math.pi, 0.15, (255, 100, 0), (200, 80, 0)),
            ("LEFT [-90 deg]", -math.pi / 2, 0.15, (0, 255, 0), (0, 200, 0)),
            ("ZENITH (+90 deg)", 0.0, math.pi / 2 - 0.25, (255, 255, 255), (180, 180, 180)),
            ("NADIR (-90 deg)", 0.0, -math.pi / 2 + 0.25, (200, 200, 200), (120, 120, 120))
        ]

        for text, lm_lon, lm_lat, col, bar_col in landmarks:
            px = int(((lm_lon + math.pi) / (2 * math.pi)) * width) % width
            py = int(((math.pi / 2 - lm_lat) / math.pi) * height) % height

            draw_seamless_line(env, (px, max(0, py - 180)), (px, min(height - 1, py + 180)), bar_col, 8)
            draw_seamless_circle(env, (px, py), 22, col, -1)
            draw_seamless_circle(env, (px, py), 26, (255, 255, 255), 3)

            font = cv2.FONT_HERSHEY_DUPLEX
            t_scale = 1.4
            thickness = 3
            draw_seamless_text(env, text, px, py - 40, font, t_scale, col, thickness, (0, 0, 0), thickness + 4)

    return env


def euler_to_rotation_matrix(yaw_deg, pitch_deg, roll_deg):
    """Compute 3D rotation matrix for Camera-to-World coordinates from Euler angles.

    Follows Ry(yaw) @ Rx(pitch) @ Rz(roll) convention.

    Args:
        yaw_deg: Yaw rotation in degrees.
        pitch_deg: Pitch rotation in degrees.
        roll_deg: Roll rotation in degrees.

    Returns:
        3x3 rotation matrix as a float32 numpy array.
    """
    y = math.radians(yaw_deg)
    p = math.radians(pitch_deg)
    r = math.radians(roll_deg)

    Ry = np.array([
        [math.cos(y), 0, math.sin(y)],
        [0, 1, 0],
        [-math.sin(y), 0, math.cos(y)]
    ], dtype=np.float32)

    Rx = np.array([
        [1, 0, 0],
        [0, math.cos(p), -math.sin(p)],
        [0, math.sin(p), math.cos(p)]
    ], dtype=np.float32)

    Rz = np.array([
        [math.cos(r), -math.sin(r), 0],
        [math.sin(r), math.cos(r), 0],
        [0, 0, 1]
    ], dtype=np.float32)

    return Ry @ Rx @ Rz


def build_lens_ray_grid_with_defects(
    lens_dim=1920,
    ih_fov=190.8,
    iv_fov=190.8,
    left_y_offset=2,
    rear_roll_offset=1.2,
    rear_pitch_offset=0.0,
    rear_yaw_offset=0.0
):
    """Precompute 3D unit ray vectors for Front and Rear lenses with simulated defects.

    Args:
        lens_dim: Fisheye circle dimension (width and height) in pixels.
        ih_fov: Horizontal field of view in degrees.
        iv_fov: Vertical field of view in degrees.
        left_y_offset: Vertical center shift of front lens in pixels.
        rear_roll_offset: Independent roll rotation offset of rear lens in degrees.
        rear_pitch_offset: Independent pitch rotation offset of rear lens in degrees.
        rear_yaw_offset: Independent yaw rotation offset of rear lens in degrees.

    Returns:
        Tuple of (front_data, rear_data), each containing (y_indices, x_indices, valid_rays, vignette_weights).
    """
    r_circle = (lens_dim / 2.0)

    theta_h_max = math.radians(ih_fov / 2.0)
    theta_v_max = math.radians(iv_fov / 2.0)
    fx = r_circle / theta_h_max
    fy = r_circle / theta_v_max

    y_idx, x_idx = np.indices((lens_dim, lens_dim), dtype=np.float32)

    # --- FRONT LENS (Left Frame) ---
    cx_front = lens_dim / 2.0 - 0.5
    cy_front = (lens_dim / 2.0 - 0.5) + float(left_y_offset)

    dx_f = x_idx - cx_front
    dy_f = y_idx - cy_front
    r_f = np.sqrt(dx_f * dx_f + dy_f * dy_f)

    valid_f = r_f <= r_circle
    th_x_f = dx_f / fx
    th_y_f = dy_f / fy
    theta_f = np.sqrt(th_x_f * th_x_f + th_y_f * th_y_f)
    phi_f = np.arctan2(th_y_f, th_x_f)

    sin_th_f = np.sin(theta_f)
    cos_th_f = np.cos(theta_f)
    vx_f = sin_th_f * np.cos(phi_f)
    vy_f = sin_th_f * np.sin(phi_f)
    vz_f = cos_th_f
    rays_front = np.stack([vx_f, vy_f, vz_f], axis=-1)

    vignette_f = np.ones_like(r_f, dtype=np.float32)
    fade_start = r_circle * 0.985
    fade_f = (r_f > fade_start) & (r_f <= r_circle)
    vignette_f[fade_f] = 0.5 * (1.0 + np.cos(math.pi * (r_f[fade_f] - fade_start) / (r_circle - fade_start)))
    vignette_f[r_f > r_circle] = 0.0

    # --- REAR LENS (Right Frame) ---
    cx_rear = lens_dim / 2.0 - 0.5
    cy_rear = lens_dim / 2.0 - 0.5

    dx_r = x_idx - cx_rear
    dy_r = y_idx - cy_rear
    r_r = np.sqrt(dx_r * dx_r + dy_r * dy_r)

    valid_r = r_r <= r_circle
    th_x_r = dx_r / fx
    th_y_r = dy_r / fy
    theta_r = np.sqrt(th_x_r * th_x_r + th_y_r * th_y_r)
    phi_r = np.arctan2(th_y_r, th_x_r)

    sin_th_r = np.sin(theta_r)
    cos_th_r = np.cos(theta_r)
    vx_r = -sin_th_r * np.cos(phi_r)
    vy_r = sin_th_r * np.sin(phi_r)
    vz_r = -cos_th_r
    rays_rear_base = np.stack([vx_r, vy_r, vz_r], axis=-1)

    # Apply rear lens independent mechanical offset rotation
    R_rear_offset = euler_to_rotation_matrix(rear_yaw_offset, rear_pitch_offset, rear_roll_offset)
    rays_rear = np.dot(rays_rear_base, R_rear_offset.T)

    vignette_r = np.ones_like(r_r, dtype=np.float32)
    fade_r = (r_r > fade_start) & (r_r <= r_circle)
    vignette_r[fade_r] = 0.5 * (1.0 + np.cos(math.pi * (r_r[fade_r] - fade_start) / (r_circle - fade_start)))
    vignette_r[r_r > r_circle] = 0.0

    # Extract valid pixels
    y_f_idx, x_f_idx = np.where(valid_f)
    rays_f_valid = rays_front[valid_f]
    vig_f_valid = vignette_f[valid_f, np.newaxis]

    y_r_idx, x_r_idx = np.where(valid_r)
    rays_r_valid = rays_rear[valid_r]
    vig_r_valid = vignette_r[valid_r, np.newaxis]

    front_data = (y_f_idx, x_f_idx, rays_f_valid, vig_f_valid)
    rear_data = (y_r_idx, x_r_idx, rays_r_valid, vig_r_valid)

    return front_data, rear_data


def generate_unstabilized_defective_video(
    output_path="test_fisheye.mp4",
    duration=3.0,
    fps=30,
    width=3840,
    height=1920,
    ih_fov=190.8,
    iv_fov=190.8,
    left_y_offset=2,
    rear_roll_offset=1.2,
    rear_pitch_offset=0.0,
    rear_yaw_offset=0.0,
    yaw_defect=-2.0,
    pitch_defect=-1.2,
    roll_defect=0.6,
    crf=32,
    preset="medium"
):
    """Render a synthetic dual-fisheye video sequence with dynamic wobble and geometric defects.

    Simulates high-frequency camera shake, projects rays into the spherical
    environment map, applies circular aperture masking and vignetting, writes
    video via FFmpeg pipe, and serializes synchronized telemetry.

    Args:
        output_path: Destination path for rendered MP4 video.
        duration: Video duration in seconds.
        fps: Playback frame rate.
        width: Full side-by-side frame width in pixels.
        height: Frame height in pixels.
        ih_fov: Simulated horizontal FOV defect in degrees.
        iv_fov: Simulated vertical FOV defect in degrees.
        left_y_offset: Simulated left lens vertical shift in pixels.
        rear_roll_offset: Rear lens roll rotation offset in degrees.
        rear_pitch_offset: Rear lens pitch rotation offset in degrees.
        rear_yaw_offset: Rear lens yaw rotation offset in degrees.
        yaw_defect: Static baseline yaw error in degrees.
        pitch_defect: Static baseline pitch error in degrees.
        roll_defect: Static baseline roll error in degrees.
        crf: Constant Rate Factor for x264 video compression (default 32).
        preset: FFmpeg x264 compression speed preset (default "medium").

    Raises:
        RuntimeError: If OpenCV is not installed.
    """
    total_frames = int(duration * fps)
    lens_dim = height
    half_w = width // 2

    start_time = time.time()
    print(f"[INFO] Rendering {width}x{height} @ {fps}fps ({duration}s, {total_frames} frames)...", flush=True)

    env_w, env_h = 4096, 2048
    base_env = create_spherical_environment(env_w, env_h)

    front_data, rear_data = build_lens_ray_grid_with_defects(
        lens_dim=lens_dim,
        ih_fov=ih_fov,
        iv_fov=iv_fov,
        left_y_offset=left_y_offset,
        rear_roll_offset=rear_roll_offset,
        rear_pitch_offset=rear_pitch_offset,
        rear_yaw_offset=rear_yaw_offset
    )
    y_f, x_f, rays_f, vig_f = front_data
    y_r, x_r, rays_r, vig_r = rear_data

    R_static_defect = euler_to_rotation_matrix(yaw_defect, pitch_defect, roll_defect)

    use_ffmpeg = True
    ffmpeg_proc = None
    writer = None

    try:
        from utils.tool_resolver import resolve_ffmpeg
        ffmpeg_bin = resolve_ffmpeg()["path"]
    except Exception:
        ffmpeg_bin = "ffmpeg"

    try:
        ffmpeg_cmd = [
            ffmpeg_bin, "-y", "-loglevel", "error",
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-s", f"{width}x{height}",
            "-pix_fmt", "bgr24",
            "-r", str(fps),
            "-i", "-",
            "-f", "lavfi",
            "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "128k",
            "-shortest",
            "-preset", str(preset),
            "-crf", str(crf),
            output_path
        ]
        ffmpeg_proc = subprocess.Popen(
            ffmpeg_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except Exception as e:
        print(f"[WARN] FFmpeg pipe unavailable: {e}. Using OpenCV VideoWriter.", flush=True)
        use_ffmpeg = False
        if HAS_OPENCV:
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            writer = cv2.VideoWriter(output_path, fourcc, float(fps), (width, height))
        else:
            raise RuntimeError("Neither FFmpeg nor OpenCV is available for video encoding.")

    inv_two_pi = env_w / (2.0 * math.pi)
    inv_pi = env_h / math.pi
    telemetry_data = []

    for frame_idx in range(total_frames):
        t = frame_idx / float(fps)

        # Dynamic Unstabilized Motion Shake (Realistic Handheld / Stride Oscillation)
        dyn_pitch = 4.5 * math.sin(2.0 * math.pi * 1.5 * t) + 1.8 * math.cos(2.0 * math.pi * 3.1 * t)
        dyn_roll = 5.8 * math.sin(2.0 * math.pi * 1.2 * t + 0.4) + 2.2 * math.sin(2.0 * math.pi * 2.7 * t)
        dyn_yaw = 3.6 * math.sin(2.0 * math.pi * 0.8 * t) + 1.4 * math.cos(2.0 * math.pi * 1.9 * t)

        telemetry_data.append((t, dyn_roll, dyn_pitch, dyn_yaw))

        R_dynamic = euler_to_rotation_matrix(dyn_yaw, dyn_pitch, dyn_roll)
        R_total = R_dynamic @ R_static_defect

        current_env = base_env.copy()
        if HAS_OPENCV:
            orb_lon = (2.0 * math.pi * t / duration) - math.pi
            orb_lat = 0.15 * math.sin(4.0 * math.pi * t)
            ox = int(((orb_lon + math.pi) / (2 * math.pi)) * env_w) % env_w
            oy = int(((math.pi / 2 - orb_lat) / math.pi) * env_h) % env_h
            draw_seamless_circle(current_env, (ox, oy), 35, (0, 165, 255), -1)
            draw_seamless_circle(current_env, (ox, oy), 42, (255, 255, 255), 4)

        # 1. Front Lens sampling
        w_rays_f = np.dot(rays_f, R_total.T)
        lat_f = np.arcsin(np.clip(-w_rays_f[:, 1], -1.0, 1.0))
        lon_f = np.arctan2(w_rays_f[:, 0], w_rays_f[:, 2])
        u_f = ((lon_f + math.pi) * inv_two_pi).astype(np.int32) % env_w
        v_f = ((math.pi / 2.0 - lat_f) * inv_pi).astype(np.int32) % env_h

        sampled_f = current_env[v_f, u_f]
        img_front = np.zeros((lens_dim, lens_dim, 3), dtype=np.uint8)
        img_front[y_f, x_f] = (sampled_f.astype(np.float32) * vig_f).astype(np.uint8)

        # 2. Rear Lens sampling
        w_rays_r = np.dot(rays_r, R_total.T)
        lat_r = np.arcsin(np.clip(-w_rays_r[:, 1], -1.0, 1.0))
        lon_r = np.arctan2(w_rays_r[:, 0], w_rays_r[:, 2])
        u_r = ((lon_r + math.pi) * inv_two_pi).astype(np.int32) % env_w
        v_r = ((math.pi / 2.0 - lat_r) * inv_pi).astype(np.int32) % env_h

        sampled_r = current_env[v_r, u_r]
        img_rear = np.zeros((lens_dim, lens_dim, 3), dtype=np.uint8)
        img_rear[y_r, x_r] = (sampled_r.astype(np.float32) * vig_r).astype(np.uint8)

        # 3. Assemble SBS Frame
        sbs_frame = np.zeros((height, width, 3), dtype=np.uint8)
        sbs_frame[:, :half_w] = img_front
        sbs_frame[:, half_w:] = img_rear

        # 4. HUD Telemetry & Defect Overlay
        if HAS_OPENCV:
            font = cv2.FONT_HERSHEY_SIMPLEX
            # Left Lens Overlay
            cv2.putText(sbs_frame, "LENS 1 [FRONT 0 deg] - RAW FISHEYE", (50, 70), font, 1.1, (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(sbs_frame, "LENS 1 [FRONT 0 deg] - RAW FISHEYE", (50, 70), font, 1.1, (255, 255, 255), 2, cv2.LINE_AA)

            defect_str1 = f"DEFECTS: FOV={ih_fov:.1f}x{iv_fov:.1f} | LEFT_Y={left_y_offset:+d}px | REAR_ROLL={rear_roll_offset:+.1f} deg"
            cv2.putText(sbs_frame, defect_str1, (50, 115), font, 0.8, (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(sbs_frame, defect_str1, (50, 115), font, 0.8, (0, 200, 255), 2, cv2.LINE_AA)

            defect_str2 = f"ERRORS: YAW_ERR={yaw_defect:+.1f} deg | PITCH_ERR={pitch_defect:+.1f} deg | ROLL_ERR={roll_defect:+.1f} deg"
            cv2.putText(sbs_frame, defect_str2, (50, 155), font, 0.8, (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(sbs_frame, defect_str2, (50, 155), font, 0.8, (0, 200, 255), 2, cv2.LINE_AA)

            dyn_str = f"MOTION: PITCH={dyn_pitch:+05.1f} deg | ROLL={dyn_roll:+05.1f} deg | YAW={dyn_yaw:+05.1f} deg"
            cv2.putText(sbs_frame, dyn_str, (50, 195), font, 0.8, (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(sbs_frame, dyn_str, (50, 195), font, 0.8, (50, 255, 255), 2, cv2.LINE_AA)

            frame_str = f"FRAME: {frame_idx + 1:03d}/{total_frames:03d} | TIME: {t:.3f}s"
            cv2.putText(sbs_frame, frame_str, (50, height - 50), font, 1.0, (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(sbs_frame, frame_str, (50, height - 50), font, 1.0, (0, 255, 0), 2, cv2.LINE_AA)

            # Right Lens Overlay
            cv2.putText(sbs_frame, "LENS 2 [REAR 180 deg] - RAW FISHEYE", (half_w + 50, 70), font, 1.1, (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(sbs_frame, "LENS 2 [REAR 180 deg] - RAW FISHEYE", (half_w + 50, 70), font, 1.1, (255, 255, 255), 2, cv2.LINE_AA)

            cv2.putText(sbs_frame, defect_str1, (half_w + 50, 115), font, 0.8, (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(sbs_frame, defect_str1, (half_w + 50, 115), font, 0.8, (0, 200, 255), 2, cv2.LINE_AA)

            cv2.putText(sbs_frame, defect_str2, (half_w + 50, 155), font, 0.8, (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(sbs_frame, defect_str2, (half_w + 50, 155), font, 0.8, (0, 200, 255), 2, cv2.LINE_AA)

            cv2.putText(sbs_frame, dyn_str, (half_w + 50, 195), font, 0.8, (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(sbs_frame, dyn_str, (half_w + 50, 195), font, 0.8, (50, 255, 255), 2, cv2.LINE_AA)

            cv2.putText(sbs_frame, frame_str, (half_w + 50, height - 50), font, 1.0, (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(sbs_frame, frame_str, (half_w + 50, height - 50), font, 1.0, (0, 255, 0), 2, cv2.LINE_AA)

        # Write frame to stream
        if use_ffmpeg and ffmpeg_proc is not None:
            ffmpeg_proc.stdin.write(sbs_frame.tobytes())
        elif writer is not None:
            writer.write(sbs_frame)

        if (frame_idx + 1) % 15 == 0 or frame_idx == total_frames - 1:
            print(f"[PROGRESS] Rendered frame {frame_idx + 1}/{total_frames} ({(frame_idx + 1)/total_frames*100:.0f}%)", flush=True)

    if use_ffmpeg and ffmpeg_proc is not None:
        ffmpeg_proc.stdin.close()
        ffmpeg_proc.wait()
    elif writer is not None:
        writer.release()

    elapsed = time.time() - start_time
    print(f"[SUCCESS] Video rendered in {elapsed:.2f}s -> {output_path}", flush=True)

    # 5. Export Mockup Telemetry Files and Embed in MP4
    export_mockup_telemetry(output_path, telemetry_data, fps=fps)


def export_mockup_telemetry(video_path, telemetry_data, fps=30):
    """Export companion telemetry text and embed vrot metadata into MP4 container.

    Args:
        video_path: Path to target MP4 video file.
        telemetry_data: Sequence of (timestamp, roll, pitch, yaw) tuples.
        fps: Video frame rate.
    """
    from datetime import datetime, timedelta
    base_name = os.path.splitext(video_path)[0]
    txt_path = base_name + "_telemetry.txt"

    print(f"[INFO] Exporting mockup telemetry files...", flush=True)

    # 1. Export standard WitMotion / Samsung Gear 360 9-column telemetry text
    start_time = datetime(2026, 7, 21, 0, 0, 0)
    with open(txt_path, "w", newline="\n", encoding="utf-8") as f:
        headers = ["ChipTime", "AngleX(deg)", "AngleY(deg)", "AngleZ(deg)", "q0", "q1", "q2", "q3", "Time(s)"]
        f.write("\t".join(headers) + "\n")
        for t, r, p, y in telemetry_data:
            chip_dt = start_time + timedelta(seconds=t)
            chip_str = chip_dt.strftime("%Y-%m-%d %H:%M:%S") + f".{int((t % 1) * 1000):03d}"
            # Compute Unit Quaternions from Euler angles
            cy = math.cos(math.radians(y) * 0.5)
            sy = math.sin(math.radians(y) * 0.5)
            cp = math.cos(math.radians(p) * 0.5)
            sp = math.sin(math.radians(p) * 0.5)
            cr = math.cos(math.radians(r) * 0.5)
            sr = math.sin(math.radians(r) * 0.5)
            q0 = cr * cp * cy + sr * sp * sy
            q1 = sr * cp * cy - cr * sp * sy
            q2 = cr * sp * cy + sr * cp * sy
            q3 = cr * cp * sy - sr * sp * cy
            f.write(f"{chip_str}\t{r:.6f}\t{p:.6f}\t{y:.6f}\t{q0:.6f}\t{q1:.6f}\t{q2:.6f}\t{q3:.6f}\t{t:.6f}\n")
    print(f"       - Telemetry TXT (9-col WitMotion/Gear360 format): {txt_path}", flush=True)

    # 2. Embed 'vrot' metadata atom into MP4 container
    try:
        embed_vrot_atom(video_path, telemetry_data)
        print(f"       - Embedded 'vrot' telemetry box into: {video_path}", flush=True)
    except Exception as e:
        print(f"[WARN] Could not embed vrot box into MP4: {e}", flush=True)


def embed_vrot_atom(mp4_path, telemetry_data):
    """Embed Samsung Gear 360 'vrot' metadata atom directly into MP4 moov.udta box.

    Args:
        mp4_path: Filepath to target MP4 video.
        telemetry_data: Sequence of (timestamp, roll, pitch, yaw) tuples.
    """
    if not os.path.exists(mp4_path):
        return

    # Build vrot records
    records = bytearray()
    scale = 1000
    for t, r, p, y in telemetry_data:
        # Scale to int32 (yaw, pitch, roll)
        y_val = int(round(y * scale))
        p_val = int(round(p * scale))
        r_val = int(round(r * scale))
        records += struct.pack(">iiiiii", scale, y_val, scale, p_val, scale, r_val)

    # vrot box
    vrot_payload = b"\x00\x00\x00\x00" + records
    vrot_box = struct.pack(">I", len(vrot_payload) + 8) + b"vrot" + vrot_payload
    udta_box = struct.pack(">I", len(vrot_box) + 8) + b"udta" + vrot_box

    # Locate moov box in MP4
    file_size = os.path.getsize(mp4_path)
    with open(mp4_path, "rb") as f:
        data = f.read()

    # Search for moov box offset
    offset = 0
    moov_offset = None
    moov_size = None
    while offset + 8 <= len(data):
        box_size, box_type = struct.unpack(">I4s", data[offset:offset+8])
        if box_type == b"moov":
            moov_offset = offset
            moov_size = box_size
            break
        if box_size <= 0:
            break
        offset += box_size

    if moov_offset is None:
        return

    moov_bytes = data[moov_offset:moov_offset+moov_size]
    # Check if udta exists inside moov
    udta_idx = moov_bytes.find(b"udta")
    if udta_idx != -1:
        # Replace existing udta
        udta_box_offset = udta_idx - 4
        old_udta_size, = struct.unpack(">I", moov_bytes[udta_box_offset:udta_box_offset+4])
        new_moov = moov_bytes[:udta_box_offset] + udta_box + moov_bytes[udta_box_offset+old_udta_size:]
    else:
        # Append udta to moov
        new_moov = moov_bytes + udta_box

    # Update moov size header
    new_moov = struct.pack(">I", len(new_moov)) + new_moov[4:]

    # Write modified MP4
    new_file_data = data[:moov_offset] + new_moov + data[moov_offset+moov_size:]
    with open(mp4_path, "wb") as f:
        f.write(new_file_data)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate synthetic unstabilized raw dual fisheye video with defect simulation.")
    parser.add_argument("--output", default="test_fisheye.mp4", help="Output MP4 file path")
    parser.add_argument("--duration", type=float, default=3.0, help="Duration in seconds (default 3.0s)")
    parser.add_argument("--fps", type=int, default=30, help="Frames per second (default 30)")
    parser.add_argument("--width", type=int, default=3840, help="Frame width (default 3840)")
    parser.add_argument("--height", type=int, default=1920, help="Frame height (default 1920)")
    parser.add_argument("--ih_fov", type=float, default=190.8, help="Input Horizontal FOV defect (default 190.8 deg)")
    parser.add_argument("--iv_fov", type=float, default=190.8, help="Input Vertical FOV defect (default 190.8 deg)")
    parser.add_argument("--left_y_offset", type=int, default=2, help="Left lens vertical shift defect (default +2 px)")
    parser.add_argument("--rear_roll_offset", type=float, default=1.2, help="Rear lens roll offset defect (default +1.2 deg)")
    parser.add_argument("--rear_pitch_offset", type=float, default=0.0, help="Rear lens pitch offset defect (default 0.0 deg)")
    parser.add_argument("--rear_yaw_offset", type=float, default=0.0, help="Rear lens yaw offset defect (default 0.0 deg)")
    parser.add_argument("--yaw_defect", type=float, default=-2.0, help="Static yaw rotation defect (default -2.0 deg)")
    parser.add_argument("--pitch_defect", type=float, default=-1.2, help="Static pitch rotation defect (default -1.2 deg)")
    parser.add_argument("--roll_defect", type=float, default=0.6, help="Static roll rotation defect (default +0.6 deg)")
    parser.add_argument("--crf", type=int, default=32, help="Constant Rate Factor for x264 compression (default 32)")
    parser.add_argument("--preset", default="medium", help="FFmpeg x264 compression preset (default 'medium')")

    args = parser.parse_args()
    generate_unstabilized_defective_video(
        output_path=args.output,
        duration=args.duration,
        fps=args.fps,
        width=args.width,
        height=args.height,
        ih_fov=args.ih_fov,
        iv_fov=args.iv_fov,
        left_y_offset=args.left_y_offset,
        rear_roll_offset=args.rear_roll_offset,
        rear_pitch_offset=args.rear_pitch_offset,
        rear_yaw_offset=args.rear_yaw_offset,
        yaw_defect=args.yaw_defect,
        pitch_defect=args.pitch_defect,
        roll_defect=args.roll_defect,
        crf=args.crf,
        preset=args.preset
    )
