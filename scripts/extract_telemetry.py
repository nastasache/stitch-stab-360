import sys
sys.dont_write_bytecode = True
import os
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

"""Multi-format video telemetry extraction and orientation normalization.

Extracts embedded IMU metadata from Samsung Gear 360 (vrot box), GoPro (GPMF
CORI/IORI), Google/Ricoh (CAMM), and companion Gyroflow (.gcsv) / CSV logs,
outputting standardized 9-column Euler/quaternion telemetry.
"""

import re
import struct
import json
import math
import subprocess
from datetime import datetime, timedelta

WIN_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from utils.mp4_utils import get_video_properties, find_box, parse_vrot

def euler_to_quaternion(roll_deg, pitch_deg, yaw_deg):
    """Convert Euler angles (roll, pitch, yaw) in degrees to a unit quaternion.

    Args:
        roll_deg: Roll angle in degrees.
        pitch_deg: Pitch angle in degrees.
        yaw_deg: Yaw angle in degrees.

    Returns:
        tuple[float, float, float, float]: Unit quaternion components (w, x, y, z).
    """
    roll = math.radians(roll_deg)
    pitch = math.radians(pitch_deg)
    yaw = math.radians(yaw_deg)
    
    cy = math.cos(yaw * 0.5)
    sy = math.sin(yaw * 0.5)
    cp = math.cos(pitch * 0.5)
    sp = math.sin(pitch * 0.5)
    cr = math.cos(roll * 0.5)
    sr = math.sin(roll * 0.5)
    
    w = cr * cp * cy + sr * sp * sy
    x = sr * cp * cy - cr * sp * sy
    y = cr * sp * cy + sr * cp * sy
    z = cr * cp * sy - sr * sp * cy
    return w, x, y, z

def quaternion_to_euler(w, x, y, z):
    """Convert unit quaternion to Euler angles in degrees.

    Args:
        w: Quaternion scalar (real) component.
        x: Quaternion vector x component.
        y: Quaternion vector y component.
        z: Quaternion vector z component.

    Returns:
        tuple[float, float, float]: Euler angles (roll, pitch, yaw) in degrees.
    """
    sinr_cosp = 2.0 * (w * x + y * z)
    cosr_cosp = 1.0 - 2.0 * (x * x + y * y)
    roll = math.degrees(math.atan2(sinr_cosp, cosr_cosp))

    sinp = 2.0 * (w * y - z * x)
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, sinp))))

    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    yaw = math.degrees(math.atan2(siny_cosp, cosy_cosp))
    return roll, pitch, yaw

def parse_gpmf_stream(video_path, fps=30.0):
    """Extract and decode GoPro GPMF metadata stream from video container.

    Extracts CORI/IORI orientation quaternion payloads via FFmpeg raw stream mapping.

    Args:
        video_path: Filesystem path to the GoPro video file.
        fps: Inferred video frame rate in frames per second.

    Returns:
        list[tuple[float, float, float]] | None: List of (roll, pitch, yaw) tuples
            in degrees, or None if no GPMF orientation stream is found.
    """
    try:
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-map", "0:m:handler_name:GoPro MET?",
            "-map", "0:d:0?",
            "-f", "rawvideo", "-"
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=WIN_NO_WINDOW)
        raw_bytes, _ = proc.communicate(timeout=20)
        if not raw_bytes or len(raw_bytes) < 32:
            return None

        rot_data = []
        offset = 0
        n_bytes = len(raw_bytes)
        
        while offset + 8 <= n_bytes:
            fourcc = raw_bytes[offset:offset+4].decode('latin1', errors='ignore')
            type_char = chr(raw_bytes[offset+4])
            elem_size = raw_bytes[offset+5]
            elem_count = struct.unpack('>H', raw_bytes[offset+6:offset+8])[0]
            payload_len = elem_size * elem_count
            pad = (4 - (payload_len % 4)) % 4
            next_offset = offset + 8 + payload_len + pad

            if fourcc in ['CORI', 'IORI'] and type_char in ['s', 'S'] and elem_size == 2 and elem_count >= 4:
                chunk = raw_bytes[offset+8:offset+8+payload_len]
                for s_idx in range(0, len(chunk), 8):
                    if s_idx + 8 <= len(chunk):
                        w_raw, x_raw, y_raw, z_raw = struct.unpack('>hhhh', chunk[s_idx:s_idx+8])
                        scale = 32767.0
                        qw, qx, qy, qz = w_raw / scale, x_raw / scale, y_raw / scale, z_raw / scale
                        r, p, y = quaternion_to_euler(qw, qx, qy, qz)
                        rot_data.append((r, p, y))
            offset = next_offset

        return rot_data if len(rot_data) > 0 else None
    except Exception as e:
        print(f"[Telemetry] GoPro GPMF parse notice: {e}")
        return None

