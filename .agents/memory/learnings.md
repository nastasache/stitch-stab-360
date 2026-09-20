# Learnings


## [2026-09-01] FTS5 Porter Stemmer
SQLite FTS5 full text search provides instant BM25 matching without external torch/vector DB overhead.


## [2026-09-20] FFmpeg Filtergraph Command Sanitization
Permit semicolons in sanitize_cmd_arg when matching valid FFmpeg filtergraph syntax (e.g. split/crop chains) while preventing shell command chaining, since create_subprocess_exec runs with shell=False and cannot execute shell separators.


## [2026-09-20] Float Preservation in Telemetry Smoothing
Configured --telemetry_smoothing as float in scripts/pipeline.py and routers/jobs.py. The underlying smooth_telemetry_angles filter uses Gaussian sigma = window_size / 4.0, which naturally operates with continuous floating-point precision. Decimal floats must not be truncated to ints where mathematical filters benefit from sub-frame precision.


## [2026-09-20] CLI & API Type Hardening
Configured --duration as float in pipeline.py and routers/jobs.py to prevent truncation of fractional durations. Added _parse_int to pipeline.py argument parser for discrete parameters (blend_width, frame indices, resolutions) to safely accept integer-equivalent float representations without raising ValueError. Enforced int(float(...)) across API form parsing in routers/videos.py, routers/jobs.py, and routers/streetview.py.


## [2026-09-21] Preserve Query Parameters in Client-Side Media Sanitizers
Updated resolveVideoSrc in app.js and sanitizeMediaUrl in horizon_editor.html to cleanly extract and validate safe query strings (?t=timestamp cachebusters) before splitting path segments. Previously, non-alphanumeric replacement converted ?t= into _t_, causing 404 Not Found on intermediate video playback.


## [2026-09-21] Auto-Sync Virtual Environment Packages in start.bat
Enhanced start.bat to proactively verify that defusedxml is importable in the virtual environment before launching uvicorn. If missing (e.g. from an older venv created before requirements update), start.bat automatically runs pip install -r requirements.txt to prevent ModuleNotFoundError on no-docker installations.
