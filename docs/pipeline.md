# 360° Video Processing & Stabilization Pipeline

This document explains the technical architecture, execution flow, quality modes, and stage-by-stage mechanics of the **StitchStab 360** pipeline engine (`scripts/pipeline.py`).

---

## 🔄 Complete Pipeline Execution Lifecycle

```
[Raw Dual-Fisheye MP4]
       │
       ▼
1. Pre-Processing, Trimming & Lossless Atom Slicing
   └── scripts/crop_camera_video.py, utils/mp4_utils.py (preserves vrot/camm/gpmd/m360/opax/opai)
       │
       ▼
2. Auto-Calibration, Circle Detection & Sensor Warmup
   └── scripts/auto_calibrate.py, scripts/detect_warmup.py
       │
       ▼
3. Equirectangular Remapping & Seam Stitching (Optional: --skip_stitching if already equirect)
   └── Direct FFmpeg v360 filter graph, scripts/generate_alpha_mask.py, anti-vignette
       │
       ▼
4. Pipeline Quality & Execution Mode Selection (--stab_quality_mode)
   ├── Mode 0: Standard Multi-Pass (intermediate re-encodings)
   ├── Mode 1: Single-Pass SO(3) Transform Composition (compose_sendcmd_files)
   ├── Mode 2: Lossless Intermediates (ffv1 / qtrle)
   ├── Mode 3: Lanczos Resampling (v360:interp=lanczos)
   └── Mode 4: Hybrid Sequential Extraction + Single-Pass Master Render (Default)
       │
       ▼
5. Multi-Stage Stabilization Cascade (Configurable Execution Order)
   ├── A. Telemetry / IMU Fusion (scripts/extract_telemetry.py, utils/imu_fusion.py)
   ├── B. Kopf 3D-2D Visual Feature Tracking (scripts/stabilize_kopf.py)
   ├── C. Kabsch Orthogonal Trajectory SVD Alignment (scripts/stabilize_kabsch.py)
   ├── D. 360 Spherical Optical Smoothing (scripts/stabilize_vidstab.py)
   ├── E. Cinematic L1/L2 Trajectory Optimization (scripts/stabilize_cinematic.py)
   ├── F. Horizon Leveling & Auto-Checkpoints (scripts/stabilize_horizon.py)
   └── G. Travel Direction Auto-Heading Lock (scripts/stabilize_traveldir.py)
       │
       ▼
6. Trajectory & Motion Curve Visualization
   └── scripts/visualize_corrections.py (multi-axis angular & tremor plots)
       │
       ▼
7. Nadir Branding & Polar Patch Overlay
   └── Built directly into pipeline.py or composited into Single-Pass Master Render
       │
       ▼
8. High-Fidelity Encoding & Audio Remuxing
   └── Hardware NVENC (h264_nvenc / hevc_nvenc) or CPU (libx264 / libx265), lossless audio copy
       │
       ▼
9. Spatial Metadata Injection
   └── spatialmedia module (Spherical v2 XML) + ISO-BMFF vrot atom embedding
       │
       ▼
10. Geospatial Publishing & External Tool Exports
    ├── Google Street View Studio Video & GPX (scripts/streetview_gpx.py, CAMM track, Leaflet map)
    ├── Gyroflow Sidecars (.gcsv, .MP4.gcsv)
    └── Kdenlive Bigsh0t Motion Track (.bigsh0t360motion)
       │
       ▼
11. Automated Sub-Pixel Verification & Performance Reporting
    └── Rectilinear cv2.phaseCorrelate, individual model reports, and net _stabilization_report.txt
       │
       ▼
[Final Stabilized 360° Deliverables & Reports]
```

---

## 🔬 Pipeline Stages in Detail

### 1. Pre-Processing, Trimming & Lossless Atom Slicing
* **Engines**: `scripts/crop_camera_video.py`, `utils/mp4_utils.py`
* **Responsibilities**:
  * Trims or crops raw input video frame-accurately while maintaining complete data retention.
  * Preserves all binary ISO-BMFF camera metadata and private telemetry atoms (`moov/udta/vrot`, `m360`, `camm`, `gpmd`, `opax`, `opai`) matching the trimmed timeline.
  * Keeps companion telemetry logs (`_telemetry.txt` or `.gcsv`) synchronized frame-for-frame.
  * Provides themed modal feedback upon crop completion, path copying, or error diagnostics in the dashboard.
