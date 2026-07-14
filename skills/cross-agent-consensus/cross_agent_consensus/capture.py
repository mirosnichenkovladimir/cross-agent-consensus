"""Raw output capture helpers for cross-agent-consensus runs."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

from cross_agent_consensus.io import (
    append_text,
    atomic_write_new,
    eprint,
    read_json_file,
    sha256_file,
    slugify,
    utc_now,
    write_bytes_new,
)
from cross_agent_consensus.layout import (
    DEFAULT_LAYOUT,
    detect_run_layout,
    normalize_round_id,
    record_round_number,
    round_dir,
    round_id_from_number,
    round_number,
)
from cross_agent_consensus.markdown_records import frontmatter, parse_records_from_file
from cross_agent_consensus.models import CaptureCommandInput, FRESH_REVIEW_MODE, Record
from cross_agent_consensus.prompts import active_review_batches, resolve_active_round, review_batch_by_id
from cross_agent_consensus.records import NARRATIVE_FINDING_ID_RE, parse_run_records, records_by_type
from cross_agent_consensus.run_audit import locked_run_command, recorded_run_version
from cross_agent_consensus.invocation.session_paths import safe_actor_component


_NARRATIVE_PARAGRAPH_LIMIT = 1200


def capture_provenance(
    run: Path,
    args: CaptureCommandInput,
) -> tuple[str, str | None, str | None, str | None, str | None]:
    """Identify the exact supervised session behind a captured source file."""

    source = Path(args.source_file).resolve() if args.source_file else None
    if source is not None and args.actor:
        actor_dir = round_dir(run, args.round) / "agents" / safe_actor_component(args.actor)
        for session in sorted(actor_dir.glob("session-*"), reverse=True):
            try:
                invocation = read_json_file(session / "invocation.json")
                exit_payload = read_json_file(session / "exit.json")
            except (OSError, ValueError):
                continue
            raw_value = invocation.get("raw_output_path")
            if not isinstance(raw_value, str) or not raw_value:
                continue
            invocation_raw = Path(raw_value)
            if not invocation_raw.is_absolute():
                invocation_raw = run / invocation_raw
            if invocation_raw.resolve() != source:
                continue
            if exit_payload.get("final_state") != "completed":
                continue
            run_version = recorded_run_version(run)
            requires_session_evidence = (
                exit_payload.get("evidence_digest_version") == "session-evidence-1"
                or exit_payload.get("raw_output_sha256") is not None
                or (run_version is not None and run_version >= (0, 10, 0))
            )
            if requires_session_evidence:
                if exit_payload.get("evidence_digest_version") != "session-evidence-1":
                    raise ValueError(f"completed supervised session evidence marker drifted: {session.name}")
                source_sha = sha256_file(source)
                if exit_payload.get("raw_output_sha256") != source_sha:
                    raise ValueError(
                        f"captured source bytes do not match completed supervised session {session.name}"
                    )
                if not (session / "stdout.raw").is_file() or exit_payload.get("stdout_sha256") != sha256_file(
                    session / "stdout.raw"
                ):
                    raise ValueError(f"completed supervised session stdout drifted: {session.name}")
            prompt_sha = invocation.get("prompt_sha256")
            return (
                "live_cli",
                str(invocation.get("session_id") or session.name),
                str(session.relative_to(run)),
                str(prompt_sha) if prompt_sha else None,
                sha256_file(session / "exit.json"),
            )
    provider = (args.provider or "").lower().replace("-", "_")
    if provider == "host_subagent":
        return "host_subagent", None, None, None, None
    if args.source_mode == "stdin":
        return "stdin", None, None, None, None
    return "manual_import", None, None, None, None


def derive_raw_findings_from_narrative(
    raw_text: str,
    reviewer_identity: str,
    review_batch_id: str,
    artifact_version_id: str,
    run_id: str,
    created_at: str,
    existing_finding_ids: set[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Scan a reviewer narrative for `R<round>-<REVIEWER>-<NN>` ids and emit RawFinding skeletons.

    Returns a tuple of (raw_finding_ids, rendered_record_sections). Each rendered section is
    a self-contained `## RawFinding ...` block with frontmatter and a prose template body so
    the operator can fill claim/evidence after capture.
    """
    seen: dict[str, str] = {}
    for match in NARRATIVE_FINDING_ID_RE.finditer(raw_text):
        finding_id = match.group(0).lower()
        if finding_id in seen:
            continue
        start = max(0, raw_text.rfind("\n\n", 0, match.start()))
        end_match = raw_text.find("\n\n", match.end())
        end = end_match if end_match != -1 else len(raw_text)
        paragraph = raw_text[start:end].strip()
        if len(paragraph) > _NARRATIVE_PARAGRAPH_LIMIT:
            paragraph = paragraph[:_NARRATIVE_PARAGRAPH_LIMIT].rstrip() + "..."
        seen[finding_id] = paragraph
    existing_finding_ids = existing_finding_ids or set()
    qualify_batch = any(finding_id in existing_finding_ids for finding_id in seen)
    emitted_ids: dict[str, str] = {}
    for finding_id in seen:
        emitted_id = finding_id
        if qualify_batch:
            id_match = NARRATIVE_FINDING_ID_RE.fullmatch(finding_id.upper())
            if id_match is not None:
                emitted_id = (
                    f"r{id_match.group(1)}-{id_match.group(2).lower()}-"
                    f"{slugify(review_batch_id)}-{int(id_match.group(3)):03d}"
                )
        emitted_ids[finding_id] = emitted_id
    finding_ids: list[str] = list(emitted_ids.values())
    sections: list[str] = []
    for finding_id, paragraph in seen.items():
        emitted_id = emitted_ids[finding_id]
        sections.append(
            "\n".join(
                [
                    "",
                    f"## RawFinding {emitted_id}",
                    frontmatter(
                        {
                            "record_type": "RawFinding",
                            "schema_version": "m2-markdown-2",
                            "run_id": run_id,
                            "actor_identity": "orchestrator-capture-tool",
                            "created_at": created_at,
                            "raw_finding_id": emitted_id,
                            "reviewer_identity": reviewer_identity,
                            "artifact_version_id": artifact_version_id,
                            "review_batch_id": review_batch_id,
                            "location": "# TODO: extract from narrative",
                            "claim": "# TODO: extract from narrative",
                            "evidence": "# TODO: extract from narrative",
                            "severity_or_materiality_claim": "# TODO: extract from narrative",
                            "scope_classification": "in_scope",
                            "blocking_status": "non_blocking",
                            "suggested_fix_or_null": None,
                        }
                    ),
                    "",
                    "### Narrative Context",
                    "",
                    paragraph,
                    "",
                ]
            )
        )
    return finding_ids, sections


