import sys
sys.dont_write_bytecode = True
import os
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

"""Original Camera Video Crop & Telemetry Atom Slicer.

Trims dual-fisheye camera videos while slicing and preserving internal
telemetry metadata atoms (moov/udta/vrot) frame-for-frame to prevent
desynchronization of IMU gyro orientation tracks.
"""
import struct
import subprocess
import json
import argparse

WIN_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from utils.mp4_utils import get_video_properties, find_box

def get_actual_start_frame(input_path, requested_start_time, fps):
    """Calculates keyframe-aligned start frame index for lossless video cropping.

    Probes GOP keyframe boundaries around requested start time using ffprobe
    to ensure stream copy trims begin cleanly on an IDR/keyframe.

    Args:
        input_path (str): Filepath to the source video.
        requested_start_time (float): Requested start time in seconds.
        fps (float): Video framerate.

    Returns:
        int: Best keyframe-aligned start frame index.
    """
    if requested_start_time <= 0:
        return 0
    read_start = max(0.0, requested_start_time - 10.0)
    read_end = requested_start_time + 2.0
    cmd = [
        "ffprobe", "-v", "error",
        "-read_intervals", f"{read_start:.3f}%{read_end:.3f}",
        "-select_streams", "v:0",
        "-show_entries", "packet=pts_time,flags",
        "-of", "json",
        input_path
    ]
    try:
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, creationflags=WIN_NO_WINDOW).decode('utf-8').strip()
        data = json.loads(output)
        packets = data.get("packets", [])
        best_kf_pts = 0.0
        for pkt in packets:
            pts = float(pkt.get("pts_time", 0.0))
            flags = pkt.get("flags", "")
            if "K" in flags:
                if pts <= requested_start_time + 0.01:
                    best_kf_pts = pts
        return int(round(best_kf_pts * fps))
    except Exception as e:
        print(f"Warning: Could not determine keyframe alignment: {e}", file=sys.stderr)
        return int(round(requested_start_time * fps))

def crop_udta(udta_bytes, start_frame, end_frame):
    """Slices binary udta box children, truncating the vrot atom frame samples.

    Extracts 24-byte per-frame vrot gyro orientation samples within [start_frame, end_frame]
    and repacks the udta box container.

    Args:
        udta_bytes (bytes | bytearray): Raw bytes of the source udta MP4 atom.
        start_frame (int): Starting frame index (inclusive).
        end_frame (int): Ending frame index (exclusive, or -1 for end of video).

    Returns:
        bytes: Repacked binary udta box containing sliced telemetry.
    """
    # udta_bytes starts with size and type (8 bytes)
    offset = 8
    end = len(udta_bytes)
    new_children = bytearray()
    
    while offset < end:
        if offset + 8 > end:
            break
        size, box_type = struct.unpack(">I4s", udta_bytes[offset:offset+8])
        box_type_str = box_type.decode("latin1")
        
        box_data = udta_bytes[offset : offset + size]
        
        if box_type_str == "vrot":
            version_flags = box_data[8:12]
            payload = box_data[12:]
            # Each sample is 24 bytes
            total_samples = len(payload) // 24
            
            s_frame = start_frame
            e_frame = end_frame if end_frame > 0 else total_samples
            
            s_frame = max(0, min(s_frame, total_samples))
            e_frame = max(s_frame, min(e_frame, total_samples))
            
            cropped_payload = payload[s_frame * 24 : e_frame * 24]
            new_box_content = version_flags + cropped_payload
            new_size = len(new_box_content) + 8
            new_box = struct.pack(">I4s", new_size, b"vrot") + new_box_content
            new_children.extend(new_box)
            print(f"Cropped 'vrot' from {total_samples} samples to {len(cropped_payload)//24} samples.")
        else:
            new_children.extend(box_data)
            print(f"Copied box '{box_type_str}' as-is ({size} bytes).")
            
        offset += size
        
    new_udta_size = len(new_children) + 8
    new_udta = struct.pack(">I4s", new_udta_size, b"udta") + new_children
    return new_udta

