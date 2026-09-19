import sys
sys.dont_write_bytecode = True
import os
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

"""Dual-fisheye equirectangular seam alpha blend mask generator."""

def main():
    """Generate a horizontal sinusoidal alpha blend mask for dual-fisheye equirectangular stitching."""
    import math
    import argparse
    from PIL import Image

    parser = argparse.ArgumentParser()
    parser.add_argument("--blend_width", type=int, default=200)
    parser.add_argument("--output", default="data/input/masks/alpha_mask.png")
    args = parser.parse_args()

    WIDTH  = 3840
    HEIGHT = 1920
    BLEND_HALF = max(1, args.blend_width // 2)

    # Seam centres in equirectangular pixel space
    SEAM_LEFT  = 960   # -90°
    SEAM_RIGHT = 2880  # +90°

    def sinusoidal(t):
        """Compute smooth S-curve interpolation from normalized coordinate.

        Args:
            t: Normalized interpolation factor in [0.0, 1.0].

        Returns:
            int: Interpolated 8-bit alpha value in [0, 255].
        """
        return int(255 * (0.5 - 0.5 * math.cos(math.pi * t)))

    row = [0] * WIDTH
    for x in range(WIDTH):
        # Default: white (rear lens, eq_right, overlay)
        val = 255

        # Pure-front zone: between the two seam centres
        # We want to show eq_left (base) here, so mask must be 0 (black)
        if SEAM_LEFT + BLEND_HALF <= x <= SEAM_RIGHT - BLEND_HALF:
            val = 0

        # Transition at left seam (960): white → black
        elif SEAM_LEFT - BLEND_HALF <= x < SEAM_LEFT + BLEND_HALF:
            t = (x - (SEAM_LEFT - BLEND_HALF)) / (2 * BLEND_HALF)
            val = 255 - sinusoidal(t)

        # Transition at right seam (2880): black → white
        elif SEAM_RIGHT - BLEND_HALF < x <= SEAM_RIGHT + BLEND_HALF:
            t = ((SEAM_RIGHT + BLEND_HALF) - x) / (2 * BLEND_HALF)
            val = 255 - sinusoidal(t)

        row[x] = val

    out_dir = os.path.dirname(args.output)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    img = Image.new('L', (WIDTH, HEIGHT))
    img.putdata(row * HEIGHT)
    img.save(args.output)
    print(f"Alpha mask generated at {args.output}")

if __name__ == '__main__':
    main()
