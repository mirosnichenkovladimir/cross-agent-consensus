"""Deterministic, read-only next-action planning for one CAC run."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from cross_agent_consensus.config import legacy_adapter_for_command
from cross_agent_consensus.io import slugify
from cross_agent_consensus.invocation.readiness import policy_allows_unattended_scoped
from cross_agent_consensus.lifecycle import (
    artifact_chain,
    current_author_response_finding_ids,
    effective_blocking_finding_ids,
    expected_reviewers_for_batch,
    human_decision_awaits_artifact,
    latest_binding_human_decisions,
    latest_escalations_by_affected_id,
    latest_remediation_batches,
    rereview_batch_resolves,
    rereview_decisions_for_batch,
)
from cross_agent_consensus.models import (
    CheckpointChoice,
    NextActionPlan,
    PendingCheckpoint,
    Record,
)
from cross_agent_consensus.records import RunSnapshot, first_record, parse_run_snapshot, records_by_type
from cross_agent_consensus.run_audit import derive_run_phase, read_run_events, recorded_run_version
from cross_agent_consensus.validation import (
    check_links,
    check_integrity,
    check_participants,
    check_records,
    check_reviewer_isolation,
    check_run_events,
    check_terminal,
    remediation_cap_blockers,
    required_validators,
    validator_status,
)


NEXT_ACTION_PLAN_SCHEMA = "cross-agent-consensus-next-action-plan-1"
SINGLETON_RECORD_TYPES = ("TaskBrief", "Policy", "Participants", "ReviewScope", "ConfigResolution")


@dataclass(frozen=True)
class RereviewRequirements:
    missing_decisions: tuple[tuple[str, str, str, str], ...]
    missing_batches: tuple[str, ...]
    revisions: tuple[str, ...]
    new_batches: tuple[str, ...]
    escalations: tuple[str, ...]


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _identifier_token(value: str) -> str:
    """Preserve readable identifiers while disambiguating lossy slug conversions."""

    slug = slugify(value)
    if slug == value:
        return slug
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
    return f"{slug}-{digest}"


def _record_input(record: Record) -> dict[str, Any]:
    return {
        "record_type": record.record_type,
        "record_id": record.record_id,
        "data": record.data,
    }


def _record_journal_sha256(records: list[Record], events: list[dict[str, Any]]) -> str:
    return _canonical_sha256(
        {
            "records": [_record_input(record) for record in records],
            "run_journal": events,
        }
    )


def next_action_plan_dict(plan: NextActionPlan) -> dict[str, Any]:
    return {
        "schema_version": plan.schema_version,
        "run_id": plan.run_id,
        "phase": plan.phase,
        "plan_status": plan.plan_status,
        "terminal_status": plan.terminal_status,
        "runnable_actions": list(plan.runnable_actions),
        "blockers": list(plan.blockers),
        "required_records": list(plan.required_records),
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
            for checkpoint in plan.pending_checkpoints
        ],
        "record_journal_sha256": plan.record_journal_sha256,
    }


def next_action_plan_json(plan: NextActionPlan) -> str:
    """Render byte-stable JSON. The plan deliberately has no observation timestamp."""

    return json.dumps(next_action_plan_dict(plan), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _expected_reviewers(records: list[Record], batch: Record) -> list[str]:
    return sorted(expected_reviewers_for_batch(records, batch))


def _resolved_execution(records: list[Record], participant_identity: str) -> dict[str, Any] | None:
    resolution = first_record(records, "ConfigResolution")
    if resolution is None:
        return None
    identities = resolution.data.get("resolved_participant_identities")
    executions = resolution.data.get("resolved_execution_profiles")
    if isinstance(identities, dict) and isinstance(executions, dict):
        binding = identities.get(participant_identity)
        if isinstance(binding, dict):
            execution = executions.get(binding.get("execution_profile_id"))
            if isinstance(execution, dict):
                return execution
    effective = resolution.data.get("effective_values")
    key = f"reviewer_clis.{participant_identity}.command"
    entry = effective.get(key) if isinstance(effective, dict) else None
    command = entry.get("value") if isinstance(entry, dict) else entry
    if isinstance(command, list) and command and all(isinstance(value, str) for value in command):
        adapter_id, output_mode = legacy_adapter_for_command(command)
        return {
            "execution_profile_id": "legacy-inline-execution-profile",
            "adapter_id": adapter_id,
            "command": command,
            "output_mode": output_mode,
        }
    return None


def _external_action(records: list[Record], participant_identity: str, role: str) -> bool:
    execution = _resolved_execution(records, participant_identity)
    if execution is None:
        return False
    command = execution.get("command")
    return execution.get("adapter_id") != "manual" and isinstance(command, list) and bool(command)


def _operator_checkpoint(
    records: list[Record],
    run_id: str,
    participant_identity: str,
    phase: str,
    round_id: str,
    action_qualifier: str | None = None,
) -> PendingCheckpoint | None:
    if policy_allows_unattended_scoped(
        records,
        run_id=run_id,
        round_id=round_id,
        phase=phase,
        actor=participant_identity,
    ):
        return None
    identity_token = _identifier_token(participant_identity)
    qualifier_suffix = f"-{_identifier_token(action_qualifier)}" if action_qualifier else ""
    return PendingCheckpoint(
        checkpoint_id=f"approve-{identity_token}-{slugify(phase)}{qualifier_suffix}",
        checkpoint_type="operator_approval",
        record_id="OperatorApproval",
        choices=(
            CheckpointChoice(
                "approve_exact_invocation",
                f"CAC may launch {participant_identity} after OperatorApproval binds the prompt, argv, ExecutionProfile, ArtifactVersion, and working directory.",
            ),
            CheckpointChoice(
                "keep_manual",
                f"CAC must not launch {participant_identity}; a human must supply the required protocol record.",
            ),
        ),
    )


def _pending_human_checkpoints(records: list[Record]) -> tuple[PendingCheckpoint, ...]:
    pending: list[PendingCheckpoint] = []
    binding_decisions = latest_binding_human_decisions(records)
    latest_escalations = latest_escalations_by_affected_id(records)
    unresolved_escalations: dict[str, Record] = {}
    for affected_id, escalation in latest_escalations.items():
        if affected_id not in binding_decisions:
            unresolved_escalations[escalation.record_id] = escalation
    for escalation in records_by_type(records, "EscalationRecord"):
        if not escalation.data.get("affected_finding_ids"):
            unresolved_escalations[escalation.record_id] = escalation
    for escalation in unresolved_escalations.values():
        escalation_id = str(escalation.data.get("escalation_record_id") or escalation.record_id)
        pending.append(
            PendingCheckpoint(
                checkpoint_id=f"decide-{_identifier_token(escalation_id)}",
                checkpoint_type="human_decision",
                record_id=escalation_id,
                choices=(
                    CheckpointChoice(
                        "mark_resolved",
                        "A HumanDecision closes the listed NormalizedFinding records and CAC can proceed to required validators.",
                    ),
                    CheckpointChoice(
                        "require_revision",
                        "A HumanDecision requires a new ArtifactVersion before another remediation ReviewBatch.",
                    ),
                    CheckpointChoice(
                        "terminate_escalated_to_human",
                        "A HumanDecision permits a TerminationRecord with terminal_condition escalated_to_human.",
                    ),
                    CheckpointChoice(
                        "abort_run",
                        "A HumanDecision requires an AbortRecord before an aborted TerminationRecord.",
                    ),
                ),
            )
        )
    return tuple(sorted(pending, key=lambda checkpoint: checkpoint.checkpoint_id))


def _singleton_conflicts(records: list[Record]) -> list[str]:
    blockers: list[str] = []
    for record_type in SINGLETON_RECORD_TYPES:
        candidates = records_by_type(records, record_type)
        if len(candidates) > 1:
            ids = ", ".join(sorted(record.record_id for record in candidates))
            blockers.append(f"{record_type} conflict: expected one record, found {ids}")
    termination_records = records_by_type(records, "TerminationRecord")
    if len(termination_records) > 1:
        ids = ", ".join(sorted(record.record_id for record in termination_records))
        blockers.append(f"TerminationRecord conflict: expected at most one record, found {ids}")
    terminal_human_decisions = {
        str(decision.data.get("decision_type"))
        for decision in latest_binding_human_decisions(records).values()
        if decision.data.get("decision_type") in {"terminate_escalated_to_human", "abort_run"}
    }
    if len(terminal_human_decisions) > 1:
        blockers.append(
            "HumanDecision conflict: abort_run and terminate_escalated_to_human are both binding"
        )
    for decision in records_by_type(records, "HumanDecision"):
        if decision.data.get("decision_type") not in {"abort_run", "terminate_escalated_to_human"}:
            continue
        affected_ids = {
            str(value)
            for value in (decision.data.get("affected_finding_ids_or_validator_ids") or [])
        }
        if affected_ids != {"__run_scope__"}:
            blockers.append(
                f"HumanDecision conflict: {decision.record_id} decision_type {decision.data.get('decision_type')} must target exactly __run_scope__"
            )
    blockers.extend(artifact_chain(records).blockers)
    return blockers


def _missing_foundational_records(records: list[Record], require_config_resolution: bool) -> list[str]:
    if first_record(records, "TaskBrief") is None:
        return []
    required_types = ["Policy", "Participants", "ReviewScope"]
    if require_config_resolution:
        required_types.append("ConfigResolution")
    return [record_type for record_type in required_types if first_record(records, record_type) is None]


def _config_resolution_messages(records: list[Record]) -> list[str]:
    resolution = first_record(records, "ConfigResolution")
    if resolution is None or resolution.data.get("config_schema_version") != "cross-agent-consensus-config-2":
        return []
    identities = resolution.data.get("resolved_participant_identities")
    executions = resolution.data.get("resolved_execution_profiles")
    if not isinstance(identities, dict) or not isinstance(executions, dict):
        return ["ConfigResolution conflict: schema-2 profile mappings must be objects"]
    participants = first_record(records, "Participants")
    if participants is None:
        return []
    selected: list[tuple[str, str]] = []
    for field, role in (("orchestrator_identity", "orchestrator"), ("author_identity", "author")):
        value = participants.data.get(field)
        if value:
            selected.append((str(value), role))
    for field, role in (("reviewer_identities", "reviewer"), ("validator_identities", "validator")):
        values = participants.data.get(field)
        if isinstance(values, list):
            selected.extend((str(value), role) for value in values)

    messages: list[str] = []
    for identity, role in sorted(selected):
        binding = identities.get(identity)
        if not isinstance(binding, dict):
            messages.append(f"ConfigResolution conflict: ParticipantIdentity {identity} has no profile binding")
            continue
        if binding.get("role") != role:
            messages.append(
                f"ConfigResolution conflict: ParticipantIdentity {identity} has role {binding.get('role')!r}, expected {role!r}"
            )
        execution_profile_id = binding.get("execution_profile_id")
        if not isinstance(execution_profile_id, str) or not isinstance(executions.get(execution_profile_id), dict):
            messages.append(
                f"ConfigResolution conflict: ParticipantIdentity {identity} references missing ExecutionProfile {execution_profile_id!r}"
            )
    return messages


def _missing_review_outputs(records: list[Record], batch: Record) -> list[str]:
    batch_id = str(batch.data.get("review_batch_id") or batch.record_id)
    captured = {
        str(output.data.get("reviewer_identity"))
        for output in records_by_type(records, "RawReviewerOutput")
        if str(output.data.get("review_batch_id") or "") == batch_id
    }
    return [reviewer for reviewer in _expected_reviewers(records, batch) if reviewer not in captured]


def _missing_normalizations(records: list[Record], batch: Record) -> list[str]:
    batch_id = str(batch.data.get("review_batch_id") or batch.record_id)
    raw_ids = {
        str(finding.data.get("raw_finding_id"))
        for finding in records_by_type(records, "RawFinding")
        if str(finding.data.get("review_batch_id") or "") == batch_id
    }
    normalized_ids = {
        str(raw_id)
        for normalization in records_by_type(records, "NormalizationRecord")
        for raw_id in (normalization.data.get("source_raw_finding_ids") or [])
    }
    return sorted(raw_ids - normalized_ids)


def _blocking_finding_ids(records: list[Record]) -> list[str]:
    return sorted(effective_blocking_finding_ids(records))


def _missing_author_responses(records: list[Record]) -> list[str]:
    responded = current_author_response_finding_ids(records)
    return [finding_id for finding_id in _blocking_finding_ids(records) if finding_id not in responded]


def _latest_remediation_batch(records: list[Record], finding_id: str) -> Record | None:
    return latest_remediation_batches(records).get(finding_id)


def _rereview_requirements(records: list[Record]) -> RereviewRequirements:
    missing: list[tuple[str, str, str, str]] = []
    missing_batches: list[str] = []
    revisions: list[str] = []
    new_batches: list[str] = []
    escalations: list[str] = []
    latest_artifact = artifact_chain(records).head
    latest_artifact_id = (
        str(latest_artifact.data.get("artifact_version_id") or latest_artifact.record_id)
        if latest_artifact
        else ""
    )
    for finding_id in _blocking_finding_ids(records):
        batch = _latest_remediation_batch(records, finding_id)
        if batch is None:
            missing_batches.append(finding_id)
            continue
        batch_id = str(batch.data.get("review_batch_id") or batch.record_id)
        decisions = rereview_decisions_for_batch(records, finding_id, batch)
        expected = expected_reviewers_for_batch(records, batch)
        round_id = str(batch.data.get("round_id") or "round-1")
        missing_reviewers = [
            reviewer for reviewer in _expected_reviewers(records, batch) if reviewer not in decisions
        ]
        if missing_reviewers:
            cap_reached_before_invocation = any(
                remediation_cap_blockers(records, [finding_id], reviewer)
                for reviewer in missing_reviewers
            )
            if cap_reached_before_invocation:
                escalations.append(finding_id)
            else:
                missing.extend(
                    (finding_id, batch_id, round_id, reviewer)
                    for reviewer in missing_reviewers
                )
            continue
        if not expected or rereview_batch_resolves(expected, decisions):
            continue
        cap_reached = any(
            remediation_cap_blockers(records, [finding_id], reviewer)
            for reviewer in sorted(expected)
        )
        if cap_reached:
            escalations.append(finding_id)
        elif str(batch.data.get("target_artifact_version_id") or "") == latest_artifact_id:
            revisions.append(finding_id)
        else:
            new_batches.append(finding_id)
    return RereviewRequirements(
        missing_decisions=tuple(sorted(missing)),
        missing_batches=tuple(sorted(missing_batches)),
        revisions=tuple(sorted(revisions)),
        new_batches=tuple(sorted(new_batches)),
        escalations=tuple(sorted(escalations)),
    )


def _human_directed_actions(records: list[Record]) -> tuple[list[str], list[str], list[str]]:
    actions: list[str] = []
    blockers: list[str] = []
    required: list[str] = []
    decisions = latest_binding_human_decisions(records)
    run_decision = decisions.get("__run_scope__")
    run_decision_type = run_decision.data.get("decision_type") if run_decision else None
    if run_decision_type == "abort_run":
        if records_by_type(records, "AbortRecord"):
            actions.append("terminate-run-aborted")
        else:
            actions.append("record-abort")
            blockers.append("AbortRecord is missing for binding HumanDecision abort_run")
            required.append("AbortRecord")
        return actions, blockers, required
    if run_decision_type == "terminate_escalated_to_human":
        actions.append("terminate-run-escalated-to-human")
        return actions, blockers, required

    latest_artifact = artifact_chain(records).head
    latest_artifact_id = (
        str(latest_artifact.data.get("artifact_version_id") or latest_artifact.record_id)
        if latest_artifact
        else ""
    )
    batches = latest_remediation_batches(records)
    for finding_id, decision in sorted(decisions.items()):
        if human_decision_awaits_artifact(records, decision):
            actions.append(f"record-artifact-version-{_identifier_token(finding_id)}")
            blockers.append(
                f"new ArtifactVersion is missing for HumanDecision {decision.record_id} on affected identifier {finding_id}"
            )
            required.append(f"ArtifactVersion:after:{decision.record_id}")
            continue
        if decision.data.get("decision_type") != "require_revision":
            continue
        batch = batches.get(finding_id)
        if batch is None or str(batch.data.get("target_artifact_version_id") or "") != latest_artifact_id:
            actions.append(f"create-remediation-review-batch-{_identifier_token(finding_id)}")
            blockers.append(
                f"remediation ReviewBatch targeting ArtifactVersion {latest_artifact_id} is missing for HumanDecision require_revision on NormalizedFinding {finding_id}"
            )
            required.append(f"ReviewBatch:remediation_verification:{finding_id}:{latest_artifact_id}")
    return actions, blockers, required


def _append_external_action(
    records: list[Record],
    run_id: str,
    actions: list[str],
    checkpoints: list[PendingCheckpoint],
    participant_identity: str,
    role: str,
    phase: str,
    manual_action: str,
    round_id: str,
    action_qualifier: str | None = None,
) -> None:
    macro_records_required_output = phase == "reviewer"
    if not macro_records_required_output or not _external_action(records, participant_identity, role):
        actions.append(manual_action)
        return
    qualifier_suffix = f"-{_identifier_token(action_qualifier)}" if action_qualifier else ""
    action_id = f"invoke-{_identifier_token(participant_identity)}-{slugify(role)}{qualifier_suffix}"
    checkpoint = _operator_checkpoint(
        records,
        run_id,
        participant_identity,
        phase,
        round_id,
        action_qualifier,
    )
    if checkpoint is not None:
        checkpoints.append(checkpoint)
        return
    actions.append(action_id)


def _latest_round_id(records: list[Record]) -> str:
    batches = records_by_type(records, "ReviewBatch")
    return str(batches[-1].data.get("round_id") or "round-1") if batches else "round-1"


def _terminal_status(records: list[Record]) -> str:
    termination = first_record(records, "TerminationRecord")
    if termination is None:
        return "non_terminal"
    condition = termination.data.get("terminal_condition")
    if condition == "consensus_reached":
        return "success"
    if condition == "aborted":
        return "failure"
    return "unresolved"


def _ordered_checkpoints(checkpoints: Iterable[PendingCheckpoint]) -> tuple[PendingCheckpoint, ...]:
    unique = dict.fromkeys(checkpoints)
    return tuple(
        sorted(
            unique,
            key=lambda checkpoint: (
                checkpoint.checkpoint_id,
                checkpoint.checkpoint_type,
                checkpoint.record_id,
                tuple((choice.choice_id, choice.consequence) for choice in checkpoint.choices),
            ),
        )
    )


def build_next_action_plan(
    run_id: str,
    records: list[Record],
    events: list[dict[str, Any]],
    *,
    invalid_input_blockers: Iterable[str] = (),
    require_config_resolution: bool = False,
) -> NextActionPlan:
    """Build a plan from validated protocol records, RunJournal entries, and ConfigResolution."""

    record_journal_digest = _record_journal_sha256(records, events)
    supplied_invalid_blockers = tuple(sorted(set(invalid_input_blockers)))
    if supplied_invalid_blockers:
        return NextActionPlan(
            schema_version=NEXT_ACTION_PLAN_SCHEMA,
            run_id=run_id,
            phase="invalid_input",
            plan_status="invalid",
            terminal_status="unresolved",
            runnable_actions=(),
            blockers=supplied_invalid_blockers,
            required_records=(),
            pending_checkpoints=(),
            record_journal_sha256=record_journal_digest,
        )
    phase = derive_run_phase(records)
    missing_foundations = _missing_foundational_records(records, require_config_resolution)
    blockers = sorted(
        set(_singleton_conflicts(records))
        | {f"{record_type} is missing" for record_type in missing_foundations}
    )
    if blockers:
        return NextActionPlan(
            schema_version=NEXT_ACTION_PLAN_SCHEMA,
            run_id=run_id,
            phase=phase,
            plan_status="invalid",
            terminal_status="unresolved",
            runnable_actions=(),
            blockers=tuple(blockers),
            required_records=tuple(missing_foundations),
            pending_checkpoints=(),
            record_journal_sha256=record_journal_digest,
        )
    if phase == "terminated":
        return NextActionPlan(
            schema_version=NEXT_ACTION_PLAN_SCHEMA,
            run_id=run_id,
            phase=phase,
            plan_status="terminal",
            terminal_status=_terminal_status(records),
            runnable_actions=(),
            blockers=(),
            required_records=(),
            pending_checkpoints=(),
            record_journal_sha256=record_journal_digest,
        )

    actions: list[str] = []
    required: list[str] = []
    checkpoints = list(_pending_human_checkpoints(records))
    human_actions, human_blockers, human_required = _human_directed_actions(records)

    terminal_human_action = bool(human_actions) and human_actions[0] in {
        "record-abort",
        "terminate-run-aborted",
        "terminate-run-escalated-to-human",
    }

    if terminal_human_action:
        actions.extend(human_actions)
        blockers.extend(human_blockers)
        required.extend(human_required)
        checkpoints.clear()
    elif checkpoints:
        actions.append("record-human-decision")
        for checkpoint in checkpoints:
            blockers.append(f"HumanDecision is missing for EscalationRecord {checkpoint.record_id}")
            required.append(f"HumanDecision:{checkpoint.record_id}")
    elif human_actions:
        actions.extend(human_actions)
        blockers.extend(human_blockers)
        required.extend(human_required)
    elif phase == "not_initialized":
        actions.append("initialize-run")
        blockers.append("TaskBrief is missing")
        required.append("TaskBrief")
    elif phase == "awaiting_artifact":
        participants = first_record(records, "Participants")
        author = str(participants.data.get("author_identity") or "author") if participants else "author"
        _append_external_action(
            records,
            run_id,
            actions,
            checkpoints,
            author,
            "author",
            "author",
            "record-artifact-version",
            _latest_round_id(records),
        )
        blockers.append("ArtifactVersion is missing")
        required.append("ArtifactVersion")
    elif phase == "awaiting_review":
        batches = records_by_type(records, "ReviewBatch")
        if not batches:
            actions.append("create-review-batch")
            blockers.append("ReviewBatch is missing")
            required.append("ReviewBatch")
        else:
            batch = batches[-1]
            batch_id = str(batch.data.get("review_batch_id") or batch.record_id)
            for reviewer in _missing_review_outputs(records, batch):
                _append_external_action(
                    records,
                    run_id,
                    actions,
                    checkpoints,
                    reviewer,
                    "reviewer",
                    "reviewer",
                    f"collect-{_identifier_token(reviewer)}-review",
                    str(batch.data.get("round_id") or "round-1"),
                )
                blockers.append(f"{reviewer} RawReviewerOutput is missing for ReviewBatch {batch_id}")
                required.append(f"RawReviewerOutput:{batch_id}:{reviewer}")
    elif phase == "awaiting_normalization":
        batch = records_by_type(records, "ReviewBatch")[-1]
        actions.append("normalize-raw-findings")
        for raw_id in _missing_normalizations(records, batch):
            blockers.append(f"NormalizationRecord is missing for RawFinding {raw_id}")
            required.append(f"NormalizationRecord:{raw_id}")
    elif phase == "awaiting_author_response":
        participants = first_record(records, "Participants")
        author = str(participants.data.get("author_identity") or "author") if participants else "author"
        for finding_id in _missing_author_responses(records):
            _append_external_action(
                records,
                run_id,
                actions,
                checkpoints,
                author,
                "author-response",
                "author-response",
                f"record-author-response-{_identifier_token(finding_id)}",
                _latest_round_id(records),
            )
            blockers.append(f"AuthorResponse is missing for NormalizedFinding {finding_id}")
            required.append(f"AuthorResponse:{finding_id}")
    elif phase == "awaiting_rereview":
        requirements = _rereview_requirements(records)
        for finding_id in requirements.missing_batches:
            actions.append(f"create-remediation-review-batch-{_identifier_token(finding_id)}")
            blockers.append(f"remediation ReviewBatch is missing for NormalizedFinding {finding_id}")
            required.append(f"ReviewBatch:remediation_verification:{finding_id}")
        for finding_id, batch_id, round_id, reviewer in requirements.missing_decisions:
            _append_external_action(
                records,
                run_id,
                actions,
                checkpoints,
                reviewer,
                "rereviewer",
                "rereview",
                f"record-rereview-decision-{_identifier_token(reviewer)}-{_identifier_token(finding_id)}",
                round_id,
                batch_id,
            )
            blockers.append(
                f"{reviewer} ReReviewDecision is missing for NormalizedFinding {finding_id} in ReviewBatch {batch_id}"
            )
            required.append(f"ReReviewDecision:{batch_id}:{finding_id}:{reviewer}")
        for finding_id in requirements.revisions:
            actions.append(f"record-artifact-version-{_identifier_token(finding_id)}")
            blockers.append(
                f"new ArtifactVersion is missing after non-resolving ReReviewDecision records for NormalizedFinding {finding_id}"
            )
            required.append(f"ArtifactVersion:revision:{finding_id}")
        for finding_id in requirements.new_batches:
            actions.append(f"create-remediation-review-batch-{_identifier_token(finding_id)}")
            blockers.append(
                f"remediation ReviewBatch is missing for the latest ArtifactVersion and NormalizedFinding {finding_id}"
            )
            required.append(f"ReviewBatch:remediation_verification:{finding_id}")
        for finding_id in requirements.escalations:
            actions.append(f"record-escalation-{_identifier_token(finding_id)}")
            blockers.append(
                f"EscalationRecord is missing after the remediation cap for NormalizedFinding {finding_id}"
            )
            required.append(f"EscalationRecord:{finding_id}")
    elif phase == "awaiting_validation":
        status = validator_status(records)
        binding_decisions = latest_binding_human_decisions(records)
        participants = first_record(records, "Participants")
        participant_validators = participants.data.get("validator_identities") if participants else None
        validator_identities = {
            str(value) for value in participant_validators
        } if isinstance(participant_validators, list) else set()
        for validator in sorted(str(value) for value in required_validators(records)):
            if status.get(validator) in {"pass", "waived"}:
                continue
            human_decision = binding_decisions.get(validator)
            if human_decision is not None and human_decision.data.get("decision_type") == "waive_validator":
                actions.append(f"record-waived-validation-evidence-{_identifier_token(validator)}")
                blockers.append(
                    f"waived ValidationEvidence is missing for binding HumanDecision {human_decision.record_id} on validator {validator}"
                )
                required.append(f"ValidationEvidence:{validator}:waived")
                continue
            if validator in validator_identities:
                _append_external_action(
                    records,
                    run_id,
                    actions,
                    checkpoints,
                    validator,
                    "validator",
                    "validator",
                    f"run-validator-{_identifier_token(validator)}",
                    _latest_round_id(records),
                )
            else:
                actions.append(f"run-validator-{_identifier_token(validator)}")
            blockers.append(f"passing or waived ValidationEvidence is missing for validator {validator}")
            required.append(f"ValidationEvidence:{validator}")
    elif phase == "ready_for_termination":
        actions.append("terminate-run")
    for checkpoint in checkpoints:
        if checkpoint.checkpoint_type != "operator_approval":
            continue
        blockers.append(f"OperatorApproval is missing for checkpoint {checkpoint.checkpoint_id}")
        required.append(f"OperatorApproval:{checkpoint.checkpoint_id}")
    return NextActionPlan(
        schema_version=NEXT_ACTION_PLAN_SCHEMA,
        run_id=run_id,
        phase=phase,
        plan_status="actionable" if actions else "waiting",
        terminal_status="non_terminal",
        runnable_actions=tuple(sorted(set(actions))),
        blockers=tuple(sorted(set(blockers))),
        required_records=tuple(sorted(set(required))),
        pending_checkpoints=_ordered_checkpoints(checkpoints),
        record_journal_sha256=record_journal_digest,
    )


def _failed_check_messages(label: str, result_messages: list[str]) -> list[str]:
    return [f"{label} conflict: {message}" for message in result_messages]


def derive_next_action_plan(run: Path) -> NextActionPlan:
    """Read and validate one run without creating files, launching participants, or calling an LLM."""

    snapshot: RunSnapshot = parse_run_snapshot(run)
    records = snapshot.records
    events = read_run_events(run)
    if not records:
        empty_run_blockers: list[str] = []
        record_check = check_records(run, snapshot)
        if not record_check.ok:
            empty_run_blockers.extend(_failed_check_messages("ProtocolRecord", record_check.messages))
        if events:
            event_check = check_run_events(run)
            if not event_check.ok:
                empty_run_blockers.extend(_failed_check_messages("RunJournal", event_check.messages))
        return build_next_action_plan(
            run.name,
            records,
            events,
            invalid_input_blockers=empty_run_blockers,
        )
    invalid: list[str] = []
    for label, result in (
        ("ProtocolRecord", check_records(run, snapshot)),
        ("ParticipantIdentity", check_participants(run, snapshot)),
        ("ProtocolRecord link", check_links(run, snapshot)),
        ("ReviewerRecord", check_reviewer_isolation(run, snapshot)),
        ("EvidenceRecord", check_integrity(run, snapshot)),
        ("RunJournal", check_run_events(run)),
    ):
        if not result.ok:
            invalid.extend(_failed_check_messages(label, result.messages))
    invalid.extend(_config_resolution_messages(records))
    if records_by_type(records, "TerminationRecord"):
        terminal = check_terminal(run, snapshot)
        if not terminal.ok:
            invalid.extend(_failed_check_messages("TerminationRecord", terminal.messages))
    version = recorded_run_version(run)
    require_config_resolution = version is not None and version >= (0, 12, 0)
    return build_next_action_plan(
        run.name,
        records,
        events,
        invalid_input_blockers=invalid,
        require_config_resolution=require_config_resolution,
    )
