# Testing Standards, Code Guidelines & Developer Protocols

This document defines the testing architecture, coding conventions, Python runtime constraints, and contribution protocols for **StitchStab 360**.

---

## 🧪 Testing Architecture

All tests reside under `tests/` and are executed using Python's standard `unittest` framework:

```
tests/
├── api/
│   └── test_server_endpoints.py    # FastAPI routes, schemas, file explorer & job endpoints
├── pipeline/
│   └── test_pipeline_stages.py     # Pipeline mock runs, atom preservation, stage sequencing
└── unit/
    ├── test_math_transforms.py     # Spherical projections, Mahony filters, quaternion math
    ├── test_presets.py             # Rig profile configurations and preset schema validation
    ├── test_telemetry.py           # Gyroflow/WitMotion companion logs & telemetry conversion
    └── test_stabilize_horizon.py   # Horizon leveling, visual tilt detection & auto-calibration
```

### Running Tests
Execute tests with the mandatory `-B` flag to block bytecode compilation:

```bash
# Run all discovery tests
python -B -m unittest discover tests

# Run a specific test suite
python -B -m unittest tests/api/test_server_endpoints.py

# Run a single test case
python -B -m unittest tests.api.test_server_endpoints.TestServerEndpoints.test_check_test_exists_endpoint
```

---

## 🛡️ Hermetic Test Guidelines & Strict No-Deletion Policy

1. **Strict No-Deletion Rule**:
   - Tests and code must **NEVER** use functions or methods that delete files or folders (`os.remove`, `os.unlink`, `shutil.rmtree`, `Path.unlink()`, `rmdir()`, etc.).
   - All tests must leave generated files intact for post-test inspection and developer analysis.
2. **Dedicated Test Fixture Directory (`tests/temp/`)**:
   - Any test requiring filesystem assets or producing mock video/metadata outputs must write strictly inside `tests/temp/<suite_name>/` (e.g., `tests/temp/unit_telemetry/`).
   - `tests/temp/` is tracked via `.gitignore` so that generated fixtures do not dirty version control.
3. **Max 2-Second Mockup Rule**:
   - Automated tests must **never** process full-length raw video matrices.
   - For pipeline integration tests, generate synthetic 1–2 second mock video clips or use lightweight mock matrices (`np.zeros((64, 128, 3), dtype=np.uint8)`).

---

## 🧹 Manual Cleanup Guide for Developers & Users

Because StitchStab 360 strictly adheres to a non-destructive file preservation guarantee, automatic background purge routines are completely disabled. All cleanup is performed manually at the user's discretion:

### 1. Cleaning Test Fixtures (`tests/temp/`)
To clear temporary test outputs generated during unit and integration test runs:

* **Windows (PowerShell)**:
  ```powershell
  Get-ChildItem -Path tests\temp -Exclude .gitignore | Remove-Item -Recurse -Force
  ```
* **Windows (Command Prompt)**:
  ```cmd
  del /s /q tests\temp\*.*
  ```
* **Linux / macOS (Bash)**:
  ```bash
  find tests/temp/ -mindepth 1 ! -name '.gitignore' -exec rm -rf {} +
  ```

### 2. Cleaning Runtime Work & Intermediate Files (`data/runtime/work/`)
To clean intermediate stitching stages (`_1_stitched.mp4`), trajectory motion files (`.trf`, `.kopf360motion`), or diagnostic plots:

* **Windows (PowerShell)**:
  ```powershell
  Get-ChildItem -Path data\runtime\work -File | Remove-Item -Force
  ```
* **Linux / macOS (Bash)**:
  ```bash
  rm -rf data/runtime/work/*
  ```

---

## ⚡ Mandatory Python Fresh Execution Guard

To prevent stale `__pycache__` artifacts and cross-environment bytecode inconsistencies, the repository enforces strict runtime constraints:

### 1. File Header Guard
Every Python script (`.py`) in this repository **must** include this guard at the very top of the file (immediately following any shebang):

```python
import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
```

### 2. Execution Flags
- When invoking Python via CLI, always specify `-B`:
  ```bash
  python -B script.py
  ```
- In subprocess invocations (`subprocess.Popen` / `subprocess.run`), pass `PYTHONDONTWRITEBYTECODE=1` in the `env` dictionary.

---

## 📝 Documentation & Comment Rules

### 1. Continuous Documentation Sync
> [!IMPORTANT]
> Whenever any script, endpoint, pipeline stage, or configuration parameter is added or modified, corresponding documentation in `docs/` and `README.md` must be updated immediately in the same commit.

### 2. Google-Style Docstrings
Every function, class, and module must include complete docstrings adhering to Google format:

```python
def angle_distance(r1: tuple[float, float, float], r2: tuple[float, float, float]) -> float:
    """Compute angular distance between two (roll, pitch, yaw) orientation tuples in degrees.

    Args:
        r1: First (roll, pitch, yaw) sequence in degrees.
        r2: Second (roll, pitch, yaw) sequence in degrees.

    Returns:
        float: Angular Euclidean distance accounting for circular angle wrapping.

    Raises:
        ValueError: If input tuple has length other than 3.
    """
```

### 3. Code Preservation
- Existing developer comments and notes must be preserved.
- Code indentation and style must remain consistent without broad cosmetic reformatting.
- Enforce Linux end-of-line (`\n`) across all file writes.

---

## 🧠 Git RAG AI Memory Engine

The repository includes a Git RAG memory engine to record and query architectural decisions:

- **Record Decision**:
  ```bash
  python -B scripts/git_rag.py record "Title" "Rationale and engineering context" --target=markdown
  ```
- **Query Memory**:
  ```bash
  python -B scripts/git_rag.py query "stabilization"
  ```
- **Database & Hooks**:
  - Architectural decisions are stored in `.agents/memory/decisions.md`.
  - Learnings are stored in `.agents/memory/learnings.md`.
  - Git hooks (`.git/hooks/pre-commit`, `post-commit`) maintain search index synchronization and enforce test passing before every commit.
