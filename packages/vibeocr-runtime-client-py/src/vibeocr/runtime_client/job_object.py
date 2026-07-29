"""Windows Job Object guard used by the bootstrap process client.

This stdlib-only copy deliberately lives in the Protocol client distribution:
the bootstrap client must not import Backend implementation modules.
"""

import ctypes
import logging
import subprocess
import sys
from ctypes import wintypes

logger = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"

JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000080
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JobObjectExtendedLimitInformation = 9
PROCESS_SET_QUOTA = 0x0100
PROCESS_TERMINATE = 0x0001


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
        ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.POINTER(wintypes.ULONG)),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _get_kernel32():
    return ctypes.windll.kernel32  # type: ignore[attr-defined]


class JobObjectGuard:
    """Bind child processes to a kill-on-close Windows Job Object."""

    def __init__(self, name: str | None = None) -> None:
        self._name = name
        self._handle: int | None = None
        if _IS_WINDOWS:
            self._create_job()

    def _create_job(self) -> None:
        try:
            kernel32 = _get_kernel32()
            name_c = ctypes.c_wchar_p(self._name) if self._name else None
            handle = kernel32.CreateJobObjectW(None, name_c)
            if not handle:
                logger.warning("CreateJobObjectW failed; orphan protection disabled")
                return
            info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = (
                JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_BREAKAWAY_OK
            )
            ok = kernel32.SetInformationJobObject(
                handle,
                JobObjectExtendedLimitInformation,
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
            if not ok:
                kernel32.CloseHandle(handle)
                logger.warning(
                    "SetInformationJobObject failed; orphan protection disabled"
                )
                return
            self._handle = handle
        except Exception:
            logger.warning("Job Object setup failed", exc_info=True)

    def assign_from_popen(self, popen: subprocess.Popen) -> bool:
        if not _IS_WINDOWS or self._handle is None:
            return False
        return self._assign_pid(popen.pid)

    def _assign_pid(self, pid: int) -> bool:
        try:
            kernel32 = _get_kernel32()
            process = kernel32.OpenProcess(
                PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid
            )
            if not process:
                return False
            try:
                return bool(
                    kernel32.AssignProcessToJobObject(self._handle, process)
                )
            finally:
                kernel32.CloseHandle(process)
        except Exception:
            logger.warning("AssignProcessToJobObject failed", exc_info=True)
            return False

    def close(self) -> None:
        if not _IS_WINDOWS or self._handle is None:
            return
        try:
            _get_kernel32().CloseHandle(self._handle)
        except Exception:
            logger.warning("CloseHandle failed", exc_info=True)
        finally:
            self._handle = None

    def __enter__(self) -> "JobObjectGuard":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
