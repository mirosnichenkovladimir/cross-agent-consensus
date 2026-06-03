"""Run layout and round path policy for cross-agent-consensus runs."""

from __future__ import annotations

import re
from pathlib import Path

from cross_agent_consensus.models import Record


DEFAULT_LAYOUT = "round-first"
ROUND_FIRST_LAYOUT_VERSION = "round-first-1"
LEDGER_LAYOUT_VERSION = "m2-ledger-1"
REPORT_FILENAME = "report.md"


def detect_run_layout(run: Path) -> str:
    if (run / "run.md").exists() or (run / "rounds").exists():
        return DEFAULT_LAYOUT
    return "ledger"


def normalize_round_id(value: str | None) -> str:
    if not value:
        return "round-1"
    if value.startswith("round-"):
        return value
    return f"round-{value}"


def round_number(value: str | None) -> int:
    round_id = normalize_round_id(value)
    match = re.fullmatch(r"round-(\d+)", round_id)
    if not match:
        raise ValueError(f"round must be a positive integer or round-<n>: {value}")
    number = int(match.group(1))
    if number < 1:
        raise ValueError(f"round must be positive: {value}")
    return number


def round_dir(run: Path, value: str | None = None) -> Path:
    return run / "rounds" / f"round-{round_number(value):03d}"


def round_id_from_number(value: int) -> str:
    return f"round-{value}"


def record_path_round_number(path: Path) -> int | None:
    parts = path.parts
    for index, part in enumerate(parts):
        if part == "rounds" and index + 1 < len(parts):
            try:
                return round_number(parts[index + 1])
            except ValueError:
                return None
    return None


def record_round_number(record: Record) -> int:
    return round_number(str(record.data.get("round_id")))


def required_run_paths(run: Path, layout: str | None = None) -> list[Path]:
    layout = layout or detect_run_layout(run)
    if layout == DEFAULT_LAYOUT:
        first_round = round_dir(run, "round-1")
        return [
            run / "run.md",
            run / "validation.md",
            run / "escalations.md",
            run / "backlog.md",
            run / "artifacts",
            run / "rounds",
            first_round,
            first_round / "round.md",
            first_round / "prompts",
            first_round / "prompts" / "reviewers",
            first_round / "prompts" / "validators",
            first_round / "raw",
            first_round / "raw" / "reviewers",
            first_round / "raw" / "validators",
            first_round / "reviews",
            first_round / "rereviews",
            first_round / "normalization.md",
            first_round / "author-responses.md",
            first_round / "validation.md",
            first_round / "backlog.md",
        ]
    return [
        run / "init.md",
        run / "review-batches.md",
        run / "validation.md",
        run / "escalations.md",
        run / "backlog.md",
        run / "artifacts",
        run / "reviews",
        run / "normalization",
        run / "author-responses",
        run / "rereviews",
        run / "payloads",
        run / "payloads" / "prompts",
        run / "payloads" / "raw",
    ]


def make_run_tree(run: Path, layout: str | None = None) -> None:
    for path in required_run_paths(run, layout=layout):
        if path.suffix:
            path.parent.mkdir(parents=True, exist_ok=True)
        else:
            path.mkdir(parents=True, exist_ok=True)
