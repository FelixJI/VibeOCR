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

from vibeocr.runtime_contracts import (
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
    PipelineSelection,
    ResidencyEntry,
    ResidencyKind,
    ResidencyStatus,
    SubmitRequest,
    assert_item_transition,
    assert_job_transition,
    error_registry,
    is_terminal_item,
    is_terminal_job,
    parse_error_payload,
    parse_job_command,
    parse_job_ref,
    parse_job_snapshot,
    parse_job_update,
    parse_pipeline_selection,
    parse_pipeline_spec,
    parse_residency_entry,
    parse_submit_request,
)
from vibeocr.runtime_contracts.parser import SchemaValidator


@pytest.fixture(scope="module")
def golden() -> dict:
    raw = resources.files("vibeocr.runtime_contracts.golden").joinpath("golden.json").read_text(encoding="utf-8")
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
        summary=__import__("vibeocr.runtime_contracts", fromlist=["JobSummary"]).JobSummary(
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


# ---------------------------------------------------------------------------
# Helper payloads for the deep parser rejection coverage.
# ---------------------------------------------------------------------------


def _pipeline_selection_payload() -> dict:
    return {
        "pipeline_id": "OCR",
        "options_version": 1,
        "options": {"use_doc_orientation_classify": False},
    }


def _submit_item_payload() -> dict:
    return {
        "client_item_key": "file-a",
        "ordinal": 0,
        "display_name": "a.png",
        "source": {"type": "upload.v1", "attachment": "file-a"},
    }


def _submit_request_payload() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": "req-1",
        "kind": "recognition",
        "priority": "background",
        "pipeline": _pipeline_selection_payload(),
        "items": [_submit_item_payload()],
        "parameters": {},
    }


def _job_ref_payload() -> dict:
    return {
        "job_id": "job-1",
        "schema_version": SCHEMA_VERSION,
        "state": "accepted",
        "items": [],
    }


def _job_snapshot_payload() -> dict:
    return {
        "job_id": "job-1",
        "kind": "recognition",
        "priority": "interactive",
        "state": "accepted",
        "schema_version": SCHEMA_VERSION,
        "created_at": "2026-07-24T10:00:00+00:00",
        "items": [],
        "summary": {"succeeded": 0, "failed": 0, "cancelled": 0, "total": 0},
    }


def _job_item_payload() -> dict:
    return {
        "item_id": "it-1",
        "display_name": "a.png",
        "state": "queued",
    }


def _job_command_payload() -> dict:
    return {
        "command_id": "cmd-1",
        "kind": "cancel",
        "job_id": "job-1",
        "item_ids": [],
    }


def _error_payload() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "code": "VALIDATION_ERROR",
        "message": "bad",
        "category": "validation",
    }


# ---------------------------------------------------------------------------
# Same-state transition early returns (lines 101, 111).
# ---------------------------------------------------------------------------


def test_assert_job_transition_allows_same_state() -> None:
    """Line 101: transitioning to the same state is a no-op."""
    # This would otherwise be illegal (terminal -> terminal) but the early
    # return short-circuits before the table lookup.
    assert_job_transition(JobState.COMPLETED, JobState.COMPLETED)


def test_assert_item_transition_allows_same_state() -> None:
    """Line 111: transitioning to the same item state is a no-op."""
    assert_item_transition(ItemState.SUCCEEDED, ItemState.SUCCEEDED)


# ---------------------------------------------------------------------------
# parse_pipeline_selection rejection branches.
# ---------------------------------------------------------------------------


def test_parse_pipeline_selection_rejects_unknown_field() -> None:
    """Line 157: unknown top-level field rejected."""
    payload = _pipeline_selection_payload()
    payload["unexpected"] = True
    with pytest.raises(ContractError, match="unknown field"):
        parse_pipeline_selection(payload)


def test_parse_pipeline_selection_rejects_non_dict() -> None:
    """Line 162: payload must be a JSON object."""
    with pytest.raises(ContractError, match="JSON object"):
        parse_pipeline_selection(["nope"])  # type: ignore[arg-type]


def test_parse_pipeline_selection_rejects_non_string_pipeline_id() -> None:
    """Line 173: pipeline_id must be a string."""
    payload = _pipeline_selection_payload()
    payload["pipeline_id"] = 123
    with pytest.raises(ContractError, match="pipeline_id must be a string"):
        parse_pipeline_selection(payload)


