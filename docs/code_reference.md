# 📖 Code Reference & API Documentation

> *Auto-generated from source code docstrings via `scripts/generate_code_docs.py`.*

This document provides an automated, comprehensive code-level reference for all modules, classes, and functions in StitchStab 360.

## 📑 Table of Contents

- [**1. FastAPI Server & Backend Application**](#1.-fastapi-server--backend-application)
  - [`server.py`](#serverpy)
- [**2. Central Configuration**](#2.-central-configuration)
  - [`settings.py`](#settingspy)
- [**3. Pipeline Orchestration**](#3.-pipeline-orchestration)
  - [`pipeline.py`](#pipelinepy)
- [**4. Stabilization Engines**](#4.-stabilization-engines)
  - [`stabilize_kopf.py`](#stabilize_kopfpy)
  - [`stabilize_kabsch.py`](#stabilize_kabschpy)
  - [`stabilize_vidstab.py`](#stabilize_vidstabpy)
  - [`stabilize_cinematic.py`](#stabilize_cinematicpy)
  - [`stabilize_traveldir.py`](#stabilize_traveldirpy)
  - [`stabilize_horizon.py`](#stabilize_horizonpy)
- [**5. Calibration, Telemetry & Geospatial Tools**](#5.-calibration-telemetry--geospatial-tools)
  - [`auto_calibrate.py`](#auto_calibratepy)
  - [`detect_warmup.py`](#detect_warmuppy)
  - [`extract_telemetry.py`](#extract_telemetrypy)
  - [`convert_telemetry.py`](#convert_telemetrypy)
  - [`streetview_gpx.py`](#streetview_gpxpy)
  - [`visualize_corrections.py`](#visualize_correctionspy)
  - [`visual_odometry.py`](#visual_odometrypy)
- [**6. Remapping, Nadir & Video Operations**](#6.-remapping-nadir--video-operations)
  - [`generate_alpha_mask.py`](#generate_alpha_maskpy)
  - [`crop_camera_video.py`](#crop_camera_videopy)
- [**7. Utilities & Low-Level Helpers**](#7.-utilities--low-level-helpers)
  - [`mp4_utils.py`](#mp4_utilspy)
  - [`imu_fusion.py`](#imu_fusionpy)
  - [`calibrate_dual_fisheye.py`](#calibrate_dual_fisheyepy)
  - [`global_calibrate.py`](#global_calibratepy)
  - [`inspect_mp4.py`](#inspect_mp4py)
  - [`process.py`](#processpy)
  - [`stabilize_telemetry.py`](#stabilize_telemetrypy)
  - [`stitch_opencv.py`](#stitch_opencvpy)
- [**8. Developer & Maintenance Tools**](#8.-developer--maintenance-tools)
  - [`git_rag.py`](#git_ragpy)
  - [`generate_code_docs.py`](#generate_code_docspy)

---

## 1. FastAPI Server & Backend Application

REST API routing, WebSocket progress broadcasting, process management, and media streaming endpoints.

### <a id="serverpy"></a>`server.py`

#### Classes

##### `class _AllowlistStaticFiles(StaticFiles)` (Line 233)

StaticFiles subclass restricted to an explicit set of root-level filenames.

**Methods:**

```python
async def get_response(self, path: str, scope)
```
##### `class _ConfigStaticFiles(StaticFiles)` (Line 249)

Serves only config/config.json — blocks settings.py, run_counter.txt, etc.

**Methods:**

```python
async def get_response(self, path: str, scope)
```
#### Functions

##### *(async)* `lifespan` (Line 68)

```python
async def lifespan(app: FastAPI)
```
Manage application startup and shutdown lifecycle hooks.


**Args:**

- `app`: The running FastAPI application instance.

##### *(async)* `missing_parameter_exception_handler` (Line 91)

```python
async def missing_parameter_exception_handler(request: Request, exc: MissingParameterError)
```
Handle missing mandatory parameter exceptions with HTTP 422 JSON response.


**Args:**

- `request`: Incoming FastAPI request.
- `exc`: Raised MissingParameterError instance.


**Returns:**

JSONResponse with HTTP 422 status and error details.

##### *(async)* `security_middleware` (Line 122)

```python
async def security_middleware(request: Request, call_next)
```
Enforce payload size limits, block sensitive static file paths, and add security headers.


**Args:**

- `request`: Incoming FastAPI HTTP request.
- `call_next`: Request handler continuation callback.


**Returns:**

HTTP Response with added security headers or error response if rejected.

##### *(async)* `global_exception_handler` (Line 191)

```python
async def global_exception_handler(request: Request, exc: Exception)
```
Catch unhandled exceptions and return safe HTTP 500 responses.


**Args:**

- `request`: Incoming FastAPI HTTP request.
- `exc`: Caught unhandled exception.


**Returns:**

JSONResponse with HTTP 500 error or HTTP 499 for client disconnections.


---

## 2. Central Configuration

Central application settings, storage directories, baseline constants, and preset defaults.

### <a id="settingspy"></a>`config/settings.py`


---

## 3. Pipeline Orchestration

End-to-end multi-stage pipeline coordinator and CLI execution interface.

### <a id="pipelinepy"></a>`scripts/pipeline.py`

#### Functions

##### `_cleanup_active_child` (Line 48)

```python
def _cleanup_active_child()
```
Terminates active child subprocesses cleanly across platforms.

Kills active FFmpeg or Python child processes using taskkill on Windows
(with process tree termination) or SIGKILL on POSIX systems.

##### `_signal_handler` (Line 68)

```python
def _signal_handler(signum, frame)
```
Handles OS termination signals by terminating child processes before exit.


**Args:**

- `signum (int)`: Signal number received (SIGINT, SIGTERM, etc.).
- `frame (types.FrameType | None)`: Current execution frame.

##### `timestamped_print` (Line 87)

```python
def timestamped_print(*args, **kwargs)
```
Outputs messages prefixed with current timestamp [YYYY-MM-DD HH:MM:SS].

Passes empty prints through unaltered; wraps standard messages with the
current timestamp before delegating to builtins.print.


**Args:**

  *args: Variable arguments to print.
  **kwargs: Keyword arguments forwarded to builtins.print.

##### `get_video_duration` (Line 108)

```python
def get_video_duration(input_path)
```
Extracts total duration of a video file in seconds via ffprobe.

Queries both format-level and stream-level durations and returns the
first positive float parsed. If the input is a still photo, returns 1.0.


**Args:**

- `input_path (str)`: Filepath to the video or photo.


**Returns:**

- `float | None`: Media duration in seconds, or None if extraction failed.

##### `inject_gpano_photo_metadata` (Line 147)

```python
def inject_gpano_photo_metadata(photo_path)
```
Inject standard Google Photo Sphere (GPano) XMP metadata into a 360 photo via ExifTool.

##### `update_status` (Line 193)

```python
def update_status(status_file, data)
```
Atomically updates the pipeline status JSON file with progress metrics.

Appends current process ID and global tracking metadata. Employs a temporary
file with atomic replacement and retries to prevent write collisions.


**Args:**

- `status_file (str)`: Path to the target status.json file.
- `data (dict)`: Status payload containing phase, progress, speed, ETA, etc.

##### `run_ffmpeg` (Line 237)

```python
def run_ffmpeg(cmd, status_file, duration, start_ts, phase_label, status_code='stitching', active_duration=None)
```
Executes an FFmpeg or Python subprocess while streaming progress to status JSON.

Parses stdout/stderr for speed, time, and progress milestones, calculates
real-time ETA, and includes a safety watchdog against runaway encoding loops.


**Args:**

- `cmd (list[str])`: Command arguments array to execute.
- `status_file (str)`: Path to the status JSON file.
- `duration (float)`: Expected total media duration in seconds.
- `start_ts (float)`: Pipeline start epoch timestamp.
- `phase_label (str)`: Human-readable label for current processing phase.
- `status_code (str, optional)`: Status state string. Defaults to "stitching".
- `active_duration (float, optional)`: Duration of active video segment before padding.


**Returns:**

- `int`: Process return code (0 for success).

##### `ensure_circle_mask` (Line 462)

```python
def ensure_circle_mask(width, height, script_dir)
```
Ensures an elliptical/circular dual-fisheye alpha mask exists on disk.

Generates a high-contrast 8-bit mask image if not already cached.


**Args:**

- `width (int)`: Frame width in pixels.
- `height (int)`: Frame height in pixels.
- `script_dir (str)`: Directory containing this script.


**Returns:**

- `str | None`: Absolute path to the generated mask PNG, or None on failure.

##### `compute_2d_horizon_stabilization_angles` (Line 490)

```python
def compute_2d_horizon_stabilization_angles(roll_deg, pitch_deg, yaw_deg, target_roll, target_pitch, target_yaw, multiplier_roll=1.0, multiplier_pitch=1.0)
```
Computes 2D rotational delta angles for horizon leveling.

Calculates raw signed deltas relative to target orientation, leaving
lens-specific sign inversion to the calling context.


**Args:**

- `roll_deg (float)`: Measured camera roll angle in degrees.
- `pitch_deg (float)`: Measured camera pitch angle in degrees.
- `yaw_deg (float)`: Measured camera yaw angle in degrees.
- `target_roll (float)`: Desired target roll angle in degrees.
- `target_pitch (float)`: Desired target pitch angle in degrees.
- `target_yaw (float)`: Desired target yaw angle in degrees.
- `multiplier_roll (float, optional)`: Scaling factor for roll correction. Defaults to 1.0.
- `multiplier_pitch (float, optional)`: Scaling factor for pitch correction. Defaults to 1.0.


**Returns:**

- `tuple[float, float, float]`: Raw (stab_roll, stab_pitch, stab_yaw) deltas in degrees.

##### `v360_euler_to_matrix` (Line 525)

```python
def v360_euler_to_matrix(yaw_deg, pitch_deg, roll_deg)
```
Converts v360 intrinsic Euler rotation angles to a 3x3 rotation matrix.


**Args:**

- `yaw_deg (float)`: Yaw rotation angle in degrees.
- `pitch_deg (float)`: Pitch rotation angle in degrees.
- `roll_deg (float)`: Roll rotation angle in degrees.


**Returns:**

- `np.ndarray`: 3x3 SO(3) orthogonal rotation matrix (float64).

##### `v360_matrix_to_euler` (Line 546)

```python
def v360_matrix_to_euler(R)
```
Extracts v360 Euler rotation angles from a 3x3 rotation matrix.

Handles gimbal lock singularities when pitch approaches ±90 degrees.


**Args:**

- `R (np.ndarray)`: 3x3 rotation matrix.


**Returns:**

- `tuple[float, float, float]`: Euler angles in degrees as (yaw, pitch, roll).

##### `compose_sendcmd_files` (Line 568)

```python
def compose_sendcmd_files(sendcmd_files, output_file)
```
Combines multiple FFmpeg v360 sendcmd rotation scripts into a single composite file.

Interpolates time-series rotations in SO(3) space and applies smart stage pruning
to bypass identity or trivial transforms (<0.015° deviation).


**Args:**

- `sendcmd_files (list[str])`: List of paths to v360 sendcmd instruction files.
- `output_file (str)`: Destination path for the composed sendcmd script.


**Returns:**

- `bool`: True if composite file was successfully written, False otherwise.

##### `is_identity_sendcmd` (Line 739)

```python
def is_identity_sendcmd(filepath, threshold_deg=0.015)
```
Check whether a sendcmd file represents an identity or trivial transform (< threshold_deg max deviation).

Correctly accumulates frame-to-frame angular deltas when format is '# format: delta',
ensuring smooth continuous motion trajectories are not misclassified as identity.


**Args:**

- `filepath (str)`: Path to sendcmd file.
- `threshold_deg (float)`: Peak deviation threshold in degrees.


**Returns:**

- `bool`: True if file is missing, empty, or all cumulative angles are < threshold_deg; False otherwise.

##### `get_effective_prior_sendcmd` (Line 790)

```python
def get_effective_prior_sendcmd(active_files, out_base, stage_name)
```
Composes all active non-trivial upstream transforms into a composite reference trajectory.

Ensures downstream trajectory optimizers (cinematic, traveldir) receive the true camera
motion path rather than isolated residuals or empty dummy transforms.


**Args:**

- `active_files (list[str])`: List of preceding sendcmd filepaths in composition order.
- `out_base (str)`: Base output path prefix for temporary composed files.
- `stage_name (str)`: Identifier of the requesting stage (e.g. 'cinematic', 'traveldir').


**Returns:**

- `str`: Filepath to the effective prior trajectory file, or empty string if none.

##### `smooth_telemetry_angles` (Line 815)

```python
def smooth_telemetry_angles(angles, window_size, sigma_range_deg=2.5)
```
Smooths IMU telemetry angles using combined median and bilateral Gaussian filtering.

Eliminates non-causal pre-ring and step-jump tilt glitches across hardware IMU
discontinuities while maintaining continuous Gaussian stabilization on regular motion.


**Args:**

- `angles (list[tuple[float, float, float]])`: Raw (roll, pitch, yaw) tuples in degrees.
- `window_size (int)`: Filter window size in frames.
- `sigma_range_deg (float, optional)`: Range kernel deviation threshold in degrees. Defaults to 2.5.


**Returns:**

- `list[tuple[float, float, float]]`: Smoothed (roll, pitch, yaw) tuples in degrees.

##### `create_meta_copy` (Line 903)

```python
def create_meta_copy(filepath, status_file=None, start_ts=None, stab_name=None)
```
Injects 360 VR spatial metadata into a video copy using the spatialmedia module.

Creates a companion video file suffixed with '_VR' containing spherical
metadata atoms required for 360 projection on VR headsets and platforms.


**Args:**

- `filepath (str)`: Source equirectangular video file path.
- `status_file (str, optional)`: Status tracking file to update during injection.
- `start_ts (float, optional)`: Pipeline start timestamp for elapsed time.
- `stab_name (str, optional)`: Stabilization stage display name for status reporting.


**Returns:**

- `bool`: True if metadata injection succeeded, False otherwise.

##### `embed_vrot_metadata` (Line 980)

```python
def embed_vrot_metadata(src_video, dst_video)
```
Transfers moov/udta camera orientation telemetry (vrot atom) between MP4 containers.

Copies vendor camera orientation telemetry atoms from the source video into
the stitched output container to preserve hardware orientation tracks.


**Args:**

- `src_video (str)`: Path to source video with telemetry atoms.
- `dst_video (str)`: Path to target video receiving telemetry atoms.


**Returns:**

- `bool`: True if telemetry was copied and embedded successfully, False otherwise.

##### `analyze_kopf_jitter_from_motion` (Line 1079)

```python
def analyze_kopf_jitter_from_motion(motion_file)
```
Extracts rotational jitter, jerk, and frequency metrics from a .kopf360motion file.

Evaluates corrected jitter magnitudes, angular acceleration, jerk, and band-passed
tremor frequencies for 3D Kopf trajectory evaluation. Converts results to rectilinear
pixel equivalents for 1280px @ 90° HFoV.


**Args:**

- `motion_file (str)`: Path to the .kopf360motion data file.


**Returns:**

- `dict | None`: Dictionary containing RMS/peak correction metrics, jerk metrics,
and frequency bands, or None on read failure.

##### `analyze_kabsch_jitter_from_motion` (Line 1209)

```python
def analyze_kabsch_jitter_from_motion(motion_file)
```
Computes spherical SVD rotational jitter metrics from a .kabsch360motion file.

Calculates RMS and peak angular corrections converted to rectilinear pixel equivalents
for 1280px @ 90° HFoV.


**Args:**

- `motion_file (str)`: Path to the .kabsch360motion tracking file.


**Returns:**

- `dict | None`: Dictionary of jitter and correction metrics, or None on error.

##### `analyze_telemetry_jitter` (Line 1261)

```python
def analyze_telemetry_jitter(telemetry_file, sendcmd_file=None)
```
Analyzes raw telemetry and/or applied v360 sendcmd files for stabilization metrics.

Calculates leveling corrections, drift compensation, and angular acceleration stats.


**Args:**

- `telemetry_file (str)`: Path to parsed telemetry text file.
- `sendcmd_file (str, optional)`: Path to generated FFmpeg sendcmd script.


**Returns:**

- `dict | None`: Metric dictionary with leveling performance, or None on error.

##### `analyze_optical_jitter_from_motion` (Line 1351)

```python
def analyze_optical_jitter_from_motion(motion_file)
```
Calculates 2D optical flow translation and rotational compensation metrics.


**Args:**

- `motion_file (str)`: Path to the .optical360motion tracking file.


**Returns:**

- `dict | None`: Dictionary containing RMS/peak pixel shifts and angular limits,
or None on error.

##### `compute_stabilization_percentage` (Line 1397)

```python
def compute_stabilization_percentage(m_data, is_stabilized=None, sidecar_pct=None)
```
Calculates a unified representative stabilization percentage (+/-).

Averages primary stabilization performance metrics (peak shock reduction,
high-frequency tremor/jitter absorption, motion continuity, and vertical
horizon lock).


**Args:**

- `m_data (dict, optional)`: Metric dictionary from analyze_stabilization_report.
- `is_stabilized (bool, optional)`: Override stabilization success state.
- `sidecar_pct (float, optional)`: Optional sidecar jitter reduction percentage.


**Returns:**

- `str`: Formatted percentage string (e.g. '+3.5%', '-3.8%').

##### `write_single_stage_report` (Line 1467)

```python
def write_single_stage_report(m_key, m_name, in_v, out_v, r_path, out_base, q_mode, step1_stitch=None, target_stab_file=None, kopf_disclaimer='', fusion_info=None)
```
Writes an individual stage stabilization metrics report upon stage completion.


**Args:**

- `m_key (str)`: Method identifier key (e.g. 'telemetry', 'vidstab', 'kopf').
- `m_name (str)`: Method display name.
- `in_v (str)`: Input video path for this stage.
- `out_v (str)`: Output stabilized video path.
- `r_path (str)`: Destination filepath for the text report.
- `out_base (str)`: Base filename prefix for stage assets.
- `q_mode (str)`: Quality optimization mode identifier ('0', '1', '2', etc.).
- `step1_stitch (str, optional)`: Base stitched video path.
- `target_stab_file (str, optional)`: Final target stabilized video path.
- `kopf_disclaimer (str, optional)`: Disclaimer notice for Kopf processing.
- `fusion_info (dict | str, optional)`: Sensor fusion configuration details.


**Returns:**

- `dict | None`: Analyzed metric dictionary if available, otherwise None.

##### `extract_test_id` (Line 1926)

```python
def extract_test_id(path_str)
```
Extracts numeric test/job identifier from file path or base name (e.g. out_3381, job_3381, test_3381).

##### `check_and_apply_stage_fallback` (Line 1947)

```python
def check_and_apply_stage_fallback(stage_key, stage_display_name, report_path, sendcmd_file, stage_in_file, current_file, active_sendcmd_files, executed_methods_so_far, fallback_enabled, reverted_stages, status_file=None)
```
Checks if a stage degraded stabilization quality and reverts input if enabled.


**Args:**

- `stage_key (str)`: Method key ('vidstab', 'telemetry', etc.).
- `stage_display_name (str)`: Display name for the stage.
- `report_path (str)`: Path to stage report text file.
- `sendcmd_file (str)`: Sendcmd instructions file generated for this stage.
- `stage_in_file (str)`: Input video path before this stage executed.
- `current_file (str)`: Output video path after this stage executed.
- `active_sendcmd_files (list[str])`: Mutable list of active sendcmd files.
- `executed_methods_so_far (list[str])`: Mutable list of executed method keys.
- `fallback_enabled (bool)`: Whether automatic fallback is enabled.
- `reverted_stages (dict)`: Dictionary tracking reverted stages and reasons.
- `status_file (str, optional)`: Status file to update.


**Returns:**

- `tuple[str, bool]`: (new_current_file, was_reverted)

##### `analyze_stabilization_report` (Line 2014)

```python
def analyze_stabilization_report(raw_video, stab_video, report_file, sample_frames=0, report_title='AUTOMATIC STABILIZATION REPORT', method_name=None, test_id=None)
```
Evaluates stabilization quality via sub-pixel phase correlation on rectilinear viewports.

Extracts distortion-free 90° front viewports from both raw and stabilized equirectangular
videos, computes inter-frame displacements, and logs tremor absorption percentages.


**Args:**

- `raw_video (str)`: Path to unstabilized input equirectangular video.
- `stab_video (str)`: Path to stabilized equirectangular video.
- `report_file (str)`: Output path where stabilization report is written.
- `sample_frames (int, optional)`: Maximum frames to sample (0 for full video). Defaults to 0.
- `report_title (str, optional)`: Header title for the generated report. Defaults to "AUTOMATIC STABILIZATION REPORT".
- `method_name (str, optional)`: Stabilization method or goal name (e.g. 'telemetry', 'summary'). Defaults to None.
- `test_id (str, optional)`: Numeric test/job identifier. Defaults to None.


**Returns:**

- `dict | None`: Dictionary of comparison metrics (tremor reduction %, RMS shifts),
or None on failure.

##### `prepare_telemetry_sendcmd` (Line 2259)

```python
def prepare_telemetry_sendcmd(args, out_base, duration, suffix='single', mode=None, smoothing=None, ref_frame=None, extractor=None, multiplier=None, multiplier_roll=None, multiplier_pitch=None, multiplier_yaw=None, max_correction=None, target_equirect=False, base_pitch=0.0, base_roll_l=0.0, base_roll_r=None, **kwargs)
```
Transforms raw camera IMU telemetry into dynamic FFmpeg v360 sendcmd rotation scripts.

Extracts gyro/accelerometer data, filters angles with Gaussian smoothing,
calculates delta rotations relative to reference frame or moving average,
and formats timed commands for the FFmpeg v360 filter.


**Args:**

- `args (argparse.Namespace)`: Command-line argument namespace.
- `out_base (str)`: Base file prefix for generated artifacts.
- `duration (float)`: Video duration in seconds.
- `suffix (str, optional)`: File suffix identifier. Defaults to "single".
- `mode (str, optional)`: Telemetry stabilization mode ("smooth", "horizon", "lock").
- `smoothing (int, optional)`: Smoothing window in frames.
- `ref_frame (int, optional)`: Zero-point reference frame index.
- `extractor (str, optional)`: Extractor script identifier.
- `multiplier (float, optional)`: Global rotation multiplier.
- `multiplier_roll (float, optional)`: Roll axis multiplier.
- `multiplier_pitch (float, optional)`: Pitch axis multiplier.
- `multiplier_yaw (float, optional)`: Yaw axis multiplier.
- `max_correction (float, optional)`: Clamp limit for corrections in degrees.
- `target_equirect (bool, optional)`: Whether output target is equirectangular canvas.
- `base_pitch (float, optional)`: Base pitch offset.
- `base_roll_l (float, optional)`: Left lens base roll offset.
- `base_roll_r (float, optional)`: Right lens base roll offset.
  **kwargs: Additional keyword arguments.


**Returns:**

- `tuple[str | None, str | None]`: Paths to (sendcmd_eq_file, sendcmd_dual_file).

##### `main` (Line 2492)

```python
def main()
```
CLI entry point for the 360 Video Stitching and Stabilization Pipeline.

Parses command-line arguments, checks video properties and prerequisites,
runs the stitching filters and post-stitching stabilization stages,
injects 360 VR spatial metadata, and outputs metrics reports.


---

## 4. Stabilization Engines

6-axis IMU fusion, Kopf feature tracking, Kabsch trajectory alignment, VidSTAB optical smoothing, Cinematic path optimization, and Travel Direction lock.

### <a id="stabilize_kopfpy"></a>`scripts/stabilize_kopf.py`

#### Functions

##### `get_cubemap_maps` (Line 76)

```python
def get_cubemap_maps(H, W, face_size)
```
Computes and caches equirectangular-to-cubemap cv2.remap coordinates.

Projects each of the 6 cube faces ('px', 'nx', 'py', 'ny', 'pz', 'nz')
onto the equirectangular pixel canvas.


**Args:**

- `H (int)`: Equirectangular image height in pixels.
- `W (int)`: Equirectangular image width in pixels.
- `face_size (int)`: Width/height of each square cube face.


**Returns:**

- `dict[str, tuple[np.ndarray, np.ndarray]]`: Mapping from face name to (map_x, map_y).

##### `equirect_to_cubemap_all` (Line 142)

```python
def equirect_to_cubemap_all(gray, face_size)
```
Converts a grayscale equirectangular frame into 6 cubemap face images.


**Args:**

- `gray (np.ndarray)`: Grayscale equirectangular frame (H, W).
- `face_size (int)`: Dimension in pixels of each cube face.


**Returns:**

- `dict[str, np.ndarray]`: Dictionary of 6 cube faces ('px', 'nx', etc.) of shape (face_size, face_size).

##### `save_cubemap_cross` (Line 165)

```python
def save_cubemap_cross(video_path, frame_idx, face_size, out_path)
```
Saves a debug visualization image showing the 6 cube faces in classic cross layout.

Arranges faces matching Kopf 2016 Fig. 2:

[ Top  ]
[Left][Front][Right][Back]
[Bottom]


**Args:**

- `video_path (str)`: Path to the source video.
- `frame_idx (int)`: Frame index to extract and project.
- `face_size (int)`: Resolution of each face in pixels.
- `out_path (str)`: Destination PNG filepath.

##### `cubemap_pt_to_sphere` (Line 261)

```python
def cubemap_pt_to_sphere(name, u, v, face_size)
```
Lifts a 2D cubemap face pixel coordinate to a 3D unit-sphere directional vector.


**Args:**

- `name (str)`: Cubemap face name ('px', 'nx', 'py', 'ny', 'pz', 'nz').
- `u (float)`: Horizontal pixel coordinate on face in [0, face_size).
- `v (float)`: Vertical pixel coordinate on face in [0, face_size).
- `face_size (int)`: Cubemap face dimension in pixels.


**Returns:**

- `np.ndarray`: 3D unit vector [x, y, z] on the unit sphere.

##### `axis_angle_to_matrix` (Line 307)

```python
def axis_angle_to_matrix(aa)
```
Converts an axis-angle rotation vector into a 3x3 rotation matrix via Rodrigues' formula.


**Args:**

- `aa (array_like)`: 3-element axis-angle vector.


**Returns:**

- `np.ndarray`: 3x3 SO(3) rotation matrix.

##### `matrix_to_axis_angle` (Line 328)

```python
def matrix_to_axis_angle(R)
```
Converts a 3x3 rotation matrix into an axis-angle vector.


**Args:**

- `R (np.ndarray)`: 3x3 rotation matrix.


**Returns:**

- `np.ndarray`: 3-element axis-angle rotation vector.

##### `slerp_matrix` (Line 341)

```python
def slerp_matrix(R0, R1, t)
```
Performs spherical linear interpolation (SLERP) between two 3x3 rotation matrices.


**Args:**

- `R0 (np.ndarray)`: Initial 3x3 rotation matrix.
- `R1 (np.ndarray)`: Target 3x3 rotation matrix.
- `t (float)`: Interpolation parameter in the range [0.0, 1.0].


**Returns:**

- `np.ndarray`: Interpolated 3x3 rotation matrix.

##### `matrix_to_euler_ypr` (Line 369)

```python
def matrix_to_euler_ypr(R)
```
Decomposes a 3x3 rotation matrix into Euler yaw, pitch, and roll in degrees.

Uses ZYX rotation order (Yaw around Z, Pitch around Y, Roll around X).


**Args:**

- `R (np.ndarray)`: 3x3 rotation matrix.


**Returns:**

- `tuple[float, float, float]`: (yaw_deg, pitch_deg, roll_deg).

##### `kabsch_svd` (Line 386)

```python
def kabsch_svd(A, B)
```
Calculates best-fit rotation matrix from vector set A to vector set B via SVD.


**Args:**

- `A (np.ndarray)`: Target unit vectors of shape (N, 3).
- `B (np.ndarray)`: Source unit vectors of shape (N, 3).


**Returns:**

- `np.ndarray`: 3x3 rotation matrix.

##### `kabsch_ransac` (Line 403)

```python
def kabsch_ransac(vecs_a, vecs_b, n_iters=100, inlier_thresh_rad=0.035)
```
Robustly estimates inter-keyframe rotation using RANSAC Kabsch SVD.


**Args:**

- `vecs_a (np.ndarray)`: Target unit vectors of shape (N, 3).
- `vecs_b (np.ndarray)`: Source unit vectors of shape (N, 3).
- `n_iters (int, optional)`: RANSAC iterations. Defaults to 100.
- `inlier_thresh_rad (float, optional)`: Inlier angular threshold in radians (~2°). Defaults to 0.035.


**Returns:**

- `tuple[np.ndarray, np.ndarray]`: Best 3x3 rotation matrix and boolean inlier mask.

##### `subsample_tracks` (Line 452)

```python
def subsample_tracks(tracks, max_n=MAX_OPT_TRACKS, seed=42)
```
Uniformly subsamples feature tracks to cap optimization problem size.


**Args:**

- `tracks (list[dict])`: Full list of extracted feature tracks.
- `max_n (int, optional)`: Maximum tracks to retain. Defaults to MAX_OPT_TRACKS.
- `seed (int, optional)`: Random seed. Defaults to 42.


**Returns:**

- `list[dict]`: Subsampled track list.

##### `robust_loss` (Line 475)

```python
def robust_loss(s)
```
Evaluates Cauchy-type robust loss: rho(s) = a^2 * log(1 + s / a^2).


**Args:**

- `s (float | np.ndarray)`: Squared residual error.


**Returns:**

- `float | np.ndarray`: M-estimator robust loss value.

##### `build_cost` (Line 487)

```python
def build_cost(tracks, key_rots, key_frames_set, frame_count)
```
Constructs vectorized trajectory smoothness cost for pure-rotation model (Section 3.3).

Pre-computes observation tables for first-order and second-order spherical motion
differences across all inner frames.


**Args:**

- `tracks (list[dict])`: Feature tracks.
- `key_rots (dict[int, np.ndarray])`: Keyframe rotation matrices.
- `key_frames_set (set[int])`: Set of keyframe indices.
- `frame_count (int)`: Total number of frames in video.


**Returns:**

- `tuple[list[int], dict[int, int], callable]`: Inner frame list, index map, and cost function.

##### `spherical_barycentric_weights` (Line 592)

```python
def spherical_barycentric_weights(p)
```
Computes approximate spherical barycentric weights for point p against 6 control vertices.


**Args:**

- `p (np.ndarray)`: 3D unit vector on sphere of shape (3,).


**Returns:**

- `np.ndarray`: Softmax barycentric weights array of shape (6,).

##### `build_deformed_cost` (Line 606)

```python
def build_deformed_cost(tracks, key_rots, key_frames_set, frame_count)
```
Constructs vectorized cost for deformed-rotation jitter model (Section 3.4).

Optimizes 18 DOF per inner frame (6 vertex rotations), regularizing spread between
neighboring vertex rotations.


**Args:**

- `tracks (list[dict])`: Feature tracks.
- `key_rots (dict[int, np.ndarray])`: Keyframe rotation matrices.
- `key_frames_set (set[int])`: Set of keyframe indices.
- `frame_count (int)`: Total frames in video.


**Returns:**

- `tuple[list[int], dict[int, int], callable]`: Inner frames, index map, and cost function.

##### `gaussian_smooth_1d` (Line 721)

```python
def gaussian_smooth_1d(signal, sigma)
```
Applies zero-phase 1D Gaussian smoothing with reflection padding.


**Args:**

- `signal (np.ndarray)`: 1D array of signal values.
- `sigma (float)`: Gaussian standard deviation in frames.


**Returns:**

- `np.ndarray`: Smoothed 1D signal.

##### `main` (Line 739)

```python
def main()
```
CLI entry point for Kopf 2016 Hybrid 3D-2D 360° Video Stabilization.


---

### <a id="stabilize_kabschpy"></a>`scripts/stabilize_kabsch.py`

#### Functions

##### `pixels_to_spheres` (Line 30)

```python
def pixels_to_spheres(pts, W, H)
```
Lifts 2D equirectangular pixel coordinates to 3D unit-sphere directional vectors.

Converts image (x, y) coordinates into right-handed Cartesian directional vectors
where +X is right, +Y is up, and +Z is forward.


**Args:**

- `pts (np.ndarray)`: Array of 2D coordinates of shape (N, 2).
- `W (int)`: Frame width in pixels.
- `H (int)`: Frame height in pixels.


**Returns:**

- `np.ndarray`: Array of 3D unit vectors of shape (N, 3).

##### `kabsch` (Line 55)

```python
def kabsch(A, B)
```
Calculates the optimal 3x3 rotation matrix aligning two sets of 3D vectors via SVD.

Finds orthogonal rotation matrix R minimizing Frobenius norm ||A - B @ R.T||_F.


**Args:**

- `A (np.ndarray)`: Target 3D vectors of shape (N, 3).
- `B (np.ndarray)`: Source 3D vectors of shape (N, 3).


**Returns:**

- `np.ndarray`: 3x3 SO(3) rotation matrix mapping B onto A.

##### `kabsch_ransac` (Line 74)

```python
def kabsch_ransac(A, B, max_iters=100, threshold_deg=1.5)
```
Estimates optimal inter-frame rotation using RANSAC Kabsch SVD.

Rejects parallax and non-rigid rolling-shutter feature outliers.


**Args:**

- `A (np.ndarray)`: Target vectors of shape (N, 3).
- `B (np.ndarray)`: Source vectors of shape (N, 3).
- `max_iters (int, optional)`: Number of RANSAC iterations. Defaults to 100.
- `threshold_deg (float, optional)`: Angular inlier threshold in degrees. Defaults to 1.5.


**Returns:**

- `np.ndarray`: Robust 3x3 rotation matrix.

##### `rot_to_euler` (Line 119)

```python
def rot_to_euler(R)
```
Converts a 3x3 rotation matrix into Euler yaw, pitch, and roll angles in degrees.

Maps curr_frame -> prev_frame using convention R = R_y(yaw) @ R_x(pitch) @ R_z(roll):
- Yaw   (rot around Y=Up)      : +Yaw turns camera right
- Pitch (rot around X=Right)   : +Pitch tilts camera up
- Roll  (rot around Z=Forward) : +Roll rotates camera clockwise


**Args:**

- `R (np.ndarray)`: 3x3 rotation matrix.


**Returns:**

- `tuple[float, float, float]`: Euler angles in degrees as (yaw, pitch, roll).

##### `gaussian_smooth` (Line 145)

```python
def gaussian_smooth(signal, sigma)
```
Applies 1D Gaussian kernel smoothing with edge padding.

Pads boundaries to prevent edge-attenuation artifacts near start and end of trajectory.


**Args:**

- `signal (np.ndarray)`: 1D array of trajectory signal values.
- `sigma (float)`: Gaussian smoothing kernel standard deviation in frames.


**Returns:**

- `np.ndarray`: Convolved smoothed 1D trajectory array.

##### `read_bigsh0t360motion` (Line 169)

```python
def read_bigsh0t360motion(file_path)
```
Parses binary .bigsh0t360motion tracking files from Kdenlive frei0r plugins.


**Args:**

- `file_path (str)`: Filepath to the binary motion file.


**Returns:**

- `list[tuple[float, float, float, float, float]] | None`: List of
(t_start, t_end, yaw, pitch, roll) records, or None on error.

##### `kdenlive_horizon_features_to_track` (Line 200)

```python
def kdenlive_horizon_features_to_track(gray, mask=None, grid_step=64)
```
Samples a regular grid of tracking points across the equatorial horizon band.

Excludes nadir and zenith polar areas to reduce optical distortion errors.


**Args:**

- `gray (np.ndarray)`: Grayscale image frame.
- `mask (np.ndarray, optional)`: Binary mask. Defaults to None.
- `grid_step (int, optional)`: Grid step spacing in pixels. Defaults to 64.


**Returns:**

- `np.ndarray`: Array of feature points of shape (N, 1, 2) in float32.

##### `grid_good_features_to_track` (Line 222)

```python
def grid_good_features_to_track(gray, mask, num_grid_x=12, num_grid_y=4, points_per_cell=20)
```
Detects Shi-Tomasi corners uniformly distributed across a spherical grid.

Divides the equirectangular projection into grid cells to guarantee
omnidirectional feature distribution.


**Args:**

- `gray (np.ndarray)`: Grayscale image frame.
- `mask (np.ndarray)`: Detection validity mask.
- `num_grid_x (int, optional)`: Number of horizontal columns. Defaults to 12.
- `num_grid_y (int, optional)`: Number of vertical rows. Defaults to 4.
- `points_per_cell (int, optional)`: Max points per cell. Defaults to 20.


**Returns:**

- `np.ndarray`: Array of detected feature points of shape (N, 1, 2).

##### `main` (Line 274)

```python
def main()
```
CLI entry point for 360-aware spherical stabilization via Kabsch SVD.


---

### <a id="stabilize_vidstabpy"></a>`scripts/stabilize_vidstab.py`

#### Functions

##### `gaussian_smooth` (Line 34)

```python
def gaussian_smooth(signal, sigma)
```
Apply 1D Gaussian smoothing convolution with reflect padding at boundaries.


**Args:**

- `signal`: 1D numpy array of trajectory or motion values.
- `sigma`: Standard deviation of Gaussian kernel in frames.


**Returns:**

- `np.ndarray`: Smoothed 1D signal of the same length as input.

##### `boxcar_smooth` (Line 52)

```python
def boxcar_smooth(signal, window_size)
```
Apply 1D simple moving average (boxcar) filter with reflect padding.


**Args:**

- `signal`: 1D numpy array of trajectory values.
- `window_size`: Moving average window length in frames.


**Returns:**

- `np.ndarray`: Smoothed 1D signal of the same length as input.

##### `l1_l2_opt_smooth` (Line 72)

```python
def l1_l2_opt_smooth(signal, smoothing_val, max_iters=5, lambda_scale=10.0)
```
Optimize trajectory via iteratively reweighted L1/L2 trend filtering.

Penalizes both 1st difference (velocity) and 2nd difference (acceleration)
using sparse matrix optimization, falling back to Gaussian smoothing if sparse
solver fails.


**Args:**

- `signal`: 1D numpy array of rotation angles.
- `smoothing_val`: Smoothing strength parameter.
- `max_iters`: Number of iteratively reweighted optimization iterations.
- `lambda_scale`: Scaling factor for acceleration penalty.


**Returns:**

- `np.ndarray`: Regularized and smoothed 1D trajectory array.

##### `main` (Line 128)

```python
def main()
```
Execute command-line interface for 360-aware optical feature stabilization.


---

### <a id="stabilize_cinematicpy"></a>`scripts/stabilize_cinematic.py`

#### Functions

##### `cinematic_smooth_trajectory_1d` (Line 21)

```python
def cinematic_smooth_trajectory_1d(values: np.ndarray, lambda_acc: float=20.0, lambda_vel: float=2.0, smooth_window: int=45) -> np.ndarray
```
Optimize a 1D rotation sequence x via L1 regularized objective.

Solves: min_p 0.5 * ||p - x||^2 + lambda_acc * ||D2 p||_1 + lambda_vel * ||D1 p||_1
Approximates L1 norm using pseudo-Huber smoothing for fast Newton/L-BFGS-B convergence.


**Args:**

- `values`: 1D array of rotation angles in degrees.
- `lambda_acc`: Regularization weight penalizing acceleration (promotes smooth pans).
- `lambda_vel`: Regularization weight penalizing velocity (promotes static tripod holds).
- `smooth_window`: Size of moving average window for optimization initialization.


**Returns:**

- `np.ndarray`: Smoothed 1D trajectory array of the same shape as values.

##### `parse_sendcmd_file` (Line 97)

```python
def parse_sendcmd_file(sendcmd_path: str) -> tuple[np.ndarray, np.ndarray, float]
```
Parse an existing FFmpeg sendcmd.txt file into timestamps and Euler angles.


**Args:**

- `sendcmd_path`: Filesystem path to the FFmpeg sendcmd text file.


**Returns:**

- `tuple[np.ndarray, np.ndarray, float]`: A 3-tuple containing:
  - times_arr: 1D array of frame start timestamps in seconds.
  - angles_arr: (N, 3) array of [yaw, pitch, roll] rotation angles in degrees.
  - fps: Inferred frame rate in frames per second.

##### `write_sendcmd_file` (Line 158)

```python
def write_sendcmd_file(output_path: str, times: np.ndarray, angles: np.ndarray, fps: float)
```
Write optimized rotational trajectory into FFmpeg sendcmd format.


**Args:**

- `output_path`: Destination path for the formatted sendcmd file.
- `times`: 1D array of frame start timestamps in seconds.
- `angles`: (N, 3) array of [yaw, pitch, roll] rotation angles in degrees.
- `fps`: Video frame rate in frames per second.

##### `v360_euler_to_matrix` (Line 178)

```python
def v360_euler_to_matrix(yaw_deg: float, pitch_deg: float, roll_deg: float) -> np.ndarray
```
Converts v360 intrinsic Euler rotation angles to a 3x3 rotation matrix.


**Args:**

- `yaw_deg`: Yaw rotation angle in degrees.
- `pitch_deg`: Pitch rotation angle in degrees.
- `roll_deg`: Roll rotation angle in degrees.


**Returns:**

- `np.ndarray`: 3x3 SO(3) orthogonal rotation matrix (float64).

##### `v360_matrix_to_euler` (Line 200)

```python
def v360_matrix_to_euler(R: np.ndarray) -> tuple[float, float, float]
```
Extracts v360 Euler rotation angles from a 3x3 rotation matrix.


**Args:**

- `R`: 3x3 rotation matrix.


**Returns:**

- `tuple[float, float, float]`: Euler angles in degrees as (yaw, pitch, roll).

##### `optimize_3d_trajectory` (Line 221)

```python
def optimize_3d_trajectory(angles: np.ndarray, lambda_acc: float=20.0, lambda_vel: float=2.0, smooth_window: int=45) -> np.ndarray
```
Optimize 3D rotation trajectory using L1-norm regularization.

Unwraps yaw across spherical boundaries, independently optimizes yaw,
pitch, and roll via pseudo-Huber L1 regularization, and re-wraps yaw.


**Args:**

- `angles`: (N, 3) array of [yaw, pitch, roll] rotation angles in degrees.
- `lambda_acc`: Weight parameter penalizing non-constant angular acceleration.
- `lambda_vel`: Weight parameter penalizing non-zero angular velocity.
- `smooth_window`: Window length for moving average initialization.


**Returns:**

- `np.ndarray`: Optimized (N, 3) array of smoothed [yaw, pitch, roll] angles in degrees.

##### `main` (Line 262)

```python
def main()
```
Execute command-line interface for cinematic trajectory smoothing and video rendering.


---

### <a id="stabilize_traveldirpy"></a>`scripts/stabilize_traveldir.py`

#### Functions

##### `extract_standalone_yaw` (Line 27)

```python
def extract_standalone_yaw(video_path: str, default_fps: float=30.0) -> tuple[np.ndarray, np.ndarray, float]
```
Extract camera yaw trajectory from standalone video using telemetry, optical flow, or ffprobe.


**Args:**

- `video_path`: Path to the input video file.
- `default_fps`: Fallback framerate.


**Returns:**

- `tuple[np.ndarray, np.ndarray, float]`: (times, angles, fps)

##### `parse_sendcmd_file` (Line 98)

```python
def parse_sendcmd_file(sendcmd_path: str) -> tuple[np.ndarray, np.ndarray, float]
```
Parse an existing FFmpeg sendcmd.txt file into timestamps and Euler angles.


**Args:**

- `sendcmd_path`: Filesystem path to the FFmpeg sendcmd text file.


**Returns:**

- `tuple[np.ndarray, np.ndarray, float]`: A 3-tuple containing:
  - times_arr: 1D array of frame start timestamps in seconds.
  - angles_arr: (N, 3) array of [yaw, pitch, roll] rotation angles in degrees.
  - fps: Inferred frame rate in frames per second.

##### `write_sendcmd_file` (Line 159)

```python
def write_sendcmd_file(output_path: str, times: np.ndarray, angles: np.ndarray, fps: float)
```
Write directional-lock rotational trajectory into FFmpeg sendcmd format.


**Args:**

- `output_path`: Destination path for the formatted sendcmd file.
- `times`: 1D array of frame start timestamps in seconds.
- `angles`: (N, 3) array of [yaw, pitch, roll] rotation angles in degrees.
- `fps`: Video frame rate in frames per second.

##### `compute_traveldir` (Line 190)

```python
def compute_traveldir(angles: np.ndarray, mode: str='travel_direction', target_yaw: float=0.0, damping: float=0.9, deadband_deg: float=1.5, return_diagnostics: bool=False) -> np.ndarray | tuple[np.ndarray, dict]
```
Apply direction locking and yaw steering to a rotational trajectory.

Adjusts yaw to keep viewing heading aligned with travel direction or target
subject using angular dampening and deadband filters, leaving pitch and roll
intact (zeroed delta) to preserve horizon leveling.


**Args:**

- `angles`: (N, 3) array of [yaw, pitch, roll] rotation angles in degrees.
- `mode`: Steering mode ('travel_direction', 'target_lock', or 'damped_follow').
- `target_yaw`: Target heading yaw in degrees (used in 'target_lock' mode).
- `damping`: Angular smoothing and dampening factor in [0.0, 0.99].
- `deadband_deg`: Angular threshold in degrees below which minor jitters are ignored.
- `return_diagnostics`: If True, returns a tuple of (locked_angles, diag_dict).


**Returns:**

- `np.ndarray | tuple[np.ndarray, dict]`: Array of direction-steering deltas,
or (deltas, diagnostics dictionary) if return_diagnostics is True.

##### `main` (Line 292)

```python
def main()
```
Execute command-line interface for travel-direction lock and video rendering.


---

### <a id="stabilize_horizonpy"></a>`scripts/stabilize_horizon.py`

#### Functions

##### `euler_to_quaternion` (Line 27)

```python
def euler_to_quaternion(yaw_deg, pitch_deg, roll_deg)
```
Converts Euler angles to a normalized unit quaternion [w, x, y, z].

Follows YXZ intrinsic rotation order (Yaw around Y, Pitch around X, Roll around Z).


**Args:**

- `yaw_deg (float)`: Yaw angle in degrees.
- `pitch_deg (float)`: Pitch angle in degrees.
- `roll_deg (float)`: Roll angle in degrees.


**Returns:**

- `np.ndarray`: Unit quaternion [w, x, y, z] as a 1D float64 array.

##### `quaternion_to_euler` (Line 60)

```python
def quaternion_to_euler(q)
```
Converts a unit quaternion [w, x, y, z] to Euler angles in degrees.


**Args:**

- `q (array_like)`: 4-element quaternion [w, x, y, z].


**Returns:**

- `tuple[float, float, float]`: Euler angles in degrees as (yaw, pitch, roll).

##### `quaternion_slerp` (Line 92)

```python
def quaternion_slerp(q0, q1, t)
```
Computes spherical linear interpolation (SLERP) between two unit quaternions.

Picks the shortest geodesic path on the 3-sphere and falls back to normalized
linear interpolation when angles are infinitesimally small.


**Args:**

- `q0 (np.ndarray)`: Start unit quaternion [w, x, y, z].
- `q1 (np.ndarray)`: Target unit quaternion [w, x, y, z].
- `t (float)`: Interpolation factor in the range [0.0, 1.0].


**Returns:**

- `np.ndarray`: Interpolated unit quaternion [w, x, y, z].

##### `generate_slerp_trajectory` (Line 130)

```python
def generate_slerp_trajectory(checkpoints, total_frames, ignore_yaw=True, fps=29.97)
```
Generates continuous per-frame Euler angles [yaw, pitch, roll] using cosine easing.

Interpolates between horizon checkpoints without gimbal flips, matching
the WebGL Horizon Editor preview coordinates.


**Args:**

- `checkpoints (list[dict])`: List of checkpoint dicts with 'frame', 'pitch', 'roll', 'yaw'.
- `total_frames (int)`: Total frame count of the video.
- `ignore_yaw (bool, optional)`: Locks yaw to 0.0 for pure pitch/roll leveling. Defaults to True.
- `fps (float, optional)`: Framerate in frames per second. Defaults to 29.97.


**Returns:**

- `np.ndarray`: Array of shape (total_frames, 3) containing [yaw, pitch, roll] per frame.

##### `get_video_info` (Line 200)

```python
def get_video_info(video_path)
```
Extracts total frame count and framerate from a video using ffprobe.


**Args:**

- `video_path (str)`: Filepath to the video.


**Returns:**

- `tuple[int, float]`: Total frame count and framerate (fps).

##### `write_sendcmd_file` (Line 239)

```python
def write_sendcmd_file(trajectory, fps, output_file)
```
Writes FFmpeg sendcmd script containing incremental Euler angle deltas for the v360 filter.


**Args:**

- `trajectory (np.ndarray)`: Array of shape (N, 3) containing [yaw, pitch, roll] per frame.
- `fps (float)`: Framerate in frames per second.
- `output_file (str)`: Destination path for the generated sendcmd text file.

##### `generate_checkpoints_graph` (Line 276)

```python
def generate_checkpoints_graph(checkpoints, trajectory, fps, output_png)
```
Generates a dark-mode visualization plot of horizon trajectory curves and checkpoints.


**Args:**

- `checkpoints (list[dict])`: List of user/auto checkpoint dictionaries.
- `trajectory (np.ndarray)`: Interpolated Euler trajectory array.
- `fps (float)`: Video framerate.
- `output_png (str)`: Destination image filepath for PNG graph.


**Returns:**

- `bool`: True if graph was generated and saved, False otherwise.

##### `rdp_2d` (Line 520)

```python
def rdp_2d(p_arr, r_arr, f_start, f_end, epsilon)
```
Performs 2D Ramer-Douglas-Peucker curve simplification on pitch/roll trajectories.


**Args:**

- `p_arr (np.ndarray)`: Array of pitch angles.
- `r_arr (np.ndarray)`: Array of roll angles.
- `f_start (int)`: Start index in array.
- `f_end (int)`: End index in array.
- `epsilon (float)`: Maximum perpendicular distance tolerance in degrees.


**Returns:**

- `list[int]`: Keyframe indices retained after simplification.

##### `detect_visual_horizon_tilt` (Line 556)

```python
def detect_visual_horizon_tilt(img_bgr, max_tilt_deg=15.0, optical_target='ground')
```
Detects visual pitch and roll tilt angles from an equirectangular image.

Combines:
1. 3D spherical vertical gravity line projection (OpenCV LSD) on poles, building walls,
straight tree trunks with physical azimuth projection:
alpha_i = -cos(phi_i)*roll_corr + sin(phi_i)*pitch_corr.
2. Water surface detection (omnidirectional 360° horizontal plane).
3. RANSAC sinusoidal sky-ground/boundary edge fitting with full inlier consensus refits
and azimuthal span validation.


**Args:**

- `img_bgr (np.ndarray)`: Equirectangular BGR image frame.
- `max_tilt_deg (float, optional)`: Maximum allowable tilt threshold in degrees. Defaults to 15.0.


**Returns:**

- `tuple[float, float]`: Estimated corrective (pitch, roll) angles in degrees.

##### `auto_detect_from_video` (Line 730)

```python
def auto_detect_from_video(video_path, fps=29.97, sample_interval_sec=1.5, epsilon=1.5, lock_roll=False, optical_target='ground')
```
Extracts visual horizon checkpoints directly from sampled equirectangular video frames.

Applies temporal median filtering and rate-of-change clamping before RDP curve
simplification to eliminate high-frequency see-saw pitch balancing.


**Args:**

- `video_path (str)`: Filepath to the equirectangular video.
- `fps (float, optional)`: Video framerate. Defaults to 29.97.
- `sample_interval_sec (float, optional)`: Sampling interval in seconds. Defaults to 1.5.
- `epsilon (float, optional)`: RDP simplification tolerance in degrees. Defaults to 1.5.
- `lock_roll (bool, optional)`: Whether to lock roll angles to 0.0. Defaults to False.
- `optical_target (str, optional)`: 'ground' (contact line) or 'skyline' (canopy). Defaults to 'ground'.


**Returns:**

- `list[dict]`: Simplified horizon checkpoint dicts with 'frame', 'pitch', 'roll', 'yaw'.

##### `auto_detect_from_telemetry` (Line 828)

```python
def auto_detect_from_telemetry(telemetry_file, fps=29.97, baseline_pitch=0.0, baseline_roll=0.0, epsilon=2.5, neutral_baseline=False)
```
Extracts natural horizon checkpoints from Samsung Gear 360 telemetry IMU logs.


**Args:**

- `telemetry_file (str)`: Path to raw telemetry text file.
- `fps (float, optional)`: Target video framerate. Defaults to 29.97.
- `baseline_pitch (float, optional)`: Static pitch baseline calibration. Defaults to 0.0.
- `baseline_roll (float, optional)`: Static roll baseline calibration. Defaults to 0.0.
- `epsilon (float, optional)`: RDP simplification tolerance. Defaults to 2.5.
- `neutral_baseline (bool, optional)`: Whether to zero out baseline corrections. Defaults to False.


**Returns:**

- `list[dict]`: Checkpoint dicts extracted from telemetry.

##### `parse_raw_telemetry_angles` (Line 842)

```python
def parse_raw_telemetry_angles(telemetry_file)
```
Extracts raw (roll, pitch, yaw) tuples from .gcsv or .txt telemetry file.

##### `auto_detect_from_telemetry` (Line 901)

```python
def auto_detect_from_telemetry(telemetry_file, fps=29.97, baseline_pitch=0.0, baseline_roll=0.0, epsilon=2.5, neutral_baseline=False)
```
Extracts natural horizon checkpoints from Samsung Gear 360 telemetry IMU logs.


**Args:**

- `telemetry_file (str)`: Path to raw telemetry text file.
- `fps (float, optional)`: Target video framerate. Defaults to 29.97.
- `baseline_pitch (float, optional)`: Static pitch baseline calibration. Defaults to 0.0.
- `baseline_roll (float, optional)`: Static roll baseline calibration. Defaults to 0.0.
- `epsilon (float, optional)`: RDP simplification tolerance. Defaults to 2.5.
- `neutral_baseline (bool, optional)`: Whether to zero out baseline corrections. Defaults to False.


**Returns:**

- `list[dict]`: Checkpoint dicts extracted from telemetry.

##### `auto_detect_from_motion` (Line 949)

```python
def auto_detect_from_motion(motion_file, fps=29.97, epsilon=2.5, neutral_baseline=False)
```
Extracts natural horizon checkpoints from .kopf360motion or .kabsch360motion files.


**Args:**

- `motion_file (str)`: Path to motion tracking data file.
- `fps (float, optional)`: Video framerate. Defaults to 29.97.
- `epsilon (float, optional)`: RDP simplification tolerance in degrees. Defaults to 2.5.
- `neutral_baseline (bool, optional)`: Whether to zero out baseline. Defaults to False.


**Returns:**

- `list[dict]`: Checkpoint dicts extracted from motion data.

##### `seed_cadence_checkpoints` (Line 1005)

```python
def seed_cadence_checkpoints(total_frames, fps=29.97, interval_sec=0.5, max_checkpoints=None, adaptive=False, start_frame=0, end_frame=None, initial_angles=(0.0, 0.0, 0.0), telemetry_file=None, motion_file=None, roll_damping=0.7)
```
Generates regular cadence keyframes guarded against explosion on long videos.


**Args:**

- `total_frames (int)`: Total number of frames in the video.
- `fps (float, optional)`: Framerate in frames per second. Defaults to 29.97.
- `interval_sec (float, optional)`: Desired cadence interval in seconds. Defaults to 0.5.
- `max_checkpoints (int | None, optional)`: Optional maximum keyframes budget. Defaults to None (unconstrained).
- `adaptive (bool, optional)`: Whether to dynamically scale interval if max_checkpoints is set. Defaults to False.
- `start_frame (int, optional)`: Starting frame index. Defaults to 0.
- `end_frame (int | None, optional)`: Ending frame index (inclusive). Defaults to None (total_frames - 1).
- `initial_angles (tuple[float, float, float], optional)`: Default (pitch, roll, yaw) values. Defaults to (0.0, 0.0, 0.0).
- `telemetry_file (str | None, optional)`: Path to telemetry file to sample angles from. Defaults to None.
- `motion_file (str | None, optional)`: Path to motion tracker file to sample angles from. Defaults to None.
- `roll_damping (float, optional)`: Lateral body sway damping multiplier (0.0 to 1.0). Defaults to 0.70.


**Returns:**

- `list[dict]`: List of checkpoint dictionaries with 'frame', 'time', 'pitch', 'roll', 'yaw'.

##### `detect_pitch_extrema_checkpoints` (Line 1104)

```python
def detect_pitch_extrema_checkpoints(total_frames, fps=29.97, min_prominence_deg=0.8, max_checkpoints=None, pitch_curve=None, roll_curve=None, telemetry_file=None, motion_file=None, video_file=None, neutral_baseline=False, start_frame=0, end_frame=None, baseline_pitch=0.0, baseline_roll=0.0, roll_damping=0.7)
```
Identifies natural cadence keyframes at vertical pitch turning points (crests and troughs).

Extracts local pitch extrema where d(pitch)/dt = 0 with swing prominence >= min_prominence_deg.
Guarantees that flat camera motion generates zero redundant keyframes, while periodic gait
nodding produces keyframes precisely aligned with stride apices. Applies roll_damping
to filter lateral hip/body sway while preserving genuine horizon leveling.


**Args:**

- `total_frames (int)`: Total number of video frames.
- `fps (float, optional)`: Framerate in frames per second. Defaults to 29.97.
- `min_prominence_deg (float, optional)`: Minimum pitch swing (prominence) in degrees. Defaults to 0.8.
- `max_checkpoints (int | None, optional)`: Optional maximum keyframes budget. Defaults to None (unconstrained).
- `pitch_curve (np.ndarray | list[float] | None, optional)`: Explicit per-frame pitch angles. Defaults to None.
- `roll_curve (np.ndarray | list[float] | None, optional)`: Explicit per-frame roll angles. Defaults to None.
- `telemetry_file (str | None, optional)`: Path to IMU log (.txt / .gcsv). Defaults to None.
- `motion_file (str | None, optional)`: Path to motion tracker file (.kopf360motion). Defaults to None.
- `video_file (str | None, optional)`: Path to video file for visual horizon tracking. Defaults to None.
- `neutral_baseline (bool, optional)`: If True, zeros pitch/roll values. Defaults to False.
- `start_frame (int, optional)`: Range start frame. Defaults to 0.
- `end_frame (int | None, optional)`: Range end frame. Defaults to None (total_frames - 1).
- `baseline_pitch (float, optional)`: Static baseline pitch offset in degrees. Defaults to 0.0 (auto-centers if 0).
- `baseline_roll (float, optional)`: Static baseline roll offset in degrees. Defaults to 0.0 (auto-centers if 0).
- `roll_damping (float, optional)`: Lateral body sway damping multiplier (0.0 to 1.0). Defaults to 0.70.


**Returns:**

- `list[dict]`: Checkpoint dicts with 'frame', 'time', 'pitch', 'roll', 'yaw', and 'type'.

##### `load_checkpoints` (Line 1284)

```python
def load_checkpoints(json_file)
```
Loads checkpoints, ignoreYaw flag, and framerate from a JSON checkpoint file.


**Args:**

- `json_file (str)`: Filepath to horizon_checkpoints.json.


**Returns:**

- `tuple[list[dict], bool, float]`: Checkpoints list, ignore_yaw flag, and framerate.

##### `load_checkpoints_with_metadata` (Line 1303)

```python
def load_checkpoints_with_metadata(json_file)
```
Loads checkpoints, metadata/parameters, ignoreYaw flag, and framerate from a JSON checkpoint file.

##### `format_horizon_params_log` (Line 1316)

```python
def format_horizon_params_log(out_base, data, video_name='')
```
Generate a clean, structured human-readable audit log of horizon stabilization parameters.


**Args:**

- `out_base (str)`: Test or target run base identifier.
- `data (dict)`: Checkpoint dictionary containing checkpoints and parameters/metadata.
- `video_name (str, optional)`: Target video filename or path.


**Returns:**

- `str`: Formatted audit log text.

##### `write_horizon_params_log` (Line 1395)

```python
def write_horizon_params_log(out_base, data, dest_paths, video_name='')
```
Writes the formatted horizon parameters audit log to one or more destination file paths.


**Args:**

- `out_base (str)`: Test or target run base identifier.
- `data (dict)`: Checkpoint dictionary containing checkpoints and parameters/metadata.
- `dest_paths (list[str])`: List of filepaths where the log should be saved.
- `video_name (str, optional)`: Target video filename or path.


**Returns:**

- `list[str]`: Paths successfully written.

##### `main` (Line 1422)

```python
def main()
```
CLI entry point for 360° horizon checkpoint stabilization and leveling.


---

## 5. Calibration, Telemetry & Geospatial Tools

Optical circle auto-calibration, gyro warmup detection, binary telemetry extraction, Street View GPX sync, and trajectory plotting.

### <a id="auto_calibratepy"></a>`scripts/auto_calibrate.py`

#### Functions

##### `build_equirect_thumbnail` (Line 27)

```python
def build_equirect_thumbnail(front_half, rear_half, fov_deg, left_y_offset=0.0, rear_roll_offset=0.0, out_w=512, out_h=256)
```
Render a fast low-resolution stitched equirectangular thumbnail for scene orientation analysis.


**Args:**

- `front_half`: BGR image numpy array of front fisheye half.
- `rear_half`: BGR image numpy array of rear fisheye half.
- `fov_deg`: Assumed diagonal/circular field-of-view in degrees.
- `left_y_offset`: Vertical center shift in pixels for front lens.
- `rear_roll_offset`: Rotational roll adjustment in degrees for rear lens.
- `out_w`: Output thumbnail equirectangular width in pixels.
- `out_h`: Output thumbnail equirectangular height in pixels.


**Returns:**

- `np.ndarray`: Blended BGR equirectangular thumbnail image.

##### `extract_telemetry_orientation` (Line 99)

```python
def extract_telemetry_orientation(video_path)
```
Extract gravity-based pitch and roll angles from accompanying telemetry file.


**Args:**

- `video_path`: Filesystem path to the input video file.


**Returns:**

- `tuple[float | None, float | None]`: Tuple of (pitch_deg, roll_deg), or (None, None)
if no valid accelerometer telemetry is detected.

##### `estimate_heading_yaw` (Line 152)

```python
def estimate_heading_yaw(frame_pairs)
```
Estimate dominant forward heading yaw offset from multi-frame motion expansion.

Excludes the nadir region to avoid bias from mounting hardware or tripods.


**Args:**

- `frame_pairs`: List of (front_half, rear_half) image tuple pairs.


**Returns:**

- `float`: Estimated yaw rotation correction in degrees.

##### `detect_water_surface_horizon` (Line 182)

```python
def detect_water_surface_horizon(equirect_bgr)
```
Detect horizontal water bodies and fit physical horizon sinusoid.


**Args:**

- `equirect_bgr`: BGR equirectangular panorama image array.


**Returns:**

- `tuple[float | None, float | None, float]`: Tuple of (pitch_deg, roll_deg,
confidence_score), or (None, None, 0.0) if no water body is detected.

##### `filter_tree_and_pole_lines` (Line 253)

```python
def filter_tree_and_pole_lines(equirect_bgr, lines_flat)
```
Extract and weight structural vertical line segments from detected lines.

Filters and weights lines corresponding to poles, tree trunks, and pillars
while excluding zenith and nadir regions.


**Args:**

- `equirect_bgr`: BGR equirectangular image array.
- `lines_flat`: (N, 4) array of line endpoints [x1, y1, x2, y2].


**Returns:**

- `list[tuple[tuple[float, float, float, float], float]]`: List of ((x1, y1, x2, y2), weight).

##### `estimate_rotation_and_horizon` (Line 303)

```python
def estimate_rotation_and_horizon(frame_pairs, fov_deg=190.0, left_y_offset=0.0, rear_roll_offset=0.0, video_path=None)
```
Estimate corrective Pitch, Roll, and Yaw angles matching FFmpeg v360 conventions.

Fuses visual sky/ground horizon elevation, pole verticality consensus,
water surface horizon, and telemetry gravity.


**Args:**

- `frame_pairs`: List of (front_half, rear_half) image pairs.
- `fov_deg`: Field of view in degrees.
- `left_y_offset`: Vertical offset for left lens.
- `rear_roll_offset`: Rear lens roll offset in degrees.
- `video_path`: Optional path to source video for companion telemetry inspection.


**Returns:**

- `tuple[float, float, float]`: Corrective (yaw, pitch, roll) angles in degrees.

##### `evaluate_alignment` (Line 462)

```python
def evaluate_alignment(front_half, rear_half, ih_fov, iv_fov, left_y_offset, rear_roll_offset, detector, matcher, mode='balanced')
```
Compute seam alignment disparity score between front and rear lenses.


**Args:**

- `front_half`: Front fisheye half image array.
- `rear_half`: Rear fisheye half image array.
- `ih_fov`: Horizontal / front lens FOV in degrees.
- `iv_fov`: Vertical / rear lens FOV in degrees.
- `left_y_offset`: Front lens vertical pixel shift.
- `rear_roll_offset`: Rear lens roll adjustment in degrees.
- `detector`: OpenCV 2D feature detector (e.g. ORB).
- `matcher`: OpenCV descriptor matcher (e.g. BFMatcher).
- `mode`: Feature selection mode ('foreground', 'balanced', 'infinity').


**Returns:**

- `tuple[float, int]`: Mean disparity error across both seams and total matches found.

##### `extract_strip_features` (Line 522)

```python
def extract_strip_features(img_half, fov_deg, yaw_center_deg, y_offset, roll_deg, row_start, row_end, detector)
```
Warp seam strip to equirectangular perspective and extract keypoint descriptors.


**Args:**

- `img_half`: Fisheye half image array.
- `fov_deg`: Lens FOV in degrees.
- `yaw_center_deg`: Seam center azimuth in degrees (+90 or -90).
- `y_offset`: Vertical lens shift in pixels.
- `roll_deg`: Roll rotation in degrees.
- `row_start`: Starting row index for feature extraction ROI.
- `row_end`: Ending row index for feature extraction ROI.
- `detector`: OpenCV feature detector.


**Returns:**

- `tuple[list[cv2.KeyPoint], np.ndarray | None]`: Extracted keypoints and descriptor matrix.

##### `match_seam_descriptors` (Line 546)

```python
def match_seam_descriptors(kp_f, des_f, kp_r, des_r, matcher, ground_boost=1.2, pivot_y=180.0)
```
Calculate disparity matching score between front and rear seam descriptors.


**Args:**

- `kp_f`: Front seam keypoints.
- `des_f`: Front seam descriptor array.
- `kp_r`: Rear seam keypoints.
- `des_r`: Rear seam descriptor array.
- `matcher`: OpenCV descriptor matcher.
- `ground_boost`: Multiplier weight favoring lower ground features.
- `pivot_y`: Y coordinate threshold for ground feature weighting.


**Returns:**

- `tuple[float | None, int]`: Median disparity error (or None if insufficient matches)
and count of top matches.

##### `extract_video_calibration_frames` (Line 580)

```python
def extract_video_calibration_frames(video_path, preview_time=None, num_frames=5, out_dir='data/runtime/temp/calib_frames')
```
Extract representative sample frames across video duration for calibration.


**Args:**

- `video_path`: Filesystem path to the input dual-fisheye video.
- `preview_time`: Optional playhead timestamp in seconds to prioritize.
- `num_frames`: Total number of frames to extract across the video timeline.
- `out_dir`: Directory where extracted JPEG frames are stored.


**Returns:**

- `list[str]`: Sorted list of paths to extracted image frame files.

##### `calibrate_frames_dataset` (Line 652)

```python
def calibrate_frames_dataset(frame_pairs, mode='balanced', video_path=None, auto_level_only=False, init_fov=190.0, init_y_offset=0.0, init_rear_roll=0.0)
```
Execute multi-frame consensus calibration optimizer and horizon evaluator.


**Args:**

- `frame_pairs`: List of (front_half, rear_half) image tuple pairs.
- `mode`: Calibration weighting mode ('foreground', 'balanced', 'infinity').
- `video_path`: Optional source video path for telemetry orientation fusion.
- `auto_level_only`: When True, skips optical seam optimization and only levels horizon.
- `init_fov`: Initial FOV estimate in degrees.
- `init_y_offset`: Initial front lens vertical shift in pixels.
- `init_rear_roll`: Initial rear lens roll adjustment in degrees.


**Returns:**

- `dict`: Calibration result dictionary containing FOVs, offsets, horizon corrections,
confidence rating, and execution status.

##### `calibrate_image` (Line 897)

```python
def calibrate_image(img_path, mode='balanced', auto_level_only=False, init_fov=190.0, init_y_offset=0.0, init_rear_roll=0.0)
```
Calibrate stitching parameters from a single dual-fisheye image file.


**Args:**

- `img_path`: Filesystem path to the dual-fisheye image.
- `mode`: Calibration weighting mode ('foreground', 'balanced', 'infinity').
- `auto_level_only`: When True, performs quick horizon leveling only.
- `init_fov`: Initial FOV estimate in degrees.
- `init_y_offset`: Initial left lens vertical shift in pixels.
- `init_rear_roll`: Initial rear lens roll adjustment in degrees.


**Returns:**

- `dict`: Calibration result dictionary.

##### `calibrate_video` (Line 924)

```python
def calibrate_video(video_path, preview_time=None, num_frames=5, mode='balanced', auto_level_only=False, init_fov=190.0, init_y_offset=0.0, init_rear_roll=0.0)
```
Extract representative frames and perform joint multi-frame auto-calibration.


**Args:**

- `video_path`: Filesystem path to the input dual-fisheye video.
- `preview_time`: Optional playhead preview timestamp in seconds.
- `num_frames`: Number of sample frames to evaluate.
- `mode`: Calibration weighting mode ('foreground', 'balanced', 'infinity').
- `auto_level_only`: When True, performs quick horizon leveling only.
- `init_fov`: Initial FOV estimate in degrees.
- `init_y_offset`: Initial left lens vertical shift in pixels.
- `init_rear_roll`: Initial rear lens roll adjustment in degrees.


**Returns:**

- `dict`: Calibration result dictionary.

##### `main` (Line 963)

```python
def main()
```
Execute command-line interface for dual-fisheye auto-calibration and leveling.


---

### <a id="detect_warmuppy"></a>`scripts/detect_warmup.py`

#### Functions

##### `_angle_distance` (Line 26)

```python
def _angle_distance(r1, r2)
```
Compute angular distance between two (roll, pitch, yaw) orientation tuples in degrees.


**Args:**

- `r1`: First (roll, pitch, yaw) sequence in degrees.
- `r2`: Second (roll, pitch, yaw) sequence in degrees.


**Returns:**

- `float`: Angular Euclidean distance accounting for circular angle wrapping.

##### `inspect_telemetry_warmup` (Line 41)

```python
def inspect_telemetry_warmup(video_path, fps, max_inspect_sec=12.0)
```
Detects initial sensor boot anomalies in vrot IMU orientation data.

Checks for uninitialized zero telemetry vectors (0, 0, 0), static
frozen telemetry frames, and discrete hardware calibration step transitions
occurring in the initial boot window.


**Args:**

- `video_path (str)`: Filepath to the video file.
- `fps (float)`: Video framerate in frames per second.
- `max_inspect_sec (float, optional)`: Maximum duration to inspect in seconds. Defaults to 12.0.


**Returns:**

- `dict | None`: Dictionary with detected start_frame, reason, and type, or None.

##### `inspect_visual_warmup` (Line 166)

```python
def inspect_visual_warmup(video_path, fps, max_inspect_sec=4.0)
```
Detects optical sensor startup anomalies using OpenCV frame inspection.

Identifies completely black sensor initialization frames and frozen startup
frames by monitoring inter-frame luminance and pixel differences.


**Args:**

- `video_path (str)`: Filepath to the video file.
- `fps (float)`: Video framerate.
- `max_inspect_sec (float, optional)`: Inspection window limit in seconds. Defaults to 4.0.


**Returns:**

- `dict | None`: Dictionary with detected start_frame, reason, and type, or None.

##### `inspect_warmup` (Line 239)

```python
def inspect_warmup(input_path, max_inspect_sec=12.0)
```
Orchestrates combined telemetry and optical sensor warm-up detection.


**Args:**

- `input_path (str)`: Path to input video.
- `max_inspect_sec (float, optional)`: Maximum window to inspect in seconds. Defaults to 12.0.


**Returns:**

- `dict`: Standardized result dictionary containing status, has_warmup flag,
start_frame, start_time, and detection reasoning.

##### `main` (Line 311)

```python
def main()
```
CLI entry point for video sensor warm-up detection.


---

### <a id="extract_telemetrypy"></a>`scripts/extract_telemetry.py`

#### Functions

##### `euler_to_quaternion` (Line 28)

```python
def euler_to_quaternion(roll_deg, pitch_deg, yaw_deg)
```
Convert Euler angles (roll, pitch, yaw) in degrees to a unit quaternion.


**Args:**

- `roll_deg`: Roll angle in degrees.
- `pitch_deg`: Pitch angle in degrees.
- `yaw_deg`: Yaw angle in degrees.


**Returns:**

- `tuple[float, float, float, float]`: Unit quaternion components (w, x, y, z).

##### `quaternion_to_euler` (Line 56)

```python
def quaternion_to_euler(w, x, y, z)
```
Convert unit quaternion to Euler angles in degrees.


**Args:**

- `w`: Quaternion scalar (real) component.
- `x`: Quaternion vector x component.
- `y`: Quaternion vector y component.
- `z`: Quaternion vector z component.


**Returns:**

- `tuple[float, float, float]`: Euler angles (roll, pitch, yaw) in degrees.

##### `parse_gpmf_stream` (Line 80)

```python
def parse_gpmf_stream(video_path, fps=30.0)
```
Extract and decode GoPro GPMF metadata stream from video container.

Extracts CORI/IORI orientation quaternion payloads via FFmpeg raw stream mapping.


**Args:**

- `video_path`: Filesystem path to the GoPro video file.
- `fps`: Inferred video frame rate in frames per second.


**Returns:**

- `list[tuple[float, float, float]] | None`: List of (roll, pitch, yaw) tuples
in degrees, or None if no GPMF orientation stream is found.

##### `parse_camm_stream` (Line 134)

```python
def parse_camm_stream(video_path, fps=30.0)
```
Extract and decode Google / Ricoh / Android CAMM metadata stream.

Parses binary camera motion records for quaternion orientations (msg_type 4).


**Args:**

- `video_path`: Filesystem path to the video file.
- `fps`: Inferred video frame rate in frames per second.


**Returns:**

- `list[tuple[float, float, float]] | None`: List of (roll, pitch, yaw) tuples
in degrees, or None if no CAMM stream is found.

##### `parse_gyroflow_gcsv` (Line 180)

```python
def parse_gyroflow_gcsv(gcsv_path)
```
Parse Gyroflow .gcsv IMU log file and integrate gyro rates into Euler angles.


**Args:**

- `gcsv_path`: Filesystem path to the .gcsv log file.


**Returns:**

- `list[tuple[float, float, float]] | None`: List of integrated (roll, pitch, yaw)
tuples in degrees, or None if parsing fails.

##### `parse_companion_file` (Line 243)

```python
def parse_companion_file(file_path)
```
Parse an explicit companion telemetry file (.gcsv or Euler CSV/TXT).


**Args:**

- `file_path`: Filesystem path to the companion log file.


**Returns:**

- `tuple[list[tuple[float, float, float]] | None, str | None]`: Tuple of
(rot_data, resolved_file_path), or (None, None) if parsing fails.

##### `resolve_companion_candidates` (Line 298)

```python
def resolve_companion_candidates(input_video, source_hint='auto')
```
Finds candidate companion telemetry files for a given input video and source hint.


**Args:**

- `input_video (str)`: Path to input video file.
- `source_hint (str)`: Source type hint ('auto', 'gyroflow', 'witmotion', 'custom_csv').


**Returns:**

- `list[str]`: Candidate file paths in prioritized search order.

##### `write_standard_telemetry_file` (Line 358)

```python
def write_standard_telemetry_file(output_file, rot_data, fps=30.0)
```
Write standardized 9-column WitMotion / Samsung Gear 360 telemetry file.


**Args:**

- `output_file`: Destination path for formatted telemetry text file.
- `rot_data`: List of (roll, pitch, yaw) rotation tuples in degrees.
- `fps`: Video frame rate for synthetic timestamp generation.

##### `main` (Line 387)

```python
def main()
```
Execute command-line interface for multi-format video telemetry extraction.


---

### <a id="convert_telemetrypy"></a>`scripts/convert_telemetry.py`

#### Functions

##### `convert_telemetry` (Line 15)

```python
def convert_telemetry(input_path, target_rate=200.0)
```
Converts camera orientation angles into 200 Hz Gyroflow GCSV logs.

Differentiates Euler angles into angular velocity rates, computes normalized
gravity vectors, and linearly interpolates telemetry to target_rate. Outputs
both `<base>.gcsv` and `<base>.MP4.gcsv` sidecars.


**Args:**

- `input_path (str)`: Path to raw tab/comma-separated telemetry text file.
- `target_rate (float, optional)`: Target sample rate in Hz. Defaults to 200.0.


**Returns:**

- `bool`: True if conversion and file writes succeeded, False otherwise.


---

### <a id="streetview_gpxpy"></a>`scripts/streetview_gpx.py`

#### Functions

##### `parse_iso_or_utc` (Line 25)

```python
def parse_iso_or_utc(ts_str)
```
Parse ISO-8601 or UTC datetime string into timezone-aware datetime.


**Args:**

- `ts_str`: Datetime representation as string or datetime object.


**Returns:**

Timezone-aware UTC datetime object, or None if parsing fails.

##### `get_video_info` (Line 63)

```python
def get_video_info(video_path)
```
Probe video stream metrics and creation timestamp using ffprobe.


**Args:**

- `video_path`: Filepath to video file.


**Returns:**

Tuple of (duration_sec, width, height, fps, creation_time_dt).

##### `extract_video_creation_time_utc` (Line 109)

```python
def extract_video_creation_time_utc(video_path)
```
Extract creation_time UTC datetime from video metadata container.


**Args:**

- `video_path`: Filepath to video file.


**Returns:**

Timezone-aware UTC datetime of video creation, or file modification time fallback.

##### `parse_time_str` (Line 121)

```python
def parse_time_str(val_str)
```
Parse time string into float seconds.

Supports 'HH:MM:SS', 'MM:SS', or raw seconds numbers.


**Args:**

- `val_str`: Time string or numeric representation in seconds.


**Returns:**

Float timestamp in seconds, or None if parsing fails.

##### `clean_num_val` (Line 151)

```python
def clean_num_val(s)
```
Clean numeric string and strip trailing meter/zoom suffixes (e.g. '208m' -> 208.0).


**Args:**

- `s`: String or numeric representation of elevation or coordinate.


**Returns:**

Float numeric value, or None if conversion fails.

##### `parse_checkpoint_list` (Line 170)

```python
def parse_checkpoint_list(checkpoints_input)
```
Parse checkpoint input into structured coordinate dictionaries.

Supports lists of dicts/tuples, JSON files/strings, Google Maps URLs
(including '@lat,lon' and '!3d/!4d' pin markers), and comma-separated text lines.


**Args:**

- `checkpoints_input`: List, JSON string, filepath, or plain text containing checkpoints.


**Returns:**

List of dictionaries with keys 'lat', 'lon', and optional 'time', 'ele'.

##### `interpolate_checkpoints` (Line 310)

```python
def interpolate_checkpoints(checkpoints, total_duration_sec)
```
Interpolate checkpoint coordinates and elevations to generate 1 point per second.


**Args:**

- `checkpoints`: Sequence of checkpoint dictionaries with 'lat', 'lon', and optional 'time', 'ele'.
- `total_duration_sec`: Total duration of the track in integer seconds.


**Returns:**

- `List of dictionaries containing {'lat'`: float, 'lon': float, 'ele': float, 'sec': int}.

##### `parse_gpx_file` (Line 402)

```python
def parse_gpx_file(gpx_file_path)
```
Parse input GPX file and extract track points with timestamps and elevation.


**Args:**

- `gpx_file_path`: Filepath to GPX XML file.


**Returns:**

List of dictionaries containing 'lat', 'lon', 'ele', 'dt', and 'time_str'.

##### `haversine_distance` (Line 429)

```python
def haversine_distance(lat1, lon1, lat2, lon2)
```
Compute great-circle geodesic distance between two coordinate pairs in meters.


**Args:**

- `lat1`: Latitude of first point in decimal degrees.
- `lon1`: Longitude of first point in decimal degrees.
- `lat2`: Latitude of second point in decimal degrees.
- `lon2`: Longitude of second point in decimal degrees.


**Returns:**

Distance between points in meters using the Haversine formula.

##### `parse_coord_str` (Line 448)

```python
def parse_coord_str(coord_str)
```
Parse coordinate string into latitude and longitude tuple.


**Args:**

- `coord_str`: Coordinate string formatted as 'lat, lon', 'lat lon', or sequence.


**Returns:**

Tuple of (latitude, longitude) as floats, or None if parsing fails.

##### `find_closest_gpx_point` (Line 475)

```python
def find_closest_gpx_point(raw_gpx_points, target_lat, target_lon)
```
Find closest trackpoint to target coordinate pair.


**Args:**

- `raw_gpx_points`: Sequence of trackpoint dictionaries with 'lat' and 'lon'.
- `target_lat`: Target latitude in decimal degrees.
- `target_lon`: Target longitude in decimal degrees.


**Returns:**

Tuple of (closest_point_dict, point_index, min_dist_meters).

##### `sync_gpx_by_timestamps` (Line 499)

```python
def sync_gpx_by_timestamps(raw_gpx_points, video_start_dt, total_seconds)
```
Slice and interpolate GPX track points against a video's temporal playback window.


**Args:**

- `raw_gpx_points`: List of raw GPX trackpoint dictionaries with UTC datetime 'dt'.
- `video_start_dt`: Timezone-aware UTC datetime of the video start.
- `total_seconds`: Duration of the video in seconds.


**Returns:**

- `List of interpolated 1 Hz dictionaries {'lat'`: float, 'lon': float, 'ele': float, 'sec': int}.

##### `smooth_track_points` (Line 577)

```python
def smooth_track_points(track_points, iterations=2)
```
Apply weighted Gaussian smoothing (0.25, 0.50, 0.25) to reduce GPS sensor noise.

Preserves exact boundary coordinates and 1 Hz temporal spacing.


**Args:**

- `track_points`: Sequence of track point dictionaries.
- `iterations`: Number of smoothing passes to apply.


**Returns:**

New list of smoothed track point dictionaries.

##### `build_gpx_xml` (Line 617)

```python
def build_gpx_xml(track_points, start_utc_dt)
```
Construct formatted GPX 1.1 XML string from 1 Hz track points.


**Args:**

- `track_points`: Sequence of track point dictionaries with 'lat', 'lon', 'ele', and 'sec'.
- `start_utc_dt`: Timezone-aware UTC start datetime.


**Returns:**

Formatted XML string representing GPX track.

##### `build_map_html` (Line 657)

```python
def build_map_html(track_points, start_utc_dt, output_html_path, title_name='Google Street View Route', title=None)
```
Generate standalone interactive Leaflet HTML map visualizing the Street View route.


**Args:**

- `track_points`: Sequence of track point dictionaries.
- `start_utc_dt`: UTC start timestamp.
- `output_html_path`: Destination path for generated HTML map file.
- `title_name`: Heading label displayed above the interactive map.
- `title`: Optional alias for title_name.


**Returns:**

Filepath to the saved HTML map.

##### `update_status` (Line 864)

```python
def update_status(status_file, data)
```
Write progress dictionary atomically to status JSON file.


**Args:**

- `status_file`: Destination JSON file path.
- `data`: Progress dictionary to serialize.

##### `process_streetview` (Line 900)

```python
def process_streetview(input_video, output_video, output_gpx, mode='A', checkpoints=None, gpx_input_file=None, start_time_iso=None, time_offset_sec=0.0, auto_pad=True, additional_videos=None, output_map_html=None, bitrate='45M', strip_audio=True, start_coord=None, end_coord=None, smooth_gps=False, status_file=None, set_completed=True, start_ts=None)
```
Process 360 video and GPS data to create Street View compliant video and GPX track.

Concatenates multi-video segments, checks duration against Google Street View's
2-minute minimum requirement (padding if necessary), injects equirectangular 360
spatial metadata and creation timestamp, encodes to target bitrate, generates
synchronized GPX 1.1 track file with 1 Hz cadence, and builds interactive HTML map.


**Args:**

- `input_video`: Primary input video path or sequence of video paths.
- `output_video`: Destination path for Street View compliant MP4.
- `output_gpx`: Destination path for synchronized GPX track.
- `mode`: GPS input mode ('A' for checkpoints, 'B' for GPX file).
- `checkpoints`: List or text of map checkpoints (Mode A).
- `gpx_input_file`: Source GPX track file path (Mode B).
- `start_time_iso`: Optional ISO-8601 start timestamp override.
- `time_offset_sec`: Time synchronization offset in seconds.
- `auto_pad`: Whether to pad videos under 120s up to 126s.
- `additional_videos`: Optional extra video segments to concatenate.
- `output_map_html`: Optional path for interactive route preview HTML map.
- `bitrate`: Target video bitrate for FFmpeg re-encoding.
- `strip_audio`: Whether to remove audio stream from Street View output.
- `start_coord`: Optional coordinate string to find start point in GPX.
- `end_coord`: Optional coordinate string to find end point in GPX.
- `smooth_gps`: Whether to apply Gaussian filter to GPS coordinates.
- `status_file`: Optional path to status JSON file for tracking.
- `set_completed`: Whether to mark status as completed when finished.
- `start_ts`: Pipeline start timestamp for elapsed time calculation.


**Returns:**

Tuple of (output_video_path, output_gpx_path, output_map_html_path).

##### `run_self_test` (Line 1340)

```python
def run_self_test()
```
Run internal test suite verifying checkpoint interpolation, elevation, and GPX sync.


---

### <a id="visualize_correctionspy"></a>`scripts/visualize_corrections.py`

#### Functions

##### `parse_sendcmd` (Line 24)

```python
def parse_sendcmd(filepath)
```
Parse an FFmpeg sendcmd parameter command file.

Extracts frame timestamp intervals and rotation angles (yaw, pitch, roll).


**Args:**

- `filepath`: Filepath to sendcmd text file.


**Returns:**

List of tuples formatted as (start_time_sec, param_name, param_value).

##### `group_by_frame` (Line 62)

```python
def group_by_frame(entries)
```
Group parsed sendcmd entries by start timestamp into orientation parameter maps.


**Args:**

- `entries`: Sequence of (start_time_sec, param_name, param_value) tuples.


**Returns:**

Tuple of (sorted_timestamps_list, parameter_map_by_time).

##### `generate_graph` (Line 84)

```python
def generate_graph(times_l, by_time_l, times_r, by_time_r, output_png, fps=29.97, fusion_method='none', fusion_gain=0.5)
```
Generate dual-axis diagnostic plot of telemetry pitch and roll stabilization curves.

Visualizes raw camera tilt, applied counter-rotation, and final stabilized
horizon output across playback duration and frame indices.


**Args:**

- `times_l`: Sequence of frame timestamps for left/front lens.
- `by_time_l`: Mapping of left timestamps to parameter values.
- `times_r`: Sequence of frame timestamps for right/rear lens.
- `by_time_r`: Mapping of right timestamps to parameter values.
- `output_png`: Target destination path for PNG chart.
- `fps`: Playback frame rate for frame number secondary axis.
- `fusion_method`: 6-Axis IMU sensor fusion filter used ('ekf', 'mahony', 'complementary', 'none').
- `fusion_gain`: Tuning gain or filter parameter for sensor fusion.


**Returns:**

True if graph was successfully created and saved, False otherwise.

##### `sec_to_ass` (Line 268)

```python
def sec_to_ass(sec)
```
Convert float seconds to Advanced SubStation Alpha timestamp format (H:MM:SS.cs).


**Args:**

- `sec`: Timestamp in fractional seconds.


**Returns:**

Formatted ASS time string.

##### `generate_ass` (Line 286)

```python
def generate_ass(times, by_time, output_ass, fps=29.97, label='Left')
```
Write an ASS subtitle file displaying real-time pitch, roll, and yaw angles per frame.


**Args:**

- `times`: Sequence of frame start timestamps in seconds.
- `by_time`: Mapping of timestamps to orientation parameters.
- `output_ass`: Destination filepath for generated ASS subtitle file.
- `fps`: Playback frame rate used for frame duration calculation.
- `label`: Lens or stage identifier displayed in the overlay.


**Returns:**

True upon successful file generation.

##### `print_stats` (Line 327)

```python
def print_stats(label, times, by_time)
```
Print statistical min/max/mean pitch and roll angles to terminal stdout.


**Args:**

- `label`: Descriptive label for the sensor stream (e.g., 'Left' or 'Right').
- `times`: Sequence of frame timestamps.
- `by_time`: Mapping of timestamps to parameter dictionaries.

##### `parse_trf` (Line 345)

```python
def parse_trf(filepath)
```
Parse VidStab TRF transforms or equirectangular sendcmd trajectory files.


**Args:**

- `filepath`: Path to transform or sendcmd text file.


**Returns:**

- `Tuple of lists`: (frame_indices, delta_yaw, delta_pitch, delta_roll).

##### `generate_optical_graph` (Line 421)

```python
def generate_optical_graph(frames, dx, dy, da, output_png, fps=29.97, model_name='Optical (VidSTAB)')
```
Generate dual-axis diagnostic chart for optical stabilization transforms.

Plots instantaneous frame-to-frame shifts and cumulative trajectory drift
curves for yaw, pitch, and roll axes.


**Args:**

- `frames`: Sequence of integer frame indices.
- `dx`: Horizontal / yaw shift values.
- `dy`: Vertical / pitch shift values.
- `da`: In-plane roll rotation angles.
- `output_png`: Target destination path for PNG chart.
- `fps`: Playback frame rate for time conversion secondary axis.
- `model_name`: Heading label identifying the stabilization algorithm.


**Returns:**

True if graph was successfully created and saved, False otherwise.

##### `generate_traveldir_graph` (Line 551)

```python
def generate_traveldir_graph(times, raw_yaw, trend_yaw, locked_yaw, deadband_deg=1.5, damping=0.9, mode='travel_direction', output_png='traveldir_graph.png', fps=29.97)
```
Generate dual-panel diagnostics chart for travel-direction lock stabilization.

Plots unwrapped heading trajectories (raw, travel trend, and locked viewer heading)
with deadband envelope in top panel, and pan angular velocities with drift in bottom panel.


**Args:**

- `times`: Sequence of frame timestamps in seconds.
- `raw_yaw`: Raw camera heading angles in degrees.
- `trend_yaw`: Estimated forward travel direction trend in degrees.
- `locked_yaw`: Final stabilized viewer heading angles in degrees.
- `deadband_deg`: Angular deadband threshold in degrees.
- `damping`: Damping factor used in steering.
- `mode`: Steering mode string.
- `output_png`: Target destination path for PNG chart.
- `fps`: Video playback framerate.


**Returns:**

True if graph was successfully created and saved, False otherwise.

##### `main` (Line 706)

```python
def main()
```
CLI entry point for stabilization trajectory visualization and overlay generation.


---

### <a id="visual_odometrypy"></a>`scripts/visual_odometry.py`

#### Functions

##### `format_sec_to_hhmmss` (Line 20)

```python
def format_sec_to_hhmmss(sec: float) -> str
```
Format elapsed seconds into HH:MM:SS timestamp string.


**Args:**

- `sec`: Elapsed duration in seconds.


**Returns:**

- `str`: Formatted timestamp string (e.g. '00:01:23').

##### `extract_video_trajectory` (Line 35)

```python
def extract_video_trajectory(video_path: str, start_lat: float, start_lon: float, initial_heading_deg: float=0.0, walking_speed_mps: float=1.15, checkpoint_interval_sec: int=10, start_ele: float=315.0)
```
Extract 360 visual odometry trajectory and generate WGS 84 checkpoints.

Performs dense Farneback optical flow on equatorial equirectangular video bands,
integrates relative yaw shifts, models forward dead-reckoning motion at constant
walking velocity, and outputs geographic coordinates and curve checkpoints.


**Args:**

- `video_path`: Filesystem path to the input 360 video file.
- `start_lat`: Initial starting latitude in decimal degrees.
- `start_lon`: Initial starting longitude in decimal degrees.
- `initial_heading_deg`: Initial camera compass heading in degrees (0 = North).
- `walking_speed_mps`: Assumed walking velocity in meters per second.
- `checkpoint_interval_sec`: Interval in seconds between periodic checkpoints.
- `start_ele`: Starting elevation in meters above sea level.


**Returns:**

- `dict`: Trajectory metadata dictionary containing:
  - 'checkpoints': List of sampled waypoint dictionaries.
  - 'all_points': Full second-by-second geographic coordinate list.
  - 'total_distance_m': Cumulative path distance in meters.
  - 'net_displacement_m': Straight-line distance from start in meters.
  - 'duration_sec': Total duration analyzed in seconds.
  - 'points_count': Number of retained checkpoint waypoints.


**Raises:**

- `FileNotFoundError`: If video_path does not exist on disk.
- `ValueError`: If video stream is shorter than 2 seconds.


---

## 6. Remapping, Nadir & Video Operations

Dual-fisheye equirectangular warping, alpha mask generation, polar nadir overlay, video cropping, and spatial media injection.

### <a id="generate_alpha_maskpy"></a>`scripts/generate_alpha_mask.py`

#### Functions

##### `main` (Line 8)

```python
def main()
```
Generate a horizontal sinusoidal alpha blend mask for dual-fisheye equirectangular stitching.


---

### <a id="crop_camera_videopy"></a>`scripts/crop_camera_video.py`

#### Functions

##### `get_actual_start_frame` (Line 25)

```python
def get_actual_start_frame(input_path, requested_start_time, fps)
```
Calculates keyframe-aligned start frame index for lossless video cropping.

Probes GOP keyframe boundaries around requested start time using ffprobe
to ensure stream copy trims begin cleanly on an IDR/keyframe.


**Args:**

- `input_path (str)`: Filepath to the source video.
- `requested_start_time (float)`: Requested start time in seconds.
- `fps (float)`: Video framerate.


**Returns:**

- `int`: Best keyframe-aligned start frame index.

##### `crop_udta` (Line 67)

```python
def crop_udta(udta_bytes, start_frame, end_frame)
```
Slices binary udta box children, truncating the vrot atom frame samples.

Extracts 24-byte per-frame vrot gyro orientation samples within [start_frame, end_frame]
and repacks the udta box container.


**Args:**

- `udta_bytes (bytes | bytearray)`: Raw bytes of the source udta MP4 atom.
- `start_frame (int)`: Starting frame index (inclusive).
- `end_frame (int)`: Ending frame index (exclusive, or -1 for end of video).


**Returns:**

- `bytes`: Repacked binary udta box containing sliced telemetry.

##### `patch_file` (Line 122)

```python
def patch_file(src_original, dst_converted, out_name, start_frame, end_frame)
```
Injects cropped source udta/vrot telemetry into a trimmed destination MP4 container.

Reconstructs the moov atom header with updated byte sizes, embedding
frame-synchronized telemetry into the output file.


**Args:**

- `src_original (str)`: Source video containing original telemetry atoms.
- `dst_converted (str)`: Trimmed intermediate video lacking telemetry.
- `out_name (str)`: Destination path for final patched MP4 file.
- `start_frame (int)`: Start frame index used during video trimming.
- `end_frame (int)`: End frame index used during video trimming.


**Returns:**

- `bool`: True if patching and file output succeeded, False otherwise.

##### `main` (Line 213)

```python
def main()
```
CLI entry point for video cropping with synchronized telemetry preservation.


---

## 7. Utilities & Low-Level Helpers

MP4 ISO-BMFF binary atom slicer, telemetry math, process orchestration, and geometric projection utilities.

### <a id="mp4_utilspy"></a>`utils/mp4_utils.py`

#### Functions

##### `get_video_properties` (Line 13)

```python
def get_video_properties(input_path: str)
```
Retrieve video stream properties via ffprobe.

Extracts dimensions, frame rate, duration, frame count, and telemetry
data streams (e.g., GPMD, CAMM) from the container.


**Args:**

- `input_path`: Filesystem path to the input video file.


**Returns:**

- `dict | None`: Dictionary containing video properties ('width', 'height',
'fps', 'duration', 'total_frames', 'data_streams'), or None if
probing fails or no video stream is found.

##### `get_video_duration` (Line 64)

```python
def get_video_duration(video_path: str) -> float
```
Perform fast probe of video container duration.


**Args:**

- `video_path`: Filesystem path to the video file.


**Returns:**

- `float`: Duration of the video in seconds, or 0.0 on error.

##### `find_box` (Line 86)

```python
def find_box(f, offset: int, end: int, target_path: list, current_depth: int=0)
```
Search recursively for an MP4 atom/box matching target atom hierarchy.

Supports 32-bit and 64-bit extended box sizes and 'meta' container offsets.


**Args:**

- `f`: Open binary file handle positioned for reading.
- `offset`: Starting byte offset in the file for atom search.
- `end`: Ending byte offset bounding the current container box.
- `target_path`: Ordered list of atom four-character codes (e.g. ['moov', 'udta', 'vrot']).
- `current_depth`: Current recursion depth in target_path hierarchy.


**Returns:**

- `tuple[int, int] | None`: Tuple of (box_offset, box_size) in bytes if found, else None.

##### `parse_vrot` (Line 137)

```python
def parse_vrot(vrot_bytes: bytes) -> list
```
Unpack Samsung Gear 360 vrot telemetry records into Euler angles.

Parses 24-byte big-endian records and normalizes angles to [-180, 180] degrees.


**Args:**

- `vrot_bytes`: Raw binary payload extracted from the 'vrot' atom.


**Returns:**

- `list[tuple[float, float, float]]`: List of (roll, pitch, yaw) tuples in degrees.


---

### <a id="imu_fusionpy"></a>`utils/imu_fusion.py`

#### Classes

##### `class MahonyFilter6Axis` (Line 15)

Mahony filter for 6-axis IMU (Gyroscope + Accelerometer).

Corrects gyroscope orientation drift by fusing the accelerometer's gravity
vector via proportional-integral (PI) feedback.

**Methods:**

```python
def __init__(self, sample_rate: float=30.0, kp: float=0.5, ki: float=0.0)
```
> Initialize Mahony 6-axis filter parameters.
> 

> **Args:**

> - `sample_rate`: Sampling frequency in Hz.
> - `kp`: Proportional feedback gain.
> - `ki`: Integral feedback gain.

```python
def reset(self)
```
> Reset filter quaternion to identity and clear integral error feedback.

```python
def update(self, gyro: np.ndarray, accel: np.ndarray) -> np.ndarray
```
> Update orientation quaternion from gyro rate and accelerometer vector.
> 

> **Args:**

> - `gyro`: Tri-axial angular velocity array [gx, gy, gz] in radians/sec.
> - `accel`: Tri-axial acceleration vector [ax, ay, az] in m/s^2 or g.
> 

> **Returns:**

> - `np.ndarray`: Updated unit quaternion [w, x, y, z].

##### `class ComplementaryFilter6Axis` (Line 107)

6-Axis Complementary Filter for pitch and roll gravity fusion.

Fuses high-frequency gyroscope integration with low-frequency accelerometer
tilt angles. Yaw is derived via pure gyro integration since accelerometer
cannot measure vertical rotation.

**Methods:**

```python
def __init__(self, sample_rate: float=30.0, alpha: float=0.98)
```
> Initialize Complementary filter.
> 

> **Args:**

> - `sample_rate`: Sampling frequency in Hz.
> - `alpha`: Gyroscope weighting coefficient in [0.0, 1.0].

```python
def update(self, gyro_deg: np.ndarray, accel: np.ndarray) -> tuple[float, float, float]
```
> Update attitude angles from angular rate and acceleration.
> 

> **Args:**

> - `gyro_deg`: Tri-axial angular velocity [gx, gy, gz] in deg/sec.
> - `accel`: Tri-axial acceleration [ax, ay, az] in m/s^2 or g.
> 

> **Returns:**

> - `tuple[float, float, float]`: Fused attitude angles (roll, pitch, yaw) in degrees.

##### `class EKF6Axis` (Line 153)

Simplified Extended Kalman Filter for 6-axis attitude estimation.

Tracks roll, pitch, and gyroscope bias state vectors with covariance updates.

**Methods:**

```python
def __init__(self, sample_rate: float=30.0, q_angle: float=0.001, q_bias: float=0.003, r_measure: float=0.03)
```
> Initialize EKF process and measurement covariance parameters.
> 

> **Args:**

> - `sample_rate`: Sampling frequency in Hz.
> - `q_angle`: Process noise variance for angle estimation.
> - `q_bias`: Process noise variance for gyro bias estimation.
> - `r_measure`: Measurement noise variance for accelerometer tilt.

```python
def update(self, gyro_deg: np.ndarray, accel: np.ndarray) -> tuple[float, float, float]
```
> Update attitude angles using Extended Kalman Filter.
> 

> **Args:**

> - `gyro_deg`: Tri-axial angular velocity [gx, gy, gz] in deg/sec.
> - `accel`: Tri-axial acceleration [ax, ay, az] in m/s^2 or g.
> 

> **Returns:**

> - `tuple[float, float, float]`: Filtered attitude angles (roll, pitch, yaw) in degrees.

#### Functions

##### `quaternion_to_euler` (Line 244)

```python
def quaternion_to_euler(q: np.ndarray) -> tuple[float, float, float]
```
Convert unit quaternion [w, x, y, z] to Euler angles (roll, pitch, yaw) in degrees.


**Args:**

- `q`: 4-element array-like quaternion [w, x, y, z].


**Returns:**

- `tuple[float, float, float]`: Euler angles (roll, pitch, yaw) in degrees.

##### `fuse_6axis_sequence` (Line 275)

```python
def fuse_6axis_sequence(gyro_data: np.ndarray, accel_data: np.ndarray, fps: float=30.0, method: str='ekf', gain: float=0.51) -> np.ndarray
```
Apply 6-axis sensor fusion over a continuous sample sequence.


**Args:**

- `gyro_data`: (N, 3) array of tri-axial angular rates in deg/sec.
- `accel_data`: (N, 3) array of tri-axial acceleration vectors in m/s^2 or g.
- `fps`: Sampling frame rate in Hz.
- `method`: Fusion algorithm ('ekf', 'mahony', 'complementary', or 'gyro_only').
- `gain`: Filter tuning parameter (kp for Mahony, alpha for complementary, scale for EKF).


**Returns:**

- `np.ndarray`: (N, 3) array of fused Euler angles [roll, pitch, yaw] in degrees.


---

### <a id="calibrate_dual_fisheyepy"></a>`utils/calibrate_dual_fisheye.py`

#### Classes

##### `class DualFisheyeCalibration` (Line 30)

Class to manage, store, load, and export dual-fisheye 360° calibration parameters.

**Methods:**

```python
def __init__(self, name='Dual_Fisheye_Calibration', width=3840, height=1920)
```
> Initialize dual-fisheye calibration structure with default parameters.
> 

> **Args:**

> - `name`: Human-readable identifier for the camera calibration.
> - `width`: Output equirectangular panorama width in pixels.
> - `height`: Output equirectangular panorama height in pixels.

```python
def to_dict(self)
```
> Serialize calibration parameters to dictionary.
> 

> **Returns:**

> Dictionary containing all calibration metadata, lens parameters,
> transforms, maps, seam settings, and color compensation settings.

```python
def from_dict(cls, data)
```
> Load calibration instance from a serialized parameter dictionary.
> 

> **Args:**

> - `data`: Dictionary containing calibration configuration dictionaries.
> 

> **Returns:**

> Populated DualFisheyeCalibration instance.

```python
def save_json(self, file_path)
```
> Save calibration data to JSON file with Linux EOL.
> 

> **Args:**

> - `file_path`: Destination path for the exported JSON file.

```python
def load_json(cls, file_path)
```
> Load calibration data from a JSON file.
> 

> **Args:**

> - `file_path`: Path to the calibration JSON file to load.
> 

> **Returns:**

> Populated DualFisheyeCalibration instance.

#### Functions

##### `calibrate_single_lens` (Line 205)

```python
def calibrate_single_lens(image_paths, pattern_size=(9, 6), square_size_mm=25.0)
```
Recover single lens Kannala-Brandt calibration using OpenCV fisheye model.


**Args:**

- `image_paths`: Sequence of filepaths to calibration chessboard images.
- `pattern_size`: Chessboard internal corner dimensions (cols, rows).
- `square_size_mm`: Physical size of calibration chessboard squares in millimeters.


**Returns:**

Dictionary containing camera intrinsic matrix 'K', distortion
coefficients 'D', center 'cx', 'cy', focal lengths 'fx', 'fy',
and calibration root mean square reprojection error 'rms'.


**Raises:**

- `RuntimeError`: If OpenCV or NumPy is not installed.
- `ValueError`: If no chessboard corners could be detected in the images.

##### `calibrate_stereo_pair` (Line 281)

```python
def calibrate_stereo_pair(front_imgs, rear_imgs, pattern_size=(9, 6), square_size_mm=25.0, K1=None, D1=None, K2=None, D2=None)
```
Estimate lens-to-lens stereo transform using cv2.fisheye.stereoCalibrate.


**Args:**

- `front_imgs`: Sequence of filepaths to front lens calibration images.
- `rear_imgs`: Sequence of filepaths to rear lens calibration images.
- `pattern_size`: Chessboard internal corner dimensions (cols, rows).
- `square_size_mm`: Physical size of calibration chessboard squares in millimeters.
- `K1`: Front camera 3x3 intrinsic matrix.
- `D1`: Front camera fisheye distortion coefficients.
- `K2`: Rear camera 3x3 intrinsic matrix.
- `D2`: Rear camera fisheye distortion coefficients.


**Returns:**

Dictionary containing rotation matrix 'R', rotation vector 'rvec',
translation vector 'T', and stereo calibration RMS error 'stereo_rms'.


**Raises:**

- `RuntimeError`: If OpenCV or NumPy is not installed.
- `ValueError`: If no matching stereo corners are found between image pairs.

##### `main` (Line 365)

```python
def main()
```
CLI entry point for exporting or inspecting dual-fisheye calibration parameters.


---

### <a id="global_calibratepy"></a>`utils/global_calibrate.py`

#### Functions

##### `get_video_duration` (Line 21)

```python
def get_video_duration(video_path)
```
Probe video duration in seconds via ffprobe.


**Args:**

- `video_path`: Filepath to the video file to probe.


**Returns:**

Video duration in seconds as float, or 0.0 if probing fails.

##### `extract_frames_from_video` (Line 40)

```python
def extract_frames_from_video(video_path, num_frames, out_dir)
```
Extract representative test frames evenly distributed across a video timeline.


**Args:**

- `video_path`: Filepath to the source video file.
- `num_frames`: Number of evenly spaced frames to extract.
- `out_dir`: Target directory where extracted JPEG frames are stored.


**Returns:**

List of tuples (timestamp_seconds, frame_image_path) for extracted frames.

##### `detect_circle_bounds` (Line 77)

```python
def detect_circle_bounds(img_half)
```
Detect circular fisheye boundary and optical centroid of a single lens half.


**Args:**

- `img_half`: Single-lens image array (BGR or grayscale).


**Returns:**

Tuple of (cx, cy, radius) indicating the optical center and circle radius in pixels.

##### `fisheye_to_equirect_strip` (Line 108)

```python
def fisheye_to_equirect_strip(img_half, fov_deg, yaw_center_deg, yaw_span_deg=35.0, out_w=300, out_h=600, y_offset=0.0, roll_deg=0.0)
```
Warp a specific longitude strip of a fisheye half into equirectangular projection.


**Args:**

- `img_half`: Input fisheye lens image half.
- `fov_deg`: Assumed camera lens field of view in degrees.
- `yaw_center_deg`: Center longitude/yaw of the target strip in degrees.
- `yaw_span_deg`: Angular width of the longitude strip in degrees.
- `out_w`: Width of output remapped strip in pixels.
- `out_h`: Height of output remapped strip in pixels.
- `y_offset`: Vertical center shift adjustment in pixels.
- `roll_deg`: Angular roll rotation adjustment in degrees.


**Returns:**

Remapped equirectangular strip image as a numpy array.

##### `compute_frame_disparity` (Line 159)

```python
def compute_frame_disparity(front_half, rear_half, fov_deg, y_offset, roll_deg, detector, matcher)
```
Measure feature disparity error between front and rear lenses at seam boundaries.

Extracts overlapping longitude strips at +/-90 degrees azimuth, detects ORB
keypoints, matches descriptors between front and rear projections, and computes
median Euclidean distance error.


**Args:**

- `front_half`: Front lens image array.
- `rear_half`: Rear lens image array.
- `fov_deg`: Candidate field of view in degrees.
- `y_offset`: Candidate vertical offset in pixels.
- `roll_deg`: Candidate rear roll rotation in degrees.
- `detector`: OpenCV 2D feature detector instance (e.g., ORB).
- `matcher`: OpenCV descriptor matcher instance (e.g., BFMatcher).


**Returns:**

Tuple of (mean_disparity_error_px, total_match_count). Returns (999.0, 0)
if insufficient matches are detected.

##### `run_global_calibration` (Line 218)

```python
def run_global_calibration(video_files, num_frames_per_video=10)
```
Run multi-video global joint calibration to determine optimal stitching geometry.

Performs 4-stage optimization:
1. Timeline frame extraction across source videos.
2. Circle boundary and optical centroid detection.
3. Coarse and fine grid search for optimal lens FOV.
4. Joint grid search and micro-tuning for vertical Y-offset and rear roll.


**Args:**

- `video_files`: Sequence of video file paths for dataset sampling.
- `num_frames_per_video`: Number of frames to extract per video file.


**Returns:**

Dictionary containing optimal calibration parameters ('ih_fov', 'iv_fov',
'left_y_offset', 'rear_roll_offset', 'front_center', 'rear_center', etc.),
or None if no valid frames were extracted.


---

### <a id="inspect_mp4py"></a>`utils/inspect_mp4.py`

#### Functions

##### `parse_boxes` (Line 13)

```python
def parse_boxes(f, offset, end, indent='')
```
Recursively parse and display binary ISO BMFF box headers and hierarchy.


**Args:**

- `f`: Open binary file stream handle.
- `offset`: Starting byte offset in file.
- `end`: Ending byte boundary offset.
- `indent`: Indentation whitespace for nested container hierarchy.

##### `main` (Line 64)

```python
def main()
```
CLI entry point for inspecting MP4 file box structure and atom offsets.


---

### <a id="processpy"></a>`utils/process.py`

#### Functions

##### `assign_to_job_object` (Line 12)

```python
def assign_to_job_object(module_name: str='')
```
Assign current process to a Windows Job Object configured with KILL_ON_JOB_CLOSE.

Ensures child processes (e.g., FFmpeg workers, Python child scripts) automatically
terminate when the parent process exits or crashes.


**Args:**

- `module_name`: Optional calling module identifier for debug warnings.


---

### <a id="stabilize_telemetrypy"></a>`utils/stabilize_telemetry.py`

#### Functions

##### `main` (Line 17)

```python
def main()
```
Execute command-line interface for telemetry-driven video stabilization.


---

### <a id="stitch_opencvpy"></a>`utils/stitch_opencv.py`

#### Classes

##### `class SMC200StitchPipeline` (Line 25)

Manages dual-fisheye equirectangular stitching pipeline and FFmpeg encoding.

**Methods:**

```python
def __init__(self, calib_file, out_w=3840, out_h=1920, yaw_deg=0.0, pitch_deg=0.0, roll_deg=0.0)
```
> Initialize the stitch pipeline with calibration parameters and projection dimensions.
> 

> **Args:**

> - `calib_file`: Filepath to dual-fisheye calibration JSON.
> - `out_w`: Target panorama output width in pixels.
> - `out_h`: Target panorama output height in pixels.
> - `yaw_deg`: Global yaw orientation correction in degrees.
> - `pitch_deg`: Global pitch orientation correction in degrees.
> - `roll_deg`: Global roll orientation correction in degrees.
> 

> **Raises:**

> - `FileNotFoundError`: If calib_file is not found at the specified path.

```python
def stitch_frame(self, frame_bgr)
```
> Process a single side-by-side dual-fisheye frame into an equirectangular panorama.
> 

> **Args:**

> - `frame_bgr`: Raw input frame image array (height, width, 3) containing
> both front and rear fisheye circles side-by-side.
> 

> **Returns:**

> Stitched 360° equirectangular image as a uint8 BGR numpy array.

```python
def process_video(self, input_path, output_path, max_frames=0, encoder='libx264', crf=18, status_file=None, lossless=False)
```
> Read input video, stitch frame-by-frame, update status.json, and encode output video.
> 

> **Args:**

> - `input_path`: Filepath to source dual-fisheye video.
> - `output_path`: Filepath for the rendered stitched video.
> - `max_frames`: Optional upper limit on processed frames (0 processes all).
> - `encoder`: FFmpeg video encoder codec name (e.g., 'libx264', 'h264_nvenc', 'ffv1').
> - `crf`: Constant Rate Factor compression parameter for FFmpeg encoding.
> - `status_file`: Optional path to status JSON file for tracking progress.
> - `lossless`: Whether to encode using lossless FFV1 codec.
> 

> **Raises:**

> - `FileNotFoundError`: If input_path does not exist.
> - `RuntimeError`: If video stream cannot be opened by OpenCV.

#### Functions

##### `stitch_single_image` (Line 346)

```python
def stitch_single_image(input_image_path, output_image_path, calib_file, yaw_deg=0.0, pitch_deg=0.0, roll_deg=0.0)
```
Stitch a single image frame or extract first frame from video to equirectangular image.


**Args:**

- `input_image_path`: Path to dual-fisheye image or video file.
- `output_image_path`: Path to save the stitched equirectangular output image.
- `calib_file`: Path to calibration parameters JSON file.
- `yaw_deg`: Yaw rotation adjustment in degrees.
- `pitch_deg`: Pitch rotation adjustment in degrees.
- `roll_deg`: Roll rotation adjustment in degrees.


**Raises:**

- `ValueError`: If frame or image cannot be read from input_image_path.

##### `main` (Line 377)

```python
def main()
```
CLI entry point for dual-fisheye practical video and image stitching pipeline.


---

## 8. Developer & Maintenance Tools

Git RAG AI memory engine, automated docstring extraction, and developer maintenance utilities.

### <a id="git_ragpy"></a>`scripts/git_rag.py`

#### Functions

##### `get_repo_root` (Line 22)

```python
def get_repo_root() -> Path
```
Find the root directory path of the current Git repository.


**Returns:**

Path object representing the repository top-level root, or current
working directory if not in a git repository.

##### `get_current_head` (Line 42)

```python
def get_current_head(repo_root: Path) -> str
```
Retrieve the current HEAD commit hash of the repository.


**Args:**

- `repo_root`: Path to the root of the Git repository.


**Returns:**

40-character commit hash string, or empty string on failure.

##### `get_db_path` (Line 65)

```python
def get_db_path(repo_root: Path) -> Path
```
Return the filesystem path to the SQLite FTS5 index database.


**Args:**

- `repo_root`: Path to the root of the Git repository.


**Returns:**

Path to `.agents/.git_rag.db`.

##### `init_db` (Line 79)

```python
def init_db(db_path: Path, rebuild: bool=False) -> sqlite3.Connection
```
Initialize SQLite database with schema and FTS5 search virtual tables.


**Args:**

- `db_path`: Filesystem path to the SQLite database file.
- `rebuild`: Whether to delete existing database and rebuild schema from scratch.


**Returns:**

Active sqlite3.Connection object.

##### `extract_git_commits` (Line 130)

```python
def extract_git_commits(repo_root: Path, depth: int=50)
```
Extract recent commits, messages, diff summaries, and AI notes.


**Args:**

- `repo_root`: Root directory of the Git repository.
- `depth`: Number of recent commits to inspect.


**Returns:**

List of dictionaries containing document metadata and text content.

##### `extract_markdown_memories` (Line 207)

```python
def extract_markdown_memories(repo_root: Path)
```
Extract structured sections from markdown files in .agents/memory/*.md.


**Args:**

- `repo_root`: Root directory of the Git repository.


**Returns:**

List of dictionaries containing memory titles, sections, and timestamps.

##### `index_all` (Line 251)

```python
def index_all(repo_root: Path, depth: int=50, rebuild: bool=False)
```
Build or update the SQLite FTS5 full-text index from commits and markdown files.


**Args:**

- `repo_root`: Root directory of the Git repository.
- `depth`: Number of recent commits to index.
- `rebuild`: Whether to recreate database from scratch.


**Returns:**

Total number of indexed documents.

##### `ensure_fresh_index` (Line 292)

```python
def ensure_fresh_index(repo_root: Path)
```
Verify index freshness against current HEAD commit and auto-sync if outdated.


**Args:**

- `repo_root`: Root directory of the Git repository.

##### `sanitize_fts_query` (Line 317)

```python
def sanitize_fts_query(query: str) -> str
```
Sanitize and tokenize query string for SQLite FTS5 syntax.


**Args:**

- `query`: Raw query string.


**Returns:**

FTS5 formatted query string with prefix wildcard matches.

##### `query_index` (Line 333)

```python
def query_index(repo_root: Path, query_str: str, limit: int=5)
```
Perform BM25-ranked full-text search against indexed memory and commits.

Falls back to SQL LIKE substring search if FTS5 match parsing fails.


**Args:**

- `repo_root`: Root directory of the Git repository.
- `query_str`: Search keywords.
- `limit`: Maximum number of search results to return.


**Returns:**

List of result dictionaries sorted by relevance score.

##### `record_memory` (Line 401)

```python
def record_memory(repo_root: Path, title: str, content: str, target: str='markdown', filename: str='decisions.md', commit_hash: str='HEAD')
```
Record an engineering decision or context item to markdown memory or git notes.


**Args:**

- `repo_root`: Root directory of the Git repository.
- `title`: Short title summarizing the decision or insight.
- `content`: Detailed explanation or engineering rationale.
- `target`: Storage destination ('markdown' or 'note').
- `filename`: Destination file within `.agents/memory/` for markdown target.
- `commit_hash`: Git commit hash to attach note to if target is 'note'.

##### `install_git_hooks` (Line 443)

```python
def install_git_hooks(repo_root: Path)
```
Install post-commit and post-merge Git hooks for background auto-indexing.


**Args:**

- `repo_root`: Root directory of the Git repository.

##### `main` (Line 470)

```python
def main()
```
CLI entry point for Git RAG AI memory search, indexing, and recording.


---

### <a id="generate_code_docspy"></a>`scripts/generate_code_docs.py`

#### Functions

##### `extract_function_signature` (Line 20)

```python
def extract_function_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str
```
Extract formatted function signature including arguments, defaults, and return type.


**Args:**

- `node`: The AST FunctionDef or AsyncFunctionDef node.


**Returns:**

Formatted function signature string without trailing body block.

##### `format_docstring` (Line 46)

```python
def format_docstring(doc: str, indent: str='') -> str
```
Format a Google-style docstring into clean GitHub markdown.


**Args:**

- `doc`: Raw docstring text.
- `indent`: Optional indentation prefix.


**Returns:**

Formatted markdown text.

##### `parse_module` (Line 79)

```python
def parse_module(filepath: str) -> dict
```
Parse a Python source file using AST and extract its architectural metadata.


**Args:**

- `filepath`: Relative or absolute path to Python script.


**Returns:**

Dictionary containing module docstring, classes, and top-level functions.

##### `generate_markdown` (Line 148)

```python
def generate_markdown(categories: list[tuple[str, str, list[dict]]]) -> str
```
Generate Markdown documentation content from categorized module data.


**Args:**

- `categories`: List of tuples (category_title, category_desc, list of parsed module dicts).


**Returns:**

Complete Markdown document content string.

##### `main` (Line 217)

```python
def main()
```
Main CLI entry point for code documentation generation.


---
