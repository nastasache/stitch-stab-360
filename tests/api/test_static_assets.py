import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import unittest
import glob

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from fastapi.testclient import TestClient
from server import app

class TestStaticAssets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_app_js_served(self):
        """Verify app.js is served with JavaScript content."""
        res = self.client.get("/app.js")
        self.assertEqual(res.status_code, 200)
        self.assertGreater(len(res.text), 100)

    def test_styles_css_served(self):
        """Verify styles.css is served."""
        res = self.client.get("/styles.css")
        self.assertEqual(res.status_code, 200)
        self.assertGreater(len(res.text), 50)

    def test_horizon_editor_html_served(self):
        """Verify horizon_editor.html is accessible."""
        res = self.client.get("/horizon_editor.html")
        self.assertEqual(res.status_code, 200)
        self.assertTrue("<canvas" in res.text or "<html" in res.text)

    def test_route_editor_html_served(self):
        """Verify route_editor.html is accessible."""
        res = self.client.get("/route_editor.html")
        self.assertEqual(res.status_code, 200)
        self.assertTrue("<div id=\"map\"" in res.text or "<html" in res.text)

    def test_checkpoints_json_served(self):
        """Verify horizon checkpoints JSON files in /data/ are served properly."""
        checkpoints_files = [f.replace("\\", "/") for f in glob.glob("data/runtime/work/*horizon_checkpoints.json")]
        if not checkpoints_files:
            self.skipTest("No horizon checkpoints JSON files found in data/runtime/work/")
        res = self.client.get(f"/{checkpoints_files[0]}")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("checkpoints", data)

    def test_security_script_and_source_blocked(self):
        """Verify sensitive code, scripts, and root configs remain strictly blocked."""
        for path in ["/server.py", "/requirements.txt", "/start.bat", "/.env", "/config/settings.py", "/data/test.py"]:
            res = self.client.get(path)
            self.assertEqual(res.status_code, 404, f"{path} should return 404")

if __name__ == "__main__":
    unittest.main()
