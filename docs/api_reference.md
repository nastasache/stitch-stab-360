# FastAPI REST & WebSocket API Reference

This document provides a technical reference for the backend API endpoints exposed by `server.py`.

---

## 🛰️ Base URL & Protocols
- **HTTP**: `http://127.0.0.1:8000/api/v1`
- **WebSocket**: `ws://127.0.0.1:8000/ws`
- **OpenAPI / Swagger UI**: Available interactively at `http://127.0.0.1:8000/docs`

---

## 📋 Jobs API

### 1. Start Job
- **Endpoint**: `POST /api/v1/jobs/start`
- **Description**: Spawns a background worker thread and CLI subprocess to execute the stitching and stabilization pipeline.
- **Request Body (JSON)**:
  ```json
  {
    "input": "data/input/videos/sample.mp4",
    "output": "data/output/sample_out.mp4",
    "preset": "default",
    "test_num": "1001",
    "stabilize": true,
    "stabilize_methods": "telemetry,cinematic,traveldir",
    "telemetry_fusion": "mahony",
    "telemetry_fusion_gain": 0.51,
    "cinematic_window": 45,
    "l1_lambda_acc": 20.0,
    "l1_lambda_vel": 2.0,
    "traveldir_mode": "travel_direction",
    "traveldir_damping": 0.90,
    "traveldir_deadband": 1.5,
    "inject_final_meta": true,
    "nadir_image": "data/input/nadir/logo.png"
  }
  ```
- **Response**:
  ```json
  {
    "status": "started",
    "job_id": "job_20260909_210000",
    "message": "Job initiated successfully."
  }
  ```

### 2. Get Job Status
- **Endpoint**: `GET /api/v1/jobs/status`
- **Description**: Returns live progress metrics, status enum, and elapsed execution time.
- **Response**:
  ```json
  {
    "status": "running",
    "progress": 42,
    "step": "Stabilizing: cinematic trajectory optimization...",
    "elapsed_time": "00:02:15",
    "output_file": "data/output/sample_out.mp4"
  }
  ```
- **Status Values**: `"idle"`, `"running"`, `"exporting"`, `"completed"`, `"failed"`, `"stopped"`.

### 3. Stop Active Job
- **Endpoint**: `POST /api/v1/jobs/stop`
- **Description**: Terminates the currently active pipeline subprocess and marks job status as `"stopped"`.
- **Response**:
  ```json
  {
    "status": "stopped",
    "message": "Active job process terminated."
  }
  ```

### 4. Check Test Existence
- **Endpoint**: `GET /api/v1/jobs/check-test`
- **Query Parameters**:
  - `test_num` (optional, string): Numeric test identifier (e.g. `"2863"`).
  - `output` (optional, string): Target output filename or path.
- **Description**: Inspects `data/runtime/work/` to verify if intermediate files with the given test identifier already exist.
- **Response**:
  ```json
  {
    "status": "success",
    "exists": true,
    "test_id": "2863",
    "count": 4
  }
  ```

### 5. Get Intermediate Videos
- **Endpoint**: `GET /api/v1/jobs/intermediates`
- **Query Parameters**:
  - `input` (optional, string): Input video file name or path.
  - `output` (optional, string): Target final output video path.
  - `test_num` (optional, string): Numeric test identifier (e.g. `3325`).
  - `inject_intermediate_meta` (optional, string): Set to `"1"` to prefer VR metadata injected video files (`_VR.mp4`). Automatically honors `inject_intermediate_meta` from saved pipeline configuration when loading tests by ID.
  - `job_id` (optional, string): Execution job identifier.
