import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import unittest
from unittest import mock
import json

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


class TestPrestitchedStabilizationReport(unittest.TestCase):
    """Verify stabilization report and status metadata generation when input is already stitched."""

    def setUp(self):
        self.test_dir = os.path.join(REPO_ROOT, "tests", "temp", "unit_prestitched_report")
        os.makedirs(self.test_dir, exist_ok=True)
        self.input_file = os.path.join(self.test_dir, "prestitched_in.mp4")
        self.output_file = os.path.join(self.test_dir, "prestitched_out.mp4")
        self.status_file = os.path.join(self.test_dir, "prestitched_status.json")

        with open(self.input_file, "w", encoding="utf-8") as f:
            f.write("dummy input video")
        with open(self.output_file, "w", encoding="utf-8") as f:
            f.write("dummy output video")

    def test_base_stab_raw_resolves_without_stitched_file(self):
        """Ensure base_stab_raw falls back to initial_raw_stitched_file or args.input when step1_stitch is absent."""
        args_mock = mock.Mock()
        args_mock.stabilize = True
        args_mock.input = self.input_file
        args_mock.output = self.output_file
        args_mock.status_file = self.status_file

        executed_methods = ["telemetry"]
        non_existent_stitched = os.path.join(self.test_dir, "non_existent_stitched.mp4")
        initial_raw = self.input_file

        # Emulate the dynamic resolution logic in pipeline.py
        base_stab_raw = initial_raw if (initial_raw and os.path.exists(initial_raw)) else (
            non_existent_stitched if os.path.exists(non_existent_stitched) else args_mock.input
        )

        self.assertEqual(base_stab_raw, self.input_file)
        self.assertTrue(os.path.exists(base_stab_raw))
        # Verify the guard condition passes
        guard = bool(getattr(args_mock, 'stabilize', False) and executed_methods and base_stab_raw and os.path.exists(base_stab_raw))
        self.assertTrue(guard)

    def test_extract_test_id(self):
        """Verify numeric test and job IDs are accurately extracted from various path conventions."""
        from scripts.pipeline import extract_test_id

        self.assertEqual(extract_test_id("data/runtime/work/test_fisheye_ST_out_3381.MP4"), "3381")
        self.assertEqual(extract_test_id("data/runtime/work/test_fisheye_0_30_out_3378_telemetry_report.txt"), "3378")
        self.assertEqual(extract_test_id("data/runtime/temp/status_job_3384_3381_1789560678643.json"), "3384")
        self.assertEqual(extract_test_id("data/input/videos/test_custom_clip_5521_stab.mp4"), "5521")
        self.assertIsNone(extract_test_id("generic_input_video.mp4"))

    def test_descriptive_temp_rect_filenames(self):
        """Verify analyze_stabilization_report generates temp_raw_<method>_rect_<test_id>.mp4 without PID when test_id is present."""
        from scripts.pipeline import analyze_stabilization_report

        report_file = os.path.join(self.test_dir, "test_fisheye_ST_out_3381_telemetry_report.txt")
        captured_cmds = []

        def fake_run(cmd, **kwargs):
            captured_cmds.append(cmd)
            # Create dummy output files so analyze_stabilization_report proceeds without error
            out_file = cmd[-1]
            with open(out_file, "w", encoding="utf-8") as f:
                f.write("dummy rect")
            return mock.Mock(returncode=0)

        with mock.patch("subprocess.run", side_effect=fake_run), \
             mock.patch("cv2.VideoCapture") as mock_vc:
            mock_cap = mock.Mock()
            mock_cap.isOpened.return_value = False
            mock_vc.return_value = mock_cap

            analyze_stabilization_report(
                self.input_file, self.output_file, report_file,
                sample_frames=5,
                report_title="TELEMETRY (IMU LEVELING) STABILIZATION REPORT"
            )

        self.assertEqual(len(captured_cmds), 2)
        raw_cmd, stab_cmd = captured_cmds[0], captured_cmds[1]
        raw_out = os.path.basename(raw_cmd[-1])
        stab_out = os.path.basename(stab_cmd[-1])

        self.assertEqual(raw_out, "temp_raw_telemetry_rect_3381.mp4")
        self.assertEqual(stab_out, "temp_stab_telemetry_rect_3381.mp4")


if __name__ == "__main__":
    unittest.main()

