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
