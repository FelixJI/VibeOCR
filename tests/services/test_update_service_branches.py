"""Branch-coverage tests for update_service gaps.

Existing test_update_service.py covers the main flows. Here we fill:
- compare_versions: longer-parts branches (1.0.0 vs 1.0).
- _log_http_exchange: start_time=None, headers-dict exception.
- _asset_matches: unknown suffix → False.
- _find_asset: fallback-asset branch, no-Classic-match.
- _find_asset_size: no matching asset name.
- read_local_version: version falsy / __version__ exception.
- _fetch_release: exception path.
- _detect_network_type: exception path.
- check_for_updates: remote has no download_url.
- _valid_sha256_text: non-hex first field.
- load/save_skip_version: corrupt-JSON branches.
- download_update: empty download_url guard + cancel-before-start.
- _download_zip_with_sha: cancel after sha download.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# compare_versions: longer-parts branches
# ---------------------------------------------------------------------------


class TestCompareVersionsLength:
    def test_longer_first_returns_1(self):
        from vibeocr.services.update_service import compare_versions

        assert compare_versions("1.0.0", "1.0") == 1

    def test_shorter_first_returns_minus_1(self):
        from vibeocr.services.update_service import compare_versions

        assert compare_versions("1.0", "1.0.0") == -1

    def test_equal_returns_0(self):
        from vibeocr.services.update_service import compare_versions

        assert compare_versions("1.2.3", "1.2.3") == 0


# ---------------------------------------------------------------------------
# _log_http_exchange: edge branches
# ---------------------------------------------------------------------------


class TestLogHttpExchangeBranches:
    def test_start_time_none(self, monkeypatch):
        """start_time=None → elapsed_ms 保持 None（line 82->84）。"""
        from vibeocr.services import update_service

        captured = {}

        def capture(**kwargs):
            captured.update(kwargs)

        monkeypatch.setattr(update_service, "log_http_response", capture)
        resp = httpx.Response(200, request=httpx.Request("GET", "http://x"))
        update_service._log_http_exchange("GET", "http://x", resp)
        assert captured["elapsed_ms"] is None

    def test_headers_dict_exception(self, monkeypatch):
        """headers dict() 抛异常 → headers=None（line 87-88）。"""
        from vibeocr.services import update_service

        captured = {}

        def capture(**kwargs):
            captured.update(kwargs)

        monkeypatch.setattr(update_service, "log_http_response", capture)

        class _BadHeaders:
            def __iter__(self):
                raise RuntimeError("iter fail")

        resp = MagicMock()
        resp.headers = _BadHeaders()
        resp.content = b"data"
        resp.status_code = 200
        resp.reason_phrase = "OK"
        update_service._log_http_exchange("GET", "http://x", resp, start_time=1.0)
        # 不应抛异常；response_bytes 走 guess_response_size(None, content)
        assert captured["status_code"] == 200


# ---------------------------------------------------------------------------
# _asset_matches / _find_asset / _find_asset_size edge branches
# ---------------------------------------------------------------------------


class TestAssetMatching:
    def test_unknown_suffix_returns_false(self):
        from vibeocr.services.update_service import _asset_matches

        assert _asset_matches("foo.txt", ".tar.gz") is False

    def test_find_asset_fallback_when_no_classic(self):
        """无 -Classic- 命名时回退第一个匹配（line 185->186）。"""
        from vibeocr.services.update_service import _find_asset

        release = {
            "assets": [
                {"name": "VibeOCR-v1.0-win64.zip", "browser_download_url": "http://x/1"},
            ]
        }
        name, url = _find_asset(release, ".zip")
        assert name == "VibeOCR-v1.0-win64.zip"
        assert url == "http://x/1"

    def test_find_asset_prefers_classic_over_fallback(self):
        """-Classic- 命名优先于回退（line 182-183）。"""
        from vibeocr.services.update_service import _find_asset

        release = {
            "assets": [
                {"name": "VibeOCR-v1.0-win64.zip", "browser_download_url": "http://x/1"},
                {
                    "name": "VibeOCR-Classic-v1.0-win64.zip",
                    "browser_download_url": "http://x/2",
                },
            ]
        }
        name, url = _find_asset(release, ".zip")
        assert "Classic" in name
        assert url == "http://x/2"

    def test_find_asset_size_no_matching_name(self):
        """_find_asset_size 找不到匹配 name → 0（line 203）。"""
        from vibeocr.services.update_service import _find_asset_size

        # release 有匹配 asset 但 size 查询时 name 对不上（构造不一致）
        release = {"assets": [{"name": "other.zip", "size": 100}]}
        # _find_asset 找不到 zip（other.zip 不匹配 .zip? 它匹配）—— 用空 assets
        release_empty = {"assets": []}
        assert _find_asset_size(release_empty, ".zip") == 0


# ---------------------------------------------------------------------------
# read_local_version: fallback branches
# ---------------------------------------------------------------------------


class TestReadLocalVersionBranches:
    def test_version_falsy_falls_back(self, tmp_path, monkeypatch):
        """version.json 存在但 version 为空 → 走 __version__ 回退（line 219->223）。"""
        from vibeocr.services import update_service

        (tmp_path / "version.json").write_text(
            json.dumps({"version": ""}), encoding="utf-8"
        )
        # 注入 __version__
        fake_mod = MagicMock()
        fake_mod.__version__ = "9.9.9"
        monkeypatch.setitem(
            __import__("sys").modules, "vibeocr", fake_mod
        )
        assert update_service.read_local_version(tmp_path / "version.json") == "9.9.9"

    def test_version_json_corrupt_falls_back(self, tmp_path, monkeypatch):
        """version.json 损坏 → __version__ 回退（line 221-222）。"""
        from vibeocr.services import update_service

        (tmp_path / "version.json").write_text("not json{", encoding="utf-8")
        fake_mod = MagicMock()
        fake_mod.__version__ = "8.8.8"
        monkeypatch.setitem(
            __import__("sys").modules, "vibeocr", fake_mod
        )
        assert update_service.read_local_version(tmp_path / "version.json") == "8.8.8"

    def test_no_version_json_no_dunder_returns_zero(self, tmp_path, monkeypatch):
        """无 version.json + __version__ import 失败 → 0.0.0（line 228-230）。"""
        from vibeocr.services import update_service

        # 让 from vibeocr import __version__ 抛异常
        import sys

        monkeypatch.setitem(sys.modules, "vibeocr", None)
        assert (
            update_service.read_local_version(tmp_path / "missing.json") == "0.0.0"
        )


# ---------------------------------------------------------------------------
# _fetch_release / _detect_network_type: exception paths
# ---------------------------------------------------------------------------


class TestFetchReleaseException:
    def test_exception_returns_none(self, monkeypatch):
        """_fetch_release 网络异常 → None（line 253-255）。"""
        from vibeocr.services import update_service

        async def boom(*a, **kw):
            raise httpx.ConnectError("refused")

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = boom
        with patch("vibeocr.services.update_service.httpx.AsyncClient", return_value=mock_client):
            result = _run(update_service._fetch_release("http://x"))
        assert result is None

    def test_non_200_returns_none(self, monkeypatch):
        """非 200 状态 → None（line 251->253 未进入）。"""
        from vibeocr.services import update_service

        resp = MagicMock()
        resp.status_code = 404
        resp.json.return_value = {}
        resp.headers = {}
        resp.content = b""
        resp.request.content = None
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=resp)
        with patch(
            "vibeocr.services.update_service.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = _run(update_service._fetch_release("http://x"))
        assert result is None


class TestDetectNetworkTypeException:
    def test_exception_returns_international(self, monkeypatch):
        """NetworkDetector 抛异常 → international（line 265-266）。"""
        from vibeocr.services import update_service

        # 让 import 抛异常
        import sys

        monkeypatch.setitem(sys.modules, "vibeocr.network_detector", None)
        assert update_service._detect_network_type() == "international"


# ---------------------------------------------------------------------------
# check_for_updates: remote has no download_url
# ---------------------------------------------------------------------------


class TestCheckForUpdatesNoDownloadUrl:
    def test_remote_without_zip_returns_none_true(self, monkeypatch):
        """remote 版本更新但无 download_url → (None, True)（line 338-340）。"""
        from vibeocr.services import update_service

        # release 有更新版本但无 .zip asset
        release = {
            "tag_name": "v99.0.0",
            "assets": [],  # 无任何 asset
            "body": "",
        }
        with patch(
            "vibeocr.services.update_service._fetch_release",
            return_value=release,
        ):
            info, fetch_ok = _run(update_service.check_for_updates("0.1.0"))
        assert info is None
        assert fetch_ok is True


# ---------------------------------------------------------------------------
# _valid_sha256_text: non-hex first field
# ---------------------------------------------------------------------------


class TestValidSha256Text:
    def test_non_hex_first_field_returns_false(self):
        from vibeocr.services.update_service import _valid_sha256_text

        # 64 字符但非 hex
        assert _valid_sha256_text("z" * 64) is False

    def test_short_first_field_returns_false(self):
        from vibeocr.services.update_service import _valid_sha256_text

        assert _valid_sha256_text("abc") is False

    def test_empty_returns_false(self):
        from vibeocr.services.update_service import _valid_sha256_text

        assert _valid_sha256_text("") is False

    def test_valid_hex_returns_true(self):
        from vibeocr.services.update_service import _valid_sha256_text

        assert _valid_sha256_text("a" * 64) is True

    def test_valid_hex_with_filename_returns_true(self):
        from vibeocr.services.update_service import _valid_sha256_text

        assert _valid_sha256_text("a" * 64 + "  file.zip") is True


# ---------------------------------------------------------------------------
# load_skip_version / save_skip_version: corrupt-JSON branches
# ---------------------------------------------------------------------------


class TestSkipVersionCorruptJson:
    def test_load_corrupt_json_returns_empty(self, tmp_path):
        from vibeocr.services.update_service import load_skip_version

        path = tmp_path / "settings.json"
        path.write_text("not json{", encoding="utf-8")
        assert load_skip_version(path) == ""

    def test_load_oserror_returns_empty(self, tmp_path):
        from vibeocr.services.update_service import load_skip_version

        # 用目录冒充文件 → read_text 抛 IsADirectoryError（OSError 子类）
        path = tmp_path / "settings.json"
        path.mkdir()
        assert load_skip_version(path) == ""

    def test_save_when_existing_corrupt_resets_data(self, tmp_path):
        """save 时已存在文件损坏 → 重置 data 再写（line 729-730）。"""
        from vibeocr.services.update_service import save_skip_version

        path = tmp_path / "settings.json"
        path.write_text("not json{", encoding="utf-8")
        save_skip_version("1.2.3", path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["skip_version"] == "1.2.3"

    def test_save_when_existing_read_oserror_resets(self, tmp_path):
        """save 时已存在文件 read 失败（OSError）→ 重置（line 729-730）。"""
        from vibeocr.services.update_service import save_skip_version

        path = tmp_path / "settings.json"
        path.write_text("{}", encoding="utf-8")
        # patch read_text 抛 OSError
        with patch.object(Path, "read_text", side_effect=OSError("denied")):
            save_skip_version("2.0.0", path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["skip_version"] == "2.0.0"


# ---------------------------------------------------------------------------
# download_update: empty-URL guard + cancel-before-start
# ---------------------------------------------------------------------------


class TestDownloadUpdateGuards:
    def test_empty_download_url_returns_none_http_error(self, tmp_path):
        """download_url 为空 → (None, [http_error])（line 548-550）。"""
        from vibeocr.services.update_service import UpdateInfo, download_update

        info = UpdateInfo(
            version="1.0",
            download_url="",  # 空
            sha256_url="http://x",
            changelog="",
        )
        zip_path, reasons = _run(
            download_update(info, cache_dir=tmp_path / "cache")
        )
        assert zip_path is None
        assert "http_error" in reasons

    def test_cancel_before_start_returns_cancelled(self, tmp_path):
        """cancel_event 进入前已 set → (None, [cancelled])（line 553-555）。"""
        import threading

        from vibeocr.services.update_service import download_update

        info = MagicMock()
        info.download_url = "http://x/zip"
        cancel = threading.Event()
        cancel.set()
        zip_path, reasons = _run(
            download_update(
                info,
                cache_dir=tmp_path / "cache",
                cancel_event=cancel,
            )
        )
        assert zip_path is None
        assert "cancelled" in reasons


# ---------------------------------------------------------------------------
# _download_zip_with_sha: cancel after sha download (line 446-448)
# ---------------------------------------------------------------------------


class TestDownloadZipWithShaCancelAfterSha:
    def test_cancel_after_sha_returns_cancelled(self, tmp_path):
        """sha 下载成功后、zip 下载前取消 → cancelled（line 446-448）。"""
        import threading

        from vibeocr.services.update_service import (
            DOWNLOAD_REASON_CANCELLED,
            _download_zip_with_sha,
        )
        from tests.services.test_update_service import _make_client, _make_stream_response

        client = _make_client(
            stream_cm=_make_stream_response(200, [b"zipdata"]),
            sha_status=200,
            sha_text="a" * 64,
        )
        cancel = threading.Event()

        # 在 sha get 完成后、stream 开始前 set cancel
        original_get = client.get

        async def _delayed_get(*a, **kw):
            result = await original_get(*a, **kw)
            cancel.set()  # sha 返回后取消
            return result

        client.get = _delayed_get

        zip_path = tmp_path / "out.zip"
        sha_path = tmp_path / "out.sha256"
        attempt = _run(
            _download_zip_with_sha(
                client,
                "http://x/zip",
                "http://x/sha",
                zip_path,
                sha_path,
                None,  # progress_callback
                cancel_event=cancel,
            )
        )
        assert attempt.ok is False
        assert attempt.reason == DOWNLOAD_REASON_CANCELLED


# ---------------------------------------------------------------------------
# UpdateInfo.from_release: integration with _find_asset
# ---------------------------------------------------------------------------


class TestUpdateInfoFromRelease:
    def test_from_release_classic_naming(self):
        """from_release 选出 Classic asset 并填充字段。"""
        from vibeocr.services.update_service import UpdateInfo

        release = {
            "tag_name": "v1.2.3",
            "body": "changes",
            "assets": [
                {
                    "name": "VibeOCR-Classic-v1.2.3-win64.zip",
                    "browser_download_url": "http://x/zip",
                    "size": 12345,
                },
                {
                    "name": "VibeOCR-Classic-v1.2.3-win64.zip.sha256",
                    "browser_download_url": "http://x/sha",
                    "size": 100,
                },
            ],
        }
        info = UpdateInfo.from_release(release)
        assert info.version == "1.2.3"
        assert info.download_url == "http://x/zip"
        assert info.sha256_url == "http://x/sha"
        assert info.file_size == 12345
        assert "Classic" in info.zip_filename

    def test_from_release_empty_assets(self):
        """无 assets → 空 URL（line 138 from_release with empty）。"""
        from vibeocr.services.update_service import UpdateInfo

        release = {"tag_name": "v1.0.0", "body": "", "assets": []}
        info = UpdateInfo.from_release(release)
        assert info.version == "1.0.0"
        assert info.download_url == ""
        assert info.sha256_url == ""
        assert info.file_size == 0


# ---------------------------------------------------------------------------
# _find_asset_size: no matching name after _find_asset returns empty
# ---------------------------------------------------------------------------


class TestFindAssetSizeNoMatch:
    def test_returns_zero_when_no_asset_found(self):
        """_find_asset 返回空 name 时 _find_asset_size → 0（line 198-199 → 203）。"""
        from vibeocr.services.update_service import _find_asset_size

        # 无任何 .zip asset → _find_asset 返回 ("", "") → size 查询返回 0
        release = {
            "assets": [
                {"name": "readme.txt", "size": 50},
            ]
        }
        assert _find_asset_size(release, ".zip") == 0


# ---------------------------------------------------------------------------
# download_update: cache cleanup OSError (line 561-565)
# ---------------------------------------------------------------------------


class TestDownloadUpdateCacheCleanup:
    def test_old_file_unlink_oserror_is_swallowed(self, tmp_path):
        """残留文件 unlink 抛 OSError 时被吞掉（line 561-565）。

        用 cancel_event 在下载开始前立即取消，避免真实网络请求；
        清理循环在取消检查前已执行，覆盖 unlink OSError 分支。
        """
        import threading

        from vibeocr.services.update_service import UpdateInfo, download_update

        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        stale = cache_dir / "old.zip"
        stale.write_bytes(b"old")

        info = UpdateInfo(
            version="1.0",
            download_url="http://x/zip",
            sha256_url="http://x/sha",
            changelog="",
            zip_filename="VibeOCR-v1.0-win64.zip",
            sha256_filename="VibeOCR-v1.0-win64.zip.sha256",
        )

        # 预先 set cancel，让 download_update 在发起网络前就返回 cancelled。
        cancel = threading.Event()
        cancel.set()

        # unlink 抛 OSError（被清理循环吞掉）
        with patch.object(Path, "unlink", side_effect=OSError("denied")):
            zip_path, reasons = _run(
                download_update(
                    info,
                    cache_dir=cache_dir,
                    cancel_event=cancel,
                )
            )
        # 取消 → 返回 None
        assert zip_path is None