* **Resource Optimization Workflow (Long Videos)**:
  * Prior to running full-length, multi-stage stabilization on 4K/5.7K takes, extract a 5–10 second test slice.
  * Calibrate optical centers, FOV, rear roll, and stabilization cascade parameters on this short clip.
  * Export the verified parameters to a preset JSON (`data/input/presets/`) before dispatching the full video render to prevent compute waste and thermal throttling.

### 2. Auto-Calibration, Circle Detection & Sensor Warmup
* **Engines**: `scripts/auto_calibrate.py`, `scripts/detect_warmup.py`, `utils/calibrate_dual_fisheye.py`
* **Responsibilities**:
  * Analyzes dual-fisheye frames using Hough Circle transforms and edge gradient sweeps to compute optical circle boundaries `(cx1, cy1, cx2, cy2)` and radii `(r1, r2)`.
  * Optimizes horizontal/vertical field of view (`--ih_fov`, `--iv_fov`), vertical lens offsets (`--left_y_offset`), and rear lens roll offset (`--rear_roll_offset`).
  * Evaluates sensor warmup periods (`scripts/detect_warmup.py`) by analyzing initial orientation drift variance to ignore unstable initial frames.

### 3. Equirectangular Remapping & Seam Stitching
* **Engines**: Integrated directly within `scripts/pipeline.py`, with `scripts/generate_alpha_mask.py`
* **Mechanics**:
  * Dynamically probes input dimensions (adapting between standard $3840\times1920$ and padded $4320\times2160$ captures).
  * Applies pre-rotation (`--raw_rotation`: 0°, 90°, 180°, 270°) prior to unwarping.
  * Warps dual fisheye hemispheres into a standard $2:1$ equirectangular projection using FFmpeg's `v360` filter graph.
  * Synthesizes smooth sinusoidal alpha-blend masks (`data/runtime/temp/temp_seam_mask_*.png`) or applies custom masks (`--mask_file`) to eliminate boundary seam artifacts.
  * Applies radial anti-vignette edge compensation (`--anti_vignette`, `--anti_vignette_angle`) to balance peripheral lens brightness.
  * **Pre-Stitched & Pre-Applied Inputs**: When an already-stitched equirectangular video or pre-processed input is supplied, users can explicitly mark pre-applied features via the dashboard toggles (Stitched, Nadir Logo, and all 7 stabilization passes: Telemetry, Kopf 3D-2D, Kabsch, Vidstab, Cinematic, Horizon, and Traveldir). The pipeline and dashboard preserve full preview frame rendering with `--skip_stitching` without assuming pipeline steps based on filename substrings.

### 3b. 360° Photo Stitching Mode (Still Panoramas)
* **Engines**: `scripts/pipeline.py`, `scripts/auto_calibrate.py`, ExifTool
* **Mechanics**:
  * **Input Support**: Accepts raw dual-fisheye still images (`.jpg`, `.jpeg`, `.png`, `.webp`) in `data/input/videos/`.
  * **Dynamic Photo Handling**: When an image input is detected, temporal video stabilization cascades, video cropping, pre-applied feature overrides, external utils options, and Street View track publishing are automatically hidden and bypassed. Step 1 (*Fisheye to Equirectangular Stitching*) and Step 3 (*Nadir Logo Overlay*) remain fully active. Media dropdowns distinctly prefix entries with `[PHOTO]` and `[VIDEO]`.
  * **1-Frame Auto-Calibration**: Invokes `scripts/auto_calibrate.py --image ...` to compute alignment angles and horizon levels directly from the still frame.
  * **High-Quality Single-Frame Remap**: FFmpeg renders the equirectangular panorama directly via `-vframes 1 -pix_fmt yuvj420p -q:v 2` ensuring standard 4:2:0 chroma subsampling compatible with web/social tile processors.
  * **Spherical & Camera Metadata**: Injects standard XMP projection tags (`GPano:ProjectionType="equirectangular"`, `GPano:UsePanoramaViewer="True"`, initial view orientation vectors) and camera tags (`Make="RICOH"`, `Model="RICOH THETA S"`) via ExifTool so the resulting image is recognized immediately by Facebook, Google Photos, Kuula, and VR viewers.
  * **Dual Persistence**: Saves the stitched equirectangular image alongside a companion 1-frame MP4 preview in `data/runtime/work/` and `data/output/`.