def reviewer_capture_exists(records: list[Record], reviewer_identity: str, review_batch_id: str) -> bool:
    for record in records_by_type(records, "RawReviewerOutput"):
        if (
            record.data.get("reviewer_identity") == reviewer_identity
            and record.data.get("review_batch_id") == review_batch_id
        ):
            return True
    return False


def reviewer_payload_qualifier(args: CaptureCommandInput) -> str:
    return slugify(str(args.review_batch or "review-batch"))


def qualified_reviewer_payload_needed(run: Path, records: list[Record], args: CaptureCommandInput) -> bool:
    if getattr(args, "phase", None) != "reviewer" or not getattr(args, "review_batch", None):
        return False
    review_batch_id = args.review_batch
    if review_batch_id is None:
        return False
    batch = review_batch_by_id(records, review_batch_id)
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


def phase_record_target(run: Path, args: CaptureCommandInput, records: list[Record] | None = None) -> Path | None:
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
        return run / "reviews" / f"{normalize_round_id(args.round)}-{actor}.md"
    if args.phase == "validator":
        return run / "validation.md"
    if args.phase == "author":
        return run / "artifacts" / f"{args.artifact_version}.md" if args.artifact_version else None
    return None


def raw_payload_target_base(run: Path, args: CaptureCommandInput, records: list[Record] | None = None) -> Path:
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


def copy_raw_payload(run: Path, args: CaptureCommandInput, records: list[Record] | None = None) -> tuple[Path, str]:
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


