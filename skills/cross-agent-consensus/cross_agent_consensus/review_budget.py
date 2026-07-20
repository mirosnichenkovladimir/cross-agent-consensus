"""Cross-run review-batch budget ledger."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cross_agent_consensus.integrity import canonical_json_sha256
from cross_agent_consensus.io import (
    append_jsonl,
    atomic_write_json,
    safe_relative_path,
    utc_now,
)
from cross_agent_consensus.models import Record
from cross_agent_consensus.records import first_record, records_by_type
from cross_agent_consensus.run_audit import exclusive_file_lock


REVIEW_BUDGET_ROOT = ".cac-review-budgets"
REVIEW_BUDGET_EVENT_SCHEMA = "cross-agent-consensus-review-budget-event-1"
REVIEW_BUDGET_ANCHOR_SCHEMA = "cross-agent-consensus-review-budget-anchor-1"


@dataclass(frozen=True)
class ReviewBudgetStatus:
    review_budget_id: str
    max_launched_review_batches: int
    max_fresh_review_batches: int
    launched_review_batches: int
    launched_fresh_review_batches: int

    @property
    def remaining_review_batches(self) -> int:
        return max(0, self.max_launched_review_batches - self.launched_review_batches)


def _budget_record(records: list[Record]) -> Record | None:
    return first_record(records, "ReviewBudget")


def _budget_paths(run: Path, review_budget_id: str) -> tuple[Path, Path, Path]:
    if not review_budget_id:
        raise ValueError("review_budget_id must be non-empty")
    relative_id = safe_relative_path(review_budget_id, "review_budget_id")
    budget_dir = run.parent / REVIEW_BUDGET_ROOT / relative_id
    return budget_dir / "events.jsonl", budget_dir / "anchor.json", budget_dir / ".lock"


def review_budget_ledger_path(run: Path, review_budget_id: str) -> str:
    if not review_budget_id:
        raise ValueError("review_budget_id must be non-empty")
    relative_id = safe_relative_path(review_budget_id, "review_budget_id")
    return str(Path("..") / REVIEW_BUDGET_ROOT / relative_id / "events.jsonl")


def _event_sha256(event: dict[str, Any]) -> str:
    return canonical_json_sha256({key: value for key, value in event.items() if key != "event_sha256"})


def _read_events(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def _write_anchor(path: Path, events_path: Path, event: dict[str, Any]) -> None:
    digest = hashlib.sha256(events_path.read_bytes()).hexdigest()
    atomic_write_json(
        path,
        {
            "schema_version": REVIEW_BUDGET_ANCHOR_SCHEMA,
            "review_budget_id": event["review_budget_id"],
            "event_count": event["sequence"],
            "last_event_sha256": event["event_sha256"],
            "events_jsonl_sha256": digest,
        },
    )


def _append_event_locked(
    events_path: Path,
    anchor_path: Path,
    review_budget_id: str,
    event_type: str,
    *,
    run_id: str,
    details: dict[str, Any],
) -> dict[str, Any]:
    events = _read_events(events_path)
    event: dict[str, Any] = {
        "schema_version": REVIEW_BUDGET_EVENT_SCHEMA,
        "sequence": len(events) + 1,
        "created_at": utc_now(),
        "review_budget_id": review_budget_id,
        "run_id": run_id,
        "event_type": event_type,
        "details": details,
        "previous_event_sha256_or_null": events[-1].get("event_sha256") if events else None,
    }
    event["event_sha256"] = _event_sha256(event)
    append_jsonl(events_path, event)
    _write_anchor(anchor_path, events_path, event)
    return event


def review_budget_event_messages(run: Path, records: list[Record]) -> list[str]:
    record = _budget_record(records)
    if record is None:
        return []
    data = record.data
    review_budget_id = str(data.get("review_budget_id") or "")
    events_path, anchor_path, _ = _budget_paths(run, review_budget_id)
    messages: list[str] = []
    expected_ledger_path = review_budget_ledger_path(run, review_budget_id)
    if data.get("ledger_path") != expected_ledger_path:
        messages.append(
            f"ReviewBudget ledger_path must be {expected_ledger_path}, not {data.get('ledger_path')}"
        )
    if not events_path.is_file():
        # A new run has spent no review budget. The shared ledger is created
        # atomically immediately before the first reviewer launch.
        return messages
    events = _read_events(events_path)
    if not events:
        return [f"ReviewBudget ledger has no readable events: {events_path}"]
    previous_sha256: str | None = None
    for expected_sequence, event in enumerate(events, 1):
        if event.get("schema_version") != REVIEW_BUDGET_EVENT_SCHEMA:
            messages.append(f"ReviewBudget event {expected_sequence} has an unsupported schema")
        if event.get("sequence") != expected_sequence:
            messages.append(f"ReviewBudget event sequence must be {expected_sequence}")
        if event.get("review_budget_id") != review_budget_id:
            messages.append(f"ReviewBudget event {expected_sequence} names another review_budget_id")
        if event.get("previous_event_sha256_or_null") != previous_sha256:
            messages.append(f"ReviewBudget event {expected_sequence} has a broken predecessor digest")
        actual_sha256 = _event_sha256(event)
        if event.get("event_sha256") != actual_sha256:
            messages.append(f"ReviewBudget event {expected_sequence} digest changed")
        previous_sha256 = actual_sha256
    created = events[0]
    created_details = created.get("details")
    if created.get("event_type") != "review_budget_created" or not isinstance(created_details, dict):
        messages.append("ReviewBudget ledger must start with review_budget_created")
    elif (
        created_details.get("max_launched_review_batches") != data.get("max_launched_review_batches")
        or created_details.get("max_fresh_review_batches") != data.get("max_fresh_review_batches")
    ):
        messages.append("ReviewBudget record limits differ from the shared ledger")
    linked_runs = {
        str(event.get("run_id"))
        for event in events
        if event.get("event_type") == "run_linked"
    }
    if run.name not in linked_runs:
        messages.append(f"ReviewBudget ledger does not link run {run.name}")
    try:
        anchor = json.loads(anchor_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        messages.append(f"ReviewBudget anchor is missing or unreadable: {anchor_path}")
    else:
        ledger_sha256 = hashlib.sha256(events_path.read_bytes()).hexdigest()
        if anchor.get("schema_version") != REVIEW_BUDGET_ANCHOR_SCHEMA:
            messages.append("ReviewBudget anchor has an unsupported schema")
        if anchor.get("event_count") != len(events):
            messages.append("ReviewBudget anchor event_count differs")
        if anchor.get("last_event_sha256") != events[-1].get("event_sha256"):
            messages.append("ReviewBudget anchor leaf digest differs")
        if anchor.get("events_jsonl_sha256") != ledger_sha256:
            messages.append("ReviewBudget anchor ledger digest differs")
    return messages


def link_run_to_review_budget(run: Path, records: list[Record]) -> None:
    record = _budget_record(records)
    if record is None:
        return
    data = record.data
    review_budget_id = str(data["review_budget_id"])
    expected_ledger_path = review_budget_ledger_path(run, review_budget_id)
    if data.get("ledger_path") != expected_ledger_path:
        raise ValueError(
            f"ReviewBudget ledger_path must be {expected_ledger_path}, not {data.get('ledger_path')}"
        )
    events_path, anchor_path, lock_path = _budget_paths(run, review_budget_id)
    with exclusive_file_lock(lock_path):
        events = _read_events(events_path)
        if not events:
            _append_event_locked(
                events_path,
                anchor_path,
                review_budget_id,
                "review_budget_created",
                run_id=run.name,
                details={
                    "max_launched_review_batches": data["max_launched_review_batches"],
                    "max_fresh_review_batches": data["max_fresh_review_batches"],
                },
            )
            events = _read_events(events_path)
        created_details = events[0].get("details")
        if not isinstance(created_details, dict) or any(
            created_details.get(field) != data.get(field)
            for field in ("max_launched_review_batches", "max_fresh_review_batches")
        ):
            raise ValueError(
                f"ReviewBudget {review_budget_id} already exists with different limits"
            )
        if any(
            event.get("event_type") == "run_linked" and event.get("run_id") == run.name
            for event in events
        ):
            return
        _append_event_locked(
            events_path,
            anchor_path,
            review_budget_id,
            "run_linked",
            run_id=run.name,
            details={},
        )


def _overrun_human_decision(
    records: list[Record],
    *,
    review_budget_id: str,
    review_batch_id: str,
) -> Record | None:
    participants = first_record(records, "Participants")
    supervisor = (
        participants.data.get("human_supervisor_identity_or_null")
        if participants is not None
        else None
    )
    if not isinstance(supervisor, str) or supervisor == "none":
        return None
    for decision in reversed(records_by_type(records, "HumanDecision")):
        if (
            decision.data.get("decision_type") == "authorize_review_budget_overrun"
            and decision.data.get("review_budget_id") == review_budget_id
            and decision.data.get("approved_review_batch_id") == review_batch_id
            and decision.data.get("binding_authority") == supervisor
            and decision.data.get("affected_finding_ids_or_validator_ids") == ["__run_scope__"]
        ):
            return decision
    return None


def register_review_batch_launch(run: Path, records: list[Record], batch: Record) -> None:
    record = _budget_record(records)
    if record is None:
        return
    data = record.data
    review_budget_id = str(data["review_budget_id"])
    review_batch_id = str(batch.data.get("review_batch_id") or batch.record_id)
    link_run_to_review_budget(run, records)
    events_path, anchor_path, lock_path = _budget_paths(run, review_budget_id)
    with exclusive_file_lock(lock_path):
        messages = review_budget_event_messages(run, records)
        if messages:
            raise ValueError("; ".join(messages))
        events = _read_events(events_path)
        launches = [event for event in events if event.get("event_type") == "review_batch_launched"]
        if any(
            event.get("run_id") == run.name
            and isinstance(event.get("details"), dict)
            and event["details"].get("review_batch_id") == review_batch_id
            for event in launches
        ):
            return
        fresh_launches = [
            event
            for event in launches
            if isinstance(event.get("details"), dict)
            and event["details"].get("review_mode") == "fresh_review"
        ]
        exceeds_total = len(launches) >= int(data["max_launched_review_batches"])
        exceeds_fresh = (
            batch.data.get("review_mode") == "fresh_review"
            and len(fresh_launches) >= int(data["max_fresh_review_batches"])
        )
        decision = None
        if exceeds_total or exceeds_fresh:
            decision = _overrun_human_decision(
                records,
                review_budget_id=review_budget_id,
                review_batch_id=review_batch_id,
            )
            if decision is None:
                limits = []
                if exceeds_total:
                    limits.append(f"max_launched_review_batches={data['max_launched_review_batches']}")
                if exceeds_fresh:
                    limits.append(f"max_fresh_review_batches={data['max_fresh_review_batches']}")
                raise ValueError(
                    f"ReviewBudget {review_budget_id} reached {', '.join(limits)}; "
                    "record an exact HumanDecision authorize_review_budget_overrun before launch"
                )
        _append_event_locked(
            events_path,
            anchor_path,
            review_budget_id,
            "review_batch_launched",
            run_id=run.name,
            details={
                "review_batch_id": review_batch_id,
                "round_id": batch.data.get("round_id"),
                "review_mode": batch.data.get("review_mode"),
                "human_decision_id_or_null": decision.record_id if decision else None,
            },
        )


def review_budget_status(run: Path, records: list[Record]) -> ReviewBudgetStatus | None:
    record = _budget_record(records)
    if record is None:
        return None
    data = record.data
    review_budget_id = str(data["review_budget_id"])
    events_path, _, _ = _budget_paths(run, review_budget_id)
    launches = [
        event for event in _read_events(events_path) if event.get("event_type") == "review_batch_launched"
    ]
    fresh_launches = [
        event
        for event in launches
        if isinstance(event.get("details"), dict)
        and event["details"].get("review_mode") == "fresh_review"
    ]
    return ReviewBudgetStatus(
        review_budget_id=review_budget_id,
        max_launched_review_batches=int(data["max_launched_review_batches"]),
        max_fresh_review_batches=int(data["max_fresh_review_batches"]),
        launched_review_batches=len(launches),
        launched_fresh_review_batches=len(fresh_launches),
    )