### 4. Quality Optimization & Composition Modes (`--stab_quality_mode`)
To minimize generational encoding loss across multiple stabilization passes, `pipeline.py` supports 5 distinct execution strategies:
* **Mode 0 (Standard Multi-Pass)**: Executes each stabilization pass sequentially, writing intermediate MP4 files to disk.
* **Mode 1 (Single-Pass Transform Composition)**: Bypasses intermediate video renders. Compiles all active stage rotations in $SO(3)$ space into a single composed `sendcmd` transform file via `compose_sendcmd_files()` and performs exactly one encode.
* **Mode 2 (Lossless Intermediates)**: Renders intermediate passes using lossless codecs (`ffv1` or QuickTime Animation `qtrle`), eliminating generation artifacts between stages.
* **Mode 3 (Lanczos High-Fidelity Resampling)**: Forces `v360:interp=lanczos` across all warping stages to maintain maximum pixel sharpness.
* **Mode 4 (Hybrid Sequential Extraction + Single-Pass Master Render — Default)**:
  * Runs feature/motion extraction stages sequentially on lightweight fast intermediates or motion sidecars.
  * Binds all computed rotational matrices in $SO(3)$ space into a unified master rotation command.
  * Executes a single, pristine master encode (including nadir branding) using NVENC or libx264.

#### Smart Optimizations:
* **Smart Stage Pruning**: Automatically analyzes each stage's computed motion. If peak rotation is $< 0.015^\circ$, the stage is identified as trivial/identity and zero-cost bypassed to avoid unneeded filtering.
* **Slew-Rate Limiter**: Clamps single-frame counter-rotation velocity jumps exceeding $2.5^\circ/\text{frame}$ to eliminate visual whip-jerk tremors.
* **Interactive Transform Selection Modal (`prompt_transforms`)**: In Modes 1 and 4, the pipeline pauses with `awaiting_transforms` before executing the master render. The user reviews diagnostic reports and toggles transforms in the Web UI modal. Enforces timestamp freshness (`mtime >= pause_start_ts - 2`) and job ID verification to prevent stale cached responses from bypassing the modal.
* **Auto-Revert Degraded Stages (`fallback_unstabilized`)**: When enabled (default), the pipeline reads each intermediate stage's stabilization report upon completion. If a stage degrades video quality (`UNSTABILIZED`, `DEGRADED`, or `FAILED`), its transform is automatically discarded from the master queue, and the pipeline cursor `current_file` reverts back to the previous clean stage's output. This prevents downstream optical algorithms (Cinematic, Horizon, Traveldir) from calculating counter-rotations against artificial filter artifacts (cascade error). Toggleable via UI and saved with configuration presets.

### 5. Multi-Stage Stabilization Cascade
Multiple stabilization methods can be chained in any order via `--stabilize_methods`:

* **Execution Order & Dependencies**:
  * **Inertial Reference Priority**: When combining hardware IMU (`telemetry`) and keyframe horizon leveling (`horizon`), **`telemetry` must always execute before `horizon`**. Hardware IMU establishes the physical gravity and inertial reference frame. Applying manual horizon tilts before hardware de-rotation perturbs coordinate axes in $\mathrm{SO}(3)$ and causes axis cross-bleeding and conflicting dual-leveling.
  * **Order Validation & Alerts**: The Web UI dynamically validates the execution order, surfacing a high-visibility warning callout (`#stab-order-warning`) with a 1-click auto-fix button (`[⚡ Fix Order]`) whenever `horizon` precedes `telemetry`. `scripts/pipeline.py` logs a matching `[Pipeline WARNING]` notice.