def patch_file(src_original, dst_converted, out_name, start_frame, end_frame):
    """Injects cropped source udta/vrot telemetry into a trimmed destination MP4 container.

    Reconstructs the moov atom header with updated byte sizes, embedding
    frame-synchronized telemetry into the output file.

    Args:
        src_original (str): Source video containing original telemetry atoms.
        dst_converted (str): Trimmed intermediate video lacking telemetry.
        out_name (str): Destination path for final patched MP4 file.
        start_frame (int): Start frame index used during video trimming.
        end_frame (int): End frame index used during video trimming.

    Returns:
        bool: True if patching and file output succeeded, False otherwise.
    """
    src_size = os.path.getsize(src_original)
    dst_size = os.path.getsize(dst_converted)

    # 1. Locate and extract udta from source
    with open(src_original, "rb") as f_src:
        src_udta_info = find_box(f_src, 0, src_size, ["moov", "udta"])
        if not src_udta_info:
            print("Warning: Could not find 'moov/udta' box in source file. Skipping telemetry patch.")
            import shutil
            shutil.copy2(dst_converted, out_name)
            return True
            
        src_udta_offset, src_udta_size = src_udta_info
        f_src.seek(src_udta_offset)
        src_udta_bytes = f_src.read(src_udta_size)

    # Crop the udta telemetry data
    patched_udta_bytes = crop_udta(src_udta_bytes, start_frame, end_frame)
    src_udta_size = len(patched_udta_bytes)

    # 2. Locate moov and udta in destination
    with open(dst_converted, "rb") as f_dst:
        dst_moov_info = find_box(f_dst, 0, dst_size, ["moov"])
        if not dst_moov_info:
            print("Error: Could not find 'moov' box in destination file.", file=sys.stderr)
            return False
        dst_moov_offset, dst_moov_size = dst_moov_info

        dst_udta_info = find_box(f_dst, 0, dst_size, ["moov", "udta"])
        
        f_dst.seek(0)
        if dst_udta_info:
            dst_udta_offset, dst_udta_size = dst_udta_info
            
            # Read part1 (up to dst_udta_offset)
            part1 = f_dst.read(dst_udta_offset)
            
            # Read part2 (after dst_udta_offset + dst_udta_size)
            f_dst.seek(dst_udta_offset + dst_udta_size)
            part2 = f_dst.read()
            
            new_moov_size = dst_moov_size - dst_udta_size + src_udta_size
        else:
            dst_udta_size = 0
            dst_udta_offset = dst_moov_offset + dst_moov_size
            
            part1 = f_dst.read(dst_udta_offset)
            part2 = f_dst.read()
            
            new_moov_size = dst_moov_size + src_udta_size

    # 3. Construct new file content in memory
    new_data = bytearray(part1) + patched_udta_bytes + part2

    # Overwrite the moov box size field in the new data
    f_dst = open(dst_converted, "rb")
    f_dst.seek(dst_moov_offset)
    moov_header = f_dst.read(8)
    size_field, = struct.unpack(">I", moov_header[0:4])
    f_dst.close()

    if size_field == 1:
        new_size_bytes = struct.pack(">Q", new_moov_size)
        new_data[dst_moov_offset + 8 : dst_moov_offset + 16] = new_size_bytes
    else:
        new_size_bytes = struct.pack(">I", new_moov_size)
        new_data[dst_moov_offset : dst_moov_offset + 4] = new_size_bytes

    # 4. Write new file
    with open(out_name, "wb") as f_out:
        f_out.write(new_data)

    print(f"Successfully wrote patched file to {out_name}")
    return True

def main():
    """CLI entry point for video cropping with synchronized telemetry preservation."""
    parser = argparse.ArgumentParser(description="Crop Original Gear 360 camera video and patch telemetry.")
    parser.add_argument("--input", required=True, help="Input original camera video")
    parser.add_argument("--output", required=True, help="Output cropped camera video")
    parser.add_argument("--start_frame", type=int, default=0, help="Start frame index")
    parser.add_argument("--end_frame", type=int, default=-1, help="End frame index")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Error: Input video '{args.input}' does not exist.", file=sys.stderr)
        sys.exit(1)

    print(f"Probing: {args.input}")
    props = get_video_properties(args.input)
    if not props:
        print("Error: Failed to probe video properties.", file=sys.stderr)
        sys.exit(1)

    fps = props["fps"]
    duration = props["duration"]
    total_frames = int(round(duration * fps))
    print(f"Duration: {duration:.3f}s, FPS: {fps:.3f}, Total Frames: {total_frames}")

    start_time = args.start_frame / fps
    end_time = args.end_frame / fps if args.end_frame > 0 else duration

    print(f"Cropping from frame {args.start_frame} ({start_time:.3f}s) to frame {args.end_frame} ({end_time:.3f}s)...")

    actual_start_frame = get_actual_start_frame(args.input, start_time, fps) if args.start_frame > 0 else 0
    if actual_start_frame != args.start_frame:
        print(f"Adjusted start frame to nearest keyframe: frame {actual_start_frame} ({actual_start_frame/fps:.3f}s)")

    temp_out = f"temp_crop_{os.getpid()}.mp4"

    # Run FFmpeg stream copy
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start_time:.6f}",
        "-to", f"{end_time:.6f}",
        "-i", args.input,
        "-c", "copy",
        "-map", "0",
        temp_out
    ]
    print(f"Running FFmpeg: {' '.join(cmd)}")
    rc = subprocess.call(cmd, creationflags=WIN_NO_WINDOW)
    if rc != 0 or not os.path.exists(temp_out):
        print("Error: FFmpeg trim failed.", file=sys.stderr)
        sys.exit(1)

    temp_props = get_video_properties(temp_out)
    if temp_props:
        output_frames = int(round(temp_props["duration"] * fps))
        actual_end_frame = args.start_frame + output_frames
    else:
        actual_end_frame = args.end_frame

    # Patch telemetry
    success = patch_file(args.input, temp_out, args.output, args.start_frame, actual_end_frame)

    if not success:
        sys.exit(1)

    print("Cropping and telemetry patching successfully completed!")

if __name__ == "__main__":
    main()
