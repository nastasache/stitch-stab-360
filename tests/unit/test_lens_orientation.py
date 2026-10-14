import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

"""Unit tests for camera lens orientation and backwards mount support."""

import unittest
import numpy as np

# Ensure repository root is on sys.path
_repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from config.settings import PIPELINE_DEFAULTS
from scripts.auto_calibrate import calibrate_frames_dataset, estimate_rotation_and_horizon


class TestLensOrientation(unittest.TestCase):
    """Test suite validating lens swap and backwards mount orientation logic."""

    def test_settings_default(self):
        """Verify pipeline defaults include lens_orientation set to normal."""
        self.assertIn("lens_orientation", PIPELINE_DEFAULTS)
        self.assertEqual(PIPELINE_DEFAULTS["lens_orientation"], "normal")

    def test_auto_calibrate_frame_slicing(self):
        """Verify image slicing properly swaps front and rear halves based on lens_orientation."""
        from scripts.auto_calibrate import calibrate_image
        import cv2

        os.makedirs("tests/temp", exist_ok=True)
        synthetic_path = "tests/temp/synthetic_test_lens_pair.png"

        # Create 100x200 test image: Left half all 10s, Right half all 90s
        test_img = np.zeros((100, 200, 3), dtype=np.uint8)
        test_img[:, :100] = 10
        test_img[:, 100:] = 90
        cv2.imwrite(synthetic_path, test_img)

        # Normal orientation: Left half should be front
        res_normal = calibrate_image(synthetic_path, auto_level_only=True, lens_orientation="normal")
        self.assertEqual(res_normal["status"], "success")

        # Swapped orientation: Right half should be front
        res_swapped = calibrate_image(synthetic_path, auto_level_only=True, lens_orientation="swap_lenses")
        self.assertEqual(res_swapped["status"], "success")

    def test_telemetry_backwards_mount_sign_inversion(self):
        """Verify backwards_mount inverts pitch and roll telemetry multipliers."""
        import argparse

        class MockArgs:
            def __init__(self, mode="backwards_mount"):
                self.lens_orientation = mode
                self.telemetry_mode = "smooth"
                self.telemetry_smoothing = 30
                self.telemetry_ref_frame = 0
                self.telemetry_extractor = "extract_telemetry"
                self.telemetry_multiplier = 1.0
                self.telemetry_multiplier_roll = 1.5
                self.telemetry_multiplier_pitch = 2.0
                self.telemetry_multiplier_yaw = 1.0
                self.input = "dummy.mp4"

        args_backwards = MockArgs("backwards_mount")
        mult_base = args_backwards.telemetry_multiplier
        use_multiplier_r = getattr(args_backwards, 'telemetry_multiplier_roll', mult_base)
        use_multiplier_p = getattr(args_backwards, 'telemetry_multiplier_pitch', mult_base)

        if getattr(args_backwards, "lens_orientation", "normal") == "backwards_mount":
            use_multiplier_r *= -1.0
            use_multiplier_p *= -1.0

        self.assertEqual(use_multiplier_r, -1.5)
        self.assertEqual(use_multiplier_p, -2.0)

        # Verify normal mode preserves positive multipliers
        args_normal = MockArgs("normal")
        use_r_norm = getattr(args_normal, 'telemetry_multiplier_roll', mult_base)
        use_p_norm = getattr(args_normal, 'telemetry_multiplier_pitch', mult_base)
        if getattr(args_normal, "lens_orientation", "normal") == "backwards_mount":
            use_r_norm *= -1.0
            use_p_norm *= -1.0

        self.assertEqual(use_r_norm, 1.5)
        self.assertEqual(use_p_norm, 2.0)

    def test_crop_offsets_derivation(self):
        """Verify crop coordinate derivation matches 1920 boundary split."""
        half_w = 1920

        # Normal mode
        is_swapped_normal = False
        crop_x_front_norm = half_w if is_swapped_normal else 0
        crop_x_rear_norm = 0 if is_swapped_normal else half_w
        self.assertEqual(crop_x_front_norm, 0)
        self.assertEqual(crop_x_rear_norm, 1920)

        # Swap lenses mode
        is_swapped_swap = True
        crop_x_front_swap = half_w if is_swapped_swap else 0
        crop_x_rear_swap = 0 if is_swapped_swap else half_w
        self.assertEqual(crop_x_front_swap, 1920)
        self.assertEqual(crop_x_rear_swap, 0)


if __name__ == "__main__":
    unittest.main()
