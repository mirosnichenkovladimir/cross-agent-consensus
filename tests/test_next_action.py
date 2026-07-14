from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "skills" / "cross-agent-consensus"
CLI = PACKAGE_ROOT / "scripts" / "consensus"
sys.path.insert(0, str(PACKAGE_ROOT))

from cross_agent_consensus.models import Record
from cross_agent_consensus.integrity import command_sha256, resolved_execution_profile_sha256
from cross_agent_consensus.next_action import build_next_action_plan, next_action_plan_json


def protocol_record(record_type: str, record_id: str, **fields: object) -> Record:
    return Record(record_type, record_id, Path(f"{record_type}.md"), 1, fields)


def profiled_records() -> list[Record]:
    return [
        protocol_record(
            "ConfigResolution",
            "config-001",
            resolved_participant_identities={
                "codex": {
                    "role": "reviewer",
                    "participant_profile_id": "reviewer-default",
                    "execution_profile_id": "codex-default",
                }
            },
            resolved_execution_profiles={
                "codex-default": {
                    "adapter_id": "codex-cli",
                    "command": ["codex", "exec", "--json", "-"],
                }
            },
        )
    ]


def initialized_records(*, required_validators: list[str] | None = None) -> list[Record]:
    return [
        protocol_record("TaskBrief", "task-001"),
        protocol_record("Policy", "policy-001", required_validator_ids=required_validators or []),
        protocol_record("ReviewScope", "scope-001"),
        protocol_record(
            "Participants",
            "participants-001",
            orchestrator_identity="orchestrator",
            author_identity="author",
            reviewer_identities=["codex"],
            validator_identities=required_validators or [],
        ),
        *profiled_records(),
    ]


def reviewed_records(*, required_validators: list[str] | None = None) -> list[Record]:
    return [
        *initialized_records(required_validators=required_validators),
        protocol_record("ArtifactVersion", "v1", artifact_version_id="v1"),
        protocol_record(
            "ReviewBatch",
            "batch-001",
            review_batch_id="batch-001",
            review_mode="fresh_review",
            expected_reviewer_identities=["codex"],
            source_finding_ids=[],
        ),
        protocol_record(
            "RawReviewerOutput",
            "output-001",
            review_batch_id="batch-001",
            reviewer_identity="codex",
        ),
    ]


def blocking_finding_records() -> list[Record]:
    return [
        *reviewed_records(),
        protocol_record("RawFinding", "raw-001", raw_finding_id="raw-001", review_batch_id="batch-001"),
        protocol_record(
            "NormalizationRecord",
            "normalization-001",
            source_raw_finding_ids=["raw-001"],
        ),
        protocol_record(
            "NormalizedFinding",
            "finding-001",
            normalized_finding_id="finding-001",
            scope_classification="in_scope",
            blocking_status="blocking",
            materiality="material",
            lifecycle_state="open",
        ),
    ]


