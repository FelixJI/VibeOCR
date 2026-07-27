import pytest

import vibeocr.tables.html_adapter as html_adapter
from vibeocr.contracts.tables import TableCellV1, TableModelV1
from vibeocr.tables.blocks import canonicalize_table_block, table_model_from_block
from vibeocr.tables.html_adapter import (
    parse_table_source_layout,
    table_model_from_html,
    table_model_to_html,
)
from vibeocr.tables.projections import (
    table_model_to_grid,
    table_model_to_markdown,
    table_model_to_tsv,
)


def test_html_adapter_preserves_mixed_row_and_column_spans():
    html = (
        "<table>"
        '<tr><td rowspan="2">纵向</td><th colspan="2">横向</th></tr>'
        "<tr><td>左下</td><td>右下</td></tr>"
        "</table>"
    )

    table = table_model_from_html(html, table_id="mixed")

    assert (table.row_count, table.column_count) == (2, 3)
    assert [
        (
            cell.row,
            cell.column,
            cell.rowspan,
            cell.colspan,
            cell.text,
            cell.is_header,
        )
        for cell in table.cells
    ] == [
        (0, 0, 2, 1, "纵向", False),
        (0, 1, 1, 2, "横向", True),
        (1, 1, 1, 1, "左下", False),
        (1, 2, 1, 1, "右下", False),
    ]
    assert table_model_from_html(table_model_to_html(table), table_id="mixed") == table


def test_legacy_table_block_is_upgraded_without_losing_source_html():
    source_html = (
        '<table><tr><td rowspan="2">A</td><td>B</td></tr><tr><td>C</td></tr></table>'
    )
    block = {
        "type": "table",
        "table_body": source_html,
        "bbox": [0, 0, 100, 50],
    }

    upgraded = canonicalize_table_block(
        block,
        table_id="legacy-table",
        pipeline="MinerU",
    )

    assert upgraded["source"]["source_html"] == source_html
    assert upgraded["table"]["schema_version"] == 1
    assert table_model_from_block(upgraded).merged_ranges() == ((0, 0, 1, 0),)


def test_table_grid_projection_keeps_merged_positions_empty():
    table = table_model_from_html(
        (
            '<table><tr><td rowspan="2">纵向</td>'
            '<td colspan="2">横向</td></tr>'
            "<tr><td>左下</td><td>右下</td></tr></table>"
        ),
        table_id="grid",
    )

    assert table_model_to_grid(table) == [
        ["纵向", "横向", ""],
        ["", "左下", "右下"],
    ]
    assert table_model_to_tsv(table) == "纵向\t横向\t\n\t左下\t右下"
    markdown = table_model_to_markdown(table)
    assert markdown.warnings == ("lossy_markdown_source",)
    assert "| 纵向 | 横向 |  |" in markdown.text


def test_single_table_adapter_rejects_multiple_top_level_tables():
    with pytest.raises(ValueError, match="multiple"):
        table_model_from_html(
            "<table><tr><td>A</td></tr></table><table><tr><td>B</td></tr></table>",
            table_id="first",
        )


@pytest.mark.parametrize(
    "source, message",
    [
        ("<div>not a table</div>", "contain a table"),
        ("<table><tr><td rowspan='bogus'>A</td></tr></table>", "rowspan"),
        ("<table><tr><td>A</td></tr>", "not closed"),
        (
            "<table><tr><td rowspan='1000000000'>A</td></tr></table>",
            "rowspan",
        ),
        (
            "<table><tr><td>before<table><tr><td>inner</td></tr></table>"
            "after</td></tr></table>",
            "nested",
        ),
    ],
)
def test_html_adapter_rejects_unprovable_legacy_shapes(source, message):
    with pytest.raises(ValueError, match=message):
        table_model_from_html(source, table_id="invalid")


def test_html_adapter_supports_optional_cell_end_tags_and_empty_rows():
    table = table_model_from_html(
        "<table><tr><td>A<td>B</tr><tr></tr></table>",
        table_id="optional",
    )

    assert (table.row_count, table.column_count) == (2, 2)
    assert [cell.text for cell in table.cells] == ["A", "B"]
    assert (
        table_model_from_html(table_model_to_html(table), table_id="optional") == table
    )


