import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
"""Centralized binary discovery and capability validation.

Resolves system executables (FFmpeg, FFprobe, ExifTool) and validates
Python runtime requirements, guaranteeing that vendor-bundled builds
(such as Kdenlive's FFmpeg lacking libvidstab) are disambiguated and
that high-capability builds are prioritized across all child processes.
"""

import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

WIN_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _parse_ffmpeg_info(bin_path: Path) -> Optional[Dict[str, Any]]:
    """Execute ffmpeg -version and extract version and capability flags."""
    if not bin_path.is_file():
        return None
    try:
        res = subprocess.run(
            [str(bin_path), "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=2.5,
            creationflags=WIN_NO_WINDOW
        )
        if res.returncode != 0 and "ffmpeg version" not in res.stdout:
            return None

        stdout = res.stdout
        v_match = re.search(r"ffmpeg version\s+([^\s]+)", stdout)
        version_str = v_match.group(1) if v_match else "unknown"
        has_libvidstab = ("--enable-libvidstab" in stdout) or ("libvidstab" in stdout)
        has_nvenc = ("nvenc" in stdout) or ("--enable-nvenc" in stdout)

        # Parse numeric version components for comparison (e.g. 9.0.1 -> (9, 0, 1))
        num_match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", version_str)
        ver_tuple = (
            int(num_match.group(1)),
            int(num_match.group(2)),
            int(num_match.group(3) or 0)
        ) if num_match else (0, 0, 0)

        # Identify vendor-bundled builds that should be deprioritized
        path_str = str(bin_path).lower()
        is_vendor = any(v in path_str for v in ["kdenlive", "mediacoder", "shotcut", "audacity"])

        return {
            "path": str(bin_path),
            "version": version_str,
            "version_tuple": ver_tuple,
            "libvidstab": has_libvidstab,
            "nvenc": has_nvenc,
            "is_vendor": is_vendor
        }
    except Exception:
        return None


def resolve_ffmpeg() -> Dict[str, Any]:
    """Discover, validate, and select the optimal FFmpeg binary.

    Prioritizes:
    1. Explicit override via FFMPEG_PATH environment variable.
    2. Builds with libvidstab enabled (required for optical flow stabilization).
    3. Standalone full builds (e.g., Gyan.FFmpeg) over vendor-bundled tools (e.g., Kdenlive).
    4. Higher semantic versions.

    Ensures the selected binary's directory is prepended to os.environ['PATH'].
    """
    candidates: List[Path] = []

    # 1. Environment variable override
    env_override = os.environ.get("FFMPEG_PATH")
    if env_override:
        p = Path(env_override)
        if p.is_file():
            candidates.append(p)
        elif p.is_dir():
            target_bin = p / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
            if target_bin.is_file():
                candidates.append(target_bin)

    # 2. Known Winget package locations
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        winget_pkgs = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
        if winget_pkgs.is_dir():
            for f in winget_pkgs.glob("**/bin/ffmpeg.exe"):
                candidates.append(f)

    # 3. Known system locations
    system_roots = [
        Path("C:/ffmpeg/bin/ffmpeg.exe"),
        Path("C:/Program Files/ffmpeg/bin/ffmpeg.exe"),
        Path(local_app_data) / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe"
    ]
    for sr in system_roots:
        if sr.is_file():
            candidates.append(sr)

    # 4. Inquire PATH entries
    which_bin = shutil.which("ffmpeg")
    if which_bin:
        candidates.append(Path(which_bin))

    if sys.platform == "win32":
        try:
            where_out = subprocess.check_output(
                ["where.exe", "ffmpeg"],
                stderr=subprocess.DEVNULL,
                creationflags=WIN_NO_WINDOW
            ).decode("utf-8", errors="ignore")
            for line in where_out.splitlines():
                clean_line = line.strip()
                if clean_line:
                    candidates.append(Path(clean_line))
        except Exception:
            pass

    # Deduplicate candidate paths
    seen = set()
    unique_candidates: List[Path] = []
    for c in candidates:
        try:
            resolved = c.resolve()
            if resolved not in seen and resolved.is_file():
                seen.add(resolved)
                unique_candidates.append(resolved)
        except Exception:
            continue

    inspected = []
    for c in unique_candidates:
        info = _parse_ffmpeg_info(c)
        if info:
            inspected.append(info)

    if not inspected:
        return {
            "available": False,
            "path": None,
            "version": None,
            "libvidstab": False,
            "status": "error",
            "message": "FFmpeg not detected on system.",
            "candidates_found": []
        }

    # Sorting key:
    # 1. Has libvidstab (True > False)
    # 2. Not a vendor build (True > False)
    # 3. Higher version tuple
    def sort_score(item: Dict[str, Any]) -> Tuple[int, int, Tuple[int, int, int]]:
        return (
            1 if item["libvidstab"] else 0,
            0 if item["is_vendor"] else 1,
            item["version_tuple"]
        )

    inspected.sort(key=sort_score, reverse=True)
    best = inspected[0]

    # Prepend best binary folder to PATH to ensure subprocess invocations stay consistent
    best_dir = str(Path(best["path"]).parent)
    current_path = os.environ.get("PATH", "")
    if not current_path.startswith(best_dir):
        os.environ["PATH"] = f"{best_dir}{os.pathsep}{current_path}"

    status = "ok" if best["libvidstab"] else "warning"
    vendor_note = " (vendor build)" if best["is_vendor"] else ""
    msg = f"FFmpeg {best['version']}{vendor_note} operational"
    if best["libvidstab"]:
        msg += " (with libvidstab)"
    else:
        msg += " without libvidstab (optical flow stabilization disabled)"

    return {
        "available": True,
        "path": best["path"],
        "version": best["version"],
        "libvidstab": best["libvidstab"],
        "nvenc": best.get("nvenc", False),
        "status": status,
        "message": msg,
        "candidates_found": [
            {
                "path": i["path"],
                "version": i["version"],
                "libvidstab": i["libvidstab"],
                "is_vendor": i["is_vendor"]
            }
            for i in inspected
        ]
    }


def resolve_ffprobe(ffmpeg_path: Optional[str] = None) -> Dict[str, Any]:
    """Resolve corresponding FFprobe binary, prioritizing siblings of the active FFmpeg."""
    candidate_paths: List[Path] = []

    # 1. Check sibling in same directory as active FFmpeg
    if ffmpeg_path:
        probe_sibling = Path(ffmpeg_path).parent / ("ffprobe.exe" if sys.platform == "win32" else "ffprobe")
        if probe_sibling.is_file():
            candidate_paths.append(probe_sibling)

    # 2. Check environment variable override
    env_probe = os.environ.get("FFPROBE_PATH")
    if env_probe:
        candidate_paths.append(Path(env_probe))

    # 3. Check system PATH
    which_probe = shutil.which("ffprobe")
    if which_probe:
        candidate_paths.append(Path(which_probe))

    for p in candidate_paths:
        if not p.is_file():
            continue
        try:
            res = subprocess.run(
                [str(p), "-version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=2.0,
                creationflags=WIN_NO_WINDOW
            )
            v_match = re.search(r"ffprobe version\s+([^\s]+)", res.stdout)
            version_str = v_match.group(1) if v_match else "operational"
            return {
                "available": True,
                "path": str(p),
                "version": version_str,
                "status": "ok",
                "message": f"FFprobe {version_str} operational."
            }
        except Exception:
            continue

    return {
        "available": False,
        "path": None,
        "version": None,
        "status": "error",
        "message": "FFprobe not detected on system."
    }


def resolve_exiftool() -> Dict[str, Any]:
    """Resolve ExifTool binary across environment override, local app data, and PATH."""
    candidates: List[Path] = []

    env_exif = os.environ.get("EXIFTOOL_PATH")
    if env_exif:
        candidates.append(Path(env_exif))

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data and sys.platform.startswith("win"):
        candidates.extend([
            Path(local_app_data) / "Programs" / "ExifTool" / "ExifTool.exe",
            Path(local_app_data) / "Programs" / "ExifTool" / "exiftool.exe",
            Path("C:/Program Files/ExifTool/exiftool.exe"),
            Path("C:/Program Files (x86)/ExifTool/exiftool.exe")
        ])

    which_exif = shutil.which("exiftool")
    if which_exif:
        candidates.append(Path(which_exif))

    for p in candidates:
        if not p.is_file():
            continue
        try:
            res = subprocess.run(
                [str(p), "-ver"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=2.0,
                creationflags=WIN_NO_WINDOW
            )
            ver_text = res.stdout.strip()
            return {
                "available": True,
                "path": str(p),
                "version": ver_text,
                "status": "ok",
                "message": f"ExifTool {ver_text} operational."
            }
        except Exception:
            continue

    return {
        "available": False,
        "path": None,
        "version": None,
        "status": "error",
        "message": "ExifTool not detected in system PATH."
    }


def check_python_environment() -> Dict[str, Any]:
    """Validate Python runtime version and ensure required core packages are importable."""
    v_major, v_minor = sys.version_info.major, sys.version_info.minor
    ver_str = f"{v_major}.{v_minor}.{sys.version_info.micro}"

    # Verify key project imports
    missing_pkgs: List[str] = []
    for pkg in ["cv2", "numpy", "scipy", "fastapi", "uvicorn", "psutil"]:
        try:
            __import__(pkg)
        except ImportError:
            missing_pkgs.append(pkg)

    if v_major < 3 or (v_major == 3 and v_minor < 10):
        status = "error"
        msg = f"Python {ver_str} is unsupported. Python 3.10+ required (Python 3.12 recommended)."
    elif missing_pkgs:
        status = "error"
        msg = f"Python {ver_str} missing required packages: {', '.join(missing_pkgs)}."
    elif v_minor >= 12:
        status = "ok"
        msg = f"Python {ver_str} runtime active (optimized)."
    else:
        status = "ok"
        msg = f"Python {ver_str} runtime active."

    return {
        "version": ver_str,
        "executable": sys.executable,
        "status": status,
        "missing_packages": missing_pkgs,
        "message": msg
    }
