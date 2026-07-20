"""Deterministic single-transition driver for the opt-in remediation profile."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from cross_agent_consensus.integrity import canonical_json_sha256
from cross_agent_consensus.execution_attempts import invocation_action_id
from cross_agent_consensus.layout import normalize_round_id
from cross_agent_consensus.models import (
    BoundedRemediationPlan,
    NextActionPlan,
    Record,
    RunCommandInput,
)
from cross_agent_consensus.next_action import (
    action_identifier_token,
    build_next_action_plan,
    derive_next_action_plan,
)
from cross_agent_consensus.records import first_record, parse_run_records, records_by_type
from cross_agent_consensus.run_audit import read_run_events
from cross_agent_consensus.run_macro import _resolve_actors, cmd_run


BOUNDED_REMEDIATION_PLAN_SCHEMA = "cross-agent-consensus-bounded-remediation-plan-1"
BOUNDED_REMEDIATION_PROFILE = "bounded-remediation"
DISPATCH_PHASES = {
    "awaiting_artifact": "author",
    "awaiting_review": "reviewer",
    "awaiting_validation": "validator",
}


def _latest_round_id(records: list[Record]) -> str:
    batches = records_by_type(records, "ReviewBatch")
    if not batches:
        return "round-1"
    return normalize_round_id(str(batches[-1].data.get("round_id") or "round-1"))


def _checkpoint_payload(next_plan: Any) -> dict[str, Any]:
    return {
        "phase": next_plan.phase,
        "runnable_actions": list(next_plan.runnable_actions),
        "required_records": list(next_plan.required_records),
        "pending_checkpoints": [
            {
                "checkpoint_id": checkpoint.checkpoint_id,
                "checkpoint_type": checkpoint.checkpoint_type,
                "record_id": checkpoint.record_id,
                "choices": [
                    {
                        "choice_id": choice.choice_id,
                        "consequence": choice.consequence,
                    }
                    for choice in checkpoint.choices
                ],
            }
            for checkpoint in next_plan.pending_checkpoints
        ],
        "record_journal_sha256": next_plan.record_journal_sha256,
    }


def _checkpoint_status(
    run: Path,
    records: list[Record],
    checkpoint_id: str | None,
    checkpoint_input_sha256: str,
) -> str:
    if checkpoint_id is None:
        return "not_required"
    matching = [
        record
        for record in records_by_type(records, "OperatorApproval")
        if record.data.get("checkpoint_id") == checkpoint_id
    ]
    if any(
        record.data.get("checkpoint_input_sha256") == checkpoint_input_sha256
        for record in matching
    ):
        return "current"
    if any(
        isinstance(record.data.get("checkpoint_input_sha256"), str)
        and bounded_checkpoint_reservation_is_current(
            run,
            checkpoint_id,
            str(record.data["checkpoint_input_sha256"]),
            record.record_id,
        )
        for record in matching
    ):
        return "reserved"
    return "stale" if matching else "missing"


def bounded_checkpoint_reservation_is_current(
    run: Path,
    checkpoint_id: str,
    checkpoint_input_sha256: str,
    approval_record_id: str | None = None,
) -> bool:
    events = read_run_events(run)
    if not events or events[-1].get("event_type") != "operator_approval_recorded":
        return False
    details = events[-1].get("details")
    if not isinstance(details, dict):
        return False
    if (
        details.get("checkpoint_id_or_null") != checkpoint_id
        or details.get("checkpoint_input_sha256_or_null")
        != checkpoint_input_sha256
    ):
        return False
    recorded_approval_id = details.get("operator_approval_id")
    if approval_record_id is not None and recorded_approval_id != approval_record_id:
        return False
    records = parse_run_records(run)
    approval = next(
        (
            record
            for record in records_by_type(records, "OperatorApproval")
            if record.record_id == recorded_approval_id
        ),
        None,
    )
    if approval is None:
        return False
    prior_records = [record for record in records if record is not approval]
    prior_plan = build_next_action_plan(run.name, prior_records, events[:-1])
    return (
        canonical_json_sha256(_checkpoint_payload(prior_plan))
        == checkpoint_input_sha256
    )


def assert_bounded_checkpoint_reservation_current(
    run: Path,
    checkpoint_id: str,
    checkpoint_input_sha256: str,
    approval_record_id: str,
) -> None:
    if not bounded_checkpoint_reservation_is_current(
        run,
        checkpoint_id,
        checkpoint_input_sha256,
        approval_record_id,
    ):
        raise ValueError(
            "bounded-remediation checkpoint changed before session allocation"
        )


def _missing_reviewers(records: list[Record], round_id: str) -> tuple[str, ...]:
    batches = [
        record
        for record in records_by_type(records, "ReviewBatch")
        if normalize_round_id(str(record.data.get("round_id") or "round-1"))
        == round_id
    ]
    if not batches:
        return ()
    batch = batches[-1]
    batch_id = str(batch.data.get("review_batch_id") or batch.record_id)
    expected = batch.data.get("expected_reviewer_identities")
    if not isinstance(expected, list) or not expected:
        participants = first_record(records, "Participants")
        expected = participants.data.get("reviewer_identities") if participants else []
    captured = {
        str(record.data.get("reviewer_identity"))
        for record in records_by_type(records, "RawReviewerOutput")
        if record.data.get("review_batch_id") == batch_id
    }
    missing = tuple(
        str(identity) for identity in expected or [] if str(identity) not in captured
    )
    return missing[:1]


def _missing_validators(
    records: list[Record], next_plan: NextActionPlan
) -> tuple[str, ...]:
    participants = first_record(records, "Participants")
    identities = participants.data.get("validator_identities") if participants else []
    participant_validators = {str(identity) for identity in identities or []}
    required = {
        parts[1]
        for requirement in next_plan.required_records
        if (parts := requirement.split(":"))
        and len(parts) == 2
        and parts[0] == "ValidationEvidence"
    }
    return tuple(sorted(participant_validators & required))[:1]


def _dispatch_participants(
    records: list[Record], next_plan: NextActionPlan, dispatch_phase: str, round_id: str
) -> tuple[str, ...]:
    if dispatch_phase == "reviewer":
        return _missing_reviewers(records, round_id)
    if dispatch_phase == "validator":
        return _missing_validators(records, next_plan)
    return tuple(
        _resolve_actors(
            records,
            round_id=round_id,
            phase=dispatch_phase,
            requested=None,
        )
    )


def _has_matching_runnable_action(
    next_plan: NextActionPlan,
    dispatch_phase: str,
    participants: tuple[str, ...],
) -> bool:
    actions = set(next_plan.runnable_actions)
    if dispatch_phase == "reviewer":
        return bool(participants) and all(
            invocation_action_id(identity, "reviewer") in actions
            for identity in participants
        )
    if dispatch_phase == "validator":
        return bool(participants) and all(
            f"run-validator-{action_identifier_token(identity)}" in actions
            for identity in participants
        )
    return bool(participants) and any(
        action == "record-artifact-version"
        or action.startswith("record-artifact-version-")
        for action in actions
    )


def _ordinary_operator_wait_allows_review(
    next_plan: NextActionPlan, participants: tuple[str, ...]
) -> bool:
    if next_plan.plan_status != "waiting" or not participants:
        return False
    if any(
        requirement.startswith("OperatorApproval:ambiguous-retry:")
        or requirement.startswith("OperatorApproval:provider-rate-limit-retry:")
        for requirement in next_plan.required_records
    ):
        return False
    checkpoints = next_plan.pending_checkpoints
    if not checkpoints or any(
        checkpoint.checkpoint_type != "operator_approval"
        for checkpoint in checkpoints
    ):
        return False
    checkpoint_ids = {checkpoint.checkpoint_id for checkpoint in checkpoints}
    return all(
        f"approve-{action_identifier_token(identity)}-reviewer" in checkpoint_ids
        for identity in participants
    )


def build_bounded_remediation_plan(
    run: Path, records: list[Record], next_plan: NextActionPlan
) -> BoundedRemediationPlan:
    policy = first_record(records, "Policy")
    profile = str(policy.data.get("profile") or "") if policy else ""
    dispatch_phase = DISPATCH_PHASES.get(next_plan.phase)
    round_id = _latest_round_id(records) if dispatch_phase else None
    participants: tuple[str, ...] = ()
    blockers = list(next_plan.blockers)
    if profile != BOUNDED_REMEDIATION_PROFILE:
        blockers.append(
            f"Policy.profile is {profile or '<missing>'}; expected {BOUNDED_REMEDIATION_PROFILE}"
        )
        dispatch_phase = None
        round_id = None
    elif dispatch_phase is not None and round_id is not None:
        participants = _dispatch_participants(
            records, next_plan, dispatch_phase, round_id
        )
        if not participants:
            blockers.append(
                f"Participants has no ParticipantIdentity for {dispatch_phase} phase"
            )
    human_checkpoint = any(
        checkpoint.checkpoint_type != "operator_approval"
        for checkpoint in next_plan.pending_checkpoints
    )
    checkpoint_id = (
        f"bounded-remediation-{dispatch_phase}" if dispatch_phase is not None else None
    )
    checkpoint_input_sha256 = canonical_json_sha256(_checkpoint_payload(next_plan))
    checkpoint_status = _checkpoint_status(
        run, records, checkpoint_id, checkpoint_input_sha256
    )
    if checkpoint_status == "stale":
        blockers.append(
            f"OperatorApproval {checkpoint_id} is stale for checkpoint input {checkpoint_input_sha256}"
        )
    has_actionable_dispatch = bool(
        dispatch_phase is not None
        and next_plan.plan_status == "actionable"
        and _has_matching_runnable_action(next_plan, dispatch_phase, participants)
    )
    has_ordinary_review_approval = bool(
        dispatch_phase == "reviewer"
        and _ordinary_operator_wait_allows_review(next_plan, participants)
    )
    execution_allowed = bool(
        profile == BOUNDED_REMEDIATION_PROFILE
        and dispatch_phase is not None
        and participants
        and next_plan.terminal_status == "non_terminal"
        and not human_checkpoint
        and (has_actionable_dispatch or has_ordinary_review_approval)
    )
    return BoundedRemediationPlan(
        schema_version=BOUNDED_REMEDIATION_PLAN_SCHEMA,
        run_id=run.name,
        phase=next_plan.phase,
        plan_status=next_plan.plan_status,
        terminal_status=next_plan.terminal_status,
        record_journal_sha256=next_plan.record_journal_sha256,
        checkpoint_id_or_null=checkpoint_id,
        checkpoint_input_sha256=checkpoint_input_sha256,
        checkpoint_status=checkpoint_status,
        dispatch_phase_or_null=dispatch_phase,
        round_id_or_null=round_id,
        participant_identities=participants,
        execution_allowed=execution_allowed,
        publication_authorized=False,
        blockers=tuple(sorted(set(blockers))),
        required_records=next_plan.required_records,
    )


def derive_bounded_remediation_plan(run: Path) -> BoundedRemediationPlan:
    records = parse_run_records(run)
    return build_bounded_remediation_plan(
        run, records, derive_next_action_plan(run)
    )


def bounded_remediation_plan_dict(plan: BoundedRemediationPlan) -> dict[str, Any]:
    return {
        "schema_version": plan.schema_version,
        "run_id": plan.run_id,
        "phase": plan.phase,
        "plan_status": plan.plan_status,
        "terminal_status": plan.terminal_status,
        "record_journal_sha256": plan.record_journal_sha256,
        "checkpoint_id_or_null": plan.checkpoint_id_or_null,
        "checkpoint_input_sha256": plan.checkpoint_input_sha256,
        "checkpoint_status": plan.checkpoint_status,
        "dispatch_phase_or_null": plan.dispatch_phase_or_null,
        "round_id_or_null": plan.round_id_or_null,
        "participant_identities": list(plan.participant_identities),
        "execution_allowed": plan.execution_allowed,
        "publication_authorized": plan.publication_authorized,
        "blockers": list(plan.blockers),
        "required_records": list(plan.required_records),
    }


def bounded_remediation_plan_json(plan: BoundedRemediationPlan) -> str:
    return json.dumps(
        bounded_remediation_plan_dict(plan),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def cmd_remediate(args: argparse.Namespace) -> int:
    run = Path(args.run)
    plan = derive_bounded_remediation_plan(run)
    if args.json:
        print(bounded_remediation_plan_json(plan))
    else:
        print(f"Phase: {plan.phase}")
        print(f"Dispatch phase: {plan.dispatch_phase_or_null or 'none'}")
        print(f"Checkpoint: {plan.checkpoint_status}")
        print(f"Execution allowed: {'yes' if plan.execution_allowed else 'no'}")
        for blocker in plan.blockers:
            print(f"Blocker: {blocker}")
    if not args.execute:
        return 0 if plan.plan_status != "invalid" else 3
    if not args.approved:
        print("error: --execute requires --approved", file=sys.stderr)
        return 2
    if not args.operator_identity:
        print(
            "error: --execute requires --operator-identity",
            file=sys.stderr,
        )
        return 2
    if (
        args.checkpoint_id != plan.checkpoint_id_or_null
        or args.checkpoint_input_sha256 != plan.checkpoint_input_sha256
    ):
        print(
            "error: supplied bounded-remediation checkpoint is missing or stale; rerun remediate --json",
            file=sys.stderr,
        )
        return 3
    if not plan.execution_allowed:
        print(
            f"error: bounded remediation cannot dispatch phase {plan.phase}",
            file=sys.stderr,
        )
        return 3
    assert plan.dispatch_phase_or_null is not None
    assert plan.round_id_or_null is not None
    return cmd_run(
        RunCommandInput(
            run=str(run),
            round=plan.round_id_or_null,
            phase=plan.dispatch_phase_or_null,
            actors=",".join(plan.participant_identities),
            execute_reviewers=True,
            approved=True,
            sequential=bool(args.sequential),
            cwd=args.cwd,
            idle_timeout_seconds=args.idle_timeout_seconds,
            stale_timeout_seconds=args.stale_timeout_seconds,
            heartbeat_interval_seconds=args.heartbeat_interval_seconds,
            operator_identity=args.operator_identity,
            max_runtime_seconds=getattr(args, "max_runtime_seconds", None),
            approve_provider_rate_limit_retry=bool(
                getattr(args, "approve_provider_rate_limit_retry", False)
            ),
            checkpoint_id=plan.checkpoint_id_or_null,
            checkpoint_input_sha256=plan.checkpoint_input_sha256,
        )
    )


__all__ = [
    "BoundedRemediationPlan",
    "build_bounded_remediation_plan",
    "bounded_remediation_plan_dict",
    "bounded_remediation_plan_json",
    "assert_bounded_checkpoint_reservation_current",
    "cmd_remediate",
    "derive_bounded_remediation_plan",
]
