"""Tests for machine_cache module."""

import subprocess
from unittest.mock import patch, MagicMock
from pathlib import Path

import pytest


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

    @patch('vibeocr.machine_cache.subprocess.run')
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
        from vibeocr.machine_cache import save_cache, load_cache

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
