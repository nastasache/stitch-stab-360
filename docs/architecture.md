# System Architecture & Topology

This document details the architectural design, component interactions, and data storage topology of **StitchStab 360**.

> [!CAUTION]
> **⚠️ LOCALHOST ONLY — DO NOT EXPOSE TO A NETWORK OR THE INTERNET**
>
> StitchStab 360 is engineered exclusively for single-user use on `127.0.0.1`. The FastAPI server has **no authentication, no authorization, and no rate limiting**. Binding to `0.0.0.0` or exposing port `8000` on any network interface gives any reachable host full unauthenticated access to the file system, video pipeline, and subprocess engine.

---

## 🏛️ High-Level System Architecture

```
                                 ┌───────────────────────────────┐
                                 │       Browser Client          │
                                 │  (Three.js, Leaflet, Canvas)  │
                                 └──────────────┬────────────────┘
                                                │ HTTP / WebSockets
                                                ▼
┌───────────────────────────────────────────────────────────────────────────────────┐
│ FastAPI Application (server.py)                                                   │
│                                                                                   │
│  ┌───────────────────────┐  ┌──────────────────────┐  ┌────────────────────────┐  │
│  │   REST Endpoints      │  │  WebSocket Broadcaster│  │   Static File Routers  │  │
│  │ (/api/v1/jobs, files) │  │    (/ws/progress)    │  │ (/data, /assets, etc.) │  │
│  └──────────┬────────────┘  └──────────▲───────────┘  └────────────────────────┘  │
│             │                          │                                          │
│             ▼                          │                                          │
│  ┌─────────────────────────────────────┴───────────────────────────────────────┐  │
│  │ Job Runner & Process Manager (threading.Thread, subprocess.Popen)          │  │
│  └─────────────────────────────────────┬──────────────────────────────────────┘  │
└────────────────────────────────────────┼──────────────────────────────────────────┘
                                         │ Spawns CLI Pipeline
                                         ▼
┌───────────────────────────────────────────────────────────────────────────────────┐
│ Core Pipeline Engine (scripts/pipeline.py)                                        │
│                                                                                   │
│  ┌────────────────────┐   ┌────────────────────┐   ┌───────────────────────────┐  │
│  │ Telemetry Extractor│──▶│ Dual-Fisheye Stitch│──▶│ Stabilization Cascade     │  │
│  │  (extract_telemetry│   │  (remap_stitch.py) │   │ (IMU/Kopf/Kabsch/Cinematic│  │
│  │   & mp4_utils)     │   │                    │   │  /TravelDir/Horizon)      │  │
│  └────────────────────┘   └────────────────────┘   └─────────────┬─────────────┘  │
│                                                                  │                │
│  ┌────────────────────┐   ┌────────────────────┐                 │                │
│  │ Metadata Injector  │◀──│ FFmpeg HW/SW Encode│◀────────────────┘                │
│  │ (inject_metadata)  │   │  & Nadir Overlay   │                                  │
│  └────────────────────┘   └────────────────────┘                                  │
└───────────────────────────────────────────────────────────────────────────────────┘
```

---

## 🖥️ Backend Architecture (`server.py` & `routers/`)

The backend is built with **FastAPI** running on **Uvicorn**, organized into domain `APIRouter` modules:
- **`server.py`**: Root application orchestrator, lifespan context manager, security middleware, static mounts, and router registrations.
- **`routers/common.py`**: Shared process tree management (`kill_process_tree`), path sanitization, and HTML generators.
- **`routers/system.py`**: Health diagnostic checks, dashboard root UI, and media input listings.
- **`routers/presets.py`**: Configuration preset listing, loading, and saving endpoints.
- **`routers/videos.py`**: Video probing (`ffprobe`), stitching verification, crop, calibration, and still preview.
- **`routers/jobs.py`**: Pipeline dispatch, status polling, cancellation, interactive checkpoints, and report retrieval.
- **`routers/streetview.py`**: Street View CAMM export, GPX trajectory matching, preview map generation, and visual odometry.

