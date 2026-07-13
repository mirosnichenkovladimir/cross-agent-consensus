"""Prompt command policy and file-writing boundary."""

from __future__ import annotations

from pathlib import Path

from cross_agent_consensus.io import append_text, atomic_write_new, eprint, utc_now
from cross_agent_consensus.markdown_records import frontmatter
from cross_agent_consensus.models import CheckResult, PromptCommandInput, Record
from cross_agent_consensus.prompts import build_prompt, prompt_target, resolve_active_round, select_review_batch
from cross_agent_consensus.records import first_record, parse_run_records, records_by_type
from cross_agent_consensus.validation import check_links, check_pre_execution, check_records, remediation_cap_blockers


RemediationBlocker = tuple[str, str | None, int, str, int]


def _print_check(name: str, check: CheckResult) -> None:
    status = "PASS" if check.ok else "FAIL"
    print(f"{status} {name}")
    for message in check.messages:
        print(f"  - {message}")


def rereview_finding_ids(
    records: list[Record],
    review_batch: Record | None,
    explicit_finding_ids: list[str],
) -> list[str]:
    if explicit_finding_ids:
        return [str(finding_id) for finding_id in explicit_finding_ids]
    if review_batch is not None:
        source_finding_ids = review_batch.data.get("source_finding_ids")
        if isinstance(source_finding_ids, list) and source_finding_ids:
            return [str(finding_id) for finding_id in source_finding_ids]
    return [
        str(record.data.get("canonical_finding_id"))
        for record in records_by_type(records, "CanonicalFinding")
        if record.data.get("canonical_finding_id")
    ]


def remediation_blocker_message(blocker: RemediationBlocker) -> str:
    finding_id, reviewer, attempts, latest_decision, max_attempts = blocker
    reviewer_part = f" reviewer={reviewer}" if reviewer else ""
    if latest_decision == "needs_human":
        return (
            f"finding {finding_id}{reviewer_part} already has needs_human re-review decision; "
            "record HumanDecision before any further re-review"
        )
    if latest_decision == "no_attempts_allowed":
        return (
            f"finding {finding_id}{reviewer_part} cannot be re-reviewed because "
            "max_remediation_rounds_per_finding is 0"
        )
    return (
        f"finding {finding_id}{reviewer_part} reached max_remediation_rounds_per_finding="
        f"{max_attempts} with latest decision={latest_decision} after {attempts} attempt(s)"
    )


def requested_escalation_authority(records: list[Record]) -> str:
    participants = first_record(records, "Participants")
    if participants is not None:
        value = participants.data.get("human_supervisor_identity_or_null")
        if value and value != "none":
            return str(value)
    task = first_record(records, "TaskBrief")
    if task is not None:
        value = task.data.get("human_supervisor_identity_or_null")
        if value and value != "none":
            return str(value)
    return "human_supervisor_or_policy"


def append_remediation_cap_escalation(
    run: Path,
    records: list[Record],
    blockers: list[RemediationBlocker],
    actor: str | None,
) -> tuple[Path, bool]:
    affected_finding_ids: list[str] = []
    for finding_id, _, _, _, _ in blockers:
        if finding_id not in affected_finding_ids:
            affected_finding_ids.append(finding_id)
    for record in records_by_type(records, "EscalationRecord"):
        reason = str(record.data.get("reason") or "")
        existing = [str(value) for value in record.data.get("affected_finding_ids") or []]
        if reason.startswith("remediation cap reached") and all(
            finding_id in existing for finding_id in affected_finding_ids
        ):
            return record.path, False

    path = run / "escalations.md"
    if not path.exists():
        atomic_write_new(path, "# Escalations And Human Decisions\n")
    next_index = len(records_by_type(records, "EscalationRecord")) + 1
    escalation_id = f"escalation-{next_index:03d}"
    reason = "remediation cap reached for unresolved re-review finding(s): " + ", ".join(affected_finding_ids)
    body = "\n".join(
        [
            "",
            f"## EscalationRecord {escalation_id}",
            frontmatter(
                {
                    "record_type": "EscalationRecord",
                    "schema_version": "m2-markdown-1",
                    "run_id": run.name,
                    "actor_identity": actor or "orchestrator-consensus-tool",
                    "created_at": utc_now(),
                    "escalation_record_id": escalation_id,
                    "affected_finding_ids": affected_finding_ids,
                    "reason": reason,
                    "requested_authority": requested_escalation_authority(records),
                }
            ),
            "",
            "### Escalation Notes",
            "",
            "- Requested decision: terminate_escalated_to_human, require_revision, or revise policy.",
            "- Current blocking state: " + "; ".join(remediation_blocker_message(blocker) for blocker in blockers),
            "",
        ]
    )
    append_text(path, body)
    return path, True


def print_rereview_blockers(
    run: Path,
    records: list[Record],
    blockers: list[RemediationBlocker],
    actor: str | None,
) -> None:
    path, created = append_remediation_cap_escalation(run, records, blockers, actor)
    for blocker in blockers:
        eprint(f"error: {remediation_blocker_message(blocker)}")
    action = "wrote" if created else "existing"
    eprint(f"error: {action} EscalationRecord: {path}")
    eprint("error: stop re-review; record HumanDecision and terminate with terminal_condition=escalated_to_human")


def check_rereview_record_gate(run: Path) -> bool:
    for name, check in [("records", check_records(run)), ("links", check_links(run))]:
        if not check.ok:
            _print_check(name, check)
            return False
    return True


def cmd_prompt(args: PromptCommandInput) -> int:
    run = Path(args.run)
    pre_execution = check_pre_execution(run)
    if not pre_execution.ok and not args.force_draft:
        _print_check("pre-execution", pre_execution)
        return 2
    records = parse_run_records(run)
    try:
        args.round = resolve_active_round(records, args.round, args.review_batch)
        if args.phase == "rereview":
            if not check_rereview_record_gate(run):
                return 2
            review_batch = select_review_batch(records, args.round, args.review_batch)
            findings = rereview_finding_ids(records, review_batch, [])
            blockers = remediation_cap_blockers(records, findings, args.actor)
            if blockers:
                print_rereview_blockers(run, records, blockers, args.actor)
                return 2
        prompt = build_prompt(args, records)
        output = prompt_target(run, args, records)
        if args.force_draft and "draft" not in output.name:
            output = output.with_name(output.stem + "-draft" + output.suffix)
        if args.dry_run:
            status = "would overwrite" if output.exists() else "would write"
            print(f"{status} prompt: {output}")
            print(f"prompt bytes: {len(prompt.encode('utf-8'))}")
            if not pre_execution.ok:
                print("warning: would be a draft because pre-execution validation failed")
            return 0
        atomic_write_new(output, prompt)
    except (FileExistsError, ValueError) as exc:
        eprint(f"error: {exc}")
        return 1
    print(f"wrote prompt: {output}")
    if not pre_execution.ok:
        print("warning: prompt is a draft because pre-execution validation failed")
    return 0
