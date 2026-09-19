# StitchStab 360 — Developer Documentation

Welcome to the internal developer documentation for **StitchStab 360**, a dual-fisheye 360° video stitching, auto-calibration, multi-stage stabilization, and spatial metadata injection system.

This documentation serves as the primary technical reference for engineers maintaining, extending, or integrating with the repository.

> [!CAUTION]
> **⚠️ LOCALHOST ONLY — DO NOT EXPOSE TO A NETWORK OR THE INTERNET**
>
> StitchStab 360 is a **personal, single-user local tool** designed to run exclusively on `127.0.0.1` (localhost). The server has **no authentication, no authorization, no multi-user isolation, and no rate limiting**. Binding to `0.0.0.0`, port-forwarding, or placing behind a reverse proxy on any network-accessible host **will give any reachable client full unauthenticated access** to the video processing engine, file system paths, and subprocess execution. The application must never be deployed on a public, shared, or cloud server.



## 📚 Documentation Index

| Document | Purpose & Scope |
| :--- | :--- |
| **[`architecture.md`](./architecture.md)** | System topology, FastAPI backend lifecycle, Single-Page App frontend (Three.js & Leaflet), process isolation, and storage conventions. |
| **[`pipeline.md`](./pipeline.md)** | Deep dive into the video processing engine: demuxing, calibration, remapping, telemetry fusion, stabilization cascade, nadir overlay, and spatial metadata injection. |
| **[`adding_stabilization_methods.md`](./adding_stabilization_methods.md)** | Developer guide for authoring and integrating new stabilization methods into the cascade, API, and web UI. |
| **[`api_reference.md`](./api_reference.md)** | REST API and WebSocket specifications: endpoints, parameters, JSON payload schemas, job management, and intermediate asset discovery. |
| **[`code_reference.md`](./code_reference.md)** | Automated code-level API reference extracted directly from source docstrings and AST syntax trees via `scripts/generate_code_docs.py`. |
| **[`testing_and_standards.md`](./testing_and_standards.md)** | Developer protocols, hermetic testing rules, test suite execution, Python bytecode blocking guards, and pre-commit hooks. |

---

## 🧭 Repository Structure

```
.
├── server.py                   # FastAPI backend server & API routes
├── config/
│   └── settings.py             # Central application constants, defaults, and paths
├── scripts/
│   ├── pipeline.py             # Core pipeline orchestrator & CLI entry point
│   ├── calibrate.py            # Circle detection & auto-calibration algorithms
│   ├── detect_warmup.py        # Gyro warmup detection & orientation normalization
│   ├── extract_telemetry.py    # MP4 atom extraction (vrot, camm, gpmd, GCSV)
│   ├── inject_metadata.py      # YouTube VR & Google Street View metadata injector
│   ├── patch_nadir.py          # Nadir logo overlay & blending filter
│   ├── remap_stitch.py         # Dual-fisheye equirectangular remapping
│   ├── stabilize_cinematic.py  # L1/L2 cinematic trajectory optimizer
│   ├── stabilize_horizon.py    # Checkpoint-based horizon leveler
│   ├── stabilize_imu.py        # Mahony/Madgwick 6-axis IMU filter fusion
│   ├── stabilize_kabsch.py     # Kabsch orthogonal trajectory alignment
│   ├── stabilize_kopf.py       # Kopf visual feature tracking & stabilization
│   ├── stabilize_traveldir.py  # Heading lock to travel direction
│   ├── stabilize_vidstab.py    # libvidstab optical flow transforms
│   ├── streetview_gpx.py       # GPX/KML parsing & CAMM telemetry synchronization
│   ├── visualize_corrections.py# Matplotlib telemetry & trajectory plotter
│   ├── generate_code_docs.py   # Automated docstring extraction & Markdown doc generator
│   └── git_rag.py              # Git RAG AI memory engine & semantic search
├── utils/
│   ├── mp4_utils.py            # Binary MP4 ISO-BMFF atom parser & box slicer
│   └── ...                     # Helper utilities (geometry, math, logging)
├── tests/
│   ├── api/                    # FastAPI route & server unit tests
│   ├── pipeline/               # End-to-end and mock pipeline stage tests
│   └── unit/                   # Core math, parsing, and algorithm unit tests
├── data/
│   ├── input/                  # User input (videos/, nadir/, gps/, presets/, masks/)
│   ├── output/                 # Rendered production videos & GPX tracks
│   └── runtime/                # Transient buffers: work/ (jobs), temp/ (scratch), logs/
├── index.html                  # Frontend web dashboard entry point
├── app.js                      # Frontend logic, Three.js VR viewer, Leaflet map
└── styles.css                  # UI theme and layout styling
```