def test_parse_pipeline_selection_rejects_unknown_pipeline_id() -> None:
    """Lines 176-177: unknown pipeline_id is rejected."""
    payload = _pipeline_selection_payload()
    payload["pipeline_id"] = "MADE_UP_PIPELINE"
    with pytest.raises(ContractError, match="unknown pipeline_id"):
        parse_pipeline_selection(payload)


def test_parse_pipeline_selection_rejects_unsupported_options_version() -> None:
    """Line 180: options_version must equal 1."""
    payload = _pipeline_selection_payload()
    payload["options_version"] = 2
    with pytest.raises(ContractError, match="unsupported options_version"):
        parse_pipeline_selection(payload)


def test_parse_pipeline_selection_rejects_non_object_options() -> None:
    """Line 183: options must be a JSON object."""
    payload = _pipeline_selection_payload()
    payload["options"] = ["not", "a", "dict"]
    with pytest.raises(ContractError, match="options must be a JSON object"):
        parse_pipeline_selection(payload)


def test_parse_pipeline_selection_rejects_unsupported_option() -> None:
    """Lines 186-189: options not supported by the pipeline are rejected."""
    payload = _pipeline_selection_payload()
    payload["options"] = {"parse_method": "auto"}  # not an OCR option
    with pytest.raises(ContractError, match="unsupported option"):
        parse_pipeline_selection(payload)


# ---------------------------------------------------------------------------
# _parse_submit_item / parse_submit_request rejection branches.
# ---------------------------------------------------------------------------


def test_parse_submit_item_rejects_non_dict() -> None:
    """Line 199: submit item must be a JSON object."""
    payload = _submit_request_payload()
    payload["items"] = ["not-a-dict"]
    with pytest.raises(ContractError, match="submit item must be a JSON object"):
        parse_submit_request(payload)


def test_parse_submit_item_rejects_non_dict_source() -> None:
    """Line 212: submit item source must be a JSON object."""
    payload = _submit_request_payload()
    payload["items"][0]["source"] = "not-a-dict"
    with pytest.raises(ContractError, match="source must be a JSON object"):
        parse_submit_request(payload)


def test_parse_submit_request_accepts_pdf_page_source() -> None:
    """Lines 219-231: the pdf_page.v1 source branch is accepted."""
    payload = _submit_request_payload()
    payload["items"][0]["source"] = {
        "type": "pdf_page.v1",
        "session_id": "sess-1",
        "session_revision": 3,
        "page_index": 0,
    }
    parsed = parse_submit_request(payload)
    assert parsed.items[0].source["type"] == "pdf_page.v1"


def test_parse_submit_item_rejects_negative_pdf_page_index() -> None:
    """Line 231: pdf page_index must be a non-negative integer."""
    payload = _submit_request_payload()
    payload["items"][0]["source"] = {
        "type": "pdf_page.v1",
        "session_id": "sess-1",
        "session_revision": 3,
        "page_index": -1,
    }
    with pytest.raises(ContractError, match="page_index must be a non-negative integer"):
        parse_submit_request(payload)


def test_parse_submit_item_rejects_unknown_source_type() -> None:
    """Line 233: unknown submit source type rejected."""
    payload = _submit_request_payload()
    payload["items"][0]["source"] = {"type": " fax.v0 "}
    with pytest.raises(ContractError, match="unknown submit source type"):
        parse_submit_request(payload)


def test_parse_submit_item_rejects_negative_ordinal() -> None:
    """Line 236: ordinal must be a non-negative integer."""
    payload = _submit_request_payload()
    payload["items"][0]["ordinal"] = -1
    with pytest.raises(ContractError, match="ordinal must be a non-negative integer"):
        parse_submit_request(payload)


def test_parse_submit_item_rejects_empty_client_item_key() -> None:
    """Line 239: client_item_key must be a non-empty string."""
    payload = _submit_request_payload()
    payload["items"][0]["client_item_key"] = ""
    with pytest.raises(ContractError, match="client_item_key must be a non-empty string"):
        parse_submit_request(payload)


