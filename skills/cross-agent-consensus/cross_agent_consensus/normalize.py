"""Skeleton generator for NormalizationRecord and CanonicalFinding blocks."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from cross_agent_consensus.io import atomic_write_new, eprint, utc_now
from cross_agent_consensus.layout import normalize_round_id, round_dir
from cross_agent_consensus.markdown_records import frontmatter
from cross_agent_consensus.models import Record
from cross_agent_consensus.records import canonical_finding_ids, parse_run_records, records_by_type


_WHITESPACE_RE = re.compile(r"\s+")

# Exact content `init` writes to rounds/<round>/normalization.md; safe to overwrite silently
# on the first `normalize` call because it has no authored content to preserve.
_INIT_STUB_NORMALIZATION = "# Normalization\n\nNo normalization records have been recorded for this round.\n"


def _is_init_stub_normalization(path: Path) -> bool:
    try:
        return path.read_text(encoding="utf-8") == _INIT_STUB_NORMALIZATION
    except OSError:
        return False


def _normalize_claim_key(value: Any) -> str:
    text = _WHITESPACE_RE.sub(" ", str(value or "").strip().lower())
    return text


def _round_id_by_review_batch(records: list[Record]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for record in records_by_type(records, "ReviewBatch"):
        batch_id = record.data.get("review_batch_id")
        round_id = record.data.get("round_id")
        if batch_id and round_id:
            mapping[str(batch_id)] = str(round_id)
    return mapping


def _existing_canonical_numbers(records: list[Record], round_id: str) -> set[int]:
    pattern = re.compile(r"^cf-" + re.escape(round_id) + r"-(\d+)$")
    numbers: set[int] = set()
    for canonical_id in canonical_finding_ids(records):
        match = pattern.match(canonical_id)
        if match:
            numbers.add(int(match.group(1)))
    return numbers


def _next_canonical_id(round_id: str, used: set[int]) -> str:
    number = 1
    while number in used:
        number += 1
    used.add(number)
    return f"cf-{round_id}-{number:03d}"


def _raw_findings_for_round(records: list[Record], round_id: str) -> list[Record]:
    batch_to_round = _round_id_by_review_batch(records)
    target = normalize_round_id(round_id)
    findings: list[Record] = []
    for record in records_by_type(records, "RawFinding"):
        batch_id = record.data.get("review_batch_id")
        if not batch_id:
            continue
        if batch_to_round.get(str(batch_id)) == target:
            findings.append(record)
    return findings


def _bucket_findings(findings: list[Record], merge_overlap: bool) -> list[list[Record]]:
    if not merge_overlap:
        return [[finding] for finding in findings]
    buckets: dict[tuple[str, str], list[Record]] = {}
    order: list[tuple[str, str]] = []
    for finding in findings:
        key = (
            str(finding.data.get("location") or ""),
            _normalize_claim_key(finding.data.get("claim")),
        )
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(finding)
    return [buckets[key] for key in order]


def _render_record_block(
    *,
    heading: str,
    frontmatter_data: dict[str, Any],
    notes_heading: str,
    notes_lines: list[str],
) -> str:
    return "\n".join(
        [
            "",
            f"## {heading}",
            frontmatter(frontmatter_data),
            "",
            f"### {notes_heading}",
            "",
            *notes_lines,
            "",
        ]
    )


def _render_normalization_block(
    *,
    run_id: str,
    actor: str,
    created_at: str,
    round_id: str,
    bucket: list[Record],
    canonical_id: str,
) -> str:
    primary = bucket[0]
    raw_ids = [str(finding.data.get("raw_finding_id")) for finding in bucket if finding.data.get("raw_finding_id")]
    normalization_id = f"normalization-{canonical_id}"
    materiality = primary.data.get("severity_or_materiality_claim") or "# TODO: set materiality"
    is_merged = len(bucket) > 1
    rationale = "# TODO: merge across raw findings" if is_merged else "# TODO: merge with reviewer claim"
    return _render_record_block(
        heading=f"NormalizationRecord {normalization_id}",
        frontmatter_data={
            "record_type": "NormalizationRecord",
            "schema_version": "m2-markdown-1",
            "run_id": run_id,
            "actor_identity": actor,
            "created_at": created_at,
            "normalization_record_id": normalization_id,
            "source_raw_finding_ids": raw_ids,
            "normalizer_identity": actor,
            "classifier_identity": actor,
            "materiality": materiality,
            "scope_classification": primary.data.get("scope_classification") or "in_scope",
            "blocking_status": primary.data.get("blocking_status") or "non_blocking",
            "rationale": rationale,
            "canonical_finding_id": canonical_id,
        },
        notes_heading="Normalization Notes",
        notes_lines=[
            f"- source raw findings: {', '.join(raw_ids) or 'none'}",
            "- merge rationale: # TODO",
        ],
    )


def _render_canonical_block(
    *,
    run_id: str,
    actor: str,
    created_at: str,
    bucket: list[Record],
    canonical_id: str,
) -> str:
    primary = bucket[0]
    raw_ids = [str(finding.data.get("raw_finding_id")) for finding in bucket if finding.data.get("raw_finding_id")]
    normalization_id = f"normalization-{canonical_id}"
    claim = primary.data.get("claim") or "# TODO: copy claim"
    is_merged = len(bucket) > 1
    if is_merged:
        rationale_or_summary = "# TODO: merge rationale from " + ", ".join(raw_ids)
    else:
        rationale_or_summary = f"# TODO: merge\n\n{primary.data.get('evidence') or ''}".rstrip()
    materiality = primary.data.get("severity_or_materiality_claim") or "# TODO: set materiality"
    return _render_record_block(
        heading=f"CanonicalFinding {canonical_id}",
        frontmatter_data={
            "record_type": "CanonicalFinding",
            "schema_version": "m2-markdown-1",
            "run_id": run_id,
            "actor_identity": actor,
            "created_at": created_at,
            "canonical_finding_id": canonical_id,
            "target_artifact_version_id": primary.data.get("artifact_version_id") or "# TODO: artifact version",
            "source_raw_finding_ids": raw_ids,
            "normalization_record_id": normalization_id,
            "materiality": materiality,
            "materiality_status": "undisputed",
            "scope_classification": primary.data.get("scope_classification") or "in_scope",
            "blocking_status": primary.data.get("blocking_status") or "non_blocking",
            "lifecycle_state": "open",
            "claim": claim,
            "rationale_or_summary": rationale_or_summary,
            "clarification_pending": False,
        },
        notes_heading="Canonical Notes",
        notes_lines=[
            f"- source raw findings: {', '.join(raw_ids) or 'none'}",
            "- required action: # TODO",
        ],
    )


def build_normalization_skeleton(
    run: Path,
    round_id: str,
    *,
    actor: str = "orchestrator-consensus-tool",
    merge_overlap: bool = False,
) -> str:
    """Render `## NormalizationRecord` + `## CanonicalFinding` sections for one round.

    Returns the full markdown body (including the level-1 heading) suitable for
    writing to `rounds/<round>/normalization.md`.
    """
    records = parse_run_records(run)
    normalized_round = normalize_round_id(round_id)
    findings = _raw_findings_for_round(records, normalized_round)
    used_numbers = _existing_canonical_numbers(records, normalized_round)
    buckets = _bucket_findings(findings, merge_overlap)
    created_at = utc_now()
    body = [f"# Normalization {normalized_round}", ""]
    if not buckets:
        body.extend(["No RawFinding records exist for this round yet.", ""])
        return "\n".join(body)
    for bucket in buckets:
        canonical_id = _next_canonical_id(normalized_round, used_numbers)
        body.append(
            _render_normalization_block(
                run_id=run.name,
                actor=actor,
                created_at=created_at,
                round_id=normalized_round,
                bucket=bucket,
                canonical_id=canonical_id,
            )
        )
        body.append(
            _render_canonical_block(
                run_id=run.name,
                actor=actor,
                created_at=created_at,
                bucket=bucket,
                canonical_id=canonical_id,
            )
        )
    return "\n".join(body)


def cmd_normalize(args: argparse.Namespace) -> int:
    run = Path(args.run)
    try:
        body = build_normalization_skeleton(
            run,
            args.round,
            actor=args.actor,
            merge_overlap=args.merge_overlap,
        )
        target = round_dir(run, args.round) / "normalization.md"
        if target.exists() and (args.overwrite or _is_init_stub_normalization(target)):
            target.unlink()
        atomic_write_new(target, body)
        print(f"wrote normalization skeleton: {target}")
        return 0
    except FileExistsError as exc:
        eprint(f"error: {exc}")
        eprint("hint: pass --overwrite to replace the existing normalization.md")
        return 1
    except Exception as exc:
        eprint(f"error: {exc}")
        return 1
