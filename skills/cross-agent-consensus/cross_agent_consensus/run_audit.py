"""Run mutation locking, event journaling, and derived lifecycle state."""

from __future__ import annotations

import fcntl
import hashlib
import json
import re
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Any, Callable, ContextManager, Iterator, ParamSpec

from cross_agent_consensus.io import append_jsonl, atomic_write_json, read_json_file, sha256_file, utc_now
from cross_agent_consensus.lifecycle import (
    current_artifact_version_id,
    current_author_response_finding_ids,
    effective_blocking_finding_ids,
)
from cross_agent_consensus.models import Record
from cross_agent_consensus.records import first_record, parse_run_records, records_by_type


LEGACY_RUN_EVENT_SCHEMA = "cross-agent-consensus-run-event-1"
RUN_EVENT_SCHEMA = "cross-agent-consensus-run-event-2"
RUN_EVENT_ANCHOR_SCHEMA = "cross-agent-consensus-run-event-anchor-1"
RUN_LOCK_FILENAME = ".cac.lock"
RUN_EVENTS_FILENAME = "events.jsonl"
RUN_EVENT_ANCHOR_FILENAME = ".cac-events-anchor.json"
RUN_VERSION_RE = re.compile(r"^- `cross_agent_consensus_version`: `(?P<version>\d+\.\d+\.\d+)`$", re.MULTILINE)

RUN_PHASE_TRANSITIONS: dict[str, set[str]] = {
    "not_initialized": {"not_initialized", "awaiting_artifact", "awaiting_review"},
    "awaiting_artifact": {"awaiting_artifact", "awaiting_review"},
    "awaiting_review": {
        "awaiting_review",
        "awaiting_normalization",
        "awaiting_author_response",
        "awaiting_validation",
        "ready_for_termination",
        "terminated",
    },
    "awaiting_normalization": {
        "awaiting_normalization",
        "awaiting_author_response",
        "awaiting_rereview",
        "awaiting_validation",
        "ready_for_termination",
        "terminated",
    },
    "awaiting_author_response": {
        "awaiting_author_response",
        "awaiting_rereview",
        "awaiting_review",
        "awaiting_validation",
        "terminated",
    },
    "awaiting_rereview": {
        "awaiting_rereview",
        "awaiting_review",
        "awaiting_validation",
        "ready_for_termination",
        "terminated",
    },
    "awaiting_validation": {
        "awaiting_validation",
        "awaiting_review",
        "awaiting_normalization",
        "awaiting_author_response",
        "ready_for_termination",
        "terminated",
    },
    "ready_for_termination": {
        "ready_for_termination",
        "awaiting_review",
        "awaiting_validation",
        "terminated",
    },
    "terminated": {"terminated"},
}

P = ParamSpec("P")


