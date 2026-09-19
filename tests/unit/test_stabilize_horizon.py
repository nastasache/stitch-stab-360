import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import unittest
import tempfile
import shutil
import numpy as np

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from scripts.stabilize_horizon import auto_detect_from_telemetry, detect_visual_horizon_tilt, auto_detect_from_video
from scripts.auto_calibrate import extract_telemetry_orientation

class TestStabilizeHorizon(unittest.TestCase):
    def setUp(self):
        self.test_dir = os.path.join(REPO_ROOT, "tests", "temp", "unit_stabilize_horizon")
        os.makedirs(self.test_dir, exist_ok=True)

    def test_stabilize_horizon_auto_detect_from_gcsv(self):
        """Test auto_detect_from_telemetry in stabilize_horizon directly with a .gcsv file."""
        gcsv_file = os.path.join(self.test_dir, "horizon_sample.gcsv")
        rows = [
            "GYROFLOW IMU LOG", "version,1.3", "id,test", "orientation,Xyz",
            "tscale,1.0", "gscale,0.017453292519943295", "ascale,1.0",
            "t,gx,gy,gz,ax,ay,az"
        ]
        for i in range(100):
            t = i * 0.0333
            gx = 1.0 if (i % 20 < 10) else -1.0
            gy = 0.5 if (i % 30 < 15) else -0.5
            rows.append(f"{t:.4f},{gx:.4f},{gy:.4f},0.0,0.0,0.0,1.0")

        with open(gcsv_file, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(rows) + "\n")

        checkpoints = auto_detect_from_telemetry(gcsv_file, fps=30.0)
        self.assertIsInstance(checkpoints, list)
        self.assertGreater(len(checkpoints), 0, "Expected checkpoints auto-detected from .gcsv file")

    def test_auto_calibrate_companion_gcsv(self):
        """Test extract_telemetry_orientation in auto_calibrate with companion .MP4.gcsv."""
        mock_vid = os.path.join(self.test_dir, "calib_test.MP4")
        with open(mock_vid, "wb") as f:
            f.write(b"\x00" * 100)

        gcsv_file = os.path.join(self.test_dir, "calib_test.MP4.gcsv")
        content = (
            "t,gx,gy,gz,ax,ay,az\n" +
            "\n".join([f"{i*0.01:.2f},0.0,0.0,0.0,0.1,0.2,0.97" for i in range(20)])
        )
        with open(gcsv_file, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)

        pitch, roll = extract_telemetry_orientation(mock_vid)
        self.assertIsNotNone(pitch, "Expected valid pitch from companion .MP4.gcsv")
        self.assertIsNotNone(roll, "Expected valid roll from companion .MP4.gcsv")

    def test_detect_visual_horizon_tilt_synthetic(self):
        """Test detect_visual_horizon_tilt on synthetic equirectangular test image."""
        # Create 960x480 test image with horizontal equator and a vertical pole
        img = np.zeros((480, 960, 3), dtype=np.uint8)
        img[:240, :] = [255, 200, 100]  # Sky (top half)
        img[240:, :] = [50, 120, 50]    # Ground (bottom half)
        # Vertical pole at center (x=480, y=100 to 300)
        img[100:300, 478:482] = [20, 20, 20]

        pitch, roll = detect_visual_horizon_tilt(img)
        self.assertIsInstance(pitch, float)
        self.assertIsInstance(roll, float)
        self.assertLess(abs(pitch), 2.0, "Expected near-zero pitch on neutral horizon")
        self.assertLess(abs(roll), 2.0, "Expected near-zero roll on vertical pole")

    def test_auto_detect_from_video_lock_roll(self):
        """Test auto_detect_from_video enforces 0.0 roll across all keyframes when lock_roll=True."""
        mockup_vid = os.path.join(REPO_ROOT, "tests", "temp", "pipeline_e2e", "test_e2e_mockup_in.mp4")
        if os.path.exists(mockup_vid):
            cps = auto_detect_from_video(mockup_vid, fps=10.0, lock_roll=True)
            self.assertGreater(len(cps), 0)
            for cp in cps:
                self.assertEqual(cp["roll"], 0.0, f"Frame {cp['frame']} roll should be locked to 0.0")

    def test_detect_visual_horizon_tilt_ground_vs_skyline(self):
        """Test optical_target locks onto ground contact line near equator vs skyline canopy."""
        h, w = 480, 960
        deg_per_px = 180.0 / h
        tilt_deg = 3.0
        dy = tilt_deg / deg_per_px
        x = np.arange(w)
        phi = 2.0 * np.pi * (x / float(w) - 0.5)

        y_canopy = 190.0 - dy * np.cos(phi)
        y_ground = 242.0 - dy * np.cos(phi)

        img = np.zeros((h, w, 3), dtype=np.uint8)
        for xi in range(w):
            yc = int(round(y_canopy[xi]))
            yg = int(round(y_ground[xi]))
            img[:yc, xi] = [255, 220, 180]
            img[yc:yg, xi] = [30, 80, 30]
            img[yg:, xi] = [140, 140, 140]

        pitch_ground, roll_ground = detect_visual_horizon_tilt(img, optical_target="ground")
        pitch_skyline, roll_skyline = detect_visual_horizon_tilt(img, optical_target="skyline")

        self.assertIsInstance(pitch_ground, float)
        self.assertIsInstance(pitch_skyline, float)
        # Ground contact line provides full physical tilt tracking (~3.0 deg)
        self.assertAlmostEqual(pitch_ground, 3.0, delta=0.5)
        # Skyline is dampened (0.3x) to prevent canopy swaying (~0.9 deg)
        self.assertLess(pitch_skyline, pitch_ground)
        self.assertEqual(roll_ground, 0.0)

    def test_seed_cadence_checkpoints_short_video(self):
        """Test cadence keyframe generation on short 3-second video matching Test 3171 cadence."""
        from scripts.stabilize_horizon import seed_cadence_checkpoints
        cps = seed_cadence_checkpoints(total_frames=90, fps=30.0, interval_sec=0.5)
        self.assertEqual(len(cps), 7)
        self.assertEqual(cps[0]['frame'], 0)
        self.assertEqual(cps[1]['frame'], 15)
        self.assertEqual(cps[-1]['frame'], 89)

    def test_seed_cadence_checkpoints_long_video_adaptive_guard(self):
        """Test that long 15-minute video automatically scales interval to guard against keyframe explosion."""
        from scripts.stabilize_horizon import seed_cadence_checkpoints
        # 15 minutes @ 30 fps = 27000 frames
        cps = seed_cadence_checkpoints(total_frames=27000, fps=30.0, interval_sec=0.5, max_checkpoints=60, adaptive=True)
        self.assertLessEqual(len(cps), 60, "Adaptive guard must cap keyframes to <= max_checkpoints")
        self.assertEqual(cps[0]['frame'], 0)
        self.assertEqual(cps[-1]['frame'], 26999)

    def test_seed_cadence_checkpoints_custom_window(self):
        """Test cadence keyframe generation within a localized time window."""
        from scripts.stabilize_horizon import seed_cadence_checkpoints
        cps = seed_cadence_checkpoints(total_frames=1000, fps=30.0, interval_sec=1.0, start_frame=150, end_frame=300)
        self.assertTrue(all(150 <= cp['frame'] <= 300 for cp in cps))
        self.assertEqual(cps[0]['frame'], 150)
        self.assertEqual(cps[-1]['frame'], 300)

    def test_detect_pitch_extrema_checkpoints_synthetic_sinusoid(self):
        """Test pitch extrema turning points identification on synthetic 1Hz sinusoidal pitch wave."""
        from scripts.stabilize_horizon import detect_pitch_extrema_checkpoints
        # 3 seconds @ 30fps = 90 frames, 1 Hz sine wave with 3.0° amplitude
        frames = np.arange(90)
        t = frames / 30.0
        pitch_wave = 3.0 * np.sin(2 * np.pi * 1.0 * t)  # Peaks at t=0.25, 1.25, 2.25 (f=7, 37, 67)
        cps = detect_pitch_extrema_checkpoints(total_frames=90, fps=30.0, pitch_curve=pitch_wave, min_prominence_deg=1.0)

        self.assertGreater(len(cps), 3)
        self.assertEqual(cps[0]['frame'], 0)
        self.assertEqual(cps[-1]['frame'], 89)
        types = [c.get('type') for c in cps]
        self.assertIn('crest', types)
        self.assertIn('trough', types)

    def test_detect_pitch_extrema_checkpoints_flat_video_zero_internal_keyframes(self):
        """Test that long flat video without pitch swings generates only boundary checkpoints."""
        from scripts.stabilize_horizon import detect_pitch_extrema_checkpoints
        # 1000 frames with subtle noise (< 0.1 deg) well below 1.0 deg threshold
        pitch_flat = np.random.normal(loc=0.0, scale=0.05, size=1000)
        cps = detect_pitch_extrema_checkpoints(total_frames=1000, fps=30.0, pitch_curve=pitch_flat, min_prominence_deg=1.0)
        # Should only contain boundary keyframes (frame 0 and frame 999)
        self.assertEqual(len(cps), 2)
        self.assertEqual(cps[0]['frame'], 0)
        self.assertEqual(cps[1]['frame'], 999)

    def test_detect_pitch_extrema_budget_capping(self):
        """Test that pitch extrema detector enforces max_checkpoints budget."""
        from scripts.stabilize_horizon import detect_pitch_extrema_checkpoints
        # 1000 frames with rapid high-frequency oscillation
        frames = np.arange(1000)
        t = frames / 30.0
        pitch_wiggle = 5.0 * np.sin(2 * np.pi * 2.0 * t)
        cps = detect_pitch_extrema_checkpoints(total_frames=1000, fps=30.0, pitch_curve=pitch_wiggle,
                                               min_prominence_deg=1.0, max_checkpoints=25)
        self.assertLessEqual(len(cps), 25)
        self.assertEqual(cps[0]['frame'], 0)
        self.assertEqual(cps[-1]['frame'], 999)

    def test_detect_pitch_extrema_long_video_unconstrained(self):
        """Test that long video retains all prominent stride extrema without artificial caps when max_checkpoints is None."""
        from scripts.stabilize_horizon import detect_pitch_extrema_checkpoints, generate_slerp_trajectory
        # 6000 frames (200s @ 30fps) with 100 walking step cycles (0.5 Hz)
        frames = np.arange(6000)
        t = frames / 30.0
        pitch_walking = 4.0 * np.sin(2 * np.pi * 0.5 * t)
        cps = detect_pitch_extrema_checkpoints(total_frames=6000, fps=30.0, pitch_curve=pitch_walking,
                                               min_prominence_deg=1.0, max_checkpoints=None)
        # 100 full cycles -> ~200 peaks & troughs + 2 boundaries
        self.assertGreater(len(cps), 180, "Expected all natural step extrema preserved without artificial 60-keyframe cap")
        self.assertEqual(cps[0]['frame'], 0)
        self.assertEqual(cps[-1]['frame'], 5999)

        # Verify O(N+M) two-pointer interpolation runs cleanly on long sequence
        traj = generate_slerp_trajectory(cps, total_frames=6000, fps=30.0)
        self.assertEqual(traj.shape, (6000, 3))

    def test_compute_stabilization_percentage_penalizes_negative_shock(self):
        """Test that compute_stabilization_percentage penalizes negative peak shock rather than omitting it."""
        from scripts.pipeline import compute_stabilization_percentage
        # Test 3260 scenario: raw roll degraded peak shock to -6.1%
        m_data = {
            "pct_peak": -6.1,
            "pct_jitter": 6.6,
            "pct_overall": 7.4,
            "pct_dy": 19.3,
            "is_stabilized": True
        }
        res = compute_stabilization_percentage(m_data, is_stabilized=True)
        # Expected: (6.6 - 6.1 + 7.4 + 19.3) / 4 = 27.2 / 4 = +6.8% (not +11.1%)
        self.assertEqual(res, "+6.8%")

    def test_compute_stabilization_percentage_clean_improvement(self):
        """Test that compute_stabilization_percentage cleanly averages balanced improvements."""
        from scripts.pipeline import compute_stabilization_percentage
        # Test 3266 scenario: 0.70x / 0.50x roll damping produces all positive metrics
        m_data = {
            "pct_peak": 2.4,
            "pct_jitter": 4.2,
            "pct_overall": 6.0,
            "pct_dy": 13.4,
            "is_stabilized": True
        }
        res = compute_stabilization_percentage(m_data, is_stabilized=True)
        # Expected: (4.2 + 2.4 + 6.0 + 13.4) / 4 = 26.0 / 4 = +6.5%
        self.assertEqual(res, "+6.5%")

    def test_compute_stabilization_percentage_ignores_negative_planar_drift(self):
        """Test that negative 2D planar drift from 360 spherical counter-rotation is excluded."""
        from scripts.pipeline import compute_stabilization_percentage
        m_data = {
            "pct_peak": 5.0,
            "pct_jitter": 5.0,
            "pct_overall": -15.0,  # Spherical counter-rotation drift artifact
            "pct_dy": 11.0,
            "is_stabilized": True
        }
        res = compute_stabilization_percentage(m_data, is_stabilized=True)
        # Expected: average of [5.0, 5.0, 11.0] = 21.0 / 3 = +7.0%
        self.assertEqual(res, "+7.0%")

    def test_compute_stabilization_percentage_prioritizes_sidecar_score(self):
        """Test that high-confidence 3D sidecar tremor reduction is prioritized over 2D planar noise."""
        from scripts.pipeline import compute_stabilization_percentage
        # Kopf scenario: 2D planar tracker sees coordinate shifts (-3.4%, -1.3%, -4.6%), but sidecar absorbed 84.7% tremor
        m_data = {
            "pct_peak": -3.4,
            "pct_jitter": -1.3,
            "pct_dy": -4.6,
            "pct_overall": -1.4,
            "is_stabilized": True
        }
        res = compute_stabilization_percentage(m_data, is_stabilized=True, sidecar_pct=84.7)
        self.assertEqual(res, "+84.7%")

    def test_compute_stabilization_percentage_no_negative_yes(self):
        """Test that confirmed 3D stabilization never outputs negative score alongside YES."""
        from scripts.pipeline import compute_stabilization_percentage
        # Cinematic scenario: vertical lock is negative (-24.4%) due to smoothing curve, but peak shock and jitter improved
        m_data = {
            "pct_peak": 10.5,
            "pct_jitter": 8.1,
            "pct_dy": -24.4,
            "pct_overall": -6.2,
            "is_stabilized": True
        }
        res = compute_stabilization_percentage(m_data, is_stabilized=True)
        self.assertTrue(res.startswith("+"), f"Expected positive score for stabilized stage, got {res}")
        self.assertEqual(res, "+9.3%")

    def test_detect_pitch_extrema_prioritizes_telemetry_over_motion(self):
        """Test that detect_pitch_extrema_checkpoints prioritizes physical IMU telemetry over residual optical motion."""
        from scripts.stabilize_horizon import detect_pitch_extrema_checkpoints
        # Mock telemetry file with periodic walking stride nodding
        telem_file = os.path.join(self.test_dir, "gait_test_telemetry.txt")
        with open(telem_file, "w", encoding="utf-8", newline="\n") as f:
            f.write("AngleX(deg)\tAngleY(deg)\tAngleZ(deg)\n")
            for i in range(100):
                pitch = 16.0 + 2.5 * np.sin(2 * np.pi * i / 20.0)
                roll = -5.0 + 0.8 * np.cos(2 * np.pi * i / 20.0)
                f.write(f"{roll:.4f}\t{pitch:.4f}\t0.0000\n")

        # Mock flat motion file (e.g. Kopf running on already stabilized video)
        motion_file = os.path.join(self.test_dir, "flat_motion.kopf360motion")
        with open(motion_file, "w", encoding="utf-8", newline="\n") as f:
            f.write("# Format: frame is_keyframe yaw_deg pitch_deg roll_deg\n")
            for i in range(100):
                f.write(f"{i} 0 0.000000 0.000000 0.000000\n")

        cps = detect_pitch_extrema_checkpoints(100, fps=30.0, min_prominence_deg=0.8,
                                               motion_file=motion_file, telemetry_file=telem_file,
                                               neutral_baseline=False, baseline_pitch=0.0, baseline_roll=0.0)
        self.assertGreaterEqual(len(cps), 8, "Expected gait crests and troughs extracted from IMU telemetry")

    def test_generate_checkpoints_graph_adaptive_decimation(self):
        """Test that generate_checkpoints_graph generates clean, non-crashing graphs for sparse, dense, and empty checkpoints."""
        from scripts.stabilize_horizon import generate_checkpoints_graph

        # Case 1: Sparse checkpoints (<= 20)
        cps_sparse = [{"frame": i * 20, "time": (i * 20) / 30.0, "pitch": float(i), "roll": float(-i), "yaw": 0.0} for i in range(5)]
        traj_sparse = np.zeros((100, 3))
        out_sparse = os.path.join(self.test_dir, "graph_sparse.png")
        ok_sparse = generate_checkpoints_graph(cps_sparse, traj_sparse, 30.0, out_sparse)
        self.assertTrue(ok_sparse)
        self.assertTrue(os.path.exists(out_sparse))
        self.assertGreater(os.path.getsize(out_sparse), 1000)

        # Case 2: Dense checkpoints (400 keyframes, simulating heavy horizon graphs)
        cps_dense = [
            {
                "frame": i * 3,
                "time": (i * 3) / 30.0,
                "pitch": float(5.0 * np.sin(i * 0.1)),
                "roll": float(3.0 * np.cos(i * 0.1)),
                "yaw": float(1.0 * np.sin(i * 0.05)),
            }
            for i in range(400)
        ]
        traj_dense = np.zeros((1200, 3))
        out_dense = os.path.join(self.test_dir, "graph_dense.png")
        ok_dense = generate_checkpoints_graph(cps_dense, traj_dense, 30.0, out_dense)
        self.assertTrue(ok_dense)
        self.assertTrue(os.path.exists(out_dense))
        self.assertGreater(os.path.getsize(out_dense), 1000)

        # Case 3: Empty checkpoints
        cps_empty = []
        traj_empty = np.zeros((60, 3))
        out_empty = os.path.join(self.test_dir, "graph_empty.png")
        ok_empty = generate_checkpoints_graph(cps_empty, traj_empty, 30.0, out_empty)
        self.assertTrue(ok_empty)
        self.assertTrue(os.path.exists(out_empty))
        self.assertGreater(os.path.getsize(out_empty), 1000)

    def test_horizon_report_computes_leveling_score_and_metrics(self):
        """Test that write_single_stage_report for horizon stage prioritizes 3D tilt neutralization over 2D planar jitter."""
        import json
        from unittest import mock
        from scripts.pipeline import write_single_stage_report

        # Create dummy checkpoint file with tilt
        out_base = os.path.join(self.test_dir, "test_vid")
        cp_file = f"{out_base}_horizon_checkpoints.json"
        cps_data = {
            "ignore_yaw": True,
            "fps": 30.0,
            "checkpoints": [
                {"frame": 0, "pitch": -2.4, "roll": -0.2, "yaw": 0.0},
                {"frame": 100, "pitch": 5.0, "roll": 4.5, "yaw": 0.0},
                {"frame": 200, "pitch": -10.0, "roll": 8.0, "yaw": 0.0}
            ]
        }
        with open(cp_file, "w", encoding="utf-8") as f:
            json.dump(cps_data, f)

        # Planar metrics indicating small 2D frame jump change (+0.2%)
        m_data = {
            "is_stabilized": True,
            "num_frames": 200,
            "mean_raw_px": 7.66,
            "mean_stab_px": 7.62,
            "percentage": "+0.5%",
            "dx_mean_raw": 2.89,
            "dx_mean_stab": 3.00,
            "pct_dx": -3.7,
            "dy_mean_raw": 6.51,
            "dy_mean_stab": 6.39,
            "vertical_percentage": "+1.8%",
            "max_raw_px": 20.02,
            "max_stab_px": 20.18,
            "peak_percentage": "-0.8%",
            "rms_jitter_raw_px": 4.15,
            "rms_jitter_stab_px": 4.18,
            "jitter_percentage": "-0.8%",
            "pct_peak": -0.8,
            "pct_jitter": -0.8,
            "pct_overall": 0.5,
            "pct_dy": 1.8
        }

        in_v = f"{out_base}_cinematic.mp4"
        out_v = f"{out_base}_horizon.mp4"
        r_path = f"{out_base}_horizon_report.txt"

        with open(in_v, "w") as f: f.write("dummy")
        with open(out_v, "w") as f: f.write("dummy")

        def fake_analyze(iv, ov, rp, report_title=""):
            with open(rp, "w", encoding="utf-8") as f:
                f.write("Initial placeholder report")
            return m_data

        with mock.patch("scripts.pipeline.analyze_stabilization_report", side_effect=fake_analyze):
            ret_data = write_single_stage_report("horizon", "Horizon (Interactive Leveling)", in_v, out_v, r_path, out_base, "0")

        self.assertTrue(os.path.exists(r_path))
        with open(r_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Must report realistic moderated leveling lock (typically 55% - 85%), NOT planar 0.2% nor unrealistic 98%+
        self.assertIn("STATUS          : YES (HORIZON LEVELED)", content)
        self.assertIn("Horizon Leveling Lock :", content)
        self.assertIn("Peak Tilt Neutralized :", content)
        self.assertIn("Dynamic Roll Sway     :", content)
        self.assertIn("[NOTE] Horizon leveling applies 3D spherical rotations", content)
        # Check that composite_percentage in ret_data was updated to moderated realistic score
        self.assertIsNotNone(ret_data)
        self.assertTrue(50.0 <= ret_data.get("composite_pct_val", 0) <= 85.0)

if __name__ == "__main__":
    unittest.main()

