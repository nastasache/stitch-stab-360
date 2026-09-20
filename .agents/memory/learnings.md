# Learnings


## [2026-09-01] FTS5 Porter Stemmer
SQLite FTS5 full text search provides instant BM25 matching without external torch/vector DB overhead.


## [2026-09-20] FFmpeg Filtergraph Command Sanitization
Permit semicolons in sanitize_cmd_arg when matching valid FFmpeg filtergraph syntax (e.g. split/crop chains) while preventing shell command chaining, since create_subprocess_exec runs with shell=False and cannot execute shell separators.


## [2026-09-20] Float Preservation in Telemetry Smoothing
Configured --telemetry_smoothing as float in scripts/pipeline.py and routers/jobs.py. The underlying smooth_telemetry_angles filter uses Gaussian sigma = window_size / 4.0, which naturally operates with continuous floating-point precision. Decimal floats must not be truncated to ints where mathematical filters benefit from sub-frame precision.
