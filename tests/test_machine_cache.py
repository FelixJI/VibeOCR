"""Tests for machine_cache module."""

import subprocess
from unittest.mock import MagicMock, patch

from vibeocr.machine_cache import CACHE_VERSION


class TestGenerateMachineId:
    """Tests for generate_machine_id function."""

    def test_generate_machine_id_returns_string(self):
        """Should return a non-empty string."""
        from vibeocr.machine_cache import generate_machine_id

        result = generate_machine_id()
        assert isinstance(result, str)
        assert len(result) == 64  # SHA256 produces 64 hex chars

    def test_generate_machine_id_is_consistent(self):
        """Should return the same value on multiple calls."""
        from vibeocr.machine_cache import generate_machine_id

        result1 = generate_machine_id()
        result2 = generate_machine_id()
        assert result1 == result2

    @patch("vibeocr.machine_cache.subprocess.run")
    def test_generate_machine_id_handles_subprocess_failure(self, mock_run):
        """Should still return a valid hash even if subprocess fails."""
        mock_run.side_effect = subprocess.SubprocessError("Command failed")

        from vibeocr.machine_cache import generate_machine_id

        result = generate_machine_id()
        assert isinstance(result, str)
        assert len(result) == 64


class TestCachePath:
    """Tests for cache path functions."""

    def test_get_cache_dir(self, tmp_path):
        """Should return .vibeocr directory path."""
        from vibeocr.machine_cache import get_cache_dir

        result = get_cache_dir(tmp_path)
        assert result == tmp_path / ".vibeocr"

    def test_get_cache_path(self, tmp_path):
        """Should return cache.json path."""
        from vibeocr.machine_cache import get_cache_path

        result = get_cache_path(tmp_path)
        assert result == tmp_path / ".vibeocr" / "cache.json"


class TestCacheReadWrite:
    """Tests for cache read/write functions."""

    def test_save_and_load_cache(self, tmp_path):
        """Should save and load cache correctly."""
        from vibeocr.machine_cache import load_cache, save_cache

        data = {"test": "value", "number": 123}
        assert save_cache(tmp_path, data) is True

        loaded = load_cache(tmp_path)
        assert loaded == data

    def test_load_cache_returns_none_if_not_exists(self, tmp_path):
        """Should return None if cache file doesn't exist."""
        from vibeocr.machine_cache import load_cache

        result = load_cache(tmp_path)
        assert result is None

    def test_load_cache_returns_none_if_corrupted(self, tmp_path):
        """Should return None if cache file is corrupted JSON."""
        from vibeocr.machine_cache import load_cache

        cache_dir = tmp_path / ".vibeocr"
        cache_dir.mkdir()
        cache_file = cache_dir / "cache.json"
        cache_file.write_text("not valid json{")

        result = load_cache(tmp_path)
        assert result is None

    def test_save_cache_creates_directory(self, tmp_path):
        """Should create .vibeocr directory if it doesn't exist."""
        from vibeocr.machine_cache import save_cache

        data = {"test": "value"}
        assert save_cache(tmp_path, data) is True
        assert (tmp_path / ".vibeocr").exists()

    def test_save_cache_is_atomic_on_replace_failure(self, tmp_path):
        """os.replace 失败时不应留下半截 cache.json，且应清理临时文件。

        回归（P3 修复）：旧 save_cache 直接 open(cache.json, 'w') 写，
        写到一半崩溃会留下损坏 JSON，下次 load_cache 失败。原子写模式下
        即使 os.replace 失败，原 cache.json（若有）保持不变，临时文件被清理。
        """
        from vibeocr.machine_cache import load_cache, save_cache

        # 先写入一份有效缓存作为"旧值"
        old_data = {"version": 999, "old": True}
        assert save_cache(tmp_path, old_data) is True
        tmp_file = tmp_path / ".vibeocr" / "cache.json.tmp"

        # mock os.replace 抛异常模拟崩溃
        with patch("vibeocr.machine_cache.os.replace", side_effect=OSError("boom")):
            result = save_cache(tmp_path, {"new": True})

        assert result is False  # 保存失败
        # 原 cache.json 应保持旧值（未被半截写入污染）
        loaded = load_cache(tmp_path)
        assert loaded == old_data, "原子写失败时原缓存应保持不变"
        # 临时文件应被清理
        assert not tmp_file.exists(), "临时文件应被清理"


