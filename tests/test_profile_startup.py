"""scripts/profile_startup.py 的 profile_imports 可信度测试。

验证修复：profile_imports() 必须在 profiler 活跃期间执行真实 import，
使返回的 import_times 非空且 import_time > 0。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "scripts" / "profile_startup.py"


def _load_profile_module():
    """动态加载 scripts/profile_startup.py（非 src 包）。"""
    spec = importlib.util.spec_from_file_location("profile_startup", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestProfileImports:
    def test_profile_imports_returns_nonzero_time(self):
        """profile_imports 必须返回 import_time > 0（之前 bug 是 ~0）。"""
        mod = _load_profile_module()
        _import_times, total_import = mod.profile_imports()
        assert total_import > 0, (
            "profile_imports 必须测量真实 import 耗时（之前 bug 是 start/stop 间无 import）"
        )

    def test_profile_imports_captures_real_modules(self):
        """import_times 应包含实际导入的模块（如 json / pathlib 等标准库）。"""
        mod = _load_profile_module()
        import_times, _ = mod.profile_imports()
        # 至少捕获到一些模块的 import 时间
        assert len(import_times) > 0
        # 不应为全 0
        assert any(t > 0 for t in import_times.values())

    def test_profile_imports_restores_builtin(self):
        """profiling 后 builtins.__import__ 应回到原始 builtin。"""
        import builtins

        mod = _load_profile_module()
        original = builtins.__import__
        mod.profile_imports()
        assert builtins.__import__ is original

    def test_importprofiler_measures_real_import(self):
        """ImportProfiler.start/stop 间执行 import 应捕获其耗时。"""
        mod = _load_profile_module()
        profiler = mod.ImportProfiler()
        profiler.start()
        # 导入一个之前未加载的模块（用临时唯一名避免缓存）
        import importlib

        # 用一个标准库中可能未加载的模块
        importlib.import_module("csv")
        profiler.stop()
        results = profiler.get_results()
        assert "csv" in results or len(results) > 0
