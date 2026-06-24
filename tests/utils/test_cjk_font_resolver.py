"""CjkFontResolver 单元测试：系统 CJK 字体探测 + 子集化。"""

from __future__ import annotations

import pytest

from vibeocr.utils.cjk_font_resolver import CjkFontResolver


class TestFindSystemFont:
    """系统字体探测。"""

    def test_returns_path_when_font_exists(self, monkeypatch, tmp_path):
        """候选字体存在时返回其路径。"""
        fake_font = tmp_path / "fake.ttf"
        fake_font.write_bytes(b"fake")  # 存在即可
        resolver = CjkFontResolver()
        monkeypatch.setattr(
            resolver, "_get_candidates", lambda: [str(fake_font)]
        )
        assert resolver._find_system_font() == str(fake_font)

    def test_returns_none_when_no_font(self, monkeypatch):
        """所有候选都不存在时返回 None（优雅降级）。"""
        resolver = CjkFontResolver()
        monkeypatch.setattr(
            resolver, "_get_candidates", lambda: ["/nonexistent/font.ttf"]
        )
        assert resolver._find_system_font() is None

    def test_returns_first_existing(self, monkeypatch, tmp_path):
        """多个候选时返回第一个存在的。"""
        exists = tmp_path / "second.ttf"
        exists.write_bytes(b"x")
        resolver = CjkFontResolver()
        monkeypatch.setattr(
            resolver,
            "_get_candidates",
            lambda: ["/nonexistent/x.ttf", str(exists)],
        )
        assert resolver._find_system_font() == str(exists)

    def test_find_result_cached(self, monkeypatch, tmp_path):
        """探测结果缓存：第二次调用不再扫描文件系统。"""
        fake_font = tmp_path / "cached.ttf"
        fake_font.write_bytes(b"x")
        resolver = CjkFontResolver()
        monkeypatch.setattr(
            resolver, "_get_candidates", lambda: [str(fake_font)]
        )
        first = resolver._find_system_font()
        # 删除文件后再次调用，仍应返回缓存路径（证明不重复扫描）
        fake_font.unlink()
        second = resolver._find_system_font()
        assert first == second == str(fake_font)