class TestCacheValidation:
    """Tests for cache validation."""

    def test_is_cache_valid_returns_false_if_no_cache(self, tmp_path):
        """Should return (False, None) if no cache exists."""
        from vibeocr.machine_cache import is_cache_valid

        is_valid, _data = is_cache_valid(tmp_path)
        assert is_valid is False
        assert _data is None

    def test_is_cache_valid_returns_false_if_machine_id_mismatch(self, tmp_path):
        """Should return (False, None) if machine ID doesn't match."""
        from vibeocr.machine_cache import is_cache_valid, save_cache

        # 保存一个使用假机器码的缓存
        cache_data = {
            "version": CACHE_VERSION,
            "machine_id": "fake_machine_id_12345",
            "dependencies": {"paddlepaddle": True},
        }
        save_cache(tmp_path, cache_data)

        is_valid, _data = is_cache_valid(tmp_path)
        assert is_valid is False

    def test_is_cache_valid_returns_true_if_machine_id_matches(self, tmp_path):
        """Should return (True, data) if machine ID matches."""
        from vibeocr.machine_cache import (
            generate_machine_id,
            is_cache_valid,
            save_cache,
        )

        machine_id = generate_machine_id()
        cache_data = {
            "version": CACHE_VERSION,
            "machine_id": machine_id,
            "dependencies": {"paddlepaddle": True},
        }
        save_cache(tmp_path, cache_data)

        is_valid, data = is_cache_valid(tmp_path)
        assert is_valid is True
        assert data == cache_data

    def test_is_cache_valid_returns_false_if_version_mismatch(self, tmp_path):
        """Should return (False, None) if cache version doesn't match."""
        from vibeocr.machine_cache import (
            generate_machine_id,
            is_cache_valid,
            save_cache,
        )

        machine_id = generate_machine_id()
        cache_data = {
            "version": 999,  # 旧版本
            "machine_id": machine_id,
            "dependencies": {"paddlepaddle": True},
        }
        save_cache(tmp_path, cache_data)

        is_valid, _data = is_cache_valid(tmp_path)
        assert is_valid is False


class TestCacheOperations:
    """Tests for cache operations."""

    def test_clear_cache_removes_file(self, tmp_path):
        """Should remove cache file."""
        from vibeocr.machine_cache import clear_cache, load_cache, save_cache

        save_cache(tmp_path, {"test": "value"})
        assert load_cache(tmp_path) is not None

        result = clear_cache(tmp_path)
        assert result is True
        assert load_cache(tmp_path) is None

    def test_clear_cache_returns_true_if_no_file(self, tmp_path):
        """Should return True even if no cache file exists."""
        from vibeocr.machine_cache import clear_cache

        result = clear_cache(tmp_path)
        assert result is True

    def test_create_cache_entry(self, tmp_path):
        """Should create a valid cache entry."""
        from vibeocr.machine_cache import (
            create_cache_entry,
            generate_machine_id,
            load_cache,
        )

        dependencies = {"paddlepaddle": True, "paddlex": True, "is_gpu": True}
        hardware_info = {"has_gpu": True, "cuda_version": "cu126"}

        result = create_cache_entry(tmp_path, dependencies, hardware_info)
        assert result is not None
        assert result["version"] == CACHE_VERSION
        assert result["machine_id"] == generate_machine_id()
        assert result["dependencies"] == dependencies
        assert result["hardware_info"] == hardware_info

        # 验证已保存到文件
        loaded = load_cache(tmp_path)
        assert loaded == result


