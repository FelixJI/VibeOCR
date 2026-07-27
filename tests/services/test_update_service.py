"""update_service 模块测试"""

import asyncio
import hashlib
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


def _run(coro):
    """在同步测试中运行协程"""
    return asyncio.run(coro)


def _make_update_info(**overrides):
    """构造一个填好真实字段的 UpdateInfo（默认 0.3.1，Classic 命名）。

    生产代码里 UpdateInfo 总由 ``from_release`` 创建，会带下来 ``zip_filename`` /
    ``sha256_filename``（从 release assets 选出的真实文件名）。早期测试直接
    ``UpdateInfo(version=..., download_url=...)`` 省略文件名，但 ``download_update``
    现在依赖 ``zip_filename`` 拼 URL（空会触发空守卫失败）。本助手统一填默认值，
    调用方可用 ``**overrides`` 覆盖任一字段。
    """
    from vibeocr.services.update_service import UpdateInfo

    defaults = {
        "version": "0.3.1",
        "download_url": "https://example.com/zip",
        "sha256_url": "https://example.com/sha",
        "changelog": "",
        "zip_filename": "VibeOCR-v0.3.1-win64.zip",
        "sha256_filename": "VibeOCR-v0.3.1-win64.zip.sha256",
    }
    defaults.update(overrides)
    return UpdateInfo(**defaults)


# ---------------------------------------------------------------------------
# _download_zip_with_sha 直接测试用的 mock 构造助手
# ---------------------------------------------------------------------------


def _make_stream_response(status_code, chunks):
    """构造一个可直接用于 ``async with`` 的伪流式响应。

    支持被测代码用到的：``status_code``、``headers["content-length"]``、
    ``async for chunk in resp.aiter_bytes(chunk_size=...)``。
    """
    total = sum(len(c) for c in chunks)

    async def _aiter():
        for c in chunks:
            yield c

    class _StreamCM:
        def __init__(self) -> None:
            self.status_code = status_code
            self.headers = {"content-length": str(total)}

        async def __aenter__(self) -> "_StreamCM":
            return self

        async def __aexit__(self, *exc) -> bool:
            return False

        def aiter_bytes(self, chunk_size: int = 65536):
            return _aiter()

    return _StreamCM()


def _make_client(stream_cm=None, stream_side_effect=None, sha_status=200, sha_text=""):
    """构造一个伪 ``httpx.AsyncClient``。

    - ``client.stream(...)`` 同步返回流式上下文管理器（或抛 ``stream_side_effect``）；
    - ``client.get(sha_url)`` 返回 awaitable，解析为伪 sha 响应。
    """
    client = MagicMock()
    if stream_side_effect is not None:
        client.stream.side_effect = stream_side_effect
    else:
        client.stream.return_value = stream_cm

    client.get = AsyncMock()
    sha_resp = MagicMock()
    sha_resp.status_code = sha_status
    sha_resp.text = sha_text
    client.get.return_value = sha_resp
    return client


class TestVersionComparison:
    """语义化版本比较测试"""

    def test_compare_equal(self):
        from vibeocr.services.update_service import compare_versions

        assert compare_versions("1.0.0", "1.0.0") == 0

    def test_compare_patch_higher(self):
        from vibeocr.services.update_service import compare_versions

        assert compare_versions("1.0.1", "1.0.0") == 1

    def test_compare_minor_higher(self):
        from vibeocr.services.update_service import compare_versions

        assert compare_versions("1.1.0", "1.0.9") == 1

    def test_compare_major_higher(self):
        from vibeocr.services.update_service import compare_versions

        assert compare_versions("2.0.0", "1.9.9") == 1

    def test_compare_lower(self):
        from vibeocr.services.update_service import compare_versions

        assert compare_versions("0.9.0", "1.0.0") == -1

    def test_compare_with_v_prefix(self):
        from vibeocr.services.update_service import compare_versions

        assert compare_versions("v1.0.0", "v1.0.0") == 0


class TestUpdateInfo:
    """UpdateInfo 数据模型测试"""

    def test_from_release(self):
        from vibeocr.services.update_service import UpdateInfo

        release = {
            "tag_name": "v0.3.0",
            "body": "## Changes\n- fix: something",
            "assets": [
                {
                    "name": "VibeOCR-v0.3.0-win64.zip",
                    "browser_download_url": "https://github.com/test/v0.3.0.zip",
                    "size": 0,
                },
                {
                    "name": "VibeOCR-v0.3.0-win64.zip.sha256",
                    "browser_download_url": "https://github.com/test/v0.3.0.zip.sha256",
                },
            ],
        }
        info = UpdateInfo.from_release(release)
        assert info.version == "0.3.0"
        assert info.download_url == "https://github.com/test/v0.3.0.zip"
        assert info.sha256_url == "https://github.com/test/v0.3.0.zip.sha256"

    def test_changelog_dialog_text_uses_only_user_facing_top_level_items(self):
        from vibeocr.pyside.update import _format_changelog_for_dialog

        raw = """
### Fixed

- fix(update): 启动自动检查与关于页"检查更新"按钮并发时崩溃
  (`RuntimeError: Cannot enter into task ... while another task ...
  is being executed`). 根因：两调用点各自 ensure_future 起
  `check_and_prompt`，并发时触发 asyncio `_enter_task` 重入保护。

- fix(update): 下载进度对话框支持「取消」与「最小化」。
  - 取消：底部「取消」按钮 + 恢复标题栏关闭 X。
  - 根因：原对话框用 `& ~WindowCloseButtonHint` 去掉关闭按钮。
"""

        text = _format_changelog_for_dialog(raw)

        assert text.splitlines() == [
            '· 启动自动检查与关于页"检查更新"按钮并发时崩溃',
            "· 下载进度对话框支持「取消」与「最小化」。",
        ]
        for forbidden in (
            "RuntimeError",
            "check_and_prompt",
            "_enter_task",
            "asyncio",
            "WindowCloseButtonHint",
            "根因",
        ):
            assert forbidden not in text

    def test_no_matching_assets(self):
        from vibeocr.services.update_service import UpdateInfo

        release = {
            "tag_name": "v0.1.0",
            "body": "",
            "assets": [],
        }
        info = UpdateInfo.from_release(release)
        assert info.download_url == ""
        assert info.sha256_url == ""

    def test_excludes_webengine_asset(self):
        """_find_asset_url 必须排除历史 webengine 资源包，只匹配主包。

        旧版曾单独发布 VibeOCR-v*-webengine-win64.zip（现已内置主包）；
        排除守卫保留作历史 release asset 的防御，更新检测只应拿主包 zip。
        """
        from vibeocr.services.update_service import _find_asset_size, _find_asset_url

        # webengine 资源包排在前面，确认不会被误取
        release = {
            "assets": [
                {
                    "name": "VibeOCR-v0.4.0-webengine-win64.zip",
                    "browser_download_url": "http://webengine.zip",
                    "size": 999,
                },
                {
                    "name": "VibeOCR-v0.4.0-webengine-win64.zip.sha256",
                    "browser_download_url": "http://webengine.sha256",
                },
                {
                    "name": "VibeOCR-v0.4.0-win64.zip",
                    "browser_download_url": "http://main.zip",
                    "size": 100,
                },
                {
                    "name": "VibeOCR-v0.4.0-win64.zip.sha256",
                    "browser_download_url": "http://main.sha256",
                },
            ],
        }
        assert _find_asset_url(release, ".zip") == "http://main.zip"
        assert _find_asset_url(release, ".sha256") == "http://main.sha256"
        assert _find_asset_size(release, ".zip") == 100

    def test_zip_filename_from_classic_release(self):
        """v0.4.29+ 产物改名加 -Classic- 后，from_release 必须带下真实文件名。

        回归 v0.4.29+ 更新全挂的根因：download_update 早期硬编码
        ``VibeOCR-v{version}-win64.zip``，而 release 实际 asset 名是
        ``VibeOCR-Classic-v{version}-win64.zip``，文件名对不上 → URL 404。
        现在文件名从 release API 带下来，与产物解耦。
        """
        from vibeocr.services.update_service import UpdateInfo

        release = {
            "tag_name": "v0.4.34",
            "body": "",
            "assets": [
                {
                    "name": "VibeOCR-Classic-v0.4.34-win64.zip",
                    "browser_download_url": "https://github.com/x/Classic.zip",
                    "size": 171532573,
                },
                {
                    "name": "VibeOCR-Classic-v0.4.34-win64.zip.sha256",
                    "browser_download_url": "https://github.com/x/Classic.zip.sha256",
                },
            ],
        }
        info = UpdateInfo.from_release(release)
        assert info.version == "0.4.34"
        assert info.zip_filename == "VibeOCR-Classic-v0.4.34-win64.zip"
        assert info.sha256_filename == "VibeOCR-Classic-v0.4.34-win64.zip.sha256"
        assert "Classic" in info.download_url
        assert info.file_size == 171532573

    def test_classic_preferred_when_both_zip_present(self):
        """release 同时发布 Classic 与 Next 两个 zip 时，必须选 Classic。

        本模块（update_service.py）只在 Classic（PySide6/Python）进程运行；
        WinUI Next 是 C# 应用有独立更新链路。选错前端会导致下到无法运行的包。
        """
        from vibeocr.services.update_service import UpdateInfo

        release = {
            "tag_name": "v0.5.0",
            "body": "",
            "assets": [
                {
                    "name": "VibeOCR-Next-v0.5.0-win64.zip",
                    "browser_download_url": "https://github.com/x/Next.zip",
                    "size": 50000000,
                },
                {
                    "name": "VibeOCR-Next-v0.5.0-win64.zip.sha256",
                    "browser_download_url": "https://github.com/x/Next.zip.sha256",
                },
                {
                    "name": "VibeOCR-Classic-v0.5.0-win64.zip",
                    "browser_download_url": "https://github.com/x/Classic.zip",
                    "size": 170000000,
                },
                {
                    "name": "VibeOCR-Classic-v0.5.0-win64.zip.sha256",
                    "browser_download_url": "https://github.com/x/Classic.zip.sha256",
                },
            ],
        }
        info = UpdateInfo.from_release(release)
        assert info.zip_filename == "VibeOCR-Classic-v0.5.0-win64.zip"
        assert info.download_url == "https://github.com/x/Classic.zip"
        assert info.file_size == 170000000

    def test_legacy_release_without_classic_still_works(self):
        """历史 release（v0.4.28 及之前，无 -Classic- 命名）走回退分支仍能解析。

        兼容性：回退取第一个匹配 .zip 的 asset，保证老 release 的 asset 也能被选中。
        """
        from vibeocr.services.update_service import UpdateInfo

        release = {
            "tag_name": "v0.4.28",
            "body": "",
            "assets": [
                {
                    "name": "VibeOCR-v0.4.28-win64.zip",
                    "browser_download_url": "https://github.com/x/legacy.zip",
                    "size": 160000000,
                },
                {
                    "name": "VibeOCR-v0.4.28-win64.zip.sha256",
                    "browser_download_url": "https://github.com/x/legacy.zip.sha256",
                },
            ],
        }
        info = UpdateInfo.from_release(release)
        assert info.zip_filename == "VibeOCR-v0.4.28-win64.zip"
        assert info.download_url == "https://github.com/x/legacy.zip"


class TestLocalVersion:
    """本地版本读取测试"""

    def test_read_version_json(self, tmp_path):
        from vibeocr.services.update_service import read_local_version

        version_file = tmp_path / "version.json"
        version_file.write_text(json.dumps({"version": "0.1.0"}), encoding="utf-8")
        assert read_local_version(version_file) == "0.1.0"

    def test_read_version_json_missing(self, tmp_path):
        # 缺失时回退到 __version__（开发态也能检查更新），而非 0.0.0。
        from vibeocr import __version__
        from vibeocr.services.update_service import read_local_version

        assert read_local_version(tmp_path / "nonexistent.json") == __version__

    def test_read_version_json_corrupt(self, tmp_path):
        # 损坏时同样回退到 __version__，与缺失语义一致。
        from vibeocr import __version__
        from vibeocr.services.update_service import read_local_version

        version_file = tmp_path / "version.json"
        version_file.write_text("not json", encoding="utf-8")
        assert read_local_version(version_file) == __version__


