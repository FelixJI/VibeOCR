"""Tests for machine_cache module."""

import subprocess
from unittest.mock import patch


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
            "version": 1,
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
            "version": 1,
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
        assert result["version"] == 1
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
            "version": 1,
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
            "version": 1,
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
            "version": 1,
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
        assert refreshed["dependencies"]["paddlepaddle"] is True
