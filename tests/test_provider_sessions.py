from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "skills" / "cross-agent-consensus"
sys.path.insert(0, str(PACKAGE_ROOT))

from cross_agent_consensus.invocation.process_monitor import cmd_invoke_agent
from cross_agent_consensus.approval import approval_record_for_binding
from cross_agent_consensus.capture import cmd_capture
from cross_agent_consensus.execution_attempts import start_execution_attempt
from cross_agent_consensus.layout import round_dir
from cross_agent_consensus.models import (
    AgentInvocation,
    CaptureCommandInput,
    InvocationCommandInput,
    Record,
)
from cross_agent_consensus.provider_sessions import (
    provider_session_event,
    resolve_provider_session_continuation,
)
from cross_agent_consensus.records import parse_run_records, records_by_type
from cross_agent_consensus.run_audit import (
    provider_session_event_messages,
    append_run_event_locked,
    derive_run_phase,
    read_run_events,
    run_lock,
    run_event_messages,
)
from test_integrity_audit import _stage_run


FAKE_PROVIDER = Path(__file__).parent / "fixtures" / "fake_provider.py"


def invoke_args(run: Path, tmp: Path, mode: str, **overrides: object) -> InvocationCommandInput:
    prompt = round_dir(run, "round-1") / "prompts" / "reviewers" / "reviewer.md"
    prompt.write_text("inspect provider continuation", encoding="utf-8")
    values: dict[str, object] = {
        "run": str(run),
        "round": "round-1",
        "phase": "reviewer",
        "actor": "reviewer",
        "player": "claude-cli",
        "participant_profile_id": None,
        "execution_profile_id": None,
        "prompt": str(prompt),
        "raw_output": str(
            round_dir(run, "round-1") / "raw" / "reviewers" / "reviewer.out"
        ),
        "approved": True,
        "command": [
            sys.executable,
            str(FAKE_PROVIDER),
            mode,
            "-p",
            "--verbose",
            "--output-format",
            "stream-json",
            "--session-id",
            "provider-thread-001",
        ],
        "cwd": str(tmp),
        "idle_timeout_seconds": 1.0,
        "stale_timeout_seconds": 2.0,
        "heartbeat_interval_seconds": 0.02,
    }
    values.update(overrides)
    return InvocationCommandInput(**values)  # type: ignore[arg-type]


def captured_provider_events(run: Path) -> list[dict[str, object]]:
    return [
        event
        for event in read_run_events(run)
        if event.get("event_type") == "provider_session_captured"
    ]


