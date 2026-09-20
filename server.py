import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

"""StitchStab 360 FastAPI web server and background job orchestration engine.

Serves the interactive web dashboard and mounts modular APIRouters for system diagnostics,
preset configuration, video processing, stabilization, Street View export, and background jobs.
"""

import asyncio
import traceback
from datetime import datetime
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as _StarletteHTTPException
from starlette.requests import ClientDisconnect

# ── Centralized Configuration & Settings ─────────────────────────────────────

from config.settings import PATHS, SERVER_CONFIG, PIPELINE_DEFAULTS
from utils.process import assign_to_job_object

# ── Re-exported Common Utilities for Backward Compatibility ──────────────────

from routers.common import (
    MissingParameterError,
    require_param,
    is_safe_to_kill,
    kill_process_tree,
    _managed_pids,
    _MANAGED_PIDS_FILE,
    load_managed_pids,
    save_managed_pids,
    cleanup_all_active_jobs,
    run_async_subprocess,
    is_valid_video_file,
    is_valid_image_file,
    is_valid_media_file,
    _safe_resolve,
    resolve_input_file,
    resolve_output_file,
    resolve_nadir_logo,
    resolve_gpx_file,
    resolve_video_version,
    sanitize_preset_filename,
    get_input_video_options_html,
    get_nadir_logo_options_html,
    get_gpx_options_html,
    get_input_videos_list,
    get_nadir_logos_list,
    get_gpx_files_list,
    render_dashboard_html
)

# ── Domain APIRouters ────────────────────────────────────────────────────────

from routers import system, presets, videos, jobs, streetview

# ── Lifespan & Application Startup ───────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown lifecycle hooks.

    Args:
        app: The running FastAPI application instance.
    """
    # Startup
    assign_to_job_object()
    yield
    # Shutdown
    cleanup_all_active_jobs()

# Set working directory to project root
BASE_DIR = PATHS["BASE_DIR"]
os.chdir(str(BASE_DIR))

# Ensure required runtime and storage directories exist
for folder in PATHS["REQUIRED_DIRECTORIES"]:
    os.makedirs(folder, exist_ok=True)

app = FastAPI(title="StitchStab 360", version="2.0.0", lifespan=lifespan)

@app.exception_handler(MissingParameterError)
async def missing_parameter_exception_handler(request: Request, exc: MissingParameterError):
    """Handle missing mandatory parameter exceptions with HTTP 422 JSON response.

    Args:
        request: Incoming FastAPI request.
        exc: Raised MissingParameterError instance.

    Returns:
        JSONResponse with HTTP 422 status and error details.
    """
    return JSONResponse(
        status_code=422,
        content={"status": "error", "error": exc.message, "parameter": exc.param_name}
    )

_counter_lock = jobs._counter_lock  # Protects run_counter.txt from concurrent reads/writes (M-3)

app.add_middleware(
    CORSMiddleware,
    allow_origins=SERVER_CONFIG["CORS_ORIGINS"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1024)

# ── Security Middleware ───────────────────────────────────────────────────────

_MAX_BODY_BYTES = SERVER_CONFIG["MAX_BODY_BYTES"]

@app.middleware("http")
async def security_middleware(request: Request, call_next):
    """Enforce payload size limits, block sensitive static file paths, and add security headers.

    Args:
        request: Incoming FastAPI HTTP request.
        call_next: Request handler continuation callback.

    Returns:
        HTTP Response with added security headers or error response if rejected.
    """
    # Fast-path: reject immediately when Content-Length header is present and too large
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > _MAX_BODY_BYTES:
                return JSONResponse({"status": "error", "error": "Request body too large."}, status_code=413)
        except ValueError:
            pass

    # SEC-03: Streaming guard for chunked / Transfer-Encoding bodies that omit Content-Length.
    # Wraps the ASGI receive callable to count bytes as they arrive.
    _body_bytes_seen = 0
    _body_limit_exceeded = False
    _orig_receive = request._receive

    async def _size_limited_receive():
        nonlocal _body_bytes_seen, _body_limit_exceeded
        message = await _orig_receive()
        if message.get("type") == "http.request":
            _body_bytes_seen += len(message.get("body", b""))
            if _body_bytes_seen > _MAX_BODY_BYTES:
                _body_limit_exceeded = True
                # Signal end-of-body with an empty chunk so the handler terminates cleanly
                return {"type": "http.request", "body": b"", "more_body": False}
        return message

    request._receive = _size_limited_receive

    # H-3: Block source code and sensitive configs from static root mount
    path = request.url.path.lower()
    if path.endswith(('.py', '.bat', '.sh', '.env', '.ps1', '.cmd')):
        return Response(status_code=404)
    if (path.endswith(('.json', '.txt', '.md', '.yml', '.yaml', '.ini', '.toml', '.cfg'))
            and not path.startswith(('/api/', '/data/', '/assets/', '/samples/'))
            and path not in ('/openapi.json', '/openapi.yaml', '/config/config.json')):
        return Response(status_code=404)

    response = await call_next(request)
    # If the streaming guard tripped during body read, override with 413
    if _body_limit_exceeded:
        return JSONResponse({"status": "error", "error": "Request body too large."}, status_code=413)
    # Security headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self' http://127.0.0.1:8000 http://localhost:8000; "
        "script-src 'self'; "
        "script-src-elem 'self' 'unsafe-inline'; "
        "script-src-attr 'unsafe-hashes' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "font-src 'self' data:; "
        "img-src 'self' http://127.0.0.1:8000 http://localhost:8000 data: blob: https://server.arcgisonline.com https://*.arcgisonline.com https://*.google.com https://*.googleapis.com https://*.gstatic.com https://*.tile.openstreetmap.org https://tile.openstreetmap.org https://*.basemaps.cartocdn.com; "
        "media-src 'self' http://127.0.0.1:8000 http://localhost:8000 blob: data:; "
        "connect-src 'self' http://127.0.0.1:8000 http://localhost:8000 https://server.arcgisonline.com https://*.arcgisonline.com https://*.tile.openstreetmap.org https://tile.openstreetmap.org"
    )
    return response

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch unhandled exceptions and return safe HTTP 500 responses.

    Args:
        request: Incoming FastAPI HTTP request.
        exc: Caught unhandled exception.

    Returns:
        JSONResponse with HTTP 500 error or HTTP 499 for client disconnections.
    """
    if isinstance(exc, (asyncio.CancelledError, ClientDisconnect)):
        return Response(status_code=499)
    err_msg = str(exc) or type(exc).__name__
    print(f"[ERROR] Global Server Exception on {request.method} {request.url}: {err_msg}")
    traceback.print_exc()
    try:
        os.makedirs("data/runtime/logs", exist_ok=True)
        log_entry = (
            f"[{datetime.now().isoformat()}] [ERROR] Global Server Exception on "
            f"{request.method} {request.url}: {err_msg}\n"
            f"{traceback.format_exc()}\n"
        )
        with open("data/runtime/logs/server_errors.log", "a", encoding="utf-8") as f_err:
            f_err.write(log_entry)
    except Exception:
        pass
    return JSONResponse(
        status_code=500,
        content={"status": "error", "error": "An internal server error occurred."}
    )