### 1. Job Management, Concurrency Mutex & Process Isolation
- **Concurrency Guard & Mutex**: To protect hardware resources and prevent concurrent jobs from corrupting intermediate frames in `data/runtime/work/`, `routers/common.py:get_active_job()` inspects the child PID registry and runtime status. If a pipeline job is currently executing, subsequent `POST /api/v1/jobs` requests are rejected immediately with `409 Conflict`. Stale or terminated PIDs are automatically pruned from the registry.
- **Pre-flight Storage Space Estimation**: High-resolution 360 video processing generates extensive intermediate artifacts across multiple stabilization stages. Before starting any job, `routers/common.py:check_disk_space()` and `scripts/pipeline.py` verify available volume capacity against the estimated footprint of the configured pipeline stages plus a safety headroom buffer. If free space is insufficient, the job is cleanly rejected with `400 Bad Request`.
- **Background Execution**: Video processing jobs are long-running operations executed in dedicated background threads (`threading.Thread`).
- **Subprocess Spawning**: Each job spawns `scripts/pipeline.py` via `subprocess.Popen` with unbuffered I/O (`PYTHONUNBUFFERED=1` and `python -B`).
- **Standard Output Streaming**: As lines are emitted by the pipeline subprocess, the worker thread intercepts stdout/stderr, updates internal state, and broadcasts log frames across active WebSocket connections.
- **Cancellation**: Jobs can be stopped on demand via `/api/v1/jobs/stop` or `/api/v1/jobs/{job_id}/cancel`. The process manager terminates the subprocess hierarchy cleanly using process tree signals.

### 2. State & Progress Tracking
- **Runtime Status File**: Global state is mirrored to `data/runtime/temp/status.json` (and `status_{job_id}.json`) with fields:
  - `status`: `"idle" | "running" | "completed" | "failed" | "stopped" | "exporting"`.
  - `progress`: Integer percentage (0–100%).
  - `step`: Human-readable label of the current pipeline stage.
  - `elapsed_time`: Formatted running time string.
  - `output_file`: Target path of the active export.
- **Atomic File Serialization**: Status writes write to a temporary file before atomic renaming to prevent partial-read race conditions from polling clients.

### 3. Static Mounting & Media Streaming
- The FastAPI application mounts:
  - `/data` -> `data/` (serves input videos, output videos, intermediate work renders, and scratch masks).
  - `/assets` -> `assets/` (self-hosted fonts, favicon, and frontend resources).
- Custom video headers (`Accept-Ranges: bytes`) ensure smooth HTML5 video seeking in preview players.

### 4. Automated Testing & Continuous Verification
- **Unit & Endpoint Suite**: 42 automated tests covering API endpoints, parameter parsing, path traversal defenses, math transforms, telemetry parser, and presets.
- **Mockup End-to-End Testing**: `tests/pipeline/test_pipeline_e2e_mockup.py` renders a 1-second synthetic dual-fisheye clip conforming to the repo's max 2-second testing limit, runs the pipeline CLI, validates equirectangular MP4 generation, and cleans up all temporary fixtures.


---

## 🌐 Frontend Architecture (`index.html`, `app.js`, `js/`, `styles.css`)

The frontend is an ES6+ modular application utilizing native ES modules, Bootstrap 5, Three.js, and Leaflet:
- **`app.js`**: Core UI orchestrator, configuration serialization, API communication, and event management.
- **`js/diagnostics.js`**: System Health diagnostic polling, status badge rendering, and dependency detail modal.
- **`js/streetview_picker.js`**: Leaflet GPS map picker for Mode A/B checkpoints, marker dragging, and standalone route editor sync.
- **`js/horizon_modals.js`**: Interactive pause modal for horizon checkpoint leveling and master render transform selection.
- **`horizon_editor.html`**: Standalone Three.js 360° keyframe leveling and visual horizon alignment editor with unified Auto-Detect & Cadence generation modal (Cadence Presets, Pitch Extrema, and Sensor/Vision AI).
- **`#modal-crop-result`**: Themed modal dialog in dashboard (`index.html` / `app.js`) providing success, error, and path clipboard feedback for video cropping.
- **`js/route_utils.js`**: Shared geographic calculation utilities (Haversine distances, bearings, waypoint formatting).

