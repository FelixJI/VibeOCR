"""Tests for PdfSessionManager."""

import fitz
import pytest
from unittest.mock import MagicMock, patch

from vibeocr.managers.pdf_session_manager import PdfSessionManager
from vibeocr.services.pdf_service import PdfService


def _create_test_pdf(path, num_pages=2):
    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 72), f"Page {i + 1}", fontsize=12)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def manager(qapp):
    mgr = PdfSessionManager(parent=qapp)
    yield mgr
    mgr.shutdown()


@pytest.fixture
def test_pdf_a(tmp_path):
    return _create_test_pdf(tmp_path / "a.pdf", num_pages=2)


@pytest.fixture
def test_pdf_b(tmp_path):
    return _create_test_pdf(tmp_path / "b.pdf", num_pages=3)


class TestPdfSessionManagerSessions:
    def test_open_session(self, manager, test_pdf_a):
        session = manager.open_session(str(test_pdf_a))
        assert session is not None
        assert session.file_path == str(test_pdf_a)
        assert session.pdf_document.page_count == 2

    def test_active_session_is_last_opened(self, manager, test_pdf_a, test_pdf_b):
        manager.open_session(str(test_pdf_a))
        manager.open_session(str(test_pdf_b))
        assert manager.active_session is not None
        assert manager.active_session.file_path == str(test_pdf_b)

    def test_switch_session(self, manager, test_pdf_a, test_pdf_b):
        manager.open_session(str(test_pdf_a))
        manager.open_session(str(test_pdf_b))
        manager.switch_session(str(test_pdf_a))
        assert manager.active_session.file_path == str(test_pdf_a)

    def test_close_session(self, manager, test_pdf_a):
        manager.open_session(str(test_pdf_a))
        manager.close_session(str(test_pdf_a))
        assert manager.active_session is None
        assert len(manager.session_paths) == 0

    def test_close_active_switches_to_remaining(self, manager, test_pdf_a, test_pdf_b):
        manager.open_session(str(test_pdf_a))
        manager.open_session(str(test_pdf_b))
        manager.close_session(str(test_pdf_b))
        assert manager.active_session is not None
        assert manager.active_session.file_path == str(test_pdf_a)

    def test_session_paths(self, manager, test_pdf_a, test_pdf_b):
        manager.open_session(str(test_pdf_a))
        manager.open_session(str(test_pdf_b))
        paths = manager.session_paths
        assert str(test_pdf_a) in paths
        assert str(test_pdf_b) in paths

    def test_get_session(self, manager, test_pdf_a):
        manager.open_session(str(test_pdf_a))
        session = manager.get_session(str(test_pdf_a))
        assert session is not None
        assert session.file_path == str(test_pdf_a)

    def test_open_nonexistent_raises(self, manager):
        with pytest.raises(FileNotFoundError):
            manager.open_session("/nonexistent/file.pdf")


class TestPdfSessionManagerShutdown:
    def test_shutdown_closes_all_docs(self, manager, test_pdf_a, test_pdf_b):
        s1 = manager.open_session(str(test_pdf_a))
        s2 = manager.open_session(str(test_pdf_b))
        manager.shutdown()
        assert manager.active_session is None
        assert len(manager.session_paths) == 0