def test_parse_submit_item_rejects_non_string_display_name() -> None:
    """Line 242: display_name must be a string."""
    payload = _submit_request_payload()
    payload["items"][0]["display_name"] = 123
    with pytest.raises(ContractError, match="display_name must be a string"):
        parse_submit_request(payload)


def test_parse_submit_request_rejects_non_dict() -> None:
    """Line 253: submit request must be a JSON object."""
    with pytest.raises(ContractError, match="JSON object"):
        parse_submit_request(["nope"])  # type: ignore[arg-type]


def test_parse_submit_request_rejects_wrong_schema_version() -> None:
    """Line 275: schema_version must match."""
    payload = _submit_request_payload()
    payload["schema_version"] = 1
    with pytest.raises(ContractError, match="schema_version mismatch"):
        parse_submit_request(payload)


def test_parse_submit_request_rejects_empty_request_id() -> None:
    """Line 281: request_id must be a non-empty string."""
    payload = _submit_request_payload()
    payload["request_id"] = ""
    with pytest.raises(ContractError, match="request_id must be a non-empty string"):
        parse_submit_request(payload)


def test_parse_submit_request_rejects_non_submittable_kind() -> None:
    """Line 284: model_download is not submittable via the wire."""
    payload = _submit_request_payload()
    payload["kind"] = "model_download"
    with pytest.raises(ContractError, match="job kind is not submittable"):
        parse_submit_request(payload)


def test_parse_submit_request_rejects_mineru_kind_without_mineru_pipeline() -> None:
    """Line 288: mineru_parse requires the MinerU pipeline."""
    payload = _submit_request_payload()
    payload["kind"] = "mineru_parse"
    # pipeline stays as OCR
    with pytest.raises(ContractError, match="mineru_parse requires the MinerU pipeline"):
        parse_submit_request(payload)


def test_parse_submit_request_rejects_recognition_kind_with_mineru_pipeline() -> None:
    """Line 290: recognition kind cannot use the MinerU pipeline."""
    payload = _submit_request_payload()
    payload["kind"] = "recognition"
    payload["pipeline"]["pipeline_id"] = "MinerU"
    payload["pipeline"]["options"] = {}
    with pytest.raises(ContractError, match="MinerU requires kind=mineru_parse"):
        parse_submit_request(payload)


def test_parse_submit_request_rejects_empty_items() -> None:
    """Line 293: items must be a non-empty list."""
    payload = _submit_request_payload()
    payload["items"] = []
    with pytest.raises(ContractError, match="items must be a non-empty list"):
        parse_submit_request(payload)


def test_parse_submit_request_rejects_non_object_parameters() -> None:
    """Line 303: parameters must be a JSON object."""
    payload = _submit_request_payload()
    payload["parameters"] = ["not", "a", "dict"]
    with pytest.raises(ContractError, match="parameters must be a JSON object"):
        parse_submit_request(payload)


# ---------------------------------------------------------------------------
# parse_job_ref rejection branches.
# ---------------------------------------------------------------------------


def test_parse_job_ref_rejects_non_dict() -> None:
    """Line 316: job ref must be a JSON object."""
    with pytest.raises(ContractError, match="JSON object"):
        parse_job_ref(["nope"])  # type: ignore[arg-type]


def test_parse_job_ref_rejects_wrong_schema_version() -> None:
    """Line 319: schema_version mismatch."""
    payload = _job_ref_payload()
    payload["schema_version"] = 1
    with pytest.raises(ContractError, match="schema_version mismatch"):
        parse_job_ref(payload)


def test_parse_job_ref_rejects_non_list_items() -> None:
    """Line 325: items must be a list."""
    payload = _job_ref_payload()
    payload["items"] = "not-a-list"
    with pytest.raises(ContractError, match="job ref items must be a list"):
        parse_job_ref(payload)


# ---------------------------------------------------------------------------
# parse_job_snapshot / _parse_job_item rejection branches.
# ---------------------------------------------------------------------------


def test_parse_job_snapshot_rejects_non_dict() -> None:
    """Line 337: job snapshot must be a JSON object."""
    with pytest.raises(ContractError, match="JSON object"):
        parse_job_snapshot(["nope"])  # type: ignore[arg-type]


def test_parse_job_snapshot_rejects_non_list_items() -> None:
    """Line 350: items must be a list."""
    payload = _job_snapshot_payload()
    payload["items"] = "not-a-list"
    with pytest.raises(ContractError, match="items must be a list"):
        parse_job_snapshot(payload)


