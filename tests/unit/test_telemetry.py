import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import unittest
import tempfile
import shutil

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from scripts.convert_telemetry import convert_telemetry

class TestTelemetryConversion(unittest.TestCase):
    def setUp(self):
        self.test_dir = os.path.join(REPO_ROOT, "tests", "temp", "unit_telemetry")
        os.makedirs(self.test_dir, exist_ok=True)

    def test_nonexistent_file_returns_false(self):
        """Conversion should gracefully return False if file does not exist."""
        result = convert_telemetry(os.path.join(self.test_dir, "nonexistent.txt"))
        self.assertFalse(result)

    def test_empty_file_returns_false(self):
        """Conversion should return False on empty file."""
        empty_file = os.path.join(self.test_dir, "empty.txt")
        with open(empty_file, "w", encoding="utf-8") as f:
            f.write("")
        result = convert_telemetry(empty_file)
        self.assertFalse(result)

    def test_valid_telemetry_conversion(self):
        """Test parsing raw angle telemetry and generating Gyroflow 1.3 GCSV structure."""
        raw_telemetry = os.path.join(self.test_dir, "test_raw_telemetry.txt")
        content = (
            "Time(s)\tAngleX(deg)\tAngleY(deg)\tAngleZ(deg)\n"
            "0.000\t0.00\t0.00\t0.00\n"
            "0.010\t0.50\t-0.20\t0.10\n"
            "0.020\t1.00\t-0.40\t0.20\n"
            "0.030\t1.50\t-0.60\t0.30\n"
            "0.040\t2.00\t-0.80\t0.40\n"
            "0.050\t2.50\t-1.00\t0.50\n"
        )
        with open(raw_telemetry, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)

        success = convert_telemetry(raw_telemetry, target_rate=100.0)
        self.assertTrue(success, "convert_telemetry failed on valid input")

        out_gcsv = os.path.join(self.test_dir, "test_raw_telemetry.gcsv")
        self.assertTrue(os.path.exists(out_gcsv), "Output .gcsv file was not created")

        with open(out_gcsv, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f.readlines()]

        self.assertIn("GYROFLOW IMU LOG", lines[0])
        self.assertIn("version,1.3", lines[1])
        self.assertIn("t,gx,gy,gz,ax,ay,az", lines[7])
        self.assertGreater(len(lines), 8, "Expected telemetry data rows in .gcsv")

    def test_companion_candidate_resolution_and_parsing(self):
        """Test resolving companion candidates for auto, gyroflow, witmotion, and custom_csv."""
        from scripts.extract_telemetry import resolve_companion_candidates, parse_companion_file

        mock_video = os.path.join(self.test_dir, "test_vid.mp4")
        with open(mock_video, "wb") as f:
            f.write(b"\x00" * 1024)

        # 1. Test .gcsv companion detection under 'auto' and 'gyroflow'
        gcsv_file = os.path.join(self.test_dir, "test_vid.gcsv")
        gcsv_content = (
            "GYROFLOW IMU LOG\n"
            "version,1.3\n"
            "id,test_vid\n"
            "orientation,Xyz\n"
            "tscale,1.0\n"
            "gscale,0.017453292519943295\n"
            "ascale,1.0\n"
            "t,gx,gy,gz,ax,ay,az\n"
            "0.0,0.0,0.0,0.0,0.0,0.0,1.0\n"
            "0.01,0.1,0.2,0.0,0.0,0.0,1.0\n"
            "0.02,0.1,0.2,0.0,0.0,0.0,1.0\n"
        )
        with open(gcsv_file, "w", encoding="utf-8", newline="\n") as f:
            f.write(gcsv_content)

        cands_auto = resolve_companion_candidates(mock_video, source_hint="auto")
        self.assertIn(gcsv_file, cands_auto, "Companion .gcsv was not found in 'auto' candidate list")

        cands_gyro = resolve_companion_candidates(mock_video, source_hint="gyroflow")
        self.assertIn(gcsv_file, cands_gyro, "Companion .gcsv was not found in 'gyroflow' candidate list")

        rot_data, resolved_path = parse_companion_file(gcsv_file)
        self.assertIsNotNone(rot_data, "Failed to parse companion .gcsv file")
        self.assertEqual(len(rot_data), 3)

        # 2. Test .MP4.gcsv naming convention
        mock_video_mp4 = os.path.join(self.test_dir, "test_vid_mp4case.mp4")
        mp4_gcsv_file = os.path.join(self.test_dir, "test_vid_mp4case.mp4.gcsv")
        with open(mp4_gcsv_file, "w", encoding="utf-8", newline="\n") as f:
            f.write(gcsv_content)

        cands_mp4_gcsv = resolve_companion_candidates(mock_video_mp4, source_hint="auto")
        self.assertIn(mp4_gcsv_file, cands_mp4_gcsv, "Companion .mp4.gcsv was not found in candidate list")

        # 3. Test WitMotion _telemetry.txt detection
        witmotion_file = os.path.join(self.test_dir, "test_vid_telemetry.txt")
        wit_content = (
            "Time\tAngleX\tAngleY\tAngleZ\n"
            "0.00\t1.5\t2.5\t0.0\n"
            "0.01\t1.6\t2.6\t0.1\n"
        )
        with open(witmotion_file, "w", encoding="utf-8", newline="\n") as f:
            f.write(wit_content)

        cands_wit = resolve_companion_candidates(mock_video, source_hint="witmotion")
        self.assertIn(witmotion_file, cands_wit, "WitMotion companion file was not found")

        rot_wit, path_wit = parse_companion_file(witmotion_file)
        self.assertIsNotNone(rot_wit, "Failed to parse WitMotion companion file")
        self.assertEqual(len(rot_wit), 2)

if __name__ == "__main__":
    unittest.main()