- **Description**: Discovers and returns URLs of all intermediate video stages generated during processing (`stitched`, `telemetry`, `kopf`, `kabsch`, `vidstab`, `cinematic`, `horizon`, `traveldir`), along with the final output video, preview frame, original frame, correction graphs, reports, and parameters. Enforces deterministic sequential pipeline chain resolution to prevent master composition renders (`_master` / `chosen_transforms`) from colliding with individual intermediate tabs.
- **Response**:
  ```json
  {
    "status": "success",
    "job_id": "job_3325_3325_1789362839633",
    "target_base": "17_bridge_ST_FULL_VR_out_3325",
    "stitched": "",
    "telemetry": "data/runtime/work/17_bridge_ST_FULL_VR_out_3325_telemetry_VR.MP4",
    "kopf": "data/runtime/work/17_bridge_ST_FULL_VR_out_3325_telemetry_kopf_VR.MP4",
    "kabsch": "data/runtime/work/17_bridge_ST_FULL_VR_out_3325_telemetry_kopf_kabsch_VR.MP4",
    "vidstab": "data/runtime/work/17_bridge_ST_FULL_VR_out_3325_telemetry_kopf_kabsch_vidstab_VR.MP4",
    "cinematic": "data/runtime/work/17_bridge_ST_FULL_VR_out_3325_telemetry_kopf_kabsch_vidstab_cinematic_VR.MP4",
    "horizon": "data/runtime/work/17_bridge_ST_FULL_VR_out_3325_telemetry_kopf_kabsch_vidstab_cinematic_horizon_VR.MP4",
    "traveldir": "data/runtime/work/17_bridge_ST_FULL_VR_out_3325_telemetry_kopf_kabsch_vidstab_cinematic_horizon_traveldir_VR.MP4",
    "final": "data/runtime/work/17_bridge_ST_FULL_VR_out_3325.MP4"
  }
  ```

### 6. Detect Horizon Checkpoints
- **Endpoint**: `GET /api/v1/jobs/detect-checkpoints`
- **Query Parameters**:
  - `source` (string): `"auto" | "cadence" | "vision" | "pitch_extrema"` (default `"auto"`).
  - `out_base` (optional, string): Base path for discovering candidate intermediate artifacts.
  - `video` (optional, string): Candidate video file name.
  - `density` (optional, string): `"smooth" | "balanced" | "fine" | "detailed" | "ultra"`.
  - `optical_target` (optional, string): `"ground" | "skyline"` (default `"ground"`). Anchors vertical eye-level pitch to the ground/road/water contact line vs upper skyline/canopy boundary in Visual Horizon AI.
  - `lock_roll` (optional, boolean/string): Force roll angles to 0.0° for pitch-only leveling (default `"1"`).
  - `min_prominence` (optional, float): Minimum peak-to-trough pitch swing threshold in degrees for Smart Gait & Stride AI (default `0.8`).
  - `roll_damping` (optional, float): Lateral body sway damping multiplier for roll in Smart Gait & Stride AI (default `0.70`).
  - `max_checkpoints` (optional, integer): Optional keyframe budget limit for interactive editor UIs (default `None` / unconstrained).
  - `fps` (optional, float): Target framerate.
  - `total_frames` (optional, integer): Total duration in video frames.
- **Description**: Autodetects timeline keyframes using Smart Gait & Stride AI (`auto` / `pitch_extrema`), visual horizon AI (`vision`), or regular time cadence (`cadence`). If no companion `_telemetry.txt` exists on disk, embedded IMU atoms (`vrot`, `camm`, `gpmf`) are extracted on-demand to `data/runtime/work/`. If no telemetry is present, `auto` mode cleanly falls back to vision.
- **Response**:
  ```json
  {
    "status": "success",
    "source": "pitch_extrema",
    "checkpoints_count": 7,
    "data": {
      "version": "1.0",
      "sourceType": "pitch_extrema",
      "fps": 30.0,
      "totalFrames": 90,
      "ignoreYaw": true,
      "crests": 3,
      "troughs": 2,
      "checkpoints": [
        {"frame": 0, "time": 0.0, "pitch": 0.0, "roll": 0.0, "yaw": 0.0, "type": "boundary"},
        {"frame": 15, "time": 0.5, "pitch": 0.0, "roll": 0.0, "yaw": 0.0, "type": "crest"}
      ]
    }
  }
  ```

