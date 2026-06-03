"""Raw output capture helpers for cross-agent-consensus runs."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from typing import Any

from cross_agent_consensus.io import append_text, atomic_write_new, sha256_file, slugify, utc_now, write_bytes_new
from cross_agent_consensus.layout import (
    DEFAULT_LAYOUT,
    detect_run_layout,
    record_round_number,
    round_dir,
    round_id_from_number,
)
from cross_agent_consensus.markdown_records import frontmatter, parse_records_from_file
from cross_agent_consensus.models import FRESH_REVIEW_MODE, Record
from cross_agent_consensus.prompts import review_batch_by_id
from cross_agent_consensus.records import parse_run_records, records_by_type


def reviewer_capture_exists(records: list[Record], reviewer_identity: str, review_batch_id: str) -> bool:
    for record in records_by_type(records, "RawReviewerOutput"):
        if (
            record.data.get("reviewer_identity") == reviewer_identity
            and record.data.get("review_batch_id") == review_batch_id
        ):
            return True
    return False


def reviewer_payload_qualifier(args: Any) -> str:
    return slugify(str(args.review_batch or "review-batch"))


def qualified_reviewer_payload_needed(run: Path, records: list[Record], args: Any) -> bool:
    if getattr(args, "phase", None) != "reviewer" or not getattr(args, "review_batch", None):
        return False
    batch = review_batch_by_id(records, args.review_batch)
    if batch is None:
        return False
    actor = slugify(getattr(args, "actor", None) or "reviewer")
    round_id = round_id_from_number(record_round_number(batch))
    existing = round_dir(run, round_id) / "reviews" / f"{actor}.md"
    if not existing.is_file():
        return False
    raw_records = records_by_type(parse_records_from_file(existing), "RawReviewerOutput")
    if not raw_records:
        return False
    return any(record.data.get("review_batch_id") != args.review_batch for record in raw_records)


def phase_record_target(run: Path, args: Any, records: list[Record] | None = None) -> Path | None:
    actor = slugify(args.actor or args.phase)
    if detect_run_layout(run) == DEFAULT_LAYOUT:
        current_round = round_dir(run, args.round)
        if args.phase == "reviewer":
            if records is not None and qualified_reviewer_payload_needed(run, records, args):
                return current_round / "reviews" / f"{actor}-{reviewer_payload_qualifier(args)}.md"
            return current_round / "reviews" / f"{actor}.md"
        if args.phase == "validator":
            return current_round / "validation.md"
        if args.phase == "author":
            return run / "artifacts" / f"{args.artifact_version}.md" if args.artifact_version else None
        return None
    if args.phase == "reviewer":
        round_number = args.round or "round-1"
        if not round_number.startswith("round-"):
            round_number = f"round-{round_number}"
        return run / "reviews" / f"{round_number}-{actor}.md"
    if args.phase == "validator":
        return run / "validation.md"
    if args.phase == "author":
        return run / "artifacts" / f"{args.artifact_version}.md" if args.artifact_version else None
    return None


def raw_payload_target_base(run: Path, args: Any, records: list[Record] | None = None) -> Path:
    actor = slugify(args.actor or args.phase)
    if detect_run_layout(run) == DEFAULT_LAYOUT:
        current_round = round_dir(run, args.round)
        if args.phase == "reviewer":
            if records is not None and qualified_reviewer_payload_needed(run, records, args):
                return current_round / "raw" / "reviewers" / reviewer_payload_qualifier(args) / f"{actor}.out"
            return current_round / "raw" / "reviewers" / f"{actor}.out"
        if args.phase == "validator":
            name = slugify(args.validator_id or actor)
            return current_round / "raw" / "validators" / f"{name}.out"
        if args.phase == "author":
            return current_round / "raw" / "author.out"
        return current_round / "raw" / f"{actor}-{args.phase}.out"
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return run / "payloads" / "raw" / f"{stamp}-{actor}-{args.phase}.out"


def copy_raw_payload(run: Path, args: Any, records: list[Record] | None = None) -> tuple[Path, str]:
    target_base = raw_payload_target_base(run, args, records)
    if args.source_file:
        source = Path(args.source_file)
        if not source.is_file():
            raise FileNotFoundError(f"source file not found: {source}")
        data = source.read_bytes()
    else:
        if args.source_mode != "stdin":
            raise ValueError("--source-file is required unless --source-mode stdin is explicit")
        data = sys.stdin.buffer.read()
        if not data:
            raise ValueError("stdin capture produced an empty payload")
    for index in range(1000):
        target = target_base if index == 0 else target_base.with_name(f"{target_base.stem}-{index:03d}{target_base.suffix}")
        try:
            write_bytes_new(target, data)
            return target, sha256_file(target)
        except FileExistsError:
            continue
    raise FileExistsError(f"unable to allocate unique raw payload path under {target_base.parent}")


def append_reviewer_capture(run: Path, args: Any, raw_path: Path, raw_sha: str) -> None:
    if not args.review_batch or not args.artifact_version:
        raise ValueError("reviewer capture requires --review-batch and --artifact-version")
    records = parse_run_records(run)
    batch = review_batch_by_id(records, args.review_batch)
    if batch is None:
        raise ValueError(f"review_batch_id not found: {args.review_batch}")
    reviewer_identity = args.actor or "reviewer"
    actor = slugify(reviewer_identity)
    round_id = args.round or "round-1"
    if not round_id.startswith("round-"):
        round_id = f"round-{round_id}"
    created_at = utc_now()
    raw_id = f"raw-output-{round_id}-{actor}"
    if qualified_reviewer_payload_needed(run, records, args):
        raw_id = f"{raw_id}-{reviewer_payload_qualifier(args)}"
    target = phase_record_target(run, args, records)
    assert target is not None
    rel_raw = raw_path.relative_to(run)
    raw_text = raw_path.read_text(encoding="utf-8", errors="replace")
    is_first_round_independent = round_id == "round-1" and batch.data.get("review_mode") == FRESH_REVIEW_MODE
    content = "\n".join(
        [
            f"# Review {round_id}: {actor}",
            "",
            f"## RawReviewerOutput {raw_id}",
            frontmatter(
                {
                    "record_type": "RawReviewerOutput",
                    "schema_version": "m2-markdown-1",
                    "run_id": run.name,
                    "actor_identity": "orchestrator-capture-tool",
                    "created_at": created_at,
                    "raw_output_id": raw_id,
                    "reviewer_identity": reviewer_identity,
                    "review_batch_id": args.review_batch,
                    "artifact_version_id": args.artifact_version,
                    "raw_finding_ids": [],
                    "is_first_round_independent": is_first_round_independent,
                }
            ),
            "",
            "### Immutable Raw Reviewer Output",
            "",
            f"- raw_payload_path: `{rel_raw}`",
            f"- raw_payload_sha256: `{raw_sha}`",
            "",
            "Do not edit this fenced block after first capture.",
            "",
            "```text",
            raw_text.rstrip(),
            "```",
            "",
        ]
    )
    atomic_write_new(target, content)


def append_validator_capture(run: Path, args: Any, raw_path: Path, raw_sha: str) -> None:
    if not args.validator_id or not args.artifact_version or not args.result:
        raise ValueError("validator capture requires --validator-id, --artifact-version, and --result")
    created_at = utc_now()
    evidence_id = f"validation-evidence-{slugify(args.validator_id)}-{slugify(raw_path.stem)}"
    rel_raw = raw_path.relative_to(run)
    section = "\n".join(
        [
            "",
            f"## ValidationEvidence {evidence_id}",
            frontmatter(
                {
                    "record_type": "ValidationEvidence",
                    "schema_version": "m2-markdown-1",
                    "run_id": run.name,
                    "actor_identity": "orchestrator-capture-tool",
                    "created_at": created_at,
                    "validation_evidence_id": evidence_id,
                    "validator_id": args.validator_id,
                    "target_artifact_version_id": args.artifact_version,
                    "result": args.result,
                    "payload_reference": str(rel_raw),
                    "produced_by": args.actor or "validator",
                    "waiver_authority_or_null": args.waiver_authority,
                    "waiver_rationale_or_null": args.waiver_rationale,
                }
            ),
            "",
            "### Evidence Notes",
            "",
            f"- raw_payload_sha256: `{raw_sha}`",
            f"- source_mode: `{args.source_mode}`",
            f"- source_command_or_provider: `{args.source_command or args.provider or 'null'}`",
            "",
        ]
    )
    target = phase_record_target(run, args) or (run / "validation.md")
    append_text(target, section)
