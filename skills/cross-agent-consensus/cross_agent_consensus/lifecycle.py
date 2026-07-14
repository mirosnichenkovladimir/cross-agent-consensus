"""Shared deterministic lifecycle predicates for phase, planning, and validation."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from cross_agent_consensus.models import Record
from cross_agent_consensus.records import first_record, records_by_type


RESOLVING_REREVIEW_DECISIONS = {"verified", "rejection_accepted"}
HUMAN_RESOLVING_DECISIONS = {"mark_resolved", "accept_author_rejection", "mark_non_material"}
_MIN_RECORD_KEY = (dt.datetime.min.replace(tzinfo=dt.timezone.utc), -1)


@dataclass(frozen=True)
class ArtifactChain:
    """The unique ArtifactVersion predecessor chain, or its conflicts."""

    head: Record | None
    blockers: tuple[str, ...]


def protocol_timestamp(value: object) -> dt.datetime:
    """Parse one protocol timestamp as a timezone-aware UTC instant."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp must be a non-empty ISO 8601 string")
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = dt.datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"invalid ISO 8601 timestamp: {value}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"timestamp must include a UTC offset: {value}")
    return parsed.astimezone(dt.timezone.utc)


def record_chronology_key(records: list[Record], record: Record) -> tuple[dt.datetime, int] | None:
    """Order records by UTC instant, then their durable record traversal order."""

    try:
        timestamp = protocol_timestamp(record.data.get("created_at"))
    except ValueError:
        timestamp = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    for index, candidate in enumerate(records):
        if candidate is record:
            return timestamp, index
    return None


def record_follows(records: list[Record], candidate: Record, baseline: Record) -> bool:
    """Return whether candidate is chronologically after baseline."""

    candidate_key = record_chronology_key(records, candidate)
    baseline_key = record_chronology_key(records, baseline)
    return candidate_key is not None and baseline_key is not None and candidate_key > baseline_key


def artifact_chain(records: list[Record]) -> ArtifactChain:
    """Resolve one complete, unbranched ArtifactVersion predecessor chain."""

    artifacts = records_by_type(records, "ArtifactVersion")
    if not artifacts:
        return ArtifactChain(None, ())

    by_id: dict[str, Record] = {}
    duplicate_ids: set[str] = set()
    for artifact in artifacts:
        artifact_id = str(artifact.data.get("artifact_version_id") or artifact.record_id)
        if artifact_id in by_id:
            duplicate_ids.add(artifact_id)
        else:
            by_id[artifact_id] = artifact

    blockers = [
        f"ArtifactVersion conflict: duplicate artifact_version_id {artifact_id}"
        for artifact_id in sorted(duplicate_ids)
    ]
    predecessors: dict[str, str | None] = {}
    children: dict[str, list[str]] = {}
    for artifact_id, artifact in by_id.items():
        predecessor_value = artifact.data.get("predecessor_id_or_null")
        if predecessor_value is None or predecessor_value == "":
            predecessor_id = None
        elif isinstance(predecessor_value, str):
            predecessor_id = predecessor_value
        else:
            predecessor_id = None
            blockers.append(
                f"ArtifactVersion conflict: {artifact_id} predecessor_id_or_null must be a string or null"
            )
        predecessors[artifact_id] = predecessor_id
        if predecessor_id is None:
            continue
        if predecessor_id not in by_id:
            blockers.append(
                f"ArtifactVersion conflict: {artifact_id} references missing predecessor {predecessor_id}"
            )
        children.setdefault(predecessor_id, []).append(artifact_id)
    for predecessor_id, child_ids in sorted(children.items()):
        if len(child_ids) > 1:
            blockers.append(
                f"ArtifactVersion conflict: predecessor {predecessor_id} has branches {', '.join(sorted(child_ids))}"
            )

    referenced = {predecessor_id for predecessor_id in predecessors.values() if predecessor_id is not None}
    head_ids = sorted(set(by_id) - referenced)
    if len(head_ids) != 1:
        rendered = ", ".join(head_ids) if head_ids else "none"
        blockers.append(f"ArtifactVersion conflict: expected one chain head, found {rendered}")
        return ArtifactChain(None, tuple(sorted(set(blockers))))

    head_id = head_ids[0]
    visited: set[str] = set()
    current_id: str | None = head_id
    while current_id is not None and current_id in by_id and current_id not in visited:
        visited.add(current_id)
        current_id = predecessors[current_id]
    if current_id is not None:
        blockers.append(f"ArtifactVersion conflict: predecessor cycle includes {current_id}")
    unvisited = sorted(set(by_id) - visited)
    if unvisited:
        blockers.append(
            f"ArtifactVersion conflict: chain headed by {head_id} does not include {', '.join(unvisited)}"
        )
    unique_blockers = tuple(sorted(set(blockers)))
    return ArtifactChain(None if unique_blockers else by_id[head_id], unique_blockers)


