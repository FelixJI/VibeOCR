from unittest.mock import MagicMock

from vibeocr.client.pdf import PdfBackendClient
from vibeocr.ipc.schemas import ModelDiff, SaveResponse


def test_save_forwards_fast_finalize_flag() -> None:
    client = PdfBackendClient()
    client._command = MagicMock(  # type: ignore[method-assign]
        return_value=SaveResponse(path="C:/doc.pdf", diff=ModelDiff())
    )

    response = client.save(
        "sid-1",
        None,
        {"compress_on_save": True},
        rewrite_text_layers=False,
    )

    assert response.path == "C:/doc.pdf"
    client._command.assert_called_once_with(
        "sid-1",
        "save",
        {
            "path": None,
            "pdf_settings": {"compress_on_save": True},
            "rewrite_text_layers": False,
        },
    )
