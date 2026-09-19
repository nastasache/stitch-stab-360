import sys, os
sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
"""Low-level MP4 container box and atom inspection utility.

Recursively traverses ISOBMFF/QuickTime atom headers (`moov`, `trak`, `mdia`,
`minf`, `stbl`, `udta`, `meta`, `uuid`), reporting box byte offsets, total payload
sizes, and extended UUID byte signatures.
"""

import struct

def parse_boxes(f, offset, end, indent=""):
    """Recursively parse and display binary ISO BMFF box headers and hierarchy.

    Args:
        f: Open binary file stream handle.
        offset: Starting byte offset in file.
        end: Ending byte boundary offset.
        indent: Indentation whitespace for nested container hierarchy.
    """
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
                # Extends to end of file
                box_size = end - offset

            # Check if this is a container box
            container_types = ["moov", "trak", "mdia", "minf", "stbl", "udta", "meta"]
            
            uuid_str = ""
            if box_type_str == "uuid":
                uuid_bytes = f.read(16)
                uuid_str = " " + uuid_bytes.hex()
                header_len += 16

            print(f"{indent}{box_type_str} size={box_size}{uuid_str} offset={offset}")

            if box_type_str in container_types:
                # Recurse inside
                sub_offset = offset + header_len
                # For 'meta', there's often a 4-byte version/flags header first
                if box_type_str == "meta":
                    sub_offset += 4
                parse_boxes(f, sub_offset, offset + box_size, indent + "  ")

            offset += box_size
    except Exception as e:
        print(f"Error parsing at offset {offset}: {e}")

def main():
    """CLI entry point for inspecting MP4 file box structure and atom offsets."""
    if len(sys.argv) < 2:
        print("Usage: python inspect_mp4.py <mp4_file>")
        return
    filename = sys.argv[1]
    file_size = os.path.getsize(filename)
    print(f"File: {filename} ({file_size} bytes)")
    with open(filename, "rb") as f:
        parse_boxes(f, 0, file_size)

if __name__ == "__main__":
    main()
