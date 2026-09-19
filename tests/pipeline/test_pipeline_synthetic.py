import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import unittest
import numpy as np

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from scripts.generate_test_fisheye import create_spherical_environment

class TestPipelineSynthetic(unittest.TestCase):
    def test_synthetic_spherical_environment_generation(self):
        """Verify synthetic 360 panorama generator produces valid uint8 RGB frame buffer."""
        # Low-res test grid (512x256) for sub-second execution
        w, h = 512, 256
        env = create_spherical_environment(width=w, height=h)
        
        self.assertIsInstance(env, np.ndarray)
        self.assertEqual(env.shape, (h, w, 3))
        self.assertEqual(env.dtype, np.uint8)
        
        # Verify color diversity (sky gradient, ground, horizon)
        mean_val = np.mean(env)
        self.assertGreater(mean_val, 10.0, "Generated frame should not be completely black.")
        self.assertLess(mean_val, 245.0, "Generated frame should not be completely white.")

if __name__ == "__main__":
    unittest.main()
