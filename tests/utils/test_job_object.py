"""JobObjectGuard 单元测试。

验证 Windows Job Object 守卫的创建、绑定、关闭、降级行为。
所有 Windows 内核调用均通过 mock 验证，不依赖真实 OS 行为。
"""

import ctypes
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from vibeocr.utils.job_object import JobObjectGuard


class TestJobObjectGuardNonWindows:
    """非 Windows 平台：所有方法 no-op，不抛异常。

    注意：patch 的是 _IS_WINDOWS（运行期分支依据），而非 sys.platform
    （后者仅在模块导入时被读取一次，运行期 patch 无效）。
    """

    @patch("vibeocr.utils.job_object._IS_WINDOWS", False)
    def test_init_no_handle_on_linux(self):
        guard = JobObjectGuard()
        assert guard._handle is None

    @patch("vibeocr.utils.job_object._IS_WINDOWS", False)
    def test_assign_returns_false_on_linux(self):
        guard = JobObjectGuard()
        popen = MagicMock(spec=subprocess.Popen)
        popen.pid = 12345
        assert guard.assign_from_popen(popen) is False

    @patch("vibeocr.utils.job_object._IS_WINDOWS", False)
    def test_close_noop_on_linux(self):
        guard = JobObjectGuard()
        guard.close()  # 不抛异常
        guard.close()  # 幂等，二次安全

    @patch("vibeocr.utils.job_object._IS_WINDOWS", False)
    def test_context_manager_on_linux(self):
        with JobObjectGuard() as guard:
            assert guard._handle is None
        # 退出 with 不抛异常


class TestJobObjectGuardWindowsCreate:
    """Windows 平台：CreateJobObjectW 调用与 flag 配置。"""

    @patch("vibeocr.utils.job_object._IS_WINDOWS", True)
    @patch("vibeocr.utils.job_object.sys.platform", "win32")
    def test_create_calls_createjobobject_and_setinfo(self):
        """创建时调用 CreateJobObjectW + SetInformationJobObject。"""
        from vibeocr.utils.job_object import (
            JOB_OBJECT_LIMIT_BREAKAWAY_OK,
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        )

        fake_kernel = MagicMock()
        fake_kernel.CreateJobObjectW.return_value = 999  # 非 0 = 成功 HANDLE
        fake_kernel.SetInformationJobObject.return_value = 1  # 非 0 = 成功

        with patch(
            "vibeocr.utils.job_object.ctypes.windll",
            MagicMock(kernel32=fake_kernel),
        ):
            guard = JobObjectGuard(name="test_job")

        assert guard._handle == 999
        fake_kernel.CreateJobObjectW.assert_called_once()
        fake_kernel.SetInformationJobObject.assert_called_once()

    @patch("vibeocr.utils.job_object._IS_WINDOWS", True)
    @patch("vibeocr.utils.job_object.sys.platform", "win32")
    def test_create_sets_kill_on_job_close_flag(self):
        """SetInformationJobObject 的 LimitFlags 含 KILL_ON_JOB_CLOSE + BREAKAWAY_OK。"""
        captured = {}

        fake_kernel = MagicMock()
        fake_kernel.CreateJobObjectW.return_value = 888

        def capture_setinfo(hJob, info_class, info_ptr, len_):
            captured["info_ptr"] = info_ptr
            return 1

        fake_kernel.SetInformationJobObject.side_effect = capture_setinfo

        from vibeocr.utils.job_object import (
            JOBOBJECT_EXTENDED_LIMIT_INFORMATION as ExtInfo,
            JOB_OBJECT_LIMIT_BREAKAWAY_OK,
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        )

        with patch(
            "vibeocr.utils.job_object.ctypes.windll",
            MagicMock(kernel32=fake_kernel),
        ):
            guard = JobObjectGuard()

        # 从 ctypes 指针还原结构体读 LimitFlags
        ext = ctypes.cast(captured["info_ptr"], ctypes.POINTER(ExtInfo)).contents
        flags = ext.BasicLimitInformation.LimitFlags
        assert flags & JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        assert flags & JOB_OBJECT_LIMIT_BREAKAWAY_OK

    @patch("vibeocr.utils.job_object._IS_WINDOWS", True)
    @patch("vibeocr.utils.job_object.sys.platform", "win32")
    def test_create_failure_degrades_gracefully(self):
        """CreateJobObjectW 返回 0（失败）时降级：_handle=None，不抛异常。"""
        fake_kernel = MagicMock()
        fake_kernel.CreateJobObjectW.return_value = 0  # 失败

        with patch(
            "vibeocr.utils.job_object.ctypes.windll",
            MagicMock(kernel32=fake_kernel),
        ):
            guard = JobObjectGuard()

        assert guard._handle is None