> [!TIP]
> **Developer Guide**: To implement a new stabilization algorithm and integrate it into this cascade, backend API, and web interface, see [Adding a New Stabilization Method](./adding_stabilization_methods.md).

#### A. Telemetry & IMU Fusion
* **Engines**: `scripts/extract_telemetry.py`, `utils/imu_fusion.py`, `prepare_telemetry_sendcmd()` in `pipeline.py`
* **Filters**: Implements **Mahony**, **Madgwick**, **Complementary**, and **EKF** 6-axis attitude filters with customizable gain (`--telemetry_fusion_gain`).
* **Operational Modes**:
  * `smooth`: Moving-average temporal smoothing window (`--telemetry_smoothing`).
  * `lock`: Horizon lock relative to a chosen reference frame (`--telemetry_ref_frame`).
  * `level`: Continuous baseline gravity leveling.
  * `zero`: Absolute zero lock ($0^\circ, 0^\circ, 0^\circ$).

#### B. Kopf 3D-2D Visual Feature Tracking
* **Engine**: `scripts/stabilize_kopf.py`
* **Mechanics**:
  * Projects equirectangular frames to 6 cubemap faces (`--kopf_cube_face`, default 1024).
  * Tracks optical features across cubemap boundaries using Kanade-Lucas-Tomasi (KLT).
  * Estimates 3D camera rotation updates with optional deformed-rotation jitter modeling (`--kopf_deformed`) and non-VR reapplication (`--kopf_reapply`).

#### C. Kabsch Orthogonal Trajectory SVD Alignment
* **Engine**: `scripts/stabilize_kabsch.py`
* **Mechanics**:
  * Solves the constrained Orthogonal Procrustes problem on spherical unit vectors:
    $$\min_{R} \| R P - Q \|_F \quad \text{subject to } R^T R = I, \; \det(R) = 1$$
  * Reconciles optical tracking point clouds with absolute spherical coordinates to eliminate low-frequency drift.

#### D. 360 Spherical Optical Smoothing (VidSTAB 360)
* **Engine**: `scripts/stabilize_vidstab.py`
* **Mechanics**:
  * 360-aware spherical optical flow stabilizer (inspired by the Bigsh0t / Kdenlive VR spherical algorithms).
  * Tracks Lucas-Kanade feature vectors across spherical coordinates and estimates rotational deltas via Kabsch SVD.
  * Outputs continuous $v360$ yaw, pitch, and roll corrections, eliminating equirectangular $180^\circ$ seam-line artifacts common in planar 2D filters.

#### E. Cinematic $L_1/L_2$ Trajectory Optimization
* **Engine**: `scripts/stabilize_cinematic.py`
* **Mechanics**:
  * Formulates camera trajectory smoothing as an $L_1$-norm convex optimization problem balancing path fidelity, velocity smoothness, and acceleration constraints:
    $$\min_{C} \sum_t \| C_t - O_t \|^2 + \lambda_v \| \Delta C_t \|_1 + \lambda_a \| \Delta^2 C_t \|_1$$
  * Emulates professional crane and dolly motions while suppressing high-frequency vibrations.

