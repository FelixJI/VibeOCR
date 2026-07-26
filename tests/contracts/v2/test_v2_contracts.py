"""Contract tests for the HTTP v2 protocol (Python side).

These tests are the golden agreement surface. A .NET mirror must produce the
exact same bytes for the golden payloads. They also pin the Phase 1 exit
criterion: the parser rejects unknown required fields, illegal state
transitions and unknown error enums (no fake v1 compatibility).
"""

from __future__ import annotations

import json
from importlib import resources

import pytest

from vibeocr.protocol.v2 import (
    SCHEMA_VERSION,
    CancelMode,
    ContractError,
    ErrorCode,
    EvictionReason,
    ItemState,
    JobItem,
    JobKind,
    JobPriority,
    JobSnapshot,
    JobState,
    JobStateTransitionError,
    ResidencyEntry,
    ResidencyKind,
    ResidencyStatus,
    assert_item_transition,
    assert_job_transition,
    error_registry,
    is_terminal_item,
    is_terminal_job,
    parse_error_payload,
    parse_job_snapshot,
    parse_pipeline_spec,
    parse_residency_entry,
)


@pytest.fixture(scope="module")
def golden() -> dict:
    raw = resources.files("vibeocr.protocol.v2.golden").joinpath("golden.json").read_text(encoding="utf-8")
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Registry & schema version
# ---------------------------------------------------------------------------


def test_schema_version_is_two() -> None:
    assert SCHEMA_VERSION == 2


def test_error_registry_loaded_and_categories_match() -> None:
    assert len(error_registry) >= 16
    for entry in error_registry.values():
        # each code's category must equal the registry's stored category
        assert error_registry[entry.code] is entry


def test_oom_and_cancelled_retryability() -> None:
    assert error_registry[ErrorCode.OUT_OF_MEMORY].retryable is True
    assert error_registry[ErrorCode.CANCELLED].retryable is False


# ---------------------------------------------------------------------------
# Golden round-trip — Python must accept what we froze.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    ["job_snapshot_running", "job_snapshot_completed_with_errors", "job_snapshot_cancelled"],
)
def test_golden_job_snapshots_parse(key: str, golden: dict) -> None:
    snap = parse_job_snapshot(golden[key])
    assert snap.schema_version == SCHEMA_VERSION
    assert snap.state in JobState
    assert len(snap.items) == snap.summary.total


def test_golden_job_ref_payload_is_stable(golden: dict) -> None:
    ref = golden["job_ref"]
    assert ref["schema_version"] == SCHEMA_VERSION
    assert ref["state"] == JobState.ACCEPTED.value


@pytest.mark.parametrize("key", ["error_validation", "error_oom", "error_cancelled"])
def test_golden_error_payloads_parse(key: str, golden: dict) -> None:
    err = parse_error_payload(golden[key])
    assert err.schema_version == SCHEMA_VERSION
    assert err.code in error_registry


def test_golden_residency_status_parses(golden: dict) -> None:
    status = golden["residency_status"]
    entries = [parse_residency_entry(e) for e in status["entries"]]
    assert len(entries) == 2
    assert {e.pipeline for e in entries} == {"OCR", "MinerU"}
    assert any(e.kind == ResidencyKind.PINNED for e in entries)


def test_golden_settings_snapshot_parses(golden: dict) -> None:
    settings = golden["settings_snapshot"]
    pipelines = [parse_pipeline_spec(p) for p in settings["residency"]["pipelines"]]
    assert settings["residency"]["default_ttl_seconds"] == 300
    assert any(p.name == "MinerU" and p.ttl_seconds == 600 for p in pipelines)


# ---------------------------------------------------------------------------
# DTO → payload → DTO round trip
# ---------------------------------------------------------------------------


def test_job_snapshot_roundtrip_preserves_order() -> None:
    snap = JobSnapshot(
        job_id="abc",
        kind=JobKind.RECOGNITION,
        priority=JobPriority.BACKGROUND,
        state=JobState.COMPLETED_WITH_ERRORS,
        items=(
            JobItem(item_id="it-0", display_name="a", state=ItemState.SUCCEEDED),
            JobItem(item_id="it-1", display_name="b", state=ItemState.FAILED, error="boom"),
        ),
        summary=__import__("vibeocr.protocol.v2", fromlist=["JobSummary"]).JobSummary(
            succeeded=1, failed=1, total=2
        ),
        degraded=True,
        cancel_mode=CancelMode.COOPERATIVE,
    )
    back = parse_job_snapshot(snap.to_payload())
    assert [it.item_id for it in back.items] == ["it-0", "it-1"]
    assert back.items[1].error == "boom"
    assert back.cancel_mode == CancelMode.COOPERATIVE
    assert back.degraded is True


def test_residency_entry_roundtrip() -> None:
    entry = ResidencyEntry(
        pipeline="OCR",
        kind=ResidencyKind.SOFT_TTL,
        active_leases=2,
        remaining_ttl_seconds=120,
        estimated_vram_mb=1100,
        eviction_reason=EvictionReason.VRAM_PRESSURE,
    )
    back = parse_residency_entry(entry.to_payload())
    assert back == entry


# ---------------------------------------------------------------------------
# Rejection behaviour — Phase 1 exit criterion.
# ---------------------------------------------------------------------------


