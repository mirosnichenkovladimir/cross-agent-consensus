"""Skeleton report.md generator with pre-wired TerminationRecord / FinalReport frontmatter."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from cross_agent_consensus.io import atomic_write_new, eprint, utc_now
from cross_agent_consensus.layout import FEEDBACK_FILENAME, REPORT_FILENAME
from cross_agent_consensus.markdown_records import frontmatter
from cross_agent_consensus.models import Record
from cross_agent_consensus.records import first_record, parse_run_records, records_by_type
from cross_agent_consensus.run_audit import locked_run_command
from cross_agent_consensus.termination import field_value
from cross_agent_consensus.validation import unresolved_blockers, validator_status


FEEDBACK_SECTIONS: tuple[str, ...] = (
    "Performance anomalies",
    "Critical errors",
    "Small bugs / rough edges",
    "Logic gaps (cases the skill does not cover well)",
)
FEEDBACK_EMPTY_PLACEHOLDER = "_none_"


_TERMINATION_ID = "termination-001"
_FINAL_REPORT_ID = "final-report-001"
_ESCALATION_ID = "escalation-report-001"


def _unresolved_blocking_normalized_findings(records: list[Record]) -> list[Record]:
    unresolved_ids = set(unresolved_blockers(records))
    result: list[Record] = []
    for record in records_by_type(records, "NormalizedFinding"):
        data = record.data
        if data.get("normalized_finding_id") in unresolved_ids:
            result.append(record)
    return result


def _blocker_section(index: int, record: Record) -> list[str]:
    data = record.data
    finding_id = field_value(data.get("normalized_finding_id"), empty="unknown")
    return [
        f"### Blocker {index} — {finding_id}",
        "",
        "Problem:",
        field_value(data.get("claim"), empty="# TODO: problem statement"),
        "",
        "Explanation:",
        field_value(data.get("rationale_or_summary"), empty="# TODO: explanation"),
        "",
        "Required action:",
        field_value(data.get("suggested_fix_or_null"), empty="# TODO: required action"),
        "",
    ]


def _supporting_record_ids(
    records: list[Record],
    blocking_normalized_ids: list[str],
    include_escalation: bool,
) -> list[str]:
    supporting: list[str] = list(blocking_normalized_ids)
    for record_type in ["EscalationRecord", "HumanDecision", "AbortRecord"]:
        for record in records_by_type(records, record_type):
            if record.record_id and record.record_id not in supporting:
                supporting.append(record.record_id)
    if include_escalation and _ESCALATION_ID not in supporting:
        supporting.append(_ESCALATION_ID)
    return supporting


def build_report_skeleton(
    run: Path,
    terminal_condition: str,
    final_artifact_version: str | None,
    *,
    actor: str = "orchestrator-consensus-tool",
) -> str:
    """Render report.md skeleton with TerminationRecord + FinalReport frontmatter."""
    records = parse_run_records(run)
    created_at = utc_now()
    status = validator_status(records)
    unresolved = unresolved_blockers(records)
    blocking = _unresolved_blocking_normalized_findings(records)
    blocking_ids = [str(record.data.get("normalized_finding_id")) for record in blocking if record.data.get("normalized_finding_id")]
    include_escalation = terminal_condition == "escalated_to_human"
    unresolved_for_termination: list[str] = list(unresolved)
    if terminal_condition in {"escalated_to_human", "round_limit_reached"} and not unresolved_for_termination:
        unresolved_for_termination = list(blocking_ids)
    supporting = _supporting_record_ids(records, blocking_ids, include_escalation)

    lines: list[str] = ["# Report", "", "## Results", ""]
    if blocking:
        for index, record in enumerate(blocking, 1):
            lines.extend(_blocker_section(index, record))
    else:
        lines.extend(["No in-scope blocking normalized findings recorded.", ""])

    lines.extend(
        [
            "## Summary",
            "",
            f"- terminal condition: `{terminal_condition}`",
            f"- final artifact version: `{final_artifact_version}`",
            f"- unresolved NormalizedFinding ids: `{unresolved_for_termination if unresolved_for_termination else []}`",
            f"- validators: `{status}`",
            "",
        ]
    )

    if include_escalation:
        affected = unresolved_for_termination or blocking_ids
        lines.extend(
            [
                f"## EscalationRecord {_ESCALATION_ID}",
                frontmatter(
                    {
                        "record_type": "EscalationRecord",
                        "schema_version": "m2-markdown-2",
                        "run_id": run.name,
                        "actor_identity": actor,
                        "created_at": created_at,
                        "escalation_record_id": _ESCALATION_ID,
                        "affected_finding_ids": affected,
                        "reason": "# TODO: describe escalation rationale",
                        "requested_authority": "# TODO: name human authority",
                    }
                ),
                "",
                "### Escalation Notes",
                "",
                "- Requested decision: # TODO",
                "- Supporting evidence: # TODO",
                "",
            ]
        )

    lines.extend(
        [
            f"## TerminationRecord {_TERMINATION_ID}",
            frontmatter(
                {
                    "record_type": "TerminationRecord",
                    "schema_version": "m2-markdown-2",
                    "run_id": run.name,
                    "actor_identity": actor,
                    "created_at": created_at,
                    "termination_record_id": _TERMINATION_ID,
                    "terminal_condition": terminal_condition,
                    "reason": "# TODO: capture terminal reason",
                    "final_artifact_version_id_or_null": final_artifact_version,
                    "unresolved_finding_ids": unresolved_for_termination,
                    "supporting_record_ids": supporting,
                }
            ),
            "",
            "### Termination Notes",
            "",
            "- Consensus predicate result: # TODO",
            "- Round limit result: # TODO",
            "- Human decision or abort support: # TODO",
            "",
            f"## FinalReport {_FINAL_REPORT_ID}",
            frontmatter(
                {
                    "record_type": "FinalReport",
                    "schema_version": "m2-markdown-2",
                    "run_id": run.name,
                    "actor_identity": actor,
                    "created_at": created_at,
                    "final_report_id": _FINAL_REPORT_ID,
                    "termination_record_id": _TERMINATION_ID,
                    "terminal_condition": terminal_condition,
                    "final_artifact_version_id_or_null": final_artifact_version,
                    "validator_status": status,
                    "unresolved_finding_ids": unresolved_for_termination,
                    "backlog_path": "backlog.md",
                }
            ),
            "",
            "### Findings Summary By State",
            "",
            "See `#results` above. Edit before running `consensus terminate`.",
            "",
        ]
    )
    return "\n".join(lines)


def feedback_enabled_for_run(records: list[Record]) -> bool:
    """Read `feedback.enabled` from the run's ConfigResolution record.

    Returns False when there is no ConfigResolution, the field is absent,
    or the value is not truthy. The feedback artifact is opt-in.
    """
    resolution = first_record(records, "ConfigResolution")
    if resolution is None:
        return False
    effective = resolution.data.get("effective_values") or {}
    if not isinstance(effective, dict):
        return False
    entry = effective.get("feedback.enabled")
    if isinstance(entry, dict):
        value = entry.get("value")
    else:
        value = entry
    return bool(value)


def build_feedback_skeleton(run_id: str) -> str:
    """Render the empty feedback skeleton with the four fixed H2 sections."""
    lines: list[str] = [f"# CAC run feedback — {run_id}", ""]
    for section in FEEDBACK_SECTIONS:
        lines.extend([f"## {section}", "", f"- {FEEDBACK_EMPTY_PLACEHOLDER}", ""])
    return "\n".join(lines)


def _write_feedback_skeleton(run: Path, *, overwrite: bool) -> Path | None:
    """Write the feedback skeleton to `runs/<run_dir>/cac-run-feedback.md`.

    Returns the target path when written, None when the file already exists
    and `overwrite` is False (existing operator content is preserved).
    """
    target = run / FEEDBACK_FILENAME
    if target.exists() and not overwrite:
        return None
    body = build_feedback_skeleton(run.name)
    if target.exists() and overwrite:
        target.unlink()
    atomic_write_new(target, body)
    return target


@locked_run_command("report_skeleton_written")
def cmd_report(args: argparse.Namespace) -> int:
    run = Path(args.run)
    try:
        body = build_report_skeleton(
            run,
            args.terminal_condition,
            args.final_artifact_version,
            actor=args.actor,
        )
        target = run / REPORT_FILENAME
        if target.exists() and args.overwrite:
            target.unlink()
        atomic_write_new(target, body)
        print(f"wrote report skeleton: {target}")
        records = parse_run_records(run)
        if feedback_enabled_for_run(records):
            feedback_target = _write_feedback_skeleton(run, overwrite=args.overwrite)
            if feedback_target is not None:
                print(f"wrote feedback skeleton: {feedback_target}")
            else:
                print(f"feedback skeleton already exists: {run / FEEDBACK_FILENAME}")
        return 0
    except FileExistsError as exc:
        eprint(f"error: {exc}")
        eprint("hint: pass --overwrite to replace the existing report.md")
        return 1
    except Exception as exc:
        eprint(f"error: {exc}")
        return 1
