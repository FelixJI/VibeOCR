"""export_service 补充测试 — 覆盖 txt/html/markdown 导出、不支持的格式、get_output_filename 等"""

from vibeocr.models.ocr_result import OCRResult
from vibeocr.services.export_service import ExportService


def _make_result(**kwargs):
    return OCRResult(
        raw_text=kwargs.get("raw_text", ""),
        markdown_text=kwargs.get("markdown_text", ""),
        html_text=kwargs.get("html_text", ""),
        content_list=kwargs.get("content_list", []),
        images=kwargs.get("images", {}),
    )


class TestExportUnsupportedFormat:
    def test_unknown_format_returns_false(self, tmp_path):
        result = _make_result(raw_text="hello")
        out = tmp_path / "test.xyz"
        assert not ExportService.export(result, out, "pdf")


class TestGetOutputFilename:
    def test_markdown(self):
        assert ExportService.get_output_filename("report.pdf", "markdown") == "report.md"

    def test_html(self):
        assert ExportService.get_output_filename("doc.txt", "html") == "doc.html"

    def test_txt(self):
        assert ExportService.get_output_filename("file.docx", "txt") == "file.txt"

    def test_docx(self):
        assert ExportService.get_output_filename("scan.png", "docx") == "scan.docx"

    def test_xlsx(self):
        assert ExportService.get_output_filename("data.pdf", "xlsx") == "data.xlsx"

    def test_unknown_format_falls_back_to_txt(self):
        assert ExportService.get_output_filename("file.pdf", "xyz") == "file.txt"


class TestExportTxt:
    def test_exports_raw_text(self, tmp_path):
        result = _make_result(raw_text="hello world")
        out = tmp_path / "test.txt"
        assert ExportService.export(result, out, "txt")
        assert out.read_text(encoding="utf-8") == "hello world"

    def test_exports_markdown_as_fallback(self, tmp_path):
        result = _make_result(markdown_text="# Title")
        out = tmp_path / "test.txt"
        assert ExportService.export(result, out, "txt")
        assert out.read_text(encoding="utf-8") == "# Title"


class TestExportMarkdown:
    def test_exports_markdown_text(self, tmp_path):
        result = _make_result(markdown_text="## Hello")
        out = tmp_path / "test.md"
        assert ExportService.export(result, out, "markdown")
        assert out.read_text(encoding="utf-8") == "## Hello"

    def test_falls_back_to_raw_text(self, tmp_path):
        result = _make_result(raw_text="plain text")
        out = tmp_path / "test.md"
        assert ExportService.export(result, out, "markdown")
        assert out.read_text(encoding="utf-8") == "plain text"

    def test_saves_images_to_subdir(self, tmp_path):
        import io

        from PIL import Image

        img = Image.new("RGB", (10, 10), "blue")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        img_bytes = buf.getvalue()

        result = _make_result(
            markdown_text="![img](images/test.png)",
            images={"images/test.png": img_bytes},
        )
        out = tmp_path / "report.md"
        assert ExportService.export(result, out, "markdown")

        img_file = tmp_path / "report_images" / "images" / "test.png"
        assert img_file.exists()
        assert img_file.read_bytes() == img_bytes


class TestExportHtml:
    def test_exports_html_text(self, tmp_path):
        result = _make_result(html_text="<p>Hello</p>")
        out = tmp_path / "test.html"
        assert ExportService.export(result, out, "html")
        content = out.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in content
        assert "<p>Hello</p>" in content

    def test_embeds_base64_images(self, tmp_path):
        import io

        from PIL import Image

        img = Image.new("RGB", (10, 10), "green")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        img_bytes = buf.getvalue()

        result = _make_result(
            html_text='<img src="images/photo.png">',
            images={"images/photo.png": img_bytes},
        )
        out = tmp_path / "test.html"
        assert ExportService.export(result, out, "html")
        content = out.read_text(encoding="utf-8")
        assert "data:image/png;base64," in content

    def test_jpg_mime_type(self, tmp_path):
        result = _make_result(
            html_text='<img src="pic.jpg">',
            images={"pic.jpg": b"\xff\xd8\xff\xe0"},
        )
        out = tmp_path / "test.html"
        assert ExportService.export(result, out, "html")
        content = out.read_text(encoding="utf-8")
        assert "data:image/jpeg;base64," in content


