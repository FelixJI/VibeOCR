"""Windows Job Object 守卫。

主进程退出（含 os._exit / 段错误 / 任务管理器强杀）时，内核连带终止
被绑定的子进程，回收 GPU 显存和共享内存。

非 Windows 平台为 no-op 兼容实现。
"""

import logging
import subprocess
import sys

logger = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"


class JobObjectGuard:
    """Windows Job Object 守卫。

    主进程所有 Job 句柄关闭时，内核终止 Job 内全部进程
    （JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE）。

    仅 Windows 生效；其他平台为 no-op。
    """

    def __init__(self, name: str | None = None) -> None:
        self._name = name
        self._handle: int | None = None  # Windows: HANDLE；其他平台: None

        if not _IS_WINDOWS:
            return

        # Windows 实现在 Task 2 补充
        self._create_job()

    def _create_job(self) -> None:
        """创建并配置 Job Object（Windows）。Task 2 实现。"""
        # 占位：Task 2 填充真实 ctypes 调用

    def assign_from_popen(self, popen: subprocess.Popen) -> bool:
        """把 popen 启动的子进程加入本 Job。

        Returns:
            是否成功绑定（False 仅表示降级，不抛异常）。
        """
        if not _IS_WINDOWS or self._handle is None:
            return False
        # Windows 实现在 Task 3 补充
        return self._assign_pid(popen.pid)

    def _assign_pid(self, pid: int) -> bool:
        """通过 pid 绑定进程（Windows）。Task 3 实现。"""
        return False

    def close(self) -> None:
        """关闭 Job 句柄。幂等。"""
        if not _IS_WINDOWS or self._handle is None:
            return
        # Windows 实现在 Task 4 补充

    def __enter__(self) -> "JobObjectGuard":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