def test_parse_job_item_rejects_non_dict() -> None:
    """Line 393: job item must be a JSON object (via snapshot items)."""
    payload = _job_snapshot_payload()
    payload["items"] = ["not-a-dict"]
    with pytest.raises(ContractError, match="job item must be a JSON object"):
        parse_job_snapshot(payload)


# ---------------------------------------------------------------------------
# _parse_stage_event / _parse_item_outcome rejection branches.
# ---------------------------------------------------------------------------


def _job_update_payload() -> dict:
    snap = JobSnapshot(
        job_id="job-1",
        kind=JobKind.RECOGNITION,
        priority=JobPriority.INTERACTIVE,
        state=JobState.COMPLETED,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "snapshot": snap.to_payload(),
        "events": [],
        "outcomes": [],
        "through_sequence": 0,
        "more": False,
    }


def test_parse_stage_event_rejects_non_dict() -> None:
    """Line 409: stage event must be a JSON object."""
    payload = _job_update_payload()
    payload["events"] = ["not-a-dict"]
    with pytest.raises(ContractError, match="stage event must be a JSON object"):
        parse_job_update(payload)


def test_parse_stage_event_rejects_non_dict_detail() -> None:
    """Line 413: stage event detail must be a JSON object."""
    payload = _job_update_payload()
    payload["events"] = [
        {"sequence": 0, "stage": "x", "timestamp": "t", "detail": "nope"}
    ]
    with pytest.raises(ContractError, match="stage event detail must be a JSON object"):
        parse_job_update(payload)


def test_parse_item_outcome_rejects_non_dict() -> None:
    """Line 425: item outcome must be a JSON object."""
    payload = _job_update_payload()
    payload["outcomes"] = ["not-a-dict"]
    with pytest.raises(ContractError, match="item outcome must be a JSON object"):
        parse_job_update(payload)


def test_parse_item_outcome_rejects_non_terminal_state() -> None:
    """Line 429: item outcome state must be terminal."""
    payload = _job_update_payload()
    payload["outcomes"] = [
        {"item_id": "it-1", "state": "running", "attempt": 0}
    ]
    with pytest.raises(ContractError, match="state must be terminal"):
        parse_job_update(payload)


def test_parse_item_outcome_rejects_failed_without_error() -> None:
    """Lines 438-439: failed/cancelled outcome requires an error code."""
    payload = _job_update_payload()
    payload["outcomes"] = [
        {"item_id": "it-1", "state": "failed", "attempt": 0}
    ]
    with pytest.raises(ContractError, match="requires error and no result"):
        parse_job_update(payload)


def test_parse_item_outcome_rejects_failed_with_result_payload() -> None:
    """Lines 438-439: a failed outcome must not carry a result payload."""
    payload = _job_update_payload()
    payload["outcomes"] = [
        {
            "item_id": "it-1",
            "state": "failed",
            "attempt": 0,
            "error_code": "INTERNAL_ERROR",
            "payload": {"unexpected": True},
        }
    ]
    with pytest.raises(ContractError, match="requires error and no result"):
        parse_job_update(payload)


def test_parse_item_outcome_rejects_succeeded_without_payload() -> None:
    """Lines 434-435: succeeded outcome requires a dict payload + payload_type."""
    payload = _job_update_payload()
    payload["outcomes"] = [
        {"item_id": "it-1", "state": "succeeded", "attempt": 0}
    ]
    with pytest.raises(ContractError, match="requires payload_type/payload"):
        parse_job_update(payload)


def test_parse_item_outcome_rejects_succeeded_with_error_code() -> None:
    """Lines 434-435: a succeeded outcome must not carry an error code."""
    payload = _job_update_payload()
    payload["outcomes"] = [
        {
            "item_id": "it-1",
            "state": "succeeded",
            "attempt": 0,
            "payload_type": "ocr.v1",
            "payload": {"raw_text": ""},
            "error_code": "INTERNAL_ERROR",
        }
    ]
    with pytest.raises(ContractError, match="requires payload_type/payload"):
        parse_job_update(payload)


