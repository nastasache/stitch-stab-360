import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import unittest
import glob
import re
import json

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

class TestServerEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from server import app
        from fastapi.testclient import TestClient
        cls.client = TestClient(app)

    def test_dashboard_index_html(self):
        """Root endpoint / should render the HTML dashboard."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        text = response.text
        self.assertTrue("<!DOCTYPE html>" in text or "<html" in text)
        self.assertIn('id="input_name"', text)

    def test_get_next_prefix(self):
        """Stitch API next-prefix endpoint should return valid 4-digit numeric prefix."""
        response = self.client.get("/api/v1/jobs/next-prefix")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get("status"), "success")
        self.assertIn("prefix", data)
        prefix = int(data["prefix"])
        self.assertTrue(1000 <= prefix <= 9999)

    def test_presets_list_action(self):
        """Presets API /api/v1/presets should return available presets."""
        response = self.client.get("/api/v1/presets")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))
        self.assertIn("presets", data)
        self.assertIsInstance(data["presets"], list)

    def test_check_intermediate_videos(self):
        """Stitch API /api/v1/jobs/intermediates should return dictionary of status flags."""
        work_files = glob.glob(os.path.join(REPO_ROOT, "data", "runtime", "work", "*out_[0-9]*.*"))
        test_num = "1001"
        if work_files:
            m = re.search(r'_out_([0-9]+)', work_files[0])
            if m:
                test_num = m.group(1)
        response = self.client.get(f"/api/v1/jobs/intermediates?test_num={test_num}")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get("status"), "success")

    def test_check_test_exists_endpoint(self):
        """Jobs API /api/v1/jobs/check-test should correctly detect test existence in data/runtime/work/."""
        from unittest.mock import patch
        with patch("routers.jobs.glob.glob", side_effect=lambda pat: ["data/runtime/work/dummy_sample_out_8888.txt"] if "8888" in pat else []):
            # Test existing test ID
            resp_existing = self.client.get("/api/v1/jobs/check-test?test_num=8888")
            self.assertEqual(resp_existing.status_code, 200)
            data_existing = resp_existing.json()
            self.assertEqual(data_existing.get("status"), "success")
            self.assertTrue(data_existing.get("exists"))
            self.assertEqual(data_existing.get("test_id"), "8888")
            self.assertGreater(data_existing.get("count"), 0)

            # Test via output filename parameter
            resp_out = self.client.get("/api/v1/jobs/check-test?output=dummy_sample_out_8888.MP4")
            self.assertEqual(resp_out.status_code, 200)
            self.assertTrue(resp_out.json().get("exists"))

            # Test non-existent test ID
            resp_missing = self.client.get("/api/v1/jobs/check-test?test_num=9999999")
            self.assertEqual(resp_missing.status_code, 200)
            data_missing = resp_missing.json()
            self.assertEqual(data_missing.get("status"), "success")
            self.assertFalse(data_missing.get("exists"))
            self.assertEqual(data_missing.get("count"), 0)

    def test_check_intermediate_videos_during_exporting(self):
        """Intermediates API should suppress output_file when status is exporting."""
        status_file = os.path.join(REPO_ROOT, "data", "runtime", "temp", "status.json")
        os.makedirs(os.path.dirname(status_file), exist_ok=True)
        orig_content = None
        if os.path.exists(status_file):
            with open(status_file, "r", encoding="utf-8") as f:
                orig_content = f.read()
        try:
            with open(status_file, "w", encoding="utf-8") as f:
                json.dump({"status": "exporting", "phase": "exporting", "output": "test_output.mp4"}, f)
            response = self.client.get("/api/v1/jobs/intermediates?output=test_output.mp4")
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data.get("status"), "success")
            self.assertEqual(data.get("output_file"), "")
            self.assertEqual(data.get("final"), "")
        finally:
            if orig_content is not None:
                with open(status_file, "w", encoding="utf-8") as f:
                    f.write(orig_content)
            elif os.path.exists(status_file):
                with open(status_file, "w", encoding="utf-8") as f:
                    json.dump({"status": "idle"}, f)

    def test_video_info_missing_file_handled(self):
        """Stitch API /api/v1/videos/info should handle nonexistent file gracefully."""
        response = self.client.get("/api/v1/videos/info?input=nonexistent_file_xyz.mp4")
        self.assertEqual(response.status_code, 404)
        data = response.json()
        self.assertEqual(data.get("status"), "error")

    def test_security_path_traversal(self):
        """Path traversal via input param must be blocked (uses a real file that exists: server.py)."""
        # Attempt to reach server.py (exists on disk) via traversal — must be rejected
        res = self.client.get("/api/v1/videos/info?input=../../server.py")
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["status"], "error")
        self.assertIn("not found", res.json()["error"])

    def test_security_output_path_traversal_blocked(self):
        """Output path injection in start action should be rejected or safely clamped."""
        import io
        form = {"action": "start", "input": "nonexistent.mp4", "output": "../../windows/win.ini"}
        res = self.client.post("/api/v1/jobs", data=form)
        # Either rejects with error (input missing) or returns error status; must NOT succeed
        data = res.json()
        self.assertEqual(data.get("status"), "error")

    def test_security_root_static_mount_block(self):
        """Sensitive source files should not be accessible from the root static mount."""
        res = self.client.get("/server.py")
        self.assertEqual(res.status_code, 404)
        res = self.client.get("/requirements.txt")
        self.assertEqual(res.status_code, 404)
        res = self.client.get("/start.bat")
        self.assertEqual(res.status_code, 404)

    def test_security_large_payload(self):
        """Extremely large payloads should be blocked by middleware."""
        large_body = b"x" * (11 * 1024 * 1024)
        res = self.client.post("/api/v1/presets", content=large_body)
        self.assertEqual(res.status_code, 413)

    def test_cancel_nonexistent_job(self):
        """Cancel action for a non-existent job_id should return error, not 500."""
        res = self.client.get("/api/v1/jobs/status?job_id=nonexistent_job_xyz")
        # Must not crash — either success (idle) or safe error
        self.assertIn(res.status_code, [200, 404])

    def test_cancel_traversal_job_id(self):
        """Traversal in job_id param for cancel must not escape to filesystem."""
        res = self.client.get("/api/v1/jobs/status?job_id=../../config/run_counter")
        self.assertIn(res.status_code, [200, 400, 404])
        if res.status_code == 200:
            self.assertNotIn("run_counter", str(res.json()))

    def test_missing_output_param_raises_422(self):
        """Missing output parameter in start job must return HTTP 422 error, not use fallback."""
        form = {"action": "start", "input": "data/input/videos/test.mp4"}
        res = self.client.post("/api/v1/jobs", data=form)
        self.assertEqual(res.status_code, 422)
        data = res.json()
        self.assertEqual(data.get("status"), "error")
        self.assertEqual(data.get("parameter"), "output")
        self.assertIn("Missing required parameter: 'output'", data.get("error", ""))

    def test_missing_input_param_raises_422(self):
        """Missing input parameter in start job must return HTTP 422 error."""
        form = {"action": "start", "output": "test_stitched.mp4"}
        res = self.client.post("/api/v1/jobs", data=form)
        self.assertEqual(res.status_code, 422)
        data = res.json()
        self.assertEqual(data.get("status"), "error")
        self.assertEqual(data.get("parameter"), "input")
        self.assertIn("Missing required parameter: 'input'", data.get("error", ""))

    def test_config_settings_and_json(self):
        """Centralized config in config/settings.py and config/config.json must exist and be consistent."""
        from config.settings import PATHS, SERVER_CONFIG, PIPELINE_DEFAULTS
        self.assertIn("BASE_DIR", PATHS)
        self.assertEqual(SERVER_CONFIG["PORT"], 8000)
        self.assertIn("ih_fov", PIPELINE_DEFAULTS)
        self.assertIn("blend_width", PIPELINE_DEFAULTS)

        config_json_path = os.path.join(REPO_ROOT, "config", "config.json")
        self.assertTrue(os.path.exists(config_json_path))
        with open(config_json_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        self.assertEqual(cfg["server"]["port"], 8000)
        self.assertEqual(cfg["pipeline_defaults"]["ih_fov"], PIPELINE_DEFAULTS["ih_fov"])

        # Must also be accessible over HTTP for frontend consumption
        res = self.client.get("/config/config.json")
        self.assertEqual(res.status_code, 200)
        http_cfg = res.json()
        self.assertEqual(http_cfg["server"]["port"], 8000)
        self.assertEqual(http_cfg["pipeline_defaults"]["ih_fov"], PIPELINE_DEFAULTS["ih_fov"])

    def test_streetview_find_map(self):
        """Find map must return 200 not_found when map does not exist, and success when deterministic map exists."""
        res = self.client.get("/api/v1/streetview/find-map?input=data%2Finput%2Fvideos%2Fnonexistent_test.mp4")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "not_found")
        self.assertIsNone(data["map_url"])

        from unittest.mock import patch
        with patch("os.path.exists", side_effect=lambda p: True if "test_video_123_streetview_map.html" in str(p) else os.path.exists(p)):
            res_found = self.client.get("/api/v1/streetview/find-map?input=test_video_123.mp4")
            self.assertEqual(res_found.status_code, 200)
            data_found = res_found.json()
            self.assertEqual(data_found["status"], "success")
            self.assertEqual(data_found["map_url"], "data/output/test_video_123_streetview_map.html")

    def test_concurrent_job_rejection_409(self):
        """When an active pipeline job is executing, new start job requests must return 409 Conflict."""
        from unittest.mock import patch
        with patch("routers.jobs.get_active_job", return_value=("mock_job_999", 12345)):
            response = self.client.post("/api/v1/jobs", data={
                "input": "data/input/videos/test_fisheye.mp4",
                "output": "data/output/test_out_9999.MP4"
            })
            self.assertEqual(response.status_code, 409)
            data = response.json()
            self.assertEqual(data.get("status"), "error")
            self.assertEqual(data.get("job_id"), "mock_job_999")
            self.assertIn("already in progress", data.get("error", ""))

    def test_preflight_storage_rejection_400(self):
        """When free disk space is insufficient, job start request must be rejected with 400 Bad Request."""
        from unittest.mock import patch
        with patch("routers.jobs.get_active_job", return_value=None):
            with patch("routers.jobs.check_disk_space", return_value=(False, "Insufficient disk space on working volume: estimated 5.00 GB required, but only 0.50 GB is available.", 5.0, 0.5)):
                response = self.client.post("/api/v1/jobs", data={
                    "input": "data/input/videos/test_fisheye.mp4",
                    "output": "data/output/test_out_9999.MP4"
                })
                self.assertEqual(response.status_code, 400)
                data = response.json()
                self.assertEqual(data.get("status"), "error")
                self.assertIn("Insufficient disk space", data.get("error", ""))
                self.assertEqual(data.get("required_gb"), 5.0)
                self.assertEqual(data.get("available_gb"), 0.5)

    def test_detect_checkpoints_cadence_endpoint(self):
        """Verify detect-checkpoints endpoint supports source=cadence."""
        response = self.client.get("/api/v1/jobs/detect-checkpoints?source=cadence&total_frames=90&fps=30.0&density=balanced")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get("status"), "success")
        self.assertEqual(data.get("source"), "cadence")
        self.assertEqual(data.get("checkpoints_count"), 7)
        self.assertIn("data", data)
        self.assertEqual(len(data["data"]["checkpoints"]), 7)

    def test_detect_checkpoints_ultra_density_endpoint(self):
        """Verify detect-checkpoints endpoint maps ultra density to epsilon=0.75."""
        response = self.client.get("/api/v1/jobs/detect-checkpoints?source=cadence&total_frames=90&fps=30.0&density=ultra")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get("status"), "success")
        self.assertEqual(data.get("density"), "ultra")
        self.assertEqual(data.get("epsilon"), 0.75)

    def test_detect_checkpoints_custom_epsilon_endpoint(self):
        """Verify detect-checkpoints endpoint directly accepts custom decimal epsilon from UI slider."""
        response = self.client.get("/api/v1/jobs/detect-checkpoints?source=cadence&total_frames=90&fps=30.0&epsilon=0.90")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get("status"), "success")
        self.assertEqual(data.get("epsilon"), 0.90)

    def test_detect_checkpoints_pitch_extrema_endpoint(self):
        """Verify detect-checkpoints endpoint supports source=pitch_extrema."""
        response = self.client.get("/api/v1/jobs/detect-checkpoints?source=pitch_extrema&total_frames=90&fps=30.0&min_prominence=1.0")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get("status"), "success")
        self.assertEqual(data.get("source"), "pitch_extrema")
        self.assertIn("data", data)
        self.assertEqual(data["data"].get("sourceType"), "pitch_extrema")
        self.assertIn("crests", data["data"])
        self.assertIn("troughs", data["data"])
        self.assertIsInstance(data["data"]["checkpoints"], list)

    def test_csp_headers_contain_localhost_and_127(self):
        """Verify CSP headers permit both 127.0.0.1 and localhost for media streaming."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        csp = response.headers.get("content-security-policy", "")
        self.assertIn("http://127.0.0.1:8000", csp)
        self.assertIn("http://localhost:8000", csp)
        self.assertIn("media-src 'self' http://127.0.0.1:8000 http://localhost:8000 blob: data:;", csp)

    def test_preview_with_skip_stitching(self):
        """Verify api/v1/videos/preview handles skip_stitching=1 for already-stitched equirectangular inputs."""
        inputs = glob.glob("data/input/videos/*.MP4") + glob.glob("data/input/videos/*.mp4")
        if not inputs:
            self.skipTest("No sample video available for preview test.")
        response = self.client.post("/api/v1/videos/preview", data={
            "input": inputs[0],
            "skip_stitching": "1",
            "preview_time": "0.0"
        })
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get("status"), "success")
        self.assertIn("preview", data)

    def test_detect_checkpoints_auto_with_embedded_telemetry(self):
        """Verify detect-checkpoints extracts on-demand telemetry from video with embedded IMU atoms."""
        target_vid = "17_bridge_ST.MP4"
        if not os.path.exists(os.path.join("data", "input", "videos", target_vid)):
            self.skipTest(f"{target_vid} not present.")
        response = self.client.get(f"/api/v1/jobs/detect-checkpoints?video={target_vid}&source=auto")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get("status"), "success")
        self.assertEqual(data.get("source"), "pitch_extrema")
        self.assertGreater(data.get("checkpoints_count", 0), 0)
        self.assertIn("data", data)
        self.assertIn("checkpoints", data["data"])

    def test_video_info_has_telemetry_flag(self):
        """Verify api/v1/videos/info returns has_telemetry boolean."""
        target_vid = "17_bridge_ST.MP4"
        if not os.path.exists(os.path.join("data", "input", "videos", target_vid)):
            self.skipTest(f"{target_vid} not present.")
    def test_intermediates_traveldir_resolution_and_final_file(self):
        """Verify test 3325 intermediate traveldir video avoids master render collisions and resolves final video."""
        if not os.path.exists("data/runtime/work/17_bridge_ST_FULL_VR_out_3325_config.json"):
            self.skipTest("Test 3325 config file not found.")
        response = self.client.get("/api/v1/jobs/intermediates?test_num=3325")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get("status"), "success")
        traveldir = data.get("traveldir", "")
        self.assertIn("vidstab", traveldir)
        self.assertTrue(traveldir.endswith("traveldir_VR.MP4") or traveldir.endswith("traveldir.MP4"))
        self.assertEqual(data.get("final"), "data/runtime/work/17_bridge_ST_FULL_VR_out_3325.MP4")

    def test_sync_horizon_log_endpoint(self):
        """Verify POST /api/v1/jobs/sync-horizon-log writes the audit log to work directory."""
        test_out_base = "test_sync_log_unit"
        log_content = "================================================================================\n360° HORIZON STABILIZATION PARAMETER AUDIT LOG\n================================================================================\nTest / Target Base   : test_sync_log_unit\nStrategy / Mode      : Manual Snap / Default\nDetection Source     : none\nKeyframe Density     : Balanced (ε=2.5°)\n"
        response = self.client.post(
            "/api/v1/jobs/sync-horizon-log",
            json={"out_base": test_out_base, "log_text": log_content}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get("status"), "success")
        expected_file = f"data/runtime/work/{test_out_base}_horizon_params.log"
        self.assertTrue(os.path.exists(expected_file))
        with open(expected_file, "r", encoding="utf-8") as f:
            saved_text = f.read()
        self.assertIn("Manual Snap / Default", saved_text)

if __name__ == "__main__":
    unittest.main()