def current_artifact_version_id(records: list[Record]) -> str | None:
    """Return the validated ArtifactVersion predecessor-chain head identifier."""

    head = artifact_chain(records).head
    return str(head.data.get("artifact_version_id") or head.record_id) if head is not None else None


def human_decision_awaits_artifact(records: list[Record], decision: Record) -> bool:
    """Return whether a binding HumanDecision still requires a newer ArtifactVersion."""

    requires_artifact = decision.data.get("decision_type") == "require_revision" or bool(
        decision.data.get("requires_new_artifact_version")
    )
    if not requires_artifact:
        return False
    return not any(
        record_follows(records, artifact, decision)
        for artifact in records_by_type(records, "ArtifactVersion")
    )


def expected_reviewers_for_batch(records: list[Record], batch: Record) -> set[str]:
    expected = batch.data.get("expected_reviewer_identities")
    if isinstance(expected, list) and expected:
        return {str(value) for value in expected}
    participants = first_record(records, "Participants")
    if participants is None:
        return set()
    reviewers = participants.data.get("reviewer_identities")
    return {str(value) for value in reviewers} if isinstance(reviewers, list) else set()


def rereview_batch_resolves(expected_reviewers: set[str], decisions: dict[str, str]) -> bool:
    """Require a complete batch with one unanimous resolving conclusion."""

    if not expected_reviewers or not expected_reviewers <= decisions.keys():
        return False
    current = [decisions[reviewer] for reviewer in sorted(expected_reviewers)]
    return len(set(current)) == 1 and current[0] in RESOLVING_REREVIEW_DECISIONS


def latest_rereview_batch_decisions(
    records: list[Record],
) -> dict[str, tuple[str, set[str], dict[str, str]]]:
    """Return the latest complete batch context for each NormalizedFinding id."""

    dispute_decisions = latest_binding_dispute_decisions(records)
    decisions: dict[tuple[str, str, str], str] = {}
    fallback_order: dict[str, int] = {}
    for record in records_by_type(records, "ReReviewDecision"):
        finding_id = record.data.get("normalized_finding_id")
        batch_id = str(record.data.get("review_batch_id") or "__legacy_rereview_batch__")
        reviewer = record.data.get("reviewer_identity")
        decision = record.data.get("decision")
        dispute = dispute_decisions.get(str(finding_id))
        if dispute is not None and not record_follows(records, record, dispute):
            continue
        if finding_id and reviewer and decision:
            decisions[(str(finding_id), batch_id, str(reviewer))] = str(decision)
            fallback_order.setdefault(batch_id, len(fallback_order))

    batch_records = {
        str(record.data.get("review_batch_id")): record
        for record in records_by_type(records, "ReviewBatch")
        if record.data.get("review_batch_id")
    }
    batch_order = {batch_id: index for index, batch_id in enumerate(batch_records)}
    decision_batches: dict[str, set[str]] = {}
    for finding_id, batch_id, _reviewer in decisions:
        decision_batches.setdefault(finding_id, set()).add(batch_id)
    applicable_batches: dict[str, list[str]] = {}
    for batch_id, batch in batch_records.items():
        for finding_id in batch.data.get("source_finding_ids") or []:
            dispute = dispute_decisions.get(str(finding_id))
            if dispute is not None and not record_follows(records, batch, dispute):
                continue
            applicable_batches.setdefault(str(finding_id), []).append(batch_id)

    latest: dict[str, tuple[str, set[str], dict[str, str]]] = {}
    for finding_id in set(decision_batches) | set(applicable_batches):
        candidates = applicable_batches.get(finding_id, [])
        if candidates:
            latest_batch_id = candidates[-1]
        else:
            latest_batch_id = max(
                decision_batches[finding_id],
                key=lambda batch_id: (
                    batch_id in batch_order,
                    batch_order.get(batch_id, fallback_order.get(batch_id, -1)),
                ),
            )
        batch_decisions = {
            reviewer: decision
            for (decision_finding_id, batch_id, reviewer), decision in decisions.items()
            if decision_finding_id == finding_id and batch_id == latest_batch_id
        }
        selected_batch = batch_records.get(latest_batch_id)
        expected = expected_reviewers_for_batch(records, selected_batch) if selected_batch else set()
        latest[finding_id] = (latest_batch_id, expected or set(batch_decisions), batch_decisions)
    return latest


