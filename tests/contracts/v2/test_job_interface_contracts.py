"""Contract tests for the supervisor-only deep job interface."""

from __future__ import annotations

import pytest

from vibeocr.runtime_contracts import (
    ContractError,
    ItemOutcome,
    ItemState,
    JobCommand,
    JobCommandKind,
    JobItem,
    JobKind,
    JobPriority,
    JobRef,
    JobSnapshot,
    JobState,
    JobUpdate,
    PipelineSelection,
    StageEvent,
    SubmitItem,
    SubmitRequest,
    parse_job_command,
    parse_job_ref,
    parse_job_update,
    parse_submit_request,
)


def _request() -> SubmitRequest:
    return SubmitRequest(
        request_id="req-1",
        kind=JobKind.RECOGNITION,
        priority=JobPriority.BACKGROUND,
        pipeline=PipelineSelection(
            pipeline_id="OCR",
            options={"use_doc_orientation_classify": False},
        ),
        items=(
            SubmitItem(
                client_item_key="file-a",
                ordinal=0,
                display_name="a.png",
                source={"type": "upload.v1", "attachment": "file-a"},
            ),
            SubmitItem(
                client_item_key="file-b",
                ordinal=1,
                display_name="b.png",
                source={"type": "upload.v1", "attachment": "file-b"},
            ),
        ),
    )


def test_submit_request_roundtrip_preserves_semantic_intent() -> None:
    parsed = parse_submit_request(_request().to_payload())

    assert parsed.request_id == "req-1"
    assert parsed.priority is JobPriority.BACKGROUND
    assert parsed.pipeline.pipeline_id == "OCR"
    assert parsed.pipeline.options == {"use_doc_orientation_classify": False}
    assert [item.client_item_key for item in parsed.items] == ["file-a", "file-b"]


def test_submit_request_rejects_option_not_supported_by_pipeline() -> None:
    payload = _request().to_payload()
    payload["pipeline"]["options"]["parse_method"] = "auto"

    with pytest.raises(ContractError, match="unsupported option"):
        parse_submit_request(payload)


@pytest.mark.parametrize(
    "mutate,message",
    [
        (
            lambda payload: payload["items"][1].update(client_item_key="file-a"),
            "client_item_key",
        ),
        (
            lambda payload: payload["items"][1].update(ordinal=3),
            "ordinals",
        ),
    ],
)
def test_submit_request_rejects_ambiguous_item_identity(mutate, message) -> None:
    payload = _request().to_payload()
    mutate(payload)

    with pytest.raises(ContractError, match=message):
        parse_submit_request(payload)


def test_job_ref_roundtrip_returns_server_item_mapping() -> None:
    ref = JobRef(
        job_id="job-1",
        state=JobState.QUEUED,
        items=(
            JobItem(
                item_id="it-1",
                client_item_key="file-a",
                ordinal=0,
                display_name="a.png",
                state=ItemState.QUEUED,
            ),
        ),
    )

    parsed = parse_job_ref(ref.to_payload())

    assert parsed.items[0].item_id == "it-1"
    assert parsed.items[0].client_item_key == "file-a"
    assert parsed.items[0].ordinal == 0


def test_job_update_roundtrip_is_atomic_and_keyed() -> None:
    snapshot = JobSnapshot(
        job_id="job-1",
        kind=JobKind.RECOGNITION,
        priority=JobPriority.INTERACTIVE,
        state=JobState.COMPLETED,
        items=(
            JobItem(
                item_id="it-1",
                client_item_key="file-a",
                ordinal=0,
                display_name="a.png",
                state=ItemState.SUCCEEDED,
            ),
        ),
        event_sequence=3,
        request_id="req-1",
        pipeline=PipelineSelection("OCR"),
    )
    update = JobUpdate(
        snapshot=snapshot,
        events=(
            StageEvent(sequence=3, stage="item_succeeded", item_id="it-1"),
        ),
        outcomes=(
            ItemOutcome(
                item_id="it-1",
                state=ItemState.SUCCEEDED,
                attempt=0,
                payload_type="ocr.v1",
                payload={"raw_text": ""},
            ),
        ),
        through_sequence=3,
    )

    parsed = parse_job_update(update.to_payload())

    assert parsed.through_sequence == 3
    assert parsed.snapshot.request_id == "req-1"
    assert parsed.outcomes[0].item_id == "it-1"
    assert parsed.outcomes[0].payload == {"raw_text": ""}


def test_job_update_rejects_empty_success_payload() -> None:
    update = JobUpdate(
        snapshot=JobSnapshot(
            job_id="job-1",
            kind=JobKind.RECOGNITION,
            priority=JobPriority.INTERACTIVE,
            state=JobState.COMPLETED,
        ),
        events=(),
        outcomes=(
            ItemOutcome(
                item_id="it-1",
                state=ItemState.SUCCEEDED,
                attempt=0,
            ),
        ),
        through_sequence=0,
    ).to_payload()

    with pytest.raises(ContractError, match="requires payload_type"):
        parse_job_update(update)


def test_job_command_roundtrip_keeps_retry_scope_and_priority() -> None:
    command = JobCommand(
        command_id="cmd-1",
        kind=JobCommandKind.RETRY,
        job_id="job-1",
        item_ids=("it-1",),
        priority_override=JobPriority.INTERACTIVE,
    )

    parsed = parse_job_command(command.to_payload())

    assert parsed == command