@contextmanager
def exclusive_file_lock(path: Path) -> Iterator[None]:
    """Hold an advisory exclusive lock for the duration of the context."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def run_lock(run: Path) -> ContextManager[None]:
    return exclusive_file_lock(run / RUN_LOCK_FILENAME)


def _latest_review_batch(records: list[Record]) -> Record | None:
    batches = records_by_type(records, "ReviewBatch")
    return batches[-1] if batches else None


def _expected_reviewers(records: list[Record], batch: Record) -> set[str]:
    expected = batch.data.get("expected_reviewer_identities")
    if isinstance(expected, list) and expected:
        return {str(value) for value in expected}
    participants = first_record(records, "Participants")
    if participants is None:
        return set()
    reviewers = participants.data.get("reviewer_identities")
    return {str(value) for value in reviewers} if isinstance(reviewers, list) else set()


def _blocking_finding_ids(records: list[Record]) -> set[str]:
    return effective_blocking_finding_ids(records)


def derive_run_phase(records: list[Record]) -> str:
    """Derive one lifecycle phase from the durable protocol records."""

    if records_by_type(records, "TerminationRecord"):
        return "terminated"
    if first_record(records, "TaskBrief") is None:
        return "not_initialized"
    if not records_by_type(records, "ArtifactVersion"):
        return "awaiting_artifact"
    batch = _latest_review_batch(records)
    if batch is None:
        return "awaiting_review"
    batch_id = str(batch.data.get("review_batch_id") or "")
    if batch.data.get("review_mode") != "remediation_verification":
        expected_reviewers = _expected_reviewers(records, batch)
        captured_reviewers = {
            str(record.data.get("reviewer_identity"))
            for record in records_by_type(records, "RawReviewerOutput")
            if str(record.data.get("review_batch_id") or "") == batch_id
        }
        if expected_reviewers and not expected_reviewers <= captured_reviewers:
            return "awaiting_review"
        if not captured_reviewers:
            return "awaiting_review"

        batch_raw_ids = {
            str(record.data.get("raw_finding_id"))
            for record in records_by_type(records, "RawFinding")
            if str(record.data.get("review_batch_id") or "") == batch_id
        }
        normalized_raw_ids = {
            str(raw_id)
            for record in records_by_type(records, "NormalizationRecord")
            for raw_id in (record.data.get("source_raw_finding_ids") or [])
        }
        if batch_raw_ids - normalized_raw_ids:
            return "awaiting_normalization"

    blockers = _blocking_finding_ids(records)
    if blockers:
        responded = current_author_response_finding_ids(records)
        if blockers - responded:
            return "awaiting_author_response"
        return "awaiting_rereview"

    policy = first_record(records, "Policy")
    required = {
        str(value)
        for value in ((policy.data.get("required_validator_ids") if policy else None) or [])
    }
    target_artifact_version_id = current_artifact_version_id(records)
    validator_results: dict[str, str] = {}
    for record in records_by_type(records, "ValidationEvidence"):
        if (
            target_artifact_version_id is not None
            and record.data.get("target_artifact_version_id") != target_artifact_version_id
        ):
            continue
        validator = record.data.get("validator_id")
        result = record.data.get("result")
        if validator and result:
            validator_results[str(validator)] = str(result)
    if any(validator_results.get(validator) not in {"pass", "waived"} for validator in required):
        return "awaiting_validation"
    return "ready_for_termination"


def _event_count(path: Path) -> int:
    if not path.is_file():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip())


def _canonical_json_sha256(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _event_sha256(event: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in event.items() if key != "event_sha256"}
    return _canonical_json_sha256(unsigned)


def _nonempty_event_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return [line for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]


def _legacy_event_journal(path: Path) -> bool:
    lines = _nonempty_event_lines(path)
    if not lines:
        return False
    try:
        first = json.loads(lines[0])
    except json.JSONDecodeError:
        return False
    return isinstance(first, dict) and first.get("schema_version") == LEGACY_RUN_EVENT_SCHEMA


def recorded_run_version(run: Path) -> tuple[int, int, int] | None:
    run_path = run / "run.md"
    if not run_path.is_file():
        return None
    match = RUN_VERSION_RE.search(run_path.read_text(encoding="utf-8", errors="replace"))
    if match is None:
        return None
    major, minor, patch = (int(part) for part in match.group("version").split("."))
    return major, minor, patch


def run_requires_event_integrity_v2(run: Path) -> bool:
    """Choose compatibility from run provenance, never from one mutable event marker."""

    if (run / RUN_EVENT_ANCHOR_FILENAME).exists():
        return True
    version = recorded_run_version(run)
    if version is not None and version >= (0, 10, 0):
        return True
    return any(event.get("schema_version") == RUN_EVENT_SCHEMA for event in read_run_events(run))


def journal_phase_matches_records(
    run_version: tuple[int, int, int] | None,
    journal_phase: Any,
    derived_phase: str,
) -> bool:
    """Accept the one phase reinterpretation introduced by CAC 0.13.0."""

    if journal_phase == derived_phase:
        return True
    return (
        run_version is not None
        and run_version < (0, 13, 0)
        and journal_phase == "awaiting_rereview"
        and derived_phase in {"awaiting_validation", "ready_for_termination"}
    )


def _write_event_anchor(run: Path, event: dict[str, Any]) -> None:
    path = run / RUN_EVENTS_FILENAME
    atomic_write_json(
        run / RUN_EVENT_ANCHOR_FILENAME,
        {
            "schema_version": RUN_EVENT_ANCHOR_SCHEMA,
            "run_id": run.name,
            "event_count": int(event["sequence"]),
            "last_event_sha256": event["event_sha256"],
            "events_jsonl_sha256": sha256_file(path),
        },
    )


def append_run_event_locked(
    run: Path,
    event_type: str,
    *,
    actor_identity: str,
    phase_before: str,
    phase_after: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Append an event while the caller holds ``run_lock(run)``."""

    path = run / RUN_EVENTS_FILENAME
    requires_v2 = run_requires_event_integrity_v2(run)
    previous_events = read_run_events(run)
    if previous_events:
        preceding_phase = previous_events[-1].get("phase_after")
        if isinstance(preceding_phase, str):
            phase_before = str(preceding_phase)
    if _event_count(path) == 0 and event_type != "run_initialized" and requires_v2:
        initial_event: dict[str, Any] = {
            "schema_version": RUN_EVENT_SCHEMA,
            "sequence": 1,
            "created_at": utc_now(),
            "run_id": run.name,
            "actor_identity": "orchestrator-auto-audit",
            "event_type": "run_initialized",
            "phase_before": "not_initialized",
            "phase_after": phase_before,
            "details": {"auto_recorded_before": event_type},
            "previous_event_sha256_or_null": None,
        }
        initial_event["event_sha256"] = _event_sha256(initial_event)
        append_jsonl(path, initial_event)
        _write_event_anchor(run, initial_event)
        previous_events = [initial_event]
    sequence = _event_count(path) + 1
    event: dict[str, Any] = {
        "schema_version": (
            LEGACY_RUN_EVENT_SCHEMA
            if _legacy_event_journal(path) or (_event_count(path) == 0 and not requires_v2)
            else RUN_EVENT_SCHEMA
        ),
        "sequence": sequence,
        "created_at": utc_now(),
        "run_id": run.name,
        "actor_identity": actor_identity,
        "event_type": event_type,
        "phase_before": phase_before,
        "phase_after": phase_after,
        "details": details or {},
    }
    if event["schema_version"] == RUN_EVENT_SCHEMA:
        event["previous_event_sha256_or_null"] = (
            previous_events[-1].get("event_sha256") if previous_events else None
        )
        event["event_sha256"] = _event_sha256(event)
    append_jsonl(path, event)
    if event["schema_version"] == RUN_EVENT_SCHEMA:
        _write_event_anchor(run, event)


