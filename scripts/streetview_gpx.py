#!/usr/bin/env python3
import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
"""Google Street View compliant 360 video and GPX track generator.

Processes GPS track data (via manual map checkpoints or external GPX loggers),
synchronizes timestamps with 360 video duration, interpolates smooth 1 Hz track
points, generates CAMM telemetry tracks, injects spatial metadata, and produces
interactive Leaflet HTML route maps.
"""

import re
import math
import json
import argparse
import datetime
import time
import subprocess
import shutil
import defusedxml.ElementTree as ET

WIN_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

try:
    from utils.tool_resolver import resolve_ffprobe, resolve_ffmpeg
except Exception:
    resolve_ffprobe = None
    resolve_ffmpeg = None

def parse_iso_or_utc(ts_str):
    """Parse ISO-8601 or UTC datetime string into timezone-aware datetime.

    Args:
        ts_str: Datetime representation as string or datetime object.

    Returns:
        Timezone-aware UTC datetime object, or None if parsing fails.
    """
    if not ts_str:
        return None
    if isinstance(ts_str, datetime.datetime):
        if ts_str.tzinfo is None:
            return ts_str.replace(tzinfo=datetime.timezone.utc)
        return ts_str.astimezone(datetime.timezone.utc)
    ts_str = str(ts_str).strip()
    try:
        clean = ts_str.replace("Z", "+00:00").replace(" ", "T")
        dt = datetime.datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc)
    except Exception:
        pass
    for fmt in (
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ"
    ):
        try:
            dt = datetime.datetime.strptime(ts_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            return dt.astimezone(datetime.timezone.utc)
        except Exception:
            continue
    return None

def get_video_info(video_path):
    """Probe video stream metrics and creation timestamp using ffprobe.

    Args:
        video_path: Filepath to video file.

    Returns:
        Tuple of (duration_sec, width, height, fps, creation_time_dt).
    """
    if not video_path:
        return 0.0, 3840, 1920, 30.0, None

    clean_path = str(video_path).strip()
    if clean_path.startswith("-") or "\0" in clean_path:
        raise ValueError(f"Invalid video path: {clean_path!r}")

    base_dir_abs = os.path.realpath(os.path.abspath(_REPO_ROOT))
    abs_video_path = os.path.realpath(os.path.abspath(os.path.join(base_dir_abs, clean_path) if not os.path.isabs(clean_path) else clean_path))
    if not abs_video_path.startswith(base_dir_abs + os.sep):
        raise ValueError(f"Invalid video path outside workspace boundaries: {clean_path!r}")
    if not os.path.isfile(abs_video_path):
        return 0.0, 3840, 1920, 30.0, None

    ffprobe_bin = "ffprobe"
    if callable(resolve_ffprobe):
        try:
            info = resolve_ffprobe()
            if info and info.get("path"):
                ffprobe_bin = info["path"]
        except Exception:
            ffprobe_bin = "ffprobe"

    cmd = [
        ffprobe_bin, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "format=duration:format_tags=creation_time:stream=width,height,r_frame_rate,duration:stream_tags=creation_time",
        "-of", "json", "--", abs_video_path
    ]
    creation_dt = None
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True, creationflags=WIN_NO_WINDOW)
        data = json.loads(res.stdout)
        stream = data.get("streams", [{}])[0]
        fmt = data.get("format", {})
        
        dur_str = fmt.get("duration") or stream.get("duration") or "0"
        duration = float(dur_str)
        width = int(stream.get("width", 3840))
        height = int(stream.get("height", 1920))
        
        r_fps = stream.get("r_frame_rate", "30/1")
        if "/" in r_fps:
            num, den = r_fps.split("/")
            fps = float(num) / float(den) if float(den) != 0 else 30.0
        else:
            fps = float(r_fps)

        c_tag = fmt.get("tags", {}).get("creation_time") or stream.get("tags", {}).get("creation_time")
        if c_tag:
            creation_dt = parse_iso_or_utc(c_tag)

        if creation_dt is None and os.path.exists(abs_video_path):
            mtime = os.path.getmtime(abs_video_path)
            creation_dt = datetime.datetime.fromtimestamp(mtime, tz=datetime.timezone.utc)
            
        return duration, width, height, fps, creation_dt
    except Exception as e:
        print(f"Error reading video info from {abs_video_path}: {e}", file=sys.stderr)
        return 0.0, 3840, 1920, 30.0, None

def extract_video_creation_time_utc(video_path):
    """Extract creation_time UTC datetime from video metadata container.

    Args:
        video_path: Filepath to video file.

    Returns:
        Timezone-aware UTC datetime of video creation, or file modification time fallback.
    """
    _, _, _, _, dt = get_video_info(video_path)
    return dt

def parse_time_str(val_str):
    """Parse time string into float seconds.

    Supports 'HH:MM:SS', 'MM:SS', or raw seconds numbers.

    Args:
        val_str: Time string or numeric representation in seconds.

    Returns:
        Float timestamp in seconds, or None if parsing fails.
    """
    if isinstance(val_str, (int, float)):
        return float(val_str)
    if not isinstance(val_str, str):
        return None
    val_str = val_str.strip()
    if ":" in val_str:
        parts = val_str.split(":")
        try:
            if len(parts) == 3:
                return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
            elif len(parts) == 2:
                return float(parts[0]) * 60 + float(parts[1])
        except ValueError:
            return None
    try:
        return float(val_str)
    except ValueError:
        return None

def clean_num_val(s):
    """Clean numeric string and strip trailing meter/zoom suffixes (e.g. '208m' -> 208.0).

    Args:
        s: String or numeric representation of elevation or coordinate.

    Returns:
        Float numeric value, or None if conversion fails.
    """
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).strip().rstrip('mM').rstrip('zZ')
    try:
        return float(s)
    except ValueError:
        return None