class TestCheckForUpdates:
    """远程版本检查测试（GitHub only）"""

    def test_check_has_update(self):
        from vibeocr.services.update_service import check_for_updates

        mock_release = {
            "tag_name": "v99.0.0",
            "body": "test",
            "assets": [
                {
                    "name": "VibeOCR-v99.0.0-win64.zip",
                    "browser_download_url": "http://test.zip",
                    "size": 100,
                },
                {
                    "name": "VibeOCR-v99.0.0-win64.zip.sha256",
                    "browser_download_url": "http://test.sha256",
                },
            ],
        }
        with patch(
            "vibeocr.services.update_service._fetch_release",
            return_value=mock_release,
        ):
            update_info, fetch_ok = _run(check_for_updates("0.1.0"))
        assert update_info is not None
        assert update_info.version == "99.0.0"
        assert fetch_ok is True

    def test_check_no_update(self):
        from vibeocr.services.update_service import check_for_updates

        mock_release = {
            "tag_name": "v0.1.0",
            "body": "",
            "assets": [
                {
                    "name": "VibeOCR-v0.1.0-win64.zip",
                    "browser_download_url": "http://test.zip",
                    "size": 0,
                },
                {
                    "name": "VibeOCR-v0.1.0-win64.zip.sha256",
                    "browser_download_url": "http://test.sha256",
                },
            ],
        }
        with patch(
            "vibeocr.services.update_service._fetch_release",
            return_value=mock_release,
        ):
            update_info, fetch_ok = _run(check_for_updates("0.1.0"))
        assert update_info is None
        assert fetch_ok is True

    def test_check_github_unreachable(self):
        """GitHub 请求失败 → 返回 (None, False)，上层据此提示手动下载"""
        from vibeocr.services.update_service import check_for_updates

        with patch(
            "vibeocr.services.update_service._fetch_release",
            return_value=None,
        ):
            update_info, fetch_ok = _run(check_for_updates("0.1.0"))
        assert update_info is None
        assert fetch_ok is False


class TestVerifySha256:
    """SHA256 校验测试"""

    def test_verify_sha256(self, tmp_path):
        from vibeocr.services.update_service import verify_sha256

        test_file = tmp_path / "test.zip"
        test_file.write_bytes(b"hello world")
        sha256_file = tmp_path / "test.zip.sha256"
        expected = hashlib.sha256(b"hello world").hexdigest()
        sha256_file.write_text(f"{expected}  test.zip\n", encoding="utf-8")
        assert verify_sha256(test_file, sha256_file) is True

    def test_verify_sha256_mismatch(self, tmp_path):
        from vibeocr.services.update_service import verify_sha256

        test_file = tmp_path / "test.zip"
        test_file.write_bytes(b"hello world")
        sha256_file = tmp_path / "test.zip.sha256"
        sha256_file.write_text("0000000000000000  test.zip\n", encoding="utf-8")
        assert verify_sha256(test_file, sha256_file) is False

    def test_verify_sha256_missing_file(self, tmp_path):
        from vibeocr.services.update_service import verify_sha256

        test_file = tmp_path / "test.zip"
        test_file.write_bytes(b"data")
        assert verify_sha256(test_file, tmp_path / "missing.sha256") is False


class TestSkipVersion:
    """跳过版本管理测试"""

    def test_should_not_skip_by_default(self, tmp_path):
        from vibeocr.services.update_service import should_skip_version

        assert should_skip_version("0.2.0", tmp_path / "update_settings.json") is False

    def test_save_and_check_skip(self, tmp_path):
        from vibeocr.services.update_service import (
            save_skip_version,
            should_skip_version,
        )

        settings_path = tmp_path / "update_settings.json"
        save_skip_version("0.2.0", settings_path)
        assert should_skip_version("0.2.0", settings_path) is True
        assert should_skip_version("0.3.0", settings_path) is False

    def test_overwrite_skip(self, tmp_path):
        from vibeocr.services.update_service import load_skip_version, save_skip_version

        settings_path = tmp_path / "update_settings.json"
        save_skip_version("0.2.0", settings_path)
        save_skip_version("0.3.0", settings_path)
        assert load_skip_version(settings_path) == "0.3.0"


class TestDownloadUpdateMultiSource:
    """download_update 多源回退测试（mock _download_zip_with_sha）"""

    def test_returns_path_when_first_source_succeeds(self, tmp_path):
        from vibeocr.services.update_service import (
            DOWNLOAD_REASON_OK,
            SourceAttempt,
            download_update,
        )

        info = _make_update_info()
        with patch(
            "vibeocr.services.update_service._download_zip_with_sha",
            new_callable=AsyncMock,
            return_value=SourceAttempt(True, DOWNLOAD_REASON_OK),
        ) as mock_dl:
            result, reasons = _run(download_update(info, tmp_path))
        assert result is not None
        assert result.name == "VibeOCR-v0.3.1-win64.zip"
        assert reasons == []
        mock_dl.assert_called_once()

    def test_falls_back_to_next_source_on_failure(self, tmp_path):
        """国内首源失败 → 换源成功（domestic 有多候选才能验证换源）"""
        from vibeocr.services.update_service import (
            DOWNLOAD_REASON_HTTP_ERROR,
            DOWNLOAD_REASON_OK,
            SourceAttempt,
            download_update,
        )

        info = _make_update_info()
        with patch(
            "vibeocr.services.update_service._detect_network_type",
            return_value="domestic",
        ), patch(
            "vibeocr.services.update_service._download_zip_with_sha",
            new_callable=AsyncMock,
            side_effect=[
                SourceAttempt(False, DOWNLOAD_REASON_HTTP_ERROR),
                SourceAttempt(True, DOWNLOAD_REASON_OK),
            ],
        ) as mock_dl:
            result, reasons = _run(download_update(info, tmp_path))
        assert result is not None
        assert reasons == []
        assert mock_dl.call_count == 2  # 首源失败后换源成功

    def test_returns_none_with_reasons_when_all_sources_fail(self, tmp_path):
        """全部源失败：返回 (None, reasons)，reasons 反映真实失败原因"""
        from vibeocr.services.update_service import (
            DOWNLOAD_REASON_SHA_MISMATCH,
            SourceAttempt,
            download_update,
        )

        info = _make_update_info()
        with patch(
            "vibeocr.services.update_service._detect_network_type",
            return_value="international",
        ), patch(
            "vibeocr.services.update_service._download_zip_with_sha",
            new_callable=AsyncMock,
            return_value=SourceAttempt(False, DOWNLOAD_REASON_SHA_MISMATCH),
        ) as mock_dl:
            result, reasons = _run(download_update(info, tmp_path))
        assert result is None
        assert reasons == [DOWNLOAD_REASON_SHA_MISMATCH]
        assert mock_dl.call_count == 1  # 海外 1 候选（GitHub 直连）失败

    def test_domestic_uses_three_candidates(self, tmp_path):
        """国内走 3 候选（gh-proxy→ghproxy→GitHub）"""
        from vibeocr.services.update_service import (
            DOWNLOAD_REASON_HTTP_ERROR,
            SourceAttempt,
            download_update,
        )

        info = _make_update_info()
        with patch(
            "vibeocr.services.update_service._detect_network_type",
            return_value="domestic",
        ), patch(
            "vibeocr.services.update_service._download_zip_with_sha",
            new_callable=AsyncMock,
            return_value=SourceAttempt(False, DOWNLOAD_REASON_HTTP_ERROR),
        ) as mock_dl:
            _run(download_update(info, tmp_path))
        assert mock_dl.call_count == 3

    def test_source_switch_callback_invoked_on_each_failure(self, tmp_path):
        """每源失败触发换源回调，回调收到 (源名, reason)"""
        from vibeocr.services.update_service import (
            DOWNLOAD_REASON_SHA_MISMATCH,
            SourceAttempt,
            download_update,
        )

        info = _make_update_info()
        switches: list[tuple[str, str]] = []

        def _on_switch(source_name: str, reason: str) -> None:
            switches.append((source_name, reason))

        with patch(
            "vibeocr.services.update_service._detect_network_type",
            return_value="domestic",
        ), patch(
            "vibeocr.services.update_service._download_zip_with_sha",
            new_callable=AsyncMock,
            return_value=SourceAttempt(False, DOWNLOAD_REASON_SHA_MISMATCH),
        ):
            _run(
                download_update(
                    info, tmp_path, source_switch_callback=_on_switch
                )
            )
        # 国内 3 候选全部失败 → 3 次回调
        assert len(switches) == 3
        # 源名顺序：gh-proxy → ghproxy → GitHub
        assert [s for s, _ in switches] == [
            "gh-proxy",
            "ghproxy",
            "GitHub",
        ]
        # reason 正确透传
        assert all(r == DOWNLOAD_REASON_SHA_MISMATCH for _, r in switches)

    def test_uses_real_filename_from_update_info(self, tmp_path):
        """回归 v0.4.29+ 更新全挂：download_update 必须用 update_info.zip_filename
        拼 URL，而非硬编码 ``VibeOCR-v{version}-win64.zip``。

        场景：release v0.4.34 的真实 asset 名是 ``VibeOCR-Classic-v0.4.34-win64.zip``
        （bind_backend 步骤重命名加 -Classic- 区分双前端）。早期代码硬编码无 Classic
        的名字 → 三路源全 404 → 用户看到「连接失败」「校验失败」。
        现在从 update_info 带真实文件名下来，URL 必须含 Classic。
        """
        from vibeocr.services.update_service import (
            DOWNLOAD_REASON_OK,
            SourceAttempt,
            download_update,
        )

        info = _make_update_info(
            version="0.4.34",
            zip_filename="VibeOCR-Classic-v0.4.34-win64.zip",
            sha256_filename="VibeOCR-Classic-v0.4.34-win64.zip.sha256",
        )
        captured_urls: list[str] = []

        async def _capture_dl(client, zip_url, sha_url, zip_path, sha_path, cb, **kw):
            captured_urls.append(zip_url)
            return SourceAttempt(True, DOWNLOAD_REASON_OK)

        with patch(
            "vibeocr.services.update_service._detect_network_type",
            return_value="domestic",
        ), patch(
            "vibeocr.services.update_service._download_zip_with_sha",
            side_effect=_capture_dl,
        ):
            result, reasons = _run(download_update(info, tmp_path))

        assert result is not None
        assert reasons == []
        # 首源（gh-proxy）的 URL 必须含真实 Classic 文件名，而非硬编码名
        assert captured_urls, "未捕获到任何下载 URL"
        assert "VibeOCR-Classic-v0.4.34-win64.zip" in captured_urls[0]
        # 硬编码的旧名（无 Classic）绝不能出现
        assert "VibeOCR-v0.4.34-win64.zip" not in captured_urls[0].replace(
            "VibeOCR-Classic-v0.4.34-win64.zip", ""
        )

    def test_empty_zip_filename_returns_failure(self, tmp_path):
        """UpdateInfo 缺失 zip_filename（release 无匹配 asset）→ 立即失败，不拼 URL。

        空守卫防御：避免 download_update 拿空文件名拼出无意义的 URL 去重试 3 次。
        """
        from vibeocr.services.update_service import (
            DOWNLOAD_REASON_HTTP_ERROR,
            download_update,
        )

        info = _make_update_info(zip_filename="", sha256_filename="")
        with patch(
            "vibeocr.services.update_service._download_zip_with_sha",
            new_callable=AsyncMock,
        ) as mock_dl:
            result, reasons = _run(download_update(info, tmp_path))
        assert result is None
        assert reasons == [DOWNLOAD_REASON_HTTP_ERROR]
        mock_dl.assert_not_called()