def command_event_details(args: Any) -> dict[str, Any]:
    details: dict[str, Any] = {}
    for attribute in (
        "phase",
        "round",
        "review_batch",
        "artifact_version",
        "terminal_condition",
        "validator_id",
        "result",
        "promotion_id",
        "source_draft_sha256",
        "promoted_record_ids",
        "git_change_snapshot_sha256",
    ):
        value = getattr(args, attribute, None)
        if value is not None:
            details[attribute] = value
    return details


def locked_run_command(
    event_type: str,
    *,
    writes_when: Callable[[Any], bool] | None = None,
) -> Callable[[Callable[P, int]], Callable[P, int]]:
    """Serialize a command and append one run event after a successful write."""

    def decorator(function: Callable[P, int]) -> Callable[P, int]:
        @wraps(function)
        def wrapped(*function_args: P.args, **function_kwargs: P.kwargs) -> int:
            args = function_args[0] if function_args else function_kwargs.get("args")
            if args is None or (writes_when is not None and not writes_when(args)):
                return function(*function_args, **function_kwargs)
            run = Path(str(getattr(args, "run")))
            with run_lock(run):
                records_before = parse_run_records(run) if run.exists() else []
                phase_before = derive_run_phase(records_before)
                return_code = function(*function_args, **function_kwargs)
                records_after = parse_run_records(run)
                records_changed = records_after != records_before
                suppress_run_event = bool(getattr(args, "suppress_run_event", False))
                if (return_code == 0 or records_changed) and not suppress_run_event:
                    details = command_event_details(args)
                    if return_code != 0:
                        details["command_return_code"] = return_code
                        details["protocol_records_changed_on_nonzero_exit"] = True
                    append_run_event_locked(
                        run,
                        event_type if return_code == 0 else f"{event_type}_protocol_mutation",
                        actor_identity=str(getattr(args, "actor", None) or "orchestrator-consensus-tool"),
                        phase_before=phase_before,
                        phase_after=derive_run_phase(records_after),
                        details=details,
                    )
                return return_code

        return wrapped

    return decorator


