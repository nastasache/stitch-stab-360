import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import unittest
from pathlib import Path

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

scripts_dir = os.path.join(REPO_ROOT, "scripts")
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

import streetview_gpx


class TestStreetviewInjectionHardening(unittest.TestCase):
    """Test command-line argument injection hardening in streetview_gpx."""

    def test_get_video_info_rejects_argument_injection(self):
        """Ensure get_video_info raises ValueError if input starts with a dash."""
        dangerous_inputs = [
            "-report",
            "--version",
            "-v error",
            "--extra-flags",
            "-dump_attachment"
        ]
        for dangerous_path in dangerous_inputs:
            with self.assertRaises(ValueError, msg=f"Should reject {dangerous_path}"):
                streetview_gpx.get_video_info(dangerous_path)

    def test_get_video_info_empty_or_none(self):
        """Ensure get_video_info returns default tuple for empty or None input."""
        dur, w, h, fps, dt = streetview_gpx.get_video_info("")
        self.assertEqual(dur, 0.0)
        self.assertIsNone(dt)

        dur, w, h, fps, dt = streetview_gpx.get_video_info(None)
        self.assertEqual(dur, 0.0)
        self.assertIsNone(dt)

    def test_get_video_info_nonexistent_file(self):
        """Ensure get_video_info returns default tuple for nonexistent file."""
        nonexistent = os.path.join(REPO_ROOT, "nonexistent_fake_video.mp4")
        dur, w, h, fps, dt = streetview_gpx.get_video_info(nonexistent)
        self.assertEqual(dur, 0.0)
        self.assertIsNone(dt)


if __name__ == "__main__":
    unittest.main()
