import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import unittest
import subprocess

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from routers.common import (
    sanitize_cmd_arg,
    safe_number,
    safe_choice,
    spawn_background_process,
    run_async_subprocess,
    _safe_resolve,
    resolve_input_file,
    resolve_output_file,
    safe_job_id,
    get_status_file_path,
    get_log_file_path,
    is_valid_video_file,
    is_valid_image_file,
    resolve_runtime_file,
    safe_runtime_write_path
)


class TestSecuritySanitization(unittest.TestCase):
    """Test DRY security sanitization and background process spawning."""

    def test_sanitize_cmd_arg_valid(self):
        """Ensure standard arguments, paths, and negative numbers pass cleanly."""
        self.assertEqual(sanitize_cmd_arg("hello"), "hello")
        self.assertEqual(sanitize_cmd_arg("-15.5"), "-15.5")
        self.assertEqual(sanitize_cmd_arg("C:\\path\\to\\file.mp4"), "C:\\path\\to\\file.mp4")
        self.assertEqual(sanitize_cmd_arg(None), "")
        self.assertEqual(sanitize_cmd_arg(123), "123")

    def test_sanitize_cmd_arg_rejects_null_bytes(self):
        """Ensure arguments containing null bytes raise ValueError."""
        with self.assertRaises(ValueError):
            sanitize_cmd_arg("dangerous\0arg")

    def test_safe_number(self):
        """Ensure safe_number coerces numbers and falls back on injection attempts."""
        self.assertEqual(safe_number("42.5", 0.0), "42.5")
        self.assertEqual(safe_number("-180.0", 0.0), "-180.0")
        self.assertEqual(safe_number(25, 0, cast_fn=int), "25")
        # Malicious string should fallback to default
        self.assertEqual(safe_number("--injected-flag", 0.0), "0.0")
        self.assertEqual(safe_number("'; rm -rf /; '", 10, cast_fn=int), "10")
        self.assertEqual(safe_number(None, 5.0), "5.0")
        self.assertEqual(safe_number("", 5.0), "5.0")

    def test_safe_choice(self):
        """Ensure safe_choice allows only allowlisted values."""
        allowed = ["fast", "medium", "slow"]
        self.assertEqual(safe_choice("fast", allowed, "medium"), "fast")
        self.assertEqual(safe_choice("slow", allowed, "medium"), "slow")
        # Not in allowlist
        self.assertEqual(safe_choice("invalid", allowed, "medium"), "medium")
        self.assertEqual(safe_choice("--inject", allowed, "medium"), "medium")
        self.assertEqual(safe_choice(None, allowed, "medium"), "medium")

    def test_spawn_background_process(self):
        """Ensure spawn_background_process starts a process with correct platform flags."""
        cmd = [sys.executable, "-B", "-c", "print('dry_test_ok')"]
        proc = spawn_background_process(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = proc.communicate(timeout=5)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("dry_test_ok", stdout.decode("utf-8"))

    def test_spawn_background_process_empty_fails(self):
        """Ensure spawn_background_process rejects an empty command list."""
        with self.assertRaises(ValueError):
            spawn_background_process([])

    def test_safe_job_id(self):
        """Ensure safe_job_id removes illegal characters and generates safe IDs."""
        self.assertEqual(safe_job_id("job_abc-123"), "job_abc-123")
        self.assertEqual(safe_job_id("../../etc/passwd"), "etcpasswd")
        # Empty or None generates fallback
        self.assertTrue(safe_job_id("").startswith("job_"))
        self.assertTrue(safe_job_id(None).startswith("job_"))

    def test_safe_resolve_traversal_rejected(self):
        """Ensure directory traversal attempts are rejected."""
        self.assertEqual(_safe_resolve("../../Windows/System32/cmd.exe", ["samples"]), "")
        self.assertEqual(_safe_resolve("/etc/passwd", ["samples"]), "")
        self.assertEqual(_safe_resolve("..\\..\\secret.txt", ["samples"]), "")
        self.assertEqual(_safe_resolve("-option", ["samples"]), "")
        self.assertEqual(_safe_resolve("test\0bad.mp4", ["samples"]), "")

    def test_resolve_output_file(self):
        """Ensure output filenames are sanitized and routed safely."""
        res = resolve_output_file("my_video.mp4")
        self.assertEqual(res, "data/runtime/work/my_video.mp4")

        res_out = resolve_output_file("data/output/final_360.mp4")
        self.assertEqual(res_out, "data/output/final_360.mp4")

        # Traversal attempt in output
        res_trav = resolve_output_file("../../Windows/cmd.mp4")
        self.assertEqual(res_trav, "data/runtime/work/cmd.mp4")

        # Disallow dash prefix or null bytes
        self.assertEqual(resolve_output_file("-bad.mp4"), "")
        self.assertEqual(resolve_output_file("bad\0name.mp4"), "")

    def test_get_status_and_log_paths(self):
        """Ensure status and log file paths stay strictly within runtime dirs."""
        sf = get_status_file_path("test_123")
        self.assertEqual(sf, "data/runtime/temp/status_test_123.json")

        lf = get_log_file_path("test_123")
        self.assertEqual(lf, "data/runtime/logs/pipeline_test_123.log")

        # Traversal attempt in job_id
        sf_trav = get_status_file_path("../../etc/passwd")
        self.assertEqual(sf_trav, "data/runtime/temp/status_etcpasswd.json")

    def test_media_file_validators_reject_traversal(self):
        """Ensure is_valid_video_file rejects invalid paths and traversal attempts."""
        self.assertFalse(is_valid_video_file("../../Windows/System32/notepad.exe"))
        self.assertFalse(is_valid_video_file("/etc/shadow"))
        self.assertFalse(is_valid_video_file("-flag"))
        self.assertFalse(is_valid_video_file(""))
        self.assertFalse(is_valid_image_file("../../boot.ini"))
        self.assertFalse(is_valid_image_file(""))

    def test_runtime_helpers(self):
        """Ensure safe_runtime_write_path and resolve_runtime_file enforce strict boundaries."""
        wp = safe_runtime_write_path("output.json", subdir="data/runtime/work")
        self.assertEqual(wp, "data/runtime/work/output.json")

        # Traversal in write path
        wp_trav = safe_runtime_write_path("../../Windows/bad.txt", subdir="data/runtime/work")
        self.assertEqual(wp_trav, "data/runtime/work/bad.txt")

        # Disallow dash or null byte
        self.assertEqual(safe_runtime_write_path("-bad.txt"), "")
        self.assertEqual(safe_runtime_write_path("bad\0.txt"), "")

        # resolve_runtime_file traversal
        self.assertEqual(resolve_runtime_file("../../etc/passwd"), "")
        self.assertEqual(resolve_runtime_file("-option"), "")


if __name__ == "__main__":
    unittest.main()