def read_run_events(run: Path) -> list[dict[str, Any]]:
    path = run / RUN_EVENTS_FILENAME
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def execution_attempt_event_messages(events: list[dict[str, Any]]) -> list[str]:
    """Validate execution-attempt identity, transitions, and unsafe retries."""

    messages: list[str] = []
    attempts: dict[str, dict[str, Any]] = {}
    latest_by_action: dict[str, str] = {}
    for event in events:
        event_type = event.get("event_type")
        if event_type not in {
            "execution_attempt_started",
            "execution_attempt_ambiguous",
            "execution_attempt_failed",
            "execution_attempt_completed",
        }:
            continue
        sequence = event.get("sequence")
        details = event.get("details")
        if not isinstance(details, dict):
            messages.append(f"events.jsonl:{sequence}: execution-attempt details must be an object")
            continue
        attempt_id = details.get("attempt_id")
        action_id = details.get("action_id")
        if not isinstance(attempt_id, str) or not attempt_id:
            messages.append(f"events.jsonl:{sequence}: execution-attempt attempt_id is required")
            continue
        if event_type == "execution_attempt_started":
            if attempt_id in attempts:
                messages.append(f"events.jsonl:{sequence}: duplicate execution attempt {attempt_id}")
                continue
            if not isinstance(action_id, str) or not action_id:
                messages.append(f"events.jsonl:{sequence}: execution-attempt action_id is required")
                continue
            predecessor_id = latest_by_action.get(action_id)
            recorded_predecessor = details.get("predecessor_attempt_id_or_null")
            if recorded_predecessor != predecessor_id:
                messages.append(
                    f"events.jsonl:{sequence}: predecessor attempt must be {predecessor_id!r}"
                )
            if predecessor_id is not None:
                predecessor = attempts[predecessor_id]
                predecessor_state = predecessor["state"]
                predecessor_failure = predecessor.get("failure_mode")
                retry_safety = predecessor["details"].get("retry_safety")
                unsafe_retry = (
                    retry_safety in {"mutating", "external_side_effect"}
                    and predecessor_state != "completed"
                    and not (
                        predecessor_state == "failed"
                        and predecessor_failure == "launch_failure"
                    )
                )
                if unsafe_retry and (
                    details.get("ambiguous_retry_operator_approved") is not True
                    or not details.get("ambiguous_retry_operator_identity_or_null")
                ):
                    messages.append(
                        f"events.jsonl:{sequence}: unsafe retry of {predecessor_id} lacks operator identity"
                    )
            attempts[attempt_id] = {
                "details": details,
                "state": "started",
                "failure_mode": None,
            }
            latest_by_action[action_id] = attempt_id
            continue
        attempt = attempts.get(attempt_id)
        if attempt is None:
            messages.append(f"events.jsonl:{sequence}: observation references unknown attempt {attempt_id}")
            continue
        start_details = attempt["details"]
        if action_id != start_details.get("action_id"):
            messages.append(f"events.jsonl:{sequence}: action_id differs for attempt {attempt_id}")
        if details.get("session_id") != start_details.get("session_id"):
            messages.append(f"events.jsonl:{sequence}: session_id differs for attempt {attempt_id}")
        previous_state = attempt["state"]
        allowed_previous = {
            "execution_attempt_ambiguous": {"started"},
            "execution_attempt_failed": {"started", "ambiguous"},
            "execution_attempt_completed": {"ambiguous"},
        }[str(event_type)]
        if previous_state not in allowed_previous:
            messages.append(
                f"events.jsonl:{sequence}: invalid attempt transition {previous_state} -> {event_type}"
            )
            continue
        if event_type == "execution_attempt_ambiguous" and details.get("failure_mode") != "missing_receipt":
            messages.append(
                f"events.jsonl:{sequence}: ambiguous attempt must use failure_mode missing_receipt"
            )
        if event_type == "execution_attempt_completed" and (
            details.get("receipt_record_type") != start_details.get("expected_receipt_type")
        ):
            messages.append(f"events.jsonl:{sequence}: receipt type differs for attempt {attempt_id}")
        attempt["state"] = str(event_type).removeprefix("execution_attempt_")
        attempt["failure_mode"] = details.get("failure_mode")
    return messages