class TestDownloadZipWithSha:
    """``_download_zip_with_sha`` 直接测试（用 mock httpx.AsyncClient 驱动真实协程）。

    覆盖成功路径、zip 非 200、sha 非 200、sha 不匹配、流式异常、进度回调六个分支。
    重点验证：sha URL 由调用方精确传入（不再盲拼），且 sha 非 200 / 不匹配
    会触发清理并换源（返回带 reason 的失败 SourceAttempt）。
    """

    ZIP_URL = "https://example.com/VibeOCR-v0.3.1-win64.zip"
    # sha URL 由调用方精确传入，而非被测代码盲拼
    EXPECTED_SHA_URL = "https://example.com/VibeOCR-v0.3.1-win64.zip.sha256"

    def _import(self):
        from vibeocr.services.update_service import _download_zip_with_sha

        return _download_zip_with_sha

    def test_success_writes_zip_and_returns_true(self, tmp_path):
        _download_zip_with_sha = self._import()

        zip_bytes = b"fake-zip-content"
        real_hash = hashlib.sha256(zip_bytes).hexdigest()
        stream = _make_stream_response(200, [zip_bytes])
        client = _make_client(
            stream_cm=stream,
            sha_status=200,
            sha_text=f"{real_hash}  VibeOCR-v0.3.1-win64.zip\n",
        )
        zip_path = tmp_path / "x.zip"
        sha_path = tmp_path / "x.zip.sha256"

        ok = _run(
            _download_zip_with_sha(
                client, self.ZIP_URL, self.EXPECTED_SHA_URL, zip_path, sha_path, None
            )
        )

        assert ok.ok is True
        assert ok.reason == "ok"
        # zip 真正落盘
        assert zip_path.exists()
        assert zip_path.read_bytes() == zip_bytes
        # sha 用的是调用方精确传入的 URL
        client.get.assert_awaited_once_with(self.EXPECTED_SHA_URL)
        # 校验文件也落盘
        assert sha_path.exists()

    def test_zip_non_200_returns_false_after_sha_preflight(self, tmp_path):
        _download_zip_with_sha = self._import()

        stream = _make_stream_response(404, [b"not found"])
        client = _make_client(stream_cm=stream, sha_text="0" * 64)
        zip_path = tmp_path / "x.zip"
        sha_path = tmp_path / "x.zip.sha256"

        ok = _run(
            _download_zip_with_sha(
                client, self.ZIP_URL, self.EXPECTED_SHA_URL, zip_path, sha_path, None
            )
        )

        assert ok.ok is False
        assert ok.reason == "http_error"
        # SHA 小文件先行预检；通过后 zip 非 200 直接返回且不落盘。
        assert not zip_path.exists()
        assert not sha_path.exists()
        client.get.assert_awaited_once_with(self.EXPECTED_SHA_URL)

    def test_sha_non_200_cleans_up_zip(self, tmp_path):
        _download_zip_with_sha = self._import()

        zip_bytes = b"fake-zip-content"
        stream = _make_stream_response(200, [zip_bytes])
        # SHA 预检端点非 200，应跳过大包下载。
        client = _make_client(stream_cm=stream, sha_status=404)
        zip_path = tmp_path / "x.zip"
        sha_path = tmp_path / "x.zip.sha256"

        ok = _run(
            _download_zip_with_sha(
                client, self.ZIP_URL, self.EXPECTED_SHA_URL, zip_path, sha_path, None
            )
        )

        assert ok.ok is False
        assert ok.reason == "sha_missing"
        client.get.assert_awaited_once_with(self.EXPECTED_SHA_URL)
        # sha 非 200：已落盘的 zip 必须被清理；sha 文件根本没写
        assert not zip_path.exists()
        assert not sha_path.exists()

    def test_invalid_sha_response_skips_large_zip_download(self, tmp_path):
        """代理返回 HTML/错误页时应在下载大包前立即换源。"""
        _download_zip_with_sha = self._import()
        stream = _make_stream_response(200, [b"large package"])
        client = _make_client(
            stream_cm=stream,
            sha_status=200,
            sha_text="<html>bad gateway</html>",
        )
        zip_path = tmp_path / "x.zip"
        sha_path = tmp_path / "x.zip.sha256"

        result = _run(
            _download_zip_with_sha(
                client, self.ZIP_URL, self.EXPECTED_SHA_URL, zip_path, sha_path, None
            )
        )

        assert result.ok is False
        assert result.reason == "sha_mismatch"
        client.stream.assert_not_called()
        assert not zip_path.exists()

    def test_sha_mismatch_cleans_up_zip_and_sha(self, tmp_path):
        _download_zip_with_sha = self._import()

        zip_bytes = b"fake-zip-content"
        stream = _make_stream_response(200, [zip_bytes])
        # sha 200 但内容与 zip 实际哈希不符
        client = _make_client(
            stream_cm=stream,
            sha_status=200,
            sha_text="0000000000000000000000000000000000000000000000000000000000000000  x.zip\n",
        )
        zip_path = tmp_path / "x.zip"
        sha_path = tmp_path / "x.zip.sha256"

        ok = _run(
            _download_zip_with_sha(
                client, self.ZIP_URL, self.EXPECTED_SHA_URL, zip_path, sha_path, None
            )
        )

        assert ok.ok is False
        assert ok.reason == "sha_mismatch"
        client.get.assert_awaited_once_with(self.EXPECTED_SHA_URL)
        # 不匹配：zip 与 sha 文件均应被清理
        assert not zip_path.exists()
        assert not sha_path.exists()

    def test_exception_during_stream_returns_false_and_cleans_up(self, tmp_path):
        _download_zip_with_sha = self._import()

        # client.stream 抛异常（被宽 except 捕获）
        client = _make_client(
            stream_side_effect=httpx.ConnectError("boom"),
            sha_text="0" * 64,
        )
        zip_path = tmp_path / "x.zip"
        sha_path = tmp_path / "x.zip.sha256"

        ok = _run(
            _download_zip_with_sha(
                client, self.ZIP_URL, self.EXPECTED_SHA_URL, zip_path, sha_path, None
            )
        )

        # 宽 except 吞掉异常并返回失败 SourceAttempt，不抛出
        assert ok.ok is False
        assert ok.reason == "exception"
        assert not zip_path.exists()
        assert not sha_path.exists()

    def test_progress_callback_invoked_with_accumulating_totals(self, tmp_path):
        _download_zip_with_sha = self._import()

        # 分两块返回，验证 downloaded 累加
        chunk_a, chunk_b = b"AAAA", b"BBBBBBBB"  # 4 + 8 = 12 字节
        zip_bytes = chunk_a + chunk_b
        real_hash = hashlib.sha256(zip_bytes).hexdigest()
        stream = _make_stream_response(200, [chunk_a, chunk_b])
        client = _make_client(
            stream_cm=stream,
            sha_status=200,
            sha_text=f"{real_hash}  x.zip\n",
        )
        zip_path = tmp_path / "x.zip"
        sha_path = tmp_path / "x.zip.sha256"

        calls: list[tuple[int, int]] = []

        def _capture(downloaded: int, total: int) -> None:
            calls.append((downloaded, total))

        ok = _run(
            _download_zip_with_sha(
                client, self.ZIP_URL, self.EXPECTED_SHA_URL, zip_path, sha_path, _capture
            )
        )

        assert ok.ok is True
        assert zip_path.read_bytes() == zip_bytes
        # 至少每块回调一次
        assert len(calls) >= 2
        # 每次回调的 total 即 content-length
        total = len(zip_bytes)
        assert all(t == total for _, t in calls)
        # downloaded 单调累加，最后一次等于总字节数
        downloaded_seq = [d for d, _ in calls]
        assert downloaded_seq == sorted(downloaded_seq)
        assert downloaded_seq[-1] == total


class TestSourceLabel:
    """_source_label：URL → 人类可读源名"""

    def test_known_sources(self):
        from vibeocr.services.update_service import _source_label

        assert _source_label("https://gh-proxy.com/x") == "gh-proxy"
        assert _source_label("https://ghproxy.com/x") == "ghproxy"
        assert _source_label("https://github.com/x") == "GitHub"

    def test_unknown_falls_back_to_url(self):
        from vibeocr.services.update_service import _source_label

        assert _source_label("https://cdn.example.com/x") == "https://cdn.example.com/x"


class TestFormatFailureMessage:
    """_format_failure_message：失败原因分桶文案"""

    def _patch_net(self, net="international"):
        return patch(
            "vibeocr.services.update_service._detect_network_type",
            return_value=net,
        )

    def test_sha_mismatch_mentions_integrity(self):
        from vibeocr.pyside.update import _format_failure_message
        from vibeocr.services.update_service import DOWNLOAD_REASON_SHA_MISMATCH

        with self._patch_net():
            msg = _format_failure_message(
                ["http_error", DOWNLOAD_REASON_SHA_MISMATCH]
            )
        # 最坏原因（完整性）优先，必须出现明确措辞，而不是泛泛「网络问题」
        assert "完整性校验失败" in msg
        assert "网络" not in msg

    def test_sha_missing_mentions_missing_checksum(self):
        from vibeocr.pyside.update import _format_failure_message
        from vibeocr.services.update_service import DOWNLOAD_REASON_SHA_MISSING

        with self._patch_net():
            msg = _format_failure_message(
                ["http_error", DOWNLOAD_REASON_SHA_MISSING]
            )
        assert "缺少 SHA256 校验文件" in msg

    def test_all_http_errors_mentions_network(self):
        from vibeocr.pyside.update import _format_failure_message
        from vibeocr.services.update_service import DOWNLOAD_REASON_HTTP_ERROR

        with self._patch_net():
            msg = _format_failure_message(
                [DOWNLOAD_REASON_HTTP_ERROR, DOWNLOAD_REASON_HTTP_ERROR]
            )
        assert "无法连接服务器" in msg

    def test_message_includes_manual_download_link(self):
        from vibeocr.pyside.update import _format_failure_message
        from vibeocr.services.update_service import DOWNLOAD_REASON_HTTP_ERROR

        with self._patch_net("domestic"):
            msg = _format_failure_message([DOWNLOAD_REASON_HTTP_ERROR])
        # 手动下载链接指向 GitHub
        assert "github.com" in msg


class TestCheckAndPromptConcurrency:
    """check_and_prompt 并发互斥回归测试。

    背景：启动检查（main._check_update）与关于页"检查更新"按钮
    （AboutTab._on_check_update）各自 ensure_future 起 check_and_prompt。
    两层防御协同保证不触发 ``RuntimeError: Cannot enter into task ... while
    another task ... is being executed``（asyncio ``_enter_task`` 重入保护）：

    1. **根因修复**：模态对话框经 ``await_dialog``（``show()`` + ``finished``
       信号 → ``asyncio.Future``）非阻塞 await，不再跑 ``exec()`` 的嵌套事件循环。
       嵌套循环是重入错误的源头——它让事件循环唤醒其它任务并对其 ``_enter_task``，
       而当前任务仍处于「已 enter」状态。
    2. **串行化**：类级 ``asyncio.Lock`` 让两个调用点排队，避免并发弹出两个对话框。

    本测试验证锁的串行化契约：两并发调用的临界区不重叠（lock 持有期间无第二个
    in-flight），且第二次调用正常等到第一次释放后执行（不死锁、不丢弃）。
    """

    def _make_service(self, tmp_path, monkeypatch):
        """构造 UpdateService，version.json/缓存/设置均落在 tmp_path 隔离区。"""
        from vibeocr.pyside import update as update_ui
        from vibeocr.services import env_config

        # 隔离 data 目录，避免污染真实用户态目录
        (tmp_path / "version.json").write_text(
            json.dumps({"version": "0.1.0"}), encoding="utf-8"
        )
        monkeypatch.setattr(
            env_config, "get_data_dir", lambda: tmp_path, raising=False
        )
        # get_update_cache_dir / get_update_settings_path 走 get_data_dir，但已被模块顶层
        # import 时绑定，直接 patch 它们的返回更稳妥
        monkeypatch.setattr(
            env_config,
            "get_update_cache_dir",
            lambda: tmp_path / "cache" / "update",
            raising=False,
        )
        monkeypatch.setattr(
            env_config,
            "get_update_settings_path",
            lambda: tmp_path / "settings" / "update_settings.json",
            raising=False,
        )

        # 重置类级锁，避免上一个测试遗留的锁状态干扰
        update_ui.UpdateService._check_lock = None

        return update_ui.UpdateService(tmp_path)

    def test_two_concurrent_check_and_prompt_do_not_overlap(self, tmp_path, monkeypatch):
        """两并发 check_and_prompt：临界区互斥，第二个等第一个释放后才进入。"""
        from vibeocr.services import update_service

        service = self._make_service(tmp_path, monkeypatch)

        # 远程版本与本地相同 → check_and_prompt 走「已是最新」早退，不弹任何对话框，
        # 让测试不依赖 Qt modal。但早退发生在 await check_for_updates 之后，
        # 仍会与第二个调用在网络 await 点交错——正好覆盖竞态窗口。
        mock_release = {
            "tag_name": "v0.1.0",
            "body": "",
            "assets": [
                {
                    "name": "VibeOCR-v0.1.0-win64.zip",
                    "browser_download_url": "http://test.zip",
                    "size": 0,
                }
            ],
        }

        in_flight = 0
        max_in_flight = 0

        async def fake_fetch(*a, **kw):
            nonlocal in_flight, max_in_flight
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            # 让出事件循环，给另一个并发任务机会尝试进入临界区
            await asyncio.sleep(0)
            in_flight -= 1
            return mock_release

        monkeypatch.setattr(update_service, "_fetch_release", fake_fetch)

        async def driver():
            t1 = asyncio.ensure_future(service.check_and_prompt(None))
            t2 = asyncio.ensure_future(service.check_and_prompt(None))
            await asyncio.gather(t1, t2)

        asyncio.run(driver())

        # 临界区一次只允许一个任务进入：max_in_flight 必须为 1。
        # 修复前无锁时两任务会同时在 _fetch_release 里 in_flight=2。
        assert max_in_flight == 1, (
            f"check_and_prompt 并发互斥失败：max_in_flight={max_in_flight}（应为 1），"
            "说明两个更新检查任务的临界区发生重叠，会触发 asyncio _enter_task 重入错误。"
        )


