# StitchStab 360

A high-performance web dashboard and CLI pipeline for dual-fisheye 360° video stitching, auto-calibration, multi-stage horizon and optical stabilization, and spatial metadata injection (YouTube VR & Google Street View).

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](./LICENSE)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey.svg)]()
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688.svg)](https://fastapi.tiangolo.com/)
[![Tests](https://img.shields.io/badge/Tests-Passing-brightgreen.svg)](https://img.shields.io/badge/Tests-Passing-brightgreen.svg)

> [!CAUTION]
> **⚠️ LOCALHOST ONLY — DO NOT EXPOSE TO A NETWORK OR THE INTERNET**
>
> StitchStab 360 is a **personal, single-user local tool** designed to run exclusively on `127.0.0.1` (localhost). It has **no authentication, no authorization, no user isolation, and no rate limiting**. Binding the server to `0.0.0.0`, placing it behind a reverse proxy, or exposing port `8000` through a firewall or NAT rule **will give any reachable host full unauthenticated access** to your file system paths, video pipeline, and process execution engine. The application must never be deployed on a shared, public-facing, or cloud server.



---

## 📑 Table of Contents

- [📁 Media & Workflow Folders](#-media--workflow-folders)
- [🚀 Quick Start with Docker](#-quick-start-with-docker)
- [🛠️ Local Installation](#️-local-installation-host--no-docker-required)
- [💻 Command-Line Interface (CLI)](#-command-line-interface-cli)
- [⚙️ Core Features & Architecture](#️-core-features--architecture)
- [🧪 Testing & Quality Assurance](#-testing--quality-assurance)
- [📖 Developer Documentation](#-developer-documentation)
- [🤝 Contributing](#-contributing)
- [📚 Academic References & Citations](#-academic-references--citations)
- [📄 License](#-license)
- [🤖 AI Pair Programming & Methodology](#-ai-pair-programming--methodology)
- [⚖️ Disclaimer](#️-disclaimer)

---

## 📁 Media & Workflow Folders

The project organizes user media, system assets, and processing workspaces into structured directories:

| Directory | Type | Purpose |
| :--- | :--- | :--- |
| **`data/input/videos/`** | **[User Input]** | Place raw dual-fisheye camera videos (`.mp4`, `.insv`, `.mov`) or still photos (`.jpg`, `.jpeg`, `.png`, `.webp`) and sidecar `.gcsv` files here. |
| **`data/input/nadir/`** | **[User Input]** | Place nadir patch/logo candidates (`.png`, `.jpg`) here for nadir branding overlays. |
| **`data/input/gps/`** | **[User Input]** | Place GPX/KML track files here for Google Street View CAMM telemetry synchronization. |
| **`data/input/presets/`** | **[Input Presets]** | Configuration profiles and pipeline presets (`default.json`, `default_all.json`, `default_none.json`, etc.). |
| **`data/input/masks/`** | **[Input Masks]** | Static baseline masks (`alpha_mask.png`, `seam_mask.png`, `circle_mask_*.png`). |
| **`data/output/`** | **[User Output]** | Final rendered 360/VR equirectangular videos and photos (with GPano XMP tags), sidecar GPX, and Street View maps. |
| **`data/runtime/`** | **[Runtime]** | Internal processing buffers: `work/` (active jobs), `temp/` (scratch & masks), `logs/` (run logs). |
| **`config/`** | **[Configuration]** | Centralized application paths, server configurations, and pipeline baseline defaults (`settings.py`). |
| **`assets/`** | **[UI Assets]** | Static frontend web assets: self-hosted fonts (`assets/fonts/`) and favicon (`assets/favicon.png`). |
| **`samples/`** | **[Sample Media]** | Demo test videos and sample telemetry recordings. |
| **`scripts/`** | **[Engine]** | Core Python algorithms (pipeline orchestrator, auto-calibration, IMU/Kopf/Kabsch/VidSTAB/Cinematic/TravelDir stabilization). |

---

## 🚀 Quick Start with Docker

The fastest and most portable way to run the application across **Windows, Linux, and macOS**:

```bash
# 1. Clone the repository
git clone https://github.com/your-username/stitch-stab-360.git
cd stitch-stab-360

# 2. Start the container
docker compose up -d

# 3. Open in your browser
# http://localhost:8080
```

### Processing Videos & Photos via Web UI:
1. Copy raw dual-fisheye video (`.mp4`) or still photo (`.jpg`, `.png`, `.webp`) into `data/input/videos/`.
2. *(Optional)* Copy your nadir logo `.png` into `data/input/nadir/`.
3. Open `http://localhost:8080` (or `http://localhost:8000` on host):
   * For **photos**, the UI automatically adapts (isolates Step 1 *Fisheye Stitching*, locks 1-frame calibration, and hides video stabilization, cropping, and pre-applied feature sections). Media dropdowns prefix inputs with `[PHOTO]` and `[VIDEO]`.
   * For **videos**, customize multi-stage stabilization cascades (Telemetry, Kopf, Kabsch, VidSTAB, Cinematic, Horizon, TravelDir).
4. Click **Auto-Calibrate** or load a preset (visually inspect seams and fine-tune alignment/horizon if needed), then click **Proceed** and confirm via **Confirm & Proceed**.
5. Monitor real-time progress and inspect the interactive Three.js 360° spherical preview.
6. Retrieve the finished deliverables (VR equirectangular MP4 or GPano-tagged JPG) from `data/output/`.

### 💡 Best Practice for Long Videos (Crop → Calibrate → Test Workflow)
Processing long 4K/5.7K dual-fisheye footage through multi-stage stabilization is computationally and thermally intensive. To conserve hardware resources and avoid wasted renders:
1. **Lossless Crop (Test Snippet)**: Use the Web UI crop tool or CLI (`scripts/crop_camera_video.py`) to extract a short 5–10 second sample (150–300 frames) of moving footage. All private camera telemetry atoms (`moov/udta/vrot`, `camm`, `gpmd`, `m360`) and sidecar `.gcsv` logs are preserved frame-for-frame.
2. **Calibrate & Level**: Run auto-calibration and verify circle radii, FOV, lens offsets, and initial horizon/yaw/pitch leveling on the short clip in the Three.js 360° preview.
3. **Test Stabilization & Save Preset**: Run test stabilization passes on the sample. Once horizon lock and seam smoothness look optimal, save the configuration as a named preset in `data/input/presets/`.
4. **Execute Full Long Video Render**: Apply the verified preset to the full-length video and proceed with the production render.

---

## 🛠️ Local Installation (Host / No Docker Required)

### 0. Hardware Recommendations & System Advisory

| Component | Minimum Specification | Recommended Specification |
| :--- | :--- | :--- |
| **CPU** | 4 cores / 8 threads (Intel Core i5 / AMD Ryzen 5) | **8+ cores / 16 threads** (Intel Core i7/i9, AMD Ryzen 7/9, Apple Silicon M-series) for fast OpenCV remapping and multi-threaded encoding. |
| **GPU / VRAM** | Integrated GPU with WebGL 2.0 support (Intel Iris / AMD Radeon Graphics) | **Dedicated GPU with 4 GB+ VRAM** (NVIDIA GeForce RTX, AMD Radeon RX, or Apple Silicon) for multi-stream WebGL 360° rendering and hardware video decode. |
| **RAM** | 8 GB RAM (for 1080p/2K dual-fisheye streams) | **16 GB – 32 GB RAM** (required for 4K, 5.7K, and 8K multi-stage stabilization cascades). |
| **Storage** | 20 GB free space on HDD/SSD | **50+ GB free space on fast NVMe SSD** (accommodates high-throughput scratch buffers and multi-stage intermediate renders). |
| **Thermals** | Standard desktop/laptop cooling | **Active cooling / elevated laptop stand** (long video renders sustain 100% CPU/GPU utilization and may trigger thermal throttling). |

* **Storage & Disk Space (Strict Non-Destructive Policy)**: 
  StitchStab 360 enforces a strict safety design: **the application never automatically deletes, purges, or moves files or folders on your system**. 360° video processing generates substantial intermediate frame buffers and temporary renders in `data/runtime/work/` and `temp/`. Because the system will never silently erase your project files or test artifacts, intermediate cache accumulates until you choose to inspect and remove it manually.
* **Local Network Binding**: By default, the web dashboard binds to `127.0.0.1:8000` (accessible only from the host machine). Do not bind to `0.0.0.0` or open router ports to public networks without an authentication proxy.
* **Non-Invasive Host System Policy**: StitchStab 360 strictly respects the user's operating system environment. **The application never automatically installs system software, packages, or background daemons, and never modifies persistent system environment variables or registry PATH settings**. When external dependencies (FFmpeg, ExifTool, Python) need installation or updates, the dashboard diagnostic modal and documentation provide explicit commands for the user to execute manually at their own discretion.

#### 🧹 What to Delete by Hand to Clean Up Storage:

When you want to reclaim disk space after processing jobs, you can safely delete the contents of the following directories (ensure no processing job is actively running):

| Folder Path | Contents | Safety Level | When to Delete |
| :--- | :--- | :--- | :--- |
| **`data/runtime/temp/*`** | Scratch frame buffers, temporary audio slices, transient masks. | 🟢 **100% Safe** | Can be emptied anytime no job is running. |
| **`data/runtime/work/*`** | Multi-stage intermediate renders (`*_telemetry.mp4`, `*_vidstab.trf`, trajectory caches). | 🟢 **Safe** | Delete once your final video in `data/output/` is verified. |
| **`data/runtime/logs/*`** | Historical job execution logs and console traces. | 🟢 **Safe** | Delete periodically when no longer needed for debugging. |

> [!CAUTION]
> **What NOT to Delete:**
> * **`data/input/*`**: Contains your source camera videos, nadir branding logos, GPS tracks, and saved presets.
> * **`data/output/*`**: Contains your finished, production-ready 360° VR videos and exported maps.
> * **`config/*`**: Contains core application settings, presets, and run counters.
> * **`venv/*`**: Contains the local Python execution environment.

### 1. Display & Browser Recommendations

* **Recommended Display Resolution**:
  * **Optimal (Multi-Video Comparison)**: **1440p (2560×1440) or 4K UHD (3840×2160)**. Ideal for opening multiple simultaneous floating video players (`50vw × 50vh` 4-quadrant layout) to compare raw, stitched, and stabilized stages side-by-side with unobstructed VR timeline controls.
  * **Standard / Laptops (1080p Full HD)**: Fully functional for primary controls and single-player preview. For multi-stage comparison on 1080p or smaller screens, it is recommended to maximize the browser window (`F11`), adjust browser zoom to 80%–90%, or inspect preview stages sequentially.
* **Supported & Recommended Browsers**:
  * **Recommended**: **Google Chrome**, **Microsoft Edge**, or **Brave** (Chromium-based engines provide the best multi-stream hardware video decode acceleration and WebGL 2.0 canvas performance).
  * **Supported**: **Mozilla Firefox** (ensure hardware acceleration is enabled in browser preferences).
  * **Not Recommended**: Safari (stricter concurrent limits on simultaneous HTML5 hardware video decoders).
* **WebGL Requirement**:
  * WebGL 2.0 must be enabled in your browser for the interactive Three.js 360° spherical panning, seam alignment, and nadir preview viewport.

### 2. Software Prerequisites & Automated Installation
- **Python**: Python 3.10+ (Python 3.12 recommended, with `pip` and virtual environment support).
- **FFmpeg & FFprobe**: Installed and available in system `PATH` (compiled with `libvidstab` for optical stabilization; Gyan Full Build recommended).
- **ExifTool**: Available in system `PATH` (for camera metadata extraction and injection).

> [!TIP]
> **Multi-FFmpeg Disambiguation & Isolation**:
> If your system has multiple FFmpeg installations (such as Kdenlive, MediaCoder, or minimal builds that lack `libvidstab`), StitchStab 360's built-in tool resolver automatically inspects all candidate binaries, validates `libvidstab` optical flow support, and prioritizes the full build. You can also explicitly pin your preferred binary by setting the `FFMPEG_PATH` environment variable.

#### Automated Dependency Installation:
* **Windows (via winget or Chocolatey)**:
  ```powershell
  # Using winget:
  winget install Gyan.FFmpeg
  winget install OliverBetz.ExifTool
  winget install Python.Python.3.12

  # Or using Chocolatey:
  choco install ffmpeg-full exiftool python
  ```
* **macOS (via Homebrew)**:
  ```bash
  brew install ffmpeg exiftool python@3.11
  ```
* **Linux (Ubuntu / Debian)**:
  ```bash
  sudo apt update
  sudo apt install -y ffmpeg libimage-exiftool-perl python3 python3-venv python3-pip
  ```
> [!IMPORTANT]
> Verify that running `ffmpeg -version`, `exiftool -ver`, and `python --version` succeed from your command prompt before launching `start.bat` or `start.sh`.

### 3. Python Dependencies
```bash
pip install -r requirements.txt
```

### 4. One-Click Launch & Shutdown

* **Windows**:
  * **Start Server**: Double-click `start.bat` (or execute in PowerShell/CMD).
    * Automatically creates a Python virtual environment (`venv`) if not present.
    * Installs/upgrades dependencies from `requirements.txt`.
    * Starts the FastAPI / Uvicorn server and opens `http://127.0.0.1:8000` in your default browser.
  * **Stop Server**: Double-click `stop.bat` to cleanly terminate running server processes and release port 8000.

* **Linux / macOS**:
  * **Start Server**: Run `./start.sh` or start Uvicorn directly:
    ```bash
    python3 -B -m uvicorn server:app --host 127.0.0.1 --port 8000
    ```
  * **Stop Server**: Run `./stop.sh` to cleanly terminate the background Uvicorn server.

---

## 💻 Command-Line Interface (CLI)

The underlying processing engine can also be executed directly via CLI for headless, scripted, or batch workflows:

```bash
# Basic stitching
python -B scripts/pipeline.py \
  --input data/input/videos/sample.mp4 \
  --output data/output/sample_stitched.mp4

# Multi-stage cascade: Telemetry (with Mahony 6-axis IMU fusion) + Cinematic Smoothing + Metadata
python -B scripts/pipeline.py \
  --input data/input/videos/sample.mp4 \
  --output data/output/sample_stitched.mp4 \
  --stabilize \
  --stabilize_methods telemetry,cinematic \
  --telemetry_fusion mahony \
  --telemetry_fusion_gain 0.51 \
  --l1_lambda_acc 20.0 \
  --l1_lambda_vel 2.0 \
  --cinematic_window 45 \
  --inject_final_meta

# Full stabilization cascade with travel-direction lock and horizon checkpoints:
python -B scripts/pipeline.py \
  --input data/input/videos/sample.mp4 \
  --output data/output/sample_stitched.mp4 \
  --stabilize \
  --stabilize_methods telemetry,kopf,kabsch,vidstab,cinematic,horizon,traveldir \
  --traveldir_mode travel_direction \
  --traveldir_damping 0.90 \
  --traveldir_deadband 1.5 \
  --inject_final_meta
```

View all available CLI flags:
```bash
python -B scripts/pipeline.py --help
```

---

## ⚙️ Core Features & Architecture

* **Dual-Fisheye Cropping & Remapping**: Custom FOV, yaw/pitch/roll orientation, anti-vignette shading, and seam blending.
* **Auto-Calibration Engine**: Automated multi-frame alignment and horizon leveling via keypoint matching (ORB/AKAZE) and phase correlation with balanced, fast, and robust presets.
* **Multi-Stage Cascade Stabilization Engine**:
  * **Execution Order Validation**: Dynamic Web UI and CLI validation safeguards against suboptimal method sequences (such as scheduling Horizon Leveling before hardware Telemetry), providing 1-click auto-fix and descriptive coordinate conflict warnings.
  * **Cumulative Trajectory Composition**: Downstream tracking stages (`kopf`, `kabsch`, `vidstab`, `cinematic`, `traveldir`) are unblinded by cumulative chained transforms from preceding stages, ensuring smooth holistic stabilization without conflicting corrections.
  * **Telemetry (IMU)**: Accelerometer & gyroscope telemetry leveling supporting Gyroflow, WitMotion, GoPro (GPMF), Samsung Gear 360, CAMM, and Insta360, with generic step change-point detection for sensor calibration shifts.
  * **6-Axis IMU Sensor Fusion**: Drift elimination fusing gravity vectors and gyro rates using **Mahony** (default), **Extended Kalman Filter (EKF)**, or **Complementary** filters.
  * **Cinematic Path Optimization (`cinematic`)**: Auto-directed L1-norm SO(3) regularized trajectory solver with an analytical Jacobian for fast L-BFGS-B convergence, producing cinematic tripod holds (zero-velocity) and smooth constant-speed pans (zero-acceleration).
  * **Travel-Direction & Heading Lock (`traveldir`)**: Forward travel direction locking, target yaw orientation, and damped following with deadband filtering.
  * **Kopf 3D-2D**: Keyframe rotation smoothing with deformed-rotation jitter optimization.
  * **Kabsch Algorithm**: SVD-based spherical rotation tracking across matched features.
  * **VidSTAB (2D Optical Flow)**: Sub-pixel high-frequency jitter smoothing.
  * **Interactive Horizon Checkpoints**: Timeline keyframe leveling via the built-in Horizon Editor (`horizon_editor.html`) with 2-Point Horizon Snap, automated telemetry/vision/motion autodetection, and unified **Cadence Keyframe Seeding** (Time-Interval cadence with long-video adaptive guards, Vertical Pitch Extrema crests/troughs detection, and adaptive decimation trajectory graphs for zero-overhead, collision-free visualization of dense keyframes).
    * **Visual Alignment Guides**: Dual green orthogonal guides—a horizontal level horizon line and a vertical center reference reper line—for precision pitch and roll adjustments.
    * **Streamlined Fast-Leveling Workflow**: Unified action row (`Reset 0°`, `Delete`, and `Clear All`) positioned above the scrollable checkpoint table, and companion buttons (`Set Checkpoint` + `Next Checkpoint >`) side-by-side directly below `Yaw`, `Roll`, and `Pitch` controls with custom large steppers (`▲` / `▼`) for instantaneous, minimal-mouse-travel keyframe calibration.
    * **Timeline Checkpoint Navigator**: Real-time bottom transport counter displaying compact `X / Y` (gold when precisely on a keyframe, muted when interpolating) and a direct `Go to CP #:` number input for immediate frame jumping.
    * **Bulk Action Safety Confirmations**: Destructive bulk actions (`Reset 0° (Level)` and `Clear All`) require explicit confirmation alerts to safeguard manually calibrated keyframe sets.
    * **View Navigation & Shortcuts**:
      * **Left-Click + Drag**: Tilts Pitch up/down. Horizontal pan is locked forward ($0.0^\circ$) when `Ignore Yaw` is active to keep Pitch & Roll adjustments strictly orthogonal with screen coordinates and the green level guide.
      * **Right-Click + Drag** or **`Alt` / `Shift` + Drag**: Free $360^\circ$ inspection orbit to check horizon alignment across the entire spherical perimeter without changing leveling values.
      * **`[🎯 Center (0°)]` Button / HUD Yaw Click**: 1-click snap back to canonical forward view ($0.0^\circ$ Yaw).
      * **Reset Button**: Resets Pitch, Roll, Yaw, and View Pan back to $0.0^\circ$.
      * **Mouse Wheel**: Smooth zoom Field of View ($30^\circ$ to $120^\circ$) over viewport, or fine adjustment ($\pm 0.1^\circ$) when hovering number inputs.
      * **2-Point Snap Tool**: Click 2 points along a visible water/road horizon to automatically solve Roll and Pitch.
      * **Variable Playback Speed**: Slow down playback ($0.10\times$ super-slow, $0.25\times$ quarter, $0.50\times$ half, $0.75\times$) or speed up ($1.25\times$, $1.50\times$, $2.00\times$) to easily observe subtle horizon dips; adjustable via the transport dropdown or hotkeys `[` / `]`.
      * **Keyboard Controls**: `Space` (Play/Pause toggle), `[` / `]` (Decrease/Increase playback speed), `←` / `→` (Step 1 frame backward/forward), `Home` / `End` (Jump to first/last frame), `J` / `K` (Previous/Next checkpoint), `Esc` (Cancel drawing mode).
      * **Parameter Audit Log & Inspector (`{out_base}_horizon_params.log`)**: Full audit logging of all stabilization parameters (Strategy, Density, Epsilon, Prominence, Roll Damping, Baseline offsets, Range, Keyframes) written alongside test files in the work folder, with an interactive parameter inspector and log viewer in the editor. Clearing all keyframes automatically resets active parameters to manual default (`NONE` badge, 0 keyframes, neutral baseline), while mode switching and manual edits automatically synchronize parameters and the audit log.
  * **Reorderable Cascade**: Full drag-and-drop / reordering flexibility to customize the exact sequence of stabilization stages.
  * **Composite Stabilization Metrics**: Status lines and reports detail stage-by-stage and composite tremor absorption percentages, Jerk RMS, and horizon lock scores.
* **Interactive Route & GPS Editor**: Integrated Leaflet map editor (`route_editor.html`) supporting **OpenStreetMap (Standard)**, **Esri / Maxar Satellite**, and Google Satellite layers, with CAMM telemetry time synchronization and Street View map export.
* **Spatial Media & Street View**: Automatic spherical metadata injection (`sv3d` for YouTube VR) and Google Street View GPX + CAMM telemetry export (Modes A and B).
* **Centralized Configuration**: All system paths, network limits, and default pipeline parameters centralized in `config/settings.py` with flexible preset management (`data/input/presets/`).
* **Modern Web Dashboard**: Real-time log monitoring, interactive Three.js 360° spherical preview with texture auto-promotion, hardware decoder LRU management (max 2 active streams to prevent GPU decoder crashes on long multi-stage 4K pipelines), dual-lens alignment view, color-coded method markers, skipped stage notices, parameter tooltips, and live job status tracking.

---

## 🧪 Testing & Quality Assurance

StitchStab 360 includes a comprehensive automated test suite covering API endpoints, telemetry parsers, calibration math, and pipeline execution.

### Running Unit & API Tests

On Windows:
```cmd
run_tests.bat
```

On Linux / macOS (or cross-platform terminal):
```bash
python -B -m unittest discover -s tests -p "test_*.py"
```

> [!NOTE]
> All scripts enforce the `-B` flag (`PYTHONDONTWRITEBYTECODE=1`) to prevent `.pyc` caching and ensure fresh execution. Pre-commit hooks run the test suite automatically prior to each commit.

---

## 📖 Developer Documentation

Comprehensive technical documentation for developers, system architects, and contributors is maintained in the [`docs/`](./docs/) directory:

* **[`docs/README.md`](./docs/README.md)**: Developer documentation portal, directory tree, and quick start guide.
* **[`docs/architecture.md`](./docs/architecture.md)**: System topology, FastAPI backend, SPA frontend (Three.js WebGL & Leaflet), and non-destructive storage design.
* **[`docs/pipeline.md`](./docs/pipeline.md)**: Deep dive into the video processing engine: demuxing, calibration, remapping, telemetry fusion, stabilization cascade, nadir overlay, and spatial metadata injection.
* **[`docs/api_reference.md`](./docs/api_reference.md)**: REST API and WebSocket specifications, schemas, job management, and intermediate asset discovery.
* **[`docs/code_reference.md`](./docs/code_reference.md)**: Automated code-level API reference extracted directly from source docstrings and AST syntax trees via `scripts/generate_code_docs.py`.
* **[`docs/testing_and_standards.md`](./docs/testing_and_standards.md)**: Developer protocols, hermetic testing rules, Python bytecode blocking guards, and pre-commit hooks.

---

## 🤝 Contributing

Contributions to StitchStab 360 are welcome! When submitting issues or pull requests, please follow these guidelines:

1. **Bytecode Blocking**: Always invoke Python scripts using the `-B` flag (`python -B script.py`). Ensure every new or modified `.py` script maintains the top header guard (`sys.dont_write_bytecode = True`).
2. **Line Endings**: Maintain strictly Linux LF (`\n`) line endings across all files (no CRLF).
3. **Preserve Compatibility**: Keep existing public API endpoint signatures, configuration keys, and parameter naming conventions intact.
4. **Documentation**: Add Google-style docstrings to all new functions and classes.
5. **Run Tests**: Verify that the full test suite passes cleanly before submitting changes.

---

## 📚 Academic References & Citations

* **360° Video Stabilization Algorithm**:
  > Johannes Kopf. 2016. *360° Video Stabilization*. ACM Transactions on Graphics (Proc. SIGGRAPH Asia 2016), Vol. 35, No. 6, Article 195. [DOI](https://doi.org/10.1145/2980179.2982405)
* **L1 Optimal Camera Paths for Video Stabilization**:
  > Matthias Grundmann, Vivek Kwatra, and Irfan Essa. 2011. *Auto-Directed Video Stabilization with L1 Optimal Camera Paths*. IEEE Conference on Computer Vision and Pattern Recognition (CVPR 2011). [DOI](https://doi.org/10.1109/CVPR.2011.5995525)
* **Nonlinear Complementary Filters on SO(3)**:
  > Robert Mahony, Tarek Hamel, and Jean-Michel Pflimlin. 2008. *Nonlinear Complementary Filters on the Special Orthogonal Group*. IEEE Transactions on Automatic Control, Vol. 53, No. 5, pp. 1203–1218. [DOI](https://doi.org/10.1109/TAC.2008.923738)
* **Optimal Vector Rotation via SVD (Kabsch Algorithm)**:
  > Wolfgang Kabsch. 1976. *A solution for the best rotation to relate two sets of vectors*. Acta Crystallographica Section A, 32(5), pp. 922–923. [DOI](https://doi.org/10.1107/S0567739476001873)
* **Google Spatial Media**: [Spatial Media Metadata Injector](https://github.com/google/spatial-media)

---

## 📄 License

This project is licensed under the [MIT License](./LICENSE).

### Third-Party Licenses & Acknowledgements

StitchStab 360 includes third-party open-source libraries and fonts subject to their respective licenses:

* **Google Spatial Media** (`scripts/spatialmedia/`): [Apache License 2.0](./scripts/spatialmedia/LICENSE) (Copyright (c) 2016 Google Inc.)
* **Three.js** (`js/three/`): [MIT License](./js/three/LICENSE) (Copyright (c) 2010-2022 Three.js Authors)
* **Leaflet** (`js/leaflet/`): [BSD 2-Clause License](./js/leaflet/LICENSE) (Copyright (c) 2010-2023 Vladimir Agafonkin, (c) 2010-2011 CloudMade)
* **Outfit Font** (`assets/fonts/`): [SIL Open Font License 1.1](./assets/fonts/OFL.txt) (Copyright (c) 2021 The Outfit Project Authors)
* **Plus Jakarta Sans Font** (`assets/fonts/`): [SIL Open Font License 1.1](./assets/fonts/OFL.txt) (Copyright (c) 2020 The Plus Jakarta Sans Project Authors)

For full license texts, copyright notices, and algorithmic attributions, see [THIRD_PARTY_LICENSES.md](./THIRD_PARTY_LICENSES.md).

---

## 🤖 AI Pair Programming & Methodology

StitchStab 360 was engineered through a disciplined **human-in-the-loop AI pair programming** workflow. The system architecture, mathematical formulations (spherical trigonometry, quaternion sensor fusion, SVD Procrustes alignment, and $L_1$-norm trajectory optimization), and implementation details were developed through iterative collaboration with frontier AI models (Google Gemini and Anthropic Claude), directed, reviewed, verified, and maintained by the author.

* **Governing Rules & Protocols**: Operational constraints, terminal protections, and coding principles are codified in [`GEMINI.md`](./GEMINI.md) and [`CLAUDE.md`](./CLAUDE.md).
* **Git RAG Decision Memory**: Architectural rationales and engineering learnings are continuously recorded in [`.agents/memory/decisions.md`](./.agents/memory/decisions.md) using the repository's Git RAG engine.
* **Deterministic Verification**: Every stage, algorithm, and refactor is verified through an automated test suite (`tests/`), pre-commit hooks, and hermetic execution constraints.

---

## ⚖️ Disclaimer

1. **Localhost-Only Security Scope**: StitchStab 360 is engineered exclusively for single-user, local-machine use on `127.0.0.1`. The server provides **no authentication, no authorization, no multi-user isolation, and no rate limiting**. Binding to `0.0.0.0`, port-forwarding, or placing behind a reverse proxy on any network-accessible machine will expose unrestricted access to the video processing engine, file system paths, and subprocess execution. **Never deploy this application on a public, shared, or internet-accessible server.**
2. **"As-Is" Software**: This software is provided "as is", without warranty of any kind, express or implied, including but not limited to fitness for a particular purpose or non-infringement. The authors and contributors assume no liability for any direct, indirect, incidental, or consequential damages resulting from video processing, encoding failures, or data loss. Always maintain backups of your original camera footage.
3. **Trademarks & Non-Affiliation**: All product names, logos, brand names, and trademarks referenced in this repository (including *GoPro*, *Insta360*, *Samsung Gear 360*, *Google Street View*, and *YouTube VR*) are the property of their respective owners. These references are used strictly for identification, technical description, and interoperability purposes under nominative fair use. StitchStab 360 is an independent open-source project and is not affiliated with, endorsed by, or sponsored by any of these entities.
4. **Map Tile Services**: Map data and imagery displayed within the Route Editor and Street View previews are supplied by external tile providers (OpenStreetMap, Esri, and Google Maps). Users deploying this software publicly or commercially are responsible for complying with the respective providers' acceptable use policies, attribution rules, and API licensing requirements.
5. **Hardware Stress & Resource Usage**: Processing high-resolution video streams causes sustained hardware load. The user is responsible for monitoring system thermals, available RAM, and remaining disk storage to prevent OS memory exhaustion or storage overflow.
6. **Non-Destructive File Safety Guarantee**: By design, StitchStab 360 does not contain automatic pruning, file deletion, or background folder cleanup routines. The application will never delete or relocate files on the user's machine without explicit user command. All cache maintenance and data retention remain strictly under the user's manual supervision.


---

## 🧹 Manual Workspace & Storage Cleanup Guide

Because the processing engine and test suites enforce a zero-deletion policy, intermediate render artifacts, telemetry caches, and test fixtures are preserved for full user inspection and post-processing auditability. When you wish to reclaim disk space, clean the directories manually:

* **Clear Test Fixtures (`tests/temp/`)**:
  * **Windows PowerShell**: `Get-ChildItem -Path tests\temp -Exclude .gitignore | Remove-Item -Recurse -Force`
  * **Windows CMD**: `del /s /q tests\temp\*.*`
  * **Linux / macOS Bash**: `find tests/temp/ -mindepth 1 ! -name '.gitignore' -exec rm -rf {} +`
* **Clear Pipeline Working Cache (`data/runtime/work/`)**:
  * **Windows PowerShell**: `Get-ChildItem -Path data\runtime\work -File | Remove-Item -Force`
  * **Linux / macOS Bash**: `rm -rf data/runtime/work/*`
* **Clear Runtime Logs & Temp Files (`data/runtime/temp/`, `data/runtime/logs/`)**:
  * **Windows PowerShell**: `Get-ChildItem -Path data\runtime\temp, data\runtime\logs -File | Remove-Item -Force`
  * **Linux / macOS Bash**: `rm -rf data/runtime/temp/* data/runtime/logs/*`
