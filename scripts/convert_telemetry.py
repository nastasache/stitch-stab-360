#!/usr/bin/env python3
import sys, os
sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'

"""Telemetry Format Converter (Samsung Gear 360 to Gyroflow GCSV).

Converts raw camera orientation telemetry (.txt) into standardized 200 Hz
Gyroflow-compatible GCSV logs with angular rates (gx, gy, gz) and gravity
acceleration vectors (ax, ay, az).
"""

import math

def convert_telemetry(input_path, target_rate=200.0):
    """Converts camera orientation angles into 200 Hz Gyroflow GCSV logs.

    Differentiates Euler angles into angular velocity rates, computes normalized
    gravity vectors, and linearly interpolates telemetry to target_rate. Outputs
    both `<base>.gcsv` and `<base>.MP4.gcsv` sidecars.

    Args:
        input_path (str): Path to raw tab/comma-separated telemetry text file.
        target_rate (float, optional): Target sample rate in Hz. Defaults to 200.0.

    Returns:
        bool: True if conversion and file writes succeeded, False otherwise.
    """
    if not os.path.exists(input_path):
        print(f"Error: {input_path} does not exist.")
        return False

    base_name = os.path.splitext(input_path)[0]
    out_gcsv = base_name + ".gcsv"

    rows = []
    with open(input_path, "r", encoding="utf-8") as f:
        for line in f:
            line_str = line.strip()
            if not line_str:
                continue
            parts = line_str.split("\t") if "\t" in line_str else (line_str.split(";") if ";" in line_str else line_str.split(","))
            rows.append(parts)

    if not rows:
        print("Error: Empty telemetry file.")
        return False

    headers = [h.strip() for h in rows[0]]
    data = rows[1:]

    t_idx = headers.index('Time(s)') if 'Time(s)' in headers else 0
    ax_idx = headers.index('AngleX(deg)') if 'AngleX(deg)' in headers else 1
    ay_idx = headers.index('AngleY(deg)') if 'AngleY(deg)' in headers else 2
    az_idx = headers.index('AngleZ(deg)') if 'AngleZ(deg)' in headers else 3

    orig_pts = []
    prev_t = None
    prev_r, prev_p, prev_y = None, None, None

    for row in data:
        if len(row) <= max(t_idx, ax_idx, ay_idx, az_idx):
            continue
        try:
            t = float(row[t_idx])
            r = float(row[ax_idx])
            p = float(row[ay_idx])
            y = float(row[az_idx])
        except ValueError:
            continue

        if prev_t is None:
            gx, gy, gz = 0.0, 0.0, 0.0
        else:
            dt = t - prev_t
            if dt > 0:
                dr = ((r - prev_r + 180) % 360) - 180
                dp = ((p - prev_p + 180) % 360) - 180
                dy = ((y - prev_y + 180) % 360) - 180
                gx = dr / dt
                gy = dp / dt
                gz = dy / dt
            else:
                gx, gy, gz = 0.0, 0.0, 0.0

        r_rad, p_rad = math.radians(r), math.radians(p)
        ax = math.sin(p_rad)
        ay = -math.sin(r_rad) * math.cos(p_rad)
        az = math.cos(r_rad) * math.cos(p_rad)
        orig_pts.append((t, gx, gy, gz, ax, ay, az))
        prev_t, prev_r, prev_p, prev_y = t, r, p, y

    if not orig_pts:
        print("Error: No data points parsed.")
        return False

    dt_target = 1.0 / target_rate
    max_t = orig_pts[-1][0]

    gcsv_official = [
        "GYROFLOW IMU LOG",
        "version,1.3",
        f"id,{os.path.basename(base_name)}",
        "orientation,Xyz",
        "tscale,1.0",
        "gscale,0.017453292519943295",
        "ascale,1.0",
        "t,gx,gy,gz,ax,ay,az"
    ]

    curr_orig_idx = 0
    n_orig = len(orig_pts)
    t_curr = 0.0

    while t_curr <= max_t:
        while curr_orig_idx < n_orig - 2 and orig_pts[curr_orig_idx + 1][0] < t_curr:
            curr_orig_idx += 1

        p0 = orig_pts[curr_orig_idx]
        p1 = orig_pts[min(curr_orig_idx + 1, n_orig - 1)]

        t0, t1 = p0[0], p1[0]
        span = t1 - t0
        factor = (t_curr - t0) / span if span > 0 else 0.0
        factor = max(0.0, min(1.0, factor))

        gx = p0[1] + factor * (p1[1] - p0[1])
        gy = p0[2] + factor * (p1[2] - p0[2])
        gz = p0[3] + factor * (p1[3] - p0[3])
        ax = p0[4] + factor * (p1[4] - p0[4])
        ay = p0[5] + factor * (p1[5] - p0[5])
        az = p0[6] + factor * (p1[6] - p0[6])

        gcsv_official.append(f"{t_curr:.6f},{gx:.6f},{gy:.6f},{gz:.6f},{ax:.6f},{ay:.6f},{az:.6f}")
        t_curr += dt_target

    with open(out_gcsv, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(gcsv_official) + "\n")

    video_base = base_name[:-len("_telemetry")] if base_name.endswith("_telemetry") else base_name
    sidecar_gcsv = video_base + ".MP4.gcsv"
    with open(sidecar_gcsv, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(gcsv_official) + "\n")

    print(f"Generated 200 Hz Gyroflow GCSV file: {out_gcsv}")
    print(f"Generated 200 Hz Gyroflow Sidecar file: {sidecar_gcsv}")
    return True

if __name__ == "__main__":
    fn = sys.argv[1] if len(sys.argv) > 1 else "360_0016_telemetry.txt"
    convert_telemetry(fn)
