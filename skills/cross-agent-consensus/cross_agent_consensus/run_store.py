"""Run id allocation helpers for cross-agent-consensus."""

from __future__ import annotations

from pathlib import Path

from cross_agent_consensus.io import slugify


def run_id_base_from_task(task: str) -> str:
    task_slug = slugify(task)
    if task_slug.endswith("-consensus"):
        return task_slug
    return f"{task_slug}-consensus"


def run_id_from_task(task: str, run_root: Path) -> str:
    base = run_id_base_from_task(task)
    for index in range(1, 1000):
        candidate = f"{base}-{index:03d}"
        if not (run_root / candidate).exists():
            return candidate
    raise ValueError(f"no available run id for task slug: {base}")