def provider_session_event_messages(events: list[dict[str, Any]]) -> list[str]:
    """Verify provider-session ownership, predecessor links, and attempt binding."""

    messages: list[str] = []
    attempts: dict[str, dict[str, Any]] = {}
    attempt_states: dict[str, str] = {}
    captured_attempts: set[str] = set()
    sessions: dict[str, dict[str, Any]] = {}
    successors: dict[str, list[str]] = {}
    reservations: dict[str, dict[str, Any]] = {}
    reservations_by_predecessor: dict[str, list[str]] = {}
    consumed_reservations: set[str] = set()
    provider_owners: dict[str, str] = {}
    required_fields = {
        "provider_session_entry_id",
        "provider_session_id",
        "run_id",
        "cac_session_id",
        "execution_attempt_id",
        "participant_identity",
        "participant_profile_id",
        "execution_profile_id",
        "player_id",
        "phase",
        "round_id",
        "continuation_definition_sha256",
        "package_version",
        "effective_command_sha256",
        "prompt_sha256",
    }
    sha_fields = {
        "continuation_definition_sha256",
        "effective_command_sha256",
        "prompt_sha256",
    }
    for event in events:
        details = event.get("details")
        event_type = event.get("event_type")
        sequence = event.get("sequence")
        if event_type == "execution_attempt_started" and isinstance(details, dict):
            attempt_id = details.get("attempt_id")
            if isinstance(attempt_id, str):
                attempts[attempt_id] = details
                attempt_states[attempt_id] = "started"
            continue
        if event_type in {
            "execution_attempt_ambiguous",
            "execution_attempt_failed",
            "execution_attempt_completed",
            } and isinstance(details, dict):
            attempt_id = details.get("attempt_id")
            if isinstance(attempt_id, str):
                attempt_states[attempt_id] = str(event_type).removeprefix(
                    "execution_attempt_"
                )
            continue
        if event_type == "provider_session_resume_reserved":
            if not isinstance(details, dict):
                messages.append(
                    f"events.jsonl:{sequence}: provider resume reservation details must be an object"
                )
                continue
            reservation_id = details.get("provider_session_resume_reservation_id")
            attempt_id = details.get("execution_attempt_id")
            predecessor_id = details.get("predecessor_provider_session_entry_id")
            if not all(
                isinstance(value, str) and value
                for value in (reservation_id, attempt_id, predecessor_id)
            ):
                messages.append(
                    f"events.jsonl:{sequence}: provider resume reservation identifiers are required"
                )
                continue
            reservation_id = str(reservation_id)
            attempt_id = str(attempt_id)
            predecessor_id = str(predecessor_id)
            if reservation_id in reservations:
                messages.append(
                    f"events.jsonl:{sequence}: duplicate provider resume reservation {reservation_id}"
                )
            if attempt_states.get(attempt_id) != "started":
                messages.append(
                    f"events.jsonl:{sequence}: provider resume reservation requires a started "
                    f"execution attempt, got {attempt_states.get(attempt_id)!r}"
                )
            predecessor = sessions.get(predecessor_id)
            if predecessor is None:
                messages.append(
                    f"events.jsonl:{sequence}: provider resume reservation predecessor "
                    f"{predecessor_id!r} must be an earlier capture"
                )
            else:
                for field in {
                    "provider_session_id",
                    "participant_identity",
                    "participant_profile_id",
                    "execution_profile_id",
                    "player_id",
                    "phase",
                    "artifact_lineage_root_id_or_null",
                }:
                    if predecessor.get(field) != details.get(field):
                        messages.append(
                            f"events.jsonl:{sequence}: provider resume reservation changes {field}"
                        )
            reservations[reservation_id] = details
            reservations_by_predecessor.setdefault(predecessor_id, []).append(
                reservation_id
            )
            continue
        if event_type != "provider_session_captured":
            continue
        if not isinstance(details, dict):
            messages.append(f"events.jsonl:{sequence}: provider-session details must be an object")
            continue
        missing = sorted(field for field in required_fields if not details.get(field))
        if missing:
            messages.append(
                f"events.jsonl:{sequence}: provider-session fields are required: {', '.join(missing)}"
            )
            continue
        entry_id = str(details["provider_session_entry_id"])
        if entry_id in sessions:
            messages.append(f"events.jsonl:{sequence}: duplicate provider session entry {entry_id}")
            continue
        for field in sha_fields:
            value = details.get(field)
            if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                messages.append(f"events.jsonl:{sequence}: {field} must be a sha256 digest")
        attempt = attempts.get(str(details["execution_attempt_id"]))
        if attempt is None:
            messages.append(
                f"events.jsonl:{sequence}: provider session references unknown execution attempt "
                f"{details['execution_attempt_id']}"
            )
        else:
            attempt_id = str(details["execution_attempt_id"])
            if attempt_states.get(attempt_id) != "started":
                messages.append(
                    f"events.jsonl:{sequence}: provider session must be captured while execution "
                    f"attempt {attempt_id} is started, got {attempt_states.get(attempt_id)!r}"
                )
            if attempt_id in captured_attempts:
                messages.append(
                    f"events.jsonl:{sequence}: execution attempt {attempt_id} has multiple provider captures"
                )
            captured_attempts.add(attempt_id)
            attempt_pairs = {
                "session_id": "cac_session_id",
                "participant_identity": "participant_identity",
                "participant_profile_id": "participant_profile_id",
                "execution_profile_id": "execution_profile_id",
                "player_id": "player_id",
                "phase": "phase",
                "round_id": "round_id",
            }
            for attempt_field, provider_field in attempt_pairs.items():
                if attempt.get(attempt_field) != details.get(provider_field):
                    messages.append(
                        f"events.jsonl:{sequence}: provider session {provider_field} differs from execution attempt"
                    )
        predecessor_id = details.get("predecessor_provider_session_entry_id_or_null")
        if predecessor_id is not None:
            successors.setdefault(str(predecessor_id), []).append(entry_id)
            reservation_id = details.get(
                "provider_session_resume_reservation_id_or_null"
            )
            reservation = reservations.get(str(reservation_id))
            if reservation is None:
                messages.append(
                    f"events.jsonl:{sequence}: resumed provider capture requires an earlier reservation"
                )
            else:
                if reservation.get("execution_attempt_id") != details.get(
                    "execution_attempt_id"
                ) or reservation.get(
                    "predecessor_provider_session_entry_id"
                ) != predecessor_id:
                    messages.append(
                        f"events.jsonl:{sequence}: provider resume reservation does not match capture"
                    )
                if str(reservation_id) in consumed_reservations:
                    messages.append(
                        f"events.jsonl:{sequence}: provider resume reservation {reservation_id} "
                        "is consumed more than once"
                    )
                consumed_reservations.add(str(reservation_id))
            predecessor = sessions.get(str(predecessor_id))
            if predecessor is None:
                messages.append(
                    f"events.jsonl:{sequence}: provider session predecessor {predecessor_id!r} "
                    "must be an earlier entry"
                )
            else:
                from cross_agent_consensus.invocation.adapters import (
                    adapter_allows_provider_session_id_rotation,
                )

                stable_fields = {
                    "run_id",
                    "participant_identity",
                    "participant_profile_id",
                    "execution_profile_id",
                    "player_id",
                    "phase",
                    "artifact_lineage_root_id_or_null",
                }
                if not adapter_allows_provider_session_id_rotation(
                    str(details.get("player_id") or "")
                ):
                    stable_fields.add("provider_session_id")
                for field in sorted(stable_fields):
                    if predecessor.get(field) != details.get(field):
                        messages.append(
                            f"events.jsonl:{sequence}: resumed provider session changes {field}"
                        )
        elif details.get("provider_session_resume_reservation_id_or_null") is not None:
            messages.append(
                f"events.jsonl:{sequence}: fresh provider capture cannot consume a resume reservation"
            )
        provider_session_id = str(details["provider_session_id"])
        participant_identity = str(details["participant_identity"])
        owner = provider_owners.get(provider_session_id)
        if owner is not None and owner != participant_identity:
            messages.append(
                f"events.jsonl:{sequence}: provider session {provider_session_id!r} is shared by "
                f"ParticipantIdentity {owner!r} and {participant_identity!r}"
            )
        provider_owners[provider_session_id] = participant_identity
        sessions[entry_id] = details
    for predecessor_id, successor_ids in sorted(successors.items()):
        if len(successor_ids) > 1:
            messages.append(
                f"events.jsonl: provider session predecessor {predecessor_id!r} branches to "
                + ", ".join(successor_ids)
            )
    for predecessor_id, reservation_ids in sorted(reservations_by_predecessor.items()):
        if len(reservation_ids) > 1:
            messages.append(
                f"events.jsonl: provider session predecessor {predecessor_id!r} has multiple "
                "resume reservations: " + ", ".join(reservation_ids)
            )
    return messages


