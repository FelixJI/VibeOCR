"""验证 env_manager.download_artifact_multi_source 同步多源下载编排器

覆盖五种场景：
1. 单源直接成功（无 sha）
2. 首源失败、换源成功（无 sha，Python 运行时路径）
3. 全部失败，返回结构化原因
4. sha 校验失败换源（WebEngine 路径：sha_candidates 非空）
5. sha 文件下不到（sha_missing）换源

关键不变量验证：
- 复用 download_file_with_progress（断点续传/中性 UA 在本层不受影响）
- 失败原因复用 update_service.DOWNLOAD_REASON_* 常量集
- 源序与传入的 url_candidates 完全一致（去重保序由调用方负责）
- 换源时 source_switch_fn 收到 (source_label, reason)
"""

from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

# ---- 隔离 httpx 依赖 -------------------------------------------------------
# env_manager.download_artifact_multi_source 延迟 import update_service，
# 而 update_service 顶部 `import httpx` 在未装全量依赖的环境会失败。
# 这里在导入编排器之前，向 sys.modules 注入一个假的 update_service 模块，
# 提供编排器所需的常量 / _source_label / verify_sha256。
# 真正的 verify_sha256 在各用例里用 patch("vibeocr.services.update_service.verify_sha256")
# 覆盖（patch 命中已注入的模块对象）。
import sys as _sys

_fake_us = ModuleType("vibeocr.services.update_service")
_fake_us.DOWNLOAD_REASON_OK = "ok"
_fake_us.DOWNLOAD_REASON_HTTP_ERROR = "http_error"
_fake_us.DOWNLOAD_REASON_SHA_MISSING = "sha_missing"
_fake_us.DOWNLOAD_REASON_SHA_MISMATCH = "sha_mismatch"
_fake_us.DOWNLOAD_REASON_EXCEPTION = "exception"


def _fake_source_label(url: str) -> str:
    for label, marker in (
        ("gh-proxy", "gh-proxy.com"),
        ("ghproxy", "ghproxy.com"),
        ("GitHub", "github.com"),
    ):
        if marker in url:
            return label
    return url


_fake_us._source_label = _fake_source_label
_fake_us.verify_sha256 = MagicMock(return_value=True)
_sys.modules["vibeocr.services.update_service"] = _fake_us
# ---------------------------------------------------------------------------

from vibeocr.env_manager import download_artifact_multi_source  # noqa: E402

# 失败原因常量值（与 src/vibeocr/services/update_service.py 一致）
REASON_OK = "ok"
REASON_HTTP_ERROR = "http_error"
REASON_SHA_MISMATCH = "sha_mismatch"
REASON_SHA_MISSING = "sha_missing"


@pytest.fixture
def dest(tmp_path: Path) -> Path:
    return tmp_path / "artifact.bin"


@pytest.fixture
def sha_dest(tmp_path: Path) -> Path:
    return tmp_path / "artifact.sha256"


class TestNoShaPath:
    """无 sha_candidates 分支（Python 运行时下载路径）。"""

    def test_single_source_success(self, dest: Path):
        urls = ["https://github.com/a/b.tar.gz"]
        with patch(
            "vibeocr.env_manager.download_file_with_progress", return_value=True
        ) as dl:
            ok, reason = download_artifact_multi_source(
                urls, dest, description="Python 运行时"
            )
        assert ok is True
        assert reason == REASON_OK
        # 只调用了一次（首个源即成功）
        assert dl.call_count == 1

    def test_first_fail_second_ok(self, dest: Path):
        urls = ["https://mirror.nju.edu.cn/x", "https://github.com/y"]
        with patch(
            "vibeocr.env_manager.download_file_with_progress",
            side_effect=[False, True],
        ) as dl:
            ok, reason = download_artifact_multi_source(urls, dest)
        assert ok is True
        assert reason == REASON_OK
        assert dl.call_count == 2

    def test_all_fail_returns_reason(self, dest: Path):
        urls = ["https://a", "https://b", "https://c"]
        with patch(
            "vibeocr.env_manager.download_file_with_progress", return_value=False
        ):
            ok, reason = download_artifact_multi_source(urls, dest)
        assert ok is False
        assert reason == REASON_HTTP_ERROR

    def test_source_switch_callback_invoked(self, dest: Path):
        urls = ["https://gh-proxy.com/x", "https://github.com/y"]
        switch = MagicMock()
        with patch(
            "vibeocr.env_manager.download_file_with_progress",
            side_effect=[False, True],
        ):
            ok, _ = download_artifact_multi_source(
                urls, dest, source_switch_fn=switch
            )
        assert ok is True
        # 首源失败触发一次换源回调，label 为 gh-proxy
        switch.assert_called_once_with("gh-proxy", REASON_HTTP_ERROR)

    def test_exception_treated_as_switch(self, dest: Path):
        urls = ["https://a", "https://b"]
        with patch(
            "vibeocr.env_manager.download_file_with_progress",
            side_effect=[RuntimeError("boom"), True],
        ):
            ok, reason = download_artifact_multi_source(urls, dest)
        assert ok is True
        assert reason == REASON_OK


