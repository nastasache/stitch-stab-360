import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
"""Centralized discovery and management of fast temporary storage and RAM disks.

Provides fallback-safe path resolution for ephemeral pipeline files (.trf motion
vectors, dynamic sendcmd scripts, seam masks) prioritizing in-memory filesystems
(/dev/shm on Linux, RAMDISK_PATH / FAST_TEMP_DIR on Windows/macOS) to minimize SSD
write endurance wear and eliminate filesystem locking latency.

Strictly complies with the repository's Absolute No-Deletion Policy.
"""

from pathlib import Path
from typing import Optional


def get_ram_temp_dir(subfolder: Optional[str] = None) -> str:
    """Resolve the fastest available temporary storage directory.

    Evaluation hierarchy:
    1. Explicit environment variable: RAMDISK_PATH or FAST_TEMP_DIR.
    2. Linux POSIX shared-memory RAM disk: /dev/shm (if writable).
    3. Windows common mounted RAM disk drives (e.g. R:\\temp, Z:\\temp if present).
    4. Project-local temporary fallback: data/runtime/temp.

    Args:
        subfolder: Optional subfolder name to append and ensure exists.

    Returns:
        str: Absolute or normalized path to the writable temporary directory.
    """
    candidates = []

    # 1. Environment variable overrides
    env_ram = os.environ.get("RAMDISK_PATH") or os.environ.get("FAST_TEMP_DIR")
    if env_ram:
        candidates.append(Path(env_ram))

    # 2. Linux /dev/shm shared memory tmpfs
    if sys.platform.startswith("linux"):
        shm_path = Path("/dev/shm")
        if shm_path.is_dir() and os.access(str(shm_path), os.W_OK):
            candidates.append(shm_path / "360_temp")

    # 3. Windows RAM disk mount points
    if sys.platform == "win32":
        for win_drive in ["R:\\temp", "Z:\\temp", "T:\\temp"]:
            drive_path = Path(win_drive)
            if drive_path.is_dir() and os.access(str(drive_path), os.W_OK):
                candidates.append(drive_path / "360_temp")
                break

    # 4. Standard project fallback
    default_fallback = Path("data/runtime/temp")
    candidates.append(default_fallback)

    resolved_base: Optional[Path] = None
    for cand in candidates:
        try:
            target = cand / subfolder if subfolder else cand
            target.mkdir(parents=True, exist_ok=True)
            # Verify actual writability
            test_probe = target / f".probe_{os.getpid()}"
            with open(test_probe, "w", encoding="utf-8") as f:
                f.write("ok")
            # Note: Never delete test_probe per absolute no-deletion policy
            resolved_base = target
            break
        except Exception:
            continue

    if resolved_base is None:
        target = default_fallback / subfolder if subfolder else default_fallback
        target.mkdir(parents=True, exist_ok=True)
        resolved_base = target

    return str(resolved_base)


def is_ram_disk(path: str) -> bool:
    """Check if the provided path is located in a recognized RAM disk or tmpfs."""
    try:
        p_str = str(Path(path)).replace('\\', '/').lower()
        if "/dev/shm" in p_str:
            return True
        env_ram = (os.environ.get("RAMDISK_PATH") or "").replace('\\', '/').lower()
        if env_ram and env_ram in p_str:
            return True
        if sys.platform == "win32" and any(p_str.startswith(d) for d in ["r:/", "z:/", "t:/"]):
            return True
    except Exception:
        pass
    return False