class NextActionPlanTests(unittest.TestCase):
    def plan(self, records: list[Record]):
        return build_next_action_plan("run-001", records, [])

    def test_not_initialized_phase_requires_task_brief(self) -> None:
        plan = self.plan([])

        self.assertEqual(plan.phase, "not_initialized")
        self.assertEqual(plan.runnable_actions, ("initialize-run",))
        self.assertEqual(plan.blockers, ("TaskBrief is missing",))

    def test_awaiting_artifact_phase_requires_artifact_version(self) -> None:
        plan = self.plan(initialized_records())

        self.assertEqual(plan.phase, "awaiting_artifact")
        self.assertIn("record-artifact-version", plan.runnable_actions)
        self.assertEqual(plan.required_records, ("ArtifactVersion",))

    def test_awaiting_review_phase_names_reviewer_and_raw_output(self) -> None:
        records = [
            *initialized_records(),
            protocol_record("ArtifactVersion", "v1", artifact_version_id="v1"),
            protocol_record(
                "ReviewBatch",
                "batch-001",
                review_batch_id="batch-001",
                expected_reviewer_identities=["codex"],
            ),
        ]

        plan = self.plan(records)

        self.assertEqual(plan.phase, "awaiting_review")
        self.assertEqual(plan.runnable_actions, ())
        self.assertEqual(plan.plan_status, "waiting")
        self.assertIn("codex RawReviewerOutput is missing for ReviewBatch batch-001", plan.blockers)
        self.assertIn("RawReviewerOutput:batch-001:codex", plan.required_records)
        self.assertIn("OperatorApproval:approve-codex-reviewer", plan.required_records)
        self.assertEqual(plan.pending_checkpoints[0].record_id, "OperatorApproval")

    def test_stale_operator_approval_does_not_remove_exact_input_checkpoint(self) -> None:
        records = [
            *initialized_records(),
            protocol_record("ArtifactVersion", "v1", artifact_version_id="v1"),
            protocol_record(
                "ReviewBatch",
                "batch-002",
                review_batch_id="batch-002",
                round_id="round-2",
                expected_reviewer_identities=["codex"],
            ),
            protocol_record(
                "OperatorApproval",
                "approval-round-1",
                scope_phase="reviewer",
                scope_round_id="round-1",
                approved_actors=["codex"],
            ),
        ]

        plan = self.plan(records)

        self.assertEqual(plan.runnable_actions, ())
        self.assertEqual(plan.pending_checkpoints[0].checkpoint_id, "approve-codex-reviewer")

    def test_recorded_approval_does_not_authorize_unspecified_future_invocation(self) -> None:
        records = [
            *initialized_records(),
            protocol_record("ArtifactVersion", "v1", artifact_version_id="v1"),
            protocol_record(
                "ReviewBatch",
                "batch-001",
                review_batch_id="batch-001",
                round_id="round-1",
                target_artifact_version_id="v1",
                expected_reviewer_identities=["codex"],
            ),
        ]
        execution_digest = resolved_execution_profile_sha256(records, "codex-default")
        records.append(
            protocol_record(
                "OperatorApproval",
                "approval-round-1",
                approved_invocations=[
                    {
                        "participant_identity": "codex",
                        "participant_profile_id": "reviewer-default",
                        "execution_profile_id": "codex-default",
                        "execution_profile_sha256_or_null": execution_digest,
                        "player_id": "generic-cli",
                        "phase": "reviewer",
                        "round_id": "round-1",
                        "prompt_path": "rounds/round-001/prompts/reviewers/codex.md",
                        "prompt_sha256": "1" * 64,
                        "command_sha256": command_sha256(["codex", "exec", "--json", "-"]),
                        "working_directory": "/workspace",
                        "artifact_version_id_or_null": "v1",
                    }
                ],
            )
        )

        plan = self.plan(records)

        self.assertEqual(plan.runnable_actions, ())
        self.assertEqual(plan.pending_checkpoints[0].checkpoint_id, "approve-codex-reviewer")

    def test_checkpoint_ids_remain_distinct_when_record_ids_share_a_slug(self) -> None:
        records = [
            *initialized_records(),
            protocol_record("ArtifactVersion", "v1", artifact_version_id="v1"),
            protocol_record(
                "EscalationRecord",
                "esc.a",
                escalation_record_id="esc.a",
                affected_finding_ids=["finding-a"],
            ),
            protocol_record(
                "EscalationRecord",
                "esc-a",
                escalation_record_id="esc-a",
                affected_finding_ids=["finding-b"],
            ),
        ]

        plan = self.plan(records)

        self.assertEqual(
            [checkpoint.record_id for checkpoint in plan.pending_checkpoints],
            ["esc-a", "esc.a"],
        )
        self.assertEqual(len({item.checkpoint_id for item in plan.pending_checkpoints}), 2)
        self.assertEqual(next_action_plan_json(plan), next_action_plan_json(self.plan(records)))

    def test_action_ids_remain_distinct_when_validator_ids_share_a_slug(self) -> None:
        records = reviewed_records(required_validators=["lint.a", "lint-a"])

        plan = self.plan(records)

        self.assertEqual(plan.phase, "awaiting_validation")
        self.assertEqual(
            plan.runnable_actions,
            ("run-validator-lint-a", "run-validator-lint-a-d7c6d39c6f"),
        )

    def test_scoped_unattended_policy_removes_operator_checkpoint(self) -> None:
        records = initialized_records()
        records[1].data["unattended_invocation"] = {
            "enabled": True,
            "scope": ["phase:reviewer", "actor:codex"],
        }
        records.extend(
            [
                protocol_record("ArtifactVersion", "v1", artifact_version_id="v1"),
                protocol_record(
                    "ReviewBatch",
                    "batch-001",
                    review_batch_id="batch-001",
                    round_id="round-1",
                    expected_reviewer_identities=["codex"],
                ),
            ]
        )

        plan = self.plan(records)

        self.assertEqual(plan.runnable_actions, ("invoke-codex-reviewer",))
        self.assertEqual(plan.pending_checkpoints, ())

    def test_ambiguous_mutating_attempt_is_classified_and_withheld(self) -> None:
        records = initialized_records()
        records[1].data["unattended_invocation"] = True
        records.extend([
            protocol_record("ArtifactVersion", "v1", artifact_version_id="v1"),
            protocol_record(
                "ReviewBatch", "batch-001", review_batch_id="batch-001", round_id="round-1",
                expected_reviewer_identities=["codex"],
            ),
        ])
        start_details = {
            "attempt_id": "attempt-codex-001", "action_id": "invoke-codex-reviewer",
            "attempt_number": 1, "predecessor_attempt_id_or_null": None,
            "participant_identity": "codex", "session_id": "session-001",
            "retry_safety": "mutating",
        }
        events = [{"event_type": "execution_attempt_started", "details": start_details}]

        plan = build_next_action_plan("run-001", records, events)

        self.assertEqual(plan.runnable_actions, ())
        self.assertTrue(any("attempt-codex-001 is ambiguous" in item for item in plan.blockers))
        self.assertIn(
            "OperatorApproval:ambiguous-retry:attempt-codex-001", plan.required_records
        )

    def test_non_reviewer_phases_do_not_advertise_incomplete_invoke_macros(self) -> None:
        def bind_execution(records: list[Record], identity: str, role: str) -> None:
            resolution = next(record for record in records if record.record_type == "ConfigResolution")
            identities = resolution.data["resolved_participant_identities"]
            executions = resolution.data["resolved_execution_profiles"]
            assert isinstance(identities, dict)
            assert isinstance(executions, dict)
            profile_id = f"{identity}-execution"
            identities[identity] = {
                "role": role,
                "participant_profile_id": f"{identity}-profile",
                "execution_profile_id": profile_id,
            }
            executions[profile_id] = {
                "adapter_id": "generic-cli",
                "command": [identity, "--run"],
            }

        awaiting_artifact = initialized_records()
        bind_execution(awaiting_artifact, "author", "author")
        author_plan = self.plan(awaiting_artifact)

        awaiting_response = blocking_finding_records()
        bind_execution(awaiting_response, "author", "author")
        response_plan = self.plan(awaiting_response)

        awaiting_validation = reviewed_records(required_validators=["pytest"])
        bind_execution(awaiting_validation, "pytest", "validator")
        validator_plan = self.plan(awaiting_validation)

        self.assertEqual(author_plan.runnable_actions, ("record-artifact-version",))
        self.assertEqual(response_plan.runnable_actions, ("record-author-response-finding-001",))
        self.assertEqual(validator_plan.runnable_actions, ("run-validator-pytest",))
        for plan in (author_plan, response_plan, validator_plan):
            self.assertFalse(any(action.startswith("invoke-") for action in plan.runnable_actions))

    def test_rereview_actions_require_record_production_until_macro_supports_decisions(self) -> None:
        records = [
            *blocking_finding_records(),
            protocol_record(
                "NormalizedFinding",
                "finding-002",
                normalized_finding_id="finding-002",
                scope_classification="in_scope",
                blocking_status="blocking",
                materiality="material",
                lifecycle_state="open",
            ),
            protocol_record("AuthorResponse", "response-001", normalized_finding_id="finding-001"),
            protocol_record("AuthorResponse", "response-002", normalized_finding_id="finding-002"),
            protocol_record(
                "ReviewBatch",
                "batch-002",
                review_batch_id="batch-002",
                review_mode="remediation_verification",
                round_id="round-2",
                target_artifact_version_id="v1",
                expected_reviewer_identities=["codex"],
                source_finding_ids=["finding-001"],
            ),
            protocol_record(
                "RawReviewerOutput",
                "output-002",
                review_batch_id="batch-002",
                reviewer_identity="codex",
            ),
            protocol_record(
                "ReviewBatch",
                "batch-003",
                review_batch_id="batch-003",
                review_mode="remediation_verification",
                round_id="round-3",
                target_artifact_version_id="v1",
                expected_reviewer_identities=["codex"],
                source_finding_ids=["finding-002"],
            ),
            protocol_record(
                "RawReviewerOutput",
                "output-003",
                review_batch_id="batch-003",
                reviewer_identity="codex",
            ),
        ]
        policy = next(record for record in records if record.record_type == "Policy")
        policy.data["unattended_invocation"] = {
            "enabled": True,
            "scope": ["round:round-3", "phase:rereview", "actor:codex"],
        }

        plan = self.plan(records)

        self.assertEqual(
            plan.runnable_actions,
            (
                "record-rereview-decision-codex-finding-001",
                "record-rereview-decision-codex-finding-002",
            ),
        )
        self.assertEqual(plan.pending_checkpoints, ())

    def test_awaiting_normalization_phase_names_raw_finding(self) -> None:
        records = [
            *reviewed_records(),
            protocol_record("RawFinding", "raw-001", raw_finding_id="raw-001", review_batch_id="batch-001"),
        ]

        plan = self.plan(records)

        self.assertEqual(plan.phase, "awaiting_normalization")
        self.assertEqual(plan.runnable_actions, ("normalize-raw-findings",))
        self.assertEqual(plan.blockers, ("NormalizationRecord is missing for RawFinding raw-001",))

    def test_awaiting_author_response_phase_names_normalized_finding(self) -> None:
        plan = self.plan(blocking_finding_records())

        self.assertEqual(plan.phase, "awaiting_author_response")
        self.assertEqual(plan.runnable_actions, ("record-author-response-finding-001",))
        self.assertEqual(plan.blockers, ("AuthorResponse is missing for NormalizedFinding finding-001",))

    def test_awaiting_rereview_phase_requires_remediation_batch(self) -> None:
        records = [
            *blocking_finding_records(),
            protocol_record("AuthorResponse", "response-001", normalized_finding_id="finding-001"),
        ]

        plan = self.plan(records)

        self.assertEqual(plan.phase, "awaiting_rereview")
        self.assertEqual(plan.runnable_actions, ("create-remediation-review-batch-finding-001",))
        self.assertIn("remediation ReviewBatch is missing for NormalizedFinding finding-001", plan.blockers)

    def test_resolving_rereview_advances_to_validation(self) -> None:
        records = [
            *blocking_finding_records(),
            protocol_record("AuthorResponse", "response-001", normalized_finding_id="finding-001"),
            protocol_record(
                "ReviewBatch",
                "batch-002",
                review_batch_id="batch-002",
                review_mode="remediation_verification",
                expected_reviewer_identities=["codex"],
                source_finding_ids=["finding-001"],
            ),
            protocol_record(
                "RawReviewerOutput",
                "output-002",
                review_batch_id="batch-002",
                reviewer_identity="codex",
            ),
            protocol_record(
                "ReReviewDecision",
                "decision-001",
                normalized_finding_id="finding-001",
                review_batch_id="batch-002",
                reviewer_identity="codex",
                decision="verified",
            ),
        ]

        plan = self.plan(records)

        self.assertEqual(plan.phase, "ready_for_termination")
        self.assertEqual(plan.runnable_actions, ("terminate-run",))

    def test_mixed_resolving_rereview_decisions_require_revision(self) -> None:
        records = blocking_finding_records()
        participants = next(record for record in records if record.record_type == "Participants")
        participants.data["reviewer_identities"] = ["codex", "claude"]
        records.extend(
            [
                protocol_record("AuthorResponse", "response-001", normalized_finding_id="finding-001"),
                protocol_record(
                    "ReviewBatch",
                    "batch-002",
                    review_batch_id="batch-002",
                    review_mode="remediation_verification",
                    target_artifact_version_id="v1",
                    expected_reviewer_identities=["codex", "claude"],
                    source_finding_ids=["finding-001"],
                ),
                protocol_record(
                    "RawReviewerOutput",
                    "output-002-codex",
                    review_batch_id="batch-002",
                    reviewer_identity="codex",
                ),
                protocol_record(
                    "RawReviewerOutput",
                    "output-002-claude",
                    review_batch_id="batch-002",
                    reviewer_identity="claude",
                ),
                protocol_record(
                    "ReReviewDecision",
                    "decision-codex",
                    normalized_finding_id="finding-001",
                    review_batch_id="batch-002",
                    reviewer_identity="codex",
                    decision="verified",
                ),
                protocol_record(
                    "ReReviewDecision",
                    "decision-claude",
                    normalized_finding_id="finding-001",
                    review_batch_id="batch-002",
                    reviewer_identity="claude",
                    decision="rejection_accepted",
                ),
            ]
        )

        plan = self.plan(records)

        self.assertEqual(plan.phase, "awaiting_rereview")
        self.assertEqual(plan.runnable_actions, ("record-artifact-version-finding-001",))
        self.assertNotIn("terminate-run", plan.runnable_actions)

    def test_nonresolving_rereview_at_cap_requires_escalation(self) -> None:
        records = [
            *blocking_finding_records(),
            protocol_record("AuthorResponse", "response-001", normalized_finding_id="finding-001"),
            protocol_record(
                "ReviewBatch",
                "batch-002",
                review_batch_id="batch-002",
                review_mode="remediation_verification",
                target_artifact_version_id="v1",
                expected_reviewer_identities=["codex"],
                source_finding_ids=["finding-001"],
            ),
            protocol_record(
                "RawReviewerOutput",
                "output-002",
                review_batch_id="batch-002",
                reviewer_identity="codex",
            ),
            protocol_record(
                "ReReviewDecision",
                "decision-001",
                normalized_finding_id="finding-001",
                review_batch_id="batch-002",
                reviewer_identity="codex",
                decision="still_valid",
            ),
        ]
        scope = next(record for record in records if record.record_type == "ReviewScope")
        scope.data["max_remediation_rounds_per_finding"] = 1

        plan = self.plan(records)

        self.assertEqual(plan.runnable_actions, ("record-escalation-finding-001",))
        self.assertIn("EscalationRecord is missing after the remediation cap", plan.blockers[0])
        self.assertNotIn("invoke-codex-rereviewer", plan.runnable_actions)

    def test_remediation_cap_blocks_missing_decision_invocation(self) -> None:
        records = [
            *blocking_finding_records(),
            protocol_record("AuthorResponse", "response-001", normalized_finding_id="finding-001"),
            protocol_record(
                "ReviewBatch",
                "batch-002",
                review_batch_id="batch-002",
                review_mode="remediation_verification",
                round_id="round-2",
                target_artifact_version_id="v1",
                expected_reviewer_identities=["codex"],
                source_finding_ids=["finding-001"],
            ),
            protocol_record(
                "ReReviewDecision",
                "decision-002",
                normalized_finding_id="finding-001",
                review_batch_id="batch-002",
                reviewer_identity="codex",
                decision="still_valid",
            ),
            protocol_record(
                "ReviewBatch",
                "batch-003",
                review_batch_id="batch-003",
                review_mode="remediation_verification",
                round_id="round-3",
                target_artifact_version_id="v1",
                expected_reviewer_identities=["codex"],
                source_finding_ids=["finding-001"],
            ),
        ]
        scope = next(record for record in records if record.record_type == "ReviewScope")
        scope.data["max_remediation_rounds_per_finding"] = 1

        plan = self.plan(records)

        self.assertEqual(plan.phase, "awaiting_rereview")
        self.assertEqual(plan.runnable_actions, ("record-escalation-finding-001",))
        self.assertEqual(plan.pending_checkpoints, ())

    def test_binding_human_decisions_control_next_action(self) -> None:
        base = [
            *blocking_finding_records(),
            protocol_record("AuthorResponse", "response-001", normalized_finding_id="finding-001"),
            protocol_record(
                "EscalationRecord",
                "escalation-001",
                escalation_record_id="escalation-001",
                affected_finding_ids=["finding-001"],
                created_at="2026-07-14T10:00:00Z",
            ),
        ]
        decisions = (
            ("mark_resolved", ["finding-001"], "terminate-run"),
            ("terminate_escalated_to_human", ["__run_scope__"], "terminate-run-escalated-to-human"),
            ("abort_run", ["__run_scope__"], "record-abort"),
        )
        for decision_type, affected_ids, expected_action in decisions:
            with self.subTest(decision_type=decision_type):
                records = [
                    *base,
                    protocol_record(
                        "HumanDecision",
                        f"decision-{decision_type}",
                        decision_type=decision_type,
                        affected_finding_ids_or_validator_ids=affected_ids,
                        created_at="2026-07-14T11:00:00Z",
                    ),
                ]
                plan = self.plan(records)
                self.assertEqual(plan.runnable_actions, (expected_action,))
                self.assertNotIn("invoke-codex-rereviewer", plan.runnable_actions)

    def test_resolving_human_decision_can_require_new_artifact(self) -> None:
        records = [
            *blocking_finding_records(),
            protocol_record(
                "HumanDecision",
                "resolved-after-revision",
                decision_type="mark_resolved",
                affected_finding_ids_or_validator_ids=["finding-001"],
                requires_new_artifact_version=True,
                created_at="2026-07-14T12:00:00Z",
            ),
        ]

        plan = self.plan(records)

        self.assertEqual(plan.runnable_actions, ("record-artifact-version-finding-001",))
        self.assertEqual(plan.required_records, ("ArtifactVersion:after:resolved-after-revision",))

    def test_disputed_materiality_reopens_closed_non_material_finding(self) -> None:
        records = blocking_finding_records()
        finding = next(record for record in records if record.record_type == "NormalizedFinding")
        finding.data["materiality"] = "non_material"
        finding.data["lifecycle_state"] = "closed"
        records.append(
            protocol_record(
                "HumanDecision",
                "materiality-disputed",
                decision_type="dispute_materiality",
                affected_finding_ids_or_validator_ids=["finding-001"],
                created_at="2026-07-14T12:00:00Z",
            )
        )

        plan = self.plan(records)

        self.assertEqual(plan.phase, "awaiting_author_response")
        self.assertEqual(plan.runnable_actions, ("record-author-response-finding-001",))

    def test_disputed_materiality_does_not_reuse_old_response_or_resolving_batch(self) -> None:
        records = blocking_finding_records()
        finding = next(record for record in records if record.record_type == "NormalizedFinding")
        finding.data["materiality"] = "non_material"
        finding.data["lifecycle_state"] = "closed"
        records.extend(
            [
                protocol_record(
                    "AuthorResponse",
                    "response-before-dispute",
                    normalized_finding_id="finding-001",
                    created_at="2026-07-14T10:00:00Z",
                ),
                protocol_record(
                    "ReviewBatch",
                    "batch-before-dispute",
                    review_batch_id="batch-before-dispute",
                    review_mode="remediation_verification",
                    round_id="round-2",
                    target_artifact_version_id="v1",
                    expected_reviewer_identities=["codex"],
                    source_finding_ids=["finding-001"],
                    created_at="2026-07-14T10:10:00Z",
                ),
                protocol_record(
                    "ReReviewDecision",
                    "verified-before-dispute",
                    normalized_finding_id="finding-001",
                    review_batch_id="batch-before-dispute",
                    reviewer_identity="codex",
                    decision="verified",
                    created_at="2026-07-14T10:20:00Z",
                ),
                protocol_record(
                    "HumanDecision",
                    "materiality-disputed",
                    decision_type="dispute_materiality",
                    affected_finding_ids_or_validator_ids=["finding-001"],
                    created_at="2026-07-14T12:00:00Z",
                ),
            ]
        )

        plan = self.plan(records)

        self.assertEqual(plan.phase, "awaiting_author_response")
        self.assertEqual(plan.plan_status, "actionable")
        self.assertEqual(plan.runnable_actions, ("record-author-response-finding-001",))
        self.assertEqual(plan.required_records, ("AuthorResponse:finding-001",))

    def test_later_require_revision_does_not_erase_open_materiality_dispute(self) -> None:
        records = blocking_finding_records()
        finding = next(record for record in records if record.record_type == "NormalizedFinding")
        finding.data["materiality"] = "non_material"
        finding.data["lifecycle_state"] = "closed"
        records.extend(
            [
                protocol_record(
                    "HumanDecision",
                    "materiality-disputed",
                    decision_type="dispute_materiality",
                    affected_finding_ids_or_validator_ids=["finding-001"],
                    created_at="2026-07-14T10:00:00Z",
                ),
                protocol_record(
                    "HumanDecision",
                    "revision-required",
                    decision_type="require_revision",
                    affected_finding_ids_or_validator_ids=["finding-001"],
                    created_at="2026-07-14T11:00:00Z",
                ),
                protocol_record(
                    "ArtifactVersion",
                    "v2",
                    artifact_version_id="v2",
                    predecessor_id_or_null="v1",
                    created_at="2026-07-14T12:00:00Z",
                ),
                protocol_record(
                    "ReviewBatch",
                    "batch-after-revision",
                    review_batch_id="batch-after-revision",
                    review_mode="remediation_verification",
                    round_id="round-2",
                    target_artifact_version_id="v2",
                    expected_reviewer_identities=["codex"],
                    source_finding_ids=["finding-001"],
                    created_at="2026-07-14T13:00:00Z",
                ),
                protocol_record(
                    "ReReviewDecision",
                    "verified-without-response",
                    normalized_finding_id="finding-001",
                    review_batch_id="batch-after-revision",
                    reviewer_identity="codex",
                    decision="verified",
                    created_at="2026-07-14T14:00:00Z",
                ),
            ]
        )

        plan = self.plan(records)

        self.assertEqual(plan.phase, "awaiting_author_response")
        self.assertEqual(plan.runnable_actions, ("record-author-response-finding-001",))
        self.assertNotIn("run-validator-pytest", plan.runnable_actions)

    def test_timezone_offset_decision_before_escalation_is_not_binding(self) -> None:
        records = [
            *blocking_finding_records(),
            protocol_record(
                "EscalationRecord",
                "escalation-001",
                escalation_record_id="escalation-001",
                affected_finding_ids=["finding-001"],
                created_at="2026-07-14T10:00:00Z",
            ),
            protocol_record(
                "HumanDecision",
                "apparently-later-local-time",
                decision_type="mark_resolved",
                affected_finding_ids_or_validator_ids=["finding-001"],
                created_at="2026-07-14T12:00:00+03:00",
            ),
        ]

        plan = self.plan(records)

        self.assertEqual(plan.runnable_actions, ("record-human-decision",))
        self.assertEqual([checkpoint.record_id for checkpoint in plan.pending_checkpoints], ["escalation-001"])
        self.assertNotIn("terminate-run", plan.runnable_actions)

    def test_same_timestamp_artifact_uses_record_order_after_revision_decision(self) -> None:
        records = [
            *blocking_finding_records(),
            protocol_record(
                "HumanDecision",
                "require-revision",
                decision_type="require_revision",
                affected_finding_ids_or_validator_ids=["finding-001"],
                created_at="2026-07-14T12:00:00Z",
            ),
            protocol_record(
                "ArtifactVersion",
                "v2",
                artifact_version_id="v2",
                predecessor_id_or_null="v1",
                created_at="2026-07-14T12:00:00Z",
            ),
        ]

        plan = self.plan(records)

        self.assertEqual(plan.runnable_actions, ("create-remediation-review-batch-finding-001",))
        self.assertNotIn("record-artifact-version-finding-001", plan.runnable_actions)

    def test_finding_scoped_terminal_decision_invalidates_plan(self) -> None:
        records = [
            *blocking_finding_records(),
            protocol_record(
                "HumanDecision",
                "invalid-abort",
                decision_type="abort_run",
                affected_finding_ids_or_validator_ids=["finding-001"],
            ),
        ]

        plan = self.plan(records)

        self.assertEqual(plan.plan_status, "invalid")
        self.assertEqual(plan.runnable_actions, ())
        self.assertIn("must target exactly __run_scope__", plan.blockers[0])

    def test_require_revision_human_decision_requires_new_artifact_then_batch(self) -> None:
        records = [
            *blocking_finding_records(),
            protocol_record("AuthorResponse", "response-001", normalized_finding_id="finding-001"),
            protocol_record(
                "EscalationRecord",
                "escalation-001",
                escalation_record_id="escalation-001",
                affected_finding_ids=["finding-001"],
                created_at="2026-07-14T10:00:00Z",
            ),
            protocol_record(
                "HumanDecision",
                "decision-require-revision",
                decision_type="require_revision",
                affected_finding_ids_or_validator_ids=["finding-001"],
                created_at="2026-07-14T11:00:00Z",
            ),
        ]

        before_revision = self.plan(records)
        after_revision = self.plan(
            [
                *records,
                protocol_record(
                    "ArtifactVersion",
                    "v2",
                    artifact_version_id="v2",
                    predecessor_id_or_null="v1",
                    created_at="2026-07-14T12:00:00Z",
                ),
            ]
        )

        self.assertEqual(before_revision.runnable_actions, ("record-artifact-version-finding-001",))
        self.assertEqual(after_revision.runnable_actions, ("create-remediation-review-batch-finding-001",))
        self.assertIn("ArtifactVersion v2", after_revision.blockers[0])

    def test_awaiting_validation_phase_names_validator_evidence(self) -> None:
        plan = self.plan(reviewed_records(required_validators=["pytest"]))

        self.assertEqual(plan.phase, "awaiting_validation")
        self.assertEqual(plan.runnable_actions, ("run-validator-pytest",))
        self.assertEqual(plan.required_records, ("ValidationEvidence:pytest",))

    def test_obsolete_artifact_validation_evidence_does_not_satisfy_validator(self) -> None:
        records = [
            *reviewed_records(required_validators=["pytest"]),
            protocol_record(
                "ArtifactVersion",
                "v2",
                artifact_version_id="v2",
                predecessor_id_or_null="v1",
            ),
            protocol_record(
                "ValidationEvidence",
                "pytest-v1",
                validator_id="pytest",
                target_artifact_version_id="v1",
                result="pass",
            ),
        ]

        plan = self.plan(records)

        self.assertEqual(plan.phase, "awaiting_validation")
        self.assertEqual(plan.runnable_actions, ("run-validator-pytest",))
        self.assertNotIn("terminate-run", plan.runnable_actions)

    def test_validator_waiver_decision_requires_waived_validation_evidence(self) -> None:
        records = [
            *reviewed_records(required_validators=["pytest"]),
            protocol_record(
                "HumanDecision",
                "waive-pytest",
                decision_type="waive_validator",
                affected_finding_ids_or_validator_ids=["pytest"],
                created_at="2026-07-14T12:00:00Z",
            ),
        ]

        plan = self.plan(records)

        self.assertEqual(plan.runnable_actions, ("record-waived-validation-evidence-pytest",))
        self.assertEqual(plan.required_records, ("ValidationEvidence:pytest:waived",))
        self.assertIn("HumanDecision waive-pytest", plan.blockers[0])

    def test_missing_policy_invalidates_plan_instead_of_erasing_validators(self) -> None:
        records = [record for record in reviewed_records(required_validators=["pytest"]) if record.record_type != "Policy"]

        plan = self.plan(records)

        self.assertEqual(plan.plan_status, "invalid")
        self.assertEqual(plan.runnable_actions, ())
        self.assertIn("Policy is missing", plan.blockers)
        self.assertIn("Policy", plan.required_records)

    def test_legacy_recorded_reviewer_command_keeps_supervised_invocation(self) -> None:
        records = initialized_records()
        config_index = next(index for index, record in enumerate(records) if record.record_type == "ConfigResolution")
        records[config_index] = protocol_record(
            "ConfigResolution",
            "config-legacy",
            effective_values={
                "reviewer_clis.codex.command": {
                    "value": ["codex", "exec", "--json", "-"],
                    "source_layer": "historical",
                }
            },
        )
        records.extend(
            [
                protocol_record("ArtifactVersion", "v1", artifact_version_id="v1"),
                protocol_record(
                    "ReviewBatch",
                    "batch-001",
                    review_batch_id="batch-001",
                    expected_reviewer_identities=["codex"],
                ),
            ]
        )

        plan = self.plan(records)

        self.assertEqual(plan.runnable_actions, ())
        self.assertEqual(plan.pending_checkpoints[0].record_id, "OperatorApproval")

    def test_ready_for_termination_phase_has_one_action(self) -> None:
        records = [
            *reviewed_records(required_validators=["pytest"]),
            protocol_record(
                "ValidationEvidence",
                "pytest-pass",
                validator_id="pytest",
                target_artifact_version_id="v1",
                result="pass",
            ),
        ]

        plan = self.plan(records)

        self.assertEqual(plan.phase, "ready_for_termination")
        self.assertEqual(plan.runnable_actions, ("terminate-run",))
        self.assertEqual(plan.blockers, ())

    def test_terminated_phase_maps_terminal_conditions(self) -> None:
        for condition, terminal_status in (
            ("consensus_reached", "success"),
            ("round_limit_reached", "unresolved"),
            ("escalated_to_human", "unresolved"),
            ("aborted", "failure"),
        ):
            with self.subTest(condition=condition):
                records = [
                    *reviewed_records(),
                    protocol_record("TerminationRecord", "termination-001", terminal_condition=condition),
                ]
                plan = self.plan(records)
                self.assertEqual(plan.phase, "terminated")
                self.assertEqual(plan.plan_status, "terminal")
                self.assertEqual(plan.terminal_status, terminal_status)
                self.assertEqual(plan.runnable_actions, ())

    def test_terminated_run_ignores_stale_escalation_checkpoint(self) -> None:
        records = [
            *reviewed_records(),
            protocol_record(
                "EscalationRecord",
                "escalation-001",
                escalation_record_id="escalation-001",
                affected_finding_ids=["finding-001"],
            ),
            protocol_record(
                "TerminationRecord",
                "termination-001",
                terminal_condition="escalated_to_human",
            ),
        ]

        plan = self.plan(records)

        self.assertEqual(plan.plan_status, "terminal")
        self.assertEqual(plan.terminal_status, "unresolved")
        self.assertEqual(plan.runnable_actions, ())
        self.assertEqual(plan.pending_checkpoints, ())

    def test_run_scoped_abort_precedes_unrelated_finding_checkpoint(self) -> None:
        records = [
            *blocking_finding_records(),
            protocol_record(
                "EscalationRecord",
                "escalation-001",
                escalation_record_id="escalation-001",
                affected_finding_ids=["finding-001"],
            ),
            protocol_record(
                "HumanDecision",
                "abort-run",
                decision_type="abort_run",
                affected_finding_ids_or_validator_ids=["__run_scope__"],
            ),
        ]

        plan = self.plan(records)

        self.assertEqual(plan.runnable_actions, ("record-abort",))
        self.assertEqual(plan.required_records, ("AbortRecord",))
        self.assertEqual(plan.pending_checkpoints, ())

    def test_artifact_chain_head_uses_predecessors_not_file_order(self) -> None:
        records = [record for record in blocking_finding_records() if record.record_type != "ArtifactVersion"]
        records.extend(
            [
                protocol_record(
                    "AuthorResponse",
                    "response-001",
                    normalized_finding_id="finding-001",
                ),
                protocol_record(
                    "EscalationRecord",
                    "escalation-001",
                    escalation_record_id="escalation-001",
                    affected_finding_ids=["finding-001"],
                    created_at="2026-07-14T10:00:00Z",
                ),
                protocol_record(
                    "HumanDecision",
                    "require-revision",
                    decision_type="require_revision",
                    affected_finding_ids_or_validator_ids=["finding-001"],
                    created_at="2026-07-14T11:00:00Z",
                ),
            ]
        )
        artifacts = {
            f"v{version}": protocol_record(
                "ArtifactVersion",
                f"v{version}",
                artifact_version_id=f"v{version}",
                predecessor_id_or_null=f"v{version - 1}" if version > 1 else None,
                created_at=f"2026-07-15T{version:02}:00:00Z",
            )
            for version in range(1, 11)
        }
        records.extend(artifacts[artifact_id] for artifact_id in ["v1", "v10", "v2", "v3", "v4", "v5", "v6", "v7", "v8", "v9"])
        records.extend(
            [
                protocol_record(
                    "ReviewBatch",
                    "batch-v10",
                    review_batch_id="batch-v10",
                    review_mode="remediation_verification",
                    target_artifact_version_id="v10",
                    source_finding_ids=["finding-001"],
                    expected_reviewer_identities=["codex"],
                ),
                protocol_record(
                    "ReReviewDecision",
                    "decision-v10",
                    normalized_finding_id="finding-001",
                    review_batch_id="batch-v10",
                    reviewer_identity="codex",
                    decision="still_valid",
                ),
                protocol_record(
                    "RawReviewerOutput",
                    "output-v10",
                    review_batch_id="batch-v10",
                    reviewer_identity="codex",
                ),
            ]
        )

        plan = self.plan(records)

        self.assertEqual(plan.runnable_actions, ("record-artifact-version-finding-001",))
        self.assertNotIn("ArtifactVersion v9", "\n".join(plan.blockers))

    def test_branched_artifact_chain_rejects_actions(self) -> None:
        records = [
            *initialized_records(),
            protocol_record("ArtifactVersion", "v1", artifact_version_id="v1", predecessor_id_or_null=None),
            protocol_record("ArtifactVersion", "v2", artifact_version_id="v2", predecessor_id_or_null="v1"),
            protocol_record("ArtifactVersion", "v3", artifact_version_id="v3", predecessor_id_or_null="v1"),
        ]

        plan = self.plan(records)

        self.assertEqual(plan.plan_status, "invalid")
        self.assertEqual(plan.runnable_actions, ())
        self.assertIn("ArtifactVersion conflict: predecessor v1 has branches v2, v3", plan.blockers)

    def test_conflicting_singleton_records_reject_actions(self) -> None:
        records = [
            *initialized_records(),
            protocol_record("TaskBrief", "task-002"),
        ]

        plan = self.plan(records)

        self.assertEqual(plan.plan_status, "invalid")
        self.assertEqual(plan.runnable_actions, ())
        self.assertIn("TaskBrief conflict: expected one record, found task-001, task-002", plan.blockers)

    def test_supplied_validation_blocker_precedes_malformed_lifecycle_fields(self) -> None:
        records = [
            *initialized_records(),
            protocol_record("ArtifactVersion", "v1", artifact_version_id="v1"),
            protocol_record(
                "ReviewBatch",
                "malformed-batch",
                review_batch_id="malformed-batch",
                source_finding_ids=7,
            ),
        ]

        plan = build_next_action_plan(
            "run-001",
            records,
            [],
            invalid_input_blockers=["ProtocolRecord conflict: source_finding_ids must be a list"],
        )

        self.assertEqual(plan.phase, "invalid_input")
        self.assertEqual(plan.plan_status, "invalid")
        self.assertEqual(plan.runnable_actions, ())
        self.assertEqual(
            plan.blockers,
            ("ProtocolRecord conflict: source_finding_ids must be a list",),
        )

    def test_escalation_lists_human_choices_and_consequences(self) -> None:
        records = [
            *blocking_finding_records(),
            protocol_record("AuthorResponse", "response-001", normalized_finding_id="finding-001"),
            protocol_record(
                "EscalationRecord",
                "escalation-001",
                escalation_record_id="escalation-001",
                affected_finding_ids=["finding-001"],
            ),
        ]

        plan = self.plan(records)

        self.assertEqual(plan.runnable_actions, ("record-human-decision",))
        self.assertEqual(plan.pending_checkpoints[0].checkpoint_type, "human_decision")
        choice_ids = [choice.choice_id for choice in plan.pending_checkpoints[0].choices]
        self.assertEqual(
            choice_ids,
            ["mark_resolved", "require_revision", "terminate_escalated_to_human", "abort_run"],
        )
        self.assertTrue(all(choice.consequence for choice in plan.pending_checkpoints[0].choices))

    def test_new_escalation_does_not_resurrect_old_escalation_checkpoint(self) -> None:
        records = [
            *blocking_finding_records(),
            protocol_record("AuthorResponse", "response-001", normalized_finding_id="finding-001"),
            protocol_record(
                "EscalationRecord",
                "escalation-old",
                escalation_record_id="escalation-old",
                affected_finding_ids=["finding-001"],
                created_at="2026-07-14T10:00:00Z",
            ),
            protocol_record(
                "HumanDecision",
                "decision-old",
                decision_type="require_revision",
                affected_finding_ids_or_validator_ids=["finding-001"],
                created_at="2026-07-14T11:00:00Z",
            ),
            protocol_record(
                "EscalationRecord",
                "escalation-new",
                escalation_record_id="escalation-new",
                affected_finding_ids=["finding-001"],
                created_at="2026-07-14T12:00:00Z",
            ),
        ]

        plan = self.plan(records)

        self.assertEqual(plan.runnable_actions, ("record-human-decision",))
        self.assertEqual(
            [checkpoint.record_id for checkpoint in plan.pending_checkpoints],
            ["escalation-new"],
        )
        self.assertNotIn("escalation-old", "\n".join(plan.blockers))

    def test_json_is_byte_equivalent_and_has_no_observation_timestamp(self) -> None:
        plan = self.plan(reviewed_records(required_validators=["pytest"]))

        first = next_action_plan_json(plan)
        second = next_action_plan_json(plan)

        self.assertEqual(first, second)
        self.assertNotIn("observed_at", first)
        self.assertNotIn("created_at", first)
        self.assertIn('"record_journal_sha256"', first)
        self.assertNotIn('"input_sha256"', first)


