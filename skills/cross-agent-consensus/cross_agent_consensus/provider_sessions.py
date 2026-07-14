"""Provider-session capture and fail-closed continuation rules."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from cross_agent_consensus import __version__
from cross_agent_consensus.integrity import (
    canonical_json_sha256,
    command_sha256,
    resolved_execution_profile_sha256,
    verified_artifact_sha256,
)
from cross_agent_consensus.io import sha256_file
from cross_agent_consensus.layout import normalize_round_id
from cross_agent_consensus.lifecycle import artifact_chain
from cross_agent_consensus.models import AgentInvocation, Record
from cross_agent_consensus.records import parse_run_records, records_by_type
from cross_agent_consensus.run_audit import (
    append_run_event_locked,
    derive_run_phase,
    read_run_events,
    run_lock,
    run_event_messages,
)


PROVIDER_SESSION_EVENT_TYPE = "provider_session_captured"
PROVIDER_SESSION_RESERVATION_EVENT_TYPE = "provider_session_resume_reserved"
DEFINITION_DRIFT_RESOLUTIONS = (
    "use_recorded_profile",
    "apply_named_migration",
    "authorize_compatibility_rule",
    "start_new_run",
    "abort",
)
_PROTOCOL_VERSION_RE = re.compile(
    r"^- `protocol_version`: `(?P<version>[^`]+)`$", re.MULTILINE
)


def _run_protocol_version(run: Path) -> str | None:
    run_path = run / "run.md"
    if not run_path.is_file():
        return None
    match = _PROTOCOL_VERSION_RE.search(
        run_path.read_text(encoding="utf-8", errors="replace")
    )
    return match.group("version") if match else None


def continuation_definition_sha256(records: list[Record]) -> str:
    """Hash definitions that can change the meaning of a resumed exchange."""

    definition_types = {
        "TaskBrief",
        "Policy",
        "Participants",
        "ReviewScope",
        "ConfigResolution",
    }
    return canonical_json_sha256(
        [
            {
                "record_type": record.record_type,
                "record_id": record.record_id,
                "record": record.data,
            }
            for record in records
            if record.record_type in definition_types
        ]
    )


def artifact_lineage_binding(
    run: Path, records: list[Record]
) -> tuple[str | None, str | None, str | None]:
    """Return current ArtifactVersion id, its chain root id, and current digest."""

    chain = artifact_chain(records)
    if chain.blockers:
        raise ValueError("; ".join(chain.blockers))
    head = chain.head
    if head is None:
        return None, None, None
    artifacts = {
        str(record.data.get("artifact_version_id") or record.record_id): record
        for record in records_by_type(records, "ArtifactVersion")
    }
    current_id = str(head.data.get("artifact_version_id") or head.record_id)
    root_id = current_id
    seen: set[str] = set()
    while root_id not in seen:
        seen.add(root_id)
        predecessor = artifacts[root_id].data.get("predecessor_id_or_null")
        if not isinstance(predecessor, str) or not predecessor:
            break
        root_id = predecessor
    return current_id, root_id, verified_artifact_sha256(run, head)


def provider_session_event(
    run: Path, provider_session_entry_id: str
) -> dict[str, Any] | None:
    for event in read_run_events(run):
        details = event.get("details")
        if (
            event.get("event_type") == PROVIDER_SESSION_EVENT_TYPE
            and isinstance(details, dict)
            and details.get("provider_session_entry_id") == provider_session_entry_id
        ):
            return event
    return None


def provider_session_successor_events(
    run: Path, provider_session_entry_id: str
) -> list[dict[str, Any]]:
    return [
        event
        for event in read_run_events(run)
        if event.get("event_type") == PROVIDER_SESSION_EVENT_TYPE
        and isinstance(event.get("details"), dict)
        and event["details"].get("predecessor_provider_session_entry_id_or_null")
        == provider_session_entry_id
    ]


def provider_session_reservation_event(
    run: Path, reservation_id: str
) -> dict[str, Any] | None:
    for event in read_run_events(run):
        details = event.get("details")
        if (
            event.get("event_type") == PROVIDER_SESSION_RESERVATION_EVENT_TYPE
            and isinstance(details, dict)
            and details.get("provider_session_resume_reservation_id") == reservation_id
        ):
            return event
    return None


def active_provider_session_reservations(
    run: Path, predecessor_entry_id: str
) -> list[dict[str, Any]]:
    events = read_run_events(run)
    consumed = {
        str(event["details"].get("provider_session_resume_reservation_id_or_null"))
        for event in events
        if event.get("event_type") == PROVIDER_SESSION_EVENT_TYPE
        and isinstance(event.get("details"), dict)
        and event["details"].get("provider_session_resume_reservation_id_or_null")
    }
    return [
        event
        for event in events
        if event.get("event_type") == PROVIDER_SESSION_RESERVATION_EVENT_TYPE
        and isinstance(event.get("details"), dict)
        and event["details"].get("predecessor_provider_session_entry_id")
        == predecessor_entry_id
        and event["details"].get("provider_session_resume_reservation_id")
        not in consumed
    ]


def provider_session_reservation_details_locked(
    run: Path,
    invocation: AgentInvocation,
    attempt_id: str,
) -> dict[str, Any] | None:
    """Validate and describe one resume reservation while ``run_lock`` is held."""

    predecessor_id = invocation.resume_provider_session_entry_id
    if predecessor_id is None:
        return None
    predecessor_event = provider_session_event(run, predecessor_id)
    if predecessor_event is None:
        raise ValueError(f"provider session predecessor not found: {predecessor_id}")
    predecessor = predecessor_event.get("details")
    assert isinstance(predecessor, dict)
    if provider_session_successor_events(run, predecessor_id):
        raise ValueError(
            f"provider session predecessor already has a successor: {predecessor_id}"
        )
    active_reservations = active_provider_session_reservations(run, predecessor_id)
    if active_reservations:
        reservation_ids = [
            str(event["details"].get("provider_session_resume_reservation_id"))
            for event in active_reservations
            if isinstance(event.get("details"), dict)
        ]
        raise ValueError(
            f"provider session predecessor already has an active resume reservation: "
            + ", ".join(reservation_ids)
        )
    stable_values = {
        "provider_session_id": invocation.provider_session_id,
        "participant_identity": invocation.participant_identity,
        "participant_profile_id": invocation.participant_profile_id,
        "execution_profile_id": invocation.execution_profile_id,
        "player_id": invocation.player_id,
        "phase": invocation.phase,
        "artifact_lineage_root_id_or_null": invocation.artifact_lineage_root_id,
    }
    mismatches = [
        field
        for field, current_value in stable_values.items()
        if predecessor.get(field) != current_value
    ]
    if mismatches:
        raise ValueError(
            "provider session predecessor changed before reservation: "
            + ", ".join(sorted(mismatches))
        )
    reservation_id = f"provider-resume-{attempt_id}"
    invocation.provider_session_resume_reservation_id = reservation_id
    return {
        "provider_session_resume_reservation_id": reservation_id,
        "predecessor_provider_session_entry_id": predecessor_id,
        "provider_session_id": invocation.provider_session_id,
        "execution_attempt_id": attempt_id,
        "participant_identity": invocation.participant_identity,
        "participant_profile_id": invocation.participant_profile_id,
        "execution_profile_id": invocation.execution_profile_id,
        "player_id": invocation.player_id,
        "phase": invocation.phase,
        "artifact_lineage_root_id_or_null": invocation.artifact_lineage_root_id,
    }


def append_provider_session_reservation_locked(
    run: Path,
    invocation: AgentInvocation,
    reservation_details: dict[str, Any],
    phase: str,
) -> None:
    """Append a reservation while the caller still holds ``run_lock``."""

    append_run_event_locked(
        run,
        PROVIDER_SESSION_RESERVATION_EVENT_TYPE,
        actor_identity=invocation.participant_identity,
        phase_before=phase,
        phase_after=phase,
        details=reservation_details,
    )


def _append_continuation_decision(
    run: Path,
    *,
    participant_identity: str,
    event_type: str,
    provider_session_entry_id: str,
    reasons: list[str],
    resolution: str | None,
    operator_identity: str | None,
    resolution_reference: str | None,
) -> None:
    with run_lock(run):
        phase = derive_run_phase(parse_run_records(run))
        append_run_event_locked(
            run,
            event_type,
            actor_identity=operator_identity or participant_identity,
            phase_before=phase,
            phase_after=phase,
            details={
                "provider_session_entry_id": provider_session_entry_id,
                "participant_identity": participant_identity,
                "reasons": reasons,
                "definition_drift_resolution_or_null": resolution,
                "operator_identity_or_null": operator_identity,
                "definition_drift_reference_or_null": resolution_reference,
            },
        )


def resolve_provider_session_continuation(
    run: Path,
    records: list[Record],
    *,
    provider_session_entry_id: str,
    participant_identity: str,
    participant_profile_id: str,
    execution_profile_id: str,
    player_id: str,
    phase: str,
    definition_drift_resolution: str | None,
    operator_identity: str | None,
    definition_drift_reference: str | None,
) -> tuple[str, str | None, str, str]:
    """Return provider id, lineage root, definition digest, and accepted resolution."""

    event = provider_session_event(run, provider_session_entry_id)
    if event is None:
        raise ValueError(f"provider session entry not found: {provider_session_entry_id}")
    journal_messages = run_event_messages(run)
    if journal_messages:
        raise ValueError("RunJournal integrity blocks provider resume: " + "; ".join(journal_messages))
    details = event.get("details")
    assert isinstance(details, dict)
    successors = provider_session_successor_events(run, provider_session_entry_id)
    if successors:
        successor_ids = [
            str(successor["details"].get("provider_session_entry_id"))
            for successor in successors
            if isinstance(successor.get("details"), dict)
        ]
        reasons = [
            "provider session entry is not the latest leaf; successor entries: "
            + ", ".join(successor_ids)
        ]
        _append_continuation_decision(
            run,
            participant_identity=participant_identity,
            event_type="provider_session_continuation_rejected",
            provider_session_entry_id=provider_session_entry_id,
            reasons=reasons,
            resolution=definition_drift_resolution,
            operator_identity=operator_identity,
            resolution_reference=definition_drift_reference,
        )
        raise ValueError("provider-session continuation rejected: " + reasons[0])
    source_attempt_id = details.get("execution_attempt_id")
    source_failed = any(
        candidate.get("event_type") == "execution_attempt_failed"
        and isinstance(candidate.get("details"), dict)
        and candidate["details"].get("attempt_id") == source_attempt_id
        for candidate in read_run_events(run)
    )
    if source_failed:
        raise ValueError(
            f"provider session entry belongs to failed execution attempt: {source_attempt_id}"
        )
    current_artifact_id, current_lineage_root, _ = artifact_lineage_binding(run, records)
    current_definition_digest = continuation_definition_sha256(records)
    binding_mismatches: list[str] = []
    expected_values = {
        "participant_identity": participant_identity,
        "participant_profile_id": participant_profile_id,
        "execution_profile_id": execution_profile_id,
        "player_id": player_id,
        "phase": phase,
        "artifact_lineage_root_id_or_null": current_lineage_root,
    }
    for field, current_value in expected_values.items():
        if details.get(field) != current_value:
            binding_mismatches.append(
                f"{field} differs: captured={details.get(field)!r}, current={current_value!r}"
            )
    if details.get("run_id") not in {None, run.name}:
        binding_mismatches.append("run_id differs")
    if current_artifact_id is None:
        binding_mismatches.append("current ArtifactVersion is missing")
    if binding_mismatches:
        _append_continuation_decision(
            run,
            participant_identity=participant_identity,
            event_type="provider_session_continuation_rejected",
            provider_session_entry_id=provider_session_entry_id,
            reasons=binding_mismatches,
            resolution=definition_drift_resolution,
            operator_identity=operator_identity,
            resolution_reference=definition_drift_reference,
        )
        raise ValueError("provider-session continuation rejected: " + "; ".join(binding_mismatches))

    definition_mismatches: list[str] = []
    if details.get("continuation_definition_sha256") != current_definition_digest:
        definition_mismatches.append("protocol definition digest differs")
    if details.get("package_version") != __version__:
        definition_mismatches.append(
            f"package version differs: captured={details.get('package_version')!r}, current={__version__!r}"
        )
    current_profile_digest = resolved_execution_profile_sha256(records, execution_profile_id)
    if details.get("execution_profile_sha256_or_null") != current_profile_digest:
        definition_mismatches.append("ExecutionProfile digest differs")
    if details.get("protocol_version_or_null") != _run_protocol_version(run):
        definition_mismatches.append("protocol version differs")

    accepted_resolution = "definitions_unchanged"
    if definition_mismatches:
        accepting_resolutions = {
            "use_recorded_profile",
            "apply_named_migration",
            "authorize_compatibility_rule",
        }
        if definition_drift_resolution not in accepting_resolutions:
            _append_continuation_decision(
                run,
                participant_identity=participant_identity,
                event_type="provider_session_continuation_rejected",
                provider_session_entry_id=provider_session_entry_id,
                reasons=definition_mismatches,
                resolution=definition_drift_resolution,
                operator_identity=operator_identity,
                resolution_reference=definition_drift_reference,
            )
            suffix = (
                "; initialize a new run and omit --resume-provider-session-entry"
                if definition_drift_resolution == "start_new_run"
                else ""
            )
            raise ValueError(
                "provider-session definition drift: " + "; ".join(definition_mismatches) + suffix
            )
        if not operator_identity:
            raise ValueError(
                f"--definition-drift-resolution {definition_drift_resolution} requires --operator-identity"
            )
        if definition_drift_resolution in {
            "apply_named_migration",
            "authorize_compatibility_rule",
        } and not definition_drift_reference:
            raise ValueError(
                f"--definition-drift-resolution {definition_drift_resolution} "
                "requires --definition-drift-reference"
            )
        if (
            definition_drift_resolution == "use_recorded_profile"
            and "ExecutionProfile digest differs" in definition_mismatches
        ):
            raise ValueError(
                "use_recorded_profile cannot continue because the recorded ExecutionProfile changed"
            )
        accepted_resolution = str(definition_drift_resolution)
        _append_continuation_decision(
            run,
            participant_identity=participant_identity,
            event_type="provider_session_definition_drift_accepted",
            provider_session_entry_id=provider_session_entry_id,
            reasons=definition_mismatches,
            resolution=definition_drift_resolution,
            operator_identity=operator_identity,
            resolution_reference=definition_drift_reference,
        )
    provider_session_id = details.get("provider_session_id")
    if not isinstance(provider_session_id, str) or not provider_session_id:
        raise ValueError(f"provider session entry lacks provider_session_id: {provider_session_entry_id}")
    return provider_session_id, current_lineage_root, current_definition_digest, accepted_resolution


def capture_provider_session(
    invocation: AgentInvocation,
    *,
    provider_session_id: str,
    effective_command: list[str],
) -> str:
    """Append the provider-session binding before final output is promoted."""

    if not invocation.execution_attempt_id:
        raise ValueError("provider session capture requires execution_attempt_id")
    if invocation.provider_session_id and invocation.provider_session_id != provider_session_id:
        raise ValueError(
            "resumed provider session identifier changed: "
            f"expected {invocation.provider_session_id!r}, received {provider_session_id!r}"
        )
    entry_id = f"provider-session-{invocation.execution_attempt_id}"
    with run_lock(invocation.run):
        records = parse_run_records(invocation.run)
        artifact_id, lineage_root, artifact_sha = artifact_lineage_binding(invocation.run, records)
        definition_digest = continuation_definition_sha256(records)
        phase = derive_run_phase(records)
        if provider_session_event(invocation.run, entry_id) is not None:
            raise ValueError(f"duplicate provider session entry: {entry_id}")
        predecessor = invocation.resume_provider_session_entry_id
        if predecessor is not None and provider_session_event(invocation.run, predecessor) is None:
            raise ValueError(f"provider session predecessor not found: {predecessor}")
        if predecessor is not None:
            successors = provider_session_successor_events(invocation.run, predecessor)
            if successors:
                raise ValueError(
                    f"provider session predecessor already has a successor: {predecessor}"
                )
            reservation_id = invocation.provider_session_resume_reservation_id
            if reservation_id is None:
                raise ValueError("resumed provider session capture requires a resume reservation")
            reservation_event = provider_session_reservation_event(
                invocation.run, reservation_id
            )
            if reservation_event is None:
                raise ValueError(f"provider resume reservation not found: {reservation_id}")
            reservation = reservation_event.get("details")
            assert isinstance(reservation, dict)
            if (
                reservation.get("execution_attempt_id")
                != invocation.execution_attempt_id
                or reservation.get("predecessor_provider_session_entry_id")
                != predecessor
            ):
                raise ValueError("provider resume reservation does not match this execution attempt")
            if any(
                event.get("event_type") == PROVIDER_SESSION_EVENT_TYPE
                and isinstance(event.get("details"), dict)
                and event["details"].get(
                    "provider_session_resume_reservation_id_or_null"
                )
                == reservation_id
                for event in read_run_events(invocation.run)
            ):
                raise ValueError(f"provider resume reservation already consumed: {reservation_id}")
        append_run_event_locked(
            invocation.run,
            PROVIDER_SESSION_EVENT_TYPE,
            actor_identity=invocation.participant_identity,
            phase_before=phase,
            phase_after=phase,
            details={
                "provider_session_entry_id": entry_id,
                "provider_session_id": provider_session_id,
                "predecessor_provider_session_entry_id_or_null": predecessor,
                "provider_session_resume_reservation_id_or_null": (
                    invocation.provider_session_resume_reservation_id
                ),
                "run_id": invocation.run.name,
                "cac_session_id": invocation.session_id,
                "execution_attempt_id": invocation.execution_attempt_id,
                "participant_identity": invocation.participant_identity,
                "participant_profile_id": invocation.participant_profile_id,
                "execution_profile_id": invocation.execution_profile_id,
                "execution_profile_sha256_or_null": resolved_execution_profile_sha256(
                    records, invocation.execution_profile_id
                ),
                "player_id": invocation.player_id,
                "phase": invocation.phase,
                "round_id": normalize_round_id(invocation.round_id),
                "artifact_version_id_or_null": artifact_id,
                "artifact_lineage_root_id_or_null": lineage_root,
                "artifact_sha256_or_null": artifact_sha,
                "continuation_definition_sha256": definition_digest,
                "definition_drift_resolution": (
                    invocation.provider_session_definition_resolution
                    or "fresh_provider_session"
                ),
                "package_version": __version__,
                "protocol_version_or_null": _run_protocol_version(invocation.run),
                "effective_command_sha256": command_sha256(effective_command),
                "prompt_sha256": sha256_file(invocation.prompt_path),
            },
        )
    invocation.provider_session_id = provider_session_id
    invocation.artifact_lineage_root_id = lineage_root
    invocation.continuation_definition_sha256 = definition_digest
    return entry_id
