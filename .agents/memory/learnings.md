# Learnings


## [2026-09-01] FTS5 Porter Stemmer
SQLite FTS5 full text search provides instant BM25 matching without external torch/vector DB overhead.


## [2026-09-20] FFmpeg Filtergraph Command Sanitization
Permit semicolons in sanitize_cmd_arg when matching valid FFmpeg filtergraph syntax (e.g. split/crop chains) while preventing shell command chaining, since create_subprocess_exec runs with shell=False and cannot execute shell separators.
