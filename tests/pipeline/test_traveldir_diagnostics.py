import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import unittest
import json
import numpy as np

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from scripts.stabilize_traveldir import compute_traveldir, write_sendcmd_file
from scripts.visualize_corrections import generate_traveldir_graph


class TestTraveldirDiagnostics(unittest.TestCase):
    def setUp(self):
        self.temp_dir = os.path.join(REPO_ROOT, "tests", "temp")
        os.makedirs(self.temp_dir, exist_ok=True)

    def test_traveldir_diagnostics_and_graph(self):
        """Verify travel-direction calculation, metadata structure, and graph rendering."""
        n = 90
        fps = 30.0
        times = np.linspace(0, 3.0, n)
        yaw = 20.0 * np.sin(2.0 * np.pi * 0.5 * times)
        angles = np.zeros((n, 3))
        angles[:, 0] = yaw

        locked_angles, diag = compute_traveldir(
            angles,
            mode="travel_direction",
            damping=0.85,
            deadband_deg=1.5,
            return_diagnostics=True
        )

        self.assertIn("raw_yaw", diag)
        self.assertIn("trend_yaw", diag)
        self.assertIn("locked_yaw", diag)
        self.assertIn("steer_corr", diag)
        self.assertEqual(len(diag["raw_yaw"]), n)
        self.assertEqual(len(diag["locked_yaw"]), n)

        orig_spread = np.max(diag["raw_yaw"]) - np.min(diag["raw_yaw"])
        locked_spread = np.max(diag["locked_yaw"]) - np.min(diag["locked_yaw"])
        self.assertLess(locked_spread, orig_spread)

        sc_path = os.path.join(self.temp_dir, "test_traveldir_sc.txt")
        meta_path = os.path.join(self.temp_dir, "test_traveldir_meta.json")
        graph_path = os.path.join(self.temp_dir, "test_traveldir_graph_out.png")

        write_sendcmd_file(sc_path, times, locked_angles, fps)
        self.assertTrue(os.path.exists(sc_path))
        self.assertGreater(os.path.getsize(sc_path), 0)

        diag["times"] = times.tolist()
        diag["fps"] = fps
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(diag, f, indent=2)
        self.assertTrue(os.path.exists(meta_path))
        self.assertGreater(os.path.getsize(meta_path), 0)

        success = generate_traveldir_graph(
            times,
            diag["raw_yaw"],
            diag["trend_yaw"],
            diag["locked_yaw"],
            deadband_deg=diag["deadband_deg"],
            damping=diag["damping"],
            mode=diag["mode"],
            output_png=graph_path,
            fps=fps
        )
        self.assertTrue(success)
        self.assertTrue(os.path.exists(graph_path))
        self.assertGreater(os.path.getsize(graph_path), 1000)


if __name__ == "__main__":
    unittest.main()