def parse_checkpoint_list(checkpoints_input):
    """Parse checkpoint input into structured coordinate dictionaries.

    Supports lists of dicts/tuples, JSON files/strings, Google Maps URLs
    (including '@lat,lon' and '!3d/!4d' pin markers), and comma-separated text lines.

    Args:
        checkpoints_input: List, JSON string, filepath, or plain text containing checkpoints.

    Returns:
        List of dictionaries with keys 'lat', 'lon', and optional 'time', 'ele'.
    """
    if not checkpoints_input:
        return []

    raw_items = []
    if isinstance(checkpoints_input, str):
        s_input = checkpoints_input.strip()
        if "\n" not in s_input and "," not in s_input and "\0" not in s_input and not s_input.startswith("-") and (s_input.endswith(".json") or s_input.endswith(".txt")):
            base_dir_abs = os.path.realpath(os.path.abspath(_REPO_ROOT))
            target_abs = os.path.realpath(os.path.abspath(os.path.join(base_dir_abs, s_input) if not os.path.isabs(s_input) else s_input))
            if target_abs.startswith(base_dir_abs + os.sep) and os.path.isfile(target_abs):
                try:
                    with open(target_abs, "r", encoding="utf-8") as f:
                        content = f.read().strip()
                    try:
                        raw_items = json.loads(content)
                    except Exception:
                        raw_items = content.splitlines()
                except Exception:
                    raw_items = []
            else:
                try:
                    raw_items = json.loads(s_input)
                except Exception:
                    raw_items = s_input.splitlines()
        else:
            try:
                raw_items = json.loads(s_input)
            except Exception:
                raw_items = s_input.splitlines()
    elif isinstance(checkpoints_input, list):
        raw_items = checkpoints_input

    result = []
    for item in raw_items:
        if isinstance(item, dict):
            lat = float(item['lat'])
            lon = float(item['lon'])
            t = parse_time_str(item.get('time')) if 'time' in item else None
            ele = clean_num_val(item.get('ele')) if 'ele' in item else None
            cp = {'lat': lat, 'lon': lon}
            if t is not None:
                cp['time'] = t
            if ele is not None:
                cp['ele'] = ele
            result.append(cp)
        elif isinstance(item, str):
            line = item.strip()
            if not line or line.startswith("#"):
                continue

            # 1. Check for Google Maps URL pattern: prioritize true pin (!3d/!4d) over viewport center (@lat,lon)
            m_pin = re.search(r'!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)', line)
            m_url = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)(?:,(\d+(?:\.\d+)?)(?:m|z)?)?', line)
            if m_pin or m_url:
                if m_pin:
                    lat = float(m_pin.group(1))
                    lon = float(m_pin.group(2))
                else:
                    lat = float(m_url.group(1))
                    lon = float(m_url.group(2))

                ele = float(m_url.group(3)) if (m_url and m_url.group(3)) else None
                t = None
                
                # Extract any time token outside the URL
                clean_line = re.sub(r'https?://[^\s,]+', '', line)
                for token in clean_line.replace(',', ' ').split():
                    t_val = parse_time_str(token)
                    if t_val is not None:
                        t = t_val
                        break
                cp = {'lat': lat, 'lon': lon}
                if t is not None:
                    cp['time'] = t
                if ele is not None:
                    cp['ele'] = ele
                result.append(cp)
                continue

            # 2. Standard comma-separated parsing (2, 3, or 4 tokens)
            parts = [p.strip() for p in line.split(",") if p.strip()]
            t = None
            lat = None
            lon = None
            ele = None

            if len(parts) == 4:
                # e.g. 00:01:57, 44.4340, 26.1055, 208
                # or   44.4340, 26.1055, 00:01:57, 208
                time_idx = -1
                for i, p in enumerate(parts):
                    if ":" in p:
                        time_idx = i
                        break
                if time_idx != -1:
                    t = parse_time_str(parts[time_idx])
                    rem = [parts[i] for i in range(4) if i != time_idx]
                    lat = clean_num_val(rem[0])
                    lon = clean_num_val(rem[1])
                    ele = clean_num_val(rem[2])
                else:
                    t = parse_time_str(parts[0])
                    lat = clean_num_val(parts[1])
                    lon = clean_num_val(parts[2])
                    ele = clean_num_val(parts[3])

            elif len(parts) == 3:
                # e.g. 00:01:57, 44.4340, 26.1055
                # or   44.4340, 26.1055, 00:01:57
                # or   44.4340, 26.1055, 208 (lat, lon, ele)
                if ":" in parts[0]:
                    t = parse_time_str(parts[0])
                    lat = clean_num_val(parts[1])
                    lon = clean_num_val(parts[2])
                elif ":" in parts[2]:
                    lat = clean_num_val(parts[0])
                    lon = clean_num_val(parts[1])
                    t = parse_time_str(parts[2])
                else:
                    lat = clean_num_val(parts[0])
                    lon = clean_num_val(parts[1])
                    ele = clean_num_val(parts[2])

            elif len(parts) == 2:
                lat = clean_num_val(parts[0])
                lon = clean_num_val(parts[1])

            if lat is not None and lon is not None:
                cp = {'lat': lat, 'lon': lon}
                if t is not None:
                    cp['time'] = t
                if ele is not None:
                    cp['ele'] = ele
                result.append(cp)

    return result

def interpolate_checkpoints(checkpoints, total_duration_sec):
    """Interpolate checkpoint coordinates and elevations to generate 1 point per second.

    Args:
        checkpoints: Sequence of checkpoint dictionaries with 'lat', 'lon', and optional 'time', 'ele'.
        total_duration_sec: Total duration of the track in integer seconds.

    Returns:
        List of dictionaries containing {'lat': float, 'lon': float, 'ele': float, 'sec': int}.
    """
    if isinstance(checkpoints, (str, list)) and not (isinstance(checkpoints, list) and checkpoints and isinstance(checkpoints[0], dict)):
        checkpoints = parse_checkpoint_list(checkpoints)

    if not checkpoints:
        return []
    if len(checkpoints) == 1:
        ele_val = round(checkpoints[0].get('ele', 315.0), 3)
        return [{'lat': round(checkpoints[0]['lat'], 8), 'lon': round(checkpoints[0]['lon'], 8), 'ele': ele_val, 'sec': i} for i in range(int(total_duration_sec) + 1)]

    # Assign default elevation if completely missing from all checkpoints
    has_any_ele = any('ele' in cp and cp['ele'] is not None for cp in checkpoints)
    if not has_any_ele:
        for cp in checkpoints:
            cp['ele'] = 315.0
    else:
        # Fill missing intermediate elevations
        first_ele = next(cp['ele'] for cp in checkpoints if 'ele' in cp and cp['ele'] is not None)
        curr_ele = first_ele
        for cp in checkpoints:
            if 'ele' not in cp or cp['ele'] is None:
                cp['ele'] = curr_ele
            else:
                curr_ele = cp['ele']

    # Calculate distances between sequential checkpoints
    distances = [0.0]
    for i in range(1, len(checkpoints)):
        lat1, lon1 = checkpoints[i-1]['lat'], checkpoints[i-1]['lon']
        lat2, lon2 = checkpoints[i]['lat'], checkpoints[i]['lon']
        d = math.hypot(lat2 - lat1, lon2 - lon1)
        distances.append(d)

    total_dist = sum(distances)
    
    # Assign time offsets to checkpoints if not explicitly given
    has_custom_times = all('time' in cp and cp['time'] is not None for cp in checkpoints)
    if not has_custom_times:
        cum_dist = 0.0
        cp_times = [0.0]
        for i in range(1, len(checkpoints)):
            cum_dist += distances[i]
            ratio = (cum_dist / total_dist) if total_dist > 0 else (i / (len(checkpoints) - 1))
            cp_times.append(ratio * total_duration_sec)
        for i in range(len(checkpoints)):
            checkpoints[i]['time'] = cp_times[i]

    # Perform piecewise linear interpolation for every integer second from 0 to total_duration_sec
    result = []
    num_pts = int(total_duration_sec) + 1
    for s in range(num_pts):
        sec = float(s)
        # Find segment
        if sec <= checkpoints[0]['time']:
            lat = checkpoints[0]['lat']
            lon = checkpoints[0]['lon']
            ele = checkpoints[0]['ele']
        elif sec >= checkpoints[-1]['time']:
            lat = checkpoints[-1]['lat']
            lon = checkpoints[-1]['lon']
            ele = checkpoints[-1]['ele']
        else:
            idx = 0
            for i in range(len(checkpoints) - 1):
                if checkpoints[i]['time'] <= sec <= checkpoints[i+1]['time']:
                    idx = i
                    break
            cp1 = checkpoints[idx]
            cp2 = checkpoints[idx + 1]
            t_span = cp2['time'] - cp1['time']
            if t_span <= 0:
                factor = 0.0
            else:
                factor = (sec - cp1['time']) / t_span
                factor = max(0.0, min(1.0, factor))
            lat = cp1['lat'] + factor * (cp2['lat'] - cp1['lat'])
            lon = cp1['lon'] + factor * (cp2['lon'] - cp1['lon'])
            ele = cp1['ele'] + factor * (cp2['ele'] - cp1['ele'])

        result.append({'lat': round(lat, 8), 'lon': round(lon, 8), 'ele': round(ele, 3), 'sec': s})

    return result

