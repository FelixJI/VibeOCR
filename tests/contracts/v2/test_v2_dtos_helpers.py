"""v2 DTO helper / to_payload 边界测试。

补充 ``ItemOutcome``/``SettingsSnapshot``/``UnknownJobError`` 的 to_payload、
``new_job_id`` 唯一性、``_to_iso`` 时区分支、以及 ``entry_for``/``ErrorPayload``
序列化等未被既有契约测试直接驱动的边界。
"""

from __future__ import annotations

from datetime import UTC, datetime

from vibeocr.runtime_contracts import (
    ErrorCode,
    ErrorPayload,
    ItemOutcome,
    ItemState,
    PipelineSpec,
    ResultEntry,
    SettingsSnapshot,
    UnknownJobError,
    new_job_id,
)
from vibeocr.runtime_contracts.dtos import _to_iso
from vibeocr.runtime_contracts.errors import ErrorCategories, entry_for


def test_to_iso_none_returns_none() -> None:
    assert _to_iso(None) is None


def test_to_iso_string_passthrough() -> None:
    assert _to_iso("2024-01-01T00:00:00Z") == "2024-01-01T00:00:00Z"


def test_to_iso_naive_datetime_gets_utc() -> None:
    """无时区信息的 datetime 被赋予 UTC 后再 isoformat。"""
    naive = datetime(2024, 1, 1, 12, 0, 0)  # noqa: DTZ001 - intentionally naive; the SUT attaches UTC
    result = _to_iso(naive)
    assert result is not None
    assert result.endswith("+00:00")
    assert "2024-01-01T12:00:00" in result


def test_to_iso_aware_datetime_preserved() -> None:
    """带时区信息的 datetime 直接 isoformat。"""
    aware = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    result = _to_iso(aware)
    assert result == aware.isoformat()


def test_item_outcome_to_payload_roundtrip() -> None:
    """ItemOutcome.to_payload 含完整字段并默认 error_detail 为空 dict。"""
    outcome = ItemOutcome(
        item_id="i1",
        state=ItemState.SUCCEEDED,
        attempt=1,
        payload_type="ocr.v1",
        payload={"text": "hi"},
    )
    payload = outcome.to_payload()
    assert payload["item_id"] == "i1"
    assert payload["state"] == "succeeded"
    assert payload["attempt"] == 1
    assert payload["payload_type"] == "ocr.v1"
    assert payload["payload"] == {"text": "hi"}
    assert payload["error_code"] is None
    assert payload["error_detail"] == {}


def test_settings_snapshot_to_payload_defaults() -> None:
    """SettingsSnapshot.to_payload 默认结构与 schema 一致。"""
    snap = SettingsSnapshot()
    payload = snap.to_payload()
    assert payload["schema_version"] == SettingsSnapshot().schema_version
    assert payload["residency"]["default_ttl_seconds"] == 300
    assert payload["residency"]["pipelines"] == []
    assert payload["extra"] == {}


def test_settings_snapshot_to_payload_with_pipeline() -> None:
    """含 PipelineSpec 时 to_payload 展开为列表。"""
    spec = PipelineSpec(name="OCR", ttl_seconds=120)
    snap = SettingsSnapshot(default_ttl_seconds=60, pipelines=(spec,), extra={"k": "v"})
    payload = snap.to_payload()
    assert payload["residency"]["default_ttl_seconds"] == 60
    assert len(payload["residency"]["pipelines"]) == 1
    assert payload["extra"] == {"k": "v"}


def test_new_job_id_is_unique_uuid4_string() -> None:
    """new_job_id 返回 36 字符 UUID 字符串且每次不同。"""
    a = new_job_id()
    b = new_job_id()
    assert len(a) == 36
    assert a != b
    # UUID4 格式：xxxxxxxx-xxxx-4xxx-xxxx-xxxxxxxxxxxx
    assert a[14] == "4"


def test_unknown_job_error_to_payload() -> None:
    """UnknownJobError.to_payload 标记 unknown=True。"""
    err = UnknownJobError(job_id="job-123")
    assert err.to_payload() == {"job_id": "job-123", "unknown": True}


def test_result_entry_to_payload_defaults() -> None:
    """ResultEntry.to_payload 默认 payload 为空 dict、error_code 为 None。"""
    entry = ResultEntry(item_id="i1", display_name="page1.png")
    payload = entry.to_payload()
    assert payload == {
        "item_id": "i1",
        "display_name": "page1.png",
        "payload": {},
        "error_code": None,
    }


def test_result_entry_to_payload_with_error() -> None:
    """带 error_code 与 payload 时 to_payload 原样保留。"""
    entry = ResultEntry(
        item_id="i2",
        display_name="page2.png",
        payload={"text": "x"},
        error_code="OUT_OF_MEMORY",
    )
    payload = entry.to_payload()
    assert payload["payload"] == {"text": "x"}
    assert payload["error_code"] == "OUT_OF_MEMORY"


def test_entry_for_accepts_enum_and_string() -> None:
    """entry_for 同时接受 ErrorCode 枚举与字符串。"""
    by_enum = entry_for(ErrorCode.OUT_OF_MEMORY)
    by_str = entry_for("OUT_OF_MEMORY")
    assert by_enum is by_str
    assert by_enum.retryable is True
    assert by_enum.category is ErrorCategories.OOM


def test_error_payload_to_payload_roundtrip() -> None:
    """ErrorPayload.to_payload 序列化所有字段，detail 默认空 dict。"""
    err = ErrorPayload(
        schema_version=2,
        instance_id="inst-1",
        code=ErrorCode.OUT_OF_MEMORY,
        message="boom",
        category=ErrorCategories.OOM,
        retryable=True,
    )
    payload = err.to_payload()
    assert payload["schema_version"] == 2
    assert payload["instance_id"] == "inst-1"
    assert payload["code"] == "OUT_OF_MEMORY"
    assert payload["message"] == "boom"
    assert payload["category"] == "oom"
    assert payload["retryable"] is True
    assert payload["detail"] == {}
    assert payload["job_id"] is None


def test_error_payload_to_payload_with_detail_and_job() -> None:
    """带 detail 与 job_id 时 to_payload 原样保留。"""
    err = ErrorPayload(
        schema_version=2,
        instance_id=None,
        code=ErrorCode.VALIDATION_ERROR,
        message="bad",
        category=ErrorCategories.VALIDATION,
        retryable=False,
        detail={"k": "v"},
        job_id="job-7",
    )
    payload = err.to_payload()
    assert payload["detail"] == {"k": "v"}
    assert payload["job_id"] == "job-7"