def latest_rereview_decisions(records: list[Record]) -> dict[tuple[str, str], str]:
    """Return reviewer decisions from each finding's latest ReviewBatch."""

    return {
        (finding_id, reviewer): decision
        for finding_id, (_batch_id, _expected, decisions) in latest_rereview_batch_decisions(records).items()
        for reviewer, decision in decisions.items()
    }


def latest_remediation_batches(records: list[Record]) -> dict[str, Record]:
    dispute_decisions = latest_binding_dispute_decisions(records)
    latest: dict[str, Record] = {}
    for batch in records_by_type(records, "ReviewBatch"):
        if batch.data.get("review_mode") != "remediation_verification":
            continue
        for finding_id in batch.data.get("source_finding_ids") or []:
            dispute = dispute_decisions.get(str(finding_id))
            if dispute is not None and not record_follows(records, batch, dispute):
                continue
            latest[str(finding_id)] = batch
    return latest


def rereview_decisions_for_batch(records: list[Record], finding_id: str, batch: Record) -> dict[str, str]:
    batch_id = str(batch.data.get("review_batch_id") or batch.record_id)
    return {
        str(decision.data.get("reviewer_identity")): str(decision.data.get("decision"))
        for decision in records_by_type(records, "ReReviewDecision")
        if str(decision.data.get("normalized_finding_id") or "") == finding_id
        and str(decision.data.get("review_batch_id") or "") == batch_id
    }


def binding_human_decisions(records: list[Record]) -> dict[str, list[Record]]:
    """Return HumanDecision records made after each identifier's latest escalation."""

    latest_escalation_keys = {
        affected_id: record_chronology_key(records, escalation)
        for affected_id, escalation in latest_escalations_by_affected_id(records).items()
    }
    binding: dict[str, list[Record]] = {}
    for decision in records_by_type(records, "HumanDecision"):
        decision_key = record_chronology_key(records, decision)
        if decision_key is None:
            continue
        for affected_id in decision.data.get("affected_finding_ids_or_validator_ids") or []:
            finding_id = str(affected_id)
            escalation_key = latest_escalation_keys.get(finding_id)
            if escalation_key is not None and decision_key <= escalation_key:
                continue
            binding.setdefault(finding_id, []).append(decision)
    for finding_id, decisions in binding.items():
        decisions.sort(key=lambda decision: record_chronology_key(records, decision) or _MIN_RECORD_KEY)
    return binding


def latest_binding_human_decisions(records: list[Record]) -> dict[str, Record]:
    """Return the latest binding HumanDecision for each affected identifier."""

    return {
        finding_id: decisions[-1]
        for finding_id, decisions in binding_human_decisions(records).items()
        if decisions
    }


def latest_binding_dispute_decisions(records: list[Record]) -> dict[str, Record]:
    """Keep the latest dispute_materiality boundary despite later non-resolving decisions."""

    latest: dict[str, Record] = {}
    for finding_id, decisions in binding_human_decisions(records).items():
        disputes = [
            decision
            for decision in decisions
            if decision.data.get("decision_type") == "dispute_materiality"
        ]
        if disputes:
            latest[finding_id] = disputes[-1]
    return latest