def parse_gpx_file(gpx_file_path):
    """Parse input GPX file and extract track points with timestamps and elevation.

    Args:
        gpx_file_path: Filepath to GPX XML file.

    Returns:
        List of dictionaries containing 'lat', 'lon', 'ele', 'dt', and 'time_str'.
    """
    if not gpx_file_path or str(gpx_file_path).strip().startswith("-") or "\0" in str(gpx_file_path):
        return []
    clean_path = str(gpx_file_path).strip()
    base_dir_abs = os.path.realpath(os.path.abspath(_REPO_ROOT))
    target_abs = os.path.realpath(os.path.abspath(os.path.join(base_dir_abs, clean_path) if not os.path.isabs(clean_path) else clean_path))
    if not target_abs.startswith(base_dir_abs + os.sep):
        return []
    if not os.path.isfile(target_abs):
        return []
    try:
        tree = ET.parse(target_abs)
        root = tree.getroot()
        points = []
        for trkpt in root.findall('.//{*}trkpt') or root.findall('.//trkpt'):
            lat = float(trkpt.attrib['lat'])
            lon = float(trkpt.attrib['lon'])
            ele_elem = trkpt.find('{*}ele') if trkpt.find('{*}ele') is not None else trkpt.find('ele')
            ele = float(ele_elem.text.strip()) if (ele_elem is not None and ele_elem.text) else 0.0
            time_elem = trkpt.find('{*}time') if trkpt.find('{*}time') is not None else trkpt.find('time')
            time_str = time_elem.text.strip() if time_elem is not None and time_elem.text else None
            dt = parse_iso_or_utc(time_str) if time_str else None
            points.append({'lat': lat, 'lon': lon, 'ele': ele, 'dt': dt, 'time_str': time_str})
        return points
    except Exception as e:
        print(f"Error parsing GPX file {gpx_file_path}: {e}", file=sys.stderr)
        return []

def haversine_distance(lat1, lon1, lat2, lon2):
    """Compute great-circle geodesic distance between two coordinate pairs in meters.

    Args:
        lat1: Latitude of first point in decimal degrees.
        lon1: Longitude of first point in decimal degrees.
        lat2: Latitude of second point in decimal degrees.
        lon2: Longitude of second point in decimal degrees.

    Returns:
        Distance between points in meters using the Haversine formula.
    """
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2.0)**2
    return R * 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))

def parse_coord_str(coord_str):
    """Parse coordinate string into latitude and longitude tuple.

    Args:
        coord_str: Coordinate string formatted as 'lat, lon', 'lat lon', or sequence.

    Returns:
        Tuple of (latitude, longitude) as floats, or None if parsing fails.
    """
    if not coord_str:
        return None
    if isinstance(coord_str, (list, tuple)) and len(coord_str) >= 2:
        try:
            return float(coord_str[0]), float(coord_str[1])
        except (ValueError, TypeError):
            return None
    s = str(coord_str).strip().replace(";", ",").replace("/", ",")
    parts = [p.strip() for p in s.split(",") if p.strip()]
    if len(parts) < 2:
        parts = [p.strip() for p in s.split() if p.strip()]
    if len(parts) >= 2:
        try:
            return float(parts[0]), float(parts[1])
        except ValueError:
            return None
    return None

def find_closest_gpx_point(raw_gpx_points, target_lat, target_lon):
    """Find closest trackpoint to target coordinate pair.

    Args:
        raw_gpx_points: Sequence of trackpoint dictionaries with 'lat' and 'lon'.
        target_lat: Target latitude in decimal degrees.
        target_lon: Target longitude in decimal degrees.

    Returns:
        Tuple of (closest_point_dict, point_index, min_dist_meters).
    """
    if not raw_gpx_points:
        return None, -1, float('inf')
    best_p = None
    best_idx = -1
    min_d = float('inf')
    for i, p in enumerate(raw_gpx_points):
        d = haversine_distance(target_lat, target_lon, p['lat'], p['lon'])
        if d < min_d:
            min_d = d
            best_p = p
            best_idx = i
    return best_p, best_idx, min_d

def sync_gpx_by_timestamps(raw_gpx_points, video_start_dt, total_seconds):
    """Slice and interpolate GPX track points against a video's temporal playback window.

    Args:
        raw_gpx_points: List of raw GPX trackpoint dictionaries with UTC datetime 'dt'.
        video_start_dt: Timezone-aware UTC datetime of the video start.
        total_seconds: Duration of the video in seconds.

    Returns:
        List of interpolated 1 Hz dictionaries {'lat': float, 'lon': float, 'ele': float, 'sec': int}.
    """
    if not raw_gpx_points:
        return []

    # Filter points that have parsed timestamps
    valid_pts = [p for p in raw_gpx_points if p.get('dt') is not None]

    # If GPX points lack timestamps or fewer than 2 valid points, fallback to proportion interpolation
    if len(valid_pts) < 2:
        num_raw = len(raw_gpx_points)
        track_points = []
        for s in range(total_seconds + 1):
            idx = min(int((s / total_seconds) * (num_raw - 1)), num_raw - 1) if total_seconds > 0 else 0
            p = raw_gpx_points[idx]
            track_points.append({'lat': p['lat'], 'lon': p['lon'], 'ele': p.get('ele', 0.0), 'sec': s})
        return track_points

    # Ensure valid points are sorted by datetime
    valid_pts.sort(key=lambda p: p['dt'])

    gpx_start_dt = valid_pts[0]['dt']
    gpx_end_dt = valid_pts[-1]['dt']

    print(f"[StreetView] GPX timestamp span: {gpx_start_dt.isoformat()} -> {gpx_end_dt.isoformat()} ({len(valid_pts)} points)")
    print(f"[StreetView] Video target window: {video_start_dt.isoformat()} -> {(video_start_dt + datetime.timedelta(seconds=total_seconds)).isoformat()} ({total_seconds}s)")

    track_points = []
    pt_idx = 0
    num_pts = len(valid_pts)

    for s in range(total_seconds + 1):
        target_dt = video_start_dt + datetime.timedelta(seconds=s)

        # If target time is before the GPX track starts, clamp to first point
        if target_dt <= valid_pts[0]['dt']:
            p = valid_pts[0]
            track_points.append({'lat': round(p['lat'], 8), 'lon': round(p['lon'], 8), 'ele': round(p.get('ele', 0.0), 3), 'sec': s})
            continue

        # If target time is after the GPX track ends (e.g. video padding or extra recording), clamp to last point
        if target_dt >= valid_pts[-1]['dt']:
            p = valid_pts[-1]
            track_points.append({'lat': round(p['lat'], 8), 'lon': round(p['lon'], 8), 'ele': round(p.get('ele', 0.0), 3), 'sec': s})
            continue

        # Advance pt_idx until valid_pts[pt_idx + 1]['dt'] >= target_dt
        while pt_idx < num_pts - 2 and valid_pts[pt_idx + 1]['dt'] < target_dt:
            pt_idx += 1

        p1 = valid_pts[pt_idx]
        p2 = valid_pts[pt_idx + 1]

        t_span = (p2['dt'] - p1['dt']).total_seconds()
        if t_span <= 0:
            factor = 0.0
        else:
            factor = (target_dt - p1['dt']).total_seconds() / t_span
            factor = max(0.0, min(1.0, factor))

        lat = p1['lat'] + factor * (p2['lat'] - p1['lat'])
        lon = p1['lon'] + factor * (p2['lon'] - p1['lon'])
        ele = p1.get('ele', 0.0) + factor * (p2.get('ele', 0.0) - p1.get('ele', 0.0))
        track_points.append({'lat': round(lat, 8), 'lon': round(lon, 8), 'ele': round(ele, 3), 'sec': s})

    return track_points

synchronize_gpx_to_video_window = sync_gpx_by_timestamps

def smooth_track_points(track_points, iterations=2):
    """Apply weighted Gaussian smoothing (0.25, 0.50, 0.25) to reduce GPS sensor noise.

    Preserves exact boundary coordinates and 1 Hz temporal spacing.

    Args:
        track_points: Sequence of track point dictionaries.
        iterations: Number of smoothing passes to apply.

    Returns:
        New list of smoothed track point dictionaries.
    """
    if not track_points or len(track_points) < 3:
        return track_points

    curr = [dict(p) for p in track_points]
    n = len(curr)

    for _ in range(max(1, int(iterations))):
        smoothed = [dict(curr[0])]
        for i in range(1, n - 1):
            p0 = curr[i - 1]
            p1 = curr[i]
            p2 = curr[i + 1]

            n_lat = 0.25 * p0['lat'] + 0.50 * p1['lat'] + 0.25 * p2['lat']
            n_lon = 0.25 * p0['lon'] + 0.50 * p1['lon'] + 0.25 * p2['lon']
            n_ele = 0.25 * p0.get('ele', 0.0) + 0.50 * p1.get('ele', 0.0) + 0.25 * p2.get('ele', 0.0)

            pt = dict(p1)
            pt['lat'] = round(n_lat, 8)
            pt['lon'] = round(n_lon, 8)
            pt['ele'] = round(n_ele, 3)
            smoothed.append(pt)

        smoothed.append(dict(curr[-1]))
        curr = smoothed

    return curr

