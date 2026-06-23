"""JobObjectGuard 单元测试。

验证 Windows Job Object 守卫的创建、绑定、关闭、降级行为。
所有 Windows 内核调用均通过 mock 验证，不依赖真实 OS 行为。
"""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from vibeocr.utils.job_object import JobObjectGuard


class TestJobObjectGuardNonWindows:
    """非 Windows 平台：所有方法 no-op，不抛异常。"""

    @patch("vibeocr.utils.job_object.sys.platform", "linux")
    def test_init_no_handle_on_linux(self):
        guard = JobObjectGuard()
        assert guard._handle is None

    @patch("vibeocr.utils.job_object.sys.platform", "linux")
    def test_assign_returns_false_on_linux(self):
        guard = JobObjectGuard()
        popen = MagicMock(spec=subprocess.Popen)
        popen.pid = 12345
        assert guard.assign_from_popen(popen) is False

    @patch("vibeocr.utils.job_object.sys.platform", "linux")
    def test_close_noop_on_linux(self):
        guard = JobObjectGuard()
        guard.close()  # 不抛异常
        guard.close()  # 幂等，二次安全

    @patch("vibeocr.utils.job_object.sys.platform", "linux")
    def test_context_manager_on_linux(self):
        with JobObjectGuard() as guard:
            assert guard._handle is None
        # 退出 with 不抛异常
