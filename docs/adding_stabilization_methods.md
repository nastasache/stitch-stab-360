# Adding a New Stabilization Method — Developer Integration Guide

This guide provides a comprehensive, step-by-step walkthrough for developers looking to add a new stabilization algorithm or smoothing pass to **StitchStab 360**.

It covers the complete lifecycle: authoring the algorithm script, chaining into the multi-stage cascade orchestrator, exposing REST API parameters, adding UI controls in the web dashboard, and writing automated hermetic tests.

---

## 🏗️ 1. Stabilization Architecture Overview

StitchStab 360 stabilizes 360° equirectangular video by computing spherical rotation trajectories (Yaw, Pitch, Roll in degrees) and applying them via FFmpeg's `v360` filter or Single-Pass Master Render graphs.

### The Multi-Stage Cascade
Multiple stabilization passes can be chained in any custom sequence (e.g. `telemetry,vidstab,cinematic,horizon,traveldir`).
- **Sequential Refinement**: Each stage can take the output of prior stages into account.
- **Transform Formats**:
  - **Absolute Orientation (`sendcmd`)**: Specifies the camera's absolute orientation at timestamp $t$:
    ```text
    0.000000 [enter] v360 yaw 1.250000, pitch -0.420000, roll 0.150000;
    0.033333 [enter] v360 yaw 1.280000, pitch -0.410000, roll 0.140000;
    ```
  - **Differential Deltas (`# format: delta`)**: Used by relative adjustment stages (such as `horizon` and `traveldir`):
    ```text
    # format: delta
    0.000000 [enter] v360 yaw 0.000000, pitch -1.250000, roll 0.350000;
    0.033333 [enter] v360 yaw -0.050000, pitch -1.240000, roll 0.340000;
    ```
- **Single-Pass Rendering (Modes 1 & 4)**: The pipeline composes multiple stage transforms into a unified compound command file or single filter graph, avoiding multi-generation re-encoding loss.

---

## 📋 2. Integration Checklist

Adding a new stabilization method involves five coordinated steps:

| Step | Component | File(s) | Role |
| :--- | :--- | :--- | :--- |
| **1** | **Algorithm Engine** | `scripts/stabilize_<name>.py` | Standalone script computing per-frame Yaw, Pitch, Roll rotations. |
| **2** | **Pipeline Orchestrator** | `scripts/pipeline.py` | Registers CLI flags, dispatches the stage in the cascade loop, and handles reports/curves. |
| **3** | **Backend API** | `routers/jobs.py` & `server.py` | Validates API options and passes CLI arguments to the background worker. |
| **4** | **Web Dashboard** | `index.html`, `app.js`, `styles.css` | Adds UI sliders, toggle cards, and includes the method in the drag-and-drop order list. |
| **5** | **Testing & Documentation** | `tests/pipeline/`, `docs/` | Adds synthetic mockup test case and updates documentation. |

---

## ⚙️ Step 1: Author the Engine Script (`scripts/stabilize_<name>.py`)

Create your algorithm script under `scripts/`. Name it following the `stabilize_<name>.py` convention (e.g. `scripts/stabilize_kalman.py`).

### Mandatory Standards
1. **Python Bytecode Guard**: The top of the file **must** include the header guard:
   ```python
   #!/usr/bin/env python3
   import sys, os
   sys.dont_write_bytecode = True
   os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
   ```
2. **CLI Contract**:
   - `--input`: Path to input video file or stitched intermediate.
   - `--output`: Path to write the generated `sendcmd` text file.
   - `--sendcmd-in` (optional): Path to upstream `sendcmd` file to compound with or refine.
   - `--fps`: Stream framerate (default `30.0`).
   - Method-specific hyperparameters (e.g., `--window`, `--threshold`).
   - `--test`: Hermetic self-test flag executing on synthetic arrays without video I/O.
3. **Docstrings**: Fully documented functions adhering to Google style.

### Reference Boilerplate Template
Save as `scripts/stabilize_<name>.py`:

