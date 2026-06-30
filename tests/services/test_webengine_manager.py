"""WebEngine 资源按需下载管理器测试。

覆盖：就绪检测、版本对齐、marker 读写、下载源选择、zip 解压的路径穿越防护。
"""

from __future__ import annotations

import json
import sys
import zipfile

import pytest


@pytest.fixture
def _frozen(monkeypatch, tmp_path):
    """模拟 PyInstaller 打包态：frozen=True 且 _MEIPASS 指向临时目录。"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    return tmp_path


class TestIsWebEngineReady:
    def test_not_ready_when_dll_missing(self, _frozen):
        from vibeocr.services import webengine_manager as wm

        assert wm.is_webengine_ready() is False

    def test_ready_when_core_dll_exists(self, _frozen):
        from vibeocr.services import webengine_manager as wm

        pyside6_dir = _frozen / "PySide6"
        pyside6_dir.mkdir()
        (pyside6_dir / "Qt6WebEngineCore.dll").write_bytes(b"dll")
        assert wm.is_webengine_ready() is True


class TestVersionAlignment:
    def test_marker_roundtrip(self, monkeypatch, tmp_path):
        from vibeocr.services import webengine_manager as wm

        # marker 路径指向临时目录
        monkeypatch.setattr(
            wm, "get_webengine_assets_path",
            lambda: tmp_path / "webengine_assets.json",
        )
        assert wm._read_installed_marker() is None
        wm._write_installed_marker("0.4.0")
        assert wm._read_installed_marker() == "0.4.0"

    def test_needs_install_when_reinstall_marker_exists(self, monkeypatch, tmp_path):
        from vibeocr.services import webengine_manager as wm

        monkeypatch.setattr(
            wm, "get_webengine_reinstall_marker_path",
            lambda: tmp_path / "webengine_pending_reinstall.json",
        )
        (tmp_path / "webengine_pending_reinstall.json").write_text("{}", encoding="utf-8")
        assert wm.needs_install() is True

    def test_no_install_when_ready_and_version_matches(
        self, monkeypatch, _frozen
    ):
        from vibeocr.services import webengine_manager as wm

        # DLL 在位
        pyside6_dir = _frozen / "PySide6"
        pyside6_dir.mkdir()
        (pyside6_dir / "Qt6WebEngineCore.dll").write_bytes(b"dll")
        # version.json 声明版本，marker 一致
        (_frozen / "version.json").write_text(
            json.dumps({"webengine_assets_version": "0.4.0"}), encoding="utf-8"
        )
        monkeypatch.setattr(
            wm, "get_webengine_assets_path",
            lambda: _frozen / "webengine_assets.json",
        )
        (_frozen / "webengine_assets.json").write_text(
            json.dumps({"assets_version": "0.4.0"}), encoding="utf-8"
        )
        monkeypatch.setattr(
            wm, "get_webengine_reinstall_marker_path",
            lambda: _frozen / "no_reinstall.json",
        )
        assert wm.needs_install() is False

    def test_needs_install_when_version_mismatch(self, monkeypatch, _frozen):
        from vibeocr.services import webengine_manager as wm

        pyside6_dir = _frozen / "PySide6"
        pyside6_dir.mkdir()
        (pyside6_dir / "Qt6WebEngineCore.dll").write_bytes(b"dll")
        (_frozen / "version.json").write_text(
            json.dumps({"webengine_assets_version": "0.5.0"}), encoding="utf-8"
        )
        monkeypatch.setattr(
            wm, "get_webengine_assets_path",
            lambda: _frozen / "webengine_assets.json",
        )
        (_frozen / "webengine_assets.json").write_text(
            json.dumps({"assets_version": "0.4.0"}), encoding="utf-8"
        )
        monkeypatch.setattr(
            wm, "get_webengine_reinstall_marker_path",
            lambda: _frozen / "no_reinstall.json",
        )
        assert wm.needs_install() is True


class TestSelectAssetsSource:
    def test_domestic_prefers_gitee_with_four_candidates(self):
        """国内：Gitee 优先，4 候选（Gitee→gh-proxy→ghproxy→GitHub）"""
        from vibeocr.services import webengine_manager as wm

        urls = wm.select_assets_source("domestic", "0.4.0")
        assert len(urls) == 4
        assert "gitee.com" in urls[0]
        assert urls[0].endswith("VibeOCR-v0.4.0-webengine-win64.zip")
        # gh 代理候选在中间
        assert "gh-proxy.com" in urls[1]
        assert "ghproxy.com" in urls[2]

    def test_international_prefers_github_with_two_candidates(self):
        """海外：GitHub 优先，2 候选（GitHub→Gitee）"""
        from vibeocr.services import webengine_manager as wm

        urls = wm.select_assets_source("international", "0.4.0")
        assert len(urls) == 2
        assert "github.com" in urls[0]
        assert "gitee.com" in urls[1]

    def test_domestic_includes_gh_proxy_fallback(self):
        """国内候选必须含 gh 代理加速（gh-proxy / ghproxy）"""
        from vibeocr.services import webengine_manager as wm

        urls = wm.select_assets_source("domestic", "0.4.0")
        joined = " ".join(urls)
        assert "gh-proxy" in joined or "ghproxy" in joined


class TestSafeExtractZip:
    def test_normal_extraction(self, tmp_path):
        from vibeocr.services import webengine_manager as wm

        # 构造合法资源包 zip（顶层 PySide6/）
        zip_path = tmp_path / "assets.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("PySide6/resources/icudtl.dat", b"data")
            zf.writestr("PySide6/Qt6WebEngineCore.dll", b"dll")

        target = tmp_path / "PySide6"
        assert wm._safe_extract_zip(zip_path, target) is True
        assert (target / "resources" / "icudtl.dat").read_bytes() == b"data"
        assert (target / "Qt6WebEngineCore.dll").exists()

    def test_path_traversal_blocked(self, tmp_path):
        """含 ../ 路径穿越的成员必须被跳过，不解压到目标之外。"""
        from vibeocr.services import webengine_manager as wm

        zip_path = tmp_path / "evil.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("PySide6/Qt6WebEngineCore.dll", b"dll")
            # 恶意路径：试图逃逸到 target 父目录
            zf.writestr("../../evil.exe", b"malware")

        target = tmp_path / "PySide6"
        wm._safe_extract_zip(zip_path, target)
        # 合法文件已解压
        assert (target / "Qt6WebEngineCore.dll").exists()
        # 穿越文件未落在 target 之外（target 的父目录 tmp_path 下无 evil.exe）
        assert not (tmp_path / "evil.exe").exists()

    def test_bad_zip_returns_false(self, tmp_path):
        from vibeocr.services import webengine_manager as wm

        bad = tmp_path / "bad.zip"
        bad.write_bytes(b"not a zip")
        assert wm._safe_extract_zip(bad, tmp_path / "out") is False