class TestEnvManagerIntegration:
    """Tests for env_manager integration with cache."""

    def test_check_dependencies_uses_cache(self, tmp_path, monkeypatch):
        """Should use cached result if available."""
        from vibeocr.env_manager import check_embedded_environment_dependencies
        from vibeocr.machine_cache import generate_machine_id, save_cache

        # 创建假的 Python 环境
        python_dir = tmp_path / "python"
        python_dir.mkdir()

        # 创建假的缓存
        machine_id = generate_machine_id()
        cache_data = {
            "version": CACHE_VERSION,
            "machine_id": machine_id,
            "dependencies": {"paddlepaddle": True, "paddlex": True, "is_gpu": True},
        }
        save_cache(tmp_path, cache_data)

        # 应该返回缓存的依赖状态（不实际检测）
        result = check_embedded_environment_dependencies(tmp_path, use_cache=True)
        assert result == cache_data["dependencies"]

    def test_check_dependencies_fresh_ignores_cache(self, tmp_path):
        """Should ignore cache when use_cache=False."""
        from vibeocr.env_manager import check_embedded_environment_dependencies
        from vibeocr.machine_cache import generate_machine_id, save_cache

        # 创建假的缓存
        machine_id = generate_machine_id()
        cache_data = {
            "version": CACHE_VERSION,
            "machine_id": machine_id,
            "dependencies": {
                "paddlepaddle": True,
                "paddlex": True,
            },
        }
        save_cache(tmp_path, cache_data)

        # 不使用缓存时，应该返回空（因为 Python 不存在）
        result = check_embedded_environment_dependencies(tmp_path, use_cache=False)
        assert result == {}  # Python 不存在，返回空

    def test_check_dependencies_refreshes_stale_cache(self, tmp_path):
        """缓存显示 False 但实时 import 成功时应刷新缓存并返回 True

        回归：设置页表格状态走缓存、版本走实时 pip，两源不同步导致
        "显示未安装/已安装状态错误"。装完依赖后缓存仍是旧的 False 状态。
        """
        from vibeocr.env_manager import (
            check_embedded_environment_dependencies,
        )
        from vibeocr.machine_cache import generate_machine_id, load_cache, save_cache

        # 构造一个存在的 python.exe
        python_exe = tmp_path / "python" / "python.exe"
        python_exe.parent.mkdir(parents=True)
        python_exe.touch()

        # 缓存：paddlepaddle=False（旧状态，实际已装）
        machine_id = generate_machine_id()
        cache_data = {
            "version": CACHE_VERSION,
            "machine_id": machine_id,
            "dependencies": {"paddlepaddle": False, "torch": True},
        }
        save_cache(tmp_path, cache_data)

        # 实时复核：paddlepaddle 实际可导入
        with (
            patch(
                "vibeocr.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch(
                "vibeocr.env_manager._quick_verify_deps",
                return_value={"paddlepaddle": True, "torch": True},
            ),
            patch("vibeocr.env_manager.detect_gpu", return_value=(False, None)),
        ):
            result = check_embedded_environment_dependencies(tmp_path, use_cache=True)

        # 应返回刷新后的状态（paddlepaddle=True）
        assert result.get("paddlepaddle") is True, (
            f"缓存过期应刷新为 True，实际: {result}"
        )
        # 缓存文件也应已更新
        refreshed = load_cache(tmp_path)
        assert refreshed is not None
        assert refreshed["dependencies"]["paddlepaddle"] is True

    def test_empty_dependencies_cache_falls_back_to_real_check(self, tmp_path):
        """缓存有效但 dependencies 为空字典时不应静默返回空，应落入实时检测

        回归（修复 3）：旧逻辑在 cached_deps={} 时 stale_pkgs=[] 直接 return {}，
        导致设置页表格全显示"未安装"、首启 is_embedded_environment_ready 误报。
        """
        from vibeocr.env_manager import (
            check_embedded_environment_dependencies,
        )
        from vibeocr.machine_cache import generate_machine_id, save_cache

        python_exe = tmp_path / "python" / "python.exe"
        python_exe.parent.mkdir(parents=True)
        python_exe.touch()

        # 缓存有效，但 dependencies 是空字典（如首启从未检测过）
        machine_id = generate_machine_id()
        cache_data = {
            "version": CACHE_VERSION,
            "machine_id": machine_id,
            "dependencies": {},  # 空 → 旧逻辑会 return {}
        }
        save_cache(tmp_path, cache_data)

        # mock 实时检测返回真实结果
        with (
            patch(
                "vibeocr.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch(
                "vibeocr.env_manager._check_imports",
                return_value={"paddlepaddle": True, "paddleocr": False},
            ),
            patch("vibeocr.env_manager.detect_gpu", return_value=(False, None)),
        ):
            result = check_embedded_environment_dependencies(tmp_path, use_cache=True)

        # 不应返回空字典，而应是实时检测结果
        assert result == {"paddlepaddle": True, "paddleocr": False}, (
            f"空 dependencies 缓存应触发实时检测，实际: {result}"
        )

    def test_missing_dependencies_field_triggers_real_check(self, tmp_path):
        """缓存完全没有 dependencies 字段时也应落入实时检测"""
        from vibeocr.env_manager import (
            check_embedded_environment_dependencies,
        )
        from vibeocr.machine_cache import generate_machine_id, save_cache

        python_exe = tmp_path / "python" / "python.exe"
        python_exe.parent.mkdir(parents=True)
        python_exe.touch()

        # 缓存有效，但完全没有 dependencies 键
        machine_id = generate_machine_id()
        cache_data = {
            "version": CACHE_VERSION,
            "machine_id": machine_id,
            # 没有 dependencies 键
        }
        save_cache(tmp_path, cache_data)

        with (
            patch(
                "vibeocr.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch(
                "vibeocr.env_manager._check_imports",
                return_value={"paddlepaddle": True},
            ),
            patch("vibeocr.env_manager.detect_gpu", return_value=(False, None)),
        ):
            result = check_embedded_environment_dependencies(tmp_path, use_cache=True)

        assert result == {"paddlepaddle": True}, (
            f"缺 dependencies 字段应触发实时检测，实际: {result}"
        )