def open_materiality_dispute_finding_ids(records: list[Record]) -> set[str]:
    """Return findings whose latest materiality dispute has no later resolving decision."""

    open_disputes: set[str] = set()
    for finding_id, dispute in latest_binding_dispute_decisions(records).items():
        resolving_after_dispute = any(
            decision.data.get("decision_type") in HUMAN_RESOLVING_DECISIONS
            and record_follows(records, decision, dispute)
            for decision in binding_human_decisions(records).get(finding_id, [])
        )
        if not resolving_after_dispute:
            open_disputes.add(finding_id)
    return open_disputes


def latest_escalations_by_affected_id(records: list[Record]) -> dict[str, Record]:
    """Return only the newest EscalationRecord for each affected identifier."""

    latest: dict[str, tuple[dt.datetime, int, Record]] = {}
    for escalation in records_by_type(records, "EscalationRecord"):
        escalation_key = record_chronology_key(records, escalation)
        if escalation_key is None:
            continue
        for affected_id in escalation.data.get("affected_finding_ids") or []:
            finding_id = str(affected_id)
            previous = latest.get(finding_id)
            if previous is None or escalation_key >= previous[:2]:
                latest[finding_id] = (*escalation_key, escalation)
    return {finding_id: value[2] for finding_id, value in latest.items()}


def current_author_response_finding_ids(records: list[Record]) -> set[str]:
    """Return findings with an AuthorResponse in the current human-decision epoch."""

    dispute_decisions = latest_binding_dispute_decisions(records)
    responded: set[str] = set()
    for response in records_by_type(records, "AuthorResponse"):
        finding_id = str(response.data.get("normalized_finding_id") or "")
        if not finding_id:
            continue
        dispute = dispute_decisions.get(finding_id)
        if dispute is None or record_follows(records, response, dispute):
            responded.add(finding_id)
    return responded


def rereview_resolved_finding_ids(records: list[Record]) -> set[str]:
    resolved: set[str] = set()
    for finding_id, (_batch_id, expected, decisions) in latest_rereview_batch_decisions(records).items():
        if rereview_batch_resolves(expected, decisions):
            resolved.add(finding_id)
    return resolved


def rereview_unresolved_finding_ids(records: list[Record]) -> set[str]:
    """Return finding ids whose latest remediation batch is not unanimously resolved."""

    unresolved: set[str] = set()
    for finding_id, (_batch_id, expected, decisions) in latest_rereview_batch_decisions(records).items():
        if not rereview_batch_resolves(expected, decisions):
            unresolved.add(finding_id)
    return unresolved


def effective_blocking_finding_ids(records: list[Record]) -> set[str]:
    resolved_states = {"resolved", "verified", "rejection_accepted", "closed"}
    rereview_resolved = rereview_resolved_finding_ids(records)
    rereview_unresolved = rereview_unresolved_finding_ids(records)
    human_decisions = latest_binding_human_decisions(records)
    open_materiality_disputes = open_materiality_dispute_finding_ids(records)
    blockers: set[str] = set()
    for record in records_by_type(records, "NormalizedFinding"):
        data = record.data
        if data.get("scope_classification") != "in_scope":
            continue
        if data.get("blocking_status") not in {"blocking", "promoted_by_human"}:
            continue
        finding_id = str(data.get("normalized_finding_id") or "")
        human = human_decisions.get(finding_id)
        human_decision_type = human.data.get("decision_type") if human else None
        human_reopens_finding = finding_id in open_materiality_disputes
        pending_human_artifact = human is not None and human_decision_awaits_artifact(records, human)
        if data.get("materiality") == "non_material" and not human_reopens_finding and not pending_human_artifact:
            continue
        if (
            data.get("lifecycle_state") in resolved_states
            and finding_id not in rereview_unresolved
            and not human_reopens_finding
            and not pending_human_artifact
        ):
            continue
        if not finding_id:
            continue
        if finding_id in rereview_resolved and not human_reopens_finding and not pending_human_artifact:
            continue
        if (
            human is not None
            and human_decision_type in HUMAN_RESOLVING_DECISIONS
            and not pending_human_artifact
        ):
            continue
        blockers.add(finding_id)
    return blockers
