import json
import math
import time
from copy import deepcopy
from importlib import resources

import pytest
from jsonschema import validate
from jsonschema.exceptions import ValidationError

from vibeocr.contracts.tables import (
    MAX_TABLE_CELLS,
    CoordinateSpace,
    TableCellV1,
    TableModelV1,
    TableProvenanceV1,
)


def _valid_payload() -> dict:
    return TableModelV1(
        table_id="strict",
        row_count=1,
        column_count=1,
        cells=(TableCellV1(cell_id="cell", row=0, column=0, text="value"),),
    ).to_payload()


def test_table_model_preserves_mixed_merge_ranges_through_payload_roundtrip():
    table = TableModelV1(
        table_id="page-0-table-0",
        row_count=2,
        column_count=3,
        coordinate_space=CoordinateSpace.PIXEL,
        cells=(
            TableCellV1(
                cell_id="r0c0",
                row=0,
                column=0,
                rowspan=2,
                text="纵向",
            ),
            TableCellV1(
                cell_id="r0c1",
                row=0,
                column=1,
                colspan=2,
                text="横向",
            ),
            TableCellV1(cell_id="r1c1", row=1, column=1, text="左下"),
            TableCellV1(cell_id="r1c2", row=1, column=2, text="右下"),
        ),
    )

    assert table.merged_ranges() == ((0, 0, 1, 0), (0, 1, 0, 2))
    assert TableModelV1.from_payload(table.to_payload()) == table


def test_table_model_rejects_overlapping_cell_coverage():
    with pytest.raises(ValueError, match="overlap"):
        TableModelV1(
            table_id="overlap",
            row_count=2,
            column_count=2,
            cells=(
                TableCellV1(
                    cell_id="wide",
                    row=0,
                    column=0,
                    colspan=2,
                ),
                TableCellV1(
                    cell_id="conflict",
                    row=0,
                    column=1,
                ),
            ),
        )


def test_table_model_rejects_cells_outside_declared_grid():
    with pytest.raises(ValueError, match="outside"):
        TableModelV1(
            table_id="outside",
            row_count=1,
            column_count=1,
            cells=(
                TableCellV1(
                    cell_id="r0c0",
                    row=0,
                    column=0,
                    colspan=2,
                ),
            ),
        )


def test_table_model_payload_matches_packaged_json_schema():
    table = TableModelV1(
        table_id="schema",
        row_count=1,
        column_count=1,
        cells=(TableCellV1(cell_id="r0c0", row=0, column=0, text="value"),),
    )
    schema_path = resources.files("vibeocr.contracts").joinpath(
        "schemas/table-v1.schema.json"
    )

    validate(instance=table.to_payload(), schema=json.loads(schema_path.read_text()))


def test_table_model_roundtrip_preserves_geometry_and_provenance():
    table = TableModelV1(
        table_id="provider-table",
        row_count=1,
        column_count=1,
        coordinate_space=CoordinateSpace.PIXEL,
        cells=(
            TableCellV1(
                cell_id="provider-cell-7",
                row=0,
                column=0,
                text="值",
                bbox=(10.0, 20.0, 30.0, 40.0),
                confidence=0.98,
                source_refs=("paddle-cell:7", "ocr:3"),
            ),
        ),
        provenance=TableProvenanceV1(
            pipeline="TABLE_RECOGNITION",
            provider_schema="paddlex-table-res",
            provider_version="3.7",
            warnings=("box-order-normalized",),
        ),
    )

    assert TableModelV1.from_payload(table.to_payload()) == table


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.pop("schema_version"),
        lambda payload: payload.update({"extra": True}),
        lambda payload: payload.update({"row_count": "1"}),
        lambda payload: payload.update({"table_id": ""}),
        lambda payload: payload["cells"][0].update({"is_header": "false"}),
        lambda payload: payload["cells"][0].update({"bbox": [1, 2, 3]}),
        lambda payload: payload["cells"][0].update({"unexpected": 1}),
    ],
)
def test_table_payload_rejects_every_shape_rejected_by_schema(mutate):
    payload = deepcopy(_valid_payload())
    mutate(payload)
    schema_path = resources.files("vibeocr.contracts").joinpath(
        "schemas/table-v1.schema.json"
    )
    schema = json.loads(schema_path.read_text())

    with pytest.raises(ValidationError):
        validate(instance=payload, schema=schema)
    with pytest.raises((TypeError, ValueError)):
        TableModelV1.from_payload(payload)


def test_table_model_rejects_duplicate_cell_ids_and_nonfinite_geometry():
    with pytest.raises(ValueError, match="duplicate"):
        TableModelV1(
            table_id="duplicate",
            row_count=1,
            column_count=2,
            cells=(
                TableCellV1(cell_id="same", row=0, column=0),
                TableCellV1(cell_id="same", row=0, column=1),
            ),
        )
    with pytest.raises(ValueError, match="finite"):
        TableCellV1(
            cell_id="nan",
            row=0,
            column=0,
            bbox=(0.0, 0.0, math.nan, 1.0),
        )


def test_table_model_rejects_excessive_span_without_expanding_coverage():
    started = time.perf_counter()
    with pytest.raises(ValueError, match=r"outside|limit"):
        TableModelV1(
            table_id="bounded",
            row_count=1,
            column_count=1,
            cells=(
                TableCellV1(
                    cell_id="huge",
                    row=0,
                    column=0,
                    rowspan=10**9,
                    colspan=10**9,
                ),
            ),
        )
    assert time.perf_counter() - started < 0.1


def test_table_model_rejects_sparse_grid_that_would_exhaust_dense_projections():
    started = time.perf_counter()
    with pytest.raises(ValueError, match="grid area"):
        TableModelV1(
            table_id="sparse-but-huge",
            row_count=10_000,
            column_count=10_000,
            cells=(),
        )
    assert time.perf_counter() - started < 0.1


def test_payload_rejects_cell_count_before_constructing_any_cell(monkeypatch):
    payload = _valid_payload()
    payload["cells"] = [payload["cells"][0]] * (MAX_TABLE_CELLS + 1)
    called = False

    def fail_if_called(cls, _payload):
        nonlocal called
        called = True
        raise AssertionError("cell conversion must not run for an oversized payload")

    monkeypatch.setattr(TableCellV1, "from_payload", classmethod(fail_if_called))

    with pytest.raises(ValueError, match="cells exceed"):
        TableModelV1.from_payload(payload)
    assert called is False
