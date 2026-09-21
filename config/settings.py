import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
"""Centralized application and pipeline configuration parameters.

Defines filesystem paths, server and network properties, static asset roots,
allowed file extensions, and default parameters for dual-fisheye stitching,
optical stabilization, IMU telemetry sensor fusion, and Street View export.
"""

from pathlib import Path
from typing import Dict, List, Any

# Project Root Directory
BASE_DIR = Path(__file__).resolve().parent.parent

# Debug mode: set env var STITCHSTAB_DEBUG=1 to enable debug-only response fields (e.g. raw command lines)
DEBUG = os.environ.get("STITCHSTAB_DEBUG", "0").strip() == "1"

# Automatically resolve and prioritize optimal FFmpeg build (Gyan full with libvidstab)
try:
    from utils.tool_resolver import resolve_ffmpeg
    resolve_ffmpeg()
except Exception:
    pass

# Centralized Path Definitions
PATHS = {
    "BASE_DIR": BASE_DIR,
    "CONFIG_DIR": BASE_DIR / "config",
    "DATA_DIR": BASE_DIR / "data",
    "RUN_COUNTER_FILE": BASE_DIR / "config" / "run_counter.txt",
    "MANAGED_PIDS_FILE": BASE_DIR / "data" / "runtime" / "temp" / "managed_pids.json",
    "STATUS_FILE": BASE_DIR / "data" / "runtime" / "temp" / "status.json",
    "REQUIRED_DIRECTORIES": [
        "data/input/videos",
        "data/input/nadir",
        "data/input/gps",
        "data/input/presets",
        "data/input/masks",
        "data/output",
        "data/runtime/work",
        "data/runtime/temp",
        "data/runtime/logs",
        "samples",
        "scratch",
        "config",
        "others"
    ]
}

# Server and Network Configuration
SERVER_CONFIG = {
    "HOST": "127.0.0.1",
    "PORT": 8000,
    "MAX_BODY_BYTES": 10 * 1024 * 1024,  # 10 MB limit for incoming request payloads
    "CORS_ORIGINS": [
        "http://127.0.0.1:8000",
        "http://localhost:8000"
    ],
    "STATIC_DIRECTORIES": [
        "data",
        "assets",
        "samples",
        "js",
        "others"
    ],
    "ALLOWED_EXTENSIONS": {
        "video": ["mp4", "mov", "mkv", "avi", "webm", "m4v", "insv"],
        "image": ["png", "jpg", "jpeg", "webp"],
        "gpx": ["gpx"]
    }
}

# Baseline Pipeline and Stitching Constants
PIPELINE_DEFAULTS = {
    # Camera optics & warping
    "ih_fov": 190.00,
    "iv_fov": 190.00,
    "raw_rotation": 0,
    "yaw": 0.0,
    "pitch": 0.0,
    "roll": 0.0,
    "left_y_offset": 0.0,
    "rear_roll_offset": 0.0,
    "blend_width": 200,
    "anti_vignette_angle": 0.785,

    # FFmpeg encoding
    "ffmpeg_preset": "medium",
    "ffmpeg_crf": 18,
    "video_bitrate": "80M",
    "streetview_bitrate": "45M",
    "remove_audio": False,
    "v360_backend": "cpu",

    # Nadir overlay
    "nadir_fov": 75.0,
    "nadir_fov_v": 75.0,
    "nadir_logo_default": "logo_stei_circle.png",

    # Stabilization defaults
    "stab_quality_mode": 4,
    "vidstab_smoothing": 2,
    "vidstab_shakiness": 1,
    "vidstab_stepsize": 32,
    "vidstab_optalgo": "gauss",
    "kabsch_smoothing": 5,
    "kopf_keyframe_sec": 2.0,
    "kopf_cube_face": 1024,
    "kopf_max_features": 400,

    # Telemetry
    "telemetry_smoothing": 30,
    "telemetry_ref_frame": 0,
    "telemetry_mode": "smooth",
    "telemetry_source": "auto",
    "telemetry_multiplier": 1.0,
    "telemetry_multiplier_roll": 1.0,
    "telemetry_multiplier_pitch": 1.0,
    "telemetry_multiplier_yaw": 1.0,
    "telemetry_fusion": "mahony",
    "telemetry_fusion_gain": 0.51,
    "l1_lambda_acc": 20.0,
    "l1_lambda_vel": 2.0,
    "cinematic_window": 45,
    "traveldir_mode": "travel_direction",
    "traveldir_target_yaw": 0.0,
    "traveldir_damping": 0.90,
    "traveldir_deadband": 1.5,

    # Street View export defaults
    "streetview_mode": "A",
    "streetview_time_offset": 0.0,
    "streetview_auto_pad": 1,
    "streetview_smooth_gps": 1,
    "streetview_strip_audio": 1
}