class TestAwaitDialog:
    """await_dialog 回归测试。

    ``await_dialog`` 用 ``show()``（非阻塞）+ ``finished`` 信号 → ``asyncio.Future``
    替代阻塞的 ``dialog.exec()``，根治 qasync 嵌套事件循环触发的 ``_enter_task``
    重入 ``RuntimeError``。本测试验证该桥接的契约：

    1. ``show()`` 后另一个并发任务能在同一事件循环里推进（证明非阻塞，事件循环
       自由转动）—— 这正是原 ``exec()`` 丢失的属性。
    2. ``finished(result_code)`` 触发后 ``await`` 解除并返回该结果码。

    用 duck-typed fake dialog（不依赖真实 Qt 事件循环）：``finished`` 是个简单
    回调注册器，``show`` 用 ``loop.call_soon`` 异步触发 finish，模拟用户点按钮关闭。
    这在纯 ``asyncio.run`` 下即可验证桥接逻辑，无需 qasync / pytest-qt 事件处理。
    """

    def test_await_dialog_returns_finished_code_and_is_nonblocking(self):
        """await_dialog：返回 finished 结果码；show 期间事件循环可并发跑其它任务。"""
        from vibeocr.pyside.update import await_dialog

        class _FakeSignal:
            """最小信号桩：connect 注册回调，emit 调用它（仿 PySide6 Signal 语义）。"""

            def __init__(self) -> None:
                self._cb = None

            def connect(self, cb) -> None:
                self._cb = cb

            def emit(self, code: int) -> None:
                if self._cb is not None:
                    self._cb(code)

        class FakeDialog:
            """最小化 duck-type：await_dialog 只用 finished.connect / show 两个接口。"""

            def __init__(self) -> None:
                self.finished = _FakeSignal()
                self.showed = False

            def show(self) -> None:
                self.showed = True
                # 异步触发 finished，模拟用户关闭对话框。用 call_soon 而非直接调用，
                # 确保 await_dialog 先 await 挂起后再 resolve（测真正的非阻塞路径）。
                loop = asyncio.get_event_loop()
                loop.call_soon(self.finished.emit, 42)

        other_ran = []

        async def driver():
            dlg = FakeDialog()

            async def other():
                # 并发任务：await_dialog 真非阻塞时，它会在对话框 finish 前被推进。
                other_ran.append(True)

            other_task = asyncio.ensure_future(other())
            code = await await_dialog(dlg)  # type: ignore[arg-type]
            await other_task
            return code, dlg.showed

        code, showed = asyncio.run(driver())

        assert showed, "await_dialog 应调用 dialog.show()"
        assert code == 42, f"await_dialog 应返回 finished 的结果码 42，实际 {code}"
        assert other_ran, (
            "await_dialog 期间事件循环未能推进并发任务，说明仍是阻塞式（exec() 回归）"
        )


class TestDownloadCancel:
    """下载取消（协作式 asyncio.Event）回归测试。

    覆盖三个取消检查点：
    1. download_update 入口（进度框刚弹出就取消）；
    2. download_update 换源间隙（前一源失败、下一源开始前）；
    3. _download_zip_with_sha 流式块级（大文件下载中途）。

    取消语义：返回 (None, [cancelled])，不再尝试后续源；半成品 zip 被清理。
    """

    def _make_info(self):
        return _make_update_info()

    def test_download_update_aborts_when_cancel_event_set_before_start(self, tmp_path):
        """进入 download_update 前 cancel_event 已 set → 立即返回，不触发任何下载。"""
        from vibeocr.services.update_service import (
            DOWNLOAD_REASON_CANCELLED,
            download_update,
        )

        info = self._make_info()
        cancel_event = asyncio.Event()
        cancel_event.set()

        with patch(
            "vibeocr.services.update_service._download_zip_with_sha",
            new_callable=AsyncMock,
        ) as mock_dl:
            result, reasons = _run(
                download_update(info, tmp_path, cancel_event=cancel_event)
            )

        assert result is None
        assert reasons == [DOWNLOAD_REASON_CANCELLED]
        # 入口短路：根本不应进入任何源的下载
        mock_dl.assert_not_called()

    def test_download_update_aborts_between_sources_on_cancel(self, tmp_path):
        """前一源 sha_mismatch 失败返回 cancelled 语义 → 不再尝试后续源。

        用 domestic（多候选）验证：第一个源返回 cancelled 后，download_update
        应 break 循环，第二个源不会被调用。
        """
        from vibeocr.services.update_service import (
            DOWNLOAD_REASON_CANCELLED,
            SourceAttempt,
            download_update,
        )

        info = self._make_info()
        with patch(
            "vibeocr.services.update_service._detect_network_type",
            return_value="domestic",
        ), patch(
            "vibeocr.services.update_service._download_zip_with_sha",
            new_callable=AsyncMock,
            return_value=SourceAttempt(False, DOWNLOAD_REASON_CANCELLED),
        ) as mock_dl:
            result, reasons = _run(download_update(info, tmp_path))

        assert result is None
        # 仅第一个源被尝试，cancelled 让循环立即 break，不试 ghproxy / GitHub
        assert mock_dl.call_count == 1
        assert reasons == [DOWNLOAD_REASON_CANCELLED]

    def test_download_update_aborts_in_loop_when_event_set_between_sources(self, tmp_path):
        """换源间隙 event 被 set → break 循环，后续源不被调用。

        模拟：第一源 http_error 失败（非取消），编排器此时 set event；
        download_update 在下一源循环顶部检测到 → 跳出，fail_reasons 仅含第一源原因。
        """
        from vibeocr.services.update_service import (
            DOWNLOAD_REASON_HTTP_ERROR,
            SourceAttempt,
            download_update,
        )

        info = self._make_info()
        cancel_event = asyncio.Event()

        async def fake_dl(*a, **kw):
            # 第一次调用（第一源）失败后，set event；下一次循环顶部检测到就 break
            cancel_event.set()
            return SourceAttempt(False, DOWNLOAD_REASON_HTTP_ERROR)

        with patch(
            "vibeocr.services.update_service._detect_network_type",
            return_value="domestic",
        ), patch(
            "vibeocr.services.update_service._download_zip_with_sha",
            side_effect=fake_dl,
        ) as mock_dl:
            result, reasons = _run(
                download_update(info, tmp_path, cancel_event=cancel_event)
            )

        assert result is None
        assert mock_dl.call_count == 1  # 换源间隙被 event 拦下，第二源没机会跑
        assert reasons == [DOWNLOAD_REASON_HTTP_ERROR]  # 已记录的第一源失败原因

    def test_download_zip_with_sha_aborts_mid_stream_and_cleans_up(self, tmp_path):
        """流式块级取消：第一块写入后 set event → break，zip 被清理，回调次数受限。"""
        from vibeocr.services.update_service import (
            DOWNLOAD_REASON_CANCELLED,
            _download_zip_with_sha,
        )

        chunk_a, chunk_b = b"AAAA", b"BBBBBBBB"  # 2 块
        stream = _make_stream_response(200, [chunk_a, chunk_b])
        digest = hashlib.sha256(chunk_a + chunk_b).hexdigest()
        client = _make_client(stream_cm=stream, sha_status=200, sha_text=digest)

        cancel_event = asyncio.Event()
        calls: list[tuple[int, int]] = []

        def progress_cb(downloaded: int, total: int) -> None:
            calls.append((downloaded, total))
            # 第一块回调后立即取消（下一块写入前的检查点会 break）
            cancel_event.set()

        zip_path = tmp_path / "x.zip"
        sha_path = tmp_path / "x.zip.sha256"

        attempt = _run(
            _download_zip_with_sha(
                client,
                "https://example.com/zip",
                "https://example.com/zip.sha256",
                zip_path,
                sha_path,
                progress_cb,
                cancel_event=cancel_event,
            )
        )

        # 取消语义：返回 cancelled + zip 被清理
        assert attempt.ok is False
        assert attempt.reason == DOWNLOAD_REASON_CANCELLED
        assert not zip_path.exists()
        assert not sha_path.exists()
        # 关键：只回调了第一块就 break，没把第二块也写入（< 总块数 2）
        assert len(calls) == 1, f"应在第一块后立即中断，实际回调次数={len(calls)}"
        # SHA 已完成低成本预检，大包在第一块后立即中止。
        client.get.assert_awaited_once()

    def test_download_zip_with_sha_completes_when_not_cancelled(self, tmp_path):
        """回归保护：未取消时正常完成（取消逻辑不破坏成功路径）。"""
        from vibeocr.services.update_service import _download_zip_with_sha

        zip_bytes = b"fake-zip-content"
        real_hash = hashlib.sha256(zip_bytes).hexdigest()
        stream = _make_stream_response(200, [zip_bytes])
        client = _make_client(
            stream_cm=stream,
            sha_status=200,
            sha_text=f"{real_hash}  x.zip\n",
        )
        cancel_event = asyncio.Event()  # 不 set
        zip_path = tmp_path / "x.zip"
        sha_path = tmp_path / "x.zip.sha256"

        attempt = _run(
            _download_zip_with_sha(
                client,
                "https://example.com/zip",
                "https://example.com/zip.sha256",
                zip_path,
                sha_path,
                None,
                cancel_event=cancel_event,
            )
        )

        assert attempt.ok is True
        assert zip_path.read_bytes() == zip_bytes


class TestDownloadZipWithShaCancelAtShaStage:
    """SHA 预检与最终校验阶段的取消检查点回归测试。

    SHA 先行后仍需覆盖三个窗口：单源开始前、SHA 预检完成后、ZIP 下载完成到
    verify_sha256 启动前，确保换源优化不牺牲取消响应。
    """

    def _make_stream_response(self, status_code=200, chunks=None):
        """构造可直接用于 ``async with`` 的伪流式响应（zip 下载部分正常完成）。"""
        chunks = chunks or [b"zipdata"]
        total = sum(len(c) for c in chunks)

        async def _aiter():
            for c in chunks:
                yield c

        class _StreamCM:
            def __init__(self) -> None:
                self.status_code = status_code
                self.headers = {"content-length": str(total)}

            async def __aenter__(self) -> "_StreamCM":
                return self

            async def __aexit__(self, *exc) -> bool:
                return False

            def aiter_bytes(self, chunk_size: int = 65536):
                return _aiter()

        return _StreamCM()

    def test_cancel_before_sha_download(self, tmp_path):
        """进入单源尝试前已取消时，不发出 SHA 或 ZIP 请求。"""
        import asyncio

        from vibeocr.services.update_service import (
            DOWNLOAD_REASON_CANCELLED,
            _download_zip_with_sha,
        )

        cancel_event = asyncio.Event()
        client = MagicMock()
        client.get = AsyncMock()
        cancel_event.set()

        zip_path = tmp_path / "pkg.zip"
        sha_path = tmp_path / "pkg.sha256"
        result = _run(
            _download_zip_with_sha(
                client, "http://zip", "http://sha", zip_path, sha_path,
                progress_callback=None, cancel_event=cancel_event,
            )
        )

        assert result.ok is False
        assert result.reason == DOWNLOAD_REASON_CANCELLED
        assert not zip_path.exists()
        client.get.assert_not_called()
        client.stream.assert_not_called()

    def test_cancel_before_verify(self, tmp_path):
        """SHA 文件下完后、verify_sha256 启动前 set cancel → 返回 cancelled。

        模拟：client.get(sha_url) 正常返回 sha 内容，但在 verify 前取消。
        用 patch verify_sha256 为 side_effect set event 来模拟"verify 启动前"时机。
        """
        import asyncio

        from vibeocr.services.update_service import (
            DOWNLOAD_REASON_CANCELLED,
            _download_zip_with_sha,
        )

        zip_bytes = b"zipdata" * 100
        cancel_event = asyncio.Event()

        async def _aiter_then_cancel():
            yield zip_bytes
            cancel_event.set()

        stream_cm = self._make_stream_response(200, [zip_bytes])
        stream_cm.aiter_bytes = lambda chunk_size=65536: _aiter_then_cancel()
        client = MagicMock()
        client.stream.return_value = stream_cm
        sha_resp = MagicMock()
        sha_resp.status_code = 200
        sha_resp.text = f"{hashlib.sha256(zip_bytes).hexdigest()}  pkg.zip"
        client.get = AsyncMock(return_value=sha_resp)

        # verify_sha256 不会真正执行（取消在它之前），但 mock 确保它不被调用
        with patch(
            "vibeocr.services.update_service.verify_sha256",
            side_effect=lambda *a: (_ for _ in ()).throw(AssertionError("不应到达 verify")),
        ):
            zip_path = tmp_path / "pkg.zip"
            sha_path = tmp_path / "pkg.sha256"
            result = _run(
                _download_zip_with_sha(
                    client, "http://zip", "http://sha", zip_path, sha_path,
                    progress_callback=None, cancel_event=cancel_event,
                )
            )

        assert result.ok is False
        assert result.reason == DOWNLOAD_REASON_CANCELLED
        assert not zip_path.exists(), "zip 应被清理"
        assert not sha_path.exists(), "sha 应被清理"


