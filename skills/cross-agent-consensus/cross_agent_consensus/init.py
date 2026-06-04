"""Run initialization file generation for cross-agent-consensus."""

from __future__ import annotations

import argparse
from pathlib import Path

from cross_agent_consensus.config import config_resolution_record
from cross_agent_consensus.io import hash_locator, read_cac_version
from cross_agent_consensus.layout import ROUND_FIRST_LAYOUT_VERSION, round_dir
from cross_agent_consensus.markdown_records import frontmatter


DOCUMENT_VALIDATORS = [
    "artifact_exists",
    "review_scope_exists",
    "review_batch_mode_declared",
    "final_report_exists",
    "blocking_findings_have_author_responses",
    "final_report_unresolved_blockers_declared",
    "final_report_backlog_separated",
]
CAC_VERSION = read_cac_version()

# Stub content `init` writes to rounds/<round>/normalization.md. `normalize` recognises
# this exact text and overwrites it on first call without requiring --overwrite.
INIT_STUB_NORMALIZATION = "# Normalization\n\nNo normalization records have been recorded for this round.\n"


def infer_validators(profile: str, validators: list[str]) -> list[str]:
    if validators:
        return validators
    if profile == "document-consensus":
        return DOCUMENT_VALIDATORS
    return []


def build_init_files(args: argparse.Namespace, run_id: str, created_at: str) -> dict[Path, str]:
    run = Path(args.run_root) / run_id
    validators = infer_validators(args.profile, args.validator)
    if not validators:
        raise ValueError("--validator is required when --profile is not document-consensus")
    if args.orchestrator == args.author or args.orchestrator in args.reviewer:
        raise ValueError("--orchestrator must be distinct from --author and all --reviewer values")
    if len(set(args.reviewer)) != len(args.reviewer):
        raise ValueError("--reviewer values must be unique")

    content_hash = hash_locator(args.artifact_locator, Path.cwd())
    hash_value = content_hash if content_hash is not None else None
    success_criteria = args.success_criterion or ["Complete the task objective within the declared review scope."]
    task_id = f"task-brief-{run_id}"
    policy_id = f"policy-{run_id}"
    participants_id = f"participants-{run_id}"
    scope_id = f"review-scope-{run_id}"
    batch_id = "review-batch-round-1-fresh_review"
    first_round = round_dir(run, "round-1")
    first_round_rel = first_round.relative_to(run)

    init_sections = [
        "# Cross-Agent Consensus Run",
        "",
        "## Run Metadata",
        "",
        f"- `run_id`: `{run_id}`",
        f"- `run_root`: `{run}`",
        f"- `cross_agent_consensus_version`: `{CAC_VERSION}`",
        "- `protocol_version`: `m2-markdown-1`",
        f"- `layout_version`: `{ROUND_FIRST_LAYOUT_VERSION}`",
        f"- prompt payload root: `{first_round_rel}/prompts/`",
        f"- raw-output payload root: `{first_round_rel}/raw/`",
        f"- run id source: `{'user-supplied' if args.run_id else 'generated'}`",
        "- initial artifact version id: `v1`",
        "- first review batch id: `review-batch-round-1-fresh_review`",
        f"- first round path: `{first_round_rel}/`",
        f"- first review batch path: `{first_round_rel}/round.md`",
        "- initial artifact record path: `artifacts/v1.md`",
        config_resolution_record(args, run_id, created_at),
        f"## TaskBrief {task_id}",
        frontmatter(
            {
                "record_type": "TaskBrief",
                "schema_version": "m2-markdown-1",
                "run_id": run_id,
                "actor_identity": args.orchestrator,
                "created_at": created_at,
                "task_brief_id": task_id,
                "artifact_locator": args.artifact_locator,
                "objective": args.task,
                "success_criteria": success_criteria,
                "profile": args.profile,
                "human_supervisor_identity_or_null": args.human_supervisor,
            }
        ),
        "",
        "### Notes",
        "",
        "- Artifact type:",
        "- Known assumptions:",
        "- Questions that must be resolved before review:",
        "",
        f"## Policy {policy_id}",
        frontmatter(
            {
                "record_type": "Policy",
                "schema_version": "m2-markdown-1",
                "run_id": run_id,
                "actor_identity": args.orchestrator,
                "created_at": created_at,
                "policy_id": policy_id,
                "profile": args.profile,
                "required_validator_ids": validators,
                "round_limits": {
                    "max_fresh_review_rounds": args.max_fresh_review_rounds,
                    "max_fresh_review_rounds_without_human_approval": args.max_fresh_review_rounds_without_human_approval,
                    "max_remediation_rounds_per_finding": args.max_remediation_rounds,
                },
                "materiality_rules": {
                    "material_by_default": args.material_by_default
                    or ["missing required section inside scope", "unsafe automation statement inside scope"],
                    "non_blocking_or_out_of_scope_by_default": args.non_blocking_by_default
                    or ["wording preference", "formatting preference"],
                },
                "escalation_policy": args.escalation_policy,
                "waiver_authority_or_null": args.waiver_authority,
                "unattended_invocation": {
                    "enabled": args.unattended_invocation,
                    "scope": args.unattended_scope,
                },
            }
        ),
        "",
        "### Policy Notes",
        "",
        "- Validator waiver rules:",
        "- Human terminal handling:",
        "- Scope promotion rules:",
        "",
        f"## Participants {participants_id}",
        frontmatter(
            {
                "record_type": "Participants",
                "schema_version": "m2-markdown-1",
                "run_id": run_id,
                "actor_identity": args.orchestrator,
                "created_at": created_at,
                "participants_record_id": participants_id,
                "orchestrator_identity": args.orchestrator,
                "author_identity": args.author,
                "reviewer_identities": args.reviewer,
                "human_supervisor_identity_or_null": args.human_supervisor,
            }
        ),
        "",
        "### Isolation Notes",
        "",
        "- Orchestrator identity is distinct from Author and Reviewers: yes.",
        "- First-round reviewer isolation plan: finalize all same-round reviewer prompts before invoking any reviewer.",
        "- Prompt payload paths prepared before invocation:",
        "- Raw-output capture paths prepared before invocation:",
        "- Runtime/session version notes:",
        "",
        f"## ReviewScope {scope_id}",
        frontmatter(
            {
                "record_type": "ReviewScope",
                "schema_version": "m2-markdown-1",
                "run_id": run_id,
                "actor_identity": args.orchestrator,
                "created_at": created_at,
                "review_scope_id": scope_id,
                "objective": args.review_objective or args.task,
                "in_scope": args.in_scope or ["clarity", "completeness", "internal contradictions"],
                "out_of_scope": args.out_of_scope or ["broad refactoring unless explicitly listed"],
                "review_modes_allowed": [
                    "fresh_review",
                    "remediation_verification",
                    "regression_check",
                    "scope_triage",
                ],
                "max_fresh_review_rounds": args.max_fresh_review_rounds,
                "max_remediation_rounds_per_finding": args.max_remediation_rounds,
                "promotion_policy_or_null": args.promotion_policy,
            }
        ),
        "",
        "### Scope Confirmation",
        "",
        "- Human/objective confirmation:",
        "- In-scope overrides:",
        "- Out-of-scope overrides:",
        "- Round-limit overrides:",
        "",
    ]

    batch = "\n".join(
        [
            "# Round round-1",
            "",
            "This round folder is self-contained for prompts, raw outputs, review records, normalization, author responses, rereviews, validation evidence, and round-local backlog.",
            "",
            f"## ReviewBatch {batch_id}",
            frontmatter(
                {
                    "record_type": "ReviewBatch",
                    "schema_version": "m2-markdown-1",
                    "run_id": run_id,
                    "actor_identity": args.orchestrator,
                    "created_at": created_at,
                    "review_batch_id": batch_id,
                    "review_scope_id": scope_id,
                    "review_mode": "fresh_review",
                    "target_artifact_version_id": "v1",
                    "source_finding_ids": [],
                    "review_focus": args.review_focus,
                    "round_id": "round-1",
                    "round_path": str(first_round_rel),
                }
            ),
            "",
            "### Dispatch Notes",
            "",
            f"- Reviewer identities: {', '.join(args.reviewer)}",
            f"- Review focus/lenses: {', '.join(args.review_focus) if args.review_focus else 'none'}",
            "- Review focus values are prompt lenses only; they are not participant identities.",
            "- Prompt section used:",
            "- Isolation constraints: first-round reviewer prompts are finalized before invocation.",
            "- Source findings for non-fresh modes: none.",
            "",
        ]
    )

    artifact = "\n".join(
        [
            frontmatter(
                {
                    "record_type": "ArtifactVersion",
                    "schema_version": "m2-markdown-1",
                    "run_id": run_id,
                    "actor_identity": args.orchestrator,
                    "created_at": created_at,
                    "artifact_version_id": "v1",
                    "predecessor_id_or_null": None,
                    "content_locator": args.artifact_locator,
                    "content_hash_or_null": hash_value,
                    "produced_by": args.author,
                }
            ),
            "",
            "# Artifact Version v1",
            "",
            "## Content Or Locator",
            "",
            "```text",
            args.artifact_locator,
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

    validation_rows = [
        "| validator_id | target_artifact_version_id | latest_result | evidence_id_or_null |",
        "| --- | --- | --- | --- |",
    ]
    for validator in validators:
        validation_rows.append(f"| {validator} | v1 | pending | null |")
    validation = "\n".join(
        [
            "# Validation Summary",
            "",
            "## Validator Plan",
            "",
            *validation_rows,
            "",
            "Consensus requires every required validator to be `pass` or `waived`.",
            "",
        ]
    )

    return {
        run / "run.md": "\n".join(init_sections),
        first_round / "round.md": batch,
        run / "artifacts" / "v1.md": artifact,
        run / "validation.md": validation,
        run / "escalations.md": "# Escalations\n\nNo escalation records have been recorded.\n",
        run / "backlog.md": "# Backlog\n\nNon-blocking, deferred, and out-of-scope items belong here.\n",
        first_round / "normalization.md": INIT_STUB_NORMALIZATION,
        first_round / "author-responses.md": "# Author Responses round-1\n\nNo author responses have been recorded for this round.\n",
        first_round / "validation.md": validation,
        first_round / "backlog.md": "# Round Backlog\n\nRound-local non-blocking, deferred, and out-of-scope items belong here.\n",
    }
