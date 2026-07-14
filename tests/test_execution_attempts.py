from __future__ import annotations

import argparse
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "skills" / "cross-agent-consensus"
sys.path.insert(0, str(PACKAGE_ROOT))

from cross_agent_consensus.capture import cmd_capture
from cross_agent_consensus.execution_attempts import (
    ReceiptAttemptSource,
    append_attempt_observation,
    complete_attempt_for_receipt_locked,
    start_execution_attempt,
)
from cross_agent_consensus.cli import cmd_new_artifact
from cross_agent_consensus.invocation.process_monitor import cmd_agent_cancel, cmd_invoke_agent
from cross_agent_consensus.layout import round_dir
from cross_agent_consensus.models import AgentInvocation, CaptureCommandInput, InvocationCommandInput, Record
from cross_agent_consensus.records import parse_run_records, records_by_type
from cross_agent_consensus.run_audit import read_run_events, run_lock, run_event_messages
from test_integrity_audit import _stage_run


FAKE_PROVIDER = Path(__file__).parent / "fixtures" / "fake_provider.py"


def attempt_events(run: Path) -> list[dict[str, object]]:
    return [event for event in read_run_events(run) if str(event.get("event_type", "")).startswith("execution_attempt_")]


def invoke_args(run: Path, tmp: Path, mode: str, **overrides: object) -> InvocationCommandInput:
    prompt = round_dir(run, "round-1") / "prompts" / "reviewers" / "reviewer.md"
    prompt.write_text("inspect this", encoding="utf-8")
    values: dict[str, object] = {
        "run": str(run),
        "round": "round-1",
        "phase": "reviewer",
        "actor": "reviewer",
        "player": "generic-cli",
        "participant_profile_id": None,
        "execution_profile_id": None,
        "prompt": str(prompt),
        "raw_output": str(round_dir(run, "round-1") / "raw" / "reviewers" / "reviewer.out"),
        "approved": True,
        "command": [sys.executable, str(FAKE_PROVIDER), mode],
        "cwd": str(tmp),
        "idle_timeout_seconds": 0.05,
        "stale_timeout_seconds": 0.1,
        "heartbeat_interval_seconds": 0.02,
    }
    values.update(overrides)
    return InvocationCommandInput(**values)  # type: ignore[arg-type]