```python
#!/usr/bin/env python3
import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import argparse
import numpy as np

def compute_corrections(input_path: str, upstream_sendcmd: str | None = None,
                        param_smoothing: float = 0.5) -> list[tuple[float, float, float, float]]:
    """Compute per-frame stabilization angles (timestamp, yaw, pitch, roll).

    Args:
        input_path: Path to the input equirectangular video.
        upstream_sendcmd: Optional path to prior stage's sendcmd file.
        param_smoothing: Smoothing factor between 0.0 and 1.0.

    Returns:
        List of (timestamp_seconds, yaw_deg, pitch_deg, roll_deg) tuples.
    """
    corrections = []
    # Implementation: Read video frames / optical flow / IMU telemetry
    # Calculate yaw, pitch, roll counter-rotations in degrees.
    return corrections

def write_sendcmd_file(output_path: str, corrections: list[tuple[float, float, float, float]],
                       is_delta: bool = False) -> None:
    """Write trajectory corrections in FFmpeg v360 sendcmd format."""
    with open(output_path, "w", encoding="utf-8", newline="\n") as f:
        if is_delta:
            f.write("# format: delta\n")
        for ts, yaw, pitch, roll in corrections:
            f.write(f"{ts:.6f} [enter] v360 yaw {yaw:.6f}, pitch {pitch:.6f}, roll {roll:.6f};\n")

def run_synthetic_test() -> bool:
    """Hermetic unit validation using synthetic coordinate arrays."""
    # Verify math consistency without invoking external media decoders
    test_traj = [(0.0, 0.0, 0.0, 0.0), (0.033, 1.0, -0.5, 0.2)]
    assert len(test_traj) == 2, "Test trajectory initialization failed"
    return True

def main():
    parser = argparse.ArgumentParser(description="Custom 360 Video Stabilization Engine")
    parser.add_argument("--input", required=False, help="Input video path")
    parser.add_argument("--output", required=False, help="Output sendcmd file path")
    parser.add_argument("--sendcmd-in", default=None, help="Upstream sendcmd file")
    parser.add_argument("--fps", type=float, default=30.0, help="Video framerate")
    parser.add_argument("--smoothing", type=float, default=0.5, help="Algorithm smoothing factor")
    parser.add_argument("--test", action="store_true", help="Run synthetic self-test")
    args = parser.parse_args()

    if args.test:
        success = run_synthetic_test()
        sys.exit(0 if success else 1)

    if not args.input or not args.output:
        parser.error("--input and --output are required unless --test is specified.")

    corrections = compute_corrections(args.input, args.sendcmd_in, args.smoothing)
    write_sendcmd_file(args.output, corrections)
    print(f"Generated stabilization sendcmd: {args.output}")

if __name__ == "__main__":
    main()
```

---

## 🔗 Step 2: Hook into the Pipeline Orchestrator (`scripts/pipeline.py`)

`scripts/pipeline.py` drives the multi-stage cascade. You need to connect your new stage in four places:

### 1. Register CLI Arguments in `build_parser()`
Search for `parser.add_argument("--stabilize_methods"` in `pipeline.py` and register your method's CLI parameters:

```python
parser.add_argument("--custom_smoothing", type=float, default=0.5,
                    help="Custom stabilizer smoothing factor (0.0 - 1.0)")
```

### 2. Dispatch Stage in Cascade Loop
Locate the cascade execution loop where `active_methods` or `requested_methods` are executed (around lines 3500–3700). Add a handler block for your method identifier:

```python
elif method == "custom":
    sendcmd_file = f"{out_base}_sendcmd_custom.txt"
    custom_smoothing = getattr(args, 'custom_smoothing', 0.5)
    phase_custom = f"Stabilizing: Custom Algorithm (smoothing: {custom_smoothing})"
    update_status(args.status_file, {
        "status": "stabilizing", "phase": phase_custom,
        "progress": current_progress, "speed": "N/A", "eta": "Calculating...",
        "elapsed": int(time.time() - start_ts)
    })
    
    prev_sendcmd = get_effective_prior_sendcmd(active_sendcmd_files, out_base, "custom")
    custom_cmd = [
        sys.executable, "-B", os.path.join(script_dir, "stabilize_custom.py"),
        "--input", current_file,
        "--output", sendcmd_file,
        "--smoothing", str(custom_smoothing),
        "--fps", str(fps_val)
    ]
    if prev_sendcmd and os.path.exists(prev_sendcmd):
        custom_cmd += ["--sendcmd-in", prev_sendcmd]

    rc = run_ffmpeg(custom_cmd, args.status_file, duration, start_ts, phase_custom, status_code="stabilizing")
    if rc != 0 or not os.path.exists(sendcmd_file) or os.path.getsize(sendcmd_file) == 0:
        print("Warning: stabilize_custom.py failed, skipping custom pass.")
    else:
        # Generate trajectory visualization graphs
        try:
            vis_cmd = [
                sys.executable, "-B", os.path.join(script_dir, "visualize_corrections.py"),
                "--trf", sendcmd_file, "--graph-type", "custom", "--output", out_base, "--fps", str(fps_val)
            ]
            subprocess.run(vis_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=WIN_NO_WINDOW)
        except Exception as e_vis:
            print(f"Notice: visualize_corrections custom error: {e_vis}")

        executed_methods_so_far.append("custom")
        active_sendcmd_files.append(sendcmd_file)
```