class TestUpdateServiceStateSharing:
    """类级状态共享测试：跨实例的 cancel_event + download_state + listeners。"""

    def test_request_cancel_noop_when_idle(self, tmp_path, monkeypatch):
        """idle 态调用 request_cancel() 不抛异常（_active_cancel_event 为 None）。"""
        from vibeocr.pyside import update as us_mod
        from vibeocr.services import env_config

        monkeypatch.setattr(env_config, "get_update_cache_dir", lambda: tmp_path / "c")
        monkeypatch.setattr(env_config, "get_update_settings_path", lambda: tmp_path / "s.json")

        # 确保 idle 态
        us_mod.UpdateService._active_cancel_event = None
        # 不应抛 AttributeError
        us_mod.UpdateService.request_cancel()

    def test_request_cancel_sets_active_event_across_instances(
        self, tmp_path, monkeypatch
    ):
        """A 实例进入 downloading 后，B 实例 request_cancel() 能 set A 的 event。"""
        import asyncio

        from vibeocr.pyside import update as us_mod
        from vibeocr.services import env_config

        monkeypatch.setattr(env_config, "get_update_cache_dir", lambda: tmp_path / "c")
        monkeypatch.setattr(env_config, "get_update_settings_path", lambda: tmp_path / "s.json")

        app_dir = tmp_path / "app"
        app_dir.mkdir()
        us_mod.UpdateService(app_dir)  # service_a: 构造即生效（验证 init 不报错）
        service_b = us_mod.UpdateService(app_dir)

        # A 进入 downloading 态
        event = asyncio.Event()
        us_mod.UpdateService._active_cancel_event = event
        us_mod.UpdateService._download_state = "downloading"
        assert not event.is_set()

        # B（不同实例）发起取消
        service_b.__class__.request_cancel()
        assert event.is_set()

        # 清理
        us_mod.UpdateService._active_cancel_event = None
        us_mod.UpdateService._download_state = "idle"

    def test_register_state_listener_syncs_current_state(self):
        """注册监听器时立即同步当前 download_state（漏洞2回归）。"""
        from vibeocr.pyside.update import UpdateService

        original_state = UpdateService._download_state
        received = []
        try:
            UpdateService._download_state = "downloading"
            UpdateService.register_state_listener(received.append)
            assert received == ["downloading"], "注册时应立即收到当前状态"
        finally:
            UpdateService._download_state = original_state
            UpdateService.unregister_state_listener(received.append)


class TestDoDownloadAndUpdateNewArch:
    """新架构编排器：testzip → 抽取 updater → 启动暂存 updater → 握手 → 退出。

    重构后不再实例化 DownloadProgressDialog（改为状态栏纯文本），但编排器仍
    构造 QMessageBox（QDialog），需 QApplication 上下文，故注入 ``qapp`` fixture。
    其余内部方法（testzip / 抽取 / 启动 / 握手 / 退出）全部 mock。
    """

    def _make_service_with_zip(self, tmp_path, monkeypatch, qapp):
        """构造 service + 假 zip（含 VibeOCR/updater.exe）。"""
        import zipfile

        from vibeocr.pyside.update import UpdateService
        from vibeocr.services import env_config
        monkeypatch.setattr(env_config, "get_update_cache_dir", lambda: tmp_path / "cache" / "update")
        monkeypatch.setattr(env_config, "get_update_settings_path", lambda: tmp_path / "settings.json")
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        service = UpdateService(app_dir)

        zip_path = tmp_path / "cache" / "update" / "pkg.zip"
        zip_path.parent.mkdir(parents=True)
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("VibeOCR/updater.exe", b"updater")
            zf.writestr("VibeOCR/version.json", "{}")
        return service, zip_path

    def _make_info(self):
        return _make_update_info(
            version="9.9.9",
            download_url="http://x",
            sha256_url="http://x.sha256",
            zip_filename="VibeOCR-v9.9.9-win64.zip",
            sha256_filename="VibeOCR-v9.9.9-win64.zip.sha256",
        )

    def _mock_msgbox(self, us_mod, monkeypatch, critical_texts=None):
        """桩住 await_dialog：critical 弹窗捕获文案，其余直接返回 Ok。"""

        async def _fake_await_dialog(dlg):
            icon = dlg.icon()
            text = dlg.text()
            if icon == us_mod.QMessageBox.Icon.Critical and critical_texts is not None:
                critical_texts.append(text)
            return us_mod.QMessageBox.StandardButton.Ok

        monkeypatch.setattr(us_mod, "await_dialog", _fake_await_dialog)

    def test_corrupt_zip_shows_error_no_quit(self, tmp_path, monkeypatch, qapp):
        """testzip 失败 → 弹窗，不退出主程序（不 _force_quit）。"""
        service, zip_path = self._make_service_with_zip(tmp_path, monkeypatch, qapp)
        zip_path.write_bytes(b"corrupt")  # 把 zip 写坏

        force_quit_called = []
        monkeypatch.setattr(service, "_force_quit", lambda: force_quit_called.append(1))
        extract_called = []
        monkeypatch.setattr(service, "_extract_updater_from_zip", lambda p: extract_called.append(1) or p)

        from vibeocr.pyside import update as us_mod
        async def fake_download(*a, **k):
            return zip_path, []
        monkeypatch.setattr(us_mod, "download_update", fake_download)
        self._mock_msgbox(us_mod, monkeypatch)

        _run(service._do_download_and_update(self._make_info(), parent=None))

        assert not force_quit_called, "testzip 失败不应 _force_quit"
        assert not extract_called, "testzip 失败不应抽取 updater"

    def test_extract_failure_shows_error_no_quit(self, tmp_path, monkeypatch, qapp):
        """抽取 updater 失败 → 弹窗，不退出。"""
        service, zip_path = self._make_service_with_zip(tmp_path, monkeypatch, qapp)
        monkeypatch.setattr(service, "_verify_zip_integrity", lambda p: True)
        monkeypatch.setattr(service, "_extract_updater_from_zip",
                            lambda p: (_ for _ in ()).throw(RuntimeError("no updater")))

        force_quit_called = []
        monkeypatch.setattr(service, "_force_quit", lambda: force_quit_called.append(1))

        from vibeocr.pyside import update as us_mod
        async def fake_download(*a, **k):
            return zip_path, []
        monkeypatch.setattr(us_mod, "download_update", fake_download)
        self._mock_msgbox(us_mod, monkeypatch)

        _run(service._do_download_and_update(self._make_info(), parent=None))

        assert not force_quit_called, "抽取失败不应 _force_quit"

    def test_crashed_handshake_shows_manual_reinstall(self, tmp_path, monkeypatch, qapp):
        """新 updater 握手 crashed → 弹窗提示手动重装，不退出（无 self-update 兜底）。"""
        service, zip_path = self._make_service_with_zip(tmp_path, monkeypatch, qapp)
        monkeypatch.setattr(service, "_verify_zip_integrity", lambda p: True)
        monkeypatch.setattr(service, "_extract_updater_from_zip", lambda p: service._cache_dir / "updater.exe")

        async def fake_launch(zip_p, staged):
            return "crashed"
        monkeypatch.setattr(service, "_launch_updater", fake_launch)

        force_quit_called = []
        monkeypatch.setattr(service, "_force_quit", lambda: force_quit_called.append(1))

        from vibeocr.pyside import update as us_mod
        async def fake_download(*a, **k):
            return zip_path, []
        monkeypatch.setattr(us_mod, "download_update", fake_download)
        critical_msgs = []
        self._mock_msgbox(us_mod, monkeypatch, critical_texts=critical_msgs)

        _run(service._do_download_and_update(self._make_info(), parent=None))

        assert not force_quit_called, "crashed 不应 _force_quit"
        assert critical_msgs, "应弹窗提示"
        assert any("手动" in m for m in critical_msgs), "应提示手动重装"

    def test_download_success_sets_idle_before_testzip(
        self, tmp_path, monkeypatch, qapp
    ):
        """漏洞1回归：下载成功后、testzip 前立即切 idle（按钮不会在安装阶段误显取消）。"""
        service, zip_path = self._make_service_with_zip(tmp_path, monkeypatch, qapp)

        states_at_testzip = []

        def _spy_verify(p):
            # testzip 被调用时，记录此刻的类级状态
            states_at_testzip.append(
                (type(service)._download_state, type(service)._active_cancel_event)
            )
            return True

        monkeypatch.setattr(service, "_verify_zip_integrity", _spy_verify)
        monkeypatch.setattr(service, "_extract_updater_from_zip",
                            lambda p: service._cache_dir / "updater.exe")
        async def fake_launch(zip_p, staged):
            return "ready"
        monkeypatch.setattr(service, "_launch_updater", fake_launch)
        force_quit_called = []
        monkeypatch.setattr(service, "_force_quit", lambda: force_quit_called.append(1))

        from vibeocr.pyside import update as us_mod
        async def fake_download(*a, **k):
            return zip_path, []
        monkeypatch.setattr(us_mod, "download_update", fake_download)
        self._mock_msgbox(us_mod, monkeypatch)

        _run(service._do_download_and_update(self._make_info(), parent=None))

        assert states_at_testzip, "testzip 应被调用"
        state, cancel = states_at_testzip[0]
        assert state == "idle", "下载成功后应立即切 idle"
        assert cancel is None, "_active_cancel_event 应已清空"


class TestAboutTabButtonStateMachine:
    """关于页按钮状态机测试（漏洞2回归 + 按钮切换）。"""

    def test_button_shows_cancel_when_downloading_on_init(self, qtbot):
        """先进入 downloading 态，再构造 AboutTab → 按钮初始即「取消下载」。

        证明注册监听器时同步了当前状态（漏洞2修复）。
        """
        from vibeocr.pyside.update import UpdateService
        from vibeocr.views.tabs.about_tab import AboutTab

        original_state = UpdateService._download_state
        original_listeners = list(UpdateService._state_listeners)
        try:
            UpdateService._download_state = "downloading"
            UpdateService._state_listeners = list(original_listeners)

            tab = AboutTab()
            qtbot.addWidget(tab)

            assert tab._update_btn.text() == "取消下载"
        finally:
            UpdateService._download_state = original_state
            UpdateService._state_listeners = original_listeners

    def test_button_toggles_on_state_change(self, qtbot):
        """状态变更时按钮文本/样式切换。"""
        from vibeocr.pyside.update import UpdateService
        from vibeocr.views.tabs.about_tab import AboutTab

        original_state = UpdateService._download_state
        original_listeners = list(UpdateService._state_listeners)
        try:
            UpdateService._state_listeners = list(original_listeners)
            tab = AboutTab()
            qtbot.addWidget(tab)

            assert tab._update_btn.text() == "检查更新"

            UpdateService._set_download_state("downloading")
            assert tab._update_btn.text() == "取消下载"

            UpdateService._set_download_state("idle")
            assert tab._update_btn.text() == "检查更新"
        finally:
            UpdateService._download_state = original_state
            UpdateService._state_listeners = original_listeners


class TestProbeGithubReachable:
    """探测 api.github.com 是否可达：用于区分 international 环境下 GitHub 实际能否
    直连。不可达时 download_update 改走国内代理源序，避免「直连失败后才提示网络问题」。"""

    def test_2xx_3xx_4xx_treated_as_reachable(self):
        """2xx/3xx/4xx 均视为可达（4xx 说明能连上 GitHub，只是 rate limited 等）。"""
        from vibeocr.services.update_service import _probe_github_reachable

        for status in (200, 301, 403, 404):
            resp = MagicMock()
            resp.status_code = status
            with patch("httpx.AsyncClient") as mock_client_cls:
                client = AsyncMock()
                client.head.return_value = resp
                client.__aenter__.return_value = client
                client.__aexit__.return_value = False
                mock_client_cls.return_value = client
                assert _run(_probe_github_reachable()) is True

    def test_5xx_treated_as_unreachable(self):
        """5xx（服务端错误）视为不可达。"""
        from vibeocr.services.update_service import _probe_github_reachable

        resp = MagicMock()
        resp.status_code = 503
        with patch("httpx.AsyncClient") as mock_client_cls:
            client = AsyncMock()
            client.head.return_value = resp
            client.__aenter__.return_value = client
            client.__aexit__.return_value = False
            mock_client_cls.return_value = client
            assert _run(_probe_github_reachable()) is False

    def test_exception_treated_as_unreachable(self):
        """网络异常（DNS/连接/超时/SSL）一律视为不可达。"""
        from vibeocr.services.update_service import _probe_github_reachable

        with patch("httpx.AsyncClient") as mock_client_cls:
            client = AsyncMock()
            client.head.side_effect = httpx.ConnectError("boom")
            client.__aenter__.return_value = client
            client.__aexit__.return_value = False
            mock_client_cls.return_value = client
            assert _run(_probe_github_reachable()) is False


# ---------------------------------------------------------------------------
# download_update：GitHub 不可达时 international → domestic 源序修正
# ---------------------------------------------------------------------------


