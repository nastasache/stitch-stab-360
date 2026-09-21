import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import unittest
from pathlib import Path
from utils.temp_storage import get_ram_temp_dir, is_ram_disk


class TestTempStorage(unittest.TestCase):
    """Unit tests for RAM disk and fast temp storage resolution."""

    def test_get_ram_temp_dir_returns_valid_dir(self):
        temp_dir = get_ram_temp_dir()
        self.assertTrue(os.path.isdir(temp_dir))
        self.assertTrue(os.access(temp_dir, os.W_OK))

    def test_get_ram_temp_dir_with_subfolder(self):
        sub = "unit_test_subfolder"
        temp_dir = get_ram_temp_dir(sub)
        self.assertTrue(os.path.isdir(temp_dir))
        self.assertTrue(temp_dir.endswith(sub) or sub in temp_dir)

    def test_is_ram_disk_evaluation(self):
        self.assertTrue(is_ram_disk("/dev/shm/test"))
        self.assertFalse(is_ram_disk("C:/some/normal/path"))


if __name__ == "__main__":
    unittest.main()
