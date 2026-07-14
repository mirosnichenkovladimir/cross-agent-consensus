from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "skills" / "cross-agent-consensus"
sys.path.insert(0, str(PACKAGE_ROOT))

from cross_agent_consensus.bounded_remediation import (
    bounded_remediation_plan_json,
    build_bounded_remediation_plan,
    cmd_remediate,
    derive_bounded_remediation_plan,
)
from cross_agent_consensus.approval import (
    approval_binding,
    approval_binding_exists,
    stamp_operator_approval,
    verify_invocation_approval,
)
from cross_agent_consensus.markdown_records import frontmatter
from cross_agent_consensus.models import (
    CheckpointChoice,
    NextActionPlan,
    PendingCheckpoint,
    Record,
)
from cross_agent_consensus.layout import round_dir
from cross_agent_consensus.records import parse_run_records
from cross_agent_consensus.run_audit import (
    append_run_event_locked,
    derive_run_phase,
)
from cross_agent_consensus.next_action import build_next_action_plan
from test_run_macro import _stage_run
from test_next_action import blocking_finding_records, protocol_record


def record(record_type: str, record_id: str, **fields: object) -> Record:
    return Record(record_type, record_id, Path(f"{record_type}.md"), 1, fields)


def records_for_profile(profile: str = "bounded-remediation") -> list[Record]:
    return [
        record("Policy", "policy-1", profile=profile),
        record(
            "Participants",
            "participants-1",
            author_identity="author",
            reviewer_identities=["reviewer"],
            validator_identities=["tests"],
        ),
        record(
            "ReviewBatch",
            "batch-2",
            review_batch_id="batch-2",
            round_id="round-2",
            expected_reviewer_identities=["reviewer"],
        ),
    ]


def next_plan(
    phase: str,
    *,
    status: str = "actionable",
    terminal_status: str = "non_terminal",
    blockers: tuple[str, ...] = (),
    required: tuple[str, ...] = (),
) -> NextActionPlan:
    action = {
        "awaiting_artifact": "record-artifact-version",
        "awaiting_review": "invoke-reviewer-reviewer",
        "awaiting_validation": "run-validator-tests",
    }.get(phase, f"action-{phase}")
    return NextActionPlan(
        schema_version="cross-agent-consensus-next-action-plan-1",
        run_id="run-1",
        phase=phase,
        plan_status=status,
        terminal_status=terminal_status,
        runnable_actions=(action,),
        blockers=blockers,
        required_records=required,
        pending_checkpoints=(),
        record_journal_sha256="1" * 64,
    )


def initialized_bounded_run(root: Path) -> Path:
    run = _stage_run(
        root,
        reviewers=["reviewer"],
        unattended_scope=["phase:reviewer", "actor:reviewer"],
    )
    run_record = run / "run.md"
    run_record.write_text(
        run_record.read_text(encoding="utf-8").replace(
            "document-consensus", "bounded-remediation"
        ),
        encoding="utf-8",
    )
    config = {
        "record_type": "ConfigResolution",
        "schema_version": "m2-markdown-2",
        "run_id": run.name,
        "actor_identity": "orchestrator-config-tool",
        "created_at": "2026-07-14T00:00:00Z",
        "config_resolution_id": "config-bounded-test",
        "config_schema_version": "v1",
        "sources": ["test"],
        "effective_values": {
            "reviewer_clis.reviewer.command": {
                "value": ["/usr/bin/true"],
                "source_layer": "test",
            }
        },
        "diagnostics": {"warnings": ["none"], "errors": ["none"]},
        "redactions": ["none"],
    }
    run_record.write_text(
        run_record.read_text(encoding="utf-8")
        + "\n## ConfigResolution config-bounded-test\n"
        + frontmatter(config)
        + "\n",
        encoding="utf-8",
    )
    phase = derive_run_phase(parse_run_records(run))
    append_run_event_locked(
        run,
        "run_initialized",
        actor_identity="orchestrator",
        phase_before="not_initialized",
        phase_after=phase,
        details={"run_id_source": "test"},
    )
    return run


