"""Canonical CLI implementation for the cross-agent-consensus skill package."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

SKILL_ROOT_FOR_IMPORTS = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT_FOR_IMPORTS) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT_FOR_IMPORTS))

from cross_agent_consensus.io import (
    append_text,
    atomic_write_new,
    eprint,
    hash_locator,
    read_cac_version,
    safe_relative_path,
    slugify,
    utc_now,
)
from cross_agent_consensus.init import build_init_files
from cross_agent_consensus.normalize import cmd_normalize
from cross_agent_consensus.record_schema import ENUMS
from cross_agent_consensus.report import cmd_report
from cross_agent_consensus.config import (
    CONFIG_SCHEMA_VERSION,
    apply_config_to_init_args,
    config_resolution_record,
    find_project_config,
    init_cli_config,
    resolve_config,
    validate_config_shape,
)
from cross_agent_consensus.capture import (
    append_reviewer_capture,
    append_validator_capture,
    copy_raw_payload,
    reviewer_capture_exists,
)
from cross_agent_consensus.models import (
    CONCLUSION_VALIDATION_BATCH_PURPOSE,
    CONCLUSION_VALIDATION_REVIEW_MODE,
    CheckResult,
    ConfigResolution,
)
from cross_agent_consensus.markdown_records import (
    frontmatter,
    parse_records_from_file,
    render_yaml,
)
from cross_agent_consensus.layout import (
    DEFAULT_LAYOUT,
    REPORT_FILENAME,
    detect_run_layout,
    make_run_tree,
    round_dir,
    round_id_from_number,
    round_number,
)
from cross_agent_consensus.records import (
    canonical_finding_ids,
    first_record,
    parse_run_records,
    records_by_type,
)
from cross_agent_consensus.prompts import (
    active_review_batches,
    build_prompt,
    prompt_target,
    review_batch_by_id,
    resolve_active_round,
    select_artifact,
    select_review_batch,
)
from cross_agent_consensus.run_store import run_id_from_task
from cross_agent_consensus.termination import terminal_body
from cross_agent_consensus.validation import (
    check_links,
    check_participants,
    check_pre_execution,
    check_records,
    check_reviewer_isolation,
    check_terminal,
    check_terminal_records,
    pending_conclusion_validation_batches,
    remediation_cap_blockers,
    required_validators,
    rereview_attempt_counts,
    validator_status,
)
from cross_agent_consensus.invocation.adapters import cmd_players_probe
from cross_agent_consensus.invocation.process_monitor import (
    DEFAULT_CANCEL_GRACE_SECONDS,
    DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    DEFAULT_IDLE_TIMEOUT_SECONDS,
    DEFAULT_STALE_TIMEOUT_SECONDS,
    cmd_agent_cancel,
    cmd_invoke_agent,
)
from cross_agent_consensus.invocation.readiness import (
    INVOCATION_READY_BOUNDARY_WARNING,
    cmd_invocation_ready,
    normalize_command_separator,
)
from cross_agent_consensus.invocation.peek import cmd_agent_peek
from cross_agent_consensus.invocation.selftest import cmd_selftest
from cross_agent_consensus.invocation.session_paths import FINAL_OUTPUT_MIRROR_SUFFIX
from cross_agent_consensus.invocation.status import (
    agent_session_state_counts,
    cmd_agent_status,
    cmd_agent_watch,
    format_agent_session_state_counts,
)

CAPTURE_BOUNDARY_WARNING = (
    "capture records output that already exists. It does not start, supervise, or monitor an agent "
    "process and it does not create rounds/<round>/agents/<actor>/session-* telemetry. Use "
    "invoke-agent when live status, heartbeats, cancellation, stdout/stderr stream files, "
    "events.jsonl, state.json, exit.json, or final-output extraction are required."
)


CAC_VERSION = read_cac_version()


def cmd_init(args: argparse.Namespace) -> int:
    try:
        cli_config = init_cli_config(args)
        resolution, task_data = resolve_config(
            cwd=Path.cwd(),
            explicit_config=args.config,
            no_config=args.no_config,
            task_file=args.task_file,
            cli_config=cli_config,
            allow_reviewer_config_override=args.allow_reviewer_config_override,
            strict=True,
        )
        apply_config_to_init_args(args, resolution, task_data)
        args.config_resolution = resolution
        if resolution.errors:
            for message in resolution.errors:
                eprint(f"error: {message}")
            return 1
        missing: list[str] = []
        for attr, flag in [
            ("task", "--task or --task-file task.objective"),
            ("artifact_locator", "--artifact-locator or --task-file task.artifact_locator"),
            ("author", "--author or config participants.author"),
            ("orchestrator", "--orchestrator or config participants.orchestrator"),
            ("reviewer", "--reviewer or config participants.reviewers"),
        ]:
            value = getattr(args, attr, None)
            if value is None or value == "" or value == []:
                missing.append(flag)
        if missing:
            raise ValueError("missing required init input(s): " + ", ".join(missing))
        run_root = Path(args.run_root)
        run_id = args.run_id or run_id_from_task(args.task, run_root)
        safe_relative_path(run_id, "run_id")
        run = run_root / run_id
        if run.exists() and not args.allow_existing and not args.dry_run:
            eprint(f"error: run already exists: {run}")
            return 1
        created_at = utc_now()
        files = build_init_files(args, run_id, created_at)
        if args.dry_run:
            status = "would reuse existing run" if run.exists() else "would create run"
            print(f"{status}: {run}")
            print(f"run_id: {run_id}")
            print(f"layout: {DEFAULT_LAYOUT}")
            print("would write files:")
            for path in sorted(files):
                marker = "exists" if path.exists() else "new"
                print(f"  - [{marker}] {path}")
            if resolution.warnings:
                print("config warnings:")
                for message in resolution.warnings:
                    print(f"  - {message}")
            return 0
        make_run_tree(run, layout=DEFAULT_LAYOUT)
        for path, content in files.items():
            if path.exists() and args.allow_existing:
                continue
            atomic_write_new(path, content)
        print(f"created run: {run}")
        return 0
    except Exception as exc:
        eprint(f"error: {exc}")
        return 1


def print_check(name: str, result: CheckResult) -> None:
    status = "PASS" if result.ok else "FAIL"
    print(f"{status} {name}")
    for message in result.messages:
        print(f"  - {message}")


def cmd_validate(args: argparse.Namespace) -> int:
    run = Path(args.run)
    checks: list[tuple[str, CheckResult]] = []
    run_all = not any(
        [
            args.pre_execution,
            args.records,
            args.links,
            args.reviewer_isolation,
            args.participants,
            args.terminal,
        ]
    )
    if args.pre_execution or run_all:
        checks.append(("pre-execution", check_pre_execution(run)))
    if args.records or run_all:
        checks.append(("records", check_records(run)))
    if args.participants or run_all:
        checks.append(("participants", check_participants(run)))
    if args.links or run_all:
        checks.append(("links", check_links(run)))
    if args.reviewer_isolation or run_all:
        checks.append(("reviewer-isolation", check_reviewer_isolation(run)))
    if args.terminal:
        checks.append(("terminal", check_terminal(run)))
    for name, result in checks:
        print_check(name, result)
    return 0 if all(result.ok for _, result in checks) else 2


def cmd_status(args: argparse.Namespace) -> int:
    run = Path(args.run)
    records = parse_run_records(run)
    print(f"Run: {run}")
    print(f"Exists: {'yes' if run.exists() else 'no'}")
    for record_type in [
        "TaskBrief",
        "Policy",
        "Participants",
        "ReviewScope",
        "ReviewBatch",
        "ArtifactVersion",
        "RawReviewerOutput",
        "CanonicalFinding",
        "AuthorResponse",
        "ReReviewDecision",
        "ValidationEvidence",
        "EscalationRecord",
        "HumanDecision",
        "AbortRecord",
        "TerminationRecord",
        "FinalReport",
    ]:
        count = len(records_by_type(records, record_type))
        print(f"{record_type}: {count}")
    attempt_counts = rereview_attempt_counts(records)
    if attempt_counts:
        print("Re-review attempts:")
        for (finding_id, reviewer_identity), count in sorted(attempt_counts.items()):
            print(f"  - {finding_id} / {reviewer_identity}: {count}")
    session_counts = agent_session_state_counts(run)
    print(f"Agent sessions: {format_agent_session_state_counts(session_counts)}")
    required = required_validators(records)
    status = validator_status(records)
    if required:
        print("Validators:")
        for validator in required:
            print(f"  - {validator}: {status.get(validator, 'pending')}")
    print("Terminal readiness:")
    result = check_terminal(run)
    print(f"  - {'ready' if result.ok else 'not ready'}")
    for message in result.messages:
        print(f"    {message}")
    return 0


def config_command_resolution(args: argparse.Namespace, *, strict: bool) -> tuple[ConfigResolution, dict[str, Any]]:
    cwd = Path(args.cwd).expanduser() if getattr(args, "cwd", None) else Path.cwd()
    return resolve_config(
        cwd=cwd,
        explicit_config=getattr(args, "config", None),
        no_config=getattr(args, "no_config", False),
        task_file=getattr(args, "task_file", None),
        strict=strict,
    )


def cmd_config_show(args: argparse.Namespace) -> int:
    resolution, task_data = config_command_resolution(args, strict=False)
    payload = {
        "sources": resolution.sources,
        "effective": resolution.effective,
        "task_file": task_data,
        "diagnostics": {
            "warnings": resolution.warnings,
            "errors": resolution.errors,
        },
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if not resolution.errors else 1
    print("Config sources:")
    for source in resolution.sources:
        status = "present" if source.get("present") else "missing"
        path = source.get("path") or "null"
        note = f" ({source.get('note')})" if source.get("note") else ""
        print(f"- {source.get('layer')}: {status}: {path}{note}")
    print("")
    print("Effective config:")
    print(render_yaml(resolution.effective) if resolution.effective else "{}")
    if task_data:
        print("")
        print("Task file:")
        print(render_yaml(task_data))
    print("")
    print("Diagnostics:")
    for message in resolution.warnings:
        print(f"- warning: {message}")
    for message in resolution.errors:
        print(f"- error: {message}")
    if not resolution.warnings and not resolution.errors:
        print("- none")
    return 0 if not resolution.errors else 1


def cmd_config_validate(args: argparse.Namespace) -> int:
    resolution, _ = config_command_resolution(args, strict=True)
    for source in resolution.sources:
        status = "present" if source.get("present") else "missing"
        path = source.get("path") or "null"
        print(f"{source.get('layer')}: {status}: {path}")
    if resolution.warnings:
        print("Warnings:")
        for message in resolution.warnings:
            print(f"  - {message}")
    if resolution.errors:
        print("Errors:")
        for message in resolution.errors:
            print(f"  - {message}")
        return 2
    print("Config validation passed")
    return 0


def cmd_config_paths(args: argparse.Namespace) -> int:
    resolution, _ = config_command_resolution(args, strict=False)
    for source in resolution.sources:
        status = "present" if source.get("present") else "missing"
        path = source.get("path") or "null"
        note = f" ({source.get('note')})" if source.get("note") else ""
        print(f"{source.get('layer')}: {status}: {path}{note}")
    return 0 if not resolution.errors else 1


def setup_target_path(args: argparse.Namespace) -> Path:
    if args.output:
        return Path(args.output).expanduser()
    if args.target == "project":
        project_path, _ = find_project_config(Path.cwd())
        if project_path is not None:
            return project_path
        current = Path.cwd().resolve()
        while current.parent != current:
            if (current / ".git").exists():
                return current / ".cross-agent-consensus.yaml"
            current = current.parent
        return Path.cwd() / ".cross-agent-consensus.yaml"
    base = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    return base / "skills" / "cross-agent-consensus" / "config" / "config.local.yaml"


def setup_config_payload() -> dict[str, Any]:
    reviewer_clis: dict[str, Any] = {}
    reviewers: list[str] = []
    if shutil.which("claude"):
        reviewers.append("claude-reviewer")
        reviewer_clis["claude-reviewer"] = {
            "command": [
                "claude",
                "-p",
                "--verbose",
                "--output-format",
                "stream-json",
                "--include-partial-messages",
                "--permission-mode",
                "dontAsk",
                "--no-session-persistence",
            ],
            "prompt_transport": "stdin",
            "stdout_capture": "raw_output",
            "stderr_capture": "raw_error",
        }
    if shutil.which("codex"):
        reviewers.append("codex-independent-reviewer")
        reviewer_clis["codex-independent-reviewer"] = {
            "command": ["codex", "exec", "--json", "-"],
            "prompt_transport": "stdin",
            "stdout_capture": "raw_output",
            "stderr_capture": "raw_error",
        }
    data: dict[str, Any] = {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "defaults": {
            "profile": "document-consensus",
            "run_root": "runs",
            "round_limits": {
                "max_fresh_review_rounds": 1,
                "max_fresh_review_rounds_without_human_approval": 2,
                "max_remediation_rounds_per_finding": 2,
            },
        },
        "participants": {
            "orchestrator": "orchestrator-codex-default",
            "author": "codex-implementer",
            "reviewers": reviewers,
            "human_supervisor": "none",
        },
        "invocation": {
            "require_invocation_ready": True,
            "direct_reviewer_cli": "explicit_only",
            "peek": {
                "interval_seconds": 180,
                "tail": 80,
                "snippet_chars": 160,
                "monitor_stale_seconds": 30,
            },
        },
    }
    if reviewer_clis:
        data["reviewer_clis"] = reviewer_clis
    return data


def cmd_config_setup(args: argparse.Namespace) -> int:
    data = setup_config_payload()
    warnings, errors = validate_config_shape(data, source="setup", persistent=True, strict=True)
    if errors:
        for message in errors:
            eprint(f"error: {message}")
        return 2
    body = render_yaml(data) + "\n"
    if args.dry_run:
        print(body, end="")
        if warnings:
            print("# Warnings:")
            for message in warnings:
                print(f"# - {message}")
        return 0
    target = setup_target_path(args)
    if target.exists():
        if args.yes:
            eprint(f"error: refusing to overwrite existing config with --yes: {target}")
            return 1
        answer = input(f"Config exists at {target}. Overwrite? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            eprint("error: setup aborted")
            return 1
        target.unlink()
    elif not args.yes:
        answer = input(f"Save config to {target}? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            eprint("error: setup aborted")
            return 1
    try:
        atomic_write_new(target, body)
    except FileExistsError as exc:
        eprint(f"error: {exc}")
        return 1
    print(f"wrote config: {target}")
    if warnings:
        print("Warnings:")
        for message in warnings:
            print(f"  - {message}")
    return 0


def rereview_finding_ids(
    records: list[Any],
    review_batch: Any | None,
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


def remediation_blocker_message(blocker: tuple[str, str | None, int, str, int]) -> str:
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


def requested_escalation_authority(records: list[Any]) -> str:
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
    records: list[Any],
    blockers: list[tuple[str, str | None, int, str, int]],
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
    records: list[Any],
    blockers: list[tuple[str, str | None, int, str, int]],
    actor: str | None,
) -> None:
    path, created = append_remediation_cap_escalation(run, records, blockers, actor)
    for blocker in blockers:
        eprint(f"error: {remediation_blocker_message(blocker)}")
    action = "wrote" if created else "existing"
    eprint(f"error: {action} EscalationRecord: {path}")
    eprint("error: stop re-review; record HumanDecision and terminate with terminal_condition=escalated_to_human")


def check_rereview_record_gate(run: Path) -> bool:
    for name, result in [("records", check_records(run)), ("links", check_links(run))]:
        if not result.ok:
            print_check(name, result)
            return False
    return True


def cmd_prompt(args: argparse.Namespace) -> int:
    run = Path(args.run)
    pre = check_pre_execution(run)
    if not pre.ok and not args.force_draft:
        print_check("pre-execution", pre)
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
            if not pre.ok:
                print("warning: would be a draft because pre-execution validation failed")
            return 0
        atomic_write_new(output, prompt)
    except (FileExistsError, ValueError) as exc:
        eprint(f"error: {exc}")
        return 1
    print(f"wrote prompt: {output}")
    if not pre.ok:
        print("warning: prompt is a draft because pre-execution validation failed")
    return 0


def _resolve_single_candidate(flag: str, candidates: list[str], *, hint: str) -> str:
    if len(candidates) == 1:
        value = candidates[0]
        print(f"using {flag} {value} ({hint})")
        return value
    raise ValueError(
        f"reviewer capture requires {flag} "
        f"(found {len(candidates)} candidates: {', '.join(candidates) or 'none'})"
    )


def cmd_capture(args: argparse.Namespace) -> int:
    run = Path(args.run)
    try:
        if args.phase in {"author", "manual"} and not args.no_append_record:
            raise ValueError(f"{args.phase} capture requires --no-append-record for bare payload capture")
        records = parse_run_records(run)
        if args.phase == "reviewer" or args.review_batch:
            args.round = resolve_active_round(records, args.round, args.review_batch)
        else:
            args.round = resolve_existing_round(records, args.round)
        if args.phase == "reviewer" and not args.no_append_record:
            if not args.review_batch:
                args.review_batch = _resolve_single_candidate(
                    "--review-batch",
                    active_review_batches(records, args.round),
                    hint="only active batch",
                )
            if not args.artifact_version:
                artifacts = [
                    str(record.data.get("artifact_version_id"))
                    for record in records_by_type(records, "ArtifactVersion")
                    if record.data.get("artifact_version_id")
                ]
                args.artifact_version = _resolve_single_candidate(
                    "--artifact-version", artifacts, hint="only ArtifactVersion"
                )
            reviewer_identity = args.actor or "reviewer"
            if reviewer_capture_exists(records, reviewer_identity, args.review_batch):
                raise ValueError(
                    f"reviewer output already captured for {reviewer_identity} in ReviewBatch {args.review_batch}"
                )
        raw_path, raw_sha = copy_raw_payload(run, args, records)
        if not args.no_append_record:
            if args.phase == "reviewer":
                append_reviewer_capture(run, args, raw_path, raw_sha, records=records)
            elif args.phase == "validator":
                append_validator_capture(run, args, raw_path, raw_sha)
        print(f"captured raw payload: {raw_path}")
        print(f"sha256: {raw_sha}")
        return 0
    except Exception as exc:
        eprint(f"error: {exc}")
        return 1


def cmd_new_artifact(args: argparse.Namespace) -> int:
    run = Path(args.run)
    try:
        artifact_id = args.artifact_version
        safe_relative_path(f"{artifact_id}.md", "artifact_version")
        path = run / "artifacts" / f"{artifact_id}.md"
        content_hash = hash_locator(args.content_locator, Path.cwd())
        data = {
            "record_type": "ArtifactVersion",
            "schema_version": "m2-markdown-1",
            "run_id": run.name,
            "actor_identity": args.actor,
            "created_at": utc_now(),
            "artifact_version_id": artifact_id,
            "predecessor_id_or_null": args.predecessor,
            "content_locator": args.content_locator,
            "content_hash_or_null": content_hash,
            "produced_by": args.produced_by,
        }
        body = "\n".join(
            [
                frontmatter(data),
                "",
                f"# Artifact Version {artifact_id}",
                "",
                "## Content Or Locator",
                "",
                "```text",
                args.content_locator,
                "```",
                "",
                "## Author Notes",
                "",
                "- Summary of changes:",
                "- Assumptions:",
                "- Known limitations:",
                "",
            ]
        )
        atomic_write_new(path, body)
        print(f"created artifact: {path}")
        return 0
    except Exception as exc:
        eprint(f"error: {exc}")
        return 1


def cmd_response_skeleton(args: argparse.Namespace) -> int:
    run = Path(args.run)
    records = parse_run_records(run)
    findings = []
    for record in records_by_type(records, "CanonicalFinding"):
        data = record.data
        if data.get("scope_classification") == "in_scope" and data.get("blocking_status") == "blocking":
            findings.append(data.get("canonical_finding_id"))
    if args.finding_id:
        findings = args.finding_id
    if not findings:
        print("no in-scope blocking canonical findings found")
        return 0
    pending = pending_conclusion_validation_batches(records, [str(finding_id) for finding_id in findings])
    if pending:
        eprint(
            "error: AuthorResponse is blocked until conclusion-validation output is captured "
            f"or skipped by Policy for ReviewBatch: {', '.join(pending)}"
        )
        return 1
    try:
        args.round = resolve_existing_round(records, args.round)
    except ValueError as exc:
        eprint(f"error: {exc}")
        return 1
    if detect_run_layout(run) == DEFAULT_LAYOUT:
        target = round_dir(run, args.round) / "author-responses.md"
    else:
        target = run / "author-responses" / f"{args.round}.md"
    sections = []
    for index, finding_id in enumerate(findings, 1):
        response_id = f"author-response-{args.round}-{index:03d}"
        sections.extend(
            [
                "",
                f"## AuthorResponse {response_id}",
                "---",
                "record_type: AuthorResponse",
                "schema_version: m2-markdown-1",
                f"run_id: {run.name}",
                f"actor_identity: {args.actor}",
                f"created_at: {utc_now()}",
                f"author_response_id: {response_id}",
                f"canonical_finding_id: {finding_id}",
                "response_type: <accept|reject|partially_accept|request_clarification>",
                "rationale: <required>",
                "resulting_artifact_version_id_or_null: null",
                "clarification_request_or_null: null",
                "---",
                "",
                "### Response Notes",
                "",
            ]
        )
    if not target.exists():
        atomic_write_new(target, f"# Author Responses {args.round}\n")
    append_text(target, "\n".join(sections))
    print(f"wrote response skeleton: {target}")
    return 0


def cmd_rereview_skeleton(args: argparse.Namespace) -> int:
    run = Path(args.run)
    records = parse_run_records(run)
    try:
        args.round = resolve_active_round(records, args.round, args.review_batch)
    except ValueError as exc:
        eprint(f"error: {exc}")
        return 1
    if not check_rereview_record_gate(run):
        return 2
    review_batch = review_batch_by_id(records, args.review_batch)
    findings = rereview_finding_ids(records, review_batch, args.finding_id)
    if not findings:
        print("no canonical findings found")
        return 0
    blockers: list[tuple[str, str | None, int, str, int]] = []
    for reviewer in args.reviewer:
        blockers.extend(remediation_cap_blockers(records, findings, reviewer))
    if blockers:
        print_rereview_blockers(run, records, blockers, args.actor)
        return 2
    for reviewer in args.reviewer:
        if detect_run_layout(run) == DEFAULT_LAYOUT:
            target = round_dir(run, args.round) / "rereviews" / f"{slugify(reviewer)}.md"
        else:
            target = run / "rereviews" / f"{args.round}-{slugify(reviewer)}.md"
        sections = [f"# Re-Review {args.round}: {reviewer}", ""]
        for index, finding_id in enumerate(findings, 1):
            decision_id = f"re-review-{args.round}-{slugify(reviewer)}-{index:03d}"
            sections.extend(
                [
                    f"## ReReviewDecision {decision_id}",
                    "---",
                    "record_type: ReReviewDecision",
                    "schema_version: m2-markdown-1",
                    f"run_id: {run.name}",
                    f"actor_identity: {args.actor}",
                    f"created_at: {utc_now()}",
                    f"re_review_decision_id: {decision_id}",
                    f"canonical_finding_id: {finding_id}",
                    f"reviewer_identity: {reviewer}",
                    "decision: <verified|rejection_accepted|still_valid|disputed|needs_human>",
                    "rationale: <required>",
                    f"artifact_version_id_or_null: {args.artifact_version}",
                    f"review_batch_id: {args.review_batch}",
                    "---",
                    "",
                ]
            )
        atomic_write_new(target, "\n".join(sections))
        print(f"wrote rereview skeleton: {target}")
    return 0


def next_review_batch_id(records: list[Any], round_id: str, mode: str, purpose: str) -> str:
    existing = {str(record.data.get("review_batch_id")) for record in records_by_type(records, "ReviewBatch")}
    base = f"review-batch-{round_id}-{mode}-{purpose}"
    if base not in existing:
        return base
    for index in range(2, 1000):
        candidate = f"{base}-{index:03d}"
        if candidate not in existing:
            return candidate
    raise ValueError(f"unable to allocate ReviewBatch id for {round_id} {mode}")


def participant_reviewers(records: list[Any]) -> list[str]:
    participants = first_record(records, "Participants")
    if participants is None:
        return []
    reviewers = participants.data.get("reviewer_identities")
    return [str(value) for value in reviewers] if isinstance(reviewers, list) else []


def resolve_existing_round(records: list[Any], explicit_round: str | None) -> str:
    if explicit_round is None:
        return resolve_active_round(records, explicit_round)
    wanted = round_number(explicit_round)
    for record in records_by_type(records, "ReviewBatch"):
        try:
            if round_number(str(record.data.get("round_id"))) == wanted:
                return round_id_from_number(wanted)
        except ValueError:
            continue
    raise ValueError(f"no ReviewBatch found for {round_id_from_number(wanted)}")


def cmd_conclusion_validation(args: argparse.Namespace) -> int:
    run = Path(args.run)
    records = parse_run_records(run)
    try:
        args.round = resolve_existing_round(records, args.round)
        scope = first_record(records, "ReviewScope")
        if scope is None:
            raise ValueError("ReviewScope record is required")
        artifact = select_artifact(records, args.artifact_version)
        if artifact is None:
            raise ValueError("ArtifactVersion record is required")
        findings = args.finding_id or [
            str(record.data.get("canonical_finding_id")) for record in records_by_type(records, "CanonicalFinding")
        ]
        findings = [finding_id for finding_id in findings if finding_id]
        if not findings:
            raise ValueError("no CanonicalFinding records found; normalize findings before conclusion validation")
        canonical_ids = canonical_finding_ids(records)
        missing = [finding_id for finding_id in findings if finding_id not in canonical_ids]
        if missing:
            raise ValueError(f"canonical finding id not found: {', '.join(missing)}")
        round_id = args.round
        batch_id = args.review_batch_id or next_review_batch_id(
            records,
            round_id,
            CONCLUSION_VALIDATION_REVIEW_MODE,
            CONCLUSION_VALIDATION_BATCH_PURPOSE,
        )
        if review_batch_by_id(records, batch_id) is not None:
            raise ValueError(f"review_batch_id already exists: {batch_id}")
        safe_relative_path(batch_id, "review_batch_id")
        expected_reviewers = args.reviewer or participant_reviewers(records)
        created_at = utc_now()
        round_path = round_dir(run, args.round)
        round_path.mkdir(parents=True, exist_ok=True)
        round_md = round_path / "round.md"
        if not round_md.exists():
            atomic_write_new(round_md, f"# Round {round_id}\n")
        section = "\n".join(
            [
                "",
                f"## ReviewBatch {batch_id}",
                frontmatter(
                    {
                        "record_type": "ReviewBatch",
                        "schema_version": "m2-markdown-1",
                        "run_id": run.name,
                        "actor_identity": args.actor,
                        "created_at": created_at,
                        "review_batch_id": batch_id,
                        "review_scope_id": scope.data.get("review_scope_id"),
                        "review_mode": CONCLUSION_VALIDATION_REVIEW_MODE,
                        "target_artifact_version_id": artifact.data.get("artifact_version_id"),
                        "source_finding_ids": findings,
                        "round_id": round_id,
                        "round_path": str(round_path.relative_to(run)),
                        "batch_purpose": CONCLUSION_VALIDATION_BATCH_PURPOSE,
                        "expected_reviewer_identities": expected_reviewers,
                    }
                ),
                "",
                "### Conclusion Validation Dispatch Notes",
                "",
                "- Purpose: recalled reviewers validate the normalized canonical superset and proposed conclusions.",
                "- This is not a fresh review; reviewers must not introduce unrelated findings.",
                "- Every reviewer decision must include rationale/argumentation and evidence references.",
                "- AuthorResponse sections should wait until this validation batch is captured or explicitly skipped by policy.",
                "",
            ]
        )
        append_text(round_md, section)

        print(f"created conclusion validation batch: {batch_id}")
        print(f"round path: {round_path}")

        if args.write_prompts:
            prompt_records = parse_run_records(run)
            if not expected_reviewers:
                raise ValueError("--reviewer is required when Participants has no reviewer_identities")
            for reviewer in expected_reviewers:
                prompt_args = argparse.Namespace(
                    run=args.run,
                    phase="reviewer",
                    actor=reviewer,
                    artifact_version=str(artifact.data.get("artifact_version_id")),
                    round=args.round,
                    review_batch=batch_id,
                    output=None,
                    force_draft=False,
                )
                prompt = build_prompt(prompt_args, prompt_records)
                target = prompt_target(run, prompt_args, prompt_records)
                atomic_write_new(target, prompt)
                print(f"wrote prompt: {target}")
        return 0
    except Exception as exc:
        eprint(f"error: {exc}")
        return 1


def cmd_terminate(args: argparse.Namespace) -> int:
    run = Path(args.run)
    records = parse_run_records(run)
    prechecks = [
        ("records", check_records(run)),
        ("links", check_links(run)),
    ]
    for name, result in prechecks:
        if not result.ok:
            print_check(name, result)
            return 2
    artifact_ids = {record.data.get("artifact_version_id") for record in records_by_type(records, "ArtifactVersion")}
    if args.final_artifact_version and args.final_artifact_version not in artifact_ids:
        eprint(f"error: final artifact version not found: {args.final_artifact_version}")
        return 2
    path = run / REPORT_FILENAME
    body = terminal_body(run, args.terminal_condition, args.final_artifact_version, args.reason, records)
    with tempfile.TemporaryDirectory() as tmp_name:
        candidate = Path(tmp_name) / REPORT_FILENAME
        candidate.write_text(body, encoding="utf-8")
        candidate_records = parse_records_from_file(candidate)
        termination = first_record(candidate_records, "TerminationRecord")
        final_report = first_record(candidate_records, "FinalReport")
        terminal_result = check_terminal_records(run, records + candidate_records, termination, final_report)
        if not terminal_result.ok:
            print_check("terminal", terminal_result)
            return 2
    try:
        atomic_write_new(path, body)
    except FileExistsError as exc:
        eprint(f"error: {exc}")
        return 1
    result = check_terminal(run)
    print_check("terminal", result)
    print(f"wrote report: {path}")
    return 0 if result.ok else 2


def add_common_run_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run", required=True, help="Path to runs/<run_id>")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="consensus", description="File-based helpers for cross-agent-consensus runs.")
    parser.add_argument("--version", action="version", version=CAC_VERSION)
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create a canonical run folder and initial records.")
    init.add_argument("--task")
    init.add_argument("--task-file")
    init.add_argument("--config")
    init.add_argument("--no-config", action="store_true")
    init.add_argument("--profile")
    init.add_argument("--artifact-locator")
    init.add_argument("--author")
    init.add_argument("--orchestrator")
    init.add_argument("--reviewer", action="append")
    init.add_argument("--review-focus", action="append")
    init.add_argument("--allow-reviewer-config-override", action="store_true")
    init.add_argument("--human-supervisor")
    init.add_argument("--run-root")
    init.add_argument("--run-id")
    init.add_argument("--success-criterion", action="append")
    init.add_argument("--validator", action="append")
    init.add_argument("--in-scope", action="append")
    init.add_argument("--out-of-scope", action="append")
    init.add_argument("--review-objective")
    init.add_argument("--max-fresh-review-rounds", type=int)
    init.add_argument("--max-fresh-review-rounds-without-human-approval", type=int)
    init.add_argument("--max-remediation-rounds", type=int)
    init.add_argument("--material-by-default", action="append")
    init.add_argument("--non-blocking-by-default", action="append")
    init.add_argument("--escalation-policy", default="escalate only when required by policy or human decision")
    init.add_argument("--waiver-authority", default=None)
    init.add_argument("--promotion-policy", default=None)
    init.add_argument("--unattended-invocation", action="store_true")
    init.add_argument("--unattended-scope", action="append")
    init.add_argument("--allow-existing", action="store_true")
    init.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Resolve config and intended file layout, print them, and exit 0 "
            "without creating directories or writing records."
        ),
    )
    init.set_defaults(func=cmd_init)

    config = sub.add_parser("config", help="Inspect, validate, or write CAC configuration.")
    config_sub = config.add_subparsers(dest="config_command", required=True)

    def add_config_resolution_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--config")
        p.add_argument("--no-config", action="store_true")
        p.add_argument("--task-file")
        p.add_argument("--cwd")

    config_show = config_sub.add_parser("show", help="Show effective layered configuration.")
    add_config_resolution_args(config_show)
    config_show.add_argument("--json", action="store_true")
    config_show.set_defaults(func=cmd_config_show)

    config_validate = config_sub.add_parser("validate", help="Validate discovered configuration.")
    add_config_resolution_args(config_validate)
    config_validate.set_defaults(func=cmd_config_validate)

    config_paths = config_sub.add_parser("paths", help="Show config discovery paths.")
    add_config_resolution_args(config_paths)
    config_paths.set_defaults(func=cmd_config_paths)

    config_setup = config_sub.add_parser("setup", help="Generate a safe local or project config.")
    config_setup.add_argument("--target", choices=["user-local", "project"], default="user-local")
    config_setup.add_argument("--output")
    config_setup.add_argument("--dry-run", action="store_true")
    config_setup.add_argument("--yes", action="store_true")
    config_setup.set_defaults(func=cmd_config_setup)

    status = sub.add_parser("status", help="Show current run state without modifying it.")
    add_common_run_arg(status)
    status.set_defaults(func=cmd_status)

    validate = sub.add_parser("validate", help="Run deterministic conformance checks.")
    add_common_run_arg(validate)
    validate.add_argument("--pre-execution", action="store_true")
    validate.add_argument("--records", action="store_true")
    validate.add_argument("--links", action="store_true")
    validate.add_argument("--reviewer-isolation", action="store_true")
    validate.add_argument("--participants", action="store_true")
    validate.add_argument("--terminal", action="store_true")
    validate.set_defaults(func=cmd_validate)

    prompt = sub.add_parser("prompt", help="Generate an exact prompt payload after pre-execution validation.")
    add_common_run_arg(prompt)
    prompt.add_argument("--phase", required=True, choices=["author", "reviewer", "validator", "author-response", "rereview", "final-report"])
    prompt.add_argument("--actor")
    prompt.add_argument("--artifact-version")
    prompt.add_argument("--round")
    prompt.add_argument("--review-batch")
    prompt.add_argument("--output")
    prompt.add_argument("--force-draft", action="store_true")
    prompt.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve and validate the prompt target without writing; prints the path that would be used.",
    )
    prompt.set_defaults(func=cmd_prompt)

    capture = sub.add_parser(
        "capture",
        help="Capture raw output or evidence into the run folder.",
        description="Capture raw output or evidence into the run folder.",
        epilog=CAPTURE_BOUNDARY_WARNING,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_common_run_arg(capture)
    capture.add_argument("--phase", required=True, choices=["author", "reviewer", "validator", "manual"])
    capture.add_argument("--actor")
    capture.add_argument("--review-batch")
    capture.add_argument("--artifact-version")
    capture.add_argument("--source-file")
    capture.add_argument("--source-mode", default="file")
    capture.add_argument("--source-command")
    capture.add_argument("--provider")
    capture.add_argument("--round")
    capture.add_argument("--validator-id")
    capture.add_argument("--result", choices=["pass", "fail", "error", "waived"])
    capture.add_argument("--waiver-authority")
    capture.add_argument("--waiver-rationale")
    capture.add_argument("--no-append-record", action="store_true")
    capture.add_argument(
        "--no-narrative-extract",
        action="store_true",
        help="Disable derivation of RawFinding skeletons from narrative R<round>-<REVIEWER>-<NN> ids.",
    )
    capture.set_defaults(func=cmd_capture)

    artifact = sub.add_parser("new-artifact", help="Create a new ArtifactVersion record.")
    add_common_run_arg(artifact)
    artifact.add_argument("--artifact-version", required=True)
    artifact.add_argument("--predecessor")
    artifact.add_argument("--content-locator", required=True)
    artifact.add_argument("--produced-by", required=True)
    artifact.add_argument("--actor", default="orchestrator-consensus-tool")
    artifact.set_defaults(func=cmd_new_artifact)

    response = sub.add_parser("response-skeleton", help="Scaffold AuthorResponse sections for canonical findings.")
    add_common_run_arg(response)
    response.add_argument("--round")
    response.add_argument("--actor", default="author")
    response.add_argument("--finding-id", action="append", default=[])
    response.set_defaults(func=cmd_response_skeleton)

    conclusion = sub.add_parser(
        "conclusion-validation",
        help="Create a scope_triage ReviewBatch for normalized conclusion validation.",
    )
    add_common_run_arg(conclusion)
    conclusion.add_argument("--round")
    conclusion.add_argument("--artifact-version")
    conclusion.add_argument("--finding-id", action="append", default=[])
    conclusion.add_argument("--reviewer", action="append", default=[])
    conclusion.add_argument("--review-batch-id")
    conclusion.add_argument("--actor", default="orchestrator-consensus-tool")
    conclusion.add_argument("--write-prompts", action="store_true")
    conclusion.set_defaults(func=cmd_conclusion_validation)

    rereview = sub.add_parser("rereview-skeleton", help="Scaffold ReReviewDecision files for linked findings.")
    add_common_run_arg(rereview)
    rereview.add_argument("--round")
    rereview.add_argument("--reviewer", action="append", required=True)
    rereview.add_argument("--finding-id", action="append", default=[])
    rereview.add_argument("--artifact-version")
    rereview.add_argument("--review-batch", required=True)
    rereview.add_argument("--actor", default="orchestrator-consensus-tool")
    rereview.set_defaults(func=cmd_rereview_skeleton)

    ready = sub.add_parser(
        "invocation-ready",
        help="Fail-closed readiness check before external CLI invocation.",
        description="Fail-closed readiness check before external CLI invocation.",
        epilog=INVOCATION_READY_BOUNDARY_WARNING,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_common_run_arg(ready)
    ready.add_argument("--actor", required=True)
    ready.add_argument("--player", default="generic-cli")
    ready.add_argument("--prompt", required=True)
    ready.add_argument(
        "--raw-output",
        required=True,
        help=(
            "Pre-declared path for raw stdout/event-stream capture. invoke-agent mirrors "
            "stdout here and writes the extracted parsed result to a "
            f"<raw-output>{FINAL_OUTPUT_MIRROR_SUFFIX} sibling. invocation-ready uses it for path planning only."
        ),
    )
    ready.add_argument("--approved", action="store_true")
    ready.add_argument("--command", nargs=argparse.REMAINDER)
    ready.set_defaults(func=cmd_invocation_ready)

    invoke = sub.add_parser("invoke-agent", help="Start one explicit agent player invocation and record telemetry.")
    add_common_run_arg(invoke)
    invoke.add_argument("--round", default="round-1")
    invoke.add_argument("--phase", required=True, choices=["author", "reviewer", "validator", "manual"])
    invoke.add_argument("--actor", required=True)
    invoke.add_argument("--player", required=True)
    invoke.add_argument("--prompt", required=True)
    invoke.add_argument(
        "--raw-output",
        required=True,
        help=(
            "Path that receives a copy of raw stdout (event stream for JSON-mode CLIs). "
            "On success, the extracted final-output is also mirrored to "
            f"<raw-output>{FINAL_OUTPUT_MIRROR_SUFFIX} beside this path."
        ),
    )
    invoke.add_argument("--cwd", default=".")
    invoke.add_argument("--approved", action="store_true")
    invoke.add_argument("--idle-timeout-seconds", type=float, default=DEFAULT_IDLE_TIMEOUT_SECONDS)
    invoke.add_argument("--stale-timeout-seconds", type=float, default=DEFAULT_STALE_TIMEOUT_SECONDS)
    invoke.add_argument("--heartbeat-interval-seconds", type=float, default=DEFAULT_HEARTBEAT_INTERVAL_SECONDS)
    invoke.add_argument("--command", nargs=argparse.REMAINDER)
    invoke.set_defaults(func=cmd_invoke_agent)

    agent_status = sub.add_parser("agent-status", help="Read the latest agent session state and event tail.")
    add_common_run_arg(agent_status)
    agent_status.add_argument("--actor", required=True)
    agent_status.add_argument("--round", default="round-1")
    agent_status.add_argument("--session")
    agent_status.add_argument("--json", action="store_true")
    agent_status.add_argument("--tail", type=int, default=20)
    agent_status.set_defaults(func=cmd_agent_status)

    agent_watch = sub.add_parser("agent-watch", help="Print normalized agent events for a session.")
    add_common_run_arg(agent_watch)
    agent_watch.add_argument("--actor", required=True)
    agent_watch.add_argument("--round", default="round-1")
    agent_watch.add_argument("--session")
    agent_watch.add_argument("--follow", action="store_true")
    agent_watch.add_argument("--interval-seconds", type=float, default=1.0)
    agent_watch.set_defaults(func=cmd_agent_watch)

    agent_peek = sub.add_parser("agent-peek", help="Print a read-only operator peek for one agent session.")
    add_common_run_arg(agent_peek)
    agent_peek.add_argument("--actor", required=True, help="Reviewer identity whose session is being peeked.")
    agent_peek.add_argument("--round", default="round-1", help="Round identifier (e.g. '1' or 'round-001').")
    agent_peek.add_argument("--session", help="Specific session directory name; defaults to the latest session.")
    agent_peek.add_argument("--tail", type=int, help="Max event lines to scan from each telemetry file (1-1000).")
    agent_peek.add_argument("--snippet-chars", type=int, help="Cap on snippet text length before truncation (40-500).")
    agent_peek.add_argument("--monitor-stale-seconds", type=float, help="Heartbeat age beyond which the monitor is reported stale.")
    agent_peek.add_argument("--follow", action="store_true", help="Re-emit a snapshot every --interval-seconds until terminal or stale.")
    agent_peek.add_argument("--interval-seconds", type=float, help="Sleep between snapshots when --follow is set.")
    agent_peek.add_argument("--config", help="Explicit config file path; overrides layered resolution.")
    agent_peek.add_argument("--no-config", action="store_true", help="Skip layered config resolution; use defaults and flags only.")
    agent_peek.add_argument("--cwd", help="Override working directory used to discover layered config.")
    agent_peek.set_defaults(func=cmd_agent_peek)

    agent_cancel = sub.add_parser("agent-cancel", help="Request cancellation for a live agent session.")
    add_common_run_arg(agent_cancel)
    agent_cancel.add_argument("--actor", required=True)
    agent_cancel.add_argument("--round", default="round-1")
    agent_cancel.add_argument("--session")
    agent_cancel.add_argument("--reason", default="operator requested cancellation")
    agent_cancel.add_argument("--grace-seconds", type=float, default=DEFAULT_CANCEL_GRACE_SECONDS)
    agent_cancel.set_defaults(func=cmd_agent_cancel)

    players = sub.add_parser("players", help="Inspect built-in player adapters.")
    players_sub = players.add_subparsers(dest="players_command", required=True)
    players_probe = players_sub.add_parser("probe", help="Probe one explicit player command.")
    players_probe.add_argument("--player", required=True)
    players_probe.add_argument("--json", action="store_true")
    players_probe.add_argument("--command", nargs=argparse.REMAINDER)
    players_probe.set_defaults(func=cmd_players_probe)

    normalize = sub.add_parser(
        "normalize",
        help="Skeleton NormalizationRecord/CanonicalFinding from captured RawFindings.",
    )
    add_common_run_arg(normalize)
    normalize.add_argument("--round", required=True)
    normalize.add_argument("--actor", default="orchestrator-consensus-tool")
    normalize.add_argument("--merge-overlap", action="store_true")
    normalize.add_argument("--overwrite", action="store_true")
    normalize.set_defaults(func=cmd_normalize)

    report = sub.add_parser(
        "report",
        help="Skeleton report.md with TerminationRecord/FinalReport frontmatter pre-wired.",
    )
    add_common_run_arg(report)
    report.add_argument(
        "--terminal-condition",
        required=True,
        choices=sorted(ENUMS["terminal_condition"]),
    )
    report.add_argument("--final-artifact-version")
    report.add_argument("--actor", default="orchestrator-consensus-tool")
    report.add_argument("--overwrite", action="store_true")
    report.set_defaults(func=cmd_report)

    terminate = sub.add_parser("terminate", help="Create terminal records after deterministic terminal checks.")
    add_common_run_arg(terminate)
    terminate.add_argument("--terminal-condition", required=True, choices=sorted(ENUMS["terminal_condition"]))
    terminate.add_argument("--final-artifact-version")
    terminate.add_argument("--reason", required=True)
    terminate.set_defaults(func=cmd_terminate)

    selftest = sub.add_parser(
        "selftest",
        help="Self-diagnostic checks for the installed CAC skill package.",
    )
    selftest.add_argument(
        "--invocation",
        action="store_true",
        help="Verify the skill is installed and discoverable; emit per-host routing guidance.",
    )
    selftest.add_argument(
        "--host",
        choices=["claude", "codex", "hermes", "auto"],
        default="auto",
        help="Restrict checks to a single host; default checks every host detected on this machine.",
    )
    selftest.add_argument(
        "--write-suggested-rule",
        metavar="PATH",
        help="(opt-in) Write the recommended cac: invocation rule snippet to PATH and verify markers.",
    )
    selftest.set_defaults(func=cmd_selftest)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(normalize_command_separator(list(argv) if argv is not None else sys.argv[1:]))
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
