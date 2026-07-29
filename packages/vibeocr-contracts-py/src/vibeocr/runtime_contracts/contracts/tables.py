"""Versioned, UI/backend-neutral table semantics."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise
from typing import Any

TABLE_SCHEMA_VERSION = 1
MAX_TABLE_DIMENSION = 10_000
MAX_TABLE_CELLS = 100_000
MAX_TABLE_COVERAGE = 1_000_000
MAX_TABLE_GRID_AREA = 1_000_000

_CELL_KEYS = frozenset(
    {
        "cell_id",
        "row",
        "column",
        "rowspan",
        "colspan",
        "text",
        "is_header",
        "bbox",
        "confidence",
        "source_refs",
    }
)
_PROVENANCE_KEYS = frozenset(
    {"pipeline", "provider_schema", "provider_version", "warnings"}
)
_TABLE_KEYS = frozenset(
    {
        "schema_version",
        "table_id",
        "row_count",
        "column_count",
        "coordinate_space",
        "cells",
        "provenance",
    }
)


def _require_exact_keys(payload: dict[str, Any], expected: frozenset[str]) -> None:
    actual = frozenset(payload)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(
            f"invalid table payload keys: missing={missing}, extra={extra}"
        )


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


class CoordinateSpace(StrEnum):
    """Coordinate system used by optional table and cell bounding boxes."""

    PIXEL = "pixel"
    NORMALIZED_1 = "normalized_1"
    NORMALIZED_1000 = "normalized_1000"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class TableCellV1:
    """One anchor cell in a logical table grid."""

    cell_id: str
    row: int
    column: int
    text: str = ""
    rowspan: int = 1
    colspan: int = 1
    is_header: bool = False
    bbox: tuple[float, float, float, float] | None = None
    confidence: float | None = None
    source_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.cell_id, str) or not self.cell_id:
            raise ValueError("table cell_id must be a non-empty string")
        if not _is_int(self.row) or not _is_int(self.column):
            raise ValueError("table cell row and column must be integers")
        if not _is_int(self.rowspan) or not _is_int(self.colspan):
            raise ValueError("table cell spans must be integers")
        if not isinstance(self.text, str) or not isinstance(self.is_header, bool):
            raise ValueError("table cell text/header types are invalid")
        if self.bbox is not None and (
            not isinstance(self.bbox, tuple)
            or len(self.bbox) != 4
            or not all(_is_number(value) for value in self.bbox)
        ):
            raise ValueError("table cell bbox must contain four finite numbers")
        if self.confidence is not None and not _is_number(self.confidence):
            raise ValueError("table cell confidence must be a finite number")
        if not isinstance(self.source_refs, tuple) or not all(
            isinstance(ref, str) for ref in self.source_refs
        ):
            raise ValueError("table cell source_refs must be strings")

    def to_payload(self) -> dict[str, Any]:
        return {
            "cell_id": self.cell_id,
            "row": self.row,
            "column": self.column,
            "rowspan": self.rowspan,
            "colspan": self.colspan,
            "text": self.text,
            "is_header": self.is_header,
            "bbox": list(self.bbox) if self.bbox is not None else None,
            "confidence": self.confidence,
            "source_refs": list(self.source_refs),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> TableCellV1:
        if not isinstance(payload, dict):
            raise ValueError("table cell payload must be an object")
        _require_exact_keys(payload, _CELL_KEYS)
        if (
            not isinstance(payload["cell_id"], str)
            or not _is_int(payload["row"])
            or not _is_int(payload["column"])
            or not _is_int(payload["rowspan"])
            or not _is_int(payload["colspan"])
            or not isinstance(payload["text"], str)
            or not isinstance(payload["is_header"], bool)
            or not isinstance(payload["source_refs"], list)
            or not all(isinstance(ref, str) for ref in payload["source_refs"])
        ):
            raise ValueError("table cell payload has invalid field types")
        bbox = payload["bbox"]
        if bbox is not None and (
            not isinstance(bbox, list)
            or len(bbox) != 4
            or not all(_is_number(value) for value in bbox)
        ):
            raise ValueError("table cell bbox must contain four finite numbers")
        confidence = payload["confidence"]
        if confidence is not None and not _is_number(confidence):
            raise ValueError("table cell confidence must be a finite number")
        parsed_bbox = (
            (
                float(bbox[0]),
                float(bbox[1]),
                float(bbox[2]),
                float(bbox[3]),
            )
            if bbox is not None
            else None
        )
        return cls(
            cell_id=payload["cell_id"],
            row=payload["row"],
            column=payload["column"],
            rowspan=payload["rowspan"],
            colspan=payload["colspan"],
            text=payload["text"],
            is_header=payload["is_header"],
            bbox=parsed_bbox,
            confidence=(float(confidence) if confidence is not None else None),
            source_refs=tuple(payload["source_refs"]),
        )


@dataclass(frozen=True, slots=True)
class TableProvenanceV1:
    """Provider details and non-fatal adaptation diagnostics."""

    pipeline: str
    provider_schema: str = ""
    provider_version: str = ""
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str)
            for value in (
                self.pipeline,
                self.provider_schema,
                self.provider_version,
            )
        ):
            raise ValueError("table provenance fields must be strings")
        if not isinstance(self.warnings, tuple) or not all(
            isinstance(value, str) for value in self.warnings
        ):
            raise ValueError("table provenance warnings must be strings")

    def to_payload(self) -> dict[str, Any]:
        return {
            "pipeline": self.pipeline,
            "provider_schema": self.provider_schema,
            "provider_version": self.provider_version,
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> TableProvenanceV1:
        if not isinstance(payload, dict):
            raise ValueError("table provenance payload must be an object")
        _require_exact_keys(payload, _PROVENANCE_KEYS)
        if (
            not isinstance(payload["pipeline"], str)
            or not isinstance(payload["provider_schema"], str)
            or not isinstance(payload["provider_version"], str)
            or not isinstance(payload["warnings"], list)
            or not all(isinstance(value, str) for value in payload["warnings"])
        ):
            raise ValueError("table provenance payload has invalid field types")
        return cls(
            pipeline=payload["pipeline"],
            provider_schema=payload["provider_schema"],
            provider_version=payload["provider_version"],
            warnings=tuple(payload["warnings"]),
        )


@dataclass(frozen=True, slots=True)
class TableModelV1:
    """Canonical table topology; derived formats are projections of this model."""

    table_id: str
    row_count: int
    column_count: int
    cells: tuple[TableCellV1, ...]
    coordinate_space: CoordinateSpace = CoordinateSpace.UNKNOWN
    provenance: TableProvenanceV1 | None = None
    schema_version: int = TABLE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not _is_int(self.schema_version):
            raise ValueError("table schema_version must be an integer")
        if self.schema_version != TABLE_SCHEMA_VERSION:
            raise ValueError(
                "unsupported table schema_version: "
                f"{self.schema_version}; expected {TABLE_SCHEMA_VERSION}"
            )
        if not isinstance(self.table_id, str) or not self.table_id:
            raise ValueError("table_id must be a non-empty string")
        if not _is_int(self.row_count) or not _is_int(self.column_count):
            raise ValueError("table row_count and column_count must be integers")
        if (
            self.row_count < 0
            or self.column_count < 0
            or self.row_count > MAX_TABLE_DIMENSION
            or self.column_count > MAX_TABLE_DIMENSION
        ):
            raise ValueError("table dimensions are outside supported limits")
        if self.row_count * self.column_count > MAX_TABLE_GRID_AREA:
            raise ValueError("table grid area exceeds supported limit")
        if not isinstance(self.cells, tuple) or len(self.cells) > MAX_TABLE_CELLS:
            raise ValueError("table cells must be a bounded tuple")
        if not isinstance(self.coordinate_space, CoordinateSpace):
            raise ValueError("table coordinate_space is invalid")
        if self.provenance is not None and not isinstance(
            self.provenance, TableProvenanceV1
        ):
            raise ValueError("table provenance is invalid")
        seen_ids: set[str] = set()
        coverage = 0
        occupied: dict[int, list[tuple[int, int, str]]] = {}
        for cell in self.cells:
            if not isinstance(cell, TableCellV1):
                raise ValueError("table cells must contain TableCellV1 values")
            if cell.cell_id in seen_ids:
                raise ValueError(f"duplicate table cell_id: {cell.cell_id!r}")
            seen_ids.add(cell.cell_id)
            if (
                cell.row < 0
                or cell.column < 0
                or cell.rowspan < 1
                or cell.colspan < 1
                or cell.row + cell.rowspan > self.row_count
                or cell.column + cell.colspan > self.column_count
            ):
                raise ValueError(
                    f"table cell {cell.cell_id!r} is outside the declared grid"
                )
            coverage += cell.rowspan * cell.colspan
            if coverage > MAX_TABLE_COVERAGE:
                raise ValueError("table cell coverage exceeds supported limit")
            for row in range(cell.row, cell.row + cell.rowspan):
                start = cell.column
                end = cell.column + cell.colspan
                occupied.setdefault(row, []).append((start, end, cell.cell_id))
        for row, intervals in occupied.items():
            intervals.sort(key=lambda interval: (interval[0], interval[1]))
            for previous, current in pairwise(intervals):
                if current[0] < previous[1]:
                    raise ValueError(
                        "table cell coverage overlap on row "
                        f"{row}: {previous[2]!r} and {current[2]!r}"
                    )

    def merged_ranges(self) -> tuple[tuple[int, int, int, int], ...]:
        """Return zero-based inclusive ranges for cells that span rows or columns."""

        return tuple(
            (
                cell.row,
                cell.column,
                cell.row + cell.rowspan - 1,
                cell.column + cell.colspan - 1,
            )
            for cell in self.cells
            if cell.rowspan > 1 or cell.colspan > 1
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "table_id": self.table_id,
            "row_count": self.row_count,
            "column_count": self.column_count,
            "coordinate_space": self.coordinate_space.value,
            "cells": [cell.to_payload() for cell in self.cells],
            "provenance": (
                self.provenance.to_payload() if self.provenance is not None else None
            ),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> TableModelV1:
        if not isinstance(payload, dict):
            raise ValueError("table payload must be an object")
        _require_exact_keys(payload, _TABLE_KEYS)
        if (
            not _is_int(payload["schema_version"])
            or not isinstance(payload["table_id"], str)
            or not _is_int(payload["row_count"])
            or not _is_int(payload["column_count"])
            or not isinstance(payload["coordinate_space"], str)
            or not isinstance(payload["cells"], list)
        ):
            raise ValueError("table payload has invalid field types")
        row_count = payload["row_count"]
        column_count = payload["column_count"]
        if (
            row_count < 0
            or column_count < 0
            or row_count > MAX_TABLE_DIMENSION
            or column_count > MAX_TABLE_DIMENSION
        ):
            raise ValueError("table dimensions are outside supported limits")
        if row_count * column_count > MAX_TABLE_GRID_AREA:
            raise ValueError("table grid area exceeds supported limit")
        if len(payload["cells"]) > MAX_TABLE_CELLS:
            raise ValueError("table cells exceed supported limit")
        provenance = payload["provenance"]
        if provenance is not None and not isinstance(provenance, dict):
            raise ValueError("table provenance payload must be an object or null")
        return cls(
            schema_version=payload["schema_version"],
            table_id=payload["table_id"],
            row_count=row_count,
            column_count=column_count,
            coordinate_space=CoordinateSpace(payload["coordinate_space"]),
            cells=tuple(TableCellV1.from_payload(cell) for cell in payload["cells"]),
            provenance=(
                TableProvenanceV1.from_payload(provenance)
                if provenance is not None
                else None
            ),
        )


__all__ = [
    "MAX_TABLE_CELLS",
    "MAX_TABLE_COVERAGE",
    "MAX_TABLE_DIMENSION",
    "MAX_TABLE_GRID_AREA",
    "TABLE_SCHEMA_VERSION",
    "CoordinateSpace",
    "TableCellV1",
    "TableModelV1",
    "TableProvenanceV1",
]