#### F. Horizon Leveling & Auto-Checkpoints
* **Engine**: `scripts/stabilize_horizon.py`
* **Mechanics**:
  * Aligns camera roll and pitch across interactive or automated horizon checkpoints.
  * Generates incremental Euler angle deltas (`# format: delta`) for additive FFmpeg `v360` sendcmd rotation application.
  * **Auto-Detection (`--horizon_autodetect`)**: Automatically extracts keyframe inflection points from telemetry, vision, motion trajectories, regular cadence, or vertical pitch extrema turning points (`--horizon_autodetect_source auto|cadence|vision|pitch_extrema`).
  * **Cadence & Smart Gait AI Strategies**:
    * **Smart Gait & Stride AI (`auto` / `pitch_extrema`)**: Automatically detects physical walking/running nodal points (zero-crossings where $\frac{d(\text{pitch})}{dt} = 0$ with swing $\ge \text{min\_prominence}$, default $0.8^\circ$). Centers mount tilt baseline, inverts tilt into corrective level coordinates, and places keyframes at step nodding apices. Applies lateral roll sway damping (`--horizon_roll_damping`, default $0.70\times$) with widened temporal filtering to suppress human hip oscillation while preserving true camera tilt leveling. Operates unconstrained across long video sequences without artificial keyframe budget ceilings. Flat camera motions produce 0 keyframes.
    * **Time Interval Cadence (`cadence`)**: Seeds regular keyframe intervals matching walking, running, cycling, or vehicle rhythm (0.5s, 1.0s, 5.0s, 15.0s). Continuous pitch and roll curves are sampled directly at each timestamp.
    * **Visual Horizon AI (`vision`, Default)**: Direct video frame optical analysis for post-stabilized videos or clips lacking IMU telemetry.
    * **Keyframe Density & Cadence (`--horizon_autodetect_density`)**: Controls cadence sampling interval and Ramer-Douglas-Peucker angle tolerance: `smooth` (2.0s, ~3.5°), `balanced` (0.5s, ~2.5°), `fine` (0.33s, ~1.5°), `detailed` (0.25s, ~1.5°), and `ultra_dense` (0.15s, ~0.75°, UI default; alias `ultra`).
  * **Interactive Horizon Editor (`horizon_editor.html`)**:
    * **View-Lock Navigation**: When `Ignore Yaw` is active (default), left-click dragging locks view pan to $0.0^\circ$ forward while adjusting Pitch up/down. This ensures that Pitch and Roll adjustments remain strictly orthogonal to the 2D screen coordinate system and horizontal reference guide.
    * **Free 360° Inspection Orbit**: Holding Right-Click and dragging, or holding `Alt` / `Shift` with Left-Drag, orbits the camera view across the full $360^\circ$ sphere to inspect horizon consistency without mutating level adjustments or keyframe values.
    * **One-Click Recenter**: Whenever view yaw deviates ($|\text{viewYaw}| > 0.2^\circ$), a `[🎯 Center (0°)]` button appears in the HUD. Clicking this button or the HUD yaw value immediately snaps view pan back to forward ($0.0^\circ$).
    * **Angle Reset**: The `Reset` button resets Pitch, Roll, Yaw, and View Pan back to canonical $0.0^\circ$.
    * **Field of View**: Mouse wheel smoothly scales FOV between $30^\circ$ and $120^\circ$.
    * **Variable Playback Speed**: Transport speed dropdown supporting $0.10\times$ (super slow), $0.25\times$ (quarter), $0.50\times$ (half), $0.75\times$, $1.00\times$ (normal), $1.25\times$, $1.50\times$, and $2.00\times$, also hotkey-controllable via `[` (slow down) and `]` (speed up).
    * **Direct Standalone Video Loading**: Opening videos via manual file picker (`Open Video File`) automatically extracts and tracks video filename and stem metadata (`state.videoName`, `state.outBase`), enabling full backend Auto-Detect (Visual Horizon AI, Smart Gait, Cadence) and checkpoint work persistence without requiring query parameters.
    * **Auto-Detection Feedback**: Auto-detect dialog features static backdrop protection against accidental dismissal and displays a real-time elapsed-seconds timer (`Analyzing (Xs)...` / `Detecting Peaks (Xs)...`) on action buttons and status banners during motion/telemetry analysis.
    * **Parameter Audit Log & Inspector (`{out_base}_horizon_params.log`)**: Full audit logging of all stabilization parameters (Strategy, Density, Epsilon, Prominence, Roll Damping, Baseline offsets, Range, Keyframe summary) written alongside test files in the work folder. Active parameters are displayed directly in the editor's sidebar card with an interactive `[📋 View Parameters Log]` viewer modal.
    * **Keyboard Controls**: `Space` (Play/Pause), `[` / `]` (Decrease/Increase playback speed), `←` / `→` (Step 1 frame backward/forward), `J` / `K` (Previous/Next checkpoint), `Esc` (Cancel drawing mode).

