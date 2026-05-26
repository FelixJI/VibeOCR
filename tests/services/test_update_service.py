"""update_service 模块测试"""

import asyncio
import hashlib
import json
from unittest.mock import patch


def _run(coro):
    """在同步测试中运行协程"""
    return asyncio.get_event_loop().run_until_complete(coro)


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

    def test_from_gitee_release(self):
        from vibeocr.services.update_service import UpdateInfo

        release = {
            "tag_name": "v0.2.0",
            "body": "- feat: 新功能\n- fix: 修复bug",
            "assets": [
                {
                    "name": "VibeOCR-v0.2.0-win64.zip",
                    "browser_download_url": "https://gitee.com/test/v0.2.0.zip",
                    "size": 150000000,
                },
                {
                    "name": "VibeOCR-v0.2.0-win64.zip.sha256",
                    "browser_download_url": "https://gitee.com/test/v0.2.0.zip.sha256",
                },
            ],
        }
        info = UpdateInfo.from_gitee_release(release)
        assert info.version == "0.2.0"
        assert info.download_url == "https://gitee.com/test/v0.2.0.zip"
        assert info.sha256_url == "https://gitee.com/test/v0.2.0.zip.sha256"
        assert "新功能" in info.changelog
        assert info.file_size == 150000000

    def test_from_github_release(self):
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
        info = UpdateInfo.from_github_release(release)
        assert info.version == "0.3.0"
        assert info.download_url == "https://github.com/test/v0.3.0.zip"

    def test_no_matching_assets(self):
        from vibeocr.services.update_service import UpdateInfo

        release = {
            "tag_name": "v0.1.0",
            "body": "",
            "assets": [],
        }
        info = UpdateInfo.from_gitee_release(release)
        assert info.download_url == ""
        assert info.sha256_url == ""


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
    """远程版本检查测试"""

    def test_check_gitee_has_update(self):
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
            "vibeocr.services.update_service._fetch_gitee_release",
            return_value=mock_release,
        ):
            result = _run(check_for_updates("0.1.0", prefer_gitee=True))
        assert result is not None
        assert result.version == "99.0.0"

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
            "vibeocr.services.update_service._fetch_gitee_release",
            return_value=mock_release,
        ):
            result = _run(check_for_updates("0.1.0", prefer_gitee=True))
        assert result is None

    def test_check_gitee_fallback_github(self):
        from vibeocr.services.update_service import check_for_updates

        mock_release = {
            "tag_name": "v0.2.0",
            "body": "update",
            "assets": [
                {
                    "name": "VibeOCR-v0.2.0-win64.zip",
                    "browser_download_url": "http://test.zip",
                    "size": 0,
                },
                {
                    "name": "VibeOCR-v0.2.0-win64.zip.sha256",
                    "browser_download_url": "http://test.sha256",
                },
            ],
        }
        with (
            patch(
                "vibeocr.services.update_service._fetch_gitee_release",
                return_value=None,
            ),
            patch(
                "vibeocr.services.update_service._fetch_github_release",
                return_value=mock_release,
            ),
        ):
            result = _run(check_for_updates("0.1.0", prefer_gitee=True))
        assert result is not None
        assert result.version == "0.2.0"

    def test_check_all_fail(self):
        from vibeocr.services.update_service import check_for_updates

        with (
            patch(
                "vibeocr.services.update_service._fetch_gitee_release",
                return_value=None,
            ),
            patch(
                "vibeocr.services.update_service._fetch_github_release",
                return_value=None,
            ),
        ):
            result = _run(check_for_updates("0.1.0", prefer_gitee=True))
        assert result is None


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
