"""client 命名空间 SDK helpers 测试。"""

from pathlib import Path


def test_get_output_filename_known_formats():
    from vibeocr.client import get_output_filename

    assert get_output_filename("report.pdf", "markdown") == "report.md"
    assert get_output_filename("scan.png", "docx") == "scan.docx"
    assert get_output_filename("data.csv", "xlsx") == "data.xlsx"


def test_get_output_filename_unknown_falls_back_to_txt():
    from vibeocr.client import get_output_filename

    assert get_output_filename("file.pdf", "pdf") == "file.txt"
    assert get_output_filename("file.pdf", "") == "file.txt"


def test_get_unique_output_path_no_conflict(tmp_path):
    from vibeocr.client import get_unique_output_path

    p = tmp_path / "out.md"
    assert get_unique_output_path(p) == p


def test_get_unique_output_path_with_conflict(tmp_path):
    from vibeocr.client import get_unique_output_path

    (tmp_path / "out.md").write_text("old")
    result = get_unique_output_path(tmp_path / "out.md")
    assert result == tmp_path / "out_1.md"


def test_get_unique_output_path_multiple_conflicts(tmp_path):
    from vibeocr.client import get_unique_output_path

    (tmp_path / "r.md").write_text("a")
    (tmp_path / "r_1.md").write_text("b")
    (tmp_path / "r_2.md").write_text("c")
    result = get_unique_output_path(tmp_path / "r.md")
    assert result == tmp_path / "r_3.md"


def test_shutdown_backend_client_is_noop():
    """shutdown_backend_client 是空操作（v2 supervisor 拥有后端）。"""
    from vibeocr.client import shutdown_backend_client

    # 仅验证可调用且不抛
    shutdown_backend_client()


def test_sync_backend_error_is_runtime_error():
    """SyncBackendError 继承 RuntimeError（legacy→v2 兼容）。"""
    from vibeocr.client.errors import SyncBackendError

    err = SyncBackendError("boom")
    assert isinstance(err, RuntimeError)
    assert str(err) == "boom"
    # 可被 raise / catch
    with __import__("pytest").raises(SyncBackendError):
        raise SyncBackendError("x")
