import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

# Ensure repository root is on sys.path
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

try:
    import pytest
    HAS_PYTEST = True
except ImportError:
    HAS_PYTEST = False

if HAS_PYTEST:
    @pytest.fixture(scope="session")
    def client():
        from server import app
        from fastapi.testclient import TestClient
        return TestClient(app)

    @pytest.fixture
    def mock_telemetry_file():
        """Generates a temporary raw telemetry text file for unit testing in tests/temp."""
        temp_dir = Path(REPO_ROOT) / "tests" / "temp" / "pytest_tmp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        file_path = temp_dir / "mock_telemetry.txt"
        content = (
            "Time(s)\tAngleX(deg)\tAngleY(deg)\tAngleZ(deg)\n"
            "0.000\t0.00\t0.00\t0.00\n"
            "0.010\t0.50\t-0.20\t0.10\n"
            "0.020\t1.00\t-0.40\t0.20\n"
            "0.030\t1.50\t-0.60\t0.30\n"
            "0.040\t2.00\t-0.80\t0.40\n"
            "0.050\t2.50\t-1.00\t0.50\n"
        )
        file_path.write_text(content, encoding="utf-8")
        return str(file_path)

    @pytest.fixture
    def mock_preset_data():
        """Returns standard preset dictionary for testing."""
        return {
            "name": "Test Dual Fisheye Preset",
            "camera_model": "dual_fisheye_test",
            "ih_fov": 190.0,
            "iv_fov": 190.0,
            "left_x_offset": 0.0,
            "left_y_offset": 0.0,
            "right_x_offset": 0.0,
            "right_y_offset": 0.0,
            "rear_pitch_offset": 0.0,
            "rear_yaw_offset": 180.0,
            "rear_roll_offset": 0.0
        }