class NextActionCliTests(unittest.TestCase):
    def run_cli(self, *args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(CLI), *args],
            cwd=str(cwd),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=os.environ.copy(),
            check=False,
        )

    def test_next_json_is_read_only_and_repeatable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            artifact = tmp / "artifact.md"
            artifact.write_text("candidate\n", encoding="utf-8")
            init = self.run_cli(
                "init",
                "--task",
                "Plan next action",
                "--artifact-locator",
                str(artifact),
                "--author",
                "author",
                "--orchestrator",
                "orchestrator",
                "--reviewer",
                "codex",
                "--allow-reviewer-config-override",
                "--human-supervisor",
                "none",
                "--run-root",
                str(tmp / "runs"),
                cwd=tmp,
            )
            self.assertEqual(init.returncode, 0, init.stderr + init.stdout)
            run = next((tmp / "runs").iterdir())
            before = {
                str(path.relative_to(run)): path.read_bytes()
                for path in run.rglob("*")
                if path.is_file()
            }

            first = self.run_cli("next", "--run", str(run), "--json", cwd=tmp)
            second = self.run_cli("next", "--run", str(run), "--json", cwd=tmp)

            self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
            self.assertEqual(first.stdout, second.stdout)
            payload = json.loads(first.stdout)
            self.assertEqual(payload["phase"], "awaiting_review")
            self.assertEqual(payload["runnable_actions"], [])
            self.assertEqual(payload["plan_status"], "waiting")
            self.assertTrue(
                any("codex RawReviewerOutput is missing" in blocker for blocker in payload["blockers"])
            )
            after = {
                str(path.relative_to(run)): path.read_bytes()
                for path in run.rglob("*")
                if path.is_file()
            }
            self.assertEqual(before, after)

    def test_corrupt_run_without_parsed_records_returns_invalid_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = Path(tmp_name) / "corrupt-run"
            run.mkdir()
            (run / "run.md").write_text(
                "## TaskBrief broken\n---\nrecord_type: [\n---\n",
                encoding="utf-8",
            )

            completed = self.run_cli("next", "--run", str(run), "--json", cwd=run)

        self.assertEqual(completed.returncode, 2)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["phase"], "invalid_input")
        self.assertEqual(payload["plan_status"], "invalid")
        self.assertTrue(any("ProtocolRecord conflict" in blocker for blocker in payload["blockers"]))

    def test_next_rejects_missing_run_journal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            artifact = tmp / "artifact.md"
            artifact.write_text("candidate\n", encoding="utf-8")
            init = self.run_cli(
                "init",
                "--task",
                "Reject missing journal",
                "--artifact-locator",
                str(artifact),
                "--author",
                "author",
                "--orchestrator",
                "orchestrator",
                "--reviewer",
                "codex",
                "--allow-reviewer-config-override",
                "--human-supervisor",
                "none",
                "--run-root",
                str(tmp / "runs"),
                cwd=tmp,
            )
            self.assertEqual(init.returncode, 0, init.stderr + init.stdout)
            run = next((tmp / "runs").iterdir())
            (run / "events.jsonl").unlink()

            result = self.run_cli("next", "--run", str(run), "--json", cwd=tmp)

            self.assertEqual(result.returncode, 2)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["plan_status"], "invalid")
            self.assertEqual(payload["runnable_actions"], [])
            self.assertTrue(any("RunJournal conflict" in blocker for blocker in payload["blockers"]))


if __name__ == "__main__":
    unittest.main()