class TestDownloadUpdateGithubProbeFallback:
    """international 环境下若 GitHub 直连不可达，应改走国内代理源序（3 候选）。

    历史问题：海外网络但 GitHub 被墙时，NetworkDetector 判 international（直连），
    下载只在所有源失败后才提示「网络问题」，浪费一次完整下载。修复：下载前探测
    GitHub，不可达则降级 domestic 源序。
    """

    def test_international_github_unreachable_falls_back_to_domestic(self, tmp_path):
        """international + GitHub 不可达 → 用 domestic 源序（3 候选）。"""
        from vibeocr.services.update_service import (
            DOWNLOAD_REASON_HTTP_ERROR,
            SourceAttempt,
            download_update,
        )

        info = _make_update_info()
        with patch(
            "vibeocr.services.update_service._detect_network_type",
            return_value="international",
        ), patch(
            "vibeocr.services.update_service._probe_github_reachable",
            new_callable=AsyncMock,
            return_value=False,
        ), patch(
            "vibeocr.services.update_service._download_zip_with_sha",
            new_callable=AsyncMock,
            return_value=SourceAttempt(False, DOWNLOAD_REASON_HTTP_ERROR),
        ) as mock_dl:
            _run(download_update(info, tmp_path))
        # domestic 源序：3 候选（gh-proxy → ghproxy → GitHub）
        assert mock_dl.call_count == 3

    def test_international_github_reachable_keeps_international(self, tmp_path):
        """international + GitHub 可达 → 保持 international 源序（1 候选：GitHub 直连）。"""
        from vibeocr.services.update_service import (
            DOWNLOAD_REASON_HTTP_ERROR,
            SourceAttempt,
            download_update,
        )

        info = _make_update_info()
        with patch(
            "vibeocr.services.update_service._detect_network_type",
            return_value="international",
        ), patch(
            "vibeocr.services.update_service._probe_github_reachable",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "vibeocr.services.update_service._download_zip_with_sha",
            new_callable=AsyncMock,
            return_value=SourceAttempt(False, DOWNLOAD_REASON_HTTP_ERROR),
        ) as mock_dl:
            _run(download_update(info, tmp_path))
        # international 源序：1 候选（GitHub 直连）
        assert mock_dl.call_count == 1

    def test_domestic_does_not_probe_github(self, tmp_path):
        """domestic 环境本就代理优先，不应触发 GitHub 探测（避免多余请求）。"""
        from vibeocr.services.update_service import (
            DOWNLOAD_REASON_HTTP_ERROR,
            SourceAttempt,
            download_update,
        )

        info = _make_update_info()
        with patch(
            "vibeocr.services.update_service._detect_network_type",
            return_value="domestic",
        ), patch(
            "vibeocr.services.update_service._probe_github_reachable",
            new_callable=AsyncMock,
        ) as mock_probe, patch(
            "vibeocr.services.update_service._download_zip_with_sha",
            new_callable=AsyncMock,
            return_value=SourceAttempt(False, DOWNLOAD_REASON_HTTP_ERROR),
        ):
            _run(download_update(info, tmp_path))
        mock_probe.assert_not_awaited()


# ---------------------------------------------------------------------------
# _download_zip_with_sha：SHA256 校验移到线程池（不阻塞事件循环）
# ---------------------------------------------------------------------------


class TestDownloadZipWithShaThreadedVerify:
    """verify_sha256 通过 asyncio.to_thread 在线程池执行，避免 ~50MB 同步 read_bytes
    + 哈希阻塞 qasync 事件循环（历史 bug：下载完成后无响应）。"""

    def test_verify_runs_via_to_thread(self, tmp_path, monkeypatch):
        """校验应通过 asyncio.to_thread 派发，而非直接同步调用。

        历史 bug：校验直接 verify_sha256(...) 同步读整个 zip 入内存，在 qasync
        事件循环里冻结 UI 与取消响应。修复后必须经 to_thread。这里 mock to_thread，
        断言它被调用且第一参数是 verify_sha256，且校验函数确实在工作线程执行。
        """
        from vibeocr.services import update_service as us
        from vibeocr.services.update_service import _download_zip_with_sha

        zip_bytes = b"fake-zip-content"
        real_hash = hashlib.sha256(zip_bytes).hexdigest()
        stream = _make_stream_response(200, [zip_bytes])
        client = _make_client(
            stream_cm=stream,
            sha_status=200,
            sha_text=f"{real_hash}  x.zip\n",
        )
        zip_path = tmp_path / "x.zip"
        sha_path = tmp_path / "x.zip.sha256"

        # 包裹真实 to_thread，记录调用以断言校验确实走了它
        real_to_thread = asyncio.to_thread
        to_thread_calls: list[tuple] = []

        async def _spy_to_thread(func, *args, **kwargs):
            to_thread_calls.append((func, args, kwargs))
            return await real_to_thread(func, *args, **kwargs)

        monkeypatch.setattr(
            us.asyncio, "to_thread", _spy_to_thread, raising=True
        )

        ok = _run(
            _download_zip_with_sha(
                client,
                "https://example.com/x.zip",
                "https://example.com/x.zip.sha256",
                zip_path,
                sha_path,
                None,
            )
        )

        assert ok.ok is True
        # 校验经 to_thread 派发：唯一一次调用，第一参数是 verify_sha256
        assert len(to_thread_calls) == 1
        func, args, _kwargs = to_thread_calls[0]
        assert func is us.verify_sha256
        assert args == (zip_path, sha_path)


# ---------------------------------------------------------------------------
# _extract_updater_from_zip / _verify_zip_integrity：递送员职责（黄金法则）
# ---------------------------------------------------------------------------


class TestExtractUpdaterFromZip:
    """_extract_updater_from_zip：从 zip 按 arcname 抽取 VibeOCR/updater.exe 到暂存目录。"""

    def _make_service(self, tmp_path, monkeypatch):
        """构造 UpdateService，隔离 cache_dir 到 tmp_path。"""
        from vibeocr.pyside.update import UpdateService
        from vibeocr.services import env_config

        monkeypatch.setattr(
            env_config,
            "get_update_cache_dir",
            lambda: tmp_path / "cache" / "update",
        )
        monkeypatch.setattr(
            env_config,
            "get_update_settings_path",
            lambda: tmp_path / "settings.json",
        )
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        return UpdateService(app_dir)

    def test_extracts_updater_to_staging(self, tmp_path, monkeypatch):
        """正常：从 zip 抽取 VibeOCR/updater.exe 到 cache_dir/updater.exe。"""
        import zipfile

        service = self._make_service(tmp_path, monkeypatch)
        zip_path = tmp_path / "cache" / "update" / "pkg.zip"
        zip_path.parent.mkdir(parents=True)
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("VibeOCR/updater.exe", b"NEW UPDATER BINARY")
            zf.writestr("VibeOCR/VibeOCR.exe", b"main")
            zf.writestr("VibeOCR/version.json", "{}")

        result = service._extract_updater_from_zip(zip_path)

        assert result == service._cache_dir / "updater.exe"
        assert result.read_bytes() == b"NEW UPDATER BINARY"

    def test_missing_updater_in_zip_raises(self, tmp_path, monkeypatch):
        """zip 内无 VibeOCR/updater.exe → 抛 RuntimeError。"""
        import zipfile

        service = self._make_service(tmp_path, monkeypatch)
        zip_path = tmp_path / "cache" / "update" / "pkg.zip"
        zip_path.parent.mkdir(parents=True)
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("VibeOCR/VibeOCR.exe", b"main")

        with pytest.raises(RuntimeError, match=r"updater\.exe"):
            service._extract_updater_from_zip(zip_path)

    def test_does_not_extract_other_files(self, tmp_path, monkeypatch):
        """只抽 updater.exe，不碰其它文件（避免重复解压）。"""
        import zipfile

        service = self._make_service(tmp_path, monkeypatch)
        zip_path = tmp_path / "cache" / "update" / "pkg.zip"
        zip_path.parent.mkdir(parents=True)
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("VibeOCR/updater.exe", b"updater")
            zf.writestr("VibeOCR/_internal/big.dat", b"x" * 10000)

        service._extract_updater_from_zip(zip_path)

        # 只应有 updater.exe，不应有 _internal
        assert not (service._cache_dir / "_internal").exists()
        assert (service._cache_dir / "updater.exe").exists()


class TestVerifyZipIntegrity:
    """_verify_zip_integrity：zipfile testzip 包装。"""

    def _make_service(self, tmp_path, monkeypatch):
        from vibeocr.pyside.update import UpdateService
        from vibeocr.services import env_config

        monkeypatch.setattr(
            env_config,
            "get_update_cache_dir",
            lambda: tmp_path / "cache" / "update",
        )
        monkeypatch.setattr(
            env_config,
            "get_update_settings_path",
            lambda: tmp_path / "settings.json",
        )
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        return UpdateService(app_dir)

    def test_valid_zip_returns_true(self, tmp_path, monkeypatch):
        import zipfile

        service = self._make_service(tmp_path, monkeypatch)
        zip_path = tmp_path / "pkg.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("a.txt", "hello")
        assert service._verify_zip_integrity(zip_path) is True

    def test_corrupt_zip_returns_false(self, tmp_path, monkeypatch):
        service = self._make_service(tmp_path, monkeypatch)
        zip_path = tmp_path / "pkg.zip"
        zip_path.write_bytes(b"not a zip file at all")
        assert service._verify_zip_integrity(zip_path) is False

    def test_nonexistent_zip_returns_false(self, tmp_path, monkeypatch):
        service = self._make_service(tmp_path, monkeypatch)
        assert service._verify_zip_integrity(tmp_path / "nope.zip") is False


class TestDoDownloadAndUpdateThreadedDelivery:
    """编排器经 asyncio.to_thread 派发 testzip + 抽取 updater，避免同步阻塞冻结
    qasync 事件循环（历史 bug：下载完成后 UI 无响应退出）。

    与 ``TestDownloadZipWithShaThreadedVerify``（守护 verify_sha256）对齐：这两个
    同步 zip 操作同样会把 ~50-170MB 的读 + CRC/写盘压在事件循环线程上，必须经线程池。
    两个方法本身仍保持同步签名（供直接单元测试复用），契约只在调用点强制。
    """

    def _make_service_with_zip(self, tmp_path, monkeypatch, qapp):
        import zipfile

        from vibeocr.pyside.update import UpdateService
        from vibeocr.services import env_config

        monkeypatch.setattr(env_config, "get_update_cache_dir", lambda: tmp_path / "cache" / "update")
        monkeypatch.setattr(env_config, "get_update_settings_path", lambda: tmp_path / "settings.json")
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        service = UpdateService(app_dir)

        zip_path = tmp_path / "cache" / "update" / "pkg.zip"
        zip_path.parent.mkdir(parents=True)
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("VibeOCR/updater.exe", b"updater")
            zf.writestr("VibeOCR/version.json", "{}")
        return service, zip_path

    def _make_info(self):
        return _make_update_info(
            version="9.9.9",
            download_url="http://x",
            sha256_url="http://x.sha256",
            zip_filename="VibeOCR-v9.9.9-win64.zip",
            sha256_filename="VibeOCR-v9.9.9-win64.zip.sha256",
        )

    def _mock_msgbox(self, us_mod, monkeypatch, critical_texts=None):
        """桩住 await_dialog：critical 弹窗捕获文案，其余直接返回 Ok。

        生产代码改用 ``await await_dialog(QMessageBox(...))``（非阻塞）后，旧的对
        ``QMessageBox.information/critical`` 静态方法的 patch 已无法拦截。改为 patch
        模块级 ``await_dialog``：从传入的 QMessageBox 实例读 icon/text 判定类型，
        critical 时收集文案，统一返回 Ok。
        """

        async def _fake_await_dialog(dlg):
            icon = dlg.icon()
            text = dlg.text()
            if icon == us_mod.QMessageBox.Icon.Critical and critical_texts is not None:
                critical_texts.append(text)
            return us_mod.QMessageBox.StandardButton.Ok

        monkeypatch.setattr(us_mod, "await_dialog", _fake_await_dialog)

    def test_verify_zip_runs_via_to_thread(self, tmp_path, monkeypatch, qapp):
        """testzip 应在工作线程执行（asyncio.to_thread 派发），而非冻结事件循环。

        历史 bug（v0.4.18）：下载完成后 testzip 同步读整包做 CRC，在 qasync 协程里
        冻结事件循环，UI 无响应退出。修复后必须经线程池。用 corrupt zip 让 testzip
        走失败路径（弹 critical，已 mock），并记录被执行时的线程 id——断言它不在
        主线程（即确实派发到了工作线程）。
        """
        import threading

        from vibeocr.pyside import update as us

        service, zip_path = self._make_service_with_zip(tmp_path, monkeypatch, qapp)
        zip_path.write_bytes(b"corrupt")  # 让 testzip 失败，走 critical 分支

        # 记录 testzip 实际执行所在的线程。
        exec_thread_ids: list[int] = []
        real_verify = service._verify_zip_integrity

        def _spying_verify(path):
            exec_thread_ids.append(threading.get_ident())
            return real_verify(path)

        monkeypatch.setattr(service, "_verify_zip_integrity", _spying_verify)

        force_quit_called = []
        monkeypatch.setattr(service, "_force_quit", lambda: force_quit_called.append(1))

        async def fake_download(*a, **k):
            return zip_path, []
        monkeypatch.setattr(us, "download_update", fake_download)
        self._mock_msgbox(us, monkeypatch)

        _run(service._do_download_and_update(self._make_info(), parent=None))

        assert not force_quit_called, "testzip 失败不应 _force_quit"
        # 关键契约：testzip 在工作线程执行（asyncio.to_thread 派发），而非主线程。
        assert exec_thread_ids, "testzip 应被执行"
        assert exec_thread_ids[0] != threading.get_ident(), \
            "testzip 必须经 asyncio.to_thread 派发到工作线程（直接同步调用会冻结事件循环）"

    def test_extract_updater_runs_via_to_thread(self, tmp_path, monkeypatch, qapp):
        """抽取 updater 应在工作线程执行（asyncio.to_thread 派发）。

        与 testzip 同属下载后冻结事件循环的同步 zip I/O，必须经线程池。testzip 用
        真实有效 zip 放行，抽取阶段记录被执行时的线程 id，断言它不在主线程。
        """
        import threading

        from vibeocr.pyside import update as us

        service, zip_path = self._make_service_with_zip(tmp_path, monkeypatch, qapp)

        # testzip 放行（真实有效 zip），避免抢占到 extract 之前就 return。
        # 抽取阶段记录执行线程，断言非主线程即可——无需走完整握手（会启动真实进程）。
        exec_thread_ids: list[int] = []
        real_extract = service._extract_updater_from_zip

        def _spying_extract(path):
            exec_thread_ids.append(threading.get_ident())
            return real_extract(path)

        monkeypatch.setattr(service, "_extract_updater_from_zip", _spying_extract)
        # 握手直接返回 crashed 终止流程（不启动真实 updater 进程），验证目标是
        # extract 经 to_thread，而非握手结果。
        async def fake_launch(zip_p, staged):
            return "crashed"
        monkeypatch.setattr(service, "_launch_updater", fake_launch)
        force_quit_called = []
        monkeypatch.setattr(service, "_force_quit", lambda: force_quit_called.append(1))

        async def fake_download(*a, **k):
            return zip_path, []
        monkeypatch.setattr(us, "download_update", fake_download)
        self._mock_msgbox(us, monkeypatch)

        _run(service._do_download_and_update(self._make_info(), parent=None))

        assert not force_quit_called, "crashed 不应 _force_quit"
        # 关键契约：抽取 updater 在工作线程执行（asyncio.to_thread 派发）。
        assert exec_thread_ids, "抽取 updater 应被执行"
        assert exec_thread_ids[0] != threading.get_ident(), \
            "_extract_updater_from_zip 必须经 asyncio.to_thread 派发到工作线程（直接同步调用会冻结事件循环）"


