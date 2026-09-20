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
    run_async_subprocess
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


if __name__ == "__main__":
    unittest.main()
