import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import unittest
import unittest.mock
import shutil
from pathlib import Path
from PIL import Image

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from server import app
from fastapi.testclient import TestClient
from routers.common import (
    is_valid_image_file,
    is_valid_video_file,
    is_valid_media_file,
    get_input_video_options_html,
    get_input_videos_list
)

class TestPhotoStitching(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.test_dir = Path(REPO_ROOT) / "tests" / "temp" / "photo_stitching"
        cls.test_dir.mkdir(parents=True, exist_ok=True)
        cls.test_photo = cls.test_dir / "temp_unit_test_photo.jpg"
        cls.test_video = cls.test_dir / "temp_unit_test_video.mp4"
        
        # Create a simple 2:1 mock dual-fisheye image (960x480)
        img = Image.new("RGB", (960, 480), color=(73, 109, 137))
        img.save(str(cls.test_photo), format="JPEG")
        with open(cls.test_video, "wb") as vf:
            vf.write(b"mock video header")

    def test_image_file_validators(self):
        """Verify image and media validators identify photo extensions accurately."""
        self.assertTrue(is_valid_image_file(str(self.test_photo)))
        self.assertTrue(is_valid_media_file(str(self.test_photo)))
        self.assertFalse(is_valid_video_file(str(self.test_photo)))

    @unittest.mock.patch("routers.common.glob.glob")
    def test_input_options_include_photo(self, mock_glob):
        """Dropdown generator must list photo files with [PHOTO] prefix and video files with [VIDEO] prefix."""
        def fake_glob(pattern):
            if "*.jpg" in pattern:
                return ["data/input/videos/temp_unit_test_photo.jpg"]
            if "*.mp4" in pattern:
                return ["data/input/videos/temp_unit_test_video.mp4"]
            return []
        mock_glob.side_effect = fake_glob
        with unittest.mock.patch("routers.common.os.path.exists", return_value=True), \
             unittest.mock.patch("routers.common.os.path.getsize", return_value=1000), \
             unittest.mock.patch("routers.common.os.path.getmtime", return_value=12345.0), \
             unittest.mock.patch("routers.common.is_valid_image_file", side_effect=lambda f: str(f).endswith(".jpg")), \
             unittest.mock.patch("routers.common.is_valid_video_file", side_effect=lambda f: str(f).endswith(".mp4")):
            html_opts = get_input_video_options_html()
            self.assertIn("[PHOTO]", html_opts)
            self.assertIn("temp_unit_test_photo.jpg", html_opts)
            self.assertIn("[VIDEO]", html_opts)
            self.assertIn("temp_unit_test_video.mp4", html_opts)

            file_list = get_input_videos_list()
            matched = [f for f in file_list if "temp_unit_test_photo.jpg" in f["value"]]
            self.assertTrue(len(matched) > 0)
            self.assertTrue(matched[0]["is_photo"])
            self.assertTrue(matched[0]["text"].startswith("[PHOTO]"))

            matched_v = [f for f in file_list if "temp_unit_test_video.mp4" in f["value"]]
            self.assertTrue(len(matched_v) > 0)
            self.assertFalse(matched_v[0]["is_photo"])
            self.assertTrue(matched_v[0]["text"].startswith("[VIDEO]"))

    def test_video_info_photo_detection(self):
        """API /api/v1/videos/info should detect photo, returning is_photo=True and 1 total frame."""
        rel_path = str(self.test_photo.relative_to(REPO_ROOT)).replace("\\", "/")
        response = self.client.get(f"/api/v1/videos/info?input={rel_path}")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get("status"), "success")
        self.assertTrue(data.get("is_photo"))
        self.assertEqual(data.get("total_frames"), 1)
        self.assertEqual(data.get("width"), 960)
        self.assertEqual(data.get("height"), 480)

    def test_photo_pipeline_cli_execution(self):
        """Pipeline script should stitch a still photo and generate stitched image and companion video."""
        out_photo = Path(REPO_ROOT) / "tests" / "temp" / "photo_stitching" / "test_photo_cli_out.jpg"

        import subprocess
        cmd = [
            sys.executable, "-B", "scripts/pipeline.py",
            "--input", str(self.test_photo),
            "--output", str(out_photo),
            "--ih_fov", "190.0",
            "--iv_fov", "190.0"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)
        self.assertEqual(res.returncode, 0, f"Pipeline failed: {res.stderr}")
        self.assertTrue(out_photo.exists())
        self.assertGreater(out_photo.stat().st_size, 0)

if __name__ == "__main__":
    unittest.main()