#### G. Travel Direction Auto-Heading Lock
* **Engine**: `scripts/stabilize_traveldir.py`
* **Mechanics**:
  * Derives vehicle/camera travel vector from motion tracks, telemetry, visual phase correlation, or target heading.
  * Modes: `travel_direction` (locks forward heading), `target_lock` (locks to fixed yaw), `damped_follow` (exponential smoothing).
  * Generates incremental Euler angle deltas (`# format: delta`) for additive FFmpeg `v360` sendcmd rotation application.
  * Employs deadband angle thresholds (`--traveldir_deadband`, default $1.5^\circ$) and exponential damping factors (`--traveldir_damping`) to prevent rapid oscillatory panning.
  * Emits high-fidelity JSON trajectory metadata (`{out_base}_traveldir_meta.json`) recording raw camera yaw, forward travel trend, final locked viewer heading, and pan rates.

### 6. Trajectory & Motion Curve Visualization
* **Engine**: `scripts/visualize_corrections.py`
* **Responsibilities**:
  * Automatically parses generated `sendcmd`, `meta.json`, and `.motion` files to produce high-resolution trajectory and correction curves.
  * Specialized Diagnostics:
    * **Telemetry IMU Graph**: Dual-lens pitch/roll leveling and sensor fusion curves.
    * **Optical / VidStab / Kopf / Kabsch / Cinematic**: Multi-axis angular displacement and tremor reduction.
    * **Travel-Direction Lock (`_traveldir_graph.png`)**: Dual-panel chart displaying Raw Heading (Yaw) vs Forward Travel Trend vs Stabilized View Heading with translucent Deadband Envelope ($\pm\text{threshold}^\circ$), Frame Numbers on secondary axis, and bottom panel showing Pan Angular Velocity ($^\circ/\text{s}$), Heading Deviation ($^\circ$), and real-time Jitter Reduction %.

### 7. Nadir Branding & Polar Patch Overlay
* **Engine**: Built directly into `scripts/pipeline.py`
* **Mechanics**:
  * Projects flat logo assets (`--nadir_logo`) from planar space into polar coordinates using FFmpeg's `v360=input=flat:output=equirect:pitch=90` with horizontal and vertical FOV controls (`--nadir_fov`, `--nadir_fov_v`).
  * In Quality Modes 1 and 4, the nadir overlay is merged directly into the Single-Pass Master Render filter graph, completely avoiding additional re-encoding loss.

### 8. High-Fidelity Video Encoding & Lossless Audio Remuxing
* **Hardware NVENC**: Uses `h264_nvenc` or `hevc_nvenc` with configurable bitrate (`--video_bitrate`) or high-quality presets.
* **Software CPU**: Uses `libx264` or `libx265` with configurable CRF (`--crf`, default 18) and preset (`--preset`).
* **Audio Handling**: Preserves original multi-channel or ambisonic spatial audio losslessly via `-c:a copy`, or strips audio when requested (`--remove_audio`).

### 9. Spatial Metadata Injection
* **Engine**: `spatialmedia` package and internal `embed_vrot_metadata()` in `scripts/pipeline.py`
* **Responsibilities**:
  * Injects Google Spatial Media V1/V2 XML atoms declaring equirectangular projection geometry and stereoscopic parameters.
  * Preserves and re-embeds private ISO-BMFF `vrot` metadata atoms from the source video into output MP4 headers.
  * Emits accurate stage-specific status updates (`Injecting VR metadata for <Stage Name>`) across all chained stages (Telemetry, Kopf, Kabsch, Vidstab, Cinematic, Horizon Checkpoints, Travel-Direction Lock, Master Render) using reverse-token inspection on intermediate filenames.

### 10. Geospatial Publishing & External Tool Exports
* **Google Street View Publishing (`scripts/streetview_gpx.py`)**:
  * Generates Street View Studio compliant deliverables (`_streetview.mp4` and `_streetview.gpx`).
  * Synchronizes video frames with GPX tracks using sub-second GPS interpolation (Mode A/B), coordinate auto-matching, and Gaussian track jitter smoothing (`--streetview_smooth_gps`).
  * Injects binary ISO-BMFF `camm` (Camera Motion Metadata) telemetry tracks into the MP4 container.
  * Renders an interactive Leaflet GPS track inspection map (`_streetview_map.html`).