class TestWithShaPath:
    """带 sha_candidates 分支（WebEngine 资源包下载路径）。"""

    def test_sha_mismatch_switches_source(
        self, dest: Path, sha_dest: Path
    ):
        zip_urls = ["https://gitee.com/x/asset.zip", "https://github.com/y/asset.zip"]
        sha_urls = ["https://gitee.com/x/asset.sha256", "https://github.com/y/asset.sha256"]
        # 首源：zip+sha 都下成功但校验失败；次源：全部成功且校验通过
        with patch(
            "vibeocr.env_manager.download_file_with_progress", return_value=True
        ), patch(
            "vibeocr.services.update_service.verify_sha256",
            side_effect=[False, True],
        ) as vfy:
            ok, reason = download_artifact_multi_source(
                zip_urls,
                dest,
                description="WebEngine 资源包",
                sha_candidates=sha_urls,
                sha_dest_path=sha_dest,
            )
        assert ok is True
        assert reason == REASON_OK
        assert vfy.call_count == 2

    def test_sha_missing_switches_source(self, dest: Path, sha_dest: Path):
        # 首源 sha 下不到 → sha_missing；次源全部成功
        zip_urls = ["https://gitee.com/a.zip", "https://github.com/b.zip"]
        sha_urls = ["https://gitee.com/a.sha256", "https://github.com/b.sha256"]
        # download_file_with_progress: 首源 zip 成功、首源 sha 失败、次源 zip 成功、次源 sha 成功
        with patch(
            "vibeocr.env_manager.download_file_with_progress",
            side_effect=[True, False, True, True],
        ), patch("vibeocr.services.update_service.verify_sha256", return_value=True):
            ok, reason = download_artifact_multi_source(
                zip_urls,
                dest,
                sha_candidates=sha_urls,
                sha_dest_path=sha_dest,
            )
        assert ok is True
        assert reason == REASON_OK

    def test_all_sha_mismatch_fails(self, dest: Path, sha_dest: Path):
        zip_urls = ["https://a/x.zip", "https://b/x.zip"]
        sha_urls = ["https://a/x.sha256", "https://b/x.sha256"]
        with patch(
            "vibeocr.env_manager.download_file_with_progress", return_value=True
        ), patch("vibeocr.services.update_service.verify_sha256", return_value=False):
            ok, reason = download_artifact_multi_source(
                zip_urls,
                dest,
                sha_candidates=sha_urls,
                sha_dest_path=sha_dest,
            )
        assert ok is False
        assert reason == REASON_SHA_MISMATCH

    def test_mismatch_triggers_switch_callback(self, dest: Path, sha_dest: Path):
        zip_urls = ["https://gh-proxy.com/a.zip", "https://github.com/b.zip"]
        sha_urls = ["https://gh-proxy.com/a.sha256", "https://github.com/b.sha256"]
        switch = MagicMock()
        with patch(
            "vibeocr.env_manager.download_file_with_progress", return_value=True
        ), patch(
            "vibeocr.services.update_service.verify_sha256",
            side_effect=[False, True],
        ):
            ok, _ = download_artifact_multi_source(
                zip_urls,
                dest,
                sha_candidates=sha_urls,
                sha_dest_path=sha_dest,
                source_switch_fn=switch,
            )
        assert ok is True
        switch.assert_called_once_with("gh-proxy", REASON_SHA_MISMATCH)


class TestGuardClauses:
    def test_length_mismatch_raises(self, dest: Path, sha_dest: Path):
        # zip 与 sha 候选长度不一致应显式报错（同源配对约束）
        with pytest.raises(ValueError, match="同源配对"):
            download_artifact_multi_source(
                ["https://a", "https://b"],
                dest,
                sha_candidates=["https://a.sha256"],  # 少一个
                sha_dest_path=sha_dest,
            )