def test_canonical_html_roundtrip_preserves_whitespace_empty_rows_and_cell_ids():
    table = TableModelV1(
        table_id="roundtrip",
        row_count=2,
        column_count=1,
        cells=(
            TableCellV1(
                cell_id="stable-cell",
                row=0,
                column=0,
                text=" A  B ",
            ),
        ),
    )

    assert (
        table_model_from_html(table_model_to_html(table), table_id="roundtrip") == table
    )


def test_canonical_first_block_is_not_mislabeled_as_legacy():
    table = TableModelV1(
        table_id="canonical",
        row_count=1,
        column_count=1,
        cells=(TableCellV1(cell_id="cell", row=0, column=0, text="fresh"),),
    )
    upgraded = canonicalize_table_block(
        {
            "type": "table",
            "table": table.to_payload(),
            "table_body": "<table><tr><td>stale</td></tr></table>",
        },
        table_id="canonical",
        pipeline="MINERU",
    )

    assert upgraded["table"]["cells"][0]["text"] == "fresh"
    assert upgraded["table"]["provenance"]["provider_schema"] == "canonical-v1"
    assert upgraded["table"]["provenance"]["warnings"] == []


def test_display_mode_can_fallback_from_unknown_canonical_to_legacy_html():
    block = {
        "type": "table",
        "table": {"schema_version": 999},
        "table_body": "<table><tr><td>legacy</td></tr></table>",
    }

    with pytest.raises(ValueError):
        table_model_from_block(block)
    table = table_model_from_block(block, strict_canonical=False)
    assert table.cells[0].text == "legacy"


def test_html_adapter_aborts_while_parsing_excessive_cell_text(monkeypatch):
    monkeypatch.setattr(html_adapter, "MAX_HTML_TABLE_TEXT_CHARS", 4)

    with pytest.raises(ValueError, match="text exceeds"):
        table_model_from_html(
            "<table><tr><td>12345</td></tr></table>",
            table_id="too-much-text",
        )


def test_html_adapter_rejects_large_markup_before_parser_allocation(monkeypatch):
    monkeypatch.setattr(html_adapter, "MAX_HTML_TABLE_SOURCE_CHARS", 32)
    source = "<!--" + ("x" * 40) + "--><table><tr><td>A</td></tr></table>"

    with pytest.raises(ValueError, match="source exceeds"):
        table_model_from_html(source, table_id="too-much-markup")
    with pytest.raises(ValueError, match="source exceeds"):
        parse_table_source_layout(source, table_id="too-much-markup")


def test_html_adapter_aborts_while_parsing_excessive_cell_count(monkeypatch):
    monkeypatch.setattr(html_adapter, "MAX_TABLE_CELLS", 2)

    with pytest.raises(ValueError, match="cell count"):
        table_model_from_html(
            "<table><tr><td>A</td><td>B</td><td>C</td></tr></table>",
            table_id="too-many-cells",
        )


def test_html_adapter_aborts_while_parsing_excessive_coverage(monkeypatch):
    monkeypatch.setattr(html_adapter, "MAX_TABLE_COVERAGE", 2)

    with pytest.raises(ValueError, match="coverage"):
        table_model_from_html(
            '<table><tr><td colspan="3">A</td></tr></table>',
            table_id="too-much-coverage",
        )


def test_source_layout_uses_structured_parser_offsets_with_optional_end_tags():
    source = (
        "<table>\n<tr><td rowspan='2'><b>A</b><td>B</tr><tr><td>C</td></tr></table>"
    )

    layout = parse_table_source_layout(source, table_id="source-layout")

    assert layout.model.merged_ranges() == ((0, 0, 1, 0),)
    assert [cell.source_text for cell in layout.cells] == ["A", "B", "C"]
    assert [source[cell.content_start : cell.content_end] for cell in layout.cells] == [
        "<b>A</b>",
        "B",
        "C",
    ]
