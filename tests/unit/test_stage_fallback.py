import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from scripts.pipeline import check_and_apply_stage_fallback

class TestStageFallback(unittest.TestCase):
    def setUp(self):
        self.temp_dir = os.path.join(REPO_ROOT, "tests", "temp")
        os.makedirs(self.temp_dir, exist_ok=True)
        self.unstabilized_report = os.path.join(self.temp_dir, "test_unstab_report.txt")
        self.stabilized_report = os.path.join(self.temp_dir, "test_stab_report.txt")

        with open(self.unstabilized_report, "w", encoding="utf-8", newline="\n") as f:
            f.write("=== VIDSTAB (2D MOTION) STABILIZATION REPORT ===\n")
            f.write("STATUS          : NO (UNSTABILIZED) -23.8%\n")
            f.write("Frames Analyzed : 89\n")

        with open(self.stabilized_report, "w", encoding="utf-8", newline="\n") as f:
            f.write("=== KABSCH (SPHERICAL SVD) STABILIZATION REPORT ===\n")
            f.write("STATUS          : YES (STABILIZED) +36.0%\n")
            f.write("Frames Analyzed : 89\n")

    def test_fallback_triggers_when_unstabilized(self):
        active_sendcmds = ["cmd_telemetry.txt", "cmd_vidstab.txt"]
        executed_methods = ["telemetry", "vidstab"]
        reverted_stages = {}

        new_file, was_reverted = check_and_apply_stage_fallback(
            stage_key="vidstab",
            stage_display_name="VidSTAB (2D Motion)",
            report_path=self.unstabilized_report,
            sendcmd_file="cmd_vidstab.txt",
            stage_in_file="video_clean_prev.mp4",
            current_file="video_degraded_vidstab.mp4",
            active_sendcmd_files=active_sendcmds,
            executed_methods_so_far=executed_methods,
            fallback_enabled=True,
            reverted_stages=reverted_stages
        )

        self.assertTrue(was_reverted)
        self.assertEqual(new_file, "video_clean_prev.mp4")
        self.assertNotIn("cmd_vidstab.txt", active_sendcmds)
        self.assertNotIn("vidstab", executed_methods)
        self.assertIn("vidstab", reverted_stages)
        self.assertEqual(reverted_stages["vidstab"]["status_text"], "NO (UNSTABILIZED) -23.8%")

    def test_fallback_ignored_when_stabilized(self):
        active_sendcmds = ["cmd_telemetry.txt", "cmd_kabsch.txt"]
        executed_methods = ["telemetry", "kabsch"]
        reverted_stages = {}

        new_file, was_reverted = check_and_apply_stage_fallback(
            stage_key="kabsch",
            stage_display_name="Kabsch (Spherical SVD)",
            report_path=self.stabilized_report,
            sendcmd_file="cmd_kabsch.txt",
            stage_in_file="video_clean_prev.mp4",
            current_file="video_kabsch_out.mp4",
            active_sendcmd_files=active_sendcmds,
            executed_methods_so_far=executed_methods,
            fallback_enabled=True,
            reverted_stages=reverted_stages
        )

        self.assertFalse(was_reverted)
        self.assertEqual(new_file, "video_kabsch_out.mp4")
        self.assertIn("cmd_kabsch.txt", active_sendcmds)
        self.assertIn("kabsch", executed_methods)
        self.assertEqual(len(reverted_stages), 0)

    def test_fallback_disabled_flag(self):
        active_sendcmds = ["cmd_vidstab.txt"]
        executed_methods = ["vidstab"]
        reverted_stages = {}

        new_file, was_reverted = check_and_apply_stage_fallback(
            stage_key="vidstab",
            stage_display_name="VidSTAB",
            report_path=self.unstabilized_report,
            sendcmd_file="cmd_vidstab.txt",
            stage_in_file="video_prev.mp4",
            current_file="video_curr.mp4",
            active_sendcmd_files=active_sendcmds,
            executed_methods_so_far=executed_methods,
            fallback_enabled=False,
            reverted_stages=reverted_stages
        )

        self.assertFalse(was_reverted)
        self.assertEqual(new_file, "video_curr.mp4")
        self.assertIn("cmd_vidstab.txt", active_sendcmds)
        self.assertIn("vidstab", executed_methods)

if __name__ == "__main__":
    unittest.main()