class ProviderSessionTests(unittest.TestCase):
    def test_fresh_and_resumed_invocations_use_separate_cac_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run, _artifact = _stage_run(tmp)
            fresh = invoke_args(run, tmp, "structured")

            self.assertEqual(cmd_invoke_agent(fresh), 0)
            first = captured_provider_events(run)[0]
            first_details = first["details"]
            assert isinstance(first_details, dict)
            first_entry = str(first_details["provider_session_entry_id"])
            resumed = invoke_args(
                run,
                tmp,
                "structured",
                resume_provider_session_entry_id=first_entry,
            )

            self.assertEqual(cmd_invoke_agent(resumed), 0)
            events = captured_provider_events(run)
            approvals = records_by_type(parse_run_records(run), "OperatorApproval")
            stale_resume = invoke_args(
                run,
                tmp,
                "structured",
                resume_provider_session_entry_id=first_entry,
            )
            self.assertEqual(cmd_invoke_agent(stale_resume), 1)
            events_after_stale_resume = captured_provider_events(run)
            audit_messages = run_event_messages(run)

        self.assertEqual(len(events), 2)
        self.assertEqual(len(events_after_stale_resume), 2)
        second_details = events[1]["details"]
        assert isinstance(second_details, dict)
        self.assertEqual(second_details["provider_session_id"], "provider-thread-001")
        self.assertEqual(
            second_details["predecessor_provider_session_entry_id_or_null"], first_entry
        )
        self.assertNotEqual(first_details["cac_session_id"], second_details["cac_session_id"])
        resumed_binding = approvals[-1].data["approved_invocations"][0]
        self.assertEqual(
            resumed_binding["resume_provider_session_entry_id_or_null"], first_entry
        )
        self.assertEqual(resumed_binding["provider_session_id_or_null"], "provider-thread-001")
        self.assertEqual(audit_messages, [])

    def test_provider_session_is_captured_before_attempt_becomes_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run, _artifact = _stage_run(tmp)

            self.assertEqual(cmd_invoke_agent(invoke_args(run, tmp, "structured")), 0)
            event_types = [event["event_type"] for event in read_run_events(run)]

        self.assertLess(
            event_types.index("provider_session_captured"),
            event_types.index("execution_attempt_ambiguous"),
        )

    def test_execution_attempt_atomically_reserves_provider_session_leaf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run, _artifact = _stage_run(tmp)
            self.assertEqual(cmd_invoke_agent(invoke_args(run, tmp, "structured")), 0)
            source = captured_provider_events(run)[0]["details"]
            assert isinstance(source, dict)
            entry_id = str(source["provider_session_entry_id"])
            provider_id, lineage_root, definition_digest, resolution = (
                resolve_provider_session_continuation(
                    run,
                    parse_run_records(run),
                    provider_session_entry_id=entry_id,
                    participant_identity="reviewer",
                    participant_profile_id="legacy-inline-participant-profile",
                    execution_profile_id="legacy-inline-claude-cli-execution-profile",
                    player_id="claude-cli",
                    phase="reviewer",
                    definition_drift_resolution=None,
                    operator_identity=None,
                    definition_drift_reference=None,
                )
            )
            prompt = round_dir(run, "round-1") / "prompts" / "reviewers" / "reviewer.md"

            def pending_invocation(session_id: str) -> AgentInvocation:
                return AgentInvocation(
                    run=run,
                    round_id="round-1",
                    phase="reviewer",
                    participant_identity="reviewer",
                    participant_profile_id="legacy-inline-participant-profile",
                    execution_profile_id="legacy-inline-claude-cli-execution-profile",
                    player_id="claude-cli",
                    prompt_path=prompt,
                    raw_output_path=round_dir(run, "round-1") / "raw" / "reviewers" / f"{session_id}.out",
                    command=["claude", "-p"],
                    cwd=tmp,
                    approved=True,
                    idle_timeout_seconds=1,
                    stale_timeout_seconds=2,
                    heartbeat_interval_seconds=.1,
                    session_id=session_id,
                    resume_provider_session_entry_id=entry_id,
                    provider_session_id=provider_id,
                    artifact_lineage_root_id=lineage_root,
                    continuation_definition_sha256=definition_digest,
                    provider_session_definition_resolution=resolution,
                )

            first = pending_invocation("session-100")
            second = pending_invocation("session-101")
            start_execution_attempt(
                first,
                retry_safety="read_only",
                approve_ambiguous_retry=False,
            )

            with self.assertRaisesRegex(ValueError, "active resume reservation"):
                start_execution_attempt(
                    second,
                    retry_safety="read_only",
                    approve_ambiguous_retry=False,
                )

    def test_resume_rejects_a_different_participant_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run, _artifact = _stage_run(tmp)
            self.assertEqual(cmd_invoke_agent(invoke_args(run, tmp, "structured")), 0)
            first = captured_provider_events(run)[0]["details"]
            assert isinstance(first, dict)

            with self.assertRaisesRegex(ValueError, "participant_identity differs"):
                resolve_provider_session_continuation(
                    run,
                    parse_run_records(run),
                    provider_session_entry_id=str(first["provider_session_entry_id"]),
                    participant_identity="security-reviewer",
                    participant_profile_id="legacy-inline-participant-profile",
                    execution_profile_id="legacy-inline-claude-cli-execution-profile",
                    player_id="claude-cli",
                    phase="reviewer",
                    definition_drift_resolution=None,
                    operator_identity=None,
                    definition_drift_reference=None,
                )
            rejection_recorded = any(
                event.get("event_type") == "provider_session_continuation_rejected"
                for event in read_run_events(run)
            )

        self.assertTrue(rejection_recorded)

    def test_definition_drift_blocks_until_operator_accepts_compatibility(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run, _artifact = _stage_run(tmp)
            self.assertEqual(cmd_invoke_agent(invoke_args(run, tmp, "structured")), 0)
            first = captured_provider_events(run)[0]["details"]
            assert isinstance(first, dict)
            entry_id = str(first["provider_session_entry_id"])
            run_md = run / "run.md"
            run_md.write_text(
                run_md.read_text(encoding="utf-8").replace(
                    "audit exact inputs", "audit changed provider inputs"
                ),
                encoding="utf-8",
            )

            blocked = invoke_args(
                run,
                tmp,
                "structured",
                resume_provider_session_entry_id=entry_id,
            )
            self.assertEqual(cmd_invoke_agent(blocked), 1)
            accepted = invoke_args(
                run,
                tmp,
                "structured",
                resume_provider_session_entry_id=entry_id,
                definition_drift_resolution="authorize_compatibility_rule",
                definition_drift_reference="compat/provider-session-v1",
                operator_identity="vladimir",
            )
            self.assertEqual(cmd_invoke_agent(accepted), 0)
            events = read_run_events(run)

        self.assertTrue(
            any(
                event.get("event_type") == "provider_session_definition_drift_accepted"
                and event.get("actor_identity") == "vladimir"
                for event in events
            )
        )

    def test_missing_provider_session_identifier_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run, _artifact = _stage_run(tmp)

            self.assertEqual(cmd_invoke_agent(invoke_args(run, tmp, "missing_session_id")), 1)
            attempt_failures = [
                event
                for event in read_run_events(run)
                if event.get("event_type") == "execution_attempt_failed"
            ]
            provider_events = captured_provider_events(run)

        self.assertEqual(
            attempt_failures[-1]["details"]["failure_mode"],  # type: ignore[index]
            "missing_session_identifier",
        )
        self.assertEqual(provider_events, [])

    def test_artifact_drift_during_provider_run_is_receipt_integrity_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run, artifact = _stage_run(tmp)
            args = invoke_args(run, tmp, "structured")
            assert args.command is not None
            args.command.extend(["--mutate-path", str(artifact)])

            self.assertEqual(cmd_invoke_agent(args), 1)
            failures = [
                event
                for event in read_run_events(run)
                if event.get("event_type") == "execution_attempt_failed"
            ]
            state = (
                round_dir(run, "round-1")
                / "agents"
                / "reviewer"
                / "session-001"
                / "state.json"
            ).read_text(encoding="utf-8")

        self.assertEqual(
            failures[-1]["details"]["failure_mode"],  # type: ignore[index]
            "receipt_integrity_failure",
        )
        self.assertIn("ArtifactVersion v1 drifted", state)

    def test_provider_native_resume_selector_requires_journal_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run, _artifact = _stage_run(tmp)
            args = invoke_args(run, tmp, "structured")
            assert args.command is not None
            args.command.extend(["--resume", "foreign-provider-session"])

            self.assertEqual(cmd_invoke_agent(args), 1)
            sessions = list(
                (round_dir(run, "round-1") / "agents" / "reviewer").glob("session-*")
            )

        self.assertEqual(sessions, [])

    def test_historical_approval_cannot_authorize_provider_resume(self) -> None:
        current = {
            "participant_identity": "reviewer",
            "participant_profile_id": "reviewer-profile",
            "execution_profile_id": "reviewer-execution",
            "execution_profile_sha256_or_null": "0" * 64,
            "player_id": "claude-cli",
            "phase": "reviewer",
            "round_id": "round-1",
            "prompt_path": "prompt.md",
            "prompt_sha256": "1" * 64,
            "command_sha256": "2" * 64,
            "working_directory": "/tmp",
            "artifact_version_id_or_null": "v1",
            "artifact_sha256_or_null": "3" * 64,
            "resume_provider_session_entry_id_or_null": "provider-session-attempt-1",
            "provider_session_id_or_null": "thread-1",
        }
        historical = {
            key: value
            for key, value in current.items()
            if key
            not in {
                "participant_profile_id",
                "execution_profile_id",
                "execution_profile_sha256_or_null",
                "resume_provider_session_entry_id_or_null",
                "provider_session_id_or_null",
            }
        }
        historical["actor_identity"] = historical.pop("participant_identity")
        record = Record(
            "OperatorApproval",
            "historical-approval",
            Path("approval.md"),
            1,
            {"approved_invocations": [historical]},
        )

        self.assertIsNone(approval_record_for_binding([record], current))

    def test_run_audit_rejects_capture_after_failed_attempt(self) -> None:
        start_details = {
            "attempt_id": "attempt-1",
            "session_id": "session-001",
            "participant_identity": "reviewer",
            "participant_profile_id": "reviewer-profile",
            "execution_profile_id": "reviewer-execution",
            "player_id": "claude-cli",
            "phase": "reviewer",
            "round_id": "round-1",
        }
        capture_details = {
            "provider_session_entry_id": "provider-session-attempt-1",
            "provider_session_id": "thread-1",
            "predecessor_provider_session_entry_id_or_null": None,
            "run_id": "run-1",
            "cac_session_id": "session-001",
            "execution_attempt_id": "attempt-1",
            "participant_identity": "reviewer",
            "participant_profile_id": "reviewer-profile",
            "execution_profile_id": "reviewer-execution",
            "player_id": "claude-cli",
            "phase": "reviewer",
            "round_id": "round-1",
            "artifact_lineage_root_id_or_null": "v1",
            "continuation_definition_sha256": "1" * 64,
            "package_version": "0.15.0",
            "effective_command_sha256": "2" * 64,
            "prompt_sha256": "3" * 64,
        }
        messages = provider_session_event_messages(
            [
                {
                    "sequence": 1,
                    "event_type": "execution_attempt_started",
                    "details": start_details,
                },
                {
                    "sequence": 2,
                    "event_type": "execution_attempt_failed",
                    "details": {"attempt_id": "attempt-1"},
                },
                {
                    "sequence": 3,
                    "event_type": "provider_session_captured",
                    "details": capture_details,
                },
            ]
        )

        self.assertTrue(any("must be captured while execution attempt" in message for message in messages))

    def test_run_audit_rejects_provider_identifier_change_on_resume(self) -> None:
        stable_attempt_fields = {
            "participant_identity": "reviewer",
            "participant_profile_id": "reviewer-profile",
            "execution_profile_id": "reviewer-execution",
            "player_id": "claude-cli",
            "phase": "reviewer",
            "round_id": "round-1",
        }
        first_capture = {
            "provider_session_entry_id": "provider-session-attempt-1",
            "provider_session_id": "thread-1",
            "predecessor_provider_session_entry_id_or_null": None,
            "provider_session_resume_reservation_id_or_null": None,
            "run_id": "run-1",
            "cac_session_id": "session-001",
            "execution_attempt_id": "attempt-1",
            **stable_attempt_fields,
            "artifact_lineage_root_id_or_null": "v1",
            "continuation_definition_sha256": "1" * 64,
            "package_version": "0.15.0",
            "effective_command_sha256": "2" * 64,
            "prompt_sha256": "3" * 64,
        }
        reservation = {
            "provider_session_resume_reservation_id": "reservation-2",
            "execution_attempt_id": "attempt-2",
            "predecessor_provider_session_entry_id": "provider-session-attempt-1",
            "provider_session_id": "thread-1",
            **stable_attempt_fields,
            "artifact_lineage_root_id_or_null": "v1",
        }
        resumed_capture = {
            **first_capture,
            "provider_session_entry_id": "provider-session-attempt-2",
            "provider_session_id": "thread-switched",
            "predecessor_provider_session_entry_id_or_null": (
                "provider-session-attempt-1"
            ),
            "provider_session_resume_reservation_id_or_null": "reservation-2",
            "cac_session_id": "session-002",
            "execution_attempt_id": "attempt-2",
        }
        messages = provider_session_event_messages(
            [
                {
                    "sequence": 1,
                    "event_type": "execution_attempt_started",
                    "details": {
                        "attempt_id": "attempt-1",
                        "session_id": "session-001",
                        **stable_attempt_fields,
                    },
                },
                {
                    "sequence": 2,
                    "event_type": "provider_session_captured",
                    "details": first_capture,
                },
                {
                    "sequence": 3,
                    "event_type": "execution_attempt_ambiguous",
                    "details": {"attempt_id": "attempt-1"},
                },
                {
                    "sequence": 4,
                    "event_type": "execution_attempt_started",
                    "details": {
                        "attempt_id": "attempt-2",
                        "session_id": "session-002",
                        **stable_attempt_fields,
                    },
                },
                {
                    "sequence": 5,
                    "event_type": "provider_session_resume_reserved",
                    "details": reservation,
                },
                {
                    "sequence": 6,
                    "event_type": "provider_session_captured",
                    "details": resumed_capture,
                },
            ]
        )

        self.assertTrue(
            any(
                "resumed provider session changes provider_session_id" in message
                for message in messages
            )
        )

    def test_resume_rejects_duplicate_capture_after_completed_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run, _artifact = _stage_run(tmp)
            args = invoke_args(run, tmp, "structured")
            self.assertEqual(cmd_invoke_agent(args), 0)
            capture_args = CaptureCommandInput(
                run=str(run),
                phase="reviewer",
                actor="reviewer",
                review_batch="review-batch-round-1-fresh_review",
                artifact_version="v1",
                source_file=args.raw_output,
                source_mode="file",
                source_command=None,
                provider="claude-cli",
                round="round-1",
                validator_id=None,
                result=None,
                waiver_authority=None,
                waiver_rationale=None,
                no_append_record=False,
                no_narrative_extract=False,
            )
            self.assertEqual(cmd_capture(capture_args), 0)
            original = captured_provider_events(run)[0]["details"]
            assert isinstance(original, dict)
            forged = dict(original)
            forged["provider_session_entry_id"] = "provider-session-forged-after-completion"
            with run_lock(run):
                phase = derive_run_phase(parse_run_records(run))
                append_run_event_locked(
                    run,
                    "provider_session_captured",
                    actor_identity="forger",
                    phase_before=phase,
                    phase_after=phase,
                    details=forged,
                )

            with self.assertRaisesRegex(ValueError, "RunJournal integrity blocks"):
                resolve_provider_session_continuation(
                    run,
                    parse_run_records(run),
                    provider_session_entry_id="provider-session-forged-after-completion",
                    participant_identity="reviewer",
                    participant_profile_id="legacy-inline-participant-profile",
                    execution_profile_id="legacy-inline-claude-cli-execution-profile",
                    player_id="claude-cli",
                    phase="reviewer",
                    definition_drift_resolution=None,
                    operator_identity=None,
                    definition_drift_reference=None,
                )

    def test_provider_session_lookup_uses_entry_id_not_provider_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run, _artifact = _stage_run(tmp)
            self.assertEqual(cmd_invoke_agent(invoke_args(run, tmp, "structured")), 0)
            details = captured_provider_events(run)[0]["details"]
            assert isinstance(details, dict)
            entry_lookup = provider_session_event(
                run, str(details["provider_session_entry_id"])
            )
            provider_id_lookup = provider_session_event(
                run, str(details["provider_session_id"])
            )

        self.assertIsNotNone(entry_lookup)
        self.assertIsNone(provider_id_lookup)


if __name__ == "__main__":
    unittest.main()