# ---------------------------------------------------------------------------
# Bug 1：「稍后提醒」持久化（remind_later）—— 纯逻辑层
# ---------------------------------------------------------------------------


class TestRemindLater:
    """「稍后提醒」持久化测试。

    历史 bug：UpdateDialog 的「稍后提醒」按钮只把 action 设为 "cancel" 并 reject，
    check_and_prompt 不处理 "cancel" 分支，没有任何持久化——下次检查（启动自动 /
    手动）立刻再次弹窗，按钮形同虚设。

    修复：仿照 skip_version 三件套，新增 remind_later_until（unix 时间戳）持久化。
    自动检查（manual=False）命中暂缓窗口则不弹；手动检查（manual=True）始终弹
    （用户主动请求，忽略暂缓）。
    """

    def test_save_and_check_remind_later_active(self, tmp_path):
        """save 后、窗口内：is_remind_later_active 返回 True。"""
        from vibeocr.services.update_service import (
            REMIND_LATER_SECONDS,
            is_remind_later_active,
            save_remind_later,
        )

        settings_path = tmp_path / "update_settings.json"
        now = 1_700_000_000.0
        save_remind_later(now + REMIND_LATER_SECONDS, settings_path)
        # 同一时刻仍活跃
        assert is_remind_later_active(settings_path, now=now) is True
        # 窗口结束前一秒仍活跃
        assert (
            is_remind_later_active(settings_path, now=now + REMIND_LATER_SECONDS - 1)
            is True
        )

    def test_remind_later_expires_after_window(self, tmp_path):
        """超过窗口：is_remind_later_active 返回 False。"""
        from vibeocr.services.update_service import (
            REMIND_LATER_SECONDS,
            is_remind_later_active,
            save_remind_later,
        )

        settings_path = tmp_path / "update_settings.json"
        base = 1_700_000_000.0
        save_remind_later(base + REMIND_LATER_SECONDS, settings_path)
        # 到期那一刻即失效
        assert (
            is_remind_later_active(settings_path, now=base + REMIND_LATER_SECONDS)
            is False
        )
        # 远超窗口
        assert (
            is_remind_later_active(settings_path, now=base + REMIND_LATER_SECONDS * 10)
            is False
        )

    def test_remind_later_missing_returns_false(self, tmp_path):
        """无设置文件：is_remind_later_active 返回 False（默认不暂缓）。"""
        from vibeocr.services.update_service import is_remind_later_active

        assert (
            is_remind_later_active(tmp_path / "missing.json", now=1_700_000_000.0)
            is False
        )

    def test_remind_later_corrupt_returns_false(self, tmp_path):
        """设置文件损坏：返回 False（容错，不抛异常）。"""
        from vibeocr.services.update_service import is_remind_later_active

        settings_path = tmp_path / "update_settings.json"
        settings_path.write_text("not json", encoding="utf-8")
        assert (
            is_remind_later_active(settings_path, now=1_700_000_000.0) is False
        )

    def test_save_remind_later_preserves_skip_version(self, tmp_path):
        """save_remind_later 不能覆盖既有 skip_version（两个键共存）。"""
        from vibeocr.services.update_service import (
            load_skip_version,
            save_remind_later,
            save_skip_version,
        )

        settings_path = tmp_path / "update_settings.json"
        save_skip_version("0.9.9", settings_path)
        save_remind_later(1_700_000_000.0 + 86400, settings_path)
        # skip_version 仍在
        assert load_skip_version(settings_path) == "0.9.9"

    def test_remind_later_seconds_constant(self):
        """窗口常量：1 天（86400 秒）。"""
        from vibeocr.services.update_service import REMIND_LATER_SECONDS

        assert REMIND_LATER_SECONDS == 86400


# ---------------------------------------------------------------------------
# Bug 1：check_and_prompt 集成 —— manual 参数 / later action 处理
# ---------------------------------------------------------------------------


class TestCheckAndPromptRemindLater:
    """check_and_prompt 与 remind_later 集成测试。

    - 自动检查（manual=False）命中暂缓 → 不弹窗，状态栏提示「已暂缓」。
    - 手动检查（manual=True）忽略暂缓 → 始终弹窗。
    - 点「稍后提醒」→ 持久化 remind_later_until。
    """

    def _make_service(self, tmp_path, monkeypatch):
        from vibeocr.pyside import update as update_ui
        from vibeocr.services import env_config

        (tmp_path / "version.json").write_text(
            json.dumps({"version": "0.1.0"}), encoding="utf-8"
        )
        monkeypatch.setattr(
            env_config,
            "get_update_cache_dir",
            lambda: tmp_path / "cache" / "update",
            raising=False,
        )
        monkeypatch.setattr(
            env_config,
            "get_update_settings_path",
            lambda: tmp_path / "settings" / "update_settings.json",
            raising=False,
        )
        update_ui.UpdateService._check_lock = None
        return update_ui.UpdateService(tmp_path)

    def _mock_has_update(self, monkeypatch):
        """mock check_for_updates 返回有更新（远程版本更高）。"""
        from vibeocr.services import update_service

        info = _make_update_info(version="9.9.9")
        async def fake_check(current):
            return info, True
        monkeypatch.setattr(update_service, "check_for_updates", fake_check)
        return info

    def test_auto_check_skips_prompt_when_remind_later_active(
        self, tmp_path, monkeypatch, qapp
    ):
        """自动检查（manual=False）+ 暂缓活跃 → 不构造 UpdateDialog。"""
        from vibeocr.pyside import update as update_ui
        from vibeocr.services.update_service import (
            REMIND_LATER_SECONDS,
            save_remind_later,
        )

        service = self._make_service(tmp_path, monkeypatch)
        self._mock_has_update(monkeypatch)
        # 预置一个未到期的暂缓
        now = 1_700_000_000.0
        save_remind_later(
            now + REMIND_LATER_SECONDS, service._settings_path
        )

        dialog_created = []
        real_update_dialog = update_ui.UpdateDialog
        monkeypatch.setattr(
            update_ui,
            "UpdateDialog",
            lambda *a, **k: dialog_created.append(1) or real_update_dialog(*a, **k),
        )

        statuses: list[str] = []
        service._status_callback = lambda text, timeout=0: statuses.append(text)

        _run(service.check_and_prompt(None, manual=False, now=now))

        assert not dialog_created, "自动检查命中暂缓不应弹窗"
        assert any("暂缓" in s for s in statuses), "状态栏应提示已暂缓"

    def test_manual_check_prompts_despite_remind_later_active(
        self, tmp_path, monkeypatch, qapp
    ):
        """手动检查（manual=True）即使暂缓活跃也弹窗（用户主动请求）。"""
        from vibeocr.pyside import update as update_ui
        from vibeocr.services.update_service import (
            REMIND_LATER_SECONDS,
            save_remind_later,
        )

        service = self._make_service(tmp_path, monkeypatch)
        self._mock_has_update(monkeypatch)
        now = 1_700_000_000.0
        save_remind_later(
            now + REMIND_LATER_SECONDS, service._settings_path
        )

        # 桩住 await_dialog：模拟用户点「稍后提醒」（later），让 check_and_prompt 完成
        async def fake_await_dialog(dlg):
            dlg._action = "later"  # type: ignore[attr-defined]
            return 0
        monkeypatch.setattr(update_ui, "await_dialog", fake_await_dialog)

        dialog_created = []
        real_update_dialog = update_ui.UpdateDialog
        monkeypatch.setattr(
            update_ui,
            "UpdateDialog",
            lambda *a, **k: dialog_created.append(1) or real_update_dialog(*a, **k),
        )

        _run(service.check_and_prompt(None, manual=True, now=now))

        assert dialog_created, "手动检查应弹窗（忽略暂缓）"

    def test_later_action_persists_remind_later(
        self, tmp_path, monkeypatch, qapp
    ):
        """点「稍后提醒」→ settings 文件写入 remind_later_until（未到期）。"""
        from vibeocr.pyside import update as update_ui
        from vibeocr.services.update_service import (
            REMIND_LATER_SECONDS,
            is_remind_later_active,
            load_remind_later,
        )

        service = self._make_service(tmp_path, monkeypatch)
        self._mock_has_update(monkeypatch)

        now = 1_700_000_000.0
        async def fake_await_dialog(dlg):
            dlg._action = "later"  # type: ignore[attr-defined]
            return 0
        monkeypatch.setattr(update_ui, "await_dialog", fake_await_dialog)

        _run(service.check_and_prompt(None, manual=True, now=now))

        until = load_remind_later(service._settings_path)
        assert until == pytest.approx(now + REMIND_LATER_SECONDS)
        assert is_remind_later_active(service._settings_path, now=now) is True

    def test_skip_action_does_not_set_remind_later(
        self, tmp_path, monkeypatch, qapp
    ):
        """点「跳过此版本」不应误写 remind_later（两个机制独立）。"""
        from vibeocr.pyside import update as update_ui
        from vibeocr.services.update_service import load_remind_later

        service = self._make_service(tmp_path, monkeypatch)
        self._mock_has_update(monkeypatch)

        async def fake_await_dialog(dlg):
            dlg._action = "skip"  # type: ignore[attr-defined]
            return 0
        monkeypatch.setattr(update_ui, "await_dialog", fake_await_dialog)

        _run(service.check_and_prompt(None, manual=True, now=1_700_000_000.0))

        assert load_remind_later(service._settings_path) == 0.0


# ---------------------------------------------------------------------------
# Bug 2：request_cancel 即时切 idle + download_update 竞速取消
# ---------------------------------------------------------------------------


