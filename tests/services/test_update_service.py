"""update_service 模块测试"""

import asyncio
import hashlib
import json
from unittest.mock import AsyncMock, patch


def _run(coro):
    """在同步测试中运行协程"""
    return asyncio.run(coro)


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
        """_find_asset_url 必须排除 webengine 资源包，只匹配主包。

        资源包命名 VibeOCR-v*-webengine-win64.zip，由 webengine_manager 单独
        处理；更新检测只应拿主包 zip。
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
        from vibeocr.services.update_service import UpdateInfo, download_update

        info = UpdateInfo(
            version="0.3.1",
            download_url="https://example.com/zip",
            sha256_url="https://example.com/sha",
            changelog="",
        )
        with patch(
            "vibeocr.services.update_service._download_zip_with_sha",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_dl:
            result = _run(download_update(info, tmp_path))
        assert result is not None
        assert result.name == "VibeOCR-v0.3.1-win64.zip"
        mock_dl.assert_called_once()

    def test_falls_back_to_next_source_on_failure(self, tmp_path):
        """首源失败 → 换源成功"""
        from vibeocr.services.update_service import UpdateInfo, download_update

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
            side_effect=[False, True],
        ) as mock_dl:
            result = _run(download_update(info, tmp_path))
        assert result is not None
        assert mock_dl.call_count == 2  # 首源失败后换源成功

    def test_returns_none_when_all_sources_fail(self, tmp_path):
        from vibeocr.services.update_service import UpdateInfo, download_update

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
            return_value=False,
        ) as mock_dl:
            result = _run(download_update(info, tmp_path))
        assert result is None
        assert mock_dl.call_count == 2  # 海外 2 候选全部失败

    def test_domestic_uses_four_candidates(self, tmp_path):
        """国内走 4 候选（Gitee→gh-proxy→ghproxy→GitHub）"""
        from vibeocr.services.update_service import UpdateInfo, download_update

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
            return_value=False,
        ) as mock_dl:
            _run(download_update(info, tmp_path))
        assert mock_dl.call_count == 4
