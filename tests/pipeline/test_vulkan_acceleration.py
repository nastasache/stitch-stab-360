import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

"""Unit and integration tests for Vulkan GPU acceleration of the v360 filter."""

import unittest
import subprocess
import json
from pathlib import Path

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from utils.tool_resolver import probe_vulkan, resolve_ffmpeg
from scripts.generate_test_fisheye import generate_unstabilized_defective_video


class TestVulkanAcceleration(unittest.TestCase):
    """Test suite validating Vulkan v360 acceleration and fallback logic."""

    @classmethod
    def setUpClass(cls):
        """Prepare temporary test directory and 1-second synthetic mockup video."""
        cls.work_dir = os.path.join(REPO_ROOT, "tests", "temp", "vulkan_test")
        os.makedirs(cls.work_dir, exist_ok=True)
        cls.input_video = os.path.join(cls.work_dir, "vulkan_mockup_in.mp4")
        generate_unstabilized_defective_video(
            output_path=cls.input_video,
            duration=0.5,
            fps=10,
            width=960,
            height=480
        )

    def test_01_probe_vulkan_structure(self):
        """Verify probe_vulkan returns a boolean and status string without crashing."""
        ffmpeg_bin = resolve_ffmpeg()["path"]
        ok, reason = probe_vulkan(ffmpeg_bin=ffmpeg_bin)
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(reason, str)

    def test_02_pipeline_vulkan_stitching(self):
        """Verify pipeline execution with --v360_backend vulkan on a mockup video."""
        output_video = os.path.join(self.work_dir, "vulkan_mockup_out.mp4")
        status_file = os.path.join(self.work_dir, "vulkan_status.json")

        cmd = [
            sys.executable, "-B", "scripts/pipeline.py",
            "--input", self.input_video,
            "--output", output_video,
            "--duration", "0.5",
            "--preset", "ultrafast",
            "--crf", "32",
            "--v360_backend", "vulkan",
            "--no_prompt_transforms",
            "--status_file", status_file
        ]

        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"

        result = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env
        )

        self.assertEqual(result.returncode, 0, f"Pipeline execution failed:\n{result.stdout}")
        self.assertTrue(os.path.exists(output_video), "Rendered output video was not created.")
        self.assertGreater(os.path.getsize(output_video), 0, "Rendered output video is 0 bytes.")

    def test_03_vulkan_mode3_lanczos_fallback(self):
        """Verify that Quality Mode 3 (Lanczos) automatically bypasses Vulkan to CPU v360."""
        output_video = os.path.join(self.work_dir, "vulkan_mode3_out.mp4")
        status_file = os.path.join(self.work_dir, "vulkan_mode3_status.json")

        cmd = [
            sys.executable, "-B", "scripts/pipeline.py",
            "--input", self.input_video,
            "--output", output_video,
            "--duration", "0.5",
            "--preset", "ultrafast",
            "--crf", "32",
            "--v360_backend", "vulkan",
            "--stab_quality_mode", "3",
            "--no_prompt_transforms",
            "--status_file", status_file
        ]

        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"

        result = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env
        )

        self.assertEqual(result.returncode, 0, f"Pipeline execution failed:\n{result.stdout}")
        self.assertTrue(os.path.exists(output_video), "Rendered output video was not created.")
        self.assertIn("bypassed for Phase 1 stitching because Quality Mode 3", result.stdout)

    def test_04_vulkan_seam_blending_stitching(self):
        """Verify that split-lens seam blending accelerates via Vulkan GPU v360 filter."""
        output_video = os.path.join(self.work_dir, "vulkan_blend_out.mp4")
        status_file = os.path.join(self.work_dir, "vulkan_blend_status.json")

        cmd = [
            sys.executable, "-B", "scripts/pipeline.py",
            "--input", self.input_video,
            "--output", output_video,
            "--duration", "0.5",
            "--preset", "ultrafast",
            "--crf", "32",
            "--v360_backend", "vulkan",
            "--blend_seams",
            "--no_prompt_transforms",
            "--status_file", status_file
        ]

        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"

        result = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env
        )

        self.assertEqual(result.returncode, 0, f"Pipeline execution failed:\n{result.stdout}")
        self.assertTrue(os.path.exists(output_video), "Rendered output video was not created.")
        self.assertGreater(os.path.getsize(output_video), 0, "Rendered output video is 0 bytes.")
        self.assertIn("Stitching [Vulkan GPU]", result.stdout)
        self.assertIn("v360_vulkan=input=fisheye:output=equirect", result.stdout)

    def test_05_vulkan_numerical_equivalence(self):
        """Verify that Vulkan GPU stitching is mathematically aligned with CPU v360 (MAE < 5.0)."""
        import numpy as np
        from PIL import Image

        cpu_out = os.path.join(self.work_dir, "equiv_cpu.mp4")
        vk_out = os.path.join(self.work_dir, "equiv_vk.mp4")
        cpu_status = os.path.join(self.work_dir, "equiv_cpu_status.json")
        vk_status = os.path.join(self.work_dir, "equiv_vk_status.json")

        base_cmd = [
            sys.executable, "-B", "scripts/pipeline.py",
            "--input", self.input_video,
            "--duration", "0.2",
            "--preset", "ultrafast",
            "--crf", "32",
            "--blend_seams",
            "--yaw", "15.0",
            "--pitch", "-20.0",
            "--roll", "5.0",
            "--no_prompt_transforms"
        ]

        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"

        # CPU run
        res_cpu = subprocess.run(
            base_cmd + ["--output", cpu_out, "--v360_backend", "cpu", "--status_file", cpu_status],
            cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env
        )
        self.assertEqual(res_cpu.returncode, 0, f"CPU run failed:\n{res_cpu.stdout}")

        # Vulkan run
        res_vk = subprocess.run(
            base_cmd + ["--output", vk_out, "--v360_backend", "vulkan", "--status_file", vk_status],
            cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env
        )
        self.assertEqual(res_vk.returncode, 0, f"Vulkan run failed:\n{res_vk.stdout}")

        # Extract first frame from both
        ffmpeg_bin = resolve_ffmpeg()["path"]
        frame_cpu = os.path.join(self.work_dir, "frame_cpu.png")
        frame_vk = os.path.join(self.work_dir, "frame_vk.png")

        subprocess.run([ffmpeg_bin, "-y", "-i", cpu_out, "-vframes", "1", frame_cpu], capture_output=True)
        subprocess.run([ffmpeg_bin, "-y", "-i", vk_out, "-vframes", "1", frame_vk], capture_output=True)

        self.assertTrue(os.path.exists(frame_cpu))
        self.assertTrue(os.path.exists(frame_vk))

        img_cpu = np.array(Image.open(frame_cpu)).astype(float)
        img_vk = np.array(Image.open(frame_vk)).astype(float)

        mae = float(np.abs(img_cpu - img_vk).mean())
        self.assertLess(mae, 5.0, f"Vulkan and CPU stitching outputs diverged significantly (MAE: {mae:.2f})")


if __name__ == "__main__":
    unittest.main()