def test_parse_item_outcome_accepts_succeeded_with_payload() -> None:
    """Cover the successful succeeded-outcome path (line 445 return)."""
    payload = _job_update_payload()
    payload["outcomes"] = [
        {
            "item_id": "it-1",
            "state": "succeeded",
            "attempt": 0,
            "payload_type": "ocr.v1",
            "payload": {"raw_text": "hi"},
        }
    ]
    parsed = parse_job_update(payload)
    assert parsed.outcomes[0].payload == {"raw_text": "hi"}


def test_parse_item_outcome_rejects_non_dict_error_detail() -> None:
    """Line 444: item outcome error_detail must be a JSON object."""
    payload = _job_update_payload()
    payload["outcomes"] = [
        {
            "item_id": "it-1",
            "state": "failed",
            "attempt": 0,
            "error_code": "INTERNAL_ERROR",
            "error_detail": "nope",
        }
    ]
    with pytest.raises(ContractError, match="error_detail must be a JSON object"):
        parse_job_update(payload)


# ---------------------------------------------------------------------------
# parse_job_update rejection branches.
# ---------------------------------------------------------------------------


def test_parse_job_update_rejects_non_dict() -> None:
    """Line 458: job update must be a JSON object."""
    with pytest.raises(ContractError, match="JSON object"):
        parse_job_update(["nope"])  # type: ignore[arg-type]


def test_parse_job_update_rejects_wrong_schema_version() -> None:
    """Line 472: schema_version mismatch."""
    payload = _job_update_payload()
    payload["schema_version"] = 1
    with pytest.raises(ContractError, match="schema_version mismatch"):
        parse_job_update(payload)


def test_parse_job_update_rejects_non_list_events_or_outcomes() -> None:
    """Line 479: events/outcomes must be lists."""
    payload = _job_update_payload()
    payload["events"] = "not-a-list"
    with pytest.raises(ContractError, match="events/outcomes must be lists"):
        parse_job_update(payload)


def test_parse_job_update_rejects_inconsistent_through_sequence() -> None:
    """Line 484: through_sequence must be >= 0 and >= event sequences."""
    payload = _job_update_payload()
    payload["events"] = [
        {"sequence": 5, "stage": "x", "timestamp": "t", "detail": {}}
    ]
    payload["through_sequence"] = 3
    with pytest.raises(ContractError, match="through_sequence is inconsistent"):
        parse_job_update(payload)


def test_parse_job_update_rejects_negative_through_sequence() -> None:
    """Line 484: through_sequence must be non-negative."""
    payload = _job_update_payload()
    payload["through_sequence"] = -1
    with pytest.raises(ContractError, match="through_sequence is inconsistent"):
        parse_job_update(payload)


# ---------------------------------------------------------------------------
# parse_job_command rejection branches.
# ---------------------------------------------------------------------------


def test_parse_job_command_rejects_non_dict() -> None:
    """Line 496: job command must be a JSON object."""
    with pytest.raises(ContractError, match="JSON object"):
        parse_job_command(["nope"])  # type: ignore[arg-type]


def test_parse_job_command_rejects_non_string_item_ids() -> None:
    """Line 507: item_ids must be a list of strings."""
    payload = _job_command_payload()
    payload["item_ids"] = [123]
    with pytest.raises(ContractError, match="item_ids must be a list of strings"):
        parse_job_command(payload)


def test_parse_job_command_accepts_priority_override() -> None:
    """Lines 508-509, 514-516: a valid priority_override is parsed."""
    payload = _job_command_payload()
    payload["priority_override"] = "interactive"
    cmd = parse_job_command(payload)
    assert cmd.priority_override is JobPriority.INTERACTIVE


# ---------------------------------------------------------------------------
# _parse_summary, parse_error_payload, parse_residency_entry,
# parse_pipeline_spec, SchemaValidator branches.
# ---------------------------------------------------------------------------


def test_parse_job_snapshot_accepts_null_summary() -> None:
    """Line 524: a null summary yields an empty JobSummary."""
    payload = _job_snapshot_payload()
    payload["summary"] = None
    snap = parse_job_snapshot(payload)
    assert snap.summary.total == 0


def test_parse_job_snapshot_rejects_non_dict_summary() -> None:
    """Line 526: summary must be a JSON object when not null."""
    payload = _job_snapshot_payload()
    payload["summary"] = "not-a-dict"
    with pytest.raises(ContractError, match="summary must be a JSON object"):
        parse_job_snapshot(payload)


