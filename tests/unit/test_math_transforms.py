import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import unittest
import math
import numpy as np

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

def normalize_angle_deg(deg):
    """Normalize degree angle into range [-180, 180)."""
    return ((deg + 180.0) % 360.0) - 180.0

def euler_to_rot_matrix(roll_deg, pitch_deg, yaw_deg):
    """Compute 3D rotation matrix from Euler angles (in degrees, ZYX convention)."""
    r = math.radians(roll_deg)
    p = math.radians(pitch_deg)
    y = math.radians(yaw_deg)
    
    Rx = np.array([[1, 0, 0], [0, math.cos(r), -math.sin(r)], [0, math.sin(r), math.cos(r)]])
    Ry = np.array([[math.cos(p), 0, math.sin(p)], [0, 1, 0], [-math.sin(p), 0, math.cos(p)]])
    Rz = np.array([[math.cos(y), -math.sin(y), 0], [math.sin(y), math.cos(y), 0], [0, 0, 1]])
    
    return Rz @ Ry @ Rx

class TestMathTransforms(unittest.TestCase):
    def test_angle_normalization(self):
        """Test degree angle wrap-around bounds."""
        self.assertAlmostEqual(normalize_angle_deg(0.0), 0.0)
        self.assertAlmostEqual(normalize_angle_deg(180.0), -180.0)
        self.assertAlmostEqual(normalize_angle_deg(360.0), 0.0)
        self.assertAlmostEqual(normalize_angle_deg(-190.0), 170.0)
        self.assertAlmostEqual(normalize_angle_deg(270.0), -90.0)

    def test_rotation_matrix_properties(self):
        """Test that rotation matrices generated are orthogonal (R * R^T = I) with det = 1."""
        R = euler_to_rot_matrix(15.0, -30.0, 45.0)
        
        # Check orthogonality: R @ R.T == I
        identity = np.eye(3)
        np.testing.assert_allclose(R @ R.T, identity, atol=1e-6)
        
        # Check determinant: det(R) == 1
        det = np.linalg.det(R)
        self.assertAlmostEqual(det, 1.0, places=5)

    def test_equirectangular_bounds(self):
        """Verify spherical lat/lon bounds mapping."""
        width, height = 3840, 1920
        lat_arr = np.linspace(math.pi / 2, -math.pi / 2, height, endpoint=False)
        lon_arr = np.linspace(-math.pi, math.pi, width, endpoint=False)
        
        self.assertEqual(len(lat_arr), height)
        self.assertEqual(len(lon_arr), width)
        self.assertAlmostEqual(lat_arr[0], math.pi / 2, places=3)
        self.assertAlmostEqual(lon_arr[0], -math.pi, places=3)

if __name__ == "__main__":
    unittest.main()
