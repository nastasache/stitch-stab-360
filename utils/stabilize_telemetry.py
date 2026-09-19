#!/usr/bin/env python3
import sys, os
sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'

"""Direct telemetry-driven 360° video stabilization via FFmpeg v360.

Parses Euler angles from standardized WitMotion / Samsung Gear 360 telemetry
files, generates relative rotational sendcmd commands, and executes FFmpeg
to counter-rotate the equirectangular projection sphere.
"""

import subprocess

WIN_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

def main():
    """Execute command-line interface for telemetry-driven video stabilization."""
    if len(sys.argv) < 3:
        print("Usage: python -B utils/stabilize_telemetry.py <stitched_video.mp4> <telemetry_file.txt> [output_stabilized.mp4]")
        sys.exit(1)
        
    stitched_video = sys.argv[1]
    telemetry_file = sys.argv[2]
    output_file = sys.argv[3] if len(sys.argv) > 3 else os.path.splitext(stitched_video)[0] + "_stabilized.mp4"
    
    if not os.path.exists(stitched_video):
        print(f"Error: Stitched video file {stitched_video} not found.")
        sys.exit(1)
        
    if not os.path.exists(telemetry_file):
        print(f"Error: Telemetry file {telemetry_file} not found.")
        sys.exit(1)
        
    # Read the telemetry file
    print("Reading telemetry data...")
    angles = []
    with open(telemetry_file, "r", encoding="utf-8") as f:
        lines = f.readlines()
        headers = [x.strip() for x in lines[0].split("\t")]
        
        # We need AngleX(deg), AngleY(deg), AngleZ(deg)
        try:
            roll_idx = headers.index("AngleX(deg)")
            pitch_idx = headers.index("AngleY(deg)")
            yaw_idx = headers.index("AngleZ(deg)")
        except ValueError:
            print("Error: Could not find Euler angle columns in telemetry header.")
            sys.exit(1)
            
        for line in lines[1:]:
            parts = line.split("\t")
            if len(parts) > max(roll_idx, pitch_idx, yaw_idx):
                roll = float(parts[roll_idx])
                pitch = float(parts[pitch_idx])
                yaw = float(parts[yaw_idx])
                angles.append((roll, pitch, yaw))
                
    if not angles:
        print("Error: No telemetry samples found.")
        sys.exit(1)
        
    # Get video duration/FPS
    print("Detecting video properties...")
    # Assume 29.97 fps (or we can estimate from sample count)
    fps = 29.970
    time_step = 1.0 / fps
    
    # Generate sendcmd.txt
    sendcmd_file = "sendcmd_stabilize.txt"
    print(f"Generating rotation commands in {sendcmd_file}...")
    
    # Lock first frame to 0
    start_roll, start_pitch, start_yaw = angles[0]
    
    with open(sendcmd_file, "w", encoding="utf-8") as out_f:
        prev_roll = 0.0
        prev_pitch = 0.0
        prev_yaw = 0.0
        for frame, (roll, pitch, yaw) in enumerate(angles):
            # To stabilize, we apply the INVERSE of the camera's physical rotation
            # Also normalize relative to the starting frame
            curr_roll = -(roll - start_roll)
            curr_pitch = -(pitch - start_pitch)
            curr_yaw = -(yaw - start_yaw)

            inc_roll = curr_roll - prev_roll
            inc_pitch = curr_pitch - prev_pitch
            inc_yaw = curr_yaw - prev_yaw

            prev_roll = curr_roll
            prev_pitch = curr_pitch
            prev_yaw = curr_yaw

            start_t = frame * time_step
            end_t = (frame + 1) * time_step
            
            # Write rotation command for v360 filter
            out_f.write(
                f"{start_t:.6f}-{end_t:.6f} [enter] v360 "
                f"yaw {inc_yaw:.6f}, pitch {inc_pitch:.6f}, roll {inc_roll:.6f};\n"
            )
            
    # Run FFmpeg to apply 3D stabilization to the equirectangular sphere
    print("Stabilizing 360° video sphere using FFmpeg...")
    cmd = [
        "ffmpeg", "-y",
        "-i", stitched_video,
        "-vf", f"sendcmd=f='{sendcmd_file}',v360=input=equirect:output=equirect",
        "-c:v", "libx264", "-preset", "medium", "-crf", "17",
        "-c:a", "copy",
        output_file
    ]
    
    print(f"Running command: {' '.join(cmd)}")
    subprocess.run(cmd, creationflags=WIN_NO_WINDOW)
    print(f"Successfully stabilized! Output saved to: {output_file}")

if __name__ == "__main__":
    main()