class BoundedRemediationPlanTests(unittest.TestCase):
    def test_document_workflow_stops_for_explicit_author_response(self) -> None:
        records = blocking_finding_records()
        policy = next(
            candidate for candidate in records if candidate.record_type == "Policy"
        )
        policy.data["profile"] = "bounded-remediation"
        next_action = build_next_action_plan("document-run", records, [])

        plan = build_bounded_remediation_plan(
            Path("document-run"), records, next_action
        )

        self.assertEqual(plan.phase, "awaiting_author_response")
        self.assertFalse(plan.execution_allowed)
        self.assertEqual(plan.required_records, ("AuthorResponse:finding-001",))

    def test_code_workflow_escalates_after_two_remediation_rounds(self) -> None:
        records = blocking_finding_records()
        policy = next(
            candidate for candidate in records if candidate.record_type == "Policy"
        )
        policy.data["profile"] = "bounded-remediation"
        scope = next(
            candidate
            for candidate in records
            if candidate.record_type == "ReviewScope"
        )
        scope.data["max_remediation_rounds_per_finding"] = 2
        records.extend(
            [
                protocol_record(
                    "AuthorResponse",
                    "response-001",
                    normalized_finding_id="finding-001",
                ),
                protocol_record(
                    "ArtifactVersion",
                    "v2",
                    artifact_version_id="v2",
                    predecessor_id_or_null="v1",
                ),
                protocol_record(
                    "ReviewBatch",
                    "batch-002",
                    review_batch_id="batch-002",
                    review_mode="remediation_verification",
                    round_id="round-2",
                    target_artifact_version_id="v2",
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
                    "decision-002",
                    normalized_finding_id="finding-001",
                    review_batch_id="batch-002",
                    reviewer_identity="codex",
                    decision="still_valid",
                ),
                protocol_record(
                    "ArtifactVersion",
                    "v3",
                    artifact_version_id="v3",
                    predecessor_id_or_null="v2",
                ),
                protocol_record(
                    "ReviewBatch",
                    "batch-003",
                    review_batch_id="batch-003",
                    review_mode="remediation_verification",
                    round_id="round-3",
                    target_artifact_version_id="v3",
                    expected_reviewer_identities=["codex"],
                    source_finding_ids=["finding-001"],
                ),
                protocol_record(
                    "RawReviewerOutput",
                    "output-003",
                    review_batch_id="batch-003",
                    reviewer_identity="codex",
                ),
                protocol_record(
                    "ReReviewDecision",
                    "decision-003",
                    normalized_finding_id="finding-001",
                    review_batch_id="batch-003",
                    reviewer_identity="codex",
                    decision="still_valid",
                ),
            ]
        )
        next_action = build_next_action_plan("code-run", records, [])

        plan = build_bounded_remediation_plan(Path("code-run"), records, next_action)

        self.assertEqual(plan.phase, "awaiting_rereview")
        self.assertFalse(plan.execution_allowed)
        self.assertEqual(plan.required_records, ("EscalationRecord:finding-001",))

    def test_real_run_derivation_is_stable_and_dispatches_current_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = initialized_bounded_run(Path(tmp_name))
            first = derive_bounded_remediation_plan(run)
            second = derive_bounded_remediation_plan(run)

        self.assertEqual(first.phase, "awaiting_review")
        self.assertEqual(first.dispatch_phase_or_null, "reviewer")
        self.assertEqual(first.participant_identities, ("reviewer",))
        self.assertTrue(first.execution_allowed)
        self.assertEqual(
            bounded_remediation_plan_json(first),
            bounded_remediation_plan_json(second),
        )

    def test_document_consensus_is_not_activated(self) -> None:
        plan = build_bounded_remediation_plan(
            Path("run-1"),
            records_for_profile("document-consensus"),
            next_plan("awaiting_review"),
        )

        self.assertFalse(plan.execution_allowed)
        self.assertIsNone(plan.dispatch_phase_or_null)
        self.assertIn(
            "Policy.profile is document-consensus; expected bounded-remediation",
            plan.blockers,
        )

    def test_review_phase_resolves_round_and_reviewer_identity(self) -> None:
        plan = build_bounded_remediation_plan(
            Path("run-1"), records_for_profile(), next_plan("awaiting_review")
        )

        self.assertEqual(plan.dispatch_phase_or_null, "reviewer")
        self.assertEqual(plan.round_id_or_null, "round-2")
        self.assertEqual(plan.participant_identities, ("reviewer",))
        self.assertTrue(plan.execution_allowed)
        self.assertEqual(plan.checkpoint_status, "missing")
        self.assertFalse(plan.publication_authorized)
        self.assertEqual(
            bounded_remediation_plan_json(plan),
            bounded_remediation_plan_json(plan),
        )

    def test_checkpoint_becomes_stale_after_plan_digest_changes(self) -> None:
        records = records_for_profile()
        first = build_bounded_remediation_plan(
            Path("run-1"), records, next_plan("awaiting_review")
        )
        records.append(
            record(
                "OperatorApproval",
                "approval-1",
                checkpoint_id=first.checkpoint_id_or_null,
                checkpoint_input_sha256=first.checkpoint_input_sha256,
            )
        )
        current = build_bounded_remediation_plan(
            Path("run-1"), records, next_plan("awaiting_review")
        )
        changed = build_bounded_remediation_plan(
            Path("run-1"),
            records,
            next_plan(
                "awaiting_review",
                required=("RawReviewerOutput:batch-2:reviewer",),
            ),
        )

        self.assertEqual(current.checkpoint_status, "current")
        self.assertEqual(changed.checkpoint_status, "stale")
        self.assertNotEqual(
            first.checkpoint_input_sha256, changed.checkpoint_input_sha256
        )

    def test_waiting_ambiguous_plan_cannot_dispatch(self) -> None:
        plan = build_bounded_remediation_plan(
            Path("run-1"),
            records_for_profile(),
            next_plan(
                "awaiting_review",
                status="waiting",
                required=("OperatorApproval:ambiguous-retry:attempt-1",),
            ),
        )

        self.assertFalse(plan.execution_allowed)

    def test_explicit_ordinary_reviewer_approval_can_dispatch_waiting_plan(self) -> None:
        base = next_plan(
            "awaiting_review",
            status="waiting",
            required=(
                "OperatorApproval:approve-reviewer-reviewer",
                "RawReviewerOutput:batch-2:reviewer",
            ),
        )
        plan_input = NextActionPlan(
            **{
                **base.__dict__,
                "runnable_actions": (),
                "pending_checkpoints": (
                    PendingCheckpoint(
                        checkpoint_id="approve-reviewer-reviewer",
                        checkpoint_type="operator_approval",
                        record_id="OperatorApproval",
                        choices=(
                            CheckpointChoice(
                                "approve_exact_invocation", "launch reviewer"
                            ),
                        ),
                    ),
                ),
            }
        )

        plan = build_bounded_remediation_plan(
            Path("run-1"), records_for_profile(), plan_input
        )

        self.assertTrue(plan.execution_allowed)

    def test_partial_review_dispatches_only_missing_reviewer(self) -> None:
        records = records_for_profile()
        participants = next(
            candidate
            for candidate in records
            if candidate.record_type == "Participants"
        )
        participants.data["reviewer_identities"] = ["reviewer-a", "reviewer-b"]
        batch = next(
            candidate for candidate in records if candidate.record_type == "ReviewBatch"
        )
        batch.data["expected_reviewer_identities"] = ["reviewer-a", "reviewer-b"]
        records.append(
            record(
                "RawReviewerOutput",
                "output-a",
                review_batch_id="batch-2",
                reviewer_identity="reviewer-a",
            )
        )
        base = next_plan("awaiting_review")
        plan_input = NextActionPlan(
            **{
                **base.__dict__,
                "runnable_actions": ("invoke-reviewer-b-reviewer",),
                "required_records": (
                    "RawReviewerOutput:batch-2:reviewer-b",
                ),
            }
        )

        plan = build_bounded_remediation_plan(Path("run-1"), records, plan_input)

        self.assertEqual(plan.participant_identities, ("reviewer-b",))
        self.assertTrue(plan.execution_allowed)

    def test_partial_validation_dispatches_only_missing_validator(self) -> None:
        records = records_for_profile()
        participants = next(
            candidate
            for candidate in records
            if candidate.record_type == "Participants"
        )
        participants.data["validator_identities"] = ["tests", "waived-one"]
        base = next_plan(
            "awaiting_validation",
            required=(
                "ValidationEvidence:tests",
                "ValidationEvidence:waived-one:waived",
            ),
        )
        plan_input = NextActionPlan(
            **{
                **base.__dict__,
                "runnable_actions": (
                    "record-waived-validation-evidence-waived-one",
                    "run-validator-tests",
                ),
            }
        )

        plan = build_bounded_remediation_plan(Path("run-1"), records, plan_input)

        self.assertEqual(plan.participant_identities, ("tests",))
        self.assertTrue(plan.execution_allowed)

    def test_approval_self_mutation_becomes_current_reservation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = initialized_bounded_run(Path(tmp_name))
            plan = derive_bounded_remediation_plan(run)
            prompt = round_dir(run, "round-1") / "prompts" / "reviewers" / "reviewer.md"
            records = parse_run_records(run)
            binding = approval_binding(
                run,
                records,
                participant_identity="reviewer",
                player_id="generic-cli",
                phase="reviewer",
                round_id="round-1",
                prompt_path=prompt,
                command=["/usr/bin/true"],
                artifact_version_id="v1",
                working_directory=".",
                checkpoint_id=plan.checkpoint_id_or_null,
                checkpoint_input_sha256=plan.checkpoint_input_sha256,
            )
            stamp_operator_approval(
                run,
                round_id="round-1",
                phase="reviewer",
                bindings=[binding],
                mechanism="cli_approved_flag",
                operator_identity="operator",
                checkpoint_id=plan.checkpoint_id_or_null,
                checkpoint_input_sha256=plan.checkpoint_input_sha256,
            )

            verify_invocation_approval(
                run,
                participant_identity="reviewer",
                player_id="generic-cli",
                phase="reviewer",
                round_id="round-1",
                prompt_path=prompt,
                command=["/usr/bin/true"],
                working_directory=".",
                checkpoint_id=plan.checkpoint_id_or_null,
                checkpoint_input_sha256=plan.checkpoint_input_sha256,
            )
            reserved = derive_bounded_remediation_plan(run)

        self.assertEqual(reserved.checkpoint_status, "reserved")

    def test_semantic_and_escalation_phases_do_not_dispatch_workers(self) -> None:
        author_response = build_bounded_remediation_plan(
            Path("run-1"),
            records_for_profile(),
            next_plan(
                "awaiting_author_response",
                required=("AuthorResponse:finding-1",),
            ),
        )
        exhausted = build_bounded_remediation_plan(
            Path("run-1"),
            records_for_profile(),
            next_plan(
                "awaiting_rereview",
                required=("EscalationRecord:finding-1",),
            ),
        )

        self.assertFalse(author_response.execution_allowed)
        self.assertEqual(
            author_response.required_records, ("AuthorResponse:finding-1",)
        )
        self.assertFalse(exhausted.execution_allowed)
        self.assertEqual(
            exhausted.required_records, ("EscalationRecord:finding-1",)
        )

    def test_terminal_status_never_dispatches_provider_verdict(self) -> None:
        plan = build_bounded_remediation_plan(
            Path("run-1"),
            records_for_profile(),
            next_plan(
                "terminated", status="terminal", terminal_status="unresolved"
            ),
        )

        self.assertFalse(plan.execution_allowed)
        self.assertIsNone(plan.dispatch_phase_or_null)