class TestCacheTTLRevalidation:
    """TTL 抽检：缓存超过 CACHE_TTL_DAYS 时对 true 项也做实时复核。"""

    def _setup_cache(self, tmp_path, days_ago: float, deps: dict):
        """构造一份 N 天前的有效缓存。"""
        from datetime import datetime, timedelta

        from vibeocr.machine_cache import generate_machine_id, save_cache

        python_exe = tmp_path / "python" / "python.exe"
        python_exe.parent.mkdir(parents=True)
        python_exe.touch()

        last_check = (datetime.now() - timedelta(days=days_ago)).isoformat()
        cache_data = {
            "version": CACHE_VERSION,
            "machine_id": generate_machine_id(),
            "last_check_time": last_check,
            "dependencies": deps,
        }
        save_cache(tmp_path, cache_data)
        return python_exe

    def test_ttl_expired_revalidates_true_entries(self, tmp_path):
        """缓存超过 TTL，缓存报 true 的项若实际缺失应被复核纠正为 false。

        回归：用户清理过 site-packages 但缓存仍报已装 → 启动期误判 ready。
        TTL 抽检捕获此类假阳性。
        """
        from vibeocr.env_manager import check_embedded_environment_dependencies
        from vibeocr.machine_cache import load_cache

        self._setup_cache(tmp_path, days_ago=8, deps={"paddlepaddle": True})

        with (
            patch(
                "vibeocr.env_manager.get_embedded_python_executable",
                return_value=tmp_path / "python" / "python.exe",
            ),
            patch(
                "vibeocr.env_manager._quick_verify_deps",
                return_value={"paddlepaddle": False},  # 实际已缺失
            ),
            patch("vibeocr.env_manager.detect_gpu", return_value=(False, None)),
        ):
            result = check_embedded_environment_dependencies(tmp_path, use_cache=True)

        assert result.get("paddlepaddle") is False, (
            f"TTL 过期后 true 项应被复核纠正，实际: {result}"
        )
        # 缓存应已刷新
        refreshed = load_cache(tmp_path)
        assert refreshed is not None
        assert refreshed["dependencies"]["paddlepaddle"] is False

    def test_within_ttl_skips_true_revalidation(self, tmp_path):
        """缓存未过 TTL，true 项不应触发复核（仅 false 项复核保留）。

        mock _quick_verify_deps 应只被 false 项调用，true 项的复核不应发生。
        用 call_count 断言：false 项 0 个时 _quick_verify_deps 不应被调用。
        """
        from vibeocr.env_manager import check_embedded_environment_dependencies

        # 1 天前缓存，全 true（无 false 项 → 旧 stale_pkgs 逻辑不触发）
        self._setup_cache(tmp_path, days_ago=1, deps={"paddlepaddle": True})

        with (
            patch(
                "vibeocr.env_manager.get_embedded_python_executable",
                return_value=tmp_path / "python" / "python.exe",
            ),
            patch(
                "vibeocr.env_manager._quick_verify_deps"
            ) as mock_verify,
        ):
            result = check_embedded_environment_dependencies(tmp_path, use_cache=True)

        # TTL 未过期 + 无 false 项 → 不应触发复核
        mock_verify.assert_not_called()
        assert result == {"paddlepaddle": True}

    def test_get_cache_age_seconds_returns_none_without_cache(self, tmp_path):
        """无缓存时 get_cache_age_seconds 返回 None。"""
        from vibeocr.machine_cache import get_cache_age_seconds

        assert get_cache_age_seconds(tmp_path) is None

    def test_get_cache_age_seconds_returns_seconds(self, tmp_path):
        """有效缓存返回正的秒数。"""
        from datetime import datetime, timedelta

        from vibeocr.machine_cache import (
            generate_machine_id,
            get_cache_age_seconds,
            save_cache,
        )

        last_check = (datetime.now() - timedelta(hours=2)).isoformat()
        save_cache(
            tmp_path,
            {
                "version": CACHE_VERSION,
                "machine_id": generate_machine_id(),
                "last_check_time": last_check,
            },
        )
        age = get_cache_age_seconds(tmp_path)
        assert age is not None
        assert 7000 < age < 8000  # ~7200s，留余量


class TestCacheVersionInvalidation:
    """CACHE_VERSION 变更应使旧缓存失效（修复 5）"""

    def test_old_version_cache_invalidated(self, tmp_path):
        """version 旧值（< CACHE_VERSION）的缓存应被判无效

        回归：markdown 纳入 required_deps 后，旧缓存（无 markdown key）必须失效，
        否则 is_embedded_environment_ready 会用旧缓存误判 markdown 已装。
        """
        from vibeocr.machine_cache import (
            CACHE_VERSION,
            generate_machine_id,
            is_cache_valid,
            save_cache,
        )

        machine_id = generate_machine_id()
        # 模拟旧版本缓存（version 比 CACHE_VERSION 旧）
        old_version = CACHE_VERSION - 1
        cache_data = {
            "version": old_version,
            "machine_id": machine_id,
            "dependencies": {"paddlepaddle": True},  # 旧缓存无 markdown
        }
        save_cache(tmp_path, cache_data)

        is_valid, _data = is_cache_valid(tmp_path)
        assert is_valid is False, (
            f"version={old_version} 的旧缓存应失效（当前 CACHE_VERSION={CACHE_VERSION}）"
        )

    def test_cache_version_is_current(self):
        """CACHE_VERSION 应为 4（补充 beautifulsoup4 leaf 检测）。

        v4 变更：PaddleX[ocr] 当前要求 beautifulsoup4，但旧 leaf 清单漏掉它；
        旧缓存必须失效重建，否则仍会误判表格识别依赖已就绪。
        升级此值须同步更新本测试和 machine_cache.py 的版本注释。
        """
        from vibeocr.machine_cache import CACHE_VERSION

        assert CACHE_VERSION == 4, (
            f"CACHE_VERSION 应为 4（补充 beautifulsoup4），实际 {CACHE_VERSION}。"
            "若新增/移除检测项，请同步 bump 版本号并更新此测试。"
        )


