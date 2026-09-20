import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

"""Common server utilities, path resolvers, process management, and HTML generators."""

import re
import glob
import html
import json
import time
import shutil
import asyncio
import subprocess
import signal
from pathlib import Path
from typing import Optional, Dict, Any, List
import psutil

from config.settings import BASE_DIR, PATHS, SERVER_CONFIG, PIPELINE_DEFAULTS
from utils.process import assign_to_job_object

# Initialize Job Object for current server process
assign_to_job_object("server")

class MissingParameterError(Exception):
    """Exception raised when a required request parameter is missing or empty.

    Attributes:
        param_name: Name of the missing parameter.
        message: Descriptive error message explaining the omission.
    """
    def __init__(self, param_name: str, message: str = ""):
        self.param_name = param_name
        self.message = message or f"Missing required parameter: '{param_name}'"
        super().__init__(self.message)

def require_param(form_data: dict, name: str, param_type: type = str) -> Any:
    """Extracts a mandatory parameter from request form data.

    Args:
        form_data: Key-value dictionary parsed from an incoming HTTP form request.
        name: Parameter name to extract.
        param_type: Expected Python type (str, int, float) to cast the value into.

    Returns:
        The extracted and parsed value conforming to `param_type`.

    Raises:
        MissingParameterError: If the parameter is absent, empty, or cannot be cast.
    """
    val = form_data.get(name)
    if val is None or (isinstance(val, str) and str(val).strip() == ""):
        raise MissingParameterError(name, f"Missing required parameter: '{name}'. Value must be explicitly provided.")
    if param_type == int:
        try:
            return int(float(val))
        except (ValueError, TypeError):
            raise MissingParameterError(name, f"Parameter '{name}' must be a valid integer, received: '{val}'")
    elif param_type == float:
        try:
            return float(val)
        except (ValueError, TypeError):
            raise MissingParameterError(name, f"Parameter '{name}' must be a valid number, received: '{val}'")
    return str(val).strip()

# ── Cross-Platform Process Management ────────────────────────────────────────

def is_safe_to_kill(pid: int) -> bool:
    """Verify that the target PID strictly belongs to this 360 workspace/pipeline.

    Inspects command-line parameters to avoid killing unrelated system or user processes.

    Args:
        pid: OS process ID to validate.

    Returns:
        True if the process matches known 360 project binaries and workspace markers; False otherwise.
    """
    if not pid or not isinstance(pid, int) or pid <= 10:
        return False
    if pid == os.getpid() or (hasattr(os, "getppid") and pid == os.getppid()):
        return False

    try:
        cmdline = ""
        try:
            cmdline = " ".join(psutil.Process(pid).cmdline())
        except psutil.NoSuchProcess:
            return False
        except psutil.AccessDenied:
            return False
        except Exception:
            cmdline = ""

        cmdline_lower = cmdline.lower()
        if not cmdline_lower:
            return False

        # 1. Verify executable binary belongs to python or ffmpeg
        allowed_bins = ["python", "ffmpeg", "ffprobe"]
        if not any(b in cmdline_lower for b in allowed_bins):
            return False

        # 2. Verify command line references this specific 360 project or its pipeline scripts
        project_dir_name = os.path.basename(str(BASE_DIR)).lower()
        workspace_markers = [
            project_dir_name,
            "pipeline",
            "stitch_pipeline",
            "server.py",
            "stabilize_horizon",
            "auto_calibrate",
            "streetview_gpx",
            "crop_camera_video",
            "detect_warmup",
            "alpha_mask",
            "v360=",
            "data/",
            "data\\",
            "assets/",
            "assets\\"
        ]
        return any(marker in cmdline_lower for marker in workspace_markers)
    except Exception:
        return False

def kill_process_tree(pid: int):
    """Forcefully terminate a process and all its children after identity verification.

    Args:
        pid: Target process identifier.
    """
    if not is_safe_to_kill(pid):
        print(f"[SECURITY] Refusing to kill PID {pid}: Process does not match 360 project workspace signature.", file=sys.stderr)
        return
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/PID", str(pid), "/T"], capture_output=True, timeout=2, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            try:
                pgid = os.getpgid(pid)
                os.killpg(pgid, signal.SIGTERM)
                time.sleep(0.1)
                os.killpg(pgid, signal.SIGKILL)
            except Exception:
                try:
                    os.kill(pid, signal.SIGTERM)
                    time.sleep(0.1)
                    os.kill(pid, signal.SIGKILL)
                except Exception:
                    pass
    except Exception as e:
        print(f"[WARN] Failed to kill process tree for PID {pid}: {e}", file=sys.stderr)