class TestExportDocxExtra:
    def test_title_block(self, tmp_path):
        from docx import Document

        result = _make_result(
            content_list=[{"type": "title", "text": "My Title", "level": 2}],
        )
        out = tmp_path / "test.docx"
        assert ExportService.export(result, out, "docx")
        doc = Document(str(out))
        headings = [p for p in doc.paragraphs if p.style.name.startswith("Heading")]
        assert any("My Title" in h.text for h in headings)

    def test_equation_block(self, tmp_path):
        from docx import Document

        result = _make_result(
            content_list=[{"type": "equation", "text": "a^2+b^2=c^2"}],
        )
        out = tmp_path / "test.docx"
        assert ExportService.export(result, out, "docx")
        doc = Document(str(out))
        assert any("a^2+b^2=c^2" in p.text for p in doc.paragraphs)

    def test_code_block(self, tmp_path):
        from docx import Document

        result = _make_result(
            content_list=[{"type": "code", "code_body": "print('hi')"}],
        )
        out = tmp_path / "test.docx"
        assert ExportService.export(result, out, "docx")
        doc = Document(str(out))
        assert any("print" in p.text for p in doc.paragraphs)

    def test_fallback_to_raw_text(self, tmp_path):
        from docx import Document

        result = _make_result(raw_text="line1\nline2")
        out = tmp_path / "test.docx"
        assert ExportService.export(result, out, "docx")
        doc = Document(str(out))
        texts = [p.text for p in doc.paragraphs]
        assert "line1" in texts
        assert "line2" in texts

    def test_table_caption_and_footnote(self, tmp_path):
        from docx import Document

        result = _make_result(
            content_list=[
                {
                    "type": "table",
                    "table_body": "<tr><td>X</td></tr>",
                    "table_caption": ["Table 1"],
                    "table_footnote": ["note"],
                },
            ],
        )
        out = tmp_path / "test.docx"
        assert ExportService.export(result, out, "docx")
        doc = Document(str(out))
        all_text = " ".join(p.text for p in doc.paragraphs)
        assert "Table 1" in all_text
        assert "note" in all_text


class TestExportXlsxExtra:
    def test_title_block(self, tmp_path):
        from openpyxl import load_workbook

        result = _make_result(
            content_list=[{"type": "title", "text": "Report"}],
        )
        out = tmp_path / "test.xlsx"
        assert ExportService.export(result, out, "xlsx")
        wb = load_workbook(str(out))
        assert wb.active.title == "文本汇总"

    def test_code_block(self, tmp_path):
        from openpyxl import load_workbook

        result = _make_result(
            content_list=[{"type": "code", "code_body": "x=1"}],
        )
        out = tmp_path / "test.xlsx"
        assert ExportService.export(result, out, "xlsx")
        wb = load_workbook(str(out))
        values = [c.value for row in wb.active.iter_rows() for c in row if c.value]
        assert any("x=1" in str(v) for v in values)

    def test_fallback_to_raw_text(self, tmp_path):
        from openpyxl import load_workbook

        result = _make_result(raw_text="hello\nworld")
        out = tmp_path / "test.xlsx"
        assert ExportService.export(result, out, "xlsx")
        wb = load_workbook(str(out))
        values = [c.value for row in wb.active.iter_rows() for c in row if c.value]
        assert "hello" in values
        assert "world" in values

    def test_table_only_removes_sheet(self, tmp_path):
        from openpyxl import load_workbook

        result = _make_result(
            content_list=[
                {"type": "table", "table_body": "<tr><td>V</td></tr>"},
            ],
        )
        out = tmp_path / "test.xlsx"
        assert ExportService.export(result, out, "xlsx")
        wb = load_workbook(str(out))
        assert "Sheet" not in wb.sheetnames