### 3. Register Diagnostic Output in `analyze_stabilization_report()`
In `analyze_stabilization_report()`, ensure stage-specific logs (`_custom_report.txt`) are summarized in the net report so quality metrics appear on the dashboard.

---

## 🌐 Step 3: Expose via FastAPI Backend (`routers/jobs.py` & `server.py`)

1. **Parameter Ingestion**:
   In `routers/jobs.py`, locate `start_pipeline_job()` where `gp()` (get parameter) extracts fields from the request form:
   ```python
   custom_smoothing = float(gp("custom_smoothing", "0.5"))
   ```
2. **Command Construction**:
   Append the parameter to `pipeline_cmd`:
   ```python
   if "custom" in stab_methods:
       pipeline_cmd.extend(["--custom_smoothing", str(custom_smoothing)])
   ```
3. **Preset Compatibility**:
   Add defaults for `custom_smoothing` in `config/settings.py` or default presets (`data/input/presets/default.json`).

---

## 🖥️ Step 4: Web Dashboard Integration (`index.html` & `app.js`)

### 1. Add Method Checkbox and Reordering Item
In `index.html`, inside the stabilization methods ordering list:
```html
<li class="stab-order-item" data-method="custom">
    <label class="custom-control-label">
        <input type="checkbox" id="stab_custom" class="form-check-input" checked>
        Custom Smoothing
    </label>
    <span class="drag-handle">☰</span>
</li>
```

### 2. Add Settings Panel / Controls
Add a dedicated options card with inputs and tooltips:
```html
<div class="card mb-3" id="panel_custom_settings">
    <div class="card-header">Custom Stabilizer Options</div>
    <div class="card-body">
        <label for="custom_smoothing" class="form-label">Smoothing Strength: <span id="val_custom_smoothing">0.5</span></label>
        <input type="range" class="form-range" id="custom_smoothing" min="0.0" max="1.0" step="0.05" value="0.5">
    </div>
</div>
```

### 3. Bind Parameters in `app.js`
1. In `getJobPayload()` / form serialization:
   ```javascript
   payload.append('custom_smoothing', document.getElementById('custom_smoothing').value);
   ```
2. In preset load/save handlers:
   Include `custom_smoothing` in the JSON serialization.

---

## 🧪 Step 5: Testing & Verification Standards

### 1. Hermetic Unit Test
Add a test in `tests/pipeline/test_pipeline_stages.py` or `tests/unit/test_custom_stab.py`:
```python
import unittest
import subprocess
import sys, os

class TestCustomStabilizer(unittest.TestCase):
    def test_synthetic_self_test(self):
        """Verify the custom stabilizer self-test passes without external dependencies."""
        cmd = [sys.executable, "-B", os.path.join("scripts", "stabilize_custom.py"), "--test"]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"Self-test failed: {proc.stderr}")
```

### 2. Mockup Pipeline Integration Test
- **Max 2-Second Rule**: Never invoke pipeline tests on full production videos. Use a synthetic 1-second clip generated via FFmpeg `testsrc`:
  ```bash
  ffmpeg -f lavfi -i testsrc=duration=1:size=640x320:rate=30 -c:v libx264 test_mock.mp4
  ```

### 3. Update Code Documentation
Regenerate code references automatically:
```bash
python -B scripts/generate_code_docs.py
```
This extracts docstrings from your new script and automatically inserts them into `docs/code_reference.md`.

---

## 💡 Summary of Invariants & Developer Rules

- 🚫 **Never write bytecode**: Always run with `python -B` and ensure the header guard is present.
- 💾 **Linux EOL**: Ensure line endings are LF (`\n`).
- 🔄 **Continuous Documentation**: Always update `docs/pipeline.md`, `docs/code_reference.md`, and `README.md` when introducing a new method.
- 🎯 **Preserve Sendcmd Syntax**: All output timestamps must be microsecond-precise floats (`{ts:.6f}`) and angles in degrees.