class TestRequestCancelImmediateState:
    """request_cancel 立即切 idle 回归测试。

    历史 bug：request_cancel 只 set event，按钮切回「检查更新」依赖协程跑到 finally。
    若下载正卡在 SHA get / verify_sha256 等不可中断段，按钮要等这些段结束（最坏
    15s read timeout + 数秒哈希）才变。修复：request_cancel 在 set event 的同时
    立即 _set_download_state("idle")，让按钮在用户点击瞬间就切回。
    """

    def test_request_cancel_sets_idle_immediately(self, tmp_path, monkeypatch):
        """downloading 态调 request_cancel → _download_state 立即变 idle。"""
        from vibeocr.pyside import update as us_mod
        from vibeocr.services import env_config

        monkeypatch.setattr(
            env_config, "get_update_cache_dir", lambda: tmp_path / "c"
        )
        monkeypatch.setattr(
            env_config, "get_update_settings_path", lambda: tmp_path / "s.json"
        )

        # 模拟下载进行中：有活跃 event + downloading 态
        event = asyncio.Event()
        us_mod.UpdateService._active_cancel_event = event
        us_mod.UpdateService._download_state = "downloading"
        assert not event.is_set()

        us_mod.UpdateService.request_cancel()

        # event 被 set（取消信号）
        assert event.is_set()
        # 状态立即切 idle（不等协程退出）—— 这是本次修复的关键契约
        assert us_mod.UpdateService._download_state == "idle"

        # 清理
        us_mod.UpdateService._active_cancel_event = None


class TestDownloadUpdateCancelRaceWithShaHang:
    """download_update 在 SHA get 卡住时也能快速响应取消。

    历史 bug：SHA 预检 ``client.get(sha_url)`` 不接受协作取消，read timeout 15s。
    用户在 SHA 请求进行中点取消，要等请求完成/超时（最坏 15s）才生效。修复：在
    download_update 的多源循环里用 asyncio.wait 让 cancel_event 与下载任务竞速，
    取消先到则立即返回 cancelled，并 cancel 卡住的下载任务（CancelledError 会
    中断 httpx 请求）。
    """

    def test_cancel_returns_quickly_when_sha_get_hangs(self, tmp_path):
        """SHA get 卡住（长 sleep）时 set cancel → download_update 快速返回 cancelled。"""
        from vibeocr.services.update_service import (
            DOWNLOAD_REASON_CANCELLED,
            download_update,
        )

        info = _make_update_info()
        cancel_event = asyncio.Event()

        async def _hanging_attempt(*a, **kw):
            # 模拟 SHA get 卡住：长时间不返回（远超测试预算）
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                # 清理半成品（与生产代码 finally/except 行为一致）
                for p in a[3:5]:  # zip_path, sha_path
                    try:
                        p.unlink(missing_ok=True)
                    except OSError:
                        pass
                raise

        with patch(
            "vibeocr.services.update_service._detect_network_type",
            return_value="domestic",
        ), patch(
            "vibeocr.services.update_service._download_zip_with_sha",
            side_effect=_hanging_attempt,
        ):
            async def driver():
                # 并发：启动下载 + 短暂延迟后取消
                async def _cancel_soon():
                    await asyncio.sleep(0.2)
                    cancel_event.set()

                cancel_task = asyncio.ensure_future(_cancel_soon())
                start = time.perf_counter()
                result, reasons = await download_update(
                    info, tmp_path, cancel_event=cancel_event
                )
                elapsed = time.perf_counter() - start
                await cancel_task
                return result, reasons, elapsed

            result, reasons, elapsed = _run(driver())

        assert result is None
        assert DOWNLOAD_REASON_CANCELLED in reasons
        # 关键：取消必须在远短于 SHA read timeout（15s）内生效
        assert elapsed < 3.0, (
            f"取消响应过慢（{elapsed:.2f}s），应在 SHA get 卡住时也能快速返回"
        )

    def test_succeeds_normally_when_not_cancelled_with_race_wrapper(self, tmp_path):
        """回归保护：竞速取消包装不破坏正常成功路径。"""
        from vibeocr.services.update_service import (
            DOWNLOAD_REASON_OK,
            SourceAttempt,
            download_update,
        )

        info = _make_update_info()
        cancel_event = asyncio.Event()  # 不 set

        with patch(
            "vibeocr.services.update_service._detect_network_type",
            return_value="domestic",
        ), patch(
            "vibeocr.services.update_service._download_zip_with_sha",
            new_callable=AsyncMock,
            return_value=SourceAttempt(True, DOWNLOAD_REASON_OK),
        ):
            result, reasons = _run(
                download_update(info, tmp_path, cancel_event=cancel_event)
            )

        assert result is not None
        assert reasons == []

    def test_cancel_between_sources_still_breaks(self, tmp_path):
        """换源间隙取消仍生效（原有契约，竞速包装不应破坏）。"""
        from vibeocr.services.update_service import (
            DOWNLOAD_REASON_HTTP_ERROR,
            DOWNLOAD_REASON_OK,
            SourceAttempt,
            download_update,
        )

        info = _make_update_info()
        cancel_event = asyncio.Event()

        attempts = [
            SourceAttempt(False, DOWNLOAD_REASON_HTTP_ERROR),
            SourceAttempt(True, DOWNLOAD_REASON_OK),
        ]
        call_count = {"n": 0}

        async def _flaky(*a, **kw):
            n = call_count["n"]
            call_count["n"] += 1
            # 第一源失败后 set cancel，第二源不应被尝试
            if n == 0:
                cancel_event.set()
            return attempts[n]

        with patch(
            "vibeocr.services.update_service._detect_network_type",
            return_value="domestic",
        ), patch(
            "vibeocr.services.update_service._download_zip_with_sha",
            side_effect=_flaky,
        ):
            result, reasons = _run(
                download_update(info, tmp_path, cancel_event=cancel_event)
            )

        assert result is None
        # 第二源（ghproxy）因取消未被尝试
        assert call_count["n"] == 1
        assert reasons == [DOWNLOAD_REASON_HTTP_ERROR]


# ---------------------------------------------------------------------------
# _attempt_or_cancel 直接测试 —— 竞速包装的各分支
# ---------------------------------------------------------------------------
#
# download_update 的多源循环总是传 cancel_event（非 None），且业务路径只覆盖
# 「取消先到 / 下载先完成」两条主路径。竞速包装自身的退化分支（cancel_event=None
# 直接 await）、asyncio.wait 自身被 cancel 的清理分支、被 cancel 的下载任务最终抛
# 非CancelledError 异常时的吞异常分支，只在直接调用 _attempt_or_cancel 时可达——
# 这些是编排鲁棒性契约，单测直接打到。


class TestAttemptOrCancel:
    """``_attempt_or_cancel`` 竞速包装的直接单元测试。

    覆盖通过 ``download_update`` 业务路径不可达的内部分支：
    - ``cancel_event=None`` 退化（直接 await，无竞速）。
    - ``asyncio.wait`` 等待期间自身被 cancel（上层取消整个编排）→ 清理两个子任务
      并重新抛出（不吞 CancelledError，让上层正确感知取消链）。
    - 被 cancel 的下载任务最终抛非 CancelledError 异常 → 吞掉，返回 cancelled。
    """

    def test_no_cancel_event_awaits_directly(self):
        """cancel_event=None → 退化直接 await 协程，返回其 SourceAttempt。"""
        from vibeocr.services.update_service import (
            DOWNLOAD_REASON_OK,
            SourceAttempt,
            _attempt_or_cancel,
        )

        async def _attempt():
            return SourceAttempt(True, DOWNLOAD_REASON_OK)

        result = _run(_attempt_or_cancel(_attempt()))
        assert result == SourceAttempt(True, DOWNLOAD_REASON_OK)

    def test_wait_cancelled_cleans_up_and_reraises(self):
        """``asyncio.wait`` 自身被上层 cancel → 清理两个子任务并重新抛出 CancelledError。

        历史/防御契约：上层（如 ``download_update`` 被取消）取消整个编排时，
        ``_attempt_or_cancel`` 不能吞掉取消信号，必须透传，且清理它创建的两个
        ensure_future 子任务，避免悬挂任务。
        """
        from vibeocr.services.update_service import _attempt_or_cancel

        cancel_event = asyncio.Event()
        started = asyncio.Event()
        cleaned = {"attempt": False, "cancel": False}

        async def _hanging_attempt():
            started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cleaned["attempt"] = True
                raise

        async def driver():
            # 包一层：启动 _attempt_or_cancel，等它进入 asyncio.wait 后整体 cancel。
            outer = asyncio.ensure_future(
                _attempt_or_cancel(_hanging_attempt(), cancel_event=cancel_event)
            )
            await started.wait()
            # 给 ensure_future 调度一点时间真正进入 asyncio.wait
            await asyncio.sleep(0)
            outer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await outer

        _run(driver())

        # 两个子任务都被 cancel（清理生效）
        assert cleaned["attempt"], "下载子任务应被 cancel 清理"
        # cancel_wait 是 cancel_event.wait() 的 Task；被 cancel 后无需 is_set 即完成
        assert not cancel_event.is_set()

    def test_cancelled_attempt_raising_other_exception_is_swallowed(self):
        """被 cancel 的下载任务最终抛非 CancelledError 异常 → 吞掉，返回 cancelled。

        与 SHA-hang 用例的区别：那里被 cancel 的协程在 CancelledError 路径里清理
        半成品后 *re-raise* CancelledError；这里模拟协程把异常转译成 RuntimeError
        （如 except CancelledError 后转抛业务异常的防御写法）。``_attempt_or_cancel``
        的 ``except (CancelledError, Exception): pass`` 必须吞掉它，仍返回 cancelled，
        不让异常冒泡破坏编排器（下载取消的最终语义就是 cancelled，不管收尾抛什么）。
        """
        from vibeocr.services.update_service import (
            DOWNLOAD_REASON_CANCELLED,
            SourceAttempt,
            _attempt_or_cancel,
        )

        cancel_event = asyncio.Event()
        transmuted = {"hit": False}

        async def _attempt_that_transmutes():
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                # 收尾时把异常转译成业务异常（防御写法，把取消转成"显式失败"）
                transmuted["hit"] = True
                raise RuntimeError("收尾失败")

        async def driver():
            cancel_event.set()  # 取消先到
            return await _attempt_or_cancel(
                _attempt_that_transmutes(), cancel_event=cancel_event
            )

        result = _run(driver())

        assert transmuted["hit"]
        assert result == SourceAttempt(False, DOWNLOAD_REASON_CANCELLED)

    def test_attempt_completes_before_cancel_cancels_wait(self):
        """下载先于取消完成 → 返回其 SourceAttempt，cancel_wait 被取消（无悬挂）。"""
        from vibeocr.services.update_service import (
            DOWNLOAD_REASON_OK,
            SourceAttempt,
            _attempt_or_cancel,
        )

        cancel_event = asyncio.Event()  # 永不 set

        async def _quick_attempt():
            return SourceAttempt(True, DOWNLOAD_REASON_OK)

        result = _run(
            _attempt_or_cancel(_quick_attempt(), cancel_event=cancel_event)
        )
        assert result == SourceAttempt(True, DOWNLOAD_REASON_OK)
        # cancel_wait 已被 cancel（attempt_task in done 分支），event 不必被 set
        assert not cancel_event.is_set()


# ---------------------------------------------------------------------------
# save_remind_later 损坏既有文件容错
# ---------------------------------------------------------------------------


class TestSaveRemindLaterCorruptExisting:
    """``save_remind_later`` 写入时既有文件损坏 → 重置为空 dict 再写，不抛异常。

    与 ``load_remind_later`` 的损坏容错对偶：读取端损坏返回 0.0，写入端损坏重建为
    空 dict。保证「先损坏、后保存」链路不会因 IO/JSON 异常卡住更新流程。
    """

    def test_save_over_corrupt_existing_file(self, tmp_path):
        from vibeocr.services.update_service import (
            load_remind_later,
            save_remind_later,
        )

        settings_path = tmp_path / "update_settings.json"
        # 既有文件损坏（非合法 JSON）
        settings_path.write_text("not json {{{", encoding="utf-8")

        until = 1_700_000_000.0 + 86400
        # 不应抛异常
        save_remind_later(until, settings_path)

        # 写入后文件合法，能读回正确值
        assert load_remind_later(settings_path) == until
        # 其它键被丢弃（损坏文件无可解析内容，重置为空 dict 后只写 remind_later_until）
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        assert data == {"remind_later_until": until}

    def test_save_over_unreadable_existing_file(self, tmp_path):
        """既有文件存在但读取抛 OSError（如权限/IO）→ 重置为空 dict 再写，不抛。"""
        from vibeocr.services.update_service import (
            load_remind_later,
            save_remind_later,
        )

        settings_path = tmp_path / "update_settings.json"
        settings_path.write_text('{"skip_version": "0.9.9"}', encoding="utf-8")

        until = 1_700_000_000.0 + 86400
        # 让 read_text 抛 OSError
        with patch("pathlib.Path.read_text", side_effect=OSError("io error")):
            save_remind_later(until, settings_path)

        # 写入成功（read 失败被吞，重建为空 dict 后写入新值）
        assert load_remind_later(settings_path) == until
