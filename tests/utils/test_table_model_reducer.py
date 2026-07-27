from vibeocr.contracts.tables import (
    TableCellV1,
    TableModelV1,
    TableProvenanceV1,
)
from vibeocr.tables.reducer import update_table_cell


def test_update_table_cell_uses_stable_ids_and_refreshes_all_projections():
    table = TableModelV1(
        table_id="table-orders",
        row_count=2,
        column_count=3,
        cells=(
            TableCellV1(
                cell_id="header",
                row=0,
                column=0,
                colspan=2,
                text="重复",
                is_header=True,
            ),
            TableCellV1(
                cell_id="amount",
                row=0,
                column=2,
                rowspan=2,
                text="重复",
            ),
            TableCellV1(cell_id="detail", row=1, column=0, colspan=2, text="明细"),
        ),
        provenance=TableProvenanceV1(
            pipeline="MINERU",
            provider_schema="legacy-html",
        ),
    )
    block = {
        "type": "table",
        "block_id": "block-orders",
        "table": table.to_payload(),
        "table_body": "<table><tr><th colspan=2>旧投影</th></tr></table>",
        "text": "旧纯文本",
        "source": {"source_html": "<table>供应商原文</table>"},
    }

    updated = update_table_cell(
        block,
        table_id="table-orders",
        cell_id="amount",
        new_text="42 & <ok>",
    )

    updated_table = TableModelV1.from_payload(updated["table"])
    by_id = {cell.cell_id: cell for cell in updated_table.cells}
    assert by_id["header"].text == "重复"
    assert by_id["amount"].text == "42 & <ok>"
    assert by_id["amount"].rowspan == 2
    assert updated_table.provenance == table.provenance
    assert 'rowspan="2"' in updated["table_body"]
    assert "42 &amp; &lt;ok&gt;" in updated["table_body"]
    assert updated["text"] == "重复\t42 & <ok>\n明细"
    assert updated["source"]["source_html"] == "<table>供应商原文</table>"


def test_update_table_cell_rejects_wrong_table_or_cell_id():
    table = TableModelV1(
        table_id="table-a",
        row_count=1,
        column_count=1,
        cells=(TableCellV1(cell_id="cell-a", row=0, column=0, text="A"),),
    )
    block = {"type": "table", "table": table.to_payload()}

    for table_id, cell_id in (("table-b", "cell-a"), ("table-a", "cell-b")):
        try:
            update_table_cell(
                block,
                table_id=table_id,
                cell_id=cell_id,
                new_text="B",
            )
        except KeyError:
            pass
        else:
            raise AssertionError(
                "unknown stable IDs must not silently edit another cell"
            )


def test_result_reducer_finds_reordered_table_by_ids_and_rebuilds_projections():
    from vibeocr.models.ocr_result import OCRResult, TextBlock
    from vibeocr.tables.reducer import update_result_table_cell

    table = TableModelV1(
        table_id="table-target",
        row_count=2,
        column_count=2,
        cells=(
            TableCellV1(
                cell_id="merged",
                row=0,
                column=0,
                colspan=2,
                text="重复",
            ),
            TableCellV1(cell_id="left", row=1, column=0, text="重复"),
            TableCellV1(cell_id="right", row=1, column=1, text="尾"),
        ),
    )
    result = OCRResult(
        content_list=[
            {"type": "text", "block_id": "intro", "text": "开头"},
            {
                "type": "table",
                "block_id": "table-block",
                "table": table.to_payload(),
                "table_body": "stale",
                "text": "stale",
            },
        ],
        text_blocks=[
            TextBlock(
                text="重复\n重复\t尾",
                score=0.8,
                bbox=None,
                content_index=1,
                content_id="table-block",
                label="table",
            ),
            TextBlock(
                text="开头",
                score=0.9,
                bbox=None,
                content_index=0,
                content_id="intro",
            ),
        ],
        text_with_scores=[("重复\n重复\t尾", 0.8), ("开头", 0.9)],
    )

    content_index = update_result_table_cell(
        result,
        table_id="table-target",
        cell_id="left",
        new_text="已改 & <值>",
    )

    assert content_index == 1
    updated = TableModelV1.from_payload(result.content_list[1]["table"])
    assert [cell.text for cell in updated.cells] == ["重复", "已改 & <值>", "尾"]
    assert result.text_blocks[0].text == "重复\n已改 & <值>\t尾"
    assert result.text_blocks[0].is_manually_edited is True
    assert result.text_blocks[1].text == "开头"
    assert "<table" not in result.raw_text
    assert 'colspan="2"' in result.html_text
    assert "已改 &amp; &lt;值&gt;" in result.html_text
    assert "lossy_markdown_source" in result.content_list[1]["projection_warnings"]


def test_result_reducer_preserves_structured_non_table_projections():
    from vibeocr.models.ocr_result import OCRResult, TextBlock
    from vibeocr.tables.reducer import update_result_table_cell

    table = TableModelV1(
        table_id="table-mixed",
        row_count=1,
        column_count=1,
        cells=(TableCellV1(cell_id="value", row=0, column=0, text="旧值"),),
    )
    result = OCRResult(
        content_list=[
            {"type": "title", "level": 2, "text": "报告 & 摘要"},
            {
                "type": "table",
                "block_id": "table-mixed",
                "table": table.to_payload(),
                "table_body": "<table><tr><td>旧值</td></tr></table>",
            },
            {"type": "list", "list_items": ["第一项", "第二项"]},
            {"type": "formula", "text": "x < y"},
            {
                "type": "image",
                "image_caption": ["图 & 一"],
                "img_path": "assets/a&b.png",
            },
        ],
        text_blocks=[
            TextBlock(
                text="旧值",
                score=0.9,
                bbox=None,
                label="table",
                content_index=1,
                content_id="table-mixed",
            )
        ],
        text_with_scores=[("旧值", 0.9)],
    )

    update_result_table_cell(
        result,
        table_id="table-mixed",
        cell_id="value",
        new_text="新值",
    )

    assert result.markdown_text.split("\n\n") == [
        "## 报告 & 摘要",
        "| 新值 |\n| --- |",
        "- 第一项\n- 第二项",
        "$$x < y$$",
        "![图 & 一](assets/a&b.png)",
    ]
    assert result.html_text.index("<h2>报告 &amp; 摘要</h2>") < result.html_text.index(
        "<table"
    )
    assert "<ul><li>第一项</li><li>第二项</li></ul>" in result.html_text
    assert '<div class="equation">x &lt; y</div>' in result.html_text
    assert '<p class="image-caption">图 &amp; 一</p>' in result.html_text
    assert '<img src="assets/a&amp;b.png" alt="">' in result.html_text


def test_result_reducer_keeps_table_raw_projection_without_a_text_block():
    from vibeocr.models.ocr_result import OCRResult
    from vibeocr.tables.reducer import update_result_table_cell

    table = TableModelV1(
        table_id="table-without-bbox",
        row_count=1,
        column_count=2,
        cells=(
            TableCellV1(cell_id="left", row=0, column=0, text="A"),
            TableCellV1(cell_id="right", row=0, column=1, text="B"),
        ),
    )
    result = OCRResult(
        content_list=[
            {
                "type": "table",
                "block_id": "table-without-bbox",
                "table": table.to_payload(),
            }
        ],
        text_blocks=[],
    )

    update_result_table_cell(
        result,
        table_id="table-without-bbox",
        cell_id="right",
        new_text="updated",
    )

    assert result.raw_text == "A\tupdated"
    assert result.markdown_text.count("| A | updated |") == 1
    assert result.html_text.count("<table") == 1