class TestResetCacheToEmpty:
    """Bug 2: refresh_cache 改名为 reset_cache_to_empty，仅清空 deps/hardware_info。

    回归：原 refresh_cache 名字暗示"重新检测"，但实现只写一个空壳，
    既不调用 env_manager.check_embedded_environment_dependencies，也不读真实依赖。
    UI 刷新按钮调用它会让用户以为缓存是最新检测结果，实则全是空。
    改名 reset_cache_to_empty 明示语义；UI 刷新路径走 env_manager 做真检测
    （见 SettingsPageController._refresh_machine_cache_operation）。
    """

    def test_reset_cache_to_empty_clears_dependencies(self, tmp_path):
        """Bug 2: reset_cache_to_empty 清空 deps/hardware_info。"""
        from vibeocr.machine_cache import (
            create_cache_entry,
            load_cache,
            reset_cache_to_empty,
        )

        # 先建一个有内容的缓存
        create_cache_entry(
            tmp_path,
            dependencies={"paddle": True},
            hardware_info={"has_gpu": True},
        )
        cached = load_cache(tmp_path)
        assert cached is not None
        assert cached.get("dependencies") == {"paddle": True}

        # 重置
        assert reset_cache_to_empty(tmp_path) is True
        reset = load_cache(tmp_path)
        assert reset is not None
        assert reset.get("dependencies") == {}
        assert reset.get("hardware_info") == {}

    def test_reset_cache_to_empty_preserves_version_and_machine_id(self, tmp_path):
        """重置只清 deps/hardware_info，version/machine_id 必须保留以保证 is_cache_valid。"""
        from vibeocr.machine_cache import (
            CACHE_VERSION,
            create_cache_entry,
            generate_machine_id,
            is_cache_valid,
            load_cache,
            reset_cache_to_empty,
        )

        create_cache_entry(
            tmp_path,
            dependencies={"paddlepaddle": True},
            hardware_info={"has_gpu": False},
        )
        reset_cache_to_empty(tmp_path)
        reset = load_cache(tmp_path)
        assert reset is not None
        assert reset["version"] == CACHE_VERSION
        assert reset["machine_id"] == generate_machine_id()
        # 机器码/version 仍匹配，缓存仍判为有效（只是 deps 空）
        is_valid, _data = is_cache_valid(tmp_path)
        assert is_valid is True

    def test_reset_cache_to_empty_loses_pipeline_success(self, tmp_path):
        """reset_cache_to_empty 不保留 pipeline_success（底层行为）。

        这是为什么 UI 层的 _refresh_machine_cache_operation / _on_clear_cache_clicked
        必须在 reset 前后手动保存/还原 pipeline_success——否则会导致
        _decide_recognize_timeout 误判"模型未缓存"，给 OCR 600s 超时。
        此测试固化底层语义，防止误改。
        """
        from vibeocr.machine_cache import (
            CACHE_VERSION,
            generate_machine_id,
            load_cache,
            reset_cache_to_empty,
            save_cache,
        )

        # 建一个含 pipeline_success 的缓存
        save_cache(
            tmp_path,
            {
                "version": CACHE_VERSION,
                "machine_id": generate_machine_id(),
                "dependencies": {"paddlepaddle": True},
                "pipeline_success": {"OCR": True, "PP-StructureV3": True},
            },
        )
        # reset 后 pipeline_success 丢失
        reset_cache_to_empty(tmp_path)
        reset = load_cache(tmp_path)
        assert reset is not None
        assert "pipeline_success" not in reset
        # 这是 by-design：保留逻辑由 UI 调用方负责（见 settings_page_controller）。


class TestWarmupMachineId:
    """Bug 3: warmup_machine_id 预热机器码缓存，避免 GUI 操作感知 wmic 锁争用。

    回归：首次 generate_machine_id 触发 2 次 wmic 子进程（CPU + 主板，各最多 5s
    超时），期间持有 _machine_id_lock。若多个 GUI 路径并发调用（设置页状态、
    缓存校验、env_manager 检测），后续调用阻塞等锁，UI 卡顿数十秒。
    warmup_machine_id 允许启动期后台线程提前跑一次，后续路径直接读 _cached_machine_id。
    """

    def test_warmup_machine_id_caches_result(self, monkeypatch):
        """Bug 3: warmup_machine_id 调一次后 generate_machine_id 不再跑 wmic。"""
        import vibeocr.machine_cache as mc

        # 重置模块级缓存（其他测试可能已填充）
        monkeypatch.setattr(mc, "_cached_machine_id", None)

        call_count = {"cpu": 0, "baseboard": 0}

        def fake_cpu() -> str:
            call_count["cpu"] += 1
            return "FAKE_CPU"

        def fake_baseboard() -> str:
            call_count["baseboard"] += 1
            return "FAKE_BB"

        monkeypatch.setattr(mc, "_get_cpu_id", fake_cpu)
        monkeypatch.setattr(mc, "_get_baseboard_serial", fake_baseboard)

        mc.warmup_machine_id()
        assert call_count == {"cpu": 1, "baseboard": 1}

        # 再次调用不应触发 wmic
        mc.generate_machine_id()
        mc.generate_machine_id()
        assert call_count == {"cpu": 1, "baseboard": 1}

    def test_warmup_machine_id_noop_when_already_cached(self, monkeypatch):
        """若 _cached_machine_id 已设置，warmup_machine_id 应是 no-op，不跑 wmic。"""
        import vibeocr.machine_cache as mc

        monkeypatch.setattr(mc, "_cached_machine_id", "PRESET_ID")

        call_count = {"cpu": 0, "baseboard": 0}

        def fake_cpu() -> str:
            call_count["cpu"] += 1
            return "FAKE_CPU"

        def fake_baseboard() -> str:
            call_count["baseboard"] += 1
            return "FAKE_BB"

        monkeypatch.setattr(mc, "_get_cpu_id", fake_cpu)
        monkeypatch.setattr(mc, "_get_baseboard_serial", fake_baseboard)

        mc.warmup_machine_id()
        assert call_count == {"cpu": 0, "baseboard": 0}
        # 已设置的缓存值不被覆盖
        assert mc.generate_machine_id() == "PRESET_ID"


