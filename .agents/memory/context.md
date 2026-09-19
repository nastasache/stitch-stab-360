# High-Level Architecture & Domain Context

## 1. System Overview
StitchStab 360 is an automated dual-fisheye 360° video stitching, multi-stage optical stabilization, horizon leveling, and Street View packaging pipeline.

- **Backend**: Python 3.10+ with FastAPI running on host port 8000 (`server.py`).
- **Frontend**: Client-side Vanilla JavaScript (ES6+), HTML5 Canvas/WebGL, and native CSS (`app.js`, `styles.css`, `index.html`, `horizon_editor.html`, `route_editor.html`). No Node.js build step or package managers.
- **Subprocess Execution**: Long-running FFmpeg and computer vision pipelines are dispatched asynchronously via `run_async_subprocess()` in `routers/jobs.py`.

---

## 2. Key Modules & Directory Structure
- [`server.py`](../../server.py): FastAPI application root; mounts static assets and binds routers (`/api/jobs`, `/api/fs`, `/api/presets`, `/api/tools`).
- [`routers/jobs.py`](../../routers/jobs.py): Handles job queueing, active process polling, horizon auto-detection, and pipeline execution.
- [`scripts/pipeline.py`](../../scripts/pipeline.py): Central pipeline orchestrator driving stitching, stabilization, audio handling, and metadata injection.
- [`scripts/stabilize_horizon.py`](../../scripts/stabilize_horizon.py): Horizon auto-detection and SLERP quaternion keyframe interpolation for leveling.
- [`scripts/stabilize_traveldir.py`](../../scripts/stabilize_traveldir.py): Directional heading lock with angular deadband and damping.
- [`scripts/stabilize_vidstab.py`](../../scripts/stabilize_vidstab.py): 90° rectilinear viewport generation and `libvidstab` tremor absorption.
- [`scripts/crop_camera_video.py`](../../scripts/crop_camera_video.py): Atom-preserving video slicer for dual-fisheye/360 files.
- [`scripts/git_rag.py`](../../scripts/git_rag.py): AI Memory Engine CLI for querying and recording architectural decisions.
- [`utils/tool_resolver.py`](../../utils/tool_resolver.py): Automatically locates and validates specialized FFmpeg binaries (Gyan build with `libvidstab` and `v360`).

---

## 3. The 8-Stage Stabilization Sequence
Stabilization operations follow a strict mathematical and perceptual hierarchy:
$$\text{Telemetry} \rightarrow \text{Kopf} \rightarrow \text{Kabsch} \rightarrow \text{VidStab} \rightarrow \text{Cinematic} \rightarrow \text{Horizon} \rightarrow \text{TravelDir} \rightarrow \text{Nadir}$$

1. **`telemetry`**: IMU gyro/accelerometer sensor fusion (Mahony or EKF filters). Reads embedded `vrot` atoms, `*_telemetry.txt`, or companion Gyroflow `.gcsv` logs.
2. **`kopf`**: Cube-face optical flow feature tracking (1024 face resolution, deformed mesh tracking) for non-rigid motion estimation.
3. **`kabsch`**: SVD-based rigid 3D rotational alignment aligning consecutive frame coordinate systems.
4. **`vidstab`**: High-frequency tremor absorption executed on distortion-free 90° rectilinear front viewports to compute sub-pixel phase correlation.
5. **`cinematic`**: L1-norm smooth trajectory optimization enforcing sparsity in acceleration and jerk.
6. **`horizon`**: Absolute ground/skyline leveling using inflection checkpoints and SLERP quaternion interpolation.
7. **`traveldir`**: Viewer heading stabilization locking the camera trajectory to the forward travel vector.
8. **`nadir`**: Blends a circular nadir logo patch over the tripod/mount footprint.

---

## 4. Invariant Engineering Rules
- **Bytecode Blocking**: Every Python script must begin with `sys.dont_write_bytecode = True` and be invoked with `-B`.
- **Absolute No-Deletion**: AI assistants must never delete files or directories (`os.remove`, `rmtree`, `del`). All test writes go to `tests/temp/`.
- **Telemetry Retention**: Video cropping must retain private metadata atoms (`moov/udta/vrot`, `m360`, `opax`, `opai`, `gpmd`, `camm`) frame-for-frame.
- **Port Invariance**: Server port 8000 (`config/settings.py`) must not be hardcoded or changed.
- **Windows Host Native**: Shell commands must be Windows/PowerShell compatible with backslash (`\`) paths.