# ── Mount Domain Routers ─────────────────────────────────────────────────────

app.include_router(system.router)
app.include_router(presets.router)
app.include_router(videos.router)
app.include_router(jobs.router)
app.include_router(streetview.router)

# ── Mount Static Folders ─────────────────────────────────────────────────────
# Mount static directories so that video range streaming and static files work seamlessly
# NOTE: 'scripts' is intentionally excluded — Python source must not be publicly downloadable.
for static_dir in SERVER_CONFIG["STATIC_DIRECTORIES"]:
    d_path = BASE_DIR / static_dir
    d_path.mkdir(exist_ok=True)
    app.mount(f"/{static_dir}", StaticFiles(directory=str(d_path)), name=static_dir)

# ── Root-level Static File Allowlist (SEC-04) ────────────────────────────────
# Replaces the unconstrained StaticFiles(BASE_DIR) mount with an explicit
# allowlist so that only the five intentionally public root-level files are
# ever served.  Every other filename returns 404 before any file-system
# lookup occurs, regardless of what actually exists in BASE_DIR.

class _AllowlistStaticFiles(StaticFiles):
    """StaticFiles subclass restricted to an explicit set of root-level filenames."""
    _ALLOWED: frozenset = frozenset({
        "app.js",
        "styles.css",
        "horizon_editor.html",
        "route_editor.html",
        "favicon.ico",
    })

    async def get_response(self, path: str, scope):
        if path.lstrip("/") not in self._ALLOWED:
            raise _StarletteHTTPException(status_code=404)
        return await super().get_response(path, scope)


class _ConfigStaticFiles(StaticFiles):
    """Serves only config/config.json — blocks settings.py, run_counter.txt, etc."""
    _ALLOWED: frozenset = frozenset({"config.json"})

    async def get_response(self, path: str, scope):
        if path.lstrip("/") not in self._ALLOWED:
            raise _StarletteHTTPException(status_code=404)
        return await super().get_response(path, scope)


# Mount config/ — only config.json is accessible; settings.py and run_counter.txt are blocked.
_config_dir = BASE_DIR / "config"
_config_dir.mkdir(exist_ok=True)
app.mount("/config", _ConfigStaticFiles(directory=str(_config_dir)), name="config_static")

# Mount root static files (app.js, styles.css, horizon_editor.html, etc.)
# Only filenames in _AllowlistStaticFiles._ALLOWED are served.
app.mount("/", _AllowlistStaticFiles(directory=str(BASE_DIR), html=False), name="root_static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=SERVER_CONFIG["HOST"], port=SERVER_CONFIG["PORT"], reload=False)