class TestIsEmbeddedEnvironmentReady:
    """is_embedded_environment_ready 的真实逻辑测试（此前全被 mock 掉）。

    覆盖 1347-1389 的三个分支：python 不存在、全就绪、缓存过期复核纠正。
    """

    def test_python_not_installed_returns_missing(self, tmp_path):
        """Python 运行时不存在时返回 (False, ['Python 运行时未安装'])。"""
        from vibeocr.env_manager import is_embedded_environment_ready

        # python.exe 不存在
        with patch(
            "vibeocr.env_manager.get_embedded_python_executable",
            return_value=tmp_path / "nonexistent" / "python.exe",
        ):
            ready, missing = is_embedded_environment_ready(tmp_path)

        assert ready is False
        assert missing == ["Python 运行时未安装"]

    def test_all_required_deps_present_returns_ready(self, tmp_path):
        """所有必需依赖都为 True 时返回 (True, [])。"""
        from vibeocr.env_manager import is_embedded_environment_ready

        python_exe = tmp_path / "python" / "python.exe"
        python_exe.parent.mkdir(parents=True)
        python_exe.touch()

        all_ok = {
            "paddlepaddle": True,
            "paddleocr": True,
            "mineru": True,
            "markdown": True,
            "pymupdf": True,
            "fastapi": True,
            "uvicorn": True,
            "pydantic": True,
            "fonttools": True,
        }

        with (
            patch(
                "vibeocr.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch(
                "vibeocr.env_manager.check_embedded_environment_dependencies",
                return_value=dict(all_ok),
            ),
        ):
            ready, missing = is_embedded_environment_ready(tmp_path)

        assert ready is True
        assert missing == []

    def test_missing_deps_returns_not_ready(self, tmp_path):
        """缺依赖时返回 (False, [缺失项])。"""
        from vibeocr.env_manager import is_embedded_environment_ready

        python_exe = tmp_path / "python" / "python.exe"
        python_exe.parent.mkdir(parents=True)
        python_exe.touch()

        deps = {
            "paddlepaddle": True,
            "paddleocr": True,
            "mineru": False,  # 缺失
            "markdown": True,
            "pymupdf": True,
            "fastapi": True,
            "uvicorn": True,
            "pydantic": True,
            "fonttools": True,
        }

        # _quick_verify_deps 也报缺失 → still_missing == missing，不刷新
        with (
            patch(
                "vibeocr.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch(
                "vibeocr.env_manager.check_embedded_environment_dependencies",
                return_value=dict(deps),
            ),
            patch(
                "vibeocr.env_manager._quick_verify_deps",
                return_value={"mineru": False},
            ),
        ):
            ready, missing = is_embedded_environment_ready(tmp_path)

        assert ready is False
        assert "mineru" in missing

    def test_stale_cache_corrected_by_recheck(self, tmp_path):
        """缓存报缺失但实时复核显示已装 → missing 被纠正为空，缓存刷新。"""
        from vibeocr.env_manager import is_embedded_environment_ready

        python_exe = tmp_path / "python" / "python.exe"
        python_exe.parent.mkdir(parents=True)
        python_exe.touch()

        # 缓存：paddlepaddle=False（过期），其余全 True
        deps = {
            "paddlepaddle": False,
            "paddleocr": True,
            "mineru": True,
            "markdown": True,
            "pymupdf": True,
            "fastapi": True,
            "uvicorn": True,
            "pydantic": True,
            "fonttools": True,
        }

        with (
            patch(
                "vibeocr.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch(
                "vibeocr.env_manager.check_embedded_environment_dependencies",
                return_value=dict(deps),
            ),
            # 复核显示 paddlepaddle 实际已装 → still_missing != missing → 纠正
            patch(
                "vibeocr.env_manager._quick_verify_deps",
                return_value={"paddlepaddle": True},
            ),
            patch("vibeocr.env_manager.detect_gpu", return_value=(False, None)),
            patch("vibeocr.env_manager.create_cache_entry") as mock_create,
        ):
            ready, missing = is_embedded_environment_ready(tmp_path)

        assert ready is True
        assert missing == []
        # 过期缓存纠正后应写入新缓存
        mock_create.assert_called_once()


