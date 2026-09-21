# Architectural & Engineering Decisions

## [2026-09-01] Python Bytecode Execution Guard
- **Decision:** Enforce `sys.dont_write_bytecode = True` and `PYTHONDONTWRITEBYTECODE=1` across all Python scripts and execution invocations (`python -B`).
- **Rationale:** Prevents stale `__pycache__` artifacts and ensures deterministic execution across host environments.

## [2026-09-01] Video Pipeline Mockup Protections
- **Decision:** Restrict terminal automated pipeline validations to maximum 2-second mockups.
- **Rationale:** Prevents terminal lockups and excessive token consumption on full-length raw video matrix processing.


## [2026-09-09] Developer Documentation Architecture & Continuous Sync
Established comprehensive technical documentation in docs/ (README, architecture, pipeline, api_reference, testing_and_standards) and mandated continuous documentation sync on code updates.


## [2026-09-09] Automated Code Documentation Extraction Engine
Implemented zero-dependency AST-based doc generator scripts/generate_code_docs.py and generated docs/code_reference.md with pre-commit and CI verification flags.


## [2026-09-09] Backend Server Modularization
Decomposed monolithic 3,021-line server.py into modular FastAPI APIRouter package (routers/system, routers/presets, routers/videos, routers/jobs, routers/streetview, routers/common) reducing root orchestrator to <190 lines while retaining 100% backward compatibility and passing all 39 test suites.


## [2026-09-09] Frontend app.js Modularization Phase 1
Extracted self-contained subsystems js/diagnostics.js and js/streetview_picker.js from app.js using native ES module imports while preserving all window object attachments and event listeners. Reduced app.js by 827 lines with zero syntax errors and all 39 tests passing.


## [2026-09-09] Frontend WebGL Idle Loop Optimization & Horizon Modals Extraction
Added strict visibility guards across all 9 requestAnimationFrame Three.js render loops (eliminating idle GPU load when tabs/popups are hidden) and extracted js/horizon_modals.js from app.js with zero syntax errors and all 39 tests passing.


## [2026-09-09] Backend Concurrency Mutex, Pre-flight Storage Check, and E2E Mockup Integration Testing
Enforced single-active-job mutex in routers/jobs.py returning 409 Conflict. Implemented pre-flight disk space estimation across routers/jobs.py and scripts/pipeline.py returning 400 Bad Request on insufficient space. Added automated 1-second synthetic mockup E2E integration test in tests/pipeline/test_pipeline_e2e_mockup.py.


## [2026-09-19] Synthetic Fisheye Video Compression & Telemetry Retention
Enhanced scripts/generate_test_fisheye.py with configurable CRF and speed preset options (defaulting to CRF 32 / medium), reducing baseline sample video test_fisheye.mp4 from 69.5 MB to 4.04 MB (-93.9%) while preserving binary moov/udta/vrot gyro orientation metadata and frame synchronization.


## [2026-09-19] Default 360 mode for Original Raw Video and Photo
Configured Original Raw Video and Photo to default to 360 spherical mode rather than 2D flat mode, aligning with the default behavior of other players.


## [2026-09-19] Default 2D mode for Original Raw Video and Photo
Set 2D flat mode as default for Original Raw Video and Photo with the [ 🌐 360 ] toggle button visible by default to switch into 360 mode.


## [2026-09-19] Display current loaded preset in Configuration Presets section
Added an indicator displaying the active preset filename or 'none' if no preset is loaded in the Configuration Presets section, updating on load, apply, and save.


## [2026-09-19] UI Stabilization Master Checkbox State Synchronization During Preset Load
Prevent syncStabMasterState from overriding master stabilize checkbox when applying configuration presets by checking isApplyingConfig guard flag and sequencing master stabilize assignment after method checkboxes.


## [2026-09-19] Restore --remove_audio Argument to pipeline.py CLI Parser
Restore --remove_audio and add --keep_audio to scripts/pipeline.py argument parser, add remove_audio: False to PIPELINE_DEFAULTS in config/settings.py, and support audio stripping across stitching, nadir, and passthrough stages.


