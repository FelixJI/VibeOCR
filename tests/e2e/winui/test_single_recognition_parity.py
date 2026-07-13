from __future__ import annotations

import hashlib
import json
from pathlib import Path

from vibeocr.models.ocr_result import OCRResult
from vibeocr.services.export_service import ExportService


def test_python_export_facade_matches_single_recognition_fixture(tmp_path: Path) -> None:
    fixture = json.loads(
        (Path(__file__).parents[2] / "fixtures/parity/single-recognition/expected.json").read_text(encoding="utf-8")
    )
    result = OCRResult(
        raw_text=fixture["raw_text"],
        markdown_text=fixture["markdown_text"],
        html_text=fixture["html_text"],
        content_list=fixture["blocks"],
    )
    output = tmp_path / "Unicode 路径"
    destinations = {
        "txt": output / "结果.txt",
        "markdown": output / "结果.md",
        "html": output / "结果.html",
    }
    for format_name, destination in destinations.items():
        assert ExportService.export(result, destination, format_name)
        assert destination.exists()

    assert hashlib.sha256(destinations["txt"].read_bytes()).hexdigest() == fixture["txt_sha256"]
    assert hashlib.sha256(destinations["markdown"].read_bytes()).hexdigest() == fixture["markdown_sha256"]
    html = destinations["html"].read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in html and fixture["html_text"] in html
    assert "<meta charset='utf-8'>" in html
