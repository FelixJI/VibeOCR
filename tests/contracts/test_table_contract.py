import json
import math
import time
from copy import deepcopy
from importlib import resources

import pytest
from jsonschema import validate
from jsonschema.exceptions import ValidationError

from vibeocr.runtime_contracts.contracts.tables import (
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
    schema_path = resources.files("vibeocr.runtime_contracts.contracts").joinpath(
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
    schema_path = resources.files("vibeocr.runtime_contracts.contracts").joinpath(
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


# ---------------------------------------------------------------------------
# Direct construction rejection branches in TableCellV1 / TableProvenanceV1 /
# TableModelV1 __post_init__ and from_payload (covers the previously uncovered
# guard lines).
# ---------------------------------------------------------------------------


def test_table_cell_rejects_empty_cell_id():
    """Line 95: cell_id must be a non-empty string."""
    with pytest.raises(ValueError, match="cell_id"):
        TableCellV1(cell_id="", row=0, column=0)


def test_table_cell_rejects_non_string_cell_id():
    """Line 95 (other branch): cell_id must be a string type."""
    with pytest.raises(ValueError, match="cell_id"):
        TableCellV1(cell_id=123, row=0, column=0)  # type: ignore[arg-type]


def test_table_cell_rejects_bool_row():
    """Line 97: a bool is not a valid integer for row/column."""
    with pytest.raises(ValueError, match="row and column"):
        TableCellV1(cell_id="x", row=True, column=0)  # type: ignore[arg-type]


def test_table_cell_rejects_non_int_row():
    """Line 97: row must be an int."""
    with pytest.raises(ValueError, match="row and column"):
        TableCellV1(cell_id="x", row=1.5, column=0)  # type: ignore[arg-type]


def test_table_cell_rejects_non_int_spans():
    """Line 99: rowspan/colspan must be integers."""
    with pytest.raises(ValueError, match="spans"):
        TableCellV1(cell_id="x", row=0, column=0, rowspan=1.5)  # type: ignore[arg-type]


def test_table_cell_rejects_non_string_text():
    """Line 101: text must be a string."""
    with pytest.raises(ValueError, match="text/header"):
        TableCellV1(cell_id="x", row=0, column=0, text=123)  # type: ignore[arg-type]


def test_table_cell_rejects_non_bool_header():
    """Line 101: is_header must be a bool."""
    with pytest.raises(ValueError, match="text/header"):
        TableCellV1(cell_id="x", row=0, column=0, is_header="yes")  # type: ignore[arg-type]


def test_table_cell_rejects_non_finite_confidence():
    """Line 109: confidence must be a finite number."""
    with pytest.raises(ValueError, match="confidence"):
        TableCellV1(cell_id="x", row=0, column=0, confidence=math.nan)


def test_table_cell_rejects_non_string_source_refs():
    """Line 113: source_refs entries must all be strings."""
    with pytest.raises(ValueError, match="source_refs"):
        TableCellV1(cell_id="x", row=0, column=0, source_refs=(1,))  # type: ignore[arg-type]


def test_table_cell_rejects_non_tuple_source_refs():
    """Line 113: source_refs must be a tuple (list passed directly)."""
    with pytest.raises(ValueError, match="source_refs"):
        TableCellV1(cell_id="x", row=0, column=0, source_refs=["a"])  # type: ignore[arg-type]


def test_table_cell_from_payload_rejects_non_dict():
    """Line 132: cell payload must be an object."""
    with pytest.raises(ValueError, match="payload must be an object"):
        TableCellV1.from_payload(["not", "a", "dict"])  # type: ignore[arg-type]


def test_table_cell_from_payload_rejects_non_finite_confidence():
    """Line 155: confidence must be a finite number when parsed from payload."""
    payload = TableCellV1(cell_id="x", row=0, column=0, confidence=0.5).to_payload()
    payload["confidence"] = math.inf
    with pytest.raises(ValueError, match="confidence"):
        TableCellV1.from_payload(payload)


def test_table_provenance_rejects_non_string_field():
    """Line 198: provenance string fields must all be strings."""
    with pytest.raises(ValueError, match="provenance fields must be strings"):
        TableProvenanceV1(pipeline=123)  # type: ignore[arg-type]


def test_table_provenance_rejects_non_string_warning():
    """Line 202: provenance warnings must be strings."""
    with pytest.raises(ValueError, match="warnings must be strings"):
        TableProvenanceV1(pipeline="x", warnings=(1,))  # type: ignore[arg-type]


def test_table_provenance_rejects_non_tuple_warnings():
    """Line 202: warnings must be a tuple."""
    with pytest.raises(ValueError, match="warnings must be strings"):
        TableProvenanceV1(pipeline="x", warnings=["a"])  # type: ignore[arg-type]


def test_table_provenance_from_payload_rejects_non_dict():
    """Line 215: provenance payload must be an object."""
    with pytest.raises(ValueError, match="payload must be an object"):
        TableProvenanceV1.from_payload("nope")  # type: ignore[arg-type]


def test_table_provenance_from_payload_rejects_invalid_field_types():
    """Line 224: provenance payload field types are validated."""
    payload = TableProvenanceV1(pipeline="x").to_payload()
    payload["pipeline"] = 123
    with pytest.raises(ValueError, match="invalid field types"):
        TableProvenanceV1.from_payload(payload)


def test_table_model_rejects_non_int_schema_version():
    """Line 247: schema_version must be an integer."""
    with pytest.raises(ValueError, match="schema_version must be an integer"):
        TableModelV1(  # type: ignore[arg-type]
            table_id="x",
            row_count=1,
            column_count=1,
            cells=(),
            schema_version=1.5,
        )


def test_table_model_rejects_unsupported_schema_version():
    """Line 249: schema_version must equal TABLE_SCHEMA_VERSION."""
    with pytest.raises(ValueError, match="unsupported table schema_version"):
        TableModelV1(
            table_id="x",
            row_count=1,
            column_count=1,
            cells=(),
            schema_version=99,
        )


def test_table_model_rejects_non_int_dimensions():
    """Line 256: row_count/column_count must be integers."""
    with pytest.raises(ValueError, match="row_count and column_count must be integers"):
        TableModelV1(  # type: ignore[arg-type]
            table_id="x",
            row_count=1.5,
            column_count=1,
            cells=(),
        )


def test_table_model_rejects_dimension_outside_limits():
    """Line 263: dimensions outside supported limits."""
    with pytest.raises(ValueError, match="dimensions are outside supported limits"):
        TableModelV1(
            table_id="x",
            row_count=-1,
            column_count=1,
            cells=(),
        )


def test_table_model_rejects_non_tuple_cells():
    """Line 267: cells must be a bounded tuple."""
    with pytest.raises(ValueError, match="cells must be a bounded tuple"):
        TableModelV1(  # type: ignore[arg-type]
            table_id="x",
            row_count=1,
            column_count=1,
            cells=[TableCellV1(cell_id="c", row=0, column=0)],
        )


def test_table_model_rejects_invalid_coordinate_space():
    """Line 269: coordinate_space must be a CoordinateSpace."""
    with pytest.raises(ValueError, match="coordinate_space"):
        TableModelV1(  # type: ignore[arg-type]
            table_id="x",
            row_count=1,
            column_count=1,
            cells=(),
            coordinate_space="pixel",
        )


def test_table_model_rejects_invalid_provenance():
    """Line 273: provenance must be TableProvenanceV1 when not None."""
    with pytest.raises(ValueError, match="provenance is invalid"):
        TableModelV1(  # type: ignore[arg-type]
            table_id="x",
            row_count=1,
            column_count=1,
            cells=(),
            provenance={"pipeline": "x"},
        )


def test_table_model_rejects_non_cell_entry():
    """Line 279: cells tuple must contain TableCellV1 values."""
    with pytest.raises(ValueError, match="TableCellV1 values"):
        TableModelV1(  # type: ignore[arg-type]
            table_id="x",
            row_count=1,
            column_count=1,
            cells=("not-a-cell",),
        )


def test_table_model_rejects_excessive_cell_coverage():
    """Line 296: total cell coverage exceeds the supported limit.

    The coverage guard fires during iteration *before* the overlap check, so
    we can stack many overlapping cells in one row to push the cumulative
    rowspan*colspan over MAX_TABLE_COVERAGE.
    """
    # Each cell spans 11 columns; ~91k cells gives coverage > 1_000_000.
    cells = tuple(
        TableCellV1(cell_id=f"c{i}", row=0, column=0, colspan=11)
        for i in range(91_000)
    )
    with pytest.raises(ValueError, match="coverage exceeds"):
        TableModelV1(
            table_id="coverage",
            row_count=1,
            column_count=100,
            cells=cells,
        )


def test_table_model_from_payload_rejects_non_dict():
    """Line 340: table payload must be an object."""
    with pytest.raises(ValueError, match="payload must be an object"):
        TableModelV1.from_payload(["nope"])  # type: ignore[arg-type]


def test_table_model_from_payload_rejects_dimension_outside_limits():
    """Line 359: dimensions outside limits in from_payload."""
    payload = _valid_payload()
    payload["row_count"] = -1
    with pytest.raises(ValueError, match="dimensions are outside supported limits"):
        TableModelV1.from_payload(payload)


def test_table_model_from_payload_rejects_excessive_grid_area():
    """Line 361: grid area exceeds the supported limit in from_payload."""
    payload = _valid_payload()
    payload["row_count"] = 10_000
    payload["column_count"] = 10_000
    payload["cells"] = []
    with pytest.raises(ValueError, match="grid area exceeds"):
        TableModelV1.from_payload(payload)


def test_table_model_from_payload_rejects_non_object_provenance():
    """Line 366: provenance must be an object or null in from_payload."""
    payload = _valid_payload()
    payload["provenance"] = ["not", "a", "dict"]
    with pytest.raises(ValueError, match="provenance payload must be an object or null"):
        TableModelV1.from_payload(payload)
