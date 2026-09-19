import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

"""Automated validation ensuring all 'Copy Direct Video Link' buttons in index.html are wired in app.js."""

import unittest
import re
from pathlib import Path

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


class TestCopyLinks(unittest.TestCase):
    """Test suite ensuring all viewport and popup copy link buttons have event listeners."""

    def setUp(self):
        index_path = os.path.join(REPO_ROOT, "index.html")
        app_path = os.path.join(REPO_ROOT, "app.js")
        with open(index_path, "r", encoding="utf-8") as f:
            self.index_html = f.read()
        with open(app_path, "r", encoding="utf-8") as f:
            self.app_js = f.read()

    def test_all_html_copy_buttons_bound_in_app_js(self):
        """Verify that every button in index.html titled 'Copy Direct Video Link' is bound in app.js."""
        # Find all button tags with title containing 'Copy Direct Video Link'
        btn_pattern = re.compile(r'<button\s+[^>]*id=["\']([^"\']+)["\'][^>]*title=["\']Copy Direct Video Link["\']', re.IGNORECASE)
        found_btn_ids = set(btn_pattern.findall(self.index_html))

        expected_embedded_ids = {
            "orig-copy-link",
            "vr-stitched-copy-link",
            "vr-copy-link",
            "vr-telemetry-copy-link",
            "vr-vidstab-copy-link",
            "vr-kabsch-copy-link",
            "vr-kopf-copy-link",
            "vr-horizon-copy-link",
            "vr-cinematic-copy-link",
            "vr-traveldir-copy-link",
        }

        expected_popup_ids = {
            "btn-copy-link-original",
            "btn-copy-link-stitched",
            "btn-copy-link-360",
            "btn-copy-link-telemetry",
            "btn-copy-link-vidstab",
            "btn-copy-link-kabsch",
            "btn-copy-link-kopf",
            "btn-copy-link-horizon",
            "btn-copy-link-cinematic",
            "btn-copy-link-traveldir",
        }

        all_expected = expected_embedded_ids | expected_popup_ids

        # Ensure all expected IDs exist in index.html
        self.assertTrue(all_expected.issubset(found_btn_ids), f"Missing button IDs in index.html: {all_expected - found_btn_ids}")

        # Ensure every single button ID is referenced and bound in app.js
        missing_bindings = []
        for btn_id in sorted(found_btn_ids):
            if f"'{btn_id}'" not in self.app_js and f'"{btn_id}"' not in self.app_js:
                missing_bindings.append(btn_id)

        self.assertEqual(
            missing_bindings, [],
            f"The following copy link buttons are present in index.html but not referenced/bound in app.js: {missing_bindings}"
        )


if __name__ == "__main__":
    unittest.main()