def append_reviewer_capture(
    run: Path,
    args: CaptureCommandInput,
    raw_path: Path,
    raw_sha: str,
    records: list[Record] | None = None,
) -> None:
    if not args.review_batch or not args.artifact_version:
        raise ValueError("reviewer capture requires --review-batch and --artifact-version")
    if records is None:
        records = parse_run_records(run)
    batch = review_batch_by_id(records, args.review_batch)
    if batch is None:
        raise ValueError(f"review_batch_id not found: {args.review_batch}")
    reviewer_identity = args.actor or "reviewer"
    actor = slugify(reviewer_identity)
    round_id = normalize_round_id(args.round)
    created_at = utc_now()
    raw_id = f"raw-output-{round_id}-{actor}"
    if qualified_reviewer_payload_needed(run, records, args):
        raw_id = f"{raw_id}-{reviewer_payload_qualifier(args)}"
    target = phase_record_target(run, args, records)
    assert target is not None
    rel_raw = raw_path.relative_to(run)
    raw_text = raw_path.read_text(encoding="utf-8", errors="replace")
    is_first_round_independent = round_id == "round-1" and batch.data.get("review_mode") == FRESH_REVIEW_MODE
    capture_origin, session_id, session_path, prompt_sha, session_exit_sha = capture_provenance(run, args)
    narrative_text = raw_text
    if session_path is not None:
        final_output = run / session_path / "final-output.md"
        if final_output.is_file():
            narrative_text = final_output.read_text(encoding="utf-8", errors="replace")
    derive_narrative = not getattr(args, "no_narrative_extract", False)
    raw_finding_ids: list[str] = []
    narrative_sections: list[str] = []
    if derive_narrative:
        raw_finding_ids, narrative_sections = derive_raw_findings_from_narrative(
            raw_text=narrative_text,
            reviewer_identity=reviewer_identity,
            review_batch_id=args.review_batch,
            artifact_version_id=args.artifact_version,
            run_id=run.name,
            created_at=created_at,
            existing_finding_ids={
                str(record.data.get("raw_finding_id"))
                for record in records_by_type(records, "RawFinding")
                if record.data.get("raw_finding_id")
            },
        )
    head = "\n".join(
        [
            f"# Review {round_id}: {actor}",
            "",
            f"## RawReviewerOutput {raw_id}",
            frontmatter(
                {
                    "record_type": "RawReviewerOutput",
                    "schema_version": "m2-markdown-2",
                    "run_id": run.name,
                    "actor_identity": "orchestrator-capture-tool",
                    "created_at": created_at,
                    "raw_output_id": raw_id,
                    "reviewer_identity": reviewer_identity,
                    "review_batch_id": args.review_batch,
                    "artifact_version_id": args.artifact_version,
                    "raw_finding_ids": raw_finding_ids,
                    "is_first_round_independent": is_first_round_independent,
                    "raw_payload_path": str(rel_raw),
                    "raw_payload_sha256": raw_sha,
                    "capture_origin": capture_origin,
                    "session_id_or_null": session_id,
                    "session_path_or_null": session_path,
                    "prompt_sha256_or_null": prompt_sha,
                    "session_exit_sha256_or_null": session_exit_sha,
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
    content = head + ("".join(narrative_sections) if narrative_sections else "")
    atomic_write_new(target, content)


def append_validator_capture(run: Path, args: CaptureCommandInput, raw_path: Path, raw_sha: str) -> None:
    if not args.validator_id or not args.artifact_version or not args.result:
        raise ValueError("validator capture requires --validator-id, --artifact-version, and --result")
    created_at = utc_now()
    evidence_id = f"validation-evidence-{slugify(args.validator_id)}-{slugify(raw_path.stem)}"
    rel_raw = raw_path.relative_to(run)
    capture_origin, session_id, session_path, prompt_sha, session_exit_sha = capture_provenance(run, args)
    section = "\n".join(
        [
            "",
            f"## ValidationEvidence {evidence_id}",
            frontmatter(
                {
                    "record_type": "ValidationEvidence",
                    "schema_version": "m2-markdown-2",
                    "run_id": run.name,
                    "actor_identity": "orchestrator-capture-tool",
                    "created_at": created_at,
                    "validation_evidence_id": evidence_id,
                    "validator_id": args.validator_id,
                    "target_artifact_version_id": args.artifact_version,
                    "result": args.result,
                    "payload_reference": str(rel_raw),
                    "payload_sha256": raw_sha,
                    "produced_by": args.actor or "validator",
                    "capture_origin": capture_origin,
                    "session_id_or_null": session_id,
                    "session_path_or_null": session_path,
                    "prompt_sha256_or_null": prompt_sha,
                    "session_exit_sha256_or_null": session_exit_sha,
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


def _resolve_existing_round(records: list[Record], explicit_round: str | None) -> str:
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


def _resolve_single_candidate(flag: str, candidates: list[str], *, hint: str) -> str:
    if len(candidates) == 1:
        value = candidates[0]
        print(f"using {flag} {value} ({hint})")
        return value
    raise ValueError(
        f"reviewer capture requires {flag} "
        f"(found {len(candidates)} candidates: {', '.join(candidates) or 'none'})"
    )


@locked_run_command("evidence_captured")
def cmd_capture(args: CaptureCommandInput) -> int:
    run = Path(args.run)
    try:
        if args.phase in {"author", "manual"} and not args.no_append_record:
            raise ValueError(f"{args.phase} capture requires --no-append-record for bare payload capture")
        records = parse_run_records(run)
        if args.phase == "reviewer" or args.review_batch:
            args.round = resolve_active_round(records, args.round, args.review_batch)
        else:
            args.round = _resolve_existing_round(records, args.round)
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