class BoundedRemediationCommandTests(unittest.TestCase):
    def args(self, run: Path, **overrides: object) -> argparse.Namespace:
        values: dict[str, object] = {
            "run": str(run),
            "json": False,
            "execute": True,
            "approved": True,
            "operator_identity": "operator",
            "sequential": True,
            "cwd": ".",
            "idle_timeout_seconds": 30.0,
            "stale_timeout_seconds": 60.0,
            "heartbeat_interval_seconds": 1.0,
            "checkpoint_id": None,
            "checkpoint_input_sha256": None,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_execute_forwards_one_checkpoint_bound_transition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = Path(tmp_name) / "run-1"
            plan = build_bounded_remediation_plan(
                run, records_for_profile(), next_plan("awaiting_review")
            )
            with (
                patch(
                    "cross_agent_consensus.bounded_remediation.derive_bounded_remediation_plan",
                    return_value=plan,
                ),
                patch(
                    "cross_agent_consensus.bounded_remediation.cmd_run",
                    return_value=0,
                ) as run_command,
            ):
                code = cmd_remediate(
                    self.args(
                        run,
                        checkpoint_id=plan.checkpoint_id_or_null,
                        checkpoint_input_sha256=plan.checkpoint_input_sha256,
                    )
                )

        self.assertEqual(code, 0)
        forwarded = run_command.call_args.args[0]
        self.assertEqual(forwarded.phase, "reviewer")
        self.assertEqual(forwarded.actors, "reviewer")
        self.assertEqual(forwarded.checkpoint_id, "bounded-remediation-reviewer")
        self.assertEqual(
            forwarded.checkpoint_input_sha256, plan.checkpoint_input_sha256
        )

    def test_execute_requires_explicit_operator_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = Path(tmp_name) / "run-1"
            plan = build_bounded_remediation_plan(
                run, records_for_profile(), next_plan("awaiting_review")
            )
            with patch(
                "cross_agent_consensus.bounded_remediation.derive_bounded_remediation_plan",
                return_value=plan,
            ):
                code = cmd_remediate(self.args(run, operator_identity=None))

        self.assertEqual(code, 2)

    def test_execute_rejects_missing_or_stale_supplied_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = Path(tmp_name) / "run-1"
            plan = build_bounded_remediation_plan(
                run, records_for_profile(), next_plan("awaiting_review")
            )
            with (
                patch(
                    "cross_agent_consensus.bounded_remediation.derive_bounded_remediation_plan",
                    return_value=plan,
                ),
                patch("cross_agent_consensus.bounded_remediation.cmd_run") as run_command,
            ):
                code = cmd_remediate(
                    self.args(
                        run,
                        checkpoint_id=plan.checkpoint_id_or_null,
                        checkpoint_input_sha256="0" * 64,
                    )
                )

        self.assertEqual(code, 3)
        run_command.assert_not_called()

    def test_checkpoint_binding_cannot_reuse_plain_invocation_approval(self) -> None:
        base = {
            "participant_identity": "reviewer",
            "participant_profile_id": "reviewer-default",
            "execution_profile_id": "codex-default",
            "execution_profile_sha256_or_null": "1" * 64,
            "player_id": "codex-cli",
            "phase": "reviewer",
            "round_id": "round-1",
            "prompt_path": "prompt.md",
            "prompt_sha256": "2" * 64,
            "command_sha256": "3" * 64,
            "working_directory": "/workspace",
            "artifact_version_id_or_null": "v1",
            "artifact_sha256_or_null": "4" * 64,
            "resume_provider_session_entry_id_or_null": None,
            "provider_session_id_or_null": None,
        }
        wanted = {
            **base,
            "checkpoint_id_or_null": "bounded-remediation-reviewer",
            "checkpoint_input_sha256_or_null": "5" * 64,
        }
        approval = record(
            "OperatorApproval", "approval-1", approved_invocations=[base]
        )

        self.assertFalse(approval_binding_exists([approval], wanted))
        approval.data["approved_invocations"] = [wanted]
        self.assertTrue(approval_binding_exists([approval], wanted))

    def test_json_form_is_machine_readable_and_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = Path(tmp_name) / "run-1"
            plan = build_bounded_remediation_plan(
                run, records_for_profile(), next_plan("awaiting_review")
            )
            with patch(
                "cross_agent_consensus.bounded_remediation.derive_bounded_remediation_plan",
                return_value=plan,
            ):
                args = self.args(run, execute=False, json=True)
                with patch("builtins.print") as output:
                    code = cmd_remediate(args)

        self.assertEqual(code, 0)
        parsed = json.loads(output.call_args.args[0])
        self.assertEqual(parsed["dispatch_phase_or_null"], "reviewer")


if __name__ == "__main__":
    unittest.main()