class ExecutionAttemptTests(unittest.TestCase):
    def test_success_is_ambiguous_until_protocol_receipt_is_captured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run, _artifact = _stage_run(tmp)
            args = invoke_args(run, tmp, "stderr")

            self.assertEqual(cmd_invoke_agent(args), 0)
            self.assertEqual(
                [event["event_type"] for event in attempt_events(run)],
                ["execution_attempt_started", "execution_attempt_ambiguous"],
            )

            capture_args = CaptureCommandInput(
                run=str(run),
                phase="reviewer",
                actor="reviewer",
                review_batch="review-batch-round-1-fresh_review",
                artifact_version="v1",
                source_file=args.raw_output,
                source_mode="file",
                source_command=None,
                provider="generic-cli",
                round="round-1",
                validator_id=None,
                result=None,
                waiver_authority=None,
                waiver_rationale=None,
                no_append_record=False,
                no_narrative_extract=False,
            )
            self.assertEqual(cmd_capture(capture_args), 0)
            events = attempt_events(run)

        self.assertEqual(events[-1]["event_type"], "execution_attempt_completed")
        self.assertEqual(events[-1]["details"]["receipt_record_type"], "RawReviewerOutput")  # type: ignore[index]

    def test_nonzero_exit_records_specific_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run, _artifact = _stage_run(tmp)

            self.assertEqual(cmd_invoke_agent(invoke_args(run, tmp, "nonzero")), 4)
            events = attempt_events(run)

        self.assertEqual(events[-1]["event_type"], "execution_attempt_failed")
        self.assertEqual(events[-1]["details"]["failure_mode"], "nonzero_exit")  # type: ignore[index]

    def test_failed_provider_cannot_be_completed_by_later_capture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run, _artifact = _stage_run(tmp)
            args = invoke_args(run, tmp, "nonzero")
            self.assertEqual(cmd_invoke_agent(args), 4)
            capture_args = CaptureCommandInput(
                run=str(run), phase="reviewer", actor="reviewer",
                review_batch="review-batch-round-1-fresh_review", artifact_version="v1",
                source_file=args.raw_output, source_mode="file", source_command=None,
                provider="generic-cli", round="round-1", validator_id=None, result=None,
                waiver_authority=None, waiver_rationale=None, no_append_record=False,
                no_narrative_extract=False,
            )

            self.assertEqual(cmd_capture(capture_args), 1)
            events = attempt_events(run)
            outputs = records_by_type(parse_run_records(run), "RawReviewerOutput")

        self.assertEqual([event["event_type"] for event in events], [
            "execution_attempt_started", "execution_attempt_failed"
        ])
        self.assertEqual(outputs, [])

    def test_stale_provider_and_child_process_group_record_timeout(self) -> None:
        for mode in ("delay", "child_process"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp_name:
                tmp = Path(tmp_name)
                run, _artifact = _stage_run(tmp)

                self.assertEqual(cmd_invoke_agent(invoke_args(run, tmp, mode)), 4)
                events = attempt_events(run)

                self.assertEqual(events[-1]["details"]["failure_mode"], "timeout")  # type: ignore[index]

    def test_timeout_kills_child_that_ignores_sigterm_after_leader_exits(self) -> None:
        from cross_agent_consensus.invocation import process_monitor

        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run, _artifact = _stage_run(tmp)
            original_grace = process_monitor.DEFAULT_CANCEL_GRACE_SECONDS
            process_monitor.DEFAULT_CANCEL_GRACE_SECONDS = 0.8
            try:
                started = process_monitor.time.monotonic()
                rc = cmd_invoke_agent(invoke_args(run, tmp, "orphan_child"))
                elapsed = process_monitor.time.monotonic() - started
            finally:
                process_monitor.DEFAULT_CANCEL_GRACE_SECONDS = original_grace
            session = round_dir(run, "round-1") / "agents/reviewer/session-001"
            session_events = (session / "events.jsonl").read_text(encoding="utf-8")

        self.assertEqual(rc, 4)
        self.assertLess(elapsed, 3.0)
        self.assertIn("timeout_force_kill", session_events)

    def test_operator_cancel_kills_child_that_ignores_sigterm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run, _artifact = _stage_run(tmp)
            args = invoke_args(
                run, tmp, "orphan_child", idle_timeout_seconds=5,
                stale_timeout_seconds=10, heartbeat_interval_seconds=.02,
            )
            invoke_codes: list[int] = []
            worker = threading.Thread(target=lambda: invoke_codes.append(cmd_invoke_agent(args)))
            worker.start()
            session = round_dir(run, "round-1") / "agents/reviewer/session-001"
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if (
                    (session / "state.json").is_file()
                    and '"state": "running"' in (session / "state.json").read_text(encoding="utf-8")
                    and (session / "stdout.raw").is_file()
                    and "child_pid" in (session / "stdout.raw").read_text(encoding="utf-8")
                ):
                    break
                time.sleep(.02)
            cancel_args = argparse.Namespace(
                run=str(run), round="round-1", actor="reviewer", session=None,
                reason="test orphan cancellation", grace_seconds=.3,
            )

            self.assertEqual(cmd_agent_cancel(cancel_args), 0)
            worker.join(timeout=3)
            state = json.loads((session / "state.json").read_text(encoding="utf-8"))

        self.assertFalse(worker.is_alive())
        self.assertEqual(invoke_codes, [4])
        self.assertEqual(state["state"], "cancelled")

    def test_ambiguous_mutating_attempt_requires_operator_retry_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run, _artifact = _stage_run(tmp)
            prompt = tmp / "author-prompt.md"
            prompt.write_text("edit", encoding="utf-8")
            invocation = AgentInvocation(
                run=run,
                round_id="round-1",
                phase="author",
                participant_identity="author",
                participant_profile_id="author-profile",
                execution_profile_id="author-execution",
                player_id="generic-cli",
                prompt_path=prompt,
                raw_output_path=tmp / "author.out",
                command=[sys.executable, "-c", "print('done')"],
                cwd=tmp,
                approved=True,
                idle_timeout_seconds=1,
                stale_timeout_seconds=2,
                heartbeat_interval_seconds=0.1,
                session_id="session-001",
                retry_safety="mutating",
            )
            invocation.execution_attempt_id = start_execution_attempt(
                invocation, retry_safety="mutating", approve_ambiguous_retry=False
            )
            append_attempt_observation(
                invocation, "execution_attempt_ambiguous", failure_mode="missing_receipt"
            )
            invocation.session_id = "session-002"

            with self.assertRaisesRegex(ValueError, "operator decision required"):
                start_execution_attempt(
                    invocation, retry_safety="mutating", approve_ambiguous_retry=False
                )
            retry_id = start_execution_attempt(
                invocation,
                retry_safety="mutating",
                approve_ambiguous_retry=True,
                ambiguous_retry_operator_identity="operator",
            )

        self.assertTrue(retry_id.endswith("-002"))

    def test_failed_mutating_attempt_also_requires_operator_retry_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run, _artifact = _stage_run(tmp)
            prompt = tmp / "author-prompt.md"
            prompt.write_text("edit", encoding="utf-8")
            invocation = AgentInvocation(
                run=run, round_id="round-1", phase="author", participant_identity="author",
                participant_profile_id="author-profile", execution_profile_id="author-execution",
                player_id="generic-cli", prompt_path=prompt, raw_output_path=tmp / "author.out",
                command=["provider"], cwd=tmp, approved=True, idle_timeout_seconds=1,
                stale_timeout_seconds=2, heartbeat_interval_seconds=.1, session_id="session-001",
                retry_safety="mutating",
            )
            invocation.execution_attempt_id = start_execution_attempt(
                invocation, retry_safety="mutating", approve_ambiguous_retry=False
            )
            append_attempt_observation(
                invocation, "execution_attempt_failed", failure_mode="nonzero_exit", exit_code=23
            )
            invocation.session_id = "session-002"

            with self.assertRaisesRegex(ValueError, "operator decision required"):
                start_execution_attempt(
                    invocation, retry_safety="mutating", approve_ambiguous_retry=False
                )

    def test_receipt_uses_attempt_id_when_session_ids_repeat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run, _artifact = _stage_run(tmp)
            attempts: list[tuple[AgentInvocation, str]] = []
            for actor in ("reviewer", "other-reviewer"):
                prompt = tmp / f"{actor}.md"
                prompt.write_text(actor, encoding="utf-8")
                invocation = AgentInvocation(
                    run=run, round_id="round-1", phase="reviewer",
                    participant_identity=actor, participant_profile_id="reviewer-profile",
                    execution_profile_id="reviewer-execution", player_id="generic-cli",
                    prompt_path=prompt, raw_output_path=tmp / f"{actor}.out", command=["provider"],
                    cwd=tmp, approved=True, idle_timeout_seconds=1, stale_timeout_seconds=2,
                    heartbeat_interval_seconds=.1, session_id="session-001",
                )
                invocation.execution_attempt_id = start_execution_attempt(
                    invocation, retry_safety="read_only", approve_ambiguous_retry=False
                )
                append_attempt_observation(
                    invocation, "execution_attempt_ambiguous", failure_mode="missing_receipt"
                )
                attempts.append((invocation, invocation.execution_attempt_id))
            first, first_id = attempts[0]
            receipt = Record(
                "RawReviewerOutput", "receipt-1", run / "rounds/round-001/reviews/reviewer.md", 1,
                {"reviewer_identity": "reviewer", "artifact_version_id": "v1"},
            )
            source = ReceiptAttemptSource(
                first_id, first.session_id, first.participant_identity, first.round_id, first.phase
            )
            with run_lock(run):
                complete_attempt_for_receipt_locked(run, source, receipt)
            completed = [
                event for event in attempt_events(run)
                if event["event_type"] == "execution_attempt_completed"
            ]

        self.assertEqual(completed[0]["details"]["attempt_id"], first_id)  # type: ignore[index]
        self.assertNotEqual(completed[0]["details"]["attempt_id"], attempts[1][1])  # type: ignore[index]

    def test_reused_raw_output_path_selects_latest_session_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run, _artifact = _stage_run(tmp)
            args = invoke_args(run, tmp, "raw")
            self.assertEqual(cmd_invoke_agent(args), 0)
            first_attempt_id = str(attempt_events(run)[0]["details"]["attempt_id"])  # type: ignore[index]
            self.assertEqual(cmd_invoke_agent(args), 0)
            started = [
                event for event in attempt_events(run)
                if event["event_type"] == "execution_attempt_started"
            ]
            second_attempt_id = str(started[-1]["details"]["attempt_id"])  # type: ignore[index]
            capture_args = CaptureCommandInput(
                run=str(run), phase="reviewer", actor="reviewer",
                review_batch="review-batch-round-1-fresh_review", artifact_version="v1",
                source_file=args.raw_output, source_mode="file", source_command=None,
                provider="generic-cli", round="round-1", validator_id=None, result=None,
                waiver_authority=None, waiver_rationale=None, no_append_record=False,
                no_narrative_extract=False,
            )

            self.assertEqual(cmd_capture(capture_args), 0)
            completed_id = str(attempt_events(run)[-1]["details"]["attempt_id"])  # type: ignore[index]

        self.assertNotEqual(first_attempt_id, second_attempt_id)
        self.assertEqual(completed_id, second_attempt_id)

    def test_author_artifact_receipt_completes_mutating_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run, _artifact = _stage_run(tmp)
            prompt = round_dir(run, "round-1") / "prompts/author.md"
            prompt.write_text("write v2", encoding="utf-8")
            args = invoke_args(
                run, tmp, "raw", phase="author", actor="author", prompt=str(prompt),
                raw_output=str(round_dir(run, "round-1") / "raw/author.out"),
            )
            self.assertEqual(cmd_invoke_agent(args), 0)
            attempt_id = str(attempt_events(run)[0]["details"]["attempt_id"])  # type: ignore[index]
            content = tmp / "v2.md"
            content.write_text("v2", encoding="utf-8")
            artifact_args = argparse.Namespace(
                run=str(run), artifact_version="v2", predecessor="v1",
                content_locator=str(content), produced_by="author", actor="author",
                execution_attempt=attempt_id,
            )

            self.assertEqual(cmd_new_artifact(artifact_args), 0)
            events = attempt_events(run)
            journal_messages = run_event_messages(run)

        self.assertEqual(events[-1]["event_type"], "execution_attempt_completed")
        self.assertEqual(events[-1]["details"]["receipt_record_type"], "ArtifactVersion")  # type: ignore[index]
        self.assertEqual(journal_messages, [])

    def test_structured_provider_requires_final_output(self) -> None:
        for mode, failure_mode in (
            ("malformed_stream", "malformed_stream"),
            ("missing_final_output", "missing_final_output"),
            ("partial_output", "missing_final_output"),
        ):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp_name:
                tmp = Path(tmp_name)
                run, _artifact = _stage_run(tmp)
                args = invoke_args(
                    run, tmp, mode, player="codex-cli",
                    command=[sys.executable, str(FAKE_PROVIDER), mode, "--json"],
                )

                self.assertEqual(cmd_invoke_agent(args), 1)
                events = attempt_events(run)

                self.assertEqual(events[-1]["details"]["failure_mode"], failure_mode)  # type: ignore[index]

    def test_structured_provider_accepts_fresh_final_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run, _artifact = _stage_run(tmp)
            args = invoke_args(
                run, tmp, "structured", player="codex-cli",
                command=[sys.executable, str(FAKE_PROVIDER), "structured", "--json"],
            )

            self.assertEqual(cmd_invoke_agent(args), 0)
            events = attempt_events(run)

        self.assertEqual(events[-1]["event_type"], "execution_attempt_ambiguous")
        self.assertEqual(events[-1]["details"]["failure_mode"], "missing_receipt")  # type: ignore[index]

    def test_fake_provider_captures_argv_and_stdin(self) -> None:
        import subprocess

        with tempfile.TemporaryDirectory() as tmp_name:
            capture_path = Path(tmp_name) / "provider-input.json"
            completed = subprocess.run(
                [
                    sys.executable, str(FAKE_PROVIDER), "raw",
                    "--capture-input", str(capture_path),
                ],
                input="exact prompt", text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                check=True,
            )
            captured = json.loads(capture_path.read_text(encoding="utf-8"))

        self.assertEqual(captured["stdin"], "exact prompt")
        self.assertEqual(captured["argv"][0], "raw")

    def test_fake_provider_structured_and_edge_modes_are_deterministic(self) -> None:
        import subprocess

        for mode in (
            "structured",
            "malformed_stream",
            "missing_final_output",
            "missing_session_id",
            "digest_mismatch",
            "resumed",
        ):
            completed = subprocess.run(
                [sys.executable, str(FAKE_PROVIDER), mode],
                input="prompt",
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            self.assertTrue(completed.stdout)
            if mode not in {"malformed_stream"}:
                for line in completed.stdout.splitlines():
                    json.loads(line)


if __name__ == "__main__":
    unittest.main()
