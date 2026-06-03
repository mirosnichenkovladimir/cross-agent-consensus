"""Termination report generation for cross-agent-consensus runs."""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from cross_agent_consensus.io import utc_now
from cross_agent_consensus.invocation.status import agent_session_state_counts, format_agent_session_state_counts
from cross_agent_consensus.layout import DEFAULT_LAYOUT, REPORT_FILENAME, detect_run_layout
from cross_agent_consensus.markdown_records import frontmatter
from cross_agent_consensus.models import Record
from cross_agent_consensus.records import records_by_type
from cross_agent_consensus.validation import unresolved_blockers, validator_status


def list_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item is not None and str(item)]
    if str(value):
        return [str(value)]
    return []


def compact_text(value: Any, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", "" if value is None else str(value)).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def field_value(value: Any, empty: str = "none") -> str:
    text = compact_text(value, limit=2000)
    return text if text else empty


def raw_finding_index(records: list[Record]) -> dict[str, Record]:
    return {
        str(record.data.get("raw_finding_id")): record
        for record in records_by_type(records, "RawFinding")
        if record.data.get("raw_finding_id")
    }


def source_raw_ids(record: Record) -> list[str]:
    return list_values(record.data.get("source_raw_finding_ids"))


def raw_records_for_finding(record: Record, raw_by_id: dict[str, Record]) -> list[Record]:
    return [raw_by_id[raw_id] for raw_id in source_raw_ids(record) if raw_id in raw_by_id]


def reviewers_for_finding(record: Record, raw_by_id: dict[str, Record]) -> list[str]:
    reviewers = {
        str(raw.data.get("reviewer_identity"))
        for raw in raw_records_for_finding(record, raw_by_id)
        if raw.data.get("reviewer_identity")
    }
    return sorted(reviewers)


def source_summary(record: Record, raw_by_id: dict[str, Record]) -> str:
    parts: list[str] = []
    for raw_id in source_raw_ids(record):
        raw = raw_by_id.get(raw_id)
        if raw is None:
            parts.append(raw_id)
            continue
        location = raw.data.get("location")
        parts.append(f"{raw_id}: {location}" if location else raw_id)
    return "; ".join(parts) if parts else "none"


def source_lines(record: Record, raw_by_id: dict[str, Record]) -> list[str]:
    lines: list[str] = []
    for raw_id in source_raw_ids(record):
        raw = raw_by_id.get(raw_id)
        if raw is None:
            lines.append(f"- {raw_id}")
            continue
        location = raw.data.get("location")
        lines.append(f"- {raw_id}: {location}" if location else f"- {raw_id}")
    return lines or ["- none"]


def level_summary(record: Record, raw_records: list[Record]) -> str:
    data = record.data
    severity = sorted(
        {
            str(raw.data.get("severity_or_materiality_claim"))
            for raw in raw_records
            if raw.data.get("severity_or_materiality_claim")
        }
    )
    parts = [
        str(data.get("blocking_status") or "unknown"),
        str(data.get("materiality") or data.get("materiality_status") or "unknown"),
        str(data.get("scope_classification") or "unknown"),
    ]
    if severity:
        parts.append(", ".join(severity))
    return " / ".join(parts)


def finding_result(record: Record, unresolved: set[str]) -> str:
    data = record.data
    finding_id = str(data.get("canonical_finding_id"))
    if finding_id in unresolved:
        return "unresolved"
    lifecycle = str(data.get("lifecycle_state") or "")
    if lifecycle in {"resolved", "verified", "rejection_accepted", "closed", "closed_non_material"}:
        return lifecycle
    blocking = str(data.get("blocking_status") or "")
    if blocking in {"non_blocking", "deferred"}:
        return blocking
    return lifecycle or "open"


def required_action(record: Record, raw_records: list[Record]) -> str:
    actions: list[str] = []
    direct = record.data.get("required_action") or record.data.get("suggested_fix_or_null")
    if direct:
        actions.append(str(direct))
    for raw in raw_records:
        action = raw.data.get("suggested_fix_or_null")
        if action and str(action) not in actions:
            actions.append(str(action))
    return " ".join(actions) if actions else "No required action recorded."


def result_blocks(records: list[Record], unresolved: list[str]) -> list[str]:
    raw_by_id = raw_finding_index(records)
    unresolved_set = set(unresolved)
    lines: list[str] = []
    canonical = records_by_type(records, "CanonicalFinding")
    if not canonical:
        return ["No canonical findings recorded."]
    for record in canonical:
        data = record.data
        raw_records = raw_records_for_finding(record, raw_by_id)
        reviewers = reviewers_for_finding(record, raw_by_id)
        finding_id = field_value(data.get("canonical_finding_id"), empty="unknown")
        lines.extend(
            [
                f"### {finding_id}: {field_value(data.get('claim'), empty='Untitled finding')}",
                "",
                f"Status: {finding_result(record, unresolved_set)}",
                f"Level: {level_summary(record, raw_records)}",
                f"Found by: {', '.join(reviewers) if reviewers else 'unknown'}",
                "Source:",
                *source_lines(record, raw_by_id),
                "",
                "Problem:",
                field_value(data.get("claim"), empty="No problem statement recorded."),
                "",
                "Explanation:",
                field_value(data.get("rationale_or_summary"), empty="No explanation recorded."),
                "",
                "Required action:",
                required_action(record, raw_records),
                "",
                "---",
                "",
            ]
        )
    return lines


def reviewer_stat_blocks(records: list[Record]) -> list[str]:
    raw_by_id = raw_finding_index(records)
    used_raw_ids = {
        raw_id for record in records_by_type(records, "CanonicalFinding") for raw_id in source_raw_ids(record)
    }
    raw_by_reviewer: dict[str, list[Record]] = defaultdict(list)
    for raw in raw_by_id.values():
        reviewer = str(raw.data.get("reviewer_identity") or "unknown")
        raw_by_reviewer[reviewer].append(raw)

    canonical_by_reviewer: dict[str, set[str]] = defaultdict(set)
    agreed_by_reviewer: dict[str, set[str]] = defaultdict(set)
    for record in records_by_type(records, "CanonicalFinding"):
        finding_id = str(record.data.get("canonical_finding_id"))
        reviewers = reviewers_for_finding(record, raw_by_id)
        for reviewer in reviewers:
            canonical_by_reviewer[reviewer].add(finding_id)
            if len(reviewers) > 1:
                agreed_by_reviewer[reviewer].add(finding_id)

    reviewers = sorted(set(raw_by_reviewer) | set(canonical_by_reviewer))
    if not reviewers:
        return ["No reviewer findings recorded."]
    lines: list[str] = []
    for reviewer in reviewers:
        raw_records = raw_by_reviewer.get(reviewer, [])
        raw_ids = [str(raw.data.get("raw_finding_id")) for raw in raw_records if raw.data.get("raw_finding_id")]
        canonicalized = [raw_id for raw_id in raw_ids if raw_id in used_raw_ids]
        discarded = [raw_id for raw_id in raw_ids if raw_id not in used_raw_ids]
        blocking = sum(1 for raw in raw_records if raw.data.get("blocking_status") in {"blocking", "promoted_by_human"})
        non_blocking = sum(1 for raw in raw_records if raw.data.get("blocking_status") in {"non_blocking", "deferred"})
        lines.extend(
            [
                f"### {reviewer}",
                "",
                f"Raw findings: {len(raw_ids)}",
                f"Canonicalized: {len(canonicalized)}",
                f"Discarded: {len(discarded)}",
                f"Blocking: {blocking}",
                f"Non-blocking: {non_blocking}",
                "Canonical findings: "
                + (", ".join(sorted(canonical_by_reviewer.get(reviewer, set()))) or "none"),
                "Agreed with another reviewer: "
                + (", ".join(sorted(agreed_by_reviewer.get(reviewer, set()))) or "none"),
                "",
            ]
        )
    return lines


def reviewer_agreement_blocks(records: list[Record]) -> list[str]:
    raw_by_id = raw_finding_index(records)
    lines: list[str] = []
    for record in records_by_type(records, "CanonicalFinding"):
        reviewers = reviewers_for_finding(record, raw_by_id)
        if len(reviewers) <= 1:
            continue
        lines.extend(
            [
                f"### {field_value(record.data.get('canonical_finding_id'), empty='unknown')}",
                "",
                f"Reviewers: {', '.join(reviewers)}",
                f"Source raw findings: {', '.join(source_raw_ids(record)) or 'none'}",
                f"Problem: {field_value(record.data.get('claim'), empty='No problem statement recorded.')}",
                "",
            ]
        )
    return lines or ["No multi-reviewer agreement recorded."]


def discarded_finding_blocks(records: list[Record]) -> list[str]:
    raw_by_id = raw_finding_index(records)
    used_raw_ids = {
        raw_id for record in records_by_type(records, "CanonicalFinding") for raw_id in source_raw_ids(record)
    }
    discarded = [raw for raw_id, raw in sorted(raw_by_id.items()) if raw_id not in used_raw_ids]
    if not discarded:
        return ["No raw findings were discarded."]
    lines: list[str] = []
    for raw in discarded:
        data = raw.data
        lines.extend(
            [
                f"### {field_value(data.get('raw_finding_id'), empty='unknown')}: "
                f"{field_value(data.get('claim'), empty='Untitled raw finding')}",
                "",
                f"Reviewer: {field_value(data.get('reviewer_identity'), empty='unknown')}",
                f"Level: {field_value(data.get('severity_or_materiality_claim') or data.get('blocking_status'))}",
                "Reason: not referenced by any CanonicalFinding",
                "",
            ]
        )
    return lines


def terminal_body(
    run: Path,
    terminal_condition: str,
    final_artifact: str | None,
    reason: str,
    records: list[Record],
) -> str:
    created_at = utc_now()
    status = validator_status(records)
    unresolved = unresolved_blockers(records)
    session_counts = agent_session_state_counts(run)
    supporting_record_ids = list(status.keys())
    for record_type in ["EscalationRecord", "HumanDecision", "AbortRecord"]:
        supporting_record_ids.extend(record.record_id for record in records_by_type(records, record_type))
    termination_id = "termination-001"
    final_report_id = "final-report-001"
    layout = detect_run_layout(run)
    init_reference = "`run.md`" if layout == DEFAULT_LAYOUT else "`init.md`"
    batch_reference = "`rounds/round-001/round.md`" if layout == DEFAULT_LAYOUT else "`review-batches.md`"
    validator_lines = [f"- {key}: {value}" for key, value in status.items()] or ["- none"]
    return "\n".join(
        [
            "# Report",
            "",
            "## Results",
            "",
            *result_blocks(records, unresolved),
            "",
            "## Summary",
            "",
            f"- terminal condition: `{terminal_condition}`",
            f"- reason: {reason}",
            f"- final artifact version: `{final_artifact}`",
            f"- unresolved CanonicalFinding ids: `{unresolved if unresolved else []}`",
            f"- validators: `{status}`",
            f"- agent session states: {format_agent_session_state_counts(session_counts)}",
            "",
            "## Reviewer Stats",
            "",
            *reviewer_stat_blocks(records),
            "",
            "## Reviewer Agreement",
            "",
            *reviewer_agreement_blocks(records),
            "",
            "## Discarded Raw Findings",
            "",
            *discarded_finding_blocks(records),
            "",
            "## Validation Evidence",
            "",
            *validator_lines,
            "",
            "## Agent Invocation Summary",
            "",
            f"- session states: {format_agent_session_state_counts(session_counts)}",
            "- failed or missing agent sessions are not reviewer decisions unless a Review or ReReviewDecision record exists.",
            "",
            "## Terminal Outcome",
            "",
            f"- run folder path: `{run}`",
            f"- `terminal_condition`: `{terminal_condition}`",
            f"- `termination_record_id` and `{REPORT_FILENAME}` path: `{termination_id}`, `{run / REPORT_FILENAME}`",
            f"- `final_artifact_version_id_or_null`: `{final_artifact}`",
            f"- final artifact path or null: `{final_artifact}`",
            f"- validator status summary and evidence paths: `{status}`",
            f"- FinalReport section path or anchor: `{REPORT_FILENAME}#finalreport-final-report-001`",
            f"- unresolved CanonicalFinding ids: `{unresolved}`",
            "- backlog location: `backlog.md`",
            "",
            f"## TerminationRecord {termination_id}",
            frontmatter(
                {
                    "record_type": "TerminationRecord",
                    "schema_version": "m2-markdown-1",
                    "run_id": run.name,
                    "actor_identity": "orchestrator-consensus-tool",
                    "created_at": created_at,
                    "termination_record_id": termination_id,
                    "terminal_condition": terminal_condition,
                    "reason": reason,
                    "final_artifact_version_id_or_null": final_artifact,
                    "unresolved_finding_ids": unresolved,
                    "supporting_record_ids": supporting_record_ids,
                }
            ),
            "",
            "### Termination Notes",
            "",
            f"- Consensus predicate result: {terminal_condition == 'consensus_reached'}",
            "- Round limit result:",
            "- Human decision or abort support:",
            "",
            f"## FinalReport {final_report_id}",
            frontmatter(
                {
                    "record_type": "FinalReport",
                    "schema_version": "m2-markdown-1",
                    "run_id": run.name,
                    "actor_identity": "orchestrator-consensus-tool",
                    "created_at": created_at,
                    "final_report_id": final_report_id,
                    "termination_record_id": termination_id,
                    "terminal_condition": terminal_condition,
                    "final_artifact_version_id_or_null": final_artifact,
                    "validator_status": status,
                    "unresolved_finding_ids": unresolved,
                    "backlog_path": "backlog.md",
                }
            ),
            "",
            "### Task",
            "",
            f"See {init_reference} TaskBrief.",
            "",
            "### Participants",
            "",
            f"See {init_reference} Participants.",
            "",
            "### Artifact Versions",
            "",
            f"Final artifact version: `{final_artifact}`.",
            "",
            "### Review Scope And Review Batch Modes",
            "",
            f"See {init_reference} ReviewScope and {batch_reference}.",
            "",
            "### Findings Summary By State",
            "",
            "See `#results`, `#reviewer-stats`, `#reviewer-agreement`, and `#discarded-raw-findings` above.",
            "",
            "### Accepted, Fixed, And Verified Blocking Findings",
            "",
            "See author responses and rereviews, if present.",
            "",
            "### Rejected Findings And Accepted Rejections",
            "",
            "See author responses and rereviews, if present.",
            "",
            "### Disputed Or Escalated Blocking Findings",
            "",
            f"Unresolved or escalated findings: {unresolved if unresolved else 'none'}.",
            "",
            "### Validation Evidence",
            "",
            "\n".join(validator_lines),
            "",
            "### Agent Invocation Summary",
            "",
            f"- session states: {format_agent_session_state_counts(session_counts)}",
            "- failed or missing agent sessions are not reviewer decisions unless a Review or ReReviewDecision record exists.",
            "",
            "### Non-Blocking, Deferred, And Out-Of-Scope Backlog",
            "",
            "See `backlog.md`.",
            "",
            "### Terminal Outcome",
            "",
            f"- run folder path: `{run}`",
            f"- `terminal_condition`: `{terminal_condition}`",
            f"- `termination_record_id` and `{REPORT_FILENAME}` path: `{termination_id}`, `{run / REPORT_FILENAME}`",
            f"- `final_artifact_version_id_or_null`: `{final_artifact}`",
            f"- final artifact path or null: `{final_artifact}`",
            f"- validator status summary and evidence paths: `{status}`",
            f"- FinalReport section path or anchor: `{REPORT_FILENAME}#finalreport-final-report-001`",
            f"- unresolved CanonicalFinding ids: `{unresolved}`",
            "- backlog location: `backlog.md`",
            "",
        ]
    )