class TestJobObjectGuardWindowsAssign:
    """Windows 平台：进程绑定路径。"""

    def _make_guard_with_handle(self, handle=777):
        """构造一个已成功创建（_handle 非 None）的 guard，跳过 _create_job。"""
        with (
            patch("vibeocr.utils.job_object._IS_WINDOWS", True),
            patch("vibeocr.utils.job_object.sys.platform", "win32"),
            patch.object(
                JobObjectGuard,
                "_create_job",
                lambda self: setattr(self, "_handle", handle),
            ),
        ):
            return JobObjectGuard()

    @patch("vibeocr.utils.job_object._IS_WINDOWS", True)
    @patch("vibeocr.utils.job_object.sys.platform", "win32")
    def test_assign_success_calls_open_assign_closehandle(self):
        """绑定成功：OpenProcess + AssignProcessToJobObject + CloseHandle(子进程句柄)。"""
        guard = self._make_guard_with_handle(handle=777)

        fake_kernel = MagicMock()
        fake_kernel.OpenProcess.return_value = 555  # 子进程句柄
        fake_kernel.AssignProcessToJobObject.return_value = 1  # 成功

        popen = MagicMock(spec=subprocess.Popen)
        popen.pid = 1234

        with patch(
            "vibeocr.utils.job_object.ctypes.windll",
            MagicMock(kernel32=fake_kernel),
        ):
            result = guard.assign_from_popen(popen)

        assert result is True
        fake_kernel.OpenProcess.assert_called_once()
        fake_kernel.AssignProcessToJobObject.assert_called_once_with(777, 555)
        fake_kernel.CloseHandle.assert_called_once_with(555)  # 关子进程句柄，非 Job 句柄

    @patch("vibeocr.utils.job_object._IS_WINDOWS", True)
    @patch("vibeocr.utils.job_object.sys.platform", "win32")
    def test_assign_openprocess_failure_returns_false(self):
        """OpenProcess 返回 0：返回 False，记 warning，不抛异常。"""
        guard = self._make_guard_with_handle(handle=777)

        fake_kernel = MagicMock()
        fake_kernel.OpenProcess.return_value = 0  # 失败

        popen = MagicMock(spec=subprocess.Popen)
        popen.pid = 1234

        with patch(
            "vibeocr.utils.job_object.ctypes.windll",
            MagicMock(kernel32=fake_kernel),
        ):
            result = guard.assign_from_popen(popen)

        assert result is False
        fake_kernel.AssignProcessToJobObject.assert_not_called()

    @patch("vibeocr.utils.job_object._IS_WINDOWS", True)
    @patch("vibeocr.utils.job_object.sys.platform", "win32")
    def test_assign_assignprocess_failure_returns_false(self):
        """AssignProcessToJobObject 返回 0：关子进程句柄，返回 False，不抛异常。"""
        guard = self._make_guard_with_handle(handle=777)

        fake_kernel = MagicMock()
        fake_kernel.OpenProcess.return_value = 555
        fake_kernel.AssignProcessToJobObject.return_value = 0  # 失败

        popen = MagicMock(spec=subprocess.Popen)
        popen.pid = 1234

        with patch(
            "vibeocr.utils.job_object.ctypes.windll",
            MagicMock(kernel32=fake_kernel),
        ):
            result = guard.assign_from_popen(popen)

        assert result is False
        fake_kernel.CloseHandle.assert_called_once_with(555)  # 仍清理子进程句柄
