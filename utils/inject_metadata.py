import sys, os
sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'

"""MP4 atom container metadata injection and user data (udta) atom transfer.

Extracts 'moov/udta' private metadata atom from an original source video
and copies or appends it into a converted or stabilized target MP4 container,
updating the container box sizes.
"""

import struct

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from utils.mp4_utils import find_box

def main():
    """Execute command-line interface for MP4 user data (udta) atom transfer."""
    if len(sys.argv) < 4:
        print("Usage: python -B utils/inject_metadata.py <src_original.mp4> <dst_converted.mp4> <output.mp4>")
        return

    src_name = sys.argv[1]
    dst_name = sys.argv[2]
    out_name = sys.argv[3]

    print(f"Source (with metadata): {src_name}")
    print(f"Destination (converted): {dst_name}")
    print(f"Output: {out_name}")

    if not os.path.exists(src_name):
        print(f"Error: Source file {src_name} does not exist.")
        return
    if not os.path.exists(dst_name):
        print(f"Error: Destination file {dst_name} does not exist.")
        return

    src_size = os.path.getsize(src_name)
    dst_size = os.path.getsize(dst_name)

    # 1. Locate and extract udta from source
    with open(src_name, "rb") as f_src:
        src_udta_info = find_box(f_src, 0, src_size, ["moov", "udta"])
        if not src_udta_info:
            print("Error: Could not find 'moov/udta' box in source file.")
            return
        src_udta_offset, src_udta_size = src_udta_info
        print(f"Found source 'udta' at offset {src_udta_offset} with size {src_udta_size}")
        f_src.seek(src_udta_offset)
        src_udta_bytes = f_src.read(src_udta_size)

    # 2. Locate moov and udta in destination
    with open(dst_name, "rb") as f_dst:
        dst_moov_info = find_box(f_dst, 0, dst_size, ["moov"])
        if not dst_moov_info:
            print("Error: Could not find 'moov' box in destination file.")
            return
        dst_moov_offset, dst_moov_size = dst_moov_info
        print(f"Found destination 'moov' at offset {dst_moov_offset} with size {dst_moov_size}")

        dst_udta_info = find_box(f_dst, 0, dst_size, ["moov", "udta"])
        
        f_dst.seek(0)
        
        if dst_udta_info:
            dst_udta_offset, dst_udta_size = dst_udta_info
            print(f"Found destination 'udta' at offset {dst_udta_offset} with size {dst_udta_size}")
            
            # Read part1 (up to dst_udta_offset)
            f_dst.seek(0)
            part1 = f_dst.read(dst_udta_offset)
            
            # Read part2 (after dst_udta_offset + dst_udta_size)
            f_dst.seek(dst_udta_offset + dst_udta_size)
            part2 = f_dst.read()
            
            # New moov size
            new_moov_size = dst_moov_size - dst_udta_size + src_udta_size
        else:
            print("Destination has no 'udta' box in 'moov'. Appending it to the end of 'moov'.")
            dst_udta_size = 0
            dst_udta_offset = dst_moov_offset + dst_moov_size
            
            f_dst.seek(0)
            part1 = f_dst.read(dst_udta_offset)
            part2 = f_dst.read()
            
            new_moov_size = dst_moov_size + src_udta_size

    # 3. Construct new file content in memory
    new_data = bytearray(part1) + src_udta_bytes + part2

    # Overwrite the moov box size field in the new data
    # Check if moov size is 32-bit or 64-bit in destination
    f_dst = open(dst_name, "rb")
    f_dst.seek(dst_moov_offset)
    moov_header = f_dst.read(8)
    size_field, = struct.unpack(">I", moov_header[0:4])
    f_dst.close()

    if size_field == 1:
        # 64-bit size
        print(f"Updating 64-bit 'moov' size to {new_moov_size}")
        new_size_bytes = struct.pack(">Q", new_moov_size)
        new_data[dst_moov_offset + 8 : dst_moov_offset + 16] = new_size_bytes
    else:
        # 32-bit size
        print(f"Updating 32-bit 'moov' size to {new_moov_size}")
        new_size_bytes = struct.pack(">I", new_moov_size)
        new_data[dst_moov_offset : dst_moov_offset + 4] = new_size_bytes

    # 4. Write new file
    with open(out_name, "wb") as f_out:
        f_out.write(new_data)

    print(f"Successfully wrote patched file to {out_name}")

if __name__ == "__main__":
    main()
