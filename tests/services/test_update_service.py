"""update_service 模块测试"""

import asyncio
import hashlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx


def _run(coro):
    """在同步测试中运行协程"""
    return asyncio.run(coro)


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


class TestLocalVersion:
    """本地版本读取测试"""

    def test_read_version_json(self, tmp_path):
        from vibeocr.services.update_service import read_local_version

        version_file = tmp_path / "version.json"
        version_file.write_text(json.dumps({"version": "0.1.0"}), encoding="utf-8")
        assert read_local_version(version_file) == "0.1.0"

    def test_read_version_json_missing(self, tmp_path):
        from vibeocr.services.update_service import read_local_version

        assert read_local_version(tmp_path / "nonexistent.json") == "0.0.0"

    def test_read_version_json_corrupt(self, tmp_path):
        from vibeocr.services.update_service import read_local_version

        version_file = tmp_path / "version.json"
        version_file.write_text("not json", encoding="utf-8")
        assert read_local_version(version_file) == "0.0.0"


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
            UpdateInfo,
            download_update,
        )

        info = UpdateInfo(
            version="0.3.1",
            download_url="https://example.com/zip",
            sha256_url="https://example.com/sha",
            changelog="",
        )
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
            UpdateInfo,
            download_update,
        )

        info = UpdateInfo(
            version="0.3.1",
            download_url="https://example.com/zip",
            sha256_url="https://example.com/sha",
            changelog="",
        )
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
            UpdateInfo,
            download_update,
        )

        info = UpdateInfo(
            version="0.3.1",
            download_url="https://example.com/zip",
            sha256_url="https://example.com/sha",
            changelog="",
        )
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
            UpdateInfo,
            download_update,
        )

        info = UpdateInfo(
            version="0.3.1",
            download_url="https://example.com/zip",
            sha256_url="https://example.com/sha",
            changelog="",
        )
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
            UpdateInfo,
            download_update,
        )

        info = UpdateInfo(
            version="0.3.1",
            download_url="https://example.com/zip",
            sha256_url="https://example.com/sha",
            changelog="",
        )
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

    def test_zip_non_200_returns_false_and_no_sha_fetch(self, tmp_path):
        _download_zip_with_sha = self._import()

        stream = _make_stream_response(404, [b"not found"])
        client = _make_client(stream_cm=stream)
        zip_path = tmp_path / "x.zip"
        sha_path = tmp_path / "x.zip.sha256"

        ok = _run(
            _download_zip_with_sha(
                client, self.ZIP_URL, self.EXPECTED_SHA_URL, zip_path, sha_path, None
            )
        )

        assert ok.ok is False
        assert ok.reason == "http_error"
        # zip 非 200 直接返回，不应落盘、不应尝试取 sha
        assert not zip_path.exists()
        client.get.assert_not_awaited()

    def test_sha_non_200_cleans_up_zip(self, tmp_path):
        _download_zip_with_sha = self._import()

        zip_bytes = b"fake-zip-content"
        stream = _make_stream_response(200, [zip_bytes])
        # zip 成功，但 sha 端点非 200
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
        client = _make_client(stream_side_effect=httpx.ConnectError("boom"))
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
        from vibeocr.services.update_service import (
            DOWNLOAD_REASON_SHA_MISMATCH,
            _format_failure_message,
        )

        with self._patch_net():
            msg = _format_failure_message(
                ["http_error", DOWNLOAD_REASON_SHA_MISMATCH]
            )
        # 最坏原因（完整性）优先，必须出现明确措辞，而不是泛泛「网络问题」
        assert "完整性校验失败" in msg
        assert "网络" not in msg

    def test_sha_missing_mentions_missing_checksum(self):
        from vibeocr.services.update_service import (
            DOWNLOAD_REASON_SHA_MISSING,
            _format_failure_message,
        )

        with self._patch_net():
            msg = _format_failure_message(
                ["http_error", DOWNLOAD_REASON_SHA_MISSING]
            )
        assert "缺少 SHA256 校验文件" in msg

    def test_all_http_errors_mentions_network(self):
        from vibeocr.services.update_service import (
            DOWNLOAD_REASON_HTTP_ERROR,
            _format_failure_message,
        )

        with self._patch_net():
            msg = _format_failure_message(
                [DOWNLOAD_REASON_HTTP_ERROR, DOWNLOAD_REASON_HTTP_ERROR]
            )
        assert "无法连接服务器" in msg

    def test_message_includes_manual_download_link(self):
        from vibeocr.services.update_service import (
            DOWNLOAD_REASON_HTTP_ERROR,
            _format_failure_message,
        )

        with self._patch_net("domestic"):
            msg = _format_failure_message([DOWNLOAD_REASON_HTTP_ERROR])
        # 手动下载链接指向 GitHub
        assert "github.com" in msg