### 7. Sync Horizon Parameter Audit Log
- **Endpoint**: `POST /api/v1/jobs/sync-horizon-log`
- **Request Body (JSON)**:
  - `out_base` (optional, string): Test / target run base identifier.
  - `log_text` (optional, string): Complete formatted audit log text.
  - `data` (optional, object): Structured parameters and checkpoints payload.
  - `video_name` (optional, string): Source video name.
- **Description**: Synchronizes the horizon parameter audit log (`{out_base}_horizon_params.log`) to disk in real time upon mode switches, manual edits, or checkpoint resets without triggering a background pipeline resume (`resume_checkpoints.flag`).
- **Response**:
  ```json
  {
    "status": "success",
    "saved_files": ["data/runtime/work/17_bridge_ST_horizon_params.log"],
    "out_base": "17_bridge_ST"
  }
  ```

---

## 🎬 Videos API

### 1. Video Info & Metadata Probe
- **Endpoint**: `GET /api/v1/videos/info`
- **Query Parameters**:
  - `input` (string): Video path or filename.
- **Description**: Probes video dimensions, duration, framerate, total frame count, photo flag, presence of embedded or companion IMU telemetry (`has_telemetry`), and spherical VR projection (`is_vr`).
- **Response**:
  ```json
  {
    "status": "success",
    "duration": 6.57,
    "fps": 29.97,
    "total_frames": 197,
    "width": 3840,
    "height": 1920,
    "is_photo": false,
    "has_telemetry": true,
    "is_vr": true
  }
  ```

---

## ⚙️ Presets API

### 1. List Presets
- **Endpoint**: `GET /api/v1/presets`
- **Description**: Returns an array of available preset names in `data/input/presets/`.
- **Response**:
  ```json
  {
    "status": "success",
    "presets": ["default", "default_all", "default_none", "insta360_x3", "gopro_max"]
  }
  ```

### 2. Get Preset Configuration
- **Endpoint**: `GET /api/v1/presets/{name}`
- **Description**: Returns the JSON dictionary of pipeline parameters for the specified preset.

### 3. Save Preset Configuration
- **Endpoint**: `POST /api/v1/presets/{name}`
- **Description**: Saves or updates custom calibration and stabilization parameter profiles.

---

## 📁 Files & Media Explorer API

### 1. List Directory Files
- **Endpoint**: `GET /api/v1/files/list`
- **Query Parameters**:
  - `folder` (string): `"videos" | "nadir" | "gps" | "masks"`.
- **Response**:
  ```json
  {
    "status": "success",
    "folder": "videos",
    "files": ["sample_dual_fisheye.mp4", "walk_recording.mp4"]
  }
  ```

### 2. Video Information Probe
- **Endpoint**: `GET /api/v1/files/video-info`
- **Query Parameters**:
  - `path` (string): Relative or absolute path to video.
- **Description**: Probes video streams via `ffprobe` and extracts resolution, fps, duration, codecs, rotation metadata, and audio channel count.
- **Response**:
  ```json
  {
    "status": "success",
    "width": 3840,
    "height": 1920,
    "fps": 59.94,
    "duration": 124.5,
    "vcodec": "h264",
    "acodec": "aac",
    "has_audio": true,
    "channels": 2
  }
  ```

---

## 📡 WebSocket Log & Progress Streaming

### Live Progress Stream
- **Endpoint**: `WS /ws/progress`
- **Description**: Full-duplex WebSocket connection used by the browser dashboard.
- **Payload Format (Server to Client)**:
  ```json
  {
    "type": "log",
    "text": "[Stitch] Processing frame 120/450 (26.7%)...\n",
    "progress": 26,
    "step": "Remapping dual-fisheye frames"
  }
  ```
