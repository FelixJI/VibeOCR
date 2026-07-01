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
    """模拟 PyInstaller 打包态：frozen=True 且 _MEIPASS 指向临时目录。

    打包态下 ``get_project_root()`` 返回 exe 同级目录（version.json 等
    运行时文件所在），而非 ``_MEIPASS``（``_internal/``，只读资源所在）。
    故此处同步把 ``get_project_root`` 指向 tmp_path，使测试写入的
    version.json 能被 ``_read_version_json_webengine_ver`` 读到——与生产
    环境中 version.json 位于 exe 同级、而非 ``_internal/`` 的事实一致。
    """
    from vibeocr.services import webengine_manager as wm

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(wm, "get_project_root", lambda: tmp_path)
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
    """源序逻辑现已收敛到 env_config.build_asset_url_pairs（download_and_install 用它
    生成同源 (zip, sha) 配对候选）。这些测试验证源序不变量：国内 gh-proxy→ghproxy→
    GitHub；海外 GitHub。等价于旧的 select_assets_source 行为。"""

    @staticmethod
    def _zip_urls(network_type: str, version: str = "0.4.0") -> list[str]:
        from vibeocr.services.env_config import build_asset_url_pairs

        fname = f"VibeOCR-v{version}-webengine-win64.zip"
        sha_fname = f"{fname}.sha256"
        pairs = build_asset_url_pairs(network_type, version, fname, sha_fname)
        return [p[0] for p in pairs]

    def test_domestic_prefers_gh_proxy_with_three_candidates(self):
        """国内：gh 代理优先，3 候选（gh-proxy→ghproxy→GitHub）"""
        urls = self._zip_urls("domestic")
        assert len(urls) == 3
        assert "gh-proxy.com" in urls[0]
        assert urls[0].endswith("VibeOCR-v0.4.0-webengine-win64.zip")
        # ghproxy 候选在中间
        assert "ghproxy.com" in urls[1]

    def test_international_prefers_github_with_one_candidate(self):
        """海外：GitHub 直连（1 候选）"""
        urls = self._zip_urls("international")
        assert len(urls) == 1
        assert "github.com" in urls[0]

    def test_domestic_includes_gh_proxy_fallback(self):
        """国内候选必须含 gh 代理加速（gh-proxy / ghproxy）"""
        urls = self._zip_urls("domestic")
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
