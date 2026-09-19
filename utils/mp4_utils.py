import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

"""MP4 and QuickTime container binary parsing and metadata extraction utilities."""

import struct
import subprocess
import json

WIN_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

def get_video_properties(input_path: str):
    """Retrieve video stream properties via ffprobe.

    Extracts dimensions, frame rate, duration, frame count, and telemetry
    data streams (e.g., GPMD, CAMM) from the container.

    Args:
        input_path: Filesystem path to the input video file.

    Returns:
        dict | None: Dictionary containing video properties ('width', 'height',
            'fps', 'duration', 'total_frames', 'data_streams'), or None if
            probing fails or no video stream is found.
    """
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "stream=index,codec_name,codec_type,codec_tag_string,width,height,r_frame_rate,duration,nb_frames:stream_tags=handler_name",
        "-of", "json",
        input_path
    ]
    try:
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, creationflags=WIN_NO_WINDOW).decode('utf-8', errors='ignore').strip()
        data = json.loads(output)
        video_stream = None
        data_streams = []
        for s in data.get("streams", []):
            if s.get("codec_type") == "video" and video_stream is None:
                video_stream = s
            elif s.get("codec_type") == "data" or s.get("codec_name") in ["gpmd", "camm"]:
                data_streams.append(s)

        if not video_stream:
            return None

        fps_parts = video_stream.get("r_frame_rate", "30/1").split('/')
        fps = float(fps_parts[0]) / float(fps_parts[1]) if len(fps_parts) == 2 else float(fps_parts[0])
        duration = float(video_stream.get("duration", 0))
        nb_frames = int(video_stream.get("nb_frames", 0)) if video_stream.get("nb_frames") else int(round(duration * fps))
        return {
            "width": int(video_stream.get("width", 3840)),
            "height": int(video_stream.get("height", 1920)),
            "fps": fps if fps > 0 else 30.0,
            "duration": duration,
            "total_frames": nb_frames,
            "data_streams": data_streams
        }
    except Exception as e:
        print(f"[mp4_utils] Error getting video properties: {e}", file=sys.stderr)
        return None

def get_video_duration(video_path: str) -> float:
    """Perform fast probe of video container duration.

    Args:
        video_path: Filesystem path to the video file.

    Returns:
        float: Duration of the video in seconds, or 0.0 on error.
    """
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True, creationflags=WIN_NO_WINDOW)
        return float(res.stdout.strip())
    except Exception as e:
        print(f"[mp4_utils] Error getting duration: {e}", file=sys.stderr)
        return 0.0

def find_box(f, offset: int, end: int, target_path: list, current_depth: int = 0):
    """Search recursively for an MP4 atom/box matching target atom hierarchy.

    Supports 32-bit and 64-bit extended box sizes and 'meta' container offsets.

    Args:
        f: Open binary file handle positioned for reading.
        offset: Starting byte offset in the file for atom search.
        end: Ending byte offset bounding the current container box.
        target_path: Ordered list of atom four-character codes (e.g. ['moov', 'udta', 'vrot']).
        current_depth: Current recursion depth in target_path hierarchy.

    Returns:
        tuple[int, int] | None: Tuple of (box_offset, box_size) in bytes if found, else None.
    """
    if current_depth >= len(target_path):
        return None
    target_type = target_path[current_depth]
    
    try:
        while offset < end:
            f.seek(offset)
            header = f.read(8)
            if len(header) < 8:
                break
            size, box_type = struct.unpack(">I4s", header)
            box_type_str = box_type.decode("latin1")
            
            box_size = size
            header_len = 8
            if size == 1:
                ext_header = f.read(8)
                box_size, = struct.unpack(">Q", ext_header)
                header_len = 16
            elif size == 0:
                box_size = end - offset

            if box_type_str == target_type:
                if current_depth == len(target_path) - 1:
                    return offset, box_size
                else:
                    sub_offset = offset + header_len
                    if box_type_str == "meta":
                        sub_offset += 4
                    return find_box(f, sub_offset, offset + box_size, target_path, current_depth + 1)
            
            offset += box_size
    except Exception as e:
        print(f"[mp4_utils] Error parsing box at offset {offset}: {e}", file=sys.stderr)
    return None

def parse_vrot(vrot_bytes: bytes) -> list:
    """Unpack Samsung Gear 360 vrot telemetry records into Euler angles.

    Parses 24-byte big-endian records and normalizes angles to [-180, 180] degrees.

    Args:
        vrot_bytes: Raw binary payload extracted from the 'vrot' atom.

    Returns:
        list[tuple[float, float, float]]: List of (roll, pitch, yaw) tuples in degrees.
    """
    rot_data = []
    offset = 0
    while offset + 24 <= len(vrot_bytes):
        msg = vrot_bytes[offset:offset+24]
        y_scale, y_val, p_scale, p_val, r_scale, r_val = struct.unpack(">iiiiii", msg)
        
        yaw   = (y_val / y_scale) if y_scale != 0 else 0.0
        pitch = (p_val / p_scale) if p_scale != 0 else 0.0
        roll  = (r_val / r_scale) if r_scale != 0 else 0.0
        
        if pitch > 180.0: pitch -= 360.0
        elif pitch < -180.0: pitch += 360.0
        
        if roll > 180.0: roll -= 360.0
        elif roll < -180.0: roll += 360.0
        
        if yaw > 180.0: yaw -= 360.0
        elif yaw < -180.0: yaw += 360.0
        
        rot_data.append((roll, pitch, yaw))
        offset += 24
    return rot_data