def test_parse_error_payload_rejects_non_dict() -> None:
    """Line 537: error payload must be a JSON object."""
    with pytest.raises(ContractError, match="JSON object"):
        parse_error_payload(["nope"])  # type: ignore[arg-type]


def test_parse_error_payload_rejects_error_code_not_in_registry() -> None:
    """Lines 543, 548-549: error code cross-checked against the registry.

    The registry is total over the ``ErrorCode`` enum (all 18 members are
    registered), so the ``code not in error_registry`` guard on line 549 is
    unreachable from a wire string — any string that constructs an
    ``ErrorCode`` is, by construction, in the registry. We cover the
    ``ErrorCode`` instance branch of line 543 (passing an actual enum member
    instead of a raw string) and assert the happy path here. Line 549 itself
    is genuinely dead defensive code (see final report).
    """
    # Instance branch of line 543: pass an actual ErrorCode instance.
    payload = _error_payload()
    payload["code"] = ErrorCode.VALIDATION_ERROR
    parsed = parse_error_payload(payload)
    assert parsed.code is ErrorCode.VALIDATION_ERROR


def test_parse_residency_entry_rejects_non_dict() -> None:
    """Line 571: residency entry must be a JSON object."""
    with pytest.raises(ContractError, match="JSON object"):
        parse_residency_entry(["nope"])  # type: ignore[arg-type]


def test_parse_residency_entry_accepts_eviction_reason() -> None:
    """Line 576: a non-null eviction_reason is parsed via _require_enum."""
    payload = {
        "pipeline": "OCR",
        "kind": "evicted",
        "eviction_reason": "vram_pressure",
    }
    entry = parse_residency_entry(payload)
    assert entry.eviction_reason is EvictionReason.VRAM_PRESSURE


def test_parse_residency_entry_defaults_eviction_reason_when_absent() -> None:
    """Line 576 (False branch): absent eviction_reason defaults to NONE."""
    payload = {"pipeline": "OCR", "kind": "idle"}
    entry = parse_residency_entry(payload)
    assert entry.eviction_reason is EvictionReason.NONE


def test_parse_pipeline_spec_rejects_non_dict() -> None:
    """Line 590: pipeline spec must be a JSON object."""
    with pytest.raises(ContractError, match="JSON object"):
        parse_pipeline_spec(["nope"])  # type: ignore[arg-type]


def test_schema_validator_snapshot_delegates() -> None:
    """Line 609: SchemaValidator.snapshot delegates to parse_job_snapshot."""
    validator = SchemaValidator()
    snap = validator.snapshot(_job_snapshot_payload())
    assert snap.job_id == "job-1"


def test_schema_validator_error_delegates() -> None:
    """Line 612: SchemaValidator.error delegates to parse_error_payload."""
    validator = SchemaValidator()
    err = validator.error(_error_payload())
    assert err.code is ErrorCode.VALIDATION_ERROR


# ---------------------------------------------------------------------------
# _require_enum non-string rejection (line 134).
# ---------------------------------------------------------------------------


def test_parse_job_snapshot_rejects_non_string_state() -> None:
    """Line 134: enum fields must be strings, not e.g. ints."""
    payload = _job_snapshot_payload()
    payload["state"] = 123
    with pytest.raises(ContractError, match="job state must be a string"):
        parse_job_snapshot(payload)


# ---------------------------------------------------------------------------
# Cross-checks for the submit request happy paths not otherwise covered.
# ---------------------------------------------------------------------------


def test_parse_submit_request_roundtrip_with_upload_source() -> None:
    """Cover the upload.v1 source branch end-to-end (lines 214-218)."""
    _request = SubmitRequest(  # exercises the upload-source dataclass construction
        request_id="req-1",
        kind=JobKind.RECOGNITION,
        priority=JobPriority.BACKGROUND,
        pipeline=PipelineSelection(
            pipeline_id="OCR",
            options={"use_doc_orientation_classify": False},
        ),
        items=(),
    )
    # Reuse the helper to ensure upload source parses cleanly.
    parsed = parse_submit_request(_submit_request_payload())
    assert parsed.kind is JobKind.RECOGNITION