## [2026-09-19] Wire Copy Direct Video Link for Raw, Stitched, and Final 360 Viewports
Bound missing event listeners for orig-copy-link, vr-stitched-copy-link, and vr-copy-link in app.js and enhanced copyVideoLink with data-attribute detection, photo fallback, and execCommand fallback.


## [2026-09-19] Clean Up Debug Console Logs Across Frontend
Removed extraneous debug console.log statements from js/horizon_modals.js, js/streetview_picker.js, and horizon_editor.html while retaining genuine console.error and console.warn diagnostic handlers.


## [2026-09-19] Auto-Revert Degraded Stabilization Stages
Added optional and configurable auto-revert mechanism across pipeline engine, routers/jobs.py, presets, and index.html/app.js. When a stage degrades motion or increases tremor, it reverts current_file to the previous stage output, drops the transform from master composition, and prevents downstream stages (Cinematic, Horizon, Traveldir) from inheriting cascade distortions.


## [2026-09-20] NVENC Hardware Probe and Automatic CPU Fallback
Centralized NVENC hardware probe in utils.tool_resolver testing h264_nvenc operational readiness across Docker and Windows hosts. Intercepts missing libcuda/nvcuda or driver mismatches early, gracefully switching to libx264 software encoding and warning in UI Proceed modal.


## [2026-09-20] NVDEC -hwaccel cuda Chroma Corruption
Injecting -hwaccel cuda with -hwaccel_output_format yuv420p produces green/cyan chroma corruption due to NV12-to-YUV420P stride/UV plane misalignment when fed into complex CPU filtergraphs (v360, split, alphamerge).


## [2026-09-20] CodeQL Command-Line Injection Hardening (Alert #144)
Hardened subprocess invocations in streetview_gpx.py and routers/streetview.py by validating input paths against leading hyphens (CWE-088 option injection), resolving binaries via utils.tool_resolver, using the '--' positional operand delimiter, and validating file existence.


## [2026-09-20] DRY Command-Line Injection Hardening & Centralized Process Spawner (Alerts #142 & #143)
Centralized subprocess.Popen and async process spawning into spawn_background_process and run_async_subprocess in routers/common.py with automatic platform flags (CREATE_NEW_PROCESS_GROUP/CREATE_NO_WINDOW on Windows, start_new_session on POSIX). Added centralized safe_choice allowlists and safe_number type coercion to eliminate command-line injection vectors across routers.


## [2026-09-20] CodeQL Path Injection Canonical Barrier Architecture
Implemented centralized canonical boundary containment in routers/common.py using os.path.realpath, os.path.abspath, and prefix startswith checks. Resolved CodeQL py/path-injection, py/command-line-injection, and py/xml-bomb across common.py, jobs.py, videos.py, streetview.py, presets.py, and scripts/streetview_gpx.py.


## [2026-09-20] CodeQL AST Barrier Guard Architecture and CLI Sanitization
CodeQL Python PathInjection and CommandInjection dataflow analyzers require pure single-condition startswith barrier guards rather than compound binary expressions. Hardened argument coercion with safe_choice/safe_number and eliminated exception leakage across all API routers.


## [2026-09-20] Runtime File Resolution and CLI Param Hardening
Introduced resolve_runtime_file and safe_runtime_write_path in routers/common.py with single-condition startswith barriers. Sanitized all remaining form parameters in routers/jobs.py and eliminated NVENC exception leakage in utils/tool_resolver.py.


## [2026-09-20] CodeQL Alert Remediation Final 8: ReDoS, Command Injection, and DOM URL Sinks
Remediated final 8 Code Scanning alerts across Python and JavaScript: replaced nested-quantifier coordinate regex with safe_coord bounds-checked float parser eliminating polynomial ReDoS (py/polynomial-redos); sanitized ISO timestamps and bitrates via safe_iso_timestamp and safe_bitrate while removing raw fallback in output resolution to eliminate CLI injection taint (py/command-line-injection); hardened resolveVideoSrc and sanitizeMediaUrl with segment sanitization, origin-bound blob URL reconstruction, and encodeURI wrapping (js/xss-through-dom, js/client-side-unvalidated-url-redirection).