# Registry of child processes spawned by this server: job_id -> pid
# Only PIDs registered here may be killed via the cancel action.
_managed_pids: Dict[str, int] = {}
_MANAGED_PIDS_FILE = PATHS["MANAGED_PIDS_FILE"]

def load_managed_pids():
    """Load the mapping of managed background child process IDs from disk."""
    global _managed_pids
    try:
        if _MANAGED_PIDS_FILE.exists():
            data = json.loads(_MANAGED_PIDS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _managed_pids = {k: int(v) for k, v in data.items()}
    except Exception as e:
        print(f"[WARN] load_managed_pids: failed reading PID file, resetting: {e}", file=sys.stderr)
        _managed_pids = {}

def save_managed_pids():
    """Persist the mapping of managed child process IDs to disk."""
    try:
        _MANAGED_PIDS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _MANAGED_PIDS_FILE.write_text(json.dumps(_managed_pids), encoding="utf-8")
    except Exception as e:
        print(f"[WARN] save_managed_pids: failed writing PID file: {e}", file=sys.stderr)

load_managed_pids()

def cleanup_all_active_jobs():
    """Kill all processes managed by this server and clear the PID registry."""
    global _managed_pids
    try:
        if not _managed_pids:
            return
        for job_id, pid in list(_managed_pids.items()):
            if pid and isinstance(pid, int):
                print(f"[SHUTDOWN] Terminating managed active job PID {pid} (job: {job_id})")
                kill_process_tree(pid)
        _managed_pids.clear()
        save_managed_pids()
    except Exception as e:
        print(f"[WARN] Error during cleanup_all_active_jobs: {e}", file=sys.stderr)

def get_active_job() -> Optional[tuple[str, int]]:
    """Return (job_id, pid) of any actively executing job, pruning dead or completed entries.

    Returns:
        Tuple of (job_id, pid) if an active job is executing; None otherwise.
    """
    global _managed_pids
    load_managed_pids()
    dead_jobs = []
    active = None
    for jid, pid in list(_managed_pids.items()):
        if not pid or not isinstance(pid, int) or pid <= 0:
            dead_jobs.append(jid)
            continue
        try:
            if psutil.pid_exists(pid) and is_safe_to_kill(pid):
                sf = f"data/runtime/temp/status_{jid}.json"
                if os.path.exists(sf):
                    try:
                        with open(sf, "r", encoding="utf-8") as f:
                            s_data = json.load(f)
                        st = s_data.get("status", "")
                        if st in ["completed", "failed", "cancelled", "error"]:
                            dead_jobs.append(jid)
                            continue
                    except Exception as e:
                        print(f"[WARN] get_active_job: failed reading status for job {jid}: {e}", file=sys.stderr)
                active = (jid, pid)
                break
            else:
                dead_jobs.append(jid)
        except Exception as e:
            print(f"[WARN] get_active_job: error checking PID for job {jid}: {e}", file=sys.stderr)
            dead_jobs.append(jid)

    if not active and os.path.exists("data/runtime/temp/status.json"):
        try:
            with open("data/runtime/temp/status.json", "r", encoding="utf-8") as f:
                s_data = json.load(f)
            if isinstance(s_data, dict):
                st = s_data.get("status", "")
                pid = s_data.get("pid")
                if st in ['stitching', 'stabilizing', 'nadiring', 'injecting', 'reporting', 'exporting', 'processing', 'awaiting_transforms']:
                    if pid and isinstance(pid, int) and psutil.pid_exists(pid) and is_safe_to_kill(pid):
                        active = (s_data.get("job_id") or "active_job", pid)
        except Exception as e:
            print(f"[WARN] get_active_job: failed reading fallback status.json: {e}", file=sys.stderr)

    if dead_jobs:
        for jid in dead_jobs:
            _managed_pids.pop(jid, None)
        save_managed_pids()

    return active

def check_disk_space(input_file: str = "", num_stages: int = 1, min_margin_mb: int = 500) -> tuple[bool, str, float, float]:
    """Verify that the target runtime drive has sufficient free space for encoding passes.

    Args:
        input_file: Path to input video file (used to gauge stage output sizes).
        num_stages: Number of sequential encoding/rendering stages enabled.
        min_margin_mb: Minimum required safety buffer in megabytes.

    Returns:
        Tuple of (is_sufficient, error_message, required_gb, available_gb).
    """
    try:
        work_dir = PATHS.get("WORK_DIR") or BASE_DIR / "data" / "runtime" / "work"
        target_path = Path(work_dir)
        if not target_path.exists():
            target_path = BASE_DIR
        disk_usage = shutil.disk_usage(str(target_path))
        free_bytes = disk_usage.free
        available_gb = free_bytes / (1024 ** 3)

        base_size_bytes = 0
        if input_file and not str(input_file).strip().startswith("-"):
            clean_in = str(input_file).strip()
            base_dir_abs = os.path.realpath(os.path.abspath(str(BASE_DIR)))
            target_in = os.path.realpath(os.path.abspath(os.path.join(base_dir_abs, clean_in) if not os.path.isabs(clean_in) else clean_in))
            if not target_in.startswith(base_dir_abs + os.sep):
                target_in = ""
            if target_in and os.path.isfile(target_in):
                try:
                    base_size_bytes = os.path.getsize(target_in)
                except Exception:
                    base_size_bytes = 0

        # Fallback estimation if input size cannot be probed: 500MB per stage
        if base_size_bytes <= 0:
            base_size_bytes = 500 * 1024 * 1024

        min_margin_bytes = min_margin_mb * 1024 * 1024
        required_bytes = (base_size_bytes * max(1, num_stages)) + min_margin_bytes
        required_gb = required_bytes / (1024 ** 3)

        if free_bytes < required_bytes:
            msg = (
                f"Insufficient disk space on working volume: estimated {required_gb:.2f} GB required "
                f"for {num_stages} stage(s), but only {available_gb:.2f} GB is available."
            )
            return False, msg, required_gb, available_gb

        return True, "", required_gb, available_gb
    except Exception as e:
        print(f"[WARN] Disk check failed: {e}", file=sys.stderr)
        return True, "Disk check warning: check failed.", 0.0, 0.0


# ── Helper Functions ─────────────────────────────────────────────────────────

def sanitize_cmd_arg(arg: Any) -> str:
    """Sanitize and validate a command-line argument to prevent command and argument injection.

    Args:
        arg: Value to sanitize.

    Returns:
        Safe string representation without null bytes.

    Raises:
        ValueError: If null bytes or prohibited control sequences are detected.
    """
    if arg is None:
        return ""
    s = str(arg)
    if any(c in s for c in ("\0", "\n", "\r", ";", "&", "|", "`", "$", ">", "<")):
        raise ValueError(f"Prohibited control sequence in command argument: {s!r}")
    return s


def safe_number(val: Any, default: Any, cast_fn=float) -> str:
    """Validate and strictly cast a numeric CLI parameter, falling back to default.

    Prevents arbitrary string injection in numeric arguments (e.g. yaw, pitch, roll, fov).

    Args:
        val: Input value from request or configuration.
        default: Fallback numeric value.
        cast_fn: Type conversion function (float or int).

    Returns:
        String representation of the validated number.
    """
    try:
        if val is None or str(val).strip() == "":
            num = float(default)
        else:
            num = float(val)
        if cast_fn is int:
            return str(int(num))
        return str(float(num))
    except (ValueError, TypeError):
        return str(default)


def safe_choice(val: Any, allowed: Any, default: str) -> str:
    """Validate a string value against a strict allowlist of permitted options.

    Args:
        val: Input value from request or configuration.
        allowed: Collection of permitted literal strings.
        default: Safe fallback string.

    Returns:
        Matched option string if present in allowlist, otherwise default.
    """
    s = str(val).strip() if val is not None else ""
    for opt in allowed:
        if s == str(opt):
            return str(opt)
    return str(default)


def spawn_background_process(
    cmd: list[str],
    stdout=None,
    stderr=subprocess.STDOUT,
    cwd: Optional[str] = None,
    env: Optional[dict] = None
) -> subprocess.Popen:
    """Spawn a detached background process across Windows and POSIX safely.

    Consolidates platform-specific process creation flags (CREATE_NEW_PROCESS_GROUP
    on Windows, start_new_session on POSIX) and guarantees arguments are sanitized.

    Args:
        cmd: List of command arguments.
        stdout: File handle or pipe for stdout redirection.
        stderr: File handle or pipe for stderr redirection.
        cwd: Working directory path.
        env: Environment variables dictionary.

    Returns:
        Spawned subprocess.Popen instance.
    """
    if not cmd:
        raise ValueError("Cannot spawn background process with empty command list.")

    allowed_executables = {sys.executable, "python", "python3", "ffmpeg", "ffprobe"}
    cmd_exec = str(cmd[0])
    if cmd_exec not in allowed_executables and not cmd_exec.endswith(os.sep + "python.exe"):
        raise ValueError(f"Unauthorized executable for background process: {cmd_exec!r}")

    clean_cmd = [sanitize_cmd_arg(a) for a in cmd if a is not None]
    if not clean_cmd:
        raise ValueError("Cannot spawn background process with empty command list.")

    kwargs: dict[str, Any] = {
        "stdout": stdout,
        "stderr": stderr,
    }
    if cwd:
        kwargs["cwd"] = cwd
    if env:
        kwargs["env"] = env

    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True

    return subprocess.Popen(clean_cmd, **kwargs)


async def run_async_subprocess(*args, **kwargs) -> tuple[bytes, bytes]:
    """Run a subprocess asynchronously with automatic fallback for Windows loops.

    Args:
        *args: Command line arguments to execute.
        **kwargs: Additional subprocess execution flags and options.

    Returns:
        A tuple containing (stdout_bytes, stderr_bytes).
    """
    clean_args = [sanitize_cmd_arg(a) for a in args if a is not None]
    if sys.platform == "win32" and "creationflags" not in kwargs:
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        proc = await asyncio.create_subprocess_exec(
            *clean_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **kwargs
        )
        stdout, stderr = await proc.communicate()
        return stdout, stderr
    except (NotImplementedError, AttributeError):
        def _sync_exec():
            cmd_list = [str(a) for a in clean_args]
            sub_kwargs = dict(kwargs)
            if sys.platform == "win32" and "creationflags" not in sub_kwargs:
                sub_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            res = subprocess.run(cmd_list, capture_output=True, **sub_kwargs)
            return res.stdout, res.stderr
        return await asyncio.to_thread(_sync_exec)

def is_valid_video_file(filename: str) -> bool:
    """Check if a given path corresponds to an existing non-empty video file.

    Args:
        filename: Path to the target video file.

    Returns:
        True if the file exists, has a non-zero size, and matches allowed video extensions.
    """
    if not filename or str(filename).strip().startswith("-"):
        return False
    clean = str(filename).strip()
    base_dir_abs = os.path.realpath(os.path.abspath(str(BASE_DIR)))
    target_abs = os.path.realpath(os.path.abspath(os.path.join(base_dir_abs, clean) if not os.path.isabs(clean) else clean))
    if not target_abs.startswith(base_dir_abs + os.sep):
        return False
    if not os.path.isfile(target_abs):
        return False
    try:
        if os.path.getsize(target_abs) == 0:
            return False
    except OSError:
        return False
    ext = Path(target_abs).suffix.lower().lstrip(".")
    return ext in SERVER_CONFIG["ALLOWED_EXTENSIONS"]["video"]

def is_valid_image_file(filename: str) -> bool:
    """Check if a given path corresponds to an existing non-empty image file.

    Args:
        filename: Path to the target image file.

    Returns:
        True if the file exists, has a non-zero size, and matches allowed image extensions.
    """
    if not filename or str(filename).strip().startswith("-"):
        return False
    clean = str(filename).strip()
    base_dir_abs = os.path.realpath(os.path.abspath(str(BASE_DIR)))
    target_abs = os.path.realpath(os.path.abspath(os.path.join(base_dir_abs, clean) if not os.path.isabs(clean) else clean))
    if not target_abs.startswith(base_dir_abs + os.sep):
        return False
    if not os.path.isfile(target_abs):
        return False
    try:
        if os.path.getsize(target_abs) == 0:
            return False
    except OSError:
        return False
    ext = Path(target_abs).suffix.lower().lstrip(".")
    return ext in SERVER_CONFIG["ALLOWED_EXTENSIONS"]["image"]

def is_valid_media_file(filename: str) -> bool:
    """Check if a given path corresponds to an existing non-empty video or image file."""
    return is_valid_video_file(filename) or is_valid_image_file(filename)

def _safe_resolve(raw: str, allowed_subdirs: list) -> str:
    """Resolve a user-supplied relative or absolute path and ensure it stays inside BASE_DIR.

    Args:
        raw: User-supplied path string.
        allowed_subdirs: List of allowable workspace subdirectory prefixes.

    Returns:
        Normalised relative path with forward slashes, or empty string if rejected.
    """
    if not raw or str(raw).strip().startswith("-"):
        return ""
    raw_str = str(raw).strip().replace("\\", "/")
    if "\0" in raw_str:
        return ""

    base_dir_abs = os.path.realpath(os.path.abspath(str(BASE_DIR)))
    bname = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', os.path.basename(raw_str)).lstrip(".-")

    # 1. Try allowed subdirectories first (most common and safest)
    if bname and bname not in (".", ".."):
        for subdir in allowed_subdirs:
            sub_dir_abs = os.path.realpath(os.path.abspath(os.path.join(base_dir_abs, subdir)))
            candidate = os.path.realpath(os.path.abspath(os.path.join(sub_dir_abs, bname)))
            if not candidate.startswith(sub_dir_abs + os.sep):
                continue
            if os.path.isfile(candidate):
                return os.path.relpath(candidate, base_dir_abs).replace("\\", "/")

    # 2. Try direct relative path inside BASE_DIR
    target_path = os.path.realpath(os.path.abspath(os.path.join(base_dir_abs, raw_str)))
    if not target_path.startswith(base_dir_abs + os.sep):
        return ""
    if os.path.isfile(target_path):
        return os.path.relpath(target_path, base_dir_abs).replace("\\", "/")

    return ""

def resolve_input_file(raw_input: str) -> str:
    """Safely resolve an input video file path within permitted directories.

    Args:
        raw_input: Raw video filename or relative path.

    Returns:
        Normalised relative path within the workspace, or empty string.
    """
    return _safe_resolve(raw_input, ["data/input/videos", "data/runtime/work", "samples"])

def resolve_runtime_file(raw_path: str, allowed_subdirs: Optional[list] = None) -> str:
    """Safely resolve an internal runtime file path (work/temp/logs) within workspace.

    Args:
        raw_path: Target filename or workspace-relative path.
        allowed_subdirs: Optional list of permitted subdirectories (defaults to runtime dirs).

    Returns:
        Workspace-relative path if valid and exists within allowed boundaries, else empty string.
    """
    subdirs = allowed_subdirs or ["data/runtime/work", "data/runtime/temp", "data/runtime/logs", "data/input/videos", "data/input/gps"]
    return _safe_resolve(raw_path, subdirs)

def safe_runtime_write_path(filename: str, subdir: str = "data/runtime/work") -> str:
    """Generate a sanitized workspace-relative path for writing files in runtime directories.

    Args:
        filename: Target filename.
        subdir: Subdirectory under workspace (e.g. data/runtime/work, data/runtime/temp).

    Returns:
        Sanitized workspace-relative path within the target subdirectory, or empty string.
    """
    if not filename or str(filename).strip().startswith("-") or "\0" in str(filename):
        return ""
    bname = os.path.basename(str(filename).strip())
    bname = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', bname).lstrip(".-")
    if not bname or bname in (".", ".."):
        return ""
    base_dir_abs = os.path.realpath(os.path.abspath(str(BASE_DIR)))
    sub_dir_abs = os.path.realpath(os.path.abspath(os.path.join(base_dir_abs, subdir)))
    target_path = os.path.realpath(os.path.abspath(os.path.join(sub_dir_abs, bname)))
    if not target_path.startswith(sub_dir_abs + os.sep):
        return ""
    return os.path.relpath(target_path, base_dir_abs).replace("\\", "/")

def resolve_output_file(raw_output: str) -> str:
    """Resolve an output video path, routing to data/output/ or data/runtime/work/.

    Args:
        raw_output: Desired output filename or path.

    Returns:
        Sanitized workspace-relative target path.
    """
    if not raw_output or str(raw_output).strip().startswith("-"):
        return ""
    raw = str(raw_output).strip().replace("\\", "/")
    if "\0" in raw:
        return ""
    bname = os.path.basename(raw)
    if not bname or bname in (".", ".."):
        return ""

    bname = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', bname)
    sub = "data/output" if (raw.startswith("data/output/") or raw.startswith("data/output")) else "data/runtime/work"

    base_dir_abs = os.path.realpath(os.path.abspath(str(BASE_DIR)))
    sub_dir_abs = os.path.realpath(os.path.abspath(os.path.join(base_dir_abs, sub)))
    target_path = os.path.realpath(os.path.abspath(os.path.join(sub_dir_abs, bname)))

    if not target_path.startswith(sub_dir_abs + os.sep):
        return ""
    return os.path.relpath(target_path, base_dir_abs).replace("\\", "/")

def safe_job_id(job_id_raw: Any) -> str:
    """Sanitize and validate a job identifier to prevent path injection.

    Returns:
        Safe alphanumeric job ID string.
    """
    clean = re.sub(r'[^a-zA-Z0-9_\-]', '', str(job_id_raw or '')).strip()
    if not clean:
        import uuid
        clean = f"job_{uuid.uuid4().hex[:12]}"
    return clean

def get_status_file_path(job_id: Any) -> str:
    """Return verified safe path to status JSON file within data/runtime/temp."""
    jid = safe_job_id(job_id)
    base_dir_abs = os.path.realpath(os.path.abspath(str(BASE_DIR)))
    temp_dir_abs = os.path.realpath(os.path.abspath(os.path.join(base_dir_abs, "data", "runtime", "temp")))
    target = os.path.realpath(os.path.abspath(os.path.join(temp_dir_abs, f"status_{jid}.json" if jid != "default" else "status.json")))
    if not target.startswith(temp_dir_abs + os.sep):
        return "data/runtime/temp/status.json"
    return os.path.relpath(target, base_dir_abs).replace("\\", "/")

def get_log_file_path(job_id: Any) -> str:
    """Return verified safe path to log file within data/runtime/logs."""
    jid = safe_job_id(job_id)
    base_dir_abs = os.path.realpath(os.path.abspath(str(BASE_DIR)))
    logs_dir_abs = os.path.realpath(os.path.abspath(os.path.join(base_dir_abs, "data", "runtime", "logs")))
    target = os.path.realpath(os.path.abspath(os.path.join(logs_dir_abs, f"pipeline_{jid}.log" if jid != "default" else "pipeline.log")))
    if not target.startswith(logs_dir_abs + os.sep):
        return "data/runtime/logs/pipeline.log"
    return os.path.relpath(target, base_dir_abs).replace("\\", "/")

def resolve_nadir_logo(raw_logo: str) -> str:
    """Safely resolve a nadir patch image path within data/input/nadir/.

    Args:
        raw_logo: Logo filename or relative path.

    Returns:
        Workspace-relative path to the nadir logo file, or empty string.
    """
    return _safe_resolve(raw_logo, ["data/input/nadir"])

def resolve_gpx_file(raw_gpx: str) -> str:
    """Safely resolve a GPX telemetry file path within data/input/gps/.

    Args:
        raw_gpx: GPX filename or relative path.

    Returns:
        Workspace-relative path to the GPX file, or empty string.
    """
    return _safe_resolve(raw_gpx, ["data/input/gps"])

def resolve_video_version(m: str, want_vr: bool) -> str:
    """Resolve between standard and VR (_VR.mp4) versions of a video.

    Args:
        m: Path to a video file.
        want_vr: If True, request the _VR version; if False, request standard version.

    Returns:
        Path to the requested version if available, falling back to the input path.
    """
    if not m or not is_valid_video_file(m):
        return ""
    m = m.replace("\\", "/")
    is_vr = bool(re.search(r'_VR\.(mp4|MP4)$', m, re.IGNORECASE))
    if want_vr:
        if is_vr:
            return m
        vr_candidate = re.sub(r'\.(mp4|MP4)$', r'_VR.\1', m, flags=re.IGNORECASE)
        if is_valid_video_file(vr_candidate):
            return vr_candidate.replace("\\", "/")
        return m
    else:
        if not is_vr:
            return m
        normal_candidate = re.sub(r'_VR\.(mp4|MP4)$', r'.\1', m, flags=re.IGNORECASE)
        if is_valid_video_file(normal_candidate):
            return normal_candidate.replace("\\", "/")
        return m

def sanitize_preset_filename(name: str) -> str:
    """Sanitize preset filename to prevent directory traversal and append .json extension.

    Args:
        name: User-supplied preset name or filename.

    Returns:
        Sanitized filename with alphanumeric and underscore characters ending in .json.
    """
    name = os.path.basename(name.strip())
    name = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', name)
    if not name.lower().endswith('.json'):
        name += '.json'
    return name

# ── Dynamic Dropdowns HTML Generators ─────────────────────────────────────────

def get_input_video_options_html() -> str:
    """Generate HTML <option> tags for media files (videos and photos) found in data/input/videos/.

    Returns:
        HTML string containing sorted option elements.
    """
    patterns = [
        "data/input/videos/*.MP4", "data/input/videos/*.mp4",
        "data/input/videos/*.insv", "data/input/videos/*.INSV",
        "data/input/videos/*.mov", "data/input/videos/*.MOV",
        "data/input/videos/*.mkv", "data/input/videos/*.MKV",
        "data/input/videos/*.jpg", "data/input/videos/*.JPG",
        "data/input/videos/*.jpeg", "data/input/videos/*.JPEG",
        "data/input/videos/*.png", "data/input/videos/*.PNG",
        "data/input/videos/*.webp", "data/input/videos/*.WEBP"
    ]
    files = []
    for p in patterns:
        files.extend(glob.glob(p))
    files = list(set([f.replace("\\", "/") for f in files]))
    files.sort(key=lambda f: os.path.getmtime(f) if os.path.exists(f) else 0, reverse=True)
    
    if not files:
        return '<option value="">No media files found in data/input/videos/ folder</option>'
    
    options = []
    for file in files:
        ext = Path(file).suffix.lower().lstrip(".")
        is_photo = ext in SERVER_CONFIG["ALLOWED_EXTENSIONS"]["image"]
        display_label = f"[PHOTO] {file}" if is_photo else f"[VIDEO] {file}"
        options.append(f'<option value="{html.escape(file)}">{html.escape(display_label)}</option>')
    return "\n".join(options)

def get_nadir_logo_options_html() -> str:
    """Generate HTML <option> tags for nadir logo images in data/input/nadir/.

    Returns:
        HTML string containing sorted option elements.
    """
    patterns = [
        "data/input/nadir/*.png", "data/input/nadir/*.jpg", "data/input/nadir/*.jpeg"
    ]
    logos = []
    for p in patterns:
        logos.extend(glob.glob(p))
    logos = list(set([l.replace("\\", "/") for l in logos]))
    logos.sort(key=lambda s: [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', s)])
    
    if not logos:
        return '<option value="">No logos found in data/input/nadir/ folder</option>'

    options = []
    for logo in logos:
        selected = " selected" if os.path.basename(logo).lower() == "logo_generic.png" else ""
        options.append(f'<option value="{html.escape(logo)}"{selected}>{html.escape(logo)}</option>')
    return "\n".join(options)

def get_gpx_options_html() -> str:
    """Generate HTML <option> tags for GPX telemetry files in data/input/gps/.

    Returns:
        HTML string containing sorted option elements.
    """
    patterns = ["data/input/gps/*.gpx", "data/input/gps/*.GPX"]
    gpx_files = []
    for p in patterns:
        gpx_files.extend(glob.glob(p))
    gpx_files = list(set([g.replace("\\", "/") for g in gpx_files]))
    gpx_files.sort(key=lambda s: [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', s)])
    
    if not gpx_files:
        return '<option value="">No .gpx files found in data/input/gps/ folder</option>'
    
    options = [f'<option value="{html.escape(g)}">{html.escape(g)}</option>' for g in gpx_files]
    return "\n".join(options)

def get_input_videos_list() -> list:
    """Scan and list available input videos and photos in data/input/videos/.

    Returns:
        List of dictionaries with 'value', 'text', and 'is_photo' keys sorted by modification time.
    """
    patterns = [
        "data/input/videos/*.MP4", "data/input/videos/*.mp4",
        "data/input/videos/*.insv", "data/input/videos/*.INSV",
        "data/input/videos/*.mov", "data/input/videos/*.MOV",
        "data/input/videos/*.mkv", "data/input/videos/*.MKV",
        "data/input/videos/*.jpg", "data/input/videos/*.JPG",
        "data/input/videos/*.jpeg", "data/input/videos/*.JPEG",
        "data/input/videos/*.png", "data/input/videos/*.PNG",
        "data/input/videos/*.webp", "data/input/videos/*.WEBP"
    ]
    files = []
    for p in patterns:
        files.extend(glob.glob(p))
    files = list(set([f.replace("\\", "/") for f in files]))
    files.sort(key=lambda f: os.path.getmtime(f) if os.path.exists(f) else 0, reverse=True)
    result = []
    for f in files:
        ext = Path(f).suffix.lower().lstrip(".")
        is_photo = ext in SERVER_CONFIG["ALLOWED_EXTENSIONS"]["image"]
        display_text = f"[PHOTO] {f}" if is_photo else f"[VIDEO] {f}"
        result.append({"value": f, "text": display_text, "is_photo": is_photo})
    return result

def get_nadir_logos_list() -> list:
    """Scan and list available nadir logos in data/input/nadir/.

    Returns:
        List of dictionaries with 'value', 'text', and 'selected' keys.
    """
    patterns = [
        "data/input/nadir/*.png", "data/input/nadir/*.jpg", "data/input/nadir/*.jpeg"
    ]
    logos = []
    for p in patterns:
        logos.extend(glob.glob(p))
    logos = list(set([l.replace("\\", "/") for l in logos]))
    logos.sort(key=lambda s: [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', s)])
    return [{"value": l, "text": l, "selected": os.path.basename(l).lower() == "logo_generic.png"} for l in logos]

def get_gpx_files_list() -> list:
    """Scan and list available GPX telemetry files in data/input/gps/.

    Returns:
        List of dictionaries with 'value' and 'text' keys.
    """
    patterns = ["data/input/gps/*.gpx", "data/input/gps/*.GPX"]
    gpx_files = []
    for p in patterns:
        gpx_files.extend(glob.glob(p))
    gpx_files = list(set([g.replace("\\", "/") for g in gpx_files]))
    gpx_files.sort(key=lambda s: [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', s)])
    return [{"value": g, "text": g} for g in gpx_files]

# PERF-01: TTL cache — rebuilt at most once every 5 seconds to avoid disk I/O + glob on every poll
_dashboard_cache: tuple[str, float] = ("", 0.0)

def render_dashboard_html() -> str:
    """Read index.html, inject dynamic dropdown options and cache-busting timestamps.

    Returns:
        Rendered HTML page string.
    """
    global _dashboard_cache
    _cached_html, _cached_at = _dashboard_cache
    if _cached_html and (time.time() - _cached_at) < 5.0:
        return _cached_html

    template_path = BASE_DIR / "index.html"
    with open(template_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    # Capture timestamp once so both assets share the same cache-bust value
    _ts = int(time.time())
    # 1. Stylesheet cache-bust
    html_content = html_content.replace('href="styles.css"', f'href="styles.css?v={_ts}"')
    # 2. App.js cache-bust
    html_content = html_content.replace('src="app.js"', f'src="app.js?v={_ts}"')

    # 3. Input video select options
    input_opts = get_input_video_options_html()
    html_content = html_content.replace('<!-- INPUT_OPTIONS -->', input_opts)

    # 4. Nadir logo options
    nadir_opts = get_nadir_logo_options_html()
    html_content = html_content.replace('<!-- NADIR_OPTIONS -->', nadir_opts)

    # 5. GPX options
    gpx_opts = get_gpx_options_html()
    html_content = html_content.replace('<!-- GPX_OPTIONS -->', gpx_opts)

    _dashboard_cache = (html_content, time.time())
    return html_content
