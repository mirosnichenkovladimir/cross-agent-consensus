"""Validate content-only worker drafts and promote them into CAC-owned records."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cross_agent_consensus.integrity import artifact_by_id, verified_artifact_sha256
from cross_agent_consensus.execution_attempts import (
    assert_attempt_accepts_receipt_locked,
    complete_attempt_for_receipt_locked,
    receipt_attempt_source_by_id,
)
from cross_agent_consensus.io import (
    atomic_write_new,
    read_json_file,
    sha256_file,
    utc_now,
    write_bytes_new,
)
from cross_agent_consensus.layout import normalize_round_id, round_dir
from cross_agent_consensus.markdown_records import frontmatter, parse_records_from_file
from cross_agent_consensus.models import PromptCommandInput
from cross_agent_consensus.prompts import prompt_target
from cross_agent_consensus.records import parse_run_records, records_by_type
from cross_agent_consensus.run_audit import locked_run_command, read_run_events
from cross_agent_consensus.invocation.session_paths import safe_actor_component


DRAFT_KINDS = {"author_artifact", "reviewer_findings", "validator_output", "synthesis"}
FINDING_FIELDS = {
    "location",
    "claim",
    "evidence",
    "severity_or_materiality_claim",
    "scope_classification",
    "blocking_status",
    "suggested_fix_or_null",
}
ROOT_FIELDS = {
    "author_artifact": {"kind", "summary", "assumptions", "known_limitations"},
    "reviewer_findings": {"kind", "review_text", "findings"},
    "validator_output": {"kind", "result", "evidence"},
    "synthesis": {"kind", "text"},
}


@dataclass(frozen=True)
class DraftSource:
    capture_origin: str
    session_id_or_null: str | None
    session_path_or_null: str | None
    prompt_sha256_or_null: str | None
    session_exit_sha256_or_null: str | None
    execution_attempt_id_or_null: str | None
    input_artifact_version_id_or_null: str | None
    prompt_source_path_or_null: str | None
    provider_raw_path_or_null: str | None
    provider_raw_sha256_or_null: str | None


def exact_object_fields(value: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"{label} contains CAC-owned or unknown fields: {', '.join(unknown)}")


def string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field} must be a list of strings")
    return value


def reviewer_finding_source_tokens(raw_bytes: bytes) -> list[bytes]:
    """Return each finding object's exact JSON token for byte-only deduplication."""

    text = raw_bytes.decode("utf-8")
    decoder = json.JSONDecoder()

    def skip_space(offset: int) -> int:
        while offset < len(text) and text[offset] in " \t\r\n":
            offset += 1
        return offset

    offset = skip_space(0)
    if offset >= len(text) or text[offset] != "{":
        return []
    offset += 1
    while True:
        offset = skip_space(offset)
        if offset < len(text) and text[offset] == "}":
            return []
        key, offset = decoder.raw_decode(text, offset)
        offset = skip_space(offset)
        if not isinstance(key, str) or offset >= len(text) or text[offset] != ":":
            return []
        value_start = skip_space(offset + 1)
        if key == "findings":
            if value_start >= len(text) or text[value_start] != "[":
                return []
            tokens: list[bytes] = []
            offset = value_start + 1
            while True:
                offset = skip_space(offset)
                if offset < len(text) and text[offset] == "]":
                    return tokens
                element_start = offset
                _element, offset = decoder.raw_decode(text, offset)
                tokens.append(text[element_start:offset].encode("utf-8"))
                offset = skip_space(offset)
                if offset < len(text) and text[offset] == ",":
                    offset += 1
                    continue
                if offset < len(text) and text[offset] == "]":
                    return tokens
                return []
        _value, offset = decoder.raw_decode(text, value_start)
        offset = skip_space(offset)
        if offset < len(text) and text[offset] == ",":
            offset += 1
            continue
        if offset < len(text) and text[offset] == "}":
            return []
        return []