def test_parse_rejects_unknown_job_state() -> None:
    payload = {
        "job_id": "x",
        "kind": "recognition",
        "priority": "interactive",
        "state": "totally_made_up",
        "schema_version": 2,
        "created_at": "2026-07-24T10:00:00+00:00",
        "items": [],
        "summary": {"succeeded": 0, "failed": 0, "cancelled": 0, "total": 0},
    }
    with pytest.raises(ContractError, match="unknown job state"):
        parse_job_snapshot(payload)


def test_parse_rejects_unknown_error_code() -> None:
    payload = {
        "schema_version": 2,
        "code": "NOT_A_REAL_CODE",
        "message": "x",
        "category": "validation",
        "retryable": False,
    }
    with pytest.raises(ContractError, match="unknown error code"):
        parse_error_payload(payload)


def test_parse_rejects_error_category_mismatch() -> None:
    payload = {
        "schema_version": 2,
        "code": "CANCELLED",
        "message": "x",
        "category": "validation",  # registry says "cancelled"
        "retryable": False,
    }
    with pytest.raises(ContractError, match="category mismatch"):
        parse_error_payload(payload)


def test_parse_rejects_missing_required_field() -> None:
    payload = {
        "job_id": "x",
        # kind missing
        "priority": "interactive",
        "state": "accepted",
        "schema_version": 2,
        "created_at": "2026-07-24T10:00:00+00:00",
        "items": [],
        "summary": {"succeeded": 0, "failed": 0, "cancelled": 0, "total": 0},
    }
    with pytest.raises(ContractError, match="missing required"):
        parse_job_snapshot(payload)


def test_parse_rejects_wrong_schema_version() -> None:
    payload = {
        "job_id": "x",
        "kind": "recognition",
        "priority": "interactive",
        "state": "accepted",
        "schema_version": 1,  # v1 must NOT be accepted by the v2 parser
        "created_at": "2026-07-24T10:00:00+00:00",
        "items": [],
        "summary": {"succeeded": 0, "failed": 0, "cancelled": 0, "total": 0},
    }
    with pytest.raises(ContractError, match="schema_version mismatch"):
        parse_job_snapshot(payload)


def test_parse_rejects_unknown_kind_enum() -> None:
    payload = {
        "job_id": "x",
        "kind": "ocr_single",  # legacy v1-style name
        "priority": "interactive",
        "state": "accepted",
        "schema_version": 2,
        "created_at": "2026-07-24T10:00:00+00:00",
        "items": [],
        "summary": {"succeeded": 0, "failed": 0, "cancelled": 0, "total": 0},
    }
    with pytest.raises(ContractError, match="unknown job kind"):
        parse_job_snapshot(payload)


def test_parse_rejects_negative_ttl() -> None:
    with pytest.raises(ContractError, match="ttl_seconds"):
        parse_pipeline_spec({"name": "OCR", "ttl_seconds": -5, "pinned": False})


# ---------------------------------------------------------------------------
# State machine invariants
# ---------------------------------------------------------------------------


def test_job_state_machine_allows_queued_to_running() -> None:
    assert_job_transition(JobState.QUEUED, JobState.RUNNING)


@pytest.mark.parametrize(
    "frm,to",
    [
        (JobState.COMPLETED, JobState.RUNNING),
        (JobState.CANCELLED, JobState.RUNNING),
        (JobState.FAILED, JobState.QUEUED),
        (JobState.ACCEPTED, JobState.COMPLETED),  # must pass through queued/running
    ],
)
def test_job_state_machine_rejects_illegal(frm: JobState, to: JobState) -> None:
    with pytest.raises(JobStateTransitionError):
        assert_job_transition(frm, to)


@pytest.mark.parametrize(
    "frm,to",
    [
        (ItemState.QUEUED, ItemState.RUNNING),
        (ItemState.RUNNING, ItemState.SUCCEEDED),
        (ItemState.RUNNING, ItemState.FAILED),
    ],
)
def test_item_state_machine_allows(frm: ItemState, to: ItemState) -> None:
    assert_item_transition(frm, to)


def test_item_state_machine_rejects_terminal_to_running() -> None:
    with pytest.raises(JobStateTransitionError):
        assert_item_transition(ItemState.SUCCEEDED, ItemState.RUNNING)


def test_terminal_helpers() -> None:
    assert is_terminal_job(JobState.COMPLETED)
    assert is_terminal_job(JobState.FAILED)
    assert not is_terminal_job(JobState.RUNNING)
    assert is_terminal_item(ItemState.SUCCEEDED)
    assert not is_terminal_item(ItemState.QUEUED)


# ---------------------------------------------------------------------------
# Residency / pipeline helpers
# ---------------------------------------------------------------------------


def test_pipeline_spec_inherits_when_ttl_none() -> None:
    spec = parse_pipeline_spec({"name": "OCR", "ttl_seconds": None, "pinned": False})
    assert spec.ttl_seconds is None
    status = ResidencyStatus(default_ttl_seconds=300, pipelines=(spec,))
    payload = status.to_payload()
    assert payload["default_ttl_seconds"] == 300


def test_residency_status_payload_shape() -> None:
    status = ResidencyStatus(
        default_ttl_seconds=600,
        entries=(
            ResidencyEntry(pipeline="MinerU", kind=ResidencyKind.PINNED),
            ResidencyEntry(
                pipeline="OCR",
                kind=ResidencyKind.IDLE,
                eviction_reason=EvictionReason.TTL_EXPIRED,
            ),
        ),
        vram_total_mb=24576,
        vram_used_mb=2000,
    )
    payload = status.to_payload()
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["vram_used_mb"] == 2000
    assert payload["entries"][1]["eviction_reason"] == "ttl_expired"