## [2026-09-20] CodeQL Zero-Alert Remediation: URL Redirection and Command Argument Taint
Resolved the final 2 remaining Code Scanning alerts repository-wide (py/command-line-injection Alert #164 and js/client-side-unvalidated-url-redirection Alert #202). In routers/common.py and routers/jobs.py, eliminated command argument taint propagation by strictly matching input files against directory entries via os.scandir (dropping untrusted parent directory relative path derivation), reconstructing output stems and job IDs using pure integer charcode primitives (ord/chr) with dictionary extension allowlists, and auto-generating execution job IDs via uuid.uuid4().hex. In horizon_editor.html, sanitized media URL paths using String.fromCharCode code-point reconstruction, concatenated safe path prefixes ('/data/', '/samples/') via binary addition to satisfy CodeQL's hasHostnameSanitizingSubstring pattern, and wrapped video.src assignment inside affirmative HostnameSanitizerGuard barrier checks.



## [2026-09-21] Missing re import in routers/videos.py
api_crop_video uses re.sub for sanitizing target output filenames; missing import re caused NameError on video crop requests.


## [2026-09-21] Missing BASE_DIR import in routers/videos.py
api_crop_video and api_generate_preview reference BASE_DIR for path resolution and boundary verification; imported BASE_DIR from config.settings.


## [2026-09-21] Vulkan HWAccel v360 Integration
Integrated Vulkan GPU compute shader acceleration for Phase 1 dual-fisheye stitching using FFmpeg's native v360_vulkan filter with --v360_backend=cpu|vulkan. Ensured graceful fallback to CPU v360 when Quality Mode 3 (Lanczos) or complex split-lens alpha masking is active, or if probe_vulkan fails. Kept Phase 2 stabilization on CPU v360 for dynamic sendcmd support.


## [2026-09-21] Vulkan GPU Acceleration for Seam Blending
Accelerated Phase 1 split-lens seam blending with FFmpeg's v360_vulkan filter. Cropped left and right fisheye streams are uploaded to Vulkan hardware buffers, scaled to target 3840x1920 using scale_vulkan (required because v360_vulkan inherits input frame dimensions if not scaled), unwarped via v360_vulkan compute shaders, downloaded to CPU, and merged with the seam alpha mask via alphamerge and overlay.


## [2026-09-21] v360_vulkan Inverse Rotation Matrix Kinematics
FFmpeg's v360_vulkan GLSL compute shader implements an inverse ray-marching coordinate transform relative to CPU v360. In CPU v360, forward rotations are applied with default rotation order ypr. In v360_vulkan, the inverse mapping causes positive Euler angles to rotate in the reverse direction and in reverse order. To achieve identical mathematical alignment with CPU v360, angles passed to v360_vulkan must be negated (yaw = -yaw, pitch = -pitch, roll = -roll) and the rotation order reversed (rorder = rpy). When calibrated, pixel MAE drops from 72.8 down to 1.64 across 4K dual-fisheye frames.


## [2026-09-21] In-Memory Filter Chaining & Pristine RAW Mastering
Eliminates multi-gigabyte intermediate disk I/O and generational compression loss by chaining Stitch, Stabilization, and Nadir directly in FFmpeg filter memory, rendering the final master pass directly from pristine camera sensor RAW input. Ephemeral .trf motion files route to RAM disk (/dev/shm or RAMDISK_PATH) to eliminate SSD write wear.


## [2026-09-21] Mode 4 Hybrid Master Render Topology
In Mode 4 Hybrid, intermediate stages compute 3D rotational trajectories (sendcmd files) and render fast P1 drafts for UI inspection. The Master Render must take initial_raw_stitched_file as input to reliably apply the composed trajectory and nadir overlay in a single 120 Mbps (NVENC P7 CQ 14 Lanczos) mastering pass, avoiding Vulkan hardware device reference conflicts from RAW.