def build_gpx_xml(track_points, start_utc_dt):
    """Construct formatted GPX 1.1 XML string from 1 Hz track points.

    Args:
        track_points: Sequence of track point dictionaries with 'lat', 'lon', 'ele', and 'sec'.
        start_utc_dt: Timezone-aware UTC start datetime.

    Returns:
        Formatted XML string representing GPX track.
    """
    gpx = ET.Element("gpx", {
        "version": "1.1",
        "creator": "360 Gear StreetView Builder",
        "xmlns": "http://www.topografix.com/GPX/1/1"
    })
    trk = ET.SubElement(gpx, "trk")
    name = ET.SubElement(trk, "name")
    name.text = "Google Street View Track"
    trkseg = ET.SubElement(trk, "trkseg")

    for pt in track_points:
        sec = pt['sec']
        pt_dt = start_utc_dt + datetime.timedelta(seconds=sec)
        iso_time = pt_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        trkpt = ET.SubElement(trkseg, "trkpt", {
            "lat": f"{pt['lat']:.8f}",
            "lon": f"{pt['lon']:.8f}"
        })
        if 'ele' in pt and pt['ele'] is not None:
            ele_el = ET.SubElement(trkpt, "ele")
            ele_el.text = f"{pt['ele']:.3f}"
        t_elem = ET.SubElement(trkpt, "time")
        t_elem.text = iso_time

    # Pretty print XML
    ET.indent(gpx, space="  ")
    xml_header = '<?xml version="1.0" encoding="UTF-8"?>\n'
    return xml_header + ET.tostring(gpx, encoding="unicode")