def parse_camm_stream(video_path, fps=30.0):
    """Extract and decode Google / Ricoh / Android CAMM metadata stream.

    Parses binary camera motion records for quaternion orientations (msg_type 4).

    Args:
        video_path: Filesystem path to the video file.
        fps: Inferred video frame rate in frames per second.

    Returns:
        list[tuple[float, float, float]] | None: List of (roll, pitch, yaw) tuples
            in degrees, or None if no CAMM stream is found.
    """
    try:
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-map", "0:m:handler_name:Camera Metadata?",
            "-map", "0:d:0?",
            "-f", "rawvideo", "-"
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=WIN_NO_WINDOW)
        raw_bytes, _ = proc.communicate(timeout=20)
        if not raw_bytes or len(raw_bytes) < 24:
            return None

        rot_data = []
        offset = 0
        n_bytes = len(raw_bytes)
        
        while offset + 12 <= n_bytes:
            msg_type = struct.unpack('<H', raw_bytes[offset+2:offset+4])[0]
            if msg_type == 4 and offset + 20 <= n_bytes:
                qx, qy, qz, qw = struct.unpack('<ffff', raw_bytes[offset+4:offset+20])
                r, p, y = quaternion_to_euler(qw, qx, qy, qz)
                rot_data.append((r, p, y))
                offset += 20
            elif msg_type in [0, 1] and offset + 16 <= n_bytes:
                offset += 16
            else:
                offset += 4

        return rot_data if len(rot_data) > 0 else None
    except Exception as e:
        print(f"[Telemetry] CAMM parse notice: {e}")
        return None