class TestGetCacheInfo:
    """get_cache_info 多行调试格式化器（此前无任何测试）。"""

    def test_no_cache_returns_placeholder(self, tmp_path):
        """无缓存文件时应返回占位串。"""
        from vibeocr.machine_cache import get_cache_info

        assert get_cache_info(tmp_path) == "无缓存"

    def test_full_cache_renders_all_fields(self, tmp_path):
        """完整缓存应渲染所有顶层字段（含 pending_backend）。"""
        from vibeocr.machine_cache import (
            CACHE_VERSION,
            generate_machine_id,
            get_cache_info,
            save_cache,
        )

        cache_data = {
            "version": CACHE_VERSION,
            "machine_id": generate_machine_id(),
            "last_check_time": "2026-07-27T10:00:00",
            "python_version": "3.13.0",
            "dependencies": {"paddlepaddle": True, "torch": False},
            "hardware_info": {"has_gpu": True, "cuda_version": "cu126"},
            "pipeline_success": {"OCR": True, "PDF": False},
            "network": {
                "paddlex_source": "modelscope",
                "mineru_source": "modelscope",
                "last_detected": "2026-07-27T09:00:00",
            },
            "pending_backend": "gpu",
        }
        save_cache(tmp_path, cache_data)
        info = get_cache_info(tmp_path)

        # 各字段都应出现在输出中
        assert "version=" + str(CACHE_VERSION) in info
        assert "machine_id=" in info
        assert "2026-07-27T10:00:00" in info
        assert "python_version=3.13.0" in info
        # dependencies 用 ✓/✗ 标记
        assert "paddlepaddle=✓" in info
        assert "torch=✗" in info
        # pipeline_success 列出 key
        assert "OCR" in info and "PDF" in info
        # network 字段
        assert "paddlex=modelscope" in info
        assert "mineru=modelscope" in info
        # pending_backend 有值时单独一行
        assert "pending_backend=gpu" in info
        # has_gpu / cuda
        assert "has_gpu=True" in info
        assert "cuda=cu126" in info

    def test_minimal_cache_shows_empty_placeholders(self, tmp_path):
        """缺 deps/pipeline/network/pending_backend 时用占位串。"""
        from vibeocr.machine_cache import get_cache_info, save_cache

        save_cache(tmp_path, {"version": 1, "machine_id": "abc"})
        info = get_cache_info(tmp_path)

        assert "dependencies: (空)" in info
        assert "pipeline_success: (无)" in info
        assert "network: (未探测)" in info
        # pending_backend 为 None 时不出现该行
        assert "pending_backend=" not in info