def build_map_html(track_points, start_utc_dt, output_html_path, title_name="Google Street View Route", title=None):
    """Generate standalone interactive Leaflet HTML map visualizing the Street View route.

    Args:
        track_points: Sequence of track point dictionaries.
        start_utc_dt: UTC start timestamp.
        output_html_path: Destination path for generated HTML map file.
        title_name: Heading label displayed above the interactive map.
        title: Optional alias for title_name.

    Returns:
        Filepath to the saved HTML map.
    """
    final_title = title if title is not None else title_name
    pts_with_time = []
    for i, pt in enumerate(track_points):
        sec = pt.get('sec', i)
        pt_dt = start_utc_dt + datetime.timedelta(seconds=sec)
        pts_with_time.append({
            'lat': pt['lat'],
            'lon': pt['lon'],
            'sec': sec,
            'time': pt_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        })
    pts_json = json.dumps(pts_with_time)

    total_sec = len(track_points) - 1 if track_points else 0
    start_time_str = start_utc_dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    duration_str = f"{total_sec // 60}m {total_sec % 60}s"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{final_title} - Route Inspector</title>
    <link rel="stylesheet" href="/js/leaflet/leaflet.css" />
    <script src="/js/leaflet/leaflet.js"></script>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
        body {{ background: #0f172a; color: #f8fafc; display: flex; flex-direction: column; height: 100vh; overflow: hidden; }}
        header {{ background: rgba(30, 41, 59, 0.95); padding: 12px 20px; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid rgba(255,255,255,0.1); z-index: 1000; }}
        h1 {{ font-size: 1.1rem; color: #38bdf8; display: flex; align-items: center; gap: 8px; }}
        .meta-badge {{ font-size: 0.8rem; background: rgba(56, 189, 248, 0.15); border: 1px solid rgba(56, 189, 248, 0.3); color: #38bdf8; padding: 4px 10px; border-radius: 6px; }}
        #map {{ flex: 1; width: 100%; }}
        .controls-card {{ position: absolute; bottom: 20px; left: 50%; transform: translateX(-50%); width: 90%; max-width: 750px; background: rgba(15, 23, 42, 0.92); backdrop-filter: blur(12px); border: 1px solid rgba(255,255,255,0.15); border-radius: 12px; padding: 14px 20px; z-index: 1000; box-shadow: 0 10px 30px rgba(0,0,0,0.6); }}
        .slider-row {{ display: flex; align-items: center; gap: 15px; margin-bottom: 8px; }}
        input[type="range"] {{ flex: 1; accent-color: #38bdf8; cursor: pointer; height: 6px; }}
        .info-row {{ display: flex; justify-content: space-between; font-size: 0.85rem; color: #94a3b8; font-family: monospace; }}
        .info-val {{ color: #f8fafc; font-weight: bold; }}
        .leaflet-control-layers {{ background: rgba(15, 23, 42, 0.92) !important; border: 1px solid rgba(255, 255, 255, 0.2) !important; border-radius: 8px !important; color: #f8fafc !important; font-size: 12px !important; padding: 8px 12px !important; box-shadow: 0 4px 15px rgba(0,0,0,0.6) !important; backdrop-filter: blur(8px); }}
        .leaflet-control-layers label {{ display: flex !important; align-items: center !important; gap: 8px !important; margin-bottom: 4px !important; cursor: pointer !important; color: #e2e8f0 !important; font-size: 12px !important; }}
        .leaflet-control-layers input[type="radio"] {{ accent-color: #38bdf8 !important; cursor: pointer !important; }}
        .leaflet-bar a {{ background: rgba(15, 23, 42, 0.92) !important; border-color: rgba(255, 255, 255, 0.15) !important; color: #38bdf8 !important; }}
        .leaflet-bar a:hover {{ background: #1e293b !important; color: #60a5fa !important; }}
    </style>
</head>
<body>
    <header>
        <h1>📍 {final_title} - Route & Timeline Inspector</h1>
        <div style="display:flex; gap:10px;">
            <span class="meta-badge">Start: {start_time_str}</span>
            <span class="meta-badge">Duration: {duration_str} ({len(track_points)} pts)</span>
        </div>
    </header>

    <div id="map"></div>

    <div class="controls-card">
        <div class="slider-row">
            <button id="btn-play" style="background:#38bdf8; color:#0f172a; border:none; padding:8px 16px; border-radius:6px; font-weight:bold; cursor:pointer;">▶ Play</button>
            <input type="range" id="timeline-slider" min="0" max="{max(0, len(track_points)-1)}" value="0">
            <span id="current-time-lbl" style="font-family:monospace; font-size:1rem; font-weight:bold; color:#38bdf8; min-width:80px;">00:00:00</span>
        </div>
        <div class="info-row">
            <span>Video Time: <span id="lbl-vid-sec" class="info-val">0.0s</span></span>
            <span>GPS UTC: <span id="lbl-utc-time" class="info-val">{pts_with_time[0]['time'] if pts_with_time else ''}</span></span>
            <span>Coord: <span id="lbl-coord" class="info-val">{pts_with_time[0]['lat'] if pts_with_time else 0}, {pts_with_time[0]['lon'] if pts_with_time else 0}</span></span>
        </div>
        <div class="note-row" style="margin-top: 8px; font-size: 0.75rem; color: #94a3b8; border-top: 1px solid rgba(255,255,255,0.08); padding-top: 6px; text-align: center;">
            ℹ️ <strong>Note:</strong> Route overlay is approximate. Minor visual offsets may occur due to satellite tile georeferencing differences (Esri World Imagery vs. Google Satellite) and consumer GPS receiver precision.
        </div>
    </div>

    <script>
        const points = {pts_json};

        function initMap() {{
            if (!points || points.length === 0) return;

            const map = L.map('map', {{
                maxZoom: 22
            }}).setView([points[0].lat, points[0].lon], 18);

            const osm = L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
                maxNativeZoom: 19,
                maxZoom: 22,
                attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors'
            }});

            const esriSat = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}', {{
                maxNativeZoom: 18,
                maxZoom: 22,
                attribution: '© Esri, Maxar'
            }});

            const googleSat = L.tileLayer('https://mt{{s}}.google.com/vt/lyrs=s&x={{x}}&y={{y}}&z={{z}}', {{
                subdomains: ['0', '1', '2', '3'],
                maxNativeZoom: 20,
                maxZoom: 22,
                attribution: '© Google Maps'
            }}).addTo(map);

            const googleHybrid = L.tileLayer('https://mt{{s}}.google.com/vt/lyrs=y&x={{x}}&y={{y}}&z={{z}}', {{
                subdomains: ['0', '1', '2', '3'],
                maxNativeZoom: 20,
                maxZoom: 22,
                attribution: '© Google Maps'
            }});

            const baseMaps = {{
                "Google Satellite (Ultra Zoom)": googleSat,
                "Google Hybrid (Roads & Labels)": googleHybrid,
                "Esri / Maxar Satellite": esriSat,
                "OpenStreetMap (Standard)": osm
            }};

            L.control.layers(baseMaps, null, {{ position: 'topright' }}).addTo(map);

            const latlngs = points.map(p => [p.lat, p.lon]);
            const polyline = L.polyline(latlngs, {{ color: '#0284c7', weight: 6, opacity: 0.9 }}).addTo(map);
            map.fitBounds(polyline.getBounds(), {{ padding: [40, 40] }});

            L.marker(latlngs[0], {{ title: "Start (00:00)" }}).addTo(map).bindPopup("<b>Route Start (00:00)</b><br>UTC: " + points[0].time);
            L.marker(latlngs[latlngs.length - 1], {{ title: "End" }}).addTo(map).bindPopup("<b>Route End</b><br>UTC: " + points[points.length-1].time);

            const currentMarker = L.circleMarker([points[0].lat, points[0].lon], {{
                color: '#ffffff',
                fillColor: '#ef4444',
                fillOpacity: 1,
                radius: 9,
                weight: 2
            }}).addTo(map);

            const slider = document.getElementById('timeline-slider');
            const timeLbl = document.getElementById('current-time-lbl');
            const vidSecLbl = document.getElementById('lbl-vid-sec');
            const utcTimeLbl = document.getElementById('lbl-utc-time');
            const coordLbl = document.getElementById('lbl-coord');
            const btnPlay = document.getElementById('btn-play');

            function formatTime(totalSec) {{
                const m = Math.floor(totalSec / 60);
                const s = Math.floor(totalSec % 60);
                return `${{String(m).padStart(2, '0')}}:${{String(s).padStart(2, '0')}}`;
            }}

            function updatePosition(idx) {{
                const p = points[idx];
                if (!p) return;
                currentMarker.setLatLng([p.lat, p.lon]);
                timeLbl.textContent = formatTime(p.sec);
                vidSecLbl.textContent = `${{p.sec}}s / ${{points.length - 1}}s`;
                utcTimeLbl.textContent = p.time;
                coordLbl.textContent = `${{p.lat.toFixed(6)}}, ${{p.lon.toFixed(6)}}`;
            }}

            slider.addEventListener('input', (e) => {{
                updatePosition(parseInt(e.target.value));
            }});

            let isPlaying = false;
            let playInterval = null;

            btnPlay.addEventListener('click', () => {{
                if (isPlaying) {{
                    clearInterval(playInterval);
                    btnPlay.textContent = '▶ Play';
                    isPlaying = false;
                }} else {{
                    btnPlay.textContent = '⏸ Pause';
                    isPlaying = true;
                    playInterval = setInterval(() => {{
                        let cur = parseInt(slider.value);
                        if (cur >= points.length - 1) {{
                            cur = 0;
                        }} else {{
                            cur += 1;
                        }}
                        slider.value = cur;
                        updatePosition(cur);
                    }}, 60);
                }}
            }});
        }}

        document.addEventListener('DOMContentLoaded', initMap);
    </script>
</body>
</html>
"""
    os.makedirs(os.path.dirname(os.path.abspath(output_html_path)), exist_ok=True)
    with open(output_html_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    print(f"[StreetView] Wrote interactive preview map: {output_html_path} ({os.path.getsize(output_html_path)} bytes)")
    return output_html_path

def update_status(status_file, data):
    """Write progress dictionary atomically to status JSON file.

    Args:
        status_file: Destination JSON file path.
        data: Progress dictionary to serialize.
    """
    if not status_file:
        return
    data["pid"] = os.getpid()
    try:
        os.makedirs(os.path.dirname(os.path.abspath(status_file)), exist_ok=True)
    except Exception:
        pass
    tmp_file = status_file + ".tmp"
    success = False
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f)
        for _ in range(3):
            try:
                os.replace(tmp_file, status_file)
                success = True
                break
            except OSError:
                import time
                time.sleep(0.02)
    except Exception:
        pass
    if not success:
        try:
            with open(status_file, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception:
            pass

def process_streetview(
    input_video,
    output_video,
    output_gpx,
    mode="A",
    checkpoints=None,
    gpx_input_file=None,
    start_time_iso=None,
    time_offset_sec=0.0,
    auto_pad=True,
    additional_videos=None,
    output_map_html=None,
    bitrate="45M",
    strip_audio=True,
    start_coord=None,
    end_coord=None,
    smooth_gps=False,
    status_file=None,
    set_completed=True,
    start_ts=None
):
    """Process 360 video and GPS data to create Street View compliant video and GPX track.

    Concatenates multi-video segments, checks duration against Google Street View's
    2-minute minimum requirement (padding if necessary), injects equirectangular 360
    spatial metadata and creation timestamp, encodes to target bitrate, generates
    synchronized GPX 1.1 track file with 1 Hz cadence, and builds interactive HTML map.

    Args:
        input_video: Primary input video path or sequence of video paths.
        output_video: Destination path for Street View compliant MP4.
        output_gpx: Destination path for synchronized GPX track.
        mode: GPS input mode ('A' for checkpoints, 'B' for GPX file).
        checkpoints: List or text of map checkpoints (Mode A).
        gpx_input_file: Source GPX track file path (Mode B).
        start_time_iso: Optional ISO-8601 start timestamp override.
        time_offset_sec: Time synchronization offset in seconds.
        auto_pad: Whether to pad videos under 120s up to 126s.
        additional_videos: Optional extra video segments to concatenate.
        output_map_html: Optional path for interactive route preview HTML map.
        bitrate: Target video bitrate for FFmpeg re-encoding.
        strip_audio: Whether to remove audio stream from Street View output.
        start_coord: Optional coordinate string to find start point in GPX.
        end_coord: Optional coordinate string to find end point in GPX.
        smooth_gps: Whether to apply Gaussian filter to GPS coordinates.
        status_file: Optional path to status JSON file for tracking.
        set_completed: Whether to mark status as completed when finished.
        start_ts: Pipeline start timestamp for elapsed time calculation.

    Returns:
        Tuple of (output_video_path, output_gpx_path, output_map_html_path).
    """
    if start_ts is None:
        start_ts = time.time()
    # Normalize input video list
    input_videos_list = []
    if isinstance(input_video, (list, tuple)):
        input_videos_list = list(input_video)
    elif isinstance(input_video, str):
        if "," in input_video:
            input_videos_list = [v.strip() for v in input_video.split(",") if v.strip()]
        else:
            input_videos_list = [input_video.strip()]

    update_status(status_file, {
        "status": "exporting",
        "phase": "Preparing Street View export & inspecting input video...",
        "progress": 10.0,
        "speed": "N/A",
        "eta": "Calculating...",
        "elapsed": 0,
        "output": output_video
    })

    if additional_videos:
        if isinstance(additional_videos, (list, tuple)):
            input_videos_list.extend(additional_videos)
        elif isinstance(additional_videos, str):
            input_videos_list.extend([v.strip() for v in additional_videos.split(",") if v.strip()])

    for v in input_videos_list:
        if str(v).strip().startswith("-"):
            raise ValueError(f"Invalid video file path: {v!r} cannot start with a dash")
        if not os.path.exists(v):
            raise FileNotFoundError(f"Input video file not found: {v}")

    # If multiple video files, concatenate losslessly first
    effective_input_video = input_videos_list[0]
    temp_concat_video = None
    if len(input_videos_list) > 1:
        temp_dir = os.path.join(os.path.dirname(os.path.abspath(output_video)), "temp")
        os.makedirs(temp_dir, exist_ok=True)
        concat_txt = os.path.join(temp_dir, f"concat_list_{os.getpid()}.txt")
        temp_concat_video = os.path.join(temp_dir, f"concat_merged_{os.getpid()}.MP4")

        with open(concat_txt, "w", encoding="utf-8", newline="\n") as f:
            for v_path in input_videos_list:
                f.write(f"file '{os.path.abspath(v_path).replace(chr(92), '/')}'\n")

        ffmpeg_concat_bin = "ffmpeg"
        if callable(resolve_ffmpeg):
            try:
                info = resolve_ffmpeg()
                if info and info.get("path"):
                    ffmpeg_concat_bin = info["path"]
            except Exception:
                ffmpeg_concat_bin = "ffmpeg"

        print(f"[StreetView] Merging {len(input_videos_list)} video segments via stream copy...")
        concat_cmd = [ffmpeg_concat_bin, "-y", "-f", "concat", "-safe", "0", "-i", concat_txt, "-c", "copy", temp_concat_video]
        subprocess.run(concat_cmd, capture_output=True, text=True, check=True, creationflags=WIN_NO_WINDOW)
        effective_input_video = temp_concat_video

    duration, width, height, fps, creation_dt = get_video_info(effective_input_video)
    print(f"[StreetView] Input video info: duration={duration:.2f}s, resolution={width}x{height}, fps={fps:.2f}")

    target_duration = duration
    need_pad = False
    pad_duration = 0.0

    if auto_pad and duration < 120.0:
        need_pad = True
        target_duration = 126.0  # Pad to 126s so it's comfortably over 2 minutes
        pad_duration = target_duration - duration
        print(f"[StreetView] Video duration ({duration:.2f}s) is < 120s. Will pad final frame by {pad_duration:.2f}s to reach {target_duration:.2f}s.")

    # Determine start UTC datetime
    if start_time_iso:
        try:
            # Handle ISO string (e.g. 2026-07-21T00:00:00Z or space-separated)
            clean_str = start_time_iso.replace("Z", "").replace(" ", "T")
            start_utc_dt = datetime.datetime.fromisoformat(clean_str)
            if start_utc_dt.tzinfo is None:
                start_utc_dt = start_utc_dt.replace(tzinfo=datetime.timezone.utc)
        except Exception:
            start_utc_dt = creation_dt or datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    elif creation_dt:
        start_utc_dt = creation_dt
    else:
        start_utc_dt = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)

    if time_offset_sec != 0.0:
        start_utc_dt += datetime.timedelta(seconds=time_offset_sec)

    creation_time_str = start_utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Generate 1-second interval track points
    total_seconds = int(target_duration)
    track_points = []

    if mode == "A" and checkpoints:
        track_points = interpolate_checkpoints(checkpoints, total_seconds)
    elif mode == "B" and gpx_input_file:
        if not os.path.exists(gpx_input_file) and os.path.exists(os.path.join("data", "input", "gps", os.path.basename(gpx_input_file))):
            gpx_input_file = os.path.join("data", "input", "gps", os.path.basename(gpx_input_file))
        raw_gpx_points = []
        if os.path.exists(gpx_input_file):
            raw_gpx_points = parse_gpx_file(gpx_input_file)
        if raw_gpx_points:
            gpx_min_t = raw_gpx_points[0]['dt']
            gpx_max_t = raw_gpx_points[-1]['dt']

            start_pair = parse_coord_str(start_coord) if start_coord else None
            end_pair = parse_coord_str(end_coord) if end_coord else None

            # 1. Match by Start Coordinates if provided
            if start_pair:
                match_p, match_idx, match_dist = find_closest_gpx_point(raw_gpx_points, start_pair[0], start_pair[1])
                if match_p and match_p.get('dt'):
                    offset_from_first = (match_p['dt'] - gpx_min_t).total_seconds()
                    print(f"[StreetView] Auto-matched Start Coordinates ({start_pair[0]:.6f}, {start_pair[1]:.6f}) to GPX point #{match_idx} at {match_p['dt'].isoformat()} (offset: +{offset_from_first:.1f}s, accuracy: {match_dist:.1f}m).")
                    start_utc_dt = match_p['dt']
                    if time_offset_sec != 0.0:
                        start_utc_dt += datetime.timedelta(seconds=time_offset_sec)
                    creation_time_str = start_utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

                    if end_pair:
                        match_end_p, match_end_idx, match_end_dist = find_closest_gpx_point(raw_gpx_points, end_pair[0], end_pair[1])
                        if match_end_p and match_end_p.get('dt'):
                            expected_dur = (match_end_p['dt'] - match_p['dt']).total_seconds()
                            print(f"[StreetView] Matched End Coordinates ({end_pair[0]:.6f}, {end_pair[1]:.6f}) to point #{match_end_idx} (span: {expected_dur:.1f}s vs video: {duration:.1f}s, accuracy: {match_end_dist:.1f}m).")

            # 2. If no start_coord, check if video timestamp falls outside the GPX recording span and auto-align
            elif not start_time_iso and (start_utc_dt < gpx_min_t or start_utc_dt > gpx_max_t):
                print(f"[StreetView] Video timestamp ({start_utc_dt.isoformat()}) falls outside GPX range ({gpx_min_t.isoformat()} -> {gpx_max_t.isoformat()}). Auto-aligning video start to GPX track start.")
                start_utc_dt = gpx_min_t
                
                # Check for subclip offset in filename pattern like _0_60 or _120_240
                if time_offset_sec == 0.0 and isinstance(input_video, str):
                    stem = re.sub(r'(\.mp4|_stitched|_out_\d+)+$', '', os.path.basename(input_video), flags=re.IGNORECASE)
                    m_sub = re.search(r'_(\d+)_(\d+)$', stem)
                    if m_sub:
                        sub_start = float(m_sub.group(1))
                        if sub_start > 0:
                            start_utc_dt += datetime.timedelta(seconds=sub_start)
                            print(f"[StreetView] Detected sub-clip start offset from filename range [{m_sub.group(1)}s -> {m_sub.group(2)}s]: +{sub_start:.1f}s")
                elif time_offset_sec != 0.0:
                    start_utc_dt += datetime.timedelta(seconds=time_offset_sec)
                
                creation_time_str = start_utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

            track_points = sync_gpx_by_timestamps(raw_gpx_points, start_utc_dt, total_seconds)
        else:
            print("[StreetView] Warning: GPX file yielded 0 points. Fallback to default coordinates.", file=sys.stderr)

    if not track_points:
        err_msg = "StreetView export failed: No checkpoints or GPX points given."
        print(f"[StreetView] Error: {err_msg}", file=sys.stderr)
        if status_file:
            update_status(status_file, {"status": "failed", "error": err_msg})
        raise ValueError(err_msg)

    if smooth_gps and track_points:
        print("[StreetView] Applying weighted Gaussian filter to smooth GPS jitter and micro-kinks.")
        track_points = smooth_track_points(track_points, iterations=2)

    # 1. Write GPX File
    update_status(status_file, {
        "status": "exporting",
        "phase": "Generating GPX track file & syncing timestamps...",
        "progress": 30.0,
        "speed": "N/A",
        "eta": "Calculating...",
        "elapsed": int(time.time() - start_ts),
        "output": output_video
    })
    gpx_xml = build_gpx_xml(track_points, start_utc_dt)
    os.makedirs(os.path.dirname(os.path.abspath(output_gpx)), exist_ok=True)
    with open(output_gpx, "w", encoding="utf-8", newline="\n") as f:
        f.write(gpx_xml)
    print(f"[StreetView] Wrote GPX track file: {output_gpx} ({os.path.getsize(output_gpx)} bytes)")

    # 2. Render Padded & Metadata-tagged Output Video
    os.makedirs(os.path.dirname(os.path.abspath(output_video)), exist_ok=True)

    ffmpeg_render_bin = "ffmpeg"
    if callable(resolve_ffmpeg):
        try:
            info = resolve_ffmpeg()
            if info and info.get("path"):
                ffmpeg_render_bin = info["path"]
        except Exception:
            ffmpeg_render_bin = "ffmpeg"

    ffmpeg_cmd = [ffmpeg_render_bin, "-y", "-i", effective_input_video]
    filter_complex = []

    if need_pad:
        filter_complex.append(f"tpad=stop_duration={pad_duration:.2f}:stop_mode=clone")

    filter_args = []
    if filter_complex:
        filter_args = ["-vf", ",".join(filter_complex)]

    meta_args = [
        "-metadata", "Spherical=true",
        "-metadata", "Stitched=true",
        "-metadata", "ProjectionType=equirectangular",
        "-metadata", f"creation_time={creation_time_str}",
        "-metadata:s:v", f"creation_time={creation_time_str}"
    ]
    if not strip_audio:
        meta_args.append("-metadata:s:a")
        meta_args.append(f"creation_time={creation_time_str}")

    b_clean = str(bitrate).strip().lower() if bitrate else "45m"
    audio_args = ["-an"] if strip_audio else ["-c:a", "copy"]

    mode_desc = "lossless stream copy" if (b_clean == "copy" and not need_pad) else "encoding"
    update_status(status_file, {
        "status": "exporting",
        "phase": f"Exporting Street View MP4 ({mode_desc}) with 360 metadata...",
        "progress": 55.0,
        "speed": "N/A",
        "eta": "Calculating...",
        "elapsed": 0,
        "output": output_video
    })

    # Render target (use temporary target if overwriting same input file)
    in_place = os.path.abspath(effective_input_video) == os.path.abspath(output_video)
    render_target = (output_video + ".tmp_render.mp4") if in_place else output_video

    if b_clean == "copy" and not need_pad:
        # Lossless stream copy: preserve original video stream without re-encoding
        video_stream_copy = ["-c:v", "copy"] + (["-an"] if strip_audio else ["-c:a", "copy"])
        cmd = ffmpeg_cmd + video_stream_copy + ["-write_tmcd", "0", "-movflags", "+faststart"] + meta_args + [render_target]
    else:
        # Parse bitrate into standard kbps
        b_upper = b_clean.upper()
        if b_upper.endswith("M"):
            try: b_k = int(float(b_upper[:-1]) * 1000)
            except Exception: b_k = 45000
        elif b_upper.endswith("K"):
            try: b_k = int(float(b_upper[:-1]))
            except Exception: b_k = 45000
        elif b_upper.isdigit():
            v = float(b_upper)
            b_k = int(v * 1000) if v < 1000 else int(v)
        else:
            b_k = 45000

        b_val = f"{b_k}k"
        max_val = f"{int(b_k * 1.25)}k"
        buf_val = f"{int(b_k * 1.6)}k"

        # NVENC command with optimized Google Street View high-fidelity encoding parameters
        cmd = ffmpeg_cmd + filter_args + [
            "-c:v", "h264_nvenc",
            "-preset", "p4",
            "-profile:v", "high",
            "-cq", "16",
            "-b:v", b_val,
            "-maxrate", max_val,
            "-bufsize", buf_val,
            "-spatial-aq", "1",
            "-temporal-aq", "1",
            "-rc-lookahead", "32",
            "-pix_fmt", "yuv420p",
            "-color_primaries", "bt709",
            "-color_trc", "bt709",
            "-colorspace", "bt709"
        ] + audio_args + ["-write_tmcd", "0", "-movflags", "+faststart"] + meta_args + [render_target]

    def _run_ffmpeg_sv(f_cmd, target_total_sec, s_file, s_ts):
        if "-progress" not in f_cmd:
            f_cmd = [f_cmd[0], "-progress", "-"] + f_cmd[1:]
        proc_pipe = subprocess.Popen(
            f_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            creationflags=WIN_NO_WINDOW
        )
        time_regex = re.compile(r'time=(\d+):(\d+):(\d+(?:\.\d+)?)', re.IGNORECASE)
        speed_regex = re.compile(r'speed=\s*([\d\.]+)x', re.IGNORECASE)
        last_upd = 0.0
        cur_speed = "1.0x"
        cur_eta = "Calculating..."
        while True:
            line = proc_pipe.stdout.readline()
            if not line:
                break
            m_spd = speed_regex.search(line)
            if m_spd:
                cur_speed = f"{m_spd.group(1)}x"
            m_time = time_regex.search(line)
            if m_time and target_total_sec > 0:
                h, m, s = float(m_time.group(1)), float(m_time.group(2)), float(m_time.group(3))
                cur_sec = h * 3600 + m * 60 + s
                now = time.time()
                if now - last_upd >= 0.5:
                    last_upd = now
                    pct = min(92.0, 35.0 + (cur_sec / float(target_total_sec)) * 57.0)
                    rem_sec = max(0.0, float(target_total_sec) - cur_sec)
                    try:
                        spd_val = float(m_spd.group(1)) if m_spd else 1.0
                        eta_val = int(rem_sec / max(0.1, spd_val))
                        cur_eta = f"{eta_val // 60:02d}:{eta_val % 60:02d}"
                    except Exception:
                        pass
                    update_status(s_file, {
                        "status": "exporting",
                        "phase": f"Generating Google Street View MP4 & GPX ({cur_sec:.0f}s / {target_total_sec:.0f}s)...",
                        "progress": round(pct, 1),
                        "speed": cur_speed,
                        "eta": cur_eta,
                        "elapsed": int(now - s_ts),
                        "output": output_video
                    })
        proc_pipe.wait()
        return proc_pipe.returncode

    print(f"[StreetView] Running FFmpeg command: {' '.join(cmd)}")
    rc = _run_ffmpeg_sv(cmd, total_seconds, status_file, start_ts)
    if rc != 0:
        # Fallback to libx264 CPU encoder if NVENC is unavailable
        if b_clean != "copy" or need_pad or strip_audio:
            cmd = ffmpeg_cmd + filter_args + [
                "-c:v", "libx264",
                "-preset", "fast",
                "-b:v", b_val,
                "-maxrate", max_val,
                "-bufsize", buf_val,
                "-pix_fmt", "yuv420p"
            ] + audio_args + ["-write_tmcd", "0", "-movflags", "+faststart"] + meta_args + [render_target]
            print(f"[StreetView] NVENC failed, retrying with libx264: {' '.join(cmd)}")
            rc = _run_ffmpeg_sv(cmd, total_seconds, status_file, start_ts)
            if rc != 0:
                raise RuntimeError(f"FFmpeg Street View export failed with code {rc}")

    if in_place and os.path.exists(render_target) and os.path.getsize(render_target) > 0:
        shutil.move(render_target, output_video)


    # 3. Spatialmedia Metadata Injection Pass as additional guarantee
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        root_dir = os.path.dirname(script_dir)
        sys.path.insert(0, script_dir)
        from spatialmedia import metadata_utils
        meta = metadata_utils.Metadata()
        meta.video = metadata_utils.generate_spherical_xml(projection='equirectangular')
        temp_inj = output_video + ".tmp_inj.mp4"
        metadata_utils.inject_metadata(output_video, temp_inj, meta, lambda m: None)
        if os.path.exists(temp_inj) and os.path.getsize(temp_inj) > 0:
            shutil.move(temp_inj, output_video)
    except Exception as e:
        print(f"[StreetView] Spatialmedia injection warning: {e}")

    # 4. Generate Interactive Route Inspector Map HTML
    if not output_map_html:
        base_no_ext = os.path.splitext(output_video)[0]
        output_map_html = base_no_ext + "_map.html"
    update_status(status_file, {
        "status": "exporting",
        "phase": "Building interactive satellite route inspector map...",
        "progress": 95.0,
        "speed": "N/A",
        "eta": "Calculating...",
        "elapsed": int(time.time() - start_ts),
        "output": output_video
    })
    try:
        title = os.path.splitext(os.path.basename(output_video))[0]
        build_map_html(track_points, start_utc_dt, output_map_html, title)
    except Exception as e:
        print(f"[StreetView] Warning generating map HTML: {e}")

    if set_completed:
        update_status(status_file, {
            "status": "completed",
            "phase": "Google Street View export completed!",
            "progress": 100.0,
            "speed": "Finished",
            "eta": "N/A",
            "output": output_video,
            "streetview_video": output_video,
            "streetview_gpx": output_gpx,
            "streetview_map": output_map_html,
            "elapsed": int(time.time() - start_ts)
        })
    else:
        update_status(status_file, {
            "status": "exporting",
            "phase": "Google Street View export completed!",
            "progress": 98.0,
            "speed": "Finished",
            "eta": "00:00",
            "elapsed": int(time.time() - start_ts),
            "output": output_video,
            "streetview_video": output_video,
            "streetview_gpx": output_gpx,
            "streetview_map": output_map_html
        })

    print(f"[StreetView] Successfully created Street View video: {output_video} ({os.path.getsize(output_video)} bytes)")
    return output_video, output_gpx, output_map_html

def run_self_test():
    """Run internal test suite verifying checkpoint interpolation, elevation, and GPX sync."""
    print("[StreetView Test] Testing coordinate interpolation, elevation, and HH:MM:SS parsing...")
    test_lines = """
    00:00:00, 40.712776, -74.005974, 200.0
    00:01:00, 40.713000, -74.005000, 210.0m
    00:02:00, 40.713500, -74.004000, 220.0
    """
    cps = parse_checkpoint_list(test_lines)
    assert len(cps) == 3
    assert cps[0]['time'] == 0.0 and cps[0]['lat'] == 40.712776 and cps[0]['ele'] == 200.0
    assert cps[1]['time'] == 60.0 and cps[1]['lat'] == 40.713000 and cps[1]['ele'] == 210.0
    assert cps[2]['time'] == 120.0 and cps[2]['lat'] == 40.713500 and cps[2]['ele'] == 220.0

    # Test Google Maps URL checkpoint
    url_test = "00:00:01, https://www.google.com/maps/place/.../@40.7812000,-73.9665000,40m/data=test"
    cps_url = parse_checkpoint_list(url_test)
    assert len(cps_url) == 1
    assert cps_url[0]['time'] == 1.0
    assert abs(cps_url[0]['lat'] - 40.7812000) < 1e-6
    assert abs(cps_url[0]['lon'] - -73.9665000) < 1e-6
    assert cps_url[0]['ele'] == 40.0

    pts = interpolate_checkpoints(cps, 120)
    assert len(pts) == 121
    assert pts[0]['lat'] == 40.712776 and pts[0]['ele'] == 200.0
    assert pts[60]['lat'] == 40.713000 and pts[60]['ele'] == 210.0
    assert pts[120]['lat'] == 40.713500 and pts[120]['ele'] == 220.0
    print("[StreetView Test] Interpolation with elevation passed! 121 track points verified.")

    dt_start = datetime.datetime(2026, 7, 21, 0, 0, 0, tzinfo=datetime.timezone.utc)
    xml_str = build_gpx_xml(pts[:5], dt_start)
    assert "<trkpt" in xml_str and "2026-07-21T00:00:00Z" in xml_str and "<ele>200.000</ele>" in xml_str
    print("[StreetView Test] GPX XML construction with 3D elevation passed!")

    print("[StreetView Test] Testing GPX timestamp window synchronization...")
    # Mock GPX track with points every 10 seconds starting at 10:00:00 UTC
    t0 = datetime.datetime(2026, 8, 17, 10, 0, 0, tzinfo=datetime.timezone.utc)
    mock_gpx = [
        {'lat': 46.5000, 'lon': 22.4000, 'dt': t0, 'time_str': '2026-08-17T10:00:00Z'},
        {'lat': 46.5100, 'lon': 22.4100, 'dt': t0 + datetime.timedelta(seconds=60), 'time_str': '2026-08-17T10:01:00Z'},
        {'lat': 46.5200, 'lon': 22.4200, 'dt': t0 + datetime.timedelta(seconds=120), 'time_str': '2026-08-17T10:02:00Z'},
        {'lat': 46.5300, 'lon': 22.4300, 'dt': t0 + datetime.timedelta(seconds=180), 'time_str': '2026-08-17T10:03:00Z'},
    ]
    # Video recorded from 10:01:00 to 10:02:30 (90 seconds)
    video_start = t0 + datetime.timedelta(seconds=60)
    synced = sync_gpx_by_timestamps(mock_gpx, video_start, 90)
    assert len(synced) == 91
    # Check start point matches 10:01:00 point
    assert abs(synced[0]['lat'] - 46.5100) < 1e-5
    # Check midpoint (30 seconds in) is halfway between 10:01:00 and 10:02:00
    assert abs(synced[30]['lat'] - 46.5150) < 1e-5
    # Check 60s point matches 10:02:00 point
    assert abs(synced[60]['lat'] - 46.5200) < 1e-5
    # Check 90s point matches halfway between 10:02:00 and 10:03:00
    assert abs(synced[90]['lat'] - 46.5250) < 1e-5
    print("[StreetView Test] Timestamp window synchronization passed! 91 interpolated points verified.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Google Street View 360 Video & GPX Generator")
    parser.add_argument("--input", help="Path to completed stitched 360 MP4 video")
    parser.add_argument("--output", help="Path for separate Street View MP4 output")
    parser.add_argument("--gpx-output", help="Path for output GPX track file")
    parser.add_argument("--output-map", help="Path for output interactive preview map HTML")
    parser.add_argument("--mode", choices=["A", "B"], default="A", help="GPS mode: A (Checkpoints) or B (GPX file)")
    parser.add_argument("--checkpoints", help="JSON string, text lines, or file path containing checkpoint lat/lon list")
    parser.add_argument("--gpx-file", help="Input GPX file path for Mode B")
    parser.add_argument("--start-time", help="ISO start UTC timestamp (e.g. 2026-07-21T00:00:00Z)")
    parser.add_argument("--time-offset", type=float, default=0.0, help="Time offset in seconds")
    parser.add_argument("--no-auto-pad", action="store_true", help="Disable auto-padding videos under 2 minutes")
    parser.add_argument("--bitrate", default="45M", help="Target video bitrate for Street View export (e.g. 45M, 30M, 60M, 80M, or copy)")
    parser.add_argument("--strip-audio", action="store_true", default=True, help="Remove audio track from Street View output")
    parser.add_argument("--start-coord", help="Start coordinate string 'lat, lon' to auto-match start point in GPX track")
    parser.add_argument("--end-coord", help="Optional end coordinate string 'lat, lon' to verify route span in GPX track")
    parser.add_argument("--smooth-gps", action="store_true", help="Apply Gaussian smoothing filter to eliminate GPS sensor jitter and micro-kinks")
    parser.add_argument("--status-file", help="Path to status JSON file for UI progress tracking")
    parser.add_argument("--test", action="store_true", help="Run self-test suite")

    args = parser.parse_args()

    if args.test:
        run_self_test()
        sys.exit(0)

    if not args.input or not args.output or not args.gpx_output:
        parser.print_help()
        sys.exit(1)

    cps = parse_checkpoint_list(args.checkpoints) if (args.mode == "A" and args.checkpoints) else []

    process_streetview(
        input_video=args.input,
        output_video=args.output,
        output_gpx=args.gpx_output,
        mode=args.mode,
        checkpoints=cps,
        gpx_input_file=args.gpx_file,
        start_time_iso=args.start_time,
        time_offset_sec=args.time_offset,
        auto_pad=not args.no_auto_pad,
        output_map_html=args.output_map,
        bitrate=args.bitrate,
        strip_audio=args.strip_audio,
        start_coord=args.start_coord,
        end_coord=args.end_coord,
        smooth_gps=args.smooth_gps,
        status_file=args.status_file
    )
