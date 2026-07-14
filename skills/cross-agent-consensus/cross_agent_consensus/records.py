"""Run record repository helpers for cross-agent-consensus."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from cross_agent_consensus.markdown_records import parse_records_from_file, parse_records_with_diagnostics
from cross_agent_consensus.models import Record, RecordParseDiagnostic


NARRATIVE_FINDING_ID_RE = re.compile(r"\bR(\d+)-([A-Z][A-Z0-9\-]*)-(\d{1,3})\b")


@dataclass
class RunSnapshot:
    run: Path
    records: list[Record]
    diagnostics: list[RecordParseDiagnostic]
    by_type: dict[str, list[Record]]


class FindingSchemaError(ValueError):
    """A run crossed the historical/current finding schema boundary."""


def unique_narrative_finding_ids(text: str) -> set[str]:
    """Return the set of narrative finding IDs in `text`, lowercased."""
    return {match.group(0).lower() for match in NARRATIVE_FINDING_ID_RE.finditer(text)}


def is_protocol_payload_path(path: Path) -> bool:
    parts = path.parts
    if "payloads" in parts:
        return True
    for index, part in enumerate(parts):
        if part == "rounds" and index + 2 < len(parts):
            round_part = parts[index + 1]
            payload_part = parts[index + 2]
            if re.fullmatch(r"round-\d+", round_part) and payload_part in {"prompts", "raw"}:
                return True
    return False


def parse_run_snapshot(run: Path) -> RunSnapshot:
    records: list[Record] = []
    diagnostics: list[RecordParseDiagnostic] = []
    for path in sorted(run.rglob("*.md")):
        if is_protocol_payload_path(path):
            continue
        parsed = parse_records_with_diagnostics(path)
        records.extend(parsed.records)
        diagnostics.extend(parsed.diagnostics)
    legacy_finding_records = [
        record for record in records if record.finding_schema_origin == "legacy"
    ]
    current_finding_records = [
        record for record in records if record.finding_schema_origin == "current"
    ]
    if legacy_finding_records and current_finding_records:
        current = current_finding_records[0]
        diagnostics.append(
            RecordParseDiagnostic(
                current.path,
                current.heading_line,
                "run mixes historical and current finding records",
                code="finding_schema",
            )
        )
    by_type: dict[str, list[Record]] = {}
    for record in records:
        by_type.setdefault(record.record_type, []).append(record)
    return RunSnapshot(run=run, records=records, diagnostics=diagnostics, by_type=by_type)


def parse_run_records(run: Path) -> list[Record]:
    snapshot = parse_run_snapshot(run)
    finding_schema_diagnostics = [
        diagnostic
        for diagnostic in snapshot.diagnostics
        if diagnostic.code == "finding_schema"
    ]
    if finding_schema_diagnostics:
        messages = "; ".join(
            f"{diagnostic.path}:{diagnostic.heading_line}: {diagnostic.message}"
            for diagnostic in finding_schema_diagnostics
        )
        raise FindingSchemaError(messages)
    return snapshot.records


def records_by_type(records: Iterable[Record], record_type: str) -> list[Record]:
    return [record for record in records if record.record_type == record_type]


def first_record(records: Iterable[Record], record_type: str) -> Record | None:
    for record in records:
        if record.record_type == record_type:
            return record
    return None


def normalized_finding_ids(records: Iterable[Record]) -> set[str]:
    return {
        str(record.data.get("normalized_finding_id"))
        for record in records_by_type(records, "NormalizedFinding")
    }