class TestMachineCacheBranches:
    """补 _get_cpu_id/_get_baseboard_serial/_get_mac_address 等的分支覆盖。"""

    def test_get_cache_age_seconds_invalid_timestamp(self, tmp_path):
        """last_check_time 非法时返回 None。"""
        from vibeocr.machine_cache import get_cache_age_seconds, save_cache

        save_cache(tmp_path, {"last_check_time": "not-a-date"})
        assert get_cache_age_seconds(tmp_path) is None

    def test_get_cache_age_seconds_missing_field(self, tmp_path):
        """无 last_check_time 字段时返回 None。"""
        from vibeocr.machine_cache import get_cache_age_seconds, save_cache

        save_cache(tmp_path, {"version": 1})
        assert get_cache_age_seconds(tmp_path) is None

    def test_get_cache_age_seconds_valid(self, tmp_path):
        """合法时间戳返回正秒数。"""
        from datetime import datetime

        from vibeocr.machine_cache import get_cache_age_seconds, save_cache

        save_cache(tmp_path, {"last_check_time": datetime.now().isoformat()})
        age = get_cache_age_seconds(tmp_path)
        assert age is not None
        assert age >= 0

    def test_load_cache_handles_generic_exception(self, tmp_path):
        """load_cache 在非 JSONDecodeError 异常时返回 None。"""
        from vibeocr.machine_cache import load_cache

        cache_dir = tmp_path / ".vibeocr"
        cache_dir.mkdir()
        # 用目录冒充 cache.json，open() 会抛 IsADirectoryError（非 JSONDecodeError）
        (cache_dir / "cache.json").mkdir()
        assert load_cache(tmp_path) is None

    def test_clear_cache_failure_returns_false(self, tmp_path):
        """clear_cache 在 unlink 抛异常时返回 False。"""
        from vibeocr.machine_cache import clear_cache, save_cache

        save_cache(tmp_path, {"x": 1})
        with patch("pathlib.Path.unlink", side_effect=OSError("denied")):
            assert clear_cache(tmp_path) is False

    def test_reset_cache_to_empty_save_failure(self, tmp_path):
        """save_cache 失败时 reset_cache_to_empty 返回 False。"""
        from vibeocr.machine_cache import reset_cache_to_empty

        with patch("vibeocr.machine_cache.save_cache", return_value=False):
            assert reset_cache_to_empty(tmp_path) is False

    def test_reset_cache_to_empty_exception(self, tmp_path):
        """generate_machine_id 抛异常时 reset_cache_to_empty 返回 False。"""
        from vibeocr.machine_cache import reset_cache_to_empty

        with patch(
            "vibeocr.machine_cache.generate_machine_id",
            side_effect=RuntimeError("boom"),
        ):
            assert reset_cache_to_empty(tmp_path) is False

    def test_create_cache_entry_save_failure(self, tmp_path):
        """save_cache 失败时 create_cache_entry 返回 None。"""
        from vibeocr.machine_cache import create_cache_entry

        with patch("vibeocr.machine_cache.save_cache", return_value=False):
            assert create_cache_entry(tmp_path, {}, {}) is None

    def test_get_cpu_id_wmic_success(self):
        """wmic 成功返回 processorid 时 _get_cpu_id 应解析第二行。"""
        from vibeocr.machine_cache import _get_cpu_id

        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = "ProcessorId\nABC123\n"
        with (
            patch("vibeocr.machine_cache.os.name", "nt"),
            patch(
                "vibeocr.machine_cache.subprocess.run", return_value=fake_result
            ),
        ):
            assert _get_cpu_id() == "ABC123"

    def test_get_cpu_id_wmic_returncode_nonzero(self):
        """wmic 返回非 0 时 _get_cpu_id 返回空串。"""
        from vibeocr.machine_cache import _get_cpu_id

        fake_result = MagicMock()
        fake_result.returncode = 1
        fake_result.stdout = ""
        with (
            patch("vibeocr.machine_cache.os.name", "nt"),
            patch(
                "vibeocr.machine_cache.subprocess.run", return_value=fake_result
            ),
        ):
            assert _get_cpu_id() == ""

    def test_get_cpu_id_non_windows(self):
        """非 Windows 时 _get_cpu_id 返回空串。"""
        from vibeocr.machine_cache import _get_cpu_id

        with patch("vibeocr.machine_cache.os.name", "posix"):
            assert _get_cpu_id() == ""

    def test_get_baseboard_serial_wmic_success(self):
        """wmic 成功返回 serialnumber 时 _get_baseboard_serial 应解析第二行。"""
        from vibeocr.machine_cache import _get_baseboard_serial

        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = "SerialNumber\nSN-456\n"
        with (
            patch("vibeocr.machine_cache.os.name", "nt"),
            patch(
                "vibeocr.machine_cache.subprocess.run", return_value=fake_result
            ),
        ):
            assert _get_baseboard_serial() == "SN-456"

    def test_get_baseboard_serial_non_windows(self):
        """非 Windows 时 _get_baseboard_serial 返回空串。"""
        from vibeocr.machine_cache import _get_baseboard_serial

        with patch("vibeocr.machine_cache.os.name", "posix"):
            assert _get_baseboard_serial() == ""

    def test_generate_machine_id_concurrent_double_check(self):
        """锁内二次检查：_cached_machine_id 已设时直接返回，不重复探测。"""
        import vibeocr.machine_cache as mc

        # 预设缓存，模拟另一个线程已写入
        sentinel = "a" * 64
        mc._cached_machine_id = sentinel
        try:
            assert mc.generate_machine_id() == sentinel
        finally:
            mc._cached_machine_id = None

    def test_update_cache_field_invalid_cache_returns_false(self, tmp_path):
        """缓存无效（不存在）时 update_cache_field 返回 False。"""
        from vibeocr.machine_cache import update_cache_field

        assert update_cache_field(tmp_path, "pending_backend", "gpu") is False

    def test_update_cache_field_writes_and_preserves_fields(self, tmp_path):
        """有效缓存时 update_cache_field 增量写单字段并保留其余字段。"""
        from vibeocr.machine_cache import (
            create_cache_entry,
            load_cache,
            update_cache_field,
        )

        create_cache_entry(tmp_path, {"paddlepaddle": True}, {"has_gpu": False})
        ok = update_cache_field(tmp_path, "pending_backend", "cpu")
        assert ok is True
        data = load_cache(tmp_path)
        assert data is not None
        assert data["pending_backend"] == "cpu"
        # 原有字段保留
        assert data["dependencies"] == {"paddlepaddle": True}
        assert data["version"] == CACHE_VERSION

    def test_get_cpu_id_single_line_output(self):
        """wmic 只返回表头（无数据行）时 _get_cpu_id 返回空串。"""
        from vibeocr.machine_cache import _get_cpu_id

        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = "ProcessorId\n"  # 只有表头
        with (
            patch("vibeocr.machine_cache.os.name", "nt"),
            patch(
                "vibeocr.machine_cache.subprocess.run", return_value=fake_result
            ),
        ):
            assert _get_cpu_id() == ""

    def test_get_baseboard_serial_single_line_output(self):
        """wmic 只返回表头时 _get_baseboard_serial 返回空串。"""
        from vibeocr.machine_cache import _get_baseboard_serial

        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = "SerialNumber\n"
        with (
            patch("vibeocr.machine_cache.os.name", "nt"),
            patch(
                "vibeocr.machine_cache.subprocess.run", return_value=fake_result
            ),
        ):
            assert _get_baseboard_serial() == ""


def test_get_machine_id_caches_after_first_call(monkeypatch):
    """第二次调用 generate_machine_id 走缓存（line 166-167）。"""
    import vibeocr.machine_cache as mc

    monkeypatch.setattr(mc, "_cached_machine_id", None)
    # 第一次调用（真实或 mock）
    first = mc.generate_machine_id()
    assert first  # 非空
    # 第二次应命中缓存（_cached_machine_id 已设置）
    assert mc._cached_machine_id == first
    second = mc.generate_machine_id()
    assert second == first


def test_get_mac_address_returns_empty_when_random(monkeypatch):
    """uuid.getnode() 两次返回不同（随机 MAC）时返回空串（line 138-140）。"""
    import vibeocr.machine_cache as mc

    call_count = {"n": 0}

    def fake_getnode():
        call_count["n"] += 1
        # 每次返回不同值 → 视为随机
        return call_count["n"]

    monkeypatch.setattr(mc.uuid, "getnode", fake_getnode)
    assert mc._get_mac_address() == ""