def run_event_messages(run: Path) -> list[str]:
    """Return malformed sequence and transition diagnostics."""

    messages: list[str] = []
    path = run / RUN_EVENTS_FILENAME
    if not path.is_file():
        if run_requires_event_integrity_v2(run):
            messages.append("events.jsonl: required for runs created by cross-agent-consensus 0.10.0 or later")
        return messages
    event_lines = _nonempty_event_lines(path)
    if not event_lines:
        messages.append("events.jsonl: journal must contain run_initialized")
        return messages
    parsed_events: list[dict[str, Any] | None] = []
    for expected_sequence, line in enumerate(event_lines, 1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            messages.append(f"events.jsonl:{expected_sequence}: invalid JSON: {exc.msg}")
            parsed_events.append(None)
            continue
        if not isinstance(event, dict):
            messages.append(f"events.jsonl:{expected_sequence}: event must be a JSON object")
            parsed_events.append(None)
            continue
        parsed_events.append(event)
        if event.get("schema_version") not in {LEGACY_RUN_EVENT_SCHEMA, RUN_EVENT_SCHEMA}:
            messages.append(f"events.jsonl:{expected_sequence}: unsupported schema_version")
        if event.get("sequence") != expected_sequence:
            messages.append(
                f"events.jsonl:{expected_sequence}: sequence must be {expected_sequence}, "
                f"got {event.get('sequence')!r}"
            )
        if not event.get("event_type"):
            messages.append(f"events.jsonl:{expected_sequence}: event_type is required")
        if not event.get("phase_before") or not event.get("phase_after"):
            messages.append(f"events.jsonl:{expected_sequence}: phase_before and phase_after are required")
            continue
        phase_before = str(event["phase_before"])
        phase_after = str(event["phase_after"])
        if phase_after not in RUN_PHASE_TRANSITIONS.get(phase_before, set()):
            messages.append(
                f"events.jsonl:{expected_sequence}: invalid run phase transition "
                f"{phase_before} -> {phase_after}"
            )
        if expected_sequence > 1:
            previous = parsed_events[expected_sequence - 2]
            if previous is not None and previous.get("phase_after") != phase_before:
                messages.append(
                    f"events.jsonl:{expected_sequence}: phase_before {phase_before!r} does not match "
                    f"previous phase_after {previous.get('phase_after')!r}"
                )

    valid_events = [event for event in parsed_events if event is not None]
    requires_v2 = run_requires_event_integrity_v2(run)
    if not valid_events or not requires_v2:
        return messages
    if any(event.get("schema_version") != RUN_EVENT_SCHEMA for event in valid_events):
        messages.append("events.jsonl: version-2 journals cannot mix event schema versions")
    if valid_events[0].get("event_type") != "run_initialized":
        messages.append("events.jsonl: first event must be run_initialized")
    previous_digest: str | None = None
    for index, event in enumerate(valid_events, 1):
        if event.get("previous_event_sha256_or_null") != previous_digest:
            messages.append(f"events.jsonl:{index}: previous event sha256 does not match")
        recorded_digest = event.get("event_sha256")
        computed_digest = _event_sha256(event)
        if recorded_digest != computed_digest:
            messages.append(f"events.jsonl:{index}: event sha256 mismatch")
        previous_digest = str(recorded_digest) if recorded_digest else None

    anchor_path = run / RUN_EVENT_ANCHOR_FILENAME
    try:
        anchor = read_json_file(anchor_path)
    except FileNotFoundError:
        messages.append(f"{RUN_EVENT_ANCHOR_FILENAME}: required for version-2 event journals")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        messages.append(f"{RUN_EVENT_ANCHOR_FILENAME}: unreadable anchor: {exc}")
    else:
        if anchor.get("schema_version") != RUN_EVENT_ANCHOR_SCHEMA:
            messages.append(f"{RUN_EVENT_ANCHOR_FILENAME}: unsupported schema_version")
        if anchor.get("run_id") != run.name:
            messages.append(f"{RUN_EVENT_ANCHOR_FILENAME}: run_id mismatch")
        if anchor.get("event_count") != len(event_lines):
            messages.append(f"{RUN_EVENT_ANCHOR_FILENAME}: event_count mismatch")
        if anchor.get("last_event_sha256") != valid_events[-1].get("event_sha256"):
            messages.append(f"{RUN_EVENT_ANCHOR_FILENAME}: last event sha256 mismatch")
        if anchor.get("events_jsonl_sha256") != sha256_file(path):
            messages.append(f"{RUN_EVENT_ANCHOR_FILENAME}: events.jsonl sha256 mismatch")

    final_phase = derive_run_phase(parse_run_records(run))
    recorded_phase = valid_events[-1].get("phase_after")
    run_version = recorded_run_version(run)
    if not journal_phase_matches_records(run_version, recorded_phase, final_phase):
        messages.append(
            "events.jsonl: final phase_after does not match protocol records: "
            f"{recorded_phase!r} != {final_phase!r}"
        )
    messages.extend(execution_attempt_event_messages(valid_events))
    messages.extend(provider_session_event_messages(valid_events))
    return messages