def parse_gyroflow_gcsv(gcsv_path):
    """Parse Gyroflow .gcsv IMU log file and integrate gyro rates into Euler angles.

    Args:
        gcsv_path: Filesystem path to the .gcsv log file.

    Returns:
        list[tuple[float, float, float]] | None: List of integrated (roll, pitch, yaw)
            tuples in degrees, or None if parsing fails.
    """
    if not os.path.exists(gcsv_path):
        return None
    try:
        rot_data = []
        cur_r, cur_p, cur_y = 0.0, 0.0, 0.0
        prev_t = None
        
        with open(gcsv_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
            
        header_idx = -1
        for i, l in enumerate(lines[:10]):
            if 't,' in l or 'timestamp' in l:
                header_idx = i
                break
                
        if header_idx == -1:
            return None
            
        headers = [h.strip().lower() for h in lines[header_idx].split(',')]
        t_idx = headers.index('t') if 't' in headers else 0
        gx_idx = headers.index('gx') if 'gx' in headers else 1
        gy_idx = headers.index('gy') if 'gy' in headers else 2
        gz_idx = headers.index('gz') if 'gz' in headers else 3
        
        for line in lines[header_idx+1:]:
            parts = line.strip().split(',')
            if len(parts) <= max(t_idx, gx_idx, gy_idx, gz_idx):
                continue
            try:
                t_val = float(parts[t_idx])
                t_sec = t_val / 1000000.0 if t_val > 100000 else (t_val / 1000.0 if t_val > 1000 else t_val)
                gx = float(parts[gx_idx])
                gy = float(parts[gy_idx])
                gz = float(parts[gz_idx])
            except ValueError:
                continue
                
            if prev_t is not None:
                dt = max(0.0001, t_sec - prev_t)
                cur_r += gx * dt
                cur_p += gy * dt
                cur_y += gz * dt
                rot_data.append((cur_r, cur_p, cur_y))
            else:
                rot_data.append((0.0, 0.0, 0.0))
            prev_t = t_sec
            
        return rot_data if len(rot_data) > 0 else None
    except Exception as e:
        print(f"[Telemetry] Gyroflow parse notice: {e}")
        return None

def parse_companion_file(file_path):
    """Parse an explicit companion telemetry file (.gcsv or Euler CSV/TXT).

    Args:
        file_path: Filesystem path to the companion log file.

    Returns:
        tuple[list[tuple[float, float, float]] | None, str | None]: Tuple of
            (rot_data, resolved_file_path), or (None, None) if parsing fails.
    """
    if not file_path or not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
        return None, None
    if file_path.lower().endswith('.gcsv'):
        res = parse_gyroflow_gcsv(file_path)
        if res:
            print(f"[Telemetry] Loaded companion Gyroflow log: {file_path}")
            return res, file_path
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            first_line = f.readline()
        fl_lower = first_line.lower()
        if 'gyroflow' in fl_lower:
            res = parse_gyroflow_gcsv(file_path)
            if res:
                print(f"[Telemetry] Loaded Gyroflow format: {file_path}")
                return res, file_path
        elif 'anglex' in fl_lower or 'roll' in fl_lower or 'time' in fl_lower:
            rot_data = []
            sep = '\t' if '\t' in first_line else (';' if ';' in first_line else ',')
            headers = [h.strip() for h in first_line.split(sep)]
            ax_idx = -1
            ay_idx = -1
            az_idx = -1
            for idx, h in enumerate(headers):
                hl = h.lower()
                if 'anglex' in hl or 'roll' in hl: ax_idx = idx
                elif 'angley' in hl or 'pitch' in hl: ay_idx = idx
                elif 'anglez' in hl or 'yaw' in hl: az_idx = idx
            
            if ax_idx != -1 and ay_idx != -1 and az_idx != -1:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    for line in f.readlines()[1:]:
                        pts = line.strip().split(sep)
                        if len(pts) > max(ax_idx, ay_idx, az_idx):
                            try:
                                rot_data.append((float(pts[ax_idx]), float(pts[ay_idx]), float(pts[az_idx])))
                            except ValueError:
                                continue
                if rot_data:
                    print(f"[Telemetry] Loaded companion Euler log: {file_path}")
                    return rot_data, file_path
    except Exception:
        pass
    return None, None

def resolve_companion_candidates(input_video, source_hint="auto"):
    """Finds candidate companion telemetry files for a given input video and source hint.

    Args:
        input_video (str): Path to input video file.
        source_hint (str): Source type hint ('auto', 'gyroflow', 'witmotion', 'custom_csv').

    Returns:
        list[str]: Candidate file paths in prioritized search order.
    """
    base_name = os.path.splitext(input_video)[0]
    dir_name = os.path.dirname(input_video) or "."
    file_name = os.path.basename(input_video)
    stem = os.path.splitext(file_name)[0]

    gyro_cands = [
        f"{base_name}.gcsv",
        f"{input_video}.gcsv",
        f"{base_name}_telemetry.gcsv",
    ]
    euler_cands = [
        f"{base_name}_telemetry.txt",
        f"{base_name}.telemetry.txt",
        f"{base_name}.csv",
        f"{base_name}_telemetry.csv",
        f"{base_name}.txt",
    ]

    for standard_dir in ["data/input/videos", "data/runtime/work"]:
        norm_std = os.path.normpath(standard_dir)
        if os.path.normpath(dir_name) != norm_std:
            gyro_cands.extend([
                os.path.join(standard_dir, f"{stem}.gcsv"),
                os.path.join(standard_dir, f"{file_name}.gcsv"),
                os.path.join(standard_dir, f"{stem}_telemetry.gcsv"),
            ])
            euler_cands.extend([
                os.path.join(standard_dir, f"{stem}_telemetry.txt"),
                os.path.join(standard_dir, f"{stem}.telemetry.txt"),
                os.path.join(standard_dir, f"{stem}.csv"),
                os.path.join(standard_dir, f"{stem}_telemetry.csv"),
                os.path.join(standard_dir, f"{stem}.txt"),
            ])

    if source_hint == "gyroflow":
        return [c for c in gyro_cands if os.path.exists(c) and os.path.getsize(c) > 0]
    elif source_hint in ["witmotion", "custom_csv"]:
        return [c for c in euler_cands if os.path.exists(c) and os.path.getsize(c) > 0]
    else:
        all_cands = gyro_cands + euler_cands
        seen = set()
        deduped = []
        for c in all_cands:
            norm_c = os.path.normcase(os.path.abspath(c))
            if norm_c not in seen:
                seen.add(norm_c)
                if os.path.exists(c) and os.path.getsize(c) > 0:
                    deduped.append(c)
        return deduped

def write_standard_telemetry_file(output_file, rot_data, fps=30.0):
    """Write standardized 9-column WitMotion / Samsung Gear 360 telemetry file.

    Args:
        output_file: Destination path for formatted telemetry text file.
        rot_data: List of (roll, pitch, yaw) rotation tuples in degrees.
        fps: Video frame rate for synthetic timestamp generation.
    """
    start_time = datetime(2026, 7, 21, 0, 0, 0)
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        headers = [
            "ChipTime", "AngleX(deg)", "AngleY(deg)", "AngleZ(deg)",
            "q0", "q1", "q2", "q3", "Time(s)"
        ]
        f.write("\t".join(headers) + "\n")
        
        for i, (roll, pitch, yaw) in enumerate(rot_data):
            time_sec = i / fps
            current_time = start_time + timedelta(seconds=time_sec)
            chip_time = current_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            qw, qx, qy, qz = euler_to_quaternion(roll, pitch, yaw)
            row = [
                chip_time,
                f"{roll:.6f}", f"{pitch:.6f}", f"{yaw:.6f}",
                f"{qw:.6f}", f"{qx:.6f}", f"{qy:.6f}", f"{qz:.6f}",
                f"{time_sec:.6f}"
            ]
            f.write("\t".join(row) + "\n")

def main():
    """Execute command-line interface for multi-format video telemetry extraction."""
    if len(sys.argv) < 2:
        print("Usage: python extract_telemetry.py <input_video.MP4> [output_telemetry.txt] [--source auto|samsung|gopro|camm|gyroflow] [--companion <path>]")
        sys.exit(1)
        
    input_video = sys.argv[1]
    if not os.path.exists(input_video):
        print(f"Error: file {input_video} does not exist.")
        sys.exit(1)
        
    output_file = sys.argv[2] if (len(sys.argv) > 2 and not sys.argv[2].startswith('--')) else os.path.splitext(input_video)[0] + "_telemetry.txt"
    
    source_hint = "auto"
    companion_path = None
    for i, arg in enumerate(sys.argv):
        if arg == "--source" and i + 1 < len(sys.argv):
            source_hint = sys.argv[i+1].lower()
        elif arg == "--companion" and i + 1 < len(sys.argv):
            companion_path = sys.argv[i+1]

    if not companion_path and source_hint == "gyroflow":
        base_name = os.path.splitext(input_video)[0]
        companion_path = f"{base_name}.gcsv"

    
    props = get_video_properties(input_video)
    fps = props["fps"] if props else 30.0
    
    rot_data = None
    source_found = None

    # 1. Check Samsung Gear 360 'vrot'
    if source_hint in ["auto", "samsung"] and rot_data is None:
        file_size = os.path.getsize(input_video)
        with open(input_video, "rb") as f:
            vrot_box = find_box(f, 0, file_size, ["moov", "udta", "vrot"])
        if vrot_box:
            offset, size = vrot_box
            with open(input_video, "rb") as f:
                f.seek(offset + 12)
                vrot_bytes = f.read(size - 12)
            rot_data = parse_vrot(vrot_bytes)
            if rot_data:
                source_found = "Samsung Gear 360 (vrot box)"

    # 2. Check GoPro GPMF
    if source_hint in ["auto", "gopro"] and rot_data is None:
        rot_data = parse_gpmf_stream(input_video, fps=fps)
        if rot_data:
            source_found = "GoPro GPMF (gpmd stream)"

    # 3. Check Google / Ricoh CAMM
    if source_hint in ["auto", "camm", "insta360"] and rot_data is None:
        rot_data = parse_camm_stream(input_video, fps=fps)
        if rot_data:
            source_found = "CAMM (Camera Motion stream)"

    # 4. Check Companion Files
    if rot_data is None:
        if companion_path and os.path.exists(companion_path):
            rot_data, comp_path = parse_companion_file(companion_path)
            if rot_data:
                source_found = f"Companion Telemetry ({os.path.basename(comp_path)})"
        elif source_hint in ["auto", "gyroflow", "witmotion", "custom_csv"]:
            candidates = resolve_companion_candidates(input_video, source_hint=source_hint)
            for cand in candidates:
                rot_data, comp_path = parse_companion_file(cand)
                if rot_data:
                    source_found = f"Companion Telemetry ({os.path.basename(comp_path)})"
                    break

    if not rot_data:
        print(f"Error: No valid telemetry stream or companion file detected for '{input_video}'.", file=sys.stderr)
        sys.exit(1)

    print(f"[Telemetry] Successfully extracted {len(rot_data)} orientation frames via {source_found}.")
    write_standard_telemetry_file(output_file, rot_data, fps=fps)
    print("Telemetry extraction complete!")

if __name__ == "__main__":
    main()
