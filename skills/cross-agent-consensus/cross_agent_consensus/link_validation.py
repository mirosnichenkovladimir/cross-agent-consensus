"""Cross-record reference validation for CAC run snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cross_agent_consensus.layout import record_path_round_number, record_round_number, round_id_from_number
from cross_agent_consensus.models import Record
from cross_agent_consensus.records import first_record, records_by_type


RUN_SCOPE_SENTINEL = "__run_scope__"


@dataclass
class LinkIndex:
    artifacts: set[Any]
    scopes: set[Any]
    batches: set[Any]
    raw_findings: set[Any]
    normalizations: set[Any]
    canonical_findings: set[Any]
    termination_records: set[Any]
    validators: set[str]
    reviewers: set[Any]
    batch_rounds: dict[str, int] = field(default_factory=dict)


def build_link_index(records: list[Record], validators: list[str]) -> LinkIndex:
    participants = first_record(records, "Participants")
    reviewers = set(participants.data.get("reviewer_identities") or []) if participants else set()
    return LinkIndex(
        artifacts={record.data.get("artifact_version_id") for record in records_by_type(records, "ArtifactVersion")},
        scopes={record.data.get("review_scope_id") for record in records_by_type(records, "ReviewScope")},
        batches={record.data.get("review_batch_id") for record in records_by_type(records, "ReviewBatch")},
        raw_findings={record.data.get("raw_finding_id") for record in records_by_type(records, "RawFinding")},
        normalizations={
            record.data.get("normalization_record_id")
            for record in records_by_type(records, "NormalizationRecord")
        },
        canonical_findings={
            record.data.get("canonical_finding_id")
            for record in records_by_type(records, "CanonicalFinding")
        },
        termination_records={
            record.data.get("termination_record_id")
            for record in records_by_type(records, "TerminationRecord")
        },
        validators=set(validators),
        reviewers=reviewers,
    )


def _record_prefix(record: Record) -> str:
    return f"{record.path}:{record.heading_line}"


def _review_batch_messages(records: list[Record], index: LinkIndex) -> list[str]:
    messages: list[str] = []
    for record in records_by_type(records, "ReviewBatch"):
        prefix = _record_prefix(record)
        if record.data.get("review_scope_id") not in index.scopes:
            messages.append(f"{prefix}: review_scope_id not found")
        if record.data.get("target_artifact_version_id") not in index.artifacts:
            messages.append(f"{prefix}: target artifact not found")
        try:
            batch_round = record_round_number(record)
            review_batch_id = record.data.get("review_batch_id")
            if review_batch_id:
                index.batch_rounds[str(review_batch_id)] = batch_round
            path_round = record_path_round_number(record.path)
            if path_round is not None and path_round != batch_round:
                messages.append(
                    f"{prefix}: ReviewBatch round_id {round_id_from_number(batch_round)} "
                    f"does not match path round-{path_round:03d}"
                )
        except ValueError as exc:
            messages.append(f"{prefix}: invalid round_id: {exc}")
    return messages


def _record_batch_round_message(record: Record, index: LinkIndex) -> str | None:
    review_batch_id = record.data.get("review_batch_id")
    if not review_batch_id:
        return None
    batch_round = index.batch_rounds.get(str(review_batch_id))
    path_round = record_path_round_number(record.path)
    if batch_round is not None and path_round is not None and path_round != batch_round:
        return (
            f"{_record_prefix(record)}: record path round-{path_round:03d} does not match "
            f"ReviewBatch {review_batch_id} round_id {round_id_from_number(batch_round)}"
        )
    return None


def _review_output_messages(records: list[Record], index: LinkIndex) -> list[str]:
    messages: list[str] = []
    for record_type in ["RawReviewerOutput", "RawFinding"]:
        for record in records_by_type(records, record_type):
            prefix = _record_prefix(record)
            if record.data.get("artifact_version_id") not in index.artifacts:
                messages.append(f"{prefix}: artifact_version_id not found")
            if record.data.get("review_batch_id") not in index.batches:
                messages.append(f"{prefix}: review_batch_id not found")
            round_message = _record_batch_round_message(record, index)
            if round_message:
                messages.append(round_message)
            if index.reviewers and record.data.get("reviewer_identity") not in index.reviewers:
                messages.append(f"{prefix}: reviewer_identity not found in Participants")
            if record_type == "RawReviewerOutput":
                for raw_finding_id in record.data.get("raw_finding_ids") or []:
                    if raw_finding_id not in index.raw_findings:
                        messages.append(f"{prefix}: raw_finding_id not found: {raw_finding_id}")
    return messages


def _normalization_messages(records: list[Record], index: LinkIndex) -> list[str]:
    messages: list[str] = []
    for record in records_by_type(records, "NormalizationRecord"):
        prefix = _record_prefix(record)
        for raw_finding_id in record.data.get("source_raw_finding_ids") or []:
            if raw_finding_id not in index.raw_findings:
                messages.append(f"{prefix}: source raw finding not found: {raw_finding_id}")
        if record.data.get("canonical_finding_id") not in index.canonical_findings:
            messages.append(f"{prefix}: canonical_finding_id not found")
    for record in records_by_type(records, "CanonicalFinding"):
        prefix = _record_prefix(record)
        if record.data.get("target_artifact_version_id") not in index.artifacts:
            messages.append(f"{prefix}: target artifact not found")
        for raw_finding_id in record.data.get("source_raw_finding_ids") or []:
            if raw_finding_id not in index.raw_findings:
                messages.append(f"{prefix}: source raw finding not found: {raw_finding_id}")
        if record.data.get("normalization_record_id") not in index.normalizations:
            messages.append(f"{prefix}: normalization_record_id not found")
    return messages


def _finding_lifecycle_messages(records: list[Record], index: LinkIndex) -> list[str]:
    messages: list[str] = []
    for record_type in ["MaterialityChallenge", "AuthorResponse", "ClarificationRecord", "ReReviewDecision"]:
        for record in records_by_type(records, record_type):
            prefix = _record_prefix(record)
            if record.data.get("canonical_finding_id") not in index.canonical_findings:
                messages.append(f"{prefix}: canonical finding not found")
            if record_type == "AuthorResponse":
                artifact = record.data.get("resulting_artifact_version_id_or_null")
                if artifact and artifact not in index.artifacts:
                    messages.append(f"{prefix}: resulting artifact not found: {artifact}")
            if record_type == "ReReviewDecision":
                if record.data.get("review_batch_id") not in index.batches:
                    messages.append(f"{prefix}: review_batch_id not found")
                round_message = _record_batch_round_message(record, index)
                if round_message:
                    messages.append(round_message)
                artifact = record.data.get("artifact_version_id_or_null")
                if artifact and artifact not in index.artifacts:
                    messages.append(f"{prefix}: artifact_version_id_or_null not found")
                if index.reviewers and record.data.get("reviewer_identity") not in index.reviewers:
                    messages.append(f"{prefix}: reviewer_identity not found in Participants")
    return messages


def _decision_and_evidence_messages(records: list[Record], index: LinkIndex) -> list[str]:
    messages: list[str] = []
    for record in records_by_type(records, "ValidationEvidence"):
        if record.data.get("target_artifact_version_id") not in index.artifacts:
            messages.append(f"{_record_prefix(record)}: target_artifact_version_id not found")
    for record in records_by_type(records, "EscalationRecord"):
        for finding_id in record.data.get("affected_finding_ids") or []:
            if finding_id != RUN_SCOPE_SENTINEL and finding_id not in index.canonical_findings:
                messages.append(f"{_record_prefix(record)}: affected finding not found: {finding_id}")
    for record in records_by_type(records, "HumanDecision"):
        for target_id in record.data.get("affected_finding_ids_or_validator_ids") or []:
            if (
                target_id != RUN_SCOPE_SENTINEL
                and target_id not in index.canonical_findings
                and target_id not in index.validators
            ):
                messages.append(f"{_record_prefix(record)}: affected finding or validator not found: {target_id}")
    return messages


def _terminal_record_messages(run: Path, records: list[Record], index: LinkIndex) -> list[str]:
    messages: list[str] = []
    for record in records_by_type(records, "AbortRecord"):
        prefix = _record_prefix(record)
        artifact = record.data.get("artifact_version_id_or_null")
        if artifact and artifact not in index.artifacts:
            messages.append(f"{prefix}: artifact_version_id_or_null not found")
        for finding_id in record.data.get("unresolved_finding_ids") or []:
            if finding_id not in index.canonical_findings:
                messages.append(f"{prefix}: unresolved finding not found: {finding_id}")
    for record in records_by_type(records, "TerminationRecord"):
        prefix = _record_prefix(record)
        artifact = record.data.get("final_artifact_version_id_or_null")
        if artifact and artifact not in index.artifacts:
            messages.append(f"{prefix}: final artifact not found: {artifact}")
        for finding_id in record.data.get("unresolved_finding_ids") or []:
            if finding_id not in index.canonical_findings:
                messages.append(f"{prefix}: unresolved finding not found: {finding_id}")
    for record in records_by_type(records, "FinalReport"):
        prefix = _record_prefix(record)
        if record.data.get("termination_record_id") not in index.termination_records:
            messages.append(f"{prefix}: termination_record_id not found")
        artifact = record.data.get("final_artifact_version_id_or_null")
        if artifact and artifact not in index.artifacts:
            messages.append(f"{prefix}: final artifact not found: {artifact}")
        for finding_id in record.data.get("unresolved_finding_ids") or []:
            if finding_id not in index.canonical_findings:
                messages.append(f"{prefix}: unresolved finding not found: {finding_id}")
        backlog_path = record.data.get("backlog_path")
        if backlog_path and not (run / str(backlog_path)).exists():
            messages.append(f"{prefix}: backlog_path not found")
    return messages


def collect_link_messages(run: Path, records: list[Record], validators: list[str]) -> list[str]:
    index = build_link_index(records, validators)
    messages: list[str] = []
    for record in records_by_type(records, "ArtifactVersion"):
        predecessor = record.data.get("predecessor_id_or_null")
        if predecessor and predecessor not in index.artifacts:
            messages.append(f"{_record_prefix(record)}: predecessor artifact not found: {predecessor}")
    messages.extend(_review_batch_messages(records, index))
    messages.extend(_review_output_messages(records, index))
    messages.extend(_normalization_messages(records, index))
    messages.extend(_finding_lifecycle_messages(records, index))
    messages.extend(_decision_and_evidence_messages(records, index))
    messages.extend(_terminal_record_messages(run, records, index))
    return messages
