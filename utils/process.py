import sys, os
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

"""Process management utilities for subprocess lifecycle and Windows job object binding."""

import ctypes
from ctypes import wintypes

_WINDOWS_JOB_HANDLE = None

def assign_to_job_object(module_name: str = ""):
    """Assign current process to a Windows Job Object configured with KILL_ON_JOB_CLOSE.

    Ensures child processes (e.g., FFmpeg workers, Python child scripts) automatically
    terminate when the parent process exits or crashes.

    Args:
        module_name: Optional calling module identifier for debug warnings.
    """
    global _WINDOWS_JOB_HANDLE
    if sys.platform != "win32" or _WINDOWS_JOB_HANDLE is not None:
        return
    try:
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ('ReadOperationCount', ctypes.c_uint64),
                ('WriteOperationCount', ctypes.c_uint64),
                ('OtherOperationCount', ctypes.c_uint64),
                ('ReadTransferCount', ctypes.c_uint64),
                ('WriteTransferCount', ctypes.c_uint64),
                ('OtherTransferCount', ctypes.c_uint64),
            ]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ('PerProcessUserTimeLimit', ctypes.c_int64),
                ('PerJobUserTimeLimit', ctypes.c_int64),
                ('LimitFlags', wintypes.DWORD),
                ('MinimumWorkingSetSize', ctypes.c_size_t),
                ('MaximumWorkingSetSize', ctypes.c_size_t),
                ('ActiveProcessLimit', wintypes.DWORD),
                ('Affinity', ctypes.c_size_t),
                ('PriorityClass', wintypes.DWORD),
                ('SchedulingClass', wintypes.DWORD),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ('BasicLimitInformation', JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ('IoInfo', IO_COUNTERS),
                ('ProcessMemoryLimit', ctypes.c_size_t),
                ('JobMemoryLimit', ctypes.c_size_t),
                ('PeakProcessMemoryLimit', ctypes.c_size_t),
                ('PeakJobMemoryLimit', ctypes.c_size_t),
            ]

        hJob = kernel32.CreateJobObjectW(None, None)
        if not hJob:
            return

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

        res = kernel32.SetInformationJobObject(
            hJob,
            9,  # JobObjectExtendedLimitInformation
            ctypes.byref(info),
            ctypes.sizeof(info)
        )
        if res:
            kernel32.AssignProcessToJobObject(hJob, kernel32.GetCurrentProcess())
            _WINDOWS_JOB_HANDLE = hJob
    except Exception as e:
        tag = f" in {module_name}" if module_name else ""
        print(f"[WARN] Failed to configure Windows Job Object{tag}: {e}", file=sys.stderr)
