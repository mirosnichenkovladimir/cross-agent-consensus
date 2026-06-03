"""Run record repository helpers for cross-agent-consensus."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from cross_agent_consensus.markdown_records import parse_records_from_file
from cross_agent_consensus.models import Record


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


def parse_run_records(run: Path) -> list[Record]:
    records: list[Record] = []
    for path in sorted(run.rglob("*.md")):
        if is_protocol_payload_path(path):
            continue
        records.extend(parse_records_from_file(path))
    return records


def records_by_type(records: Iterable[Record], record_type: str) -> list[Record]:
    return [record for record in records if record.record_type == record_type]


def first_record(records: Iterable[Record], record_type: str) -> Record | None:
    for record in records:
        if record.record_type == record_type:
            return record
    return None


def canonical_finding_ids(records: Iterable[Record]) -> set[str]:
    return {str(record.data.get("canonical_finding_id")) for record in records_by_type(records, "CanonicalFinding")}
