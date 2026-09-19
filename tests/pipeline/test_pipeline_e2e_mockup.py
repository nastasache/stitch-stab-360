import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

"""End-to-end integration test validating pipeline stitching on a synthetic mockup video."""

import unittest
import subprocess
import glob
from pathlib import Path

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from scripts.generate_test_fisheye import generate_unstabilized_defective_video


class TestPipelineE2EMockup(unittest.TestCase):
    """End-to-end test suite running the stitching pipeline against synthetic 1-second mockup video."""

    def setUp(self):
        """Prepare temporary test directory and synthetic 1-second test clip in tests/temp."""
        self.work_dir = os.path.join(REPO_ROOT, "tests", "temp", "pipeline_e2e")
        self.temp_dir = os.path.join(REPO_ROOT, "tests", "temp", "pipeline_e2e")
        os.makedirs(self.work_dir, exist_ok=True)
        os.makedirs(self.temp_dir, exist_ok=True)

        self.input_video = os.path.join(self.work_dir, "test_e2e_mockup_in.mp4")
        self.output_video = os.path.join(self.work_dir, "test_e2e_mockup_out.mp4")
        self.status_file = os.path.join(self.temp_dir, "test_e2e_status.json")

        # Render minimal 1-second 10fps mockup video (conforming to max 2-second mockup testing rule)
        generate_unstabilized_defective_video(
            output_path=self.input_video,
            duration=1.0,
            fps=10,
            width=960,
            height=480
        )

    def test_pipeline_e2e_execution(self):
        """Verify pipeline processes a 1-second clip end-to-end and outputs equirectangular video."""
        cmd = [
            sys.executable, "-B", "scripts/pipeline.py",
            "--input", self.input_video,
            "--output", self.output_video,
            "--duration", "1",
            "--preset", "ultrafast",
            "--crf", "30",
            "--no_prompt_transforms",
            "--status_file", self.status_file
        ]

        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"

        result = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env=env,
            timeout=60
        )

        self.assertEqual(
            result.returncode, 0,
            f"Pipeline failed with code {result.returncode}.\nStderr: {result.stderr}\nStdout: {result.stdout}"
        )
        self.assertTrue(
            os.path.exists(self.output_video),
            f"Output video was not created: {self.output_video}"
        )
        self.assertGreater(
            os.path.getsize(self.output_video), 0,
            f"Output video is empty: {self.output_video}"
        )


if __name__ == "__main__":
    unittest.main()