* **External NLE Sidecar Sidecars**:
  * `--util_gcsv`: Generates Gyroflow-compatible `.gcsv` and `.MP4.gcsv` telemetry logs.
  * `--util_bigsh0t`: Exports Kdenlive / Bigsh0t 360 motion tracking binary files (`.bigsh0t360motion`).

### 11. Automated Sub-Pixel Verification & Performance Reporting
* **Verification Engine**: `analyze_stabilization_report()` and `compute_stabilization_percentage()` in `scripts/pipeline.py`
* **Metrics**:
  * Extracts rectilinear viewports from raw baseline video (raw stitched video or pre-stitched input video) and stabilized videos and performs sub-pixel 2D phase correlation (`cv2.phaseCorrelate`).
  * Calculates **Peak Shock Reduction %**, **RMS Tremor Jitter %**, and **Vertical Horizon Lock %** (for horizon stages).
  * Measures 3D angular jerk and rotational jitter for spherical rotation stages.
    * **Composite Stabilization Scoring**:
      * Computes a unified representative score by averaging physical camera stability metrics (Jitter absorption, Peak Shock reduction, Vertical Horizon Lock).
      * **3D Sidecar Prioritization**: For spherical rotation stages, high-confidence 3D sidecar scores (e.g. Kopf angular tremor absorption `sidecar_pct`) serve as the representative fallback when 2D planar projection metrics suffer coordinate drift noise ($\le 0\%$), with bounded energy partition ensuring tremor absorption never exceeds 100.0%. When planar tracking confirms positive optical improvements, optical metrics govern directly.
      * **Penalty Preservation**: Physical degradations (negative Peak Shock or negative Jitter) actively penalize the score in 2D stages.
      * **Spherical Planar Drift & Status Consistency**: Planar overall 2D frame displacement (`Mean 2D Frame Jump`) is included only when $\ge 0$, since negative planar drift is an expected equirectangular coordinate artifact of 360 spherical counter-rotation. When 3D spherical stabilization is confirmed (`YES`), planar coordinate artifacts are prevented from producing negative percentages alongside successful stabilization.
    * **Horizon Checkpoints Cadence Precedence**: When extracting Smart Gait extrema, raw physical 6-axis IMU telemetry is prioritized over intermediate optical motion tracking files to ensure real human stride crests and troughs are tracked accurately.
  * Writes comprehensive net summaries (`_stabilization_report.txt`) and individual stage diagnostic logs (`_telemetry_report.txt`, `_vidstab_report.txt`, `_kabsch_report.txt`, `_kopf_report.txt`, `_cinematic_report.txt`, `_horizon_report.txt`, `_traveldir_report.txt`).

### 12. Process Supervision & Safety Watchdogs
* **Job Objects**: On Windows, child processes are bound to an OS Job Object (`utils/process.py`) to guarantee complete process tree termination upon exit or interruption.
* **Runaway Watchdog**: A background watchdog monitors FFmpeg encoding timestamps. If the encoded duration exceeds the source video length, the process is terminated immediately to prevent runaway disk usage.
* **Atomic Progress Reporting**: Maintains live progress metrics (PID, speed, ETA, FPS, phase) written atomically to `data/runtime/temp/status.json`.

### 13. Non-Destructive Artifact Retention & Manual Cleanup
* **Zero Automatic Deletion**: The pipeline never automatically deletes intermediate stitched video stages (`_1_stitched.mp4`), stabilized stage outputs, transform files (`.trf`), or telemetry dumps in `data/runtime/work/`. All files remain preserved for inspection, auditing, and iterative multi-stage tuning.
* **Manual Disk Cleanup**: Users can safely clean up working files at any time:
  * **Windows PowerShell**: `Get-ChildItem -Path data\runtime\work -File | Remove-Item -Force`
  * **Linux / macOS Bash**: `rm -rf data/runtime/work/*`
