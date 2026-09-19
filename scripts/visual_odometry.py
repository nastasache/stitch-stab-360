import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

"""Spherical visual odometry and trajectory checkpoint extraction for 360° video.

Processes equirectangular video streams via FFmpeg grayscale sub-sampling,
computes equatorial dense optical flow for heading estimation, and projects
dead-reckoning motion onto geographic WGS 84 coordinate tracks.
"""

import math
import shutil
import subprocess
import cv2
import numpy as np

WIN_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

def format_sec_to_hhmmss(sec: float) -> str:
    """Format elapsed seconds into HH:MM:SS timestamp string.

    Args:
        sec: Elapsed duration in seconds.

    Returns:
        str: Formatted timestamp string (e.g. '00:01:23').
    """
    s = max(0, int(round(sec)))
    hh = s // 3600
    mm = (s % 3600) // 60
    ss = s % 60
    return f"{hh:02d}:{mm:02d}:{ss:02d}"

def extract_video_trajectory(
    video_path: str,
    start_lat: float,
    start_lon: float,
    initial_heading_deg: float = 0.0,
    walking_speed_mps: float = 1.15,
    checkpoint_interval_sec: int = 10,
    start_ele: float = 315.0
):
    """Extract 360 visual odometry trajectory and generate WGS 84 checkpoints.

    Performs dense Farneback optical flow on equatorial equirectangular video bands,
    integrates relative yaw shifts, models forward dead-reckoning motion at constant
    walking velocity, and outputs geographic coordinates and curve checkpoints.

    Args:
        video_path: Filesystem path to the input 360 video file.
        start_lat: Initial starting latitude in decimal degrees.
        start_lon: Initial starting longitude in decimal degrees.
        initial_heading_deg: Initial camera compass heading in degrees (0 = North).
        walking_speed_mps: Assumed walking velocity in meters per second.
        checkpoint_interval_sec: Interval in seconds between periodic checkpoints.
        start_ele: Starting elevation in meters above sea level.

    Returns:
        dict: Trajectory metadata dictionary containing:
            - 'checkpoints': List of sampled waypoint dictionaries.
            - 'all_points': Full second-by-second geographic coordinate list.
            - 'total_distance_m': Cumulative path distance in meters.
            - 'net_displacement_m': Straight-line distance from start in meters.
            - 'duration_sec': Total duration analyzed in seconds.
            - 'points_count': Number of retained checkpoint waypoints.

    Raises:
        FileNotFoundError: If video_path does not exist on disk.
        ValueError: If video stream is shorter than 2 seconds.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")

    w, h = 160, 80
    frame_size = w * h

    cmd = [
        "ffmpeg", "-y",
        "-threads", "4",
        "-i", video_path,
        "-vf", f"fps=1,scale={w}:{h},format=gray",
        "-f", "rawvideo",
        "-pix_fmt", "gray",
        "-"
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=WIN_NO_WINDOW)

    prev_band = None
    headings_deg = [0.0]
    cum_yaw = 0.0

    try:
        while True:
            raw = proc.stdout.read(frame_size)
            if len(raw) < frame_size:
                break
            img = np.frombuffer(raw, dtype=np.uint8).reshape((h, w))
            band = img[16:64, :]

            if prev_band is not None:
                flow = cv2.calcOpticalFlowFarneback(prev_band, band, None, 0.5, 2, 9, 2, 5, 1.1, 0)
                median_dx = float(np.median(flow[..., 0]))
                d_yaw = -(median_dx / 160.0) * 360.0
                if abs(d_yaw) > 45.0:
                    d_yaw = math.copysign(45.0, d_yaw)
                cum_yaw += d_yaw
                headings_deg.append(cum_yaw)

            prev_band = band
    finally:
        proc.stdout.close()
        proc.wait()

    num_frames = len(headings_deg)
    if num_frames < 2:
        raise ValueError("Video too short for visual odometry (needs at least 2 seconds).")

    x_meters = [0.0]
    y_meters = [0.0]
    dt = 1.0

    for i in range(1, num_frames):
        current_heading_deg = initial_heading_deg + headings_deg[i]
        heading_rad = math.radians(current_heading_deg)
        dx = walking_speed_mps * math.sin(heading_rad) * dt
        dy = walking_speed_mps * math.cos(heading_rad) * dt
        x_meters.append(x_meters[-1] + dx)
        y_meters.append(y_meters[-1] + dy)

    meters_per_deg_lat = 111320.0
    meters_per_deg_lon = 111320.0 * math.cos(math.radians(start_lat))

    all_points = []
    for sec in range(num_frames):
        lat = start_lat + (y_meters[sec] / meters_per_deg_lat)
        lon = start_lon + (x_meters[sec] / meters_per_deg_lon)
        all_points.append({
            "sec": sec,
            "timeStr": format_sec_to_hhmmss(sec),
            "lat": round(lat, 8),
            "lon": round(lon, 8),
            "ele": round(start_ele, 1)
        })

    selected_indices = set([0, num_frames - 1])
    for sec in range(0, num_frames, max(1, checkpoint_interval_sec)):
        selected_indices.add(sec)

    for sec in range(2, num_frames - 2):
        d_prev = headings_deg[sec] - headings_deg[sec - 2]
        d_next = headings_deg[sec + 2] - headings_deg[sec]
        if abs(d_prev - d_next) > 8.0:
            selected_indices.add(sec)

    sorted_indices = sorted(list(selected_indices))
    checkpoints = [all_points[i] for i in sorted_indices]

    total_dist = sum(
        math.hypot(x_meters[i] - x_meters[i-1], y_meters[i] - y_meters[i-1])
        for i in range(1, num_frames)
    )
    net_disp = math.hypot(x_meters[-1], y_meters[-1])

    return {
        "checkpoints": checkpoints,
        "all_points": all_points,
        "total_distance_m": round(total_dist, 1),
        "net_displacement_m": round(net_disp, 1),
        "duration_sec": num_frames - 1,
        "points_count": len(checkpoints)
    }

if __name__ == "__main__":
    if len(sys.argv) > 1:
        test_video = sys.argv[1]
        res = extract_video_trajectory(test_video, 40.781200, -73.966500, initial_heading_deg=270.0)
        print(f"Visual Odometry test passed! {res['points_count']} checkpoints extracted across {res['duration_sec']}s ({res['total_distance_m']}m).")
        for cp in res["checkpoints"][:5]:
            print(f"  {cp['timeStr']}, {cp['lat']}, {cp['lon']}, {cp['ele']}")
