"""Durable execution-attempt records in the append-only RunJournal."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cross_agent_consensus.integrity import canonical_json_sha256, verified_artifact_sha256
from cross_agent_consensus.io import read_json_file, sha256_file, slugify
from cross_agent_consensus.layout import normalize_round_id, round_dir
from cross_agent_consensus.lifecycle import artifact_chain
from cross_agent_consensus.models import AgentInvocation, Record
from cross_agent_consensus.records import parse_run_records
from cross_agent_consensus.run_audit import (
    append_run_event_locked,
    derive_run_phase,
    read_run_events,
    run_lock,
)


RETRY_SAFETY_VALUES = ("read_only", "idempotent", "mutating", "external_side_effect")
ATTEMPT_EVENT_TYPES = {
    "execution_attempt_started",
    "execution_attempt_completed",
    "execution_attempt_failed",
    "execution_attempt_ambiguous",
}
TERMINAL_ATTEMPT_EVENT_TYPES = ATTEMPT_EVENT_TYPES - {"execution_attempt_started"}


@dataclass(frozen=True)
class ReceiptAttemptSource:
    execution_attempt_id: str
    session_id: str
    participant_identity: str
    round_id: str
    phase: str


def default_retry_safety(phase: str) -> str:
    if phase in {"reviewer", "validator"}:
        return "read_only"
    if phase == "author":
        return "mutating"
    return "external_side_effect"


def resolved_retry_safety(phase: str, requested: str | None) -> str:
    minimum = default_retry_safety(phase)
    value = requested or minimum
    if value not in RETRY_SAFETY_VALUES:
        raise ValueError(f"unknown retry safety: {value}")
    if RETRY_SAFETY_VALUES.index(value) < RETRY_SAFETY_VALUES.index(minimum):
        raise ValueError(f"{phase} invocation cannot weaken retry safety below {minimum}")
    return value


def _identifier_token(value: str) -> str:
    slug = slugify(value)
    if slug == value:
        return slug
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
    return f"{slug}-{digest}"


def invocation_action_id(participant_identity: str, phase: str) -> str:
    role = "reviewer" if phase == "reviewer" else phase
    return f"invoke-{_identifier_token(participant_identity)}-{slugify(role)}"


def _protocol_records_sha256(records: list[Record]) -> str:
    return canonical_json_sha256(
        [
            {
                "record_type": record.record_type,
                "record_id": record.record_id,
                "data": record.data,
            }
            for record in records
        ]
    )


def _artifact_input(run: Path, records: list[Record]) -> tuple[str | None, str | None]:
    head = artifact_chain(records).head
    if head is None:
        return None, None
    artifact_id = str(head.data.get("artifact_version_id") or head.record_id)
    return artifact_id, verified_artifact_sha256(run, head)


def _expected_receipt(run: Path, invocation: AgentInvocation) -> tuple[str, str]:
    current_round = round_dir(run, invocation.round_id)
    if invocation.phase == "reviewer":
        path = current_round / "reviews" / f"{slugify(invocation.participant_identity)}.md"
        receipt_type = "RawReviewerOutput"
    elif invocation.phase == "validator":
        path = current_round / "validation.md"
        receipt_type = "ValidationEvidence"
    elif invocation.phase == "author":
        path = run / "artifacts"
        receipt_type = "ArtifactVersion"
    else:
        path = current_round / "raw"
        receipt_type = "OperatorEvidence"
    try:
        rendered_path = str(path.relative_to(run))
    except ValueError:
        rendered_path = str(path)
    return receipt_type, rendered_path


def _attempt_events(run: Path, action_id: str) -> list[dict[str, Any]]:
    return [
        event
        for event in read_run_events(run)
        if event.get("event_type") in ATTEMPT_EVENT_TYPES
        and isinstance(event.get("details"), dict)
        and event["details"].get("action_id") == action_id
    ]


def _latest_attempt(events: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    starts = [event for event in events if event.get("event_type") == "execution_attempt_started"]
    if not starts:
        return None, None
    start = starts[-1]
    attempt_id = start["details"].get("attempt_id")
    terminal = next(
        (
            event
            for event in reversed(events)
            if event.get("event_type") in TERMINAL_ATTEMPT_EVENT_TYPES
            and event["details"].get("attempt_id") == attempt_id
        ),
        None,
    )
    return start, terminal


def _retry_requires_operator_decision(
    predecessor_start: dict[str, Any] | None,
    predecessor_terminal: dict[str, Any] | None,
    retry_safety: str,
) -> bool:
    if predecessor_start is None or retry_safety not in {"mutating", "external_side_effect"}:
        return False
    if predecessor_terminal is None:
        return True
    if predecessor_terminal.get("event_type") == "execution_attempt_completed":
        return False
    details = predecessor_terminal.get("details")
    return not (
        predecessor_terminal.get("event_type") == "execution_attempt_failed"
        and isinstance(details, dict)
        and details.get("failure_mode") == "launch_failure"
    )


def start_execution_attempt(
    invocation: AgentInvocation,
    *,
    retry_safety: str,
    approve_ambiguous_retry: bool,
    ambiguous_retry_operator_identity: str | None = None,
) -> str:
    """Append the launch intent atomically and return its attempt identifier."""

    if retry_safety not in RETRY_SAFETY_VALUES:
        raise ValueError(f"unknown retry safety: {retry_safety}")
    run = invocation.run
    action_id = invocation_action_id(invocation.participant_identity, invocation.phase)
    with run_lock(run):
        events = _attempt_events(run, action_id)
        predecessor_start, predecessor_terminal = _latest_attempt(events)
        predecessor_details = predecessor_start.get("details", {}) if predecessor_start else {}
        predecessor_safety = str(predecessor_details.get("retry_safety") or retry_safety)
        if (
            _retry_requires_operator_decision(
                predecessor_start, predecessor_terminal, predecessor_safety
            )
            and not approve_ambiguous_retry
        ):
            predecessor_id = predecessor_details.get("attempt_id")
            raise ValueError(
                f"attempt {predecessor_id} is ambiguous and {predecessor_safety}; "
                "operator decision required via --approve-ambiguous-retry"
            )
        if approve_ambiguous_retry and not ambiguous_retry_operator_identity:
            raise ValueError("--approve-ambiguous-retry requires --operator-identity")
        records = parse_run_records(run)
        attempt_number = len(
            [event for event in events if event.get("event_type") == "execution_attempt_started"]
        ) + 1
        attempt_id = f"attempt-{canonical_json_sha256({'action_id': action_id})[:10]}-{attempt_number:03d}"
        artifact_id, artifact_sha = _artifact_input(run, records)
        receipt_type, receipt_path = _expected_receipt(run, invocation)
        phase = derive_run_phase(records)
        details = {
            "attempt_id": attempt_id,
            "action_id": action_id,
            "attempt_number": attempt_number,
            "predecessor_attempt_id_or_null": (
                predecessor_details.get("attempt_id") if predecessor_start else None
            ),
            "participant_identity": invocation.participant_identity,
            "participant_profile_id": invocation.participant_profile_id,
            "execution_profile_id": invocation.execution_profile_id,
            "player_id": invocation.player_id,
            "phase": invocation.phase,
            "round_id": normalize_round_id(invocation.round_id),
            "session_id": invocation.session_id,
            "input_protocol_records_sha256": _protocol_records_sha256(records),
            "input_artifact_version_id_or_null": artifact_id,
            "input_artifact_sha256_or_null": artifact_sha,
            "prompt_sha256": sha256_file(invocation.prompt_path),
            "expected_receipt_type": receipt_type,
            "expected_receipt_path": receipt_path,
            "retry_safety": retry_safety,
            "ambiguous_retry_operator_approved": bool(approve_ambiguous_retry),
            "ambiguous_retry_operator_identity_or_null": ambiguous_retry_operator_identity,
        }
        append_run_event_locked(
            run,
            "execution_attempt_started",
            actor_identity=invocation.participant_identity,
            phase_before=phase,
            phase_after=phase,
            details=details,
        )
    return attempt_id


def append_attempt_observation(
    invocation: AgentInvocation,
    event_type: str,
    *,
    failure_mode: str | None = None,
    exit_code: int | None = None,
    signal_number: int | None = None,
) -> None:
    if event_type not in TERMINAL_ATTEMPT_EVENT_TYPES:
        raise ValueError(f"invalid execution-attempt observation: {event_type}")
    if not invocation.execution_attempt_id:
        return
    with run_lock(invocation.run):
        records = parse_run_records(invocation.run)
        phase = derive_run_phase(records)
        details: dict[str, Any] = {
            "attempt_id": invocation.execution_attempt_id,
            "action_id": invocation_action_id(invocation.participant_identity, invocation.phase),
            "session_id": invocation.session_id,
        }
        if failure_mode is not None:
            details["failure_mode"] = failure_mode
        if exit_code is not None:
            details["exit_code"] = exit_code
        if signal_number is not None:
            details["signal"] = signal_number
        append_run_event_locked(
            invocation.run,
            event_type,
            actor_identity=invocation.participant_identity,
            phase_before=phase,
            phase_after=phase,
            details=details,
        )


def receipt_attempt_source(
    run: Path,
    source_file: str | None,
    *,
    participant_identity: str | None = None,
    round_id: str | None = None,
) -> ReceiptAttemptSource | None:
    if not source_file:
        return None
    source = Path(source_file).resolve()
    candidates = sorted(
        run.glob("**/agents/*/session-*/invocation.json"),
        key=lambda path: str(path.parent),
        reverse=True,
    )
    for invocation_path in candidates:
        try:
            invocation = read_json_file(invocation_path)
        except (OSError, ValueError):
            continue
        raw_value = invocation.get("raw_output_path")
        if not isinstance(raw_value, str) or not raw_value:
            continue
        raw_path = Path(raw_value)
        if not raw_path.is_absolute():
            raw_path = run / raw_path
        recorded_identity = str(
            invocation.get("participant_identity")
            or invocation.get("actor_identity")
            or invocation_path.parent.parent.name
        )
        recorded_round = normalize_round_id(str(invocation.get("round_id") or "round-1"))
        if participant_identity is not None and recorded_identity != participant_identity:
            continue
        if round_id is not None and recorded_round != normalize_round_id(round_id):
            continue
        if raw_path.resolve() == source:
            attempt_id = invocation.get("execution_attempt_id_or_null")
            if not isinstance(attempt_id, str) or not attempt_id:
                return None
            return ReceiptAttemptSource(
                execution_attempt_id=attempt_id,
                session_id=str(invocation.get("session_id") or invocation_path.parent.name),
                participant_identity=recorded_identity,
                round_id=recorded_round,
                phase=str(invocation.get("phase") or "unknown"),
            )
    return None


def _attempt_start(run: Path, attempt_id: str) -> dict[str, Any] | None:
    return next(
        (
            event
            for event in reversed(read_run_events(run))
            if event.get("event_type") == "execution_attempt_started"
            and isinstance(event.get("details"), dict)
            and event["details"].get("attempt_id") == attempt_id
        ),
        None,
    )


def receipt_attempt_source_by_id(run: Path, attempt_id: str) -> ReceiptAttemptSource:
    start = _attempt_start(run, attempt_id)
    if start is None:
        raise ValueError(f"execution attempt not found: {attempt_id}")
    details = start["details"]
    return ReceiptAttemptSource(
        execution_attempt_id=attempt_id,
        session_id=str(details.get("session_id") or ""),
        participant_identity=str(details.get("participant_identity") or ""),
        round_id=normalize_round_id(str(details.get("round_id") or "round-1")),
        phase=str(details.get("phase") or "unknown"),
    )


def _attempt_observations(run: Path, attempt_id: str) -> list[dict[str, Any]]:
    return [
        event
        for event in read_run_events(run)
        if event.get("event_type") in TERMINAL_ATTEMPT_EVENT_TYPES
        and isinstance(event.get("details"), dict)
        and event["details"].get("attempt_id") == attempt_id
    ]


def assert_attempt_accepts_receipt_locked(
    run: Path,
    source: ReceiptAttemptSource,
    expected_receipt_type: str,
) -> None:
    start = _attempt_start(run, source.execution_attempt_id)
    if start is None:
        raise ValueError(f"execution attempt not found: {source.execution_attempt_id}")
    details = start["details"]
    if details.get("participant_identity") != source.participant_identity:
        raise ValueError("receipt ParticipantIdentity does not match execution attempt")
    if normalize_round_id(str(details.get("round_id") or "")) != source.round_id:
        raise ValueError("receipt round does not match execution attempt")
    if details.get("phase") != source.phase:
        raise ValueError("receipt phase does not match execution attempt")
    if details.get("expected_receipt_type") != expected_receipt_type:
        raise ValueError(
            f"execution attempt expects {details.get('expected_receipt_type')}, not {expected_receipt_type}"
        )
    observations = _attempt_observations(run, source.execution_attempt_id)
    if not observations:
        raise ValueError("execution attempt has no provider observation")
    latest = observations[-1]
    latest_details = latest.get("details")
    if not (
        latest.get("event_type") == "execution_attempt_ambiguous"
        and isinstance(latest_details, dict)
        and latest_details.get("failure_mode") == "missing_receipt"
    ):
        raise ValueError(
            f"execution attempt cannot accept a receipt after {latest.get('event_type')}"
        )


def complete_attempt_for_receipt_locked(
    run: Path,
    source: ReceiptAttemptSource,
    receipt: Record,
) -> None:
    assert_attempt_accepts_receipt_locked(run, source, receipt.record_type)
    start = _attempt_start(run, source.execution_attempt_id)
    if start is None:
        return
    details = start["details"]
    artifact_id = (
        receipt.data.get("artifact_version_id")
        if receipt.record_type == "RawReviewerOutput"
        else receipt.data.get("target_artifact_version_id")
        if receipt.record_type == "ValidationEvidence"
        else receipt.data.get("artifact_version_id")
    )
    expected_artifact_id = details.get("input_artifact_version_id_or_null")
    if receipt.record_type != "ArtifactVersion" and artifact_id != expected_artifact_id:
        raise ValueError("receipt ArtifactVersion does not match execution-attempt input")
    if receipt.record_type == "RawReviewerOutput" and (
        receipt.data.get("reviewer_identity") != source.participant_identity
    ):
        raise ValueError("RawReviewerOutput reviewer does not match execution attempt")
    if receipt.record_type == "ArtifactVersion" and (
        receipt.data.get("produced_by") != source.participant_identity
    ):
        raise ValueError("ArtifactVersion producer does not match execution attempt")
    records = parse_run_records(run)
    phase = derive_run_phase(records)
    append_run_event_locked(
        run,
        "execution_attempt_completed",
        actor_identity=str(details.get("participant_identity") or "orchestrator-capture-tool"),
        phase_before=phase,
        phase_after=phase,
        details={
            "attempt_id": source.execution_attempt_id,
            "action_id": details.get("action_id"),
            "session_id": source.session_id,
            "receipt_record_type": receipt.record_type,
            "receipt_record_id": receipt.record_id,
            "receipt_record_sha256": canonical_json_sha256(
                {
                    "record_type": receipt.record_type,
                    "record_id": receipt.record_id,
                    "data": receipt.data,
                }
            ),
        },
    )


def fail_attempt_for_receipt_locked(
    run: Path,
    source: ReceiptAttemptSource,
    failure_mode: str,
    reason: str,
) -> None:
    start = _attempt_start(run, source.execution_attempt_id)
    if start is None:
        return
    observations = _attempt_observations(run, source.execution_attempt_id)
    if observations and observations[-1].get("event_type") in {
        "execution_attempt_failed",
        "execution_attempt_completed",
    }:
        return
    details = start["details"]
    records = parse_run_records(run)
    phase = derive_run_phase(records)
    append_run_event_locked(
        run,
        "execution_attempt_failed",
        actor_identity=str(details.get("participant_identity") or "orchestrator-capture-tool"),
        phase_before=phase,
        phase_after=phase,
        details={
            "attempt_id": source.execution_attempt_id,
            "action_id": details.get("action_id"),
            "session_id": source.session_id,
            "failure_mode": failure_mode,
            "reason": reason[:1000],
        },
    )


def latest_attempt_statuses(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the latest state of every action for deterministic planning."""

    action_ids = {
        str(event["details"].get("action_id"))
        for event in events
        if event.get("event_type") == "execution_attempt_started"
        and isinstance(event.get("details"), dict)
        and event["details"].get("action_id")
    }
    statuses: list[dict[str, Any]] = []
    for action_id in sorted(action_ids):
        action_events = [
            event
            for event in events
            if isinstance(event.get("details"), dict)
            and event["details"].get("action_id") == action_id
        ]
        start, terminal = _latest_attempt(action_events)
        if start is None:
            continue
        start_details = start["details"]
        terminal_details = terminal.get("details", {}) if terminal else {}
        statuses.append(
            {
                "action_id": action_id,
                "attempt_id": start_details.get("attempt_id"),
                "retry_safety": start_details.get("retry_safety"),
                "event_type": terminal.get("event_type") if terminal else "execution_attempt_incomplete",
                "failure_mode_or_null": terminal_details.get("failure_mode"),
                "requires_operator_decision": _retry_requires_operator_decision(
                    start,
                    terminal,
                    str(start_details.get("retry_safety") or "read_only"),
                ),
            }
        )
    return statuses
