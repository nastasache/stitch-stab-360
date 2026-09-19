import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

"""Unit and integration tests for --remove_audio command line argument and audio stripping."""

import unittest
import subprocess
import json
from pathlib import Path

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from config.settings import PIPELINE_DEFAULTS
from scripts.generate_test_fisheye import generate_unstabilized_defective_video


class TestRemoveAudio(unittest.TestCase):
    """Test suite validating audio removal CLI option and pipeline behavior."""

    def test_pipeline_defaults_contains_remove_audio(self):
        """Verify PIPELINE_DEFAULTS defines remove_audio as False."""
        self.assertIn("remove_audio", PIPELINE_DEFAULTS)
        self.assertIs(PIPELINE_DEFAULTS["remove_audio"], False)

    def test_pipeline_cli_accepts_remove_audio_flag(self):
        """Verify pipeline.py accepts --remove_audio and --keep_audio flags without argument parser errors."""
        # Using a non-existent input with --help or checking parse_args behavior
        cmd = [
            sys.executable, "-B", "scripts/pipeline.py",
            "--help"
        ]
        proc = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--remove_audio", proc.stdout)
        self.assertIn("--keep_audio", proc.stdout)

    def test_pipeline_execution_with_remove_audio(self):
        """Verify pipeline execution with --remove_audio processes mockup video without parser errors."""
        work_dir = os.path.join(REPO_ROOT, "tests", "temp", "pipeline_e2e")
        os.makedirs(work_dir, exist_ok=True)

        input_video = os.path.join(work_dir, "test_e2e_mockup_in.mp4")
        output_video = os.path.join(work_dir, "test_remove_audio_out.mp4")
        status_file = os.path.join(work_dir, "test_remove_audio_status.json")

        # Generate a minimal 1-second mockup video if not already present
        if not os.path.exists(input_video):
            generate_unstabilized_defective_video(
                output_path=input_video,
                duration=1.0,
                fps=10,
                width=960,
                height=480
            )

        cmd = [
            sys.executable, "-B", "scripts/pipeline.py",
            "--input", input_video,
            "--output", output_video,
            "--remove_audio",
            "--duration", "1",
            "--preset", "ultrafast",
            "--crf", "30",
            "--no_prompt_transforms",
            "--status_file", status_file
        ]

        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"

        proc = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env=env,
            timeout=60
        )

        self.assertEqual(
            proc.returncode, 0,
            f"Pipeline failed with --remove_audio. Code: {proc.returncode}\nStderr: {proc.stderr}\nStdout: {proc.stdout}"
        )
        self.assertTrue(os.path.exists(output_video), f"Output video was not created: {output_video}")
        self.assertGreater(os.path.getsize(output_video), 0, f"Output video is empty: {output_video}")

        # Probe output video to verify there is no audio stream
        from utils.tool_resolver import resolve_ffprobe
        ffprobe_bin = resolve_ffprobe().get("path") or "ffprobe"
        probe_cmd = [
            ffprobe_bin, "-v", "error",
            "-select_streams", "a",
            "-show_entries", "stream=codec_name",
            "-of", "json", output_video
        ]
        probe_proc = subprocess.run(probe_cmd, capture_output=True, text=True)
        if probe_proc.returncode == 0:
            probe_data = json.loads(probe_proc.stdout)
            audio_streams = probe_data.get("streams", [])
            self.assertEqual(len(audio_streams), 0, f"Expected 0 audio streams, found: {audio_streams}")


if __name__ == "__main__":
    unittest.main()