### 1. WebGL 360° Panoramic Viewer (Three.js)
- Renders an interactive 360° spherical viewport using an inverted textured sphere (`THREE.SphereGeometry`) mapped with equirectangular video textures.
- Provides touch, drag, and mouse-wheel orbital navigation (`OrbitControls` / camera quaternion calculations).
- Supports real-time FOV adjustment, numeric orientation inputs (Yaw, Pitch, Roll), seam centering, and nadir inspection.
- **Universal 2D / 360 Toggle**: Every video player (`Original Raw`, `Stitched`, `Telemetry`, `Vidstab`, `Kabsch`, `Kopf`, `Horizon`, `Cinematic`, `Traveldir`, `Final Viewport`) features a `[ 🌐 2D | 🌐 360 ]` toggle. Original Raw video/photo defaults to 2D flat view with the `[ 🌐 360 ]` button, while stitched and stabilized players default to 360 mode.
- **Original Raw VR & Multi-Player Pan Sync**: Equirectangular/VR raw inputs are automatically rendered in Three.js with full orientation controls and bidirectional pan synchronization across all open comparison popouts.
- **Optimized Render-on-Demand & Texture Auto-Promotion**: All Three.js `requestAnimationFrame` render loops enforce strict visibility guards (`document.hidden || clientWidth === 0`), eliminating draw call overhead when tabs or floating comparison players are hidden. Render loops continuously monitor stream readiness (`readyState >= 1`) to automatically promote the viewport from the loading placeholder to the active video texture without race condition stalls.
- **Hardware Decoder LRU Management**: To prevent browser GPU hardware decoder exhaustion (e.g. NVDEC/MediaFoundation limits on concurrent 4K streams) when multiple intermediate pipeline tabs are open, an LRU queue caps active video decoders to at most 2 streams. Inactive videos are cleanly evicted while preserving timestamps (`data-last-time`), re-attaching instantly when focused.
- **Isolated Frame Stepping & Scrub Sync**: Stepping controls (`<`, `>`) and arrow keys seek only the currently focused viewport instead of broadcasting range requests across all pipeline stages, preventing local server connection saturation while preserving cross-tab timestamp synchronization upon tab switching.

### 2. Dual-Canvas Calibration & Seam Viewer
- Renders dual-fisheye frames side-by-side with interactive optical circle overlays.
- Allows visual fine-tuning of circle centers `(cx1, cy1, cx2, cy2)`, radii `(r1, r2)`, FOV, and roll tilt.
- Real-time dynamic updates mirror parameters directly into the preset payload.

### 3. Leaflet GPS & CAMM Map
- Synchronizes with GPX or embedded CAMM telemetry extracted from camera recordings.
- Displays color-coded track polylines, heading direction cones, and synchronized video playback scrubbing.

### 4. Multi-Player Comparison Layout
- Supports concurrent floating video players arranged in 4-quadrant layout (`50vw × 50vh`).
- Enables synchronized side-by-side verification of:
  - Raw Dual-Fisheye Stream
  - Stitched Baseline (`_stitched.mp4`)
  - Telemetry Stabilized (`_telemetry.mp4`)
  - Full Cascade Stabilized Output (`_out.mp4`)

### 5. Configuration Presets Management
- Allows exporting and importing JSON configuration profiles (stitching parameters, stabilization toggles, nadir logo, and Street View settings).
- **Active Preset Indicator**: Shows the current loaded preset filename (or `none` if no preset is loaded) in the Configuration Presets section.
- Supports local JSON file upload/download and server-side presets from `data/input/presets/`.

---

## 💾 Data Storage Topology & Non-Destructive Design

StitchStab 360 follows a **strict non-destructive storage policy**: the engine **never** automatically deletes, overwrites, or truncates files without user confirmation.

| Directory | Role | Retention Policy |
| :--- | :--- | :--- |
| `data/input/videos/` | Source raw video files (`.mp4`, `.insv`, `.mov`) and companion `.gcsv` files. | Permanent (Read-Only to pipeline). |
| `data/input/nadir/` | Nadir patch logos and brand graphics (`.png`, `.jpg`). | Permanent. |
| `data/input/gps/` | GPX / KML GPS tracks. | Permanent. |
| `data/input/presets/`| JSON configuration profiles (`default.json`, user custom presets). | Permanent. |
| `data/input/masks/` | Static alpha, seam, and circular cropping masks. | Permanent baseline. |
| `data/output/` | Production-ready equirectangular VR videos and synced tracks. | Permanent output. |
| `data/runtime/work/` | Intermediate stage renders (`*_telemetry.mp4`, `*_vidstab.trf`, cache). | Transient (manual purge). |
| `data/runtime/temp/` | Scratch frame matrices, audio extractions, runtime masks, `status.json`. | Transient (safe to empty). |
| `data/runtime/logs/` | Persistent text execution logs for past runs. | Retained for audit/debugging. |