def validate_content_only_draft(source: Path) -> tuple[dict[str, Any], bytes, str]:
    raw_bytes = source.read_bytes()
    finding_source_tokens = reviewer_finding_source_tokens(raw_bytes)

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"content-only draft repeats JSON field {key!r}")
            value[key] = item
        return value

    try:
        value = json.loads(raw_bytes, object_pairs_hook=unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"content-only draft must be one JSON object: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("content-only draft must be one JSON object")
    kind = value.get("kind")
    if kind not in DRAFT_KINDS:
        raise ValueError(f"draft kind must be one of: {', '.join(sorted(DRAFT_KINDS))}")
    assert isinstance(kind, str)
    exact_object_fields(value, ROOT_FIELDS[kind], f"{kind} draft")
    if kind == "author_artifact":
        if not isinstance(value.get("summary"), str) or not value["summary"].strip():
            raise ValueError("author_artifact.summary must be a non-empty string")
        string_list(value.get("assumptions"), "author_artifact.assumptions")
        string_list(value.get("known_limitations"), "author_artifact.known_limitations")
    elif kind == "reviewer_findings":
        if not isinstance(value.get("review_text"), str):
            raise ValueError("reviewer_findings.review_text must be a string")
        findings = value.get("findings")
        if not isinstance(findings, list):
            raise ValueError("reviewer_findings.findings must be a list")
        deduplicated: list[dict[str, Any]] = []
        seen: set[bytes] = set()
        for index, finding in enumerate(findings, 1):
            if not isinstance(finding, dict):
                raise ValueError(f"reviewer_findings.findings[{index}] must be an object")
            exact_object_fields(finding, FINDING_FIELDS, f"reviewer finding {index}")
            missing = sorted(FINDING_FIELDS - set(finding))
            if missing:
                raise ValueError(f"reviewer finding {index} missing fields: {', '.join(missing)}")
            for field in FINDING_FIELDS - {"suggested_fix_or_null"}:
                if not isinstance(finding.get(field), str):
                    raise ValueError(f"reviewer finding {index}.{field} must be a string")
            if finding.get("suggested_fix_or_null") is not None and not isinstance(
                finding.get("suggested_fix_or_null"), str
            ):
                raise ValueError(
                    f"reviewer finding {index}.suggested_fix_or_null must be a string or null"
                )
            if finding["scope_classification"] not in {
                "in_scope",
                "out_of_scope",
                "unclear_scope",
            }:
                raise ValueError(f"reviewer finding {index}.scope_classification is invalid")
            if finding["blocking_status"] not in {
                "blocking",
                "non_blocking",
                "deferred",
                "promoted_by_human",
            }:
                raise ValueError(f"reviewer finding {index}.blocking_status is invalid")
            source_token = (
                finding_source_tokens[index - 1]
                if len(finding_source_tokens) == len(findings)
                else f"{index}".encode("ascii")
            )
            if source_token not in seen:
                seen.add(source_token)
                deduplicated.append(finding)
        value["findings"] = deduplicated
    elif kind == "validator_output":
        if value.get("result") not in {"pass", "fail", "error", "waived"}:
            raise ValueError("validator_output.result must be pass, fail, error, or waived")
        if not isinstance(value.get("evidence"), str) or not value["evidence"].strip():
            raise ValueError("validator_output.evidence must be a non-empty string")
    elif not isinstance(value.get("text"), str) or not value["text"].strip():
        raise ValueError("synthesis.text must be a non-empty string")
    return value, raw_bytes, hashlib.sha256(raw_bytes).hexdigest()


def supervised_draft_source(
    run: Path,
    source: Path,
    *,
    actor: str,
    round_id: str,
    allow_manual_source: bool,
) -> DraftSource:
    actor_directory = round_dir(run, round_id) / "agents" / safe_actor_component(actor)
    source_resolved = source.resolve()
    for session in sorted(actor_directory.glob("session-*"), reverse=True):
        try:
            invocation = read_json_file(session / "invocation.json")
            exit_record = read_json_file(session / "exit.json")
        except (OSError, ValueError):
            continue
        raw_value = invocation.get("raw_output_path")
        if not isinstance(raw_value, str) or not raw_value:
            continue
        raw_output = Path(raw_value)
        if not raw_output.is_absolute():
            raw_output = run / raw_output
        candidates = {
            raw_output.resolve(): exit_record.get("raw_output_sha256"),
            (session / "final-output.md").resolve(): exit_record.get(
                "final_output_sha256_or_null"
            ),
            raw_output.with_suffix(raw_output.suffix + ".final-output.md").resolve(): (
                exit_record.get("final_output_sha256_or_null")
            ),
        }
        if source_resolved not in candidates:
            continue
        if exit_record.get("final_state") != "completed":
            raise ValueError(f"draft source session is not completed: {session.name}")
        if candidates[source_resolved] != sha256_file(source):
            raise ValueError(f"draft source digest differs from session evidence: {session.name}")
        attempt = next(
            (
                event.get("details")
                for event in reversed(read_run_events(run))
                if event.get("event_type") == "execution_attempt_started"
                and isinstance(event.get("details"), dict)
                and event["details"].get("session_id") == session.name
                and event["details"].get("participant_identity") == actor
            ),
            None,
        )
        prompt_sha = invocation.get("prompt_sha256")
        return DraftSource(
            capture_origin="live_cli",
            session_id_or_null=session.name,
            session_path_or_null=str(session.relative_to(run)),
            prompt_sha256_or_null=str(prompt_sha) if prompt_sha else None,
            session_exit_sha256_or_null=sha256_file(session / "exit.json"),
            execution_attempt_id_or_null=(
                str(attempt.get("attempt_id")) if isinstance(attempt, dict) else None
            ),
            input_artifact_version_id_or_null=(
                str(attempt.get("input_artifact_version_id_or_null"))
                if isinstance(attempt, dict)
                and attempt.get("input_artifact_version_id_or_null")
                else None
            ),
            prompt_source_path_or_null=(
                str(invocation.get("prompt_source_path"))
                if invocation.get("prompt_source_path")
                else None
            ),
            provider_raw_path_or_null=(
                str(raw_output.relative_to(run))
                if raw_output.is_relative_to(run)
                else None
            ),
            provider_raw_sha256_or_null=(
                str(exit_record.get("raw_output_sha256"))
                if exit_record.get("raw_output_sha256")
                else None
            ),
        )
    if not allow_manual_source:
        raise ValueError(
            "draft source is not bound to a completed supervised session; use --allow-manual-source only for an explicit import"
        )
    return DraftSource(
        "manual_import", None, None, None, None, None, None, None, None, None
    )


def verify_target_artifact(run: Path, artifact_version: str) -> dict[str, Any]:
    artifact = artifact_by_id(parse_run_records(run), artifact_version)
    if artifact is None:
        raise ValueError(f"ArtifactVersion not found: {artifact_version}")
    verified_artifact_sha256(run, artifact)
    snapshot_sha = artifact.data.get("git_change_snapshot_sha256")
    if snapshot_sha is not None:
        locator = artifact.data.get("content_locator")
        manifest = run / str(locator)
        if not manifest.is_file():
            raise ValueError(f"Git snapshot manifest is missing: {locator}")
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        if payload.get("snapshot_sha256") != snapshot_sha:
            raise ValueError(
                f"ArtifactVersion {artifact_version} Git snapshot digest changed"
            )
    return artifact.data


def provenance_fields(source: DraftSource, captured_path: Path, run: Path, source_sha: str) -> dict[str, Any]:
    return {
        "raw_payload_path": (
            source.provider_raw_path_or_null or str(captured_path.relative_to(run))
        ),
        "raw_payload_sha256": source.provider_raw_sha256_or_null or source_sha,
        "draft_payload_path": str(captured_path.relative_to(run)),
        "draft_payload_sha256": source_sha,
        "capture_origin": source.capture_origin,
        "session_id_or_null": source.session_id_or_null,
        "session_path_or_null": source.session_path_or_null,
        "prompt_sha256_or_null": source.prompt_sha256_or_null,
        "session_exit_sha256_or_null": source.session_exit_sha256_or_null,
    }


@locked_run_command("draft_promoted")
def cmd_promote_draft(args: argparse.Namespace) -> int:
    run = Path(args.run)
    captured_path: Path | None = None
    target: Path | None = None
    try:
        source_path = Path(args.source_file)
        draft, raw_bytes, source_sha = validate_content_only_draft(source_path)
        kind = str(draft["kind"])
        round_id = normalize_round_id(args.round)
        source = supervised_draft_source(
            run,
            source_path,
            actor=args.actor,
            round_id=round_id,
            allow_manual_source=args.allow_manual_source,
        )
        records = parse_run_records(run)
        if kind != "author_artifact":
            if not args.artifact_version:
                raise ValueError(f"{kind} promotion requires --artifact-version")
            verify_target_artifact(run, args.artifact_version)
            if (
                source.input_artifact_version_id_or_null is not None
                and source.input_artifact_version_id_or_null != args.artifact_version
            ):
                raise ValueError(
                    "draft source execution attempt targets another ArtifactVersion"
                )
        elif (
            source.input_artifact_version_id_or_null is not None
            and source.input_artifact_version_id_or_null != args.predecessor
        ):
            raise ValueError(
                "Author draft source execution attempt does not target the declared predecessor"
            )
        source_record_ids = list(dict.fromkeys(args.source_record))
        review_batch = None
        if kind == "reviewer_findings":
            review_batch = next(
                (
                    record
                    for record in records_by_type(records, "ReviewBatch")
                    if record.data.get("review_batch_id") == args.review_batch
                ),
                None,
            )
            if review_batch is None:
                raise ValueError(f"ReviewBatch not found: {args.review_batch}")
            if review_batch.data.get("target_artifact_version_id") != args.artifact_version:
                raise ValueError("ReviewBatch targets another ArtifactVersion")
            if source.prompt_source_path_or_null is not None:
                expected_prompt = prompt_target(
                    run,
                    PromptCommandInput(
                        run=str(run),
                        phase="reviewer",
                        actor=args.actor,
                        artifact_version=args.artifact_version,
                        round=round_id,
                        review_batch=args.review_batch,
                        output=None,
                        force_draft=False,
                        dry_run=False,
                    ),
                    records,
                )
                actual_prompt = run / source.prompt_source_path_or_null
                if actual_prompt.resolve() != expected_prompt.resolve():
                    raise ValueError(
                        "draft source prompt belongs to another ReviewBatch"
                    )
        binding = {
            "run_id": run.name,
            "kind": kind,
            "source_sha256": source_sha,
            "actor": args.actor,
            "round_id": round_id,
            "artifact_version_or_null": args.artifact_version,
            "review_batch_or_null": args.review_batch,
            "validator_id_or_null": args.validator_id,
            "predecessor_or_null": args.predecessor,
            "content_locator_or_null": args.content_locator,
            "source_record_ids": source_record_ids,
        }
        promotion_digest = hashlib.sha256(
            json.dumps(binding, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).hexdigest()
        promotion_id = f"promotion-{kind.replace('_', '-')}-{promotion_digest[:16]}"
        captured_path = run / "drafts" / "captured" / f"{promotion_id}.json"

        if kind == "author_artifact":
            if not args.content_locator:
                raise ValueError("author_artifact promotion requires --content-locator")
            artifact_id = f"v-{promotion_digest[:16]}"
            target = run / "artifacts" / f"{artifact_id}.md"
        elif kind == "reviewer_findings":
            if not args.review_batch:
                raise ValueError("reviewer_findings promotion requires --review-batch")
            target = round_dir(run, round_id) / "reviews" / "promotions" / f"{promotion_id}.md"
        elif kind == "validator_output":
            if not args.validator_id:
                raise ValueError("validator_output promotion requires --validator-id")
            target = round_dir(run, round_id) / "validations" / f"{promotion_id}.md"
        else:
            if not args.source_record:
                raise ValueError("synthesis promotion requires at least one --source-record")
            target = round_dir(run, round_id) / "synthesis" / f"{promotion_id}.md"

        expected_receipt_type = {
            "author_artifact": "ArtifactVersion",
            "reviewer_findings": "RawReviewerOutput",
            "validator_output": "ValidationEvidence",
        }.get(kind)
        attempt_source = None
        if source.execution_attempt_id_or_null is not None and expected_receipt_type:
            attempt_source = receipt_attempt_source_by_id(
                run, source.execution_attempt_id_or_null
            )

        if target.exists() and captured_path.exists():
            if sha256_file(captured_path) != source_sha:
                raise ValueError(f"captured draft conflicts with existing promotion: {promotion_id}")
            promoted_records = parse_records_from_file(target)
            receipt = next(
                (
                    record
                    for record in promoted_records
                    if record.record_type == expected_receipt_type
                ),
                None,
            )
            if attempt_source is not None and expected_receipt_type is not None:
                if receipt is None:
                    raise ValueError(f"{expected_receipt_type} receipt was not written")
                complete_attempt_for_receipt_locked(run, attempt_source, receipt)
            prior_event = any(
                event.get("event_type") == "draft_promoted"
                and isinstance(event.get("details"), dict)
                and event["details"].get("promotion_id") == promotion_id
                for event in read_run_events(run)
            )
            args.suppress_run_event = prior_event
            args.promotion_id = promotion_id
            args.source_draft_sha256 = source_sha
            args.promoted_record_ids = [record.record_id for record in promoted_records]
            print(f"draft already promoted: {promotion_id}")
            return 0
        if target.exists() or captured_path.exists():
            raise ValueError(f"incomplete existing promotion requires operator repair: {promotion_id}")
        if attempt_source is not None and expected_receipt_type:
            assert_attempt_accepts_receipt_locked(
                run, attempt_source, expected_receipt_type
            )
        write_bytes_new(captured_path, raw_bytes)
        created_at = utc_now()
        provenance = provenance_fields(source, captured_path, run, source_sha)
        promoted_record_ids: list[str] = []
        if kind == "author_artifact":
            locator = Path(args.content_locator)
            locator_path = locator if locator.is_absolute() else Path.cwd() / locator
            content_hash = sha256_file(locator_path) if locator_path.is_file() else None
            record = {
                "record_type": "ArtifactVersion",
                "schema_version": "m2-markdown-2",
                "run_id": run.name,
                "actor_identity": "orchestrator-draft-finalizer",
                "created_at": created_at,
                "artifact_version_id": artifact_id,
                "predecessor_id_or_null": args.predecessor,
                "content_locator": args.content_locator,
                "content_hash_or_null": content_hash,
                "content_locator_base_or_null": str(Path.cwd().resolve()),
                "produced_by": args.actor,
                "draft_promotion_id": promotion_id,
                **provenance,
            }
            body = "\n".join(
                [
                    frontmatter(record),
                    "",
                    f"# Artifact Version {artifact_id}",
                    "",
                    f"## Summary\n\n{draft['summary']}",
                    "",
                    "## Assumptions",
                    *[f"- {item}" for item in draft["assumptions"]],
                    "",
                    "## Known Limitations",
                    *[f"- {item}" for item in draft["known_limitations"]],
                    "",
                ]
            )
            promoted_record_ids = [artifact_id]
        elif kind == "reviewer_findings":
            raw_output_id = f"raw-output-{promotion_digest[:16]}"
            findings = list(draft["findings"])
            finding_ids = [
                f"raw-finding-{promotion_digest[:12]}-{index:03d}"
                for index in range(1, len(findings) + 1)
            ]
            review_record = {
                "record_type": "RawReviewerOutput",
                "schema_version": "m2-markdown-2",
                "run_id": run.name,
                "actor_identity": "orchestrator-draft-finalizer",
                "created_at": created_at,
                "raw_output_id": raw_output_id,
                "reviewer_identity": args.actor,
                "review_batch_id": args.review_batch,
                "artifact_version_id": args.artifact_version,
                "raw_finding_ids": finding_ids,
                "is_first_round_independent": bool(
                    round_id == "round-1"
                    and review_batch is not None
                    and review_batch.data.get("review_mode") == "fresh_review"
                ),
                "draft_promotion_id": promotion_id,
                **provenance,
            }
            sections = [
                f"# Promoted Reviewer Draft {promotion_id}",
                "",
                f"## RawReviewerOutput {raw_output_id}",
                frontmatter(review_record),
                "",
                "### Reviewer Text",
                "",
                str(draft["review_text"]),
                "",
            ]
            for finding_id, finding in zip(finding_ids, findings, strict=True):
                sections.extend(
                    [
                        f"## RawFinding {finding_id}",
                        frontmatter(
                            {
                                "record_type": "RawFinding",
                                "schema_version": "m2-markdown-2",
                                "run_id": run.name,
                                "actor_identity": "orchestrator-draft-finalizer",
                                "created_at": created_at,
                                "raw_finding_id": finding_id,
                                "reviewer_identity": args.actor,
                                "artifact_version_id": args.artifact_version,
                                "review_batch_id": args.review_batch,
                                **finding,
                                "draft_promotion_id": promotion_id,
                            }
                        ),
                        "",
                    ]
                )
            body = "\n".join(sections)
            promoted_record_ids = [raw_output_id, *finding_ids]
        elif kind == "validator_output":
            evidence_id = f"validation-evidence-{promotion_digest[:16]}"
            record = {
                "record_type": "ValidationEvidence",
                "schema_version": "m2-markdown-2",
                "run_id": run.name,
                "actor_identity": "orchestrator-draft-finalizer",
                "created_at": created_at,
                "validation_evidence_id": evidence_id,
                "validator_id": args.validator_id,
                "target_artifact_version_id": args.artifact_version,
                "result": draft["result"],
                "payload_reference": str(captured_path.relative_to(run)),
                "payload_sha256": source_sha,
                "produced_by": args.actor,
                "waiver_authority_or_null": None,
                "waiver_rationale_or_null": None,
                "draft_promotion_id": promotion_id,
                **provenance,
            }
            body = "\n".join(
                [
                    f"# Promoted Validator Draft {promotion_id}",
                    "",
                    f"## ValidationEvidence {evidence_id}",
                    frontmatter(record),
                    "",
                    "## Validator Evidence",
                    "",
                    str(draft["evidence"]),
                    "",
                ]
            )
            promoted_record_ids = [evidence_id]
        else:
            body = "\n".join(
                [
                    f"# Promoted Synthesis Draft {promotion_id}",
                    "",
                    f"- actor: `{args.actor}`",
                    f"- source_draft_sha256: `{source_sha}`",
                    "- source_record_ids:",
                    *[f"  - `{record_id}`" for record_id in source_record_ids],
                    "",
                    str(draft["text"]),
                    "",
                ]
            )
        atomic_write_new(target, body)
        if attempt_source is not None and expected_receipt_type is not None:
            receipt = next(
                (
                    record
                    for record in parse_records_from_file(target)
                    if record.record_type == expected_receipt_type
                ),
                None,
            )
            if receipt is None:
                raise ValueError(f"{expected_receipt_type} receipt was not written")
            complete_attempt_for_receipt_locked(run, attempt_source, receipt)
        args.promotion_id = promotion_id
        args.source_draft_sha256 = source_sha
        args.promoted_record_ids = promoted_record_ids
        print(f"promoted draft: {promotion_id}")
        print(f"captured source: {captured_path}")
        print(f"promoted target: {target}")
        return 0
    except Exception as exc:
        if (
            captured_path is not None
            and captured_path.exists()
            and (target is None or not target.exists())
        ):
            captured_path.unlink()
        print(f"error: {exc}", file=sys.stderr)
        return 1
