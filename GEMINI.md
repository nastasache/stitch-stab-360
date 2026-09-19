# 🟢 GEMINI.md — Repository Rules for Gemini & Antigravity Agents

## 1️⃣ Mandatory Python Fresh Execution & Cache-Block Rule
- **Header Guard Requirement**: EVERY Python script (`.py`) added or edited in this repository MUST have the following guard block at the very top of the file (immediately after any `#!/usr/bin/env python3` shebang):
  ```python
  import sys, os
  sys.dont_write_bytecode = True
  os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
  ```
- **CLI & Subprocess Executions**:
  - Always invoke Python using the `-B` flag: `python -B script.py` or `sys.executable -B script.py`.
  - Pass `PYTHONDONTWRITEBYTECODE=1` in subprocess environment dictionaries (`os.environ` / `env`).

## 2️⃣ File Safety & Absolute No-Deletion Policy
- **No File/Folder Deletion**: NEVER use or implement functions, methods, or shell commands that remove or delete files or directories (e.g., `os.remove`, `os.unlink`, `shutil.rmtree`, `unlink()`, `rmdir()`, `Remove-Item`, `del`).
- **Test Output Storage**: Tests requiring disk writes must output to `tests/temp/` and must not delete generated artifacts.
- **Manual Cleanup Only**: All storage, log, and cache cleanup remains strictly manual by the user.

## 3️⃣ 360-Video & Multimedia Pipeline Protections (OpenCV / FFmpeg)
- **No Large Video Processing in Terminal**: Never run scripts on actual large video files inside the terminal.
- **Max 2-Second Mockups**: Run automated pipeline validations exclusively using a max 2-second mockup video sequence for tests.
- **Execution & Terminal Protections**:
  - If a terminal execution command fails twice in a row, stop immediately and ask the user.
  - If an FFmpeg or OpenCV script errors out or fails in the terminal, DO NOT retry in a loop. Stop and ask the user immediately.
- **Tool Resolution**: Always resolve multimedia binaries (FFmpeg, FFprobe, ExifTool) via `utils.tool_resolver.resolve_ffmpeg()` rather than assuming generic system PATH binaries.
- **Token Economy**: Keep explanations concise and focused on structural changes.

## 4️⃣ Full Data & Telemetry Preservation on Video Crop
- **Mandatory Complete Data Retention**: ALWAYS when cropping or trimming any video, preserve ALL data and private metadata atoms (`moov/udta/vrot`, `m360`, `opax`, `opai`, `gpmd`, `camm`) matching the cropped section.
- **Tooling**: Always use `scripts/crop_camera_video.py` or dedicated binary atom slicers so that internal gyro/IMU orientation frames and sidecar telemetry (`.txt` / `.gcsv`) are cleanly preserved frame-for-frame.

## 5️⃣ Architecture, Stack & Environment Boundaries
- **Host Environment**: Target environment is Windows 11 with PowerShell / Command Prompt.
- **No Linux-Only Commands**: Never invoke `grep`, `sed`, `awk`, `export`, or `sandbox-exec`. Use PowerShell equivalents (`Select-String`) or Python scripts. Shell commands executed on the host must use Windows-compatible backslash (`\`) paths.
- **Frontend Architecture**: Client-side UI is strictly Vanilla JavaScript (ES6+), HTML5 Canvas/WebGL, and native CSS (`app.js`, `styles.css`).
- **No Node.js / NPM Build Steps**: Do not introduce bundlers (Vite/Webpack), package managers (`npm`/`yarn`), or SPA frameworks.
- **Port & Network Invariance**: Never alter or hardcode server ports. Always respect `config/settings.py` (FastAPI backend defaults to port 8000).

## 6️⃣ General Coding Principles & Code Integrity
- Security first.
- Preserve commented code. Do not remove or alter existing comments.
- Do not reformat code or alter existing indentation spacing.
- Do not change existing variable, function, or class names.
- Change files as minimally as possible; submit targeted code changes only.
- Use strictly Linux EOL (`\n`) for all file writes.
- Always verify file persistence and disk sync after writing files.
- **Continuous Documentation Sync**: Anytime code is updated, update the documentation in `docs/` and `README.md` accordingly.

## 7️⃣ Automated Testing Standard
- **Running Tests**: Always execute tests with bytecode blocking: `python -B -m pytest tests/` or run `run_tests.bat`.
- **Mock Data Only**: Automated tests must never process production videos; rely strictly on `samples/` mockups and synthetic frame generators in `tests/`.

## 8️⃣ Git RAG AI Memory Engine
- **Memory Files**: Architectural decisions are recorded in `.agents/memory/decisions.md`, learnings in `.agents/memory/learnings.md`, and pipeline architecture in `.agents/memory/context.md`.
- **Querying Context**: Run `python -B scripts/git_rag.py query "<topic>"` to retrieve past decisions and commit rationales.
- **Recording Decisions**: Run `python -B scripts/git_rag.py record "<Title>" "<Rationale>" --target=markdown`.
- **Auto-Sync**: Git hooks (`post-commit`, `post-merge`) and lazy auto-sync keep the search index `.agents/.git_rag.db` up to date automatically.
- **Proactive Memory Logging**: Whenever resolving an architectural edge-case, non-obvious bug, or calibrating an algorithm, record the technical rationale to `.agents/memory/learnings.md` or run `python -B scripts/git_rag.py record`.
