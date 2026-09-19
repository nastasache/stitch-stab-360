import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import json
import glob
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

class TestPresets(unittest.TestCase):
    def test_factory_presets_exist_and_valid(self):
        """Verify that factory preset JSON files exist and have valid JSON schema."""
        preset_dirs = [
            os.path.join(REPO_ROOT, "data", "input", "presets"),
        ]
        
        found_presets = []
        for pdir in preset_dirs:
            if os.path.exists(pdir):
                found_presets.extend(glob.glob(os.path.join(pdir, "*.json")))
                
        self.assertGreater(len(found_presets), 0, "No preset JSON files found in data/input/presets.")
        
        for preset_file in found_presets:
            with open(preset_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.assertIsInstance(data, dict, f"Preset {preset_file} must be a JSON object.")

    def test_preset_keys_and_ranges(self):
        """Validate key calibration parameters inside existing presets."""
        preset_file = os.path.join(REPO_ROOT, "data", "input", "presets", "insta360_x3.json")
        if not os.path.exists(preset_file):
            # Fallback to any found json
            all_presets = glob.glob(os.path.join(REPO_ROOT, "data", "input", "presets", "*.json"))
            if all_presets:
                preset_file = all_presets[0]
            else:
                self.skipTest("No sample preset found for key validation.")
                
        with open(preset_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        # Check standard optical parameter keys if present
        for key in ["ih_fov", "iv_fov"]:
            if key in data:
                val = float(data[key])
                self.assertGreater(val, 50.0, f"FOV {key}={val} unreasonably small")
                self.assertLess(val, 360.0, f"FOV {key}={val} unreasonably large")

if __name__ == "__main__":
    unittest.main()