---

## 🛠️ Developer Quick Start

### 1. Prerequisites
- **Python**: 3.10+ (Python 3.12 recommended)
- **FFmpeg & FFprobe**: Must be compiled with `libvidstab` and accessible in `PATH` (Gyan Full Build recommended; multi-FFmpeg installations are automatically disambiguated or can be pinned via `FFMPEG_PATH`).
- **ExifTool**: Required for metadata extraction and CAMM atom injection.

### 2. Environment Setup
```bash
# Create and activate virtual environment
python -m venv venv
# Windows:
.\venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Running the Server in Development Mode
> [!IMPORTANT]
> Always execute Python using the `-B` flag (`sys.dont_write_bytecode = True`) to prevent `.pyc` caching issues.

```bash
python -B server.py
# Or via uvicorn directly:
python -B -m uvicorn server:app --host 127.0.0.1 --port 8000 --reload
```
Navigate to `http://127.0.0.1:8000` in your browser.

### 4. Running the Test Suite
```bash
python -B -m unittest discover tests
```

### 5. Regenerating Code Documentation
```bash
# Extract docstrings and rebuild docs/code_reference.md:
python -B scripts/generate_code_docs.py

# Or verify up-to-date in CI / pre-commit:
python -B scripts/generate_code_docs.py --check
```

---

## ⚡ Core Rules for Contributors
1. **Header Guard Requirement**: Every `.py` script MUST begin with:
   ```python
   import sys, os
   sys.dont_write_bytecode = True
   os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
   ```
2. **Hermetic Testing**: Tests must never rely on transient job artifacts or persistent scratch files. Always use `tempfile.TemporaryDirectory()` or deterministic `try...finally` cleanup.
3. **Mockup Testing**: Automated pipeline validations in terminal or test suites must NEVER process full-length raw videos. Always use max 2-second mockups.
4. **Documentation Sync**: Whenever code or API contracts change, corresponding documentation in `docs/` and `README.md` must be updated immediately.

---

## 🤖 AI Pair Programming & Engineering Methodology

StitchStab 360 is engineered through a disciplined human-in-the-loop AI pair programming workflow. The architectural design, pipeline orchestration, mathematical models, and quality verification standards are directed, reviewed, and maintained by the author in collaboration with frontier AI models (Google Gemini and Anthropic Claude).

- **Rulebooks & Memory**: Repository operational constraints and protocols are unified and synchronized across [`GEMINI.md`](../GEMINI.md) and [`CLAUDE.md`](../CLAUDE.md), with matching workspace ignore filters in [`.geminiignore`](../.geminiignore) and [`.claudeignore`](../.claudeignore).
- **Architectural Rationales**: Decisions and technical context are continuously indexed in [`.agents/memory/decisions.md`](../.agents/memory/decisions.md) and [`.agents/memory/context.md`](../.agents/memory/context.md) via the Git RAG memory engine.
- **Deterministic Quality**: Every algorithm and refactor is verified through an automated test suite (`tests/`), repository guardrail verification (`scripts/verify_repo.py`), pre-commit hooks, and strict bytecode blocking (`python -B`).
