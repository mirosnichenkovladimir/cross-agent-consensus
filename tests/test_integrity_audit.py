from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "skills" / "cross-agent-consensus"
sys.path.insert(0, str(PACKAGE_ROOT))

from cross_agent_consensus.approval import (
    approval_binding,
    stamp_operator_approval,
    verify_invocation_approval,
)
from cross_agent_consensus.capture import cmd_capture
from cross_agent_consensus.init import build_init_files
from cross_agent_consensus.integrity import _approval_covers_session, command_sha256
from cross_agent_consensus.io import append_jsonl, sha256_file
from cross_agent_consensus.layout import required_run_paths, round_dir
from cross_agent_consensus.invocation.process_monitor import cmd_invoke_agent
from cross_agent_consensus.models import CaptureCommandInput, InvocationCommandInput
from cross_agent_consensus.records import parse_run_records, records_by_type
from cross_agent_consensus.run_audit import (
    LEGACY_RUN_EVENT_SCHEMA,
    RUN_EVENT_SCHEMA,
    append_run_event_locked,
    derive_run_phase,
    exclusive_file_lock,
    read_run_events,
    run_event_messages,
)
from cross_agent_consensus.validation import check_integrity


def _init_args(tmp: Path, artifact: Path) -> argparse.Namespace:
    return argparse.Namespace(
        run_root=str(tmp),
        profile="document-consensus",
        validator=[],
        orchestrator="orchestrator",
        author="author",
        reviewer=["reviewer"],
        review_focus=[],
        artifact_locator=str(artifact),
        success_criterion=[],
        task="audit exact inputs",
        run_id=None,
        max_fresh_review_rounds=1,
        max_fresh_review_rounds_without_human_approval=2,
        max_remediation_rounds=2,
        material_by_default=[],
        non_blocking_by_default=[],
        escalation_policy="policy",
        waiver_authority=None,
        unattended_invocation=False,
        unattended_scope=[],
        human_supervisor="human",
        review_objective=None,
        in_scope=[],
        out_of_scope=[],
        promotion_policy=None,
        config_resolution=None,
    )


def _stage_run(tmp: Path) -> tuple[Path, Path]:
    artifact = tmp / "artifact.md"
    artifact.write_text("approved content\n", encoding="utf-8")
    run_id = "audit-exact-inputs-consensus-001"
    files = build_init_files(_init_args(tmp, artifact), run_id, "2026-07-13T00:00:00Z")
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    run = tmp / run_id
    for path in required_run_paths(run):
        if path.suffix:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text("", encoding="utf-8")
        else:
            path.mkdir(parents=True, exist_ok=True)
    return run, artifact


class IntegrityTests(unittest.TestCase):
    def test_session_approval_requires_round_prompt_path_and_artifact_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run, artifact = _stage_run(Path(tmp_name))
            prompt = round_dir(run, "round-1") / "prompts" / "reviewers" / "reviewer.md"
            prompt.write_text("Review the exact artifact.\n", encoding="utf-8")
            command = ["reviewer-cli", "--json"]
            binding = approval_binding(
                run,
                parse_run_records(run),
                actor_identity="reviewer",
                player_id="generic-cli",
                phase="reviewer",
                round_id="round-1",
                prompt_path=prompt,
                command=command,
                artifact_version_id="v1",
            )
            stamp_operator_approval(
                run,
                round_id="round-1",
                phase="reviewer",
                bindings=[binding],
                mechanism="cli_approved_flag",
                operator_identity="human",
            )
            records = parse_run_records(run)
            exact = {
                "actor_identity": "reviewer",
                "player_id": "generic-cli",
                "phase": "reviewer",
                "round_id": "round-001",
                "prompt_path": str(prompt.relative_to(run)),
                "prompt_sha256": binding["prompt_sha256"],
                "command_digest": command_sha256(command),
                "working_directory": str(Path.cwd().resolve()),
                "artifact_version_id": "v1",
                "artifact_sha256": sha256_file(artifact),
            }

            self.assertTrue(_approval_covers_session(records, **exact))
            for field, changed in (
                ("round_id", "round-2"),
                ("prompt_path", "rounds/round-002/prompts/reviewers/reviewer.md"),
                ("artifact_sha256", "0" * 64),
            ):
                mismatched = dict(exact)
                mismatched[field] = changed
                self.assertFalse(_approval_covers_session(records, **mismatched), field)

    def test_artifact_drift_fails_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run, artifact = _stage_run(Path(tmp_name))
            self.assertTrue(check_integrity(run).ok)

            artifact.write_text("changed after review\n", encoding="utf-8")
            result = check_integrity(run)

        self.assertFalse(result.ok)
        self.assertTrue(any("ArtifactVersion v1 drifted" in message for message in result.messages))

    def test_approval_record_edit_fails_digest_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run, _artifact = _stage_run(Path(tmp_name))
            prompt = round_dir(run, "round-1") / "prompts" / "reviewers" / "reviewer.md"
            prompt.write_text("Review the exact artifact.\n", encoding="utf-8")
            binding = approval_binding(
                run,
                parse_run_records(run),
                actor_identity="reviewer",
                player_id="generic-cli",
                phase="reviewer",
                round_id="round-1",
                prompt_path=prompt,
                command=["reviewer-cli"],
                artifact_version_id="v1",
            )
            target = stamp_operator_approval(
                run,
                round_id="round-1",
                phase="reviewer",
                bindings=[binding],
                mechanism="cli_approved_flag",
                operator_identity="human",
            )
            target.write_text(
                target.read_text(encoding="utf-8").replace(
                    "operator_identity_or_null: human",
                    "operator_identity_or_null: forged",
                ),
                encoding="utf-8",
            )
            events_path = run / "events.jsonl"
            downgraded_events = []
            for line in events_path.read_text(encoding="utf-8").splitlines():
                event = json.loads(line)
                event["schema_version"] = LEGACY_RUN_EVENT_SCHEMA
                downgraded_events.append(json.dumps(event, sort_keys=True))
            events_path.write_text("\n".join(downgraded_events) + "\n", encoding="utf-8")

            integrity = check_integrity(run)

        self.assertFalse(integrity.ok)
        self.assertTrue(any("differs from run-event anchor" in message for message in integrity.messages))

    def test_approval_binds_prompt_command_and_artifact_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run, _artifact = _stage_run(Path(tmp_name))
            prompt = round_dir(run, "round-1") / "prompts" / "reviewers" / "reviewer.md"
            prompt.write_text("Review the exact artifact.\n", encoding="utf-8")
            command = ["reviewer-cli", "--json"]
            records = parse_run_records(run)
            binding = approval_binding(
                run,
                records,
                actor_identity="reviewer",
                player_id="generic-cli",
                phase="reviewer",
                round_id="round-1",
                prompt_path=prompt,
                command=command,
                artifact_version_id="v1",
            )
            stamp_operator_approval(
                run,
                round_id="round-1",
                phase="reviewer",
                bindings=[binding],
                mechanism="cli_approved_flag",
                operator_identity="human",
            )

            approval = records_by_type(parse_run_records(run), "OperatorApproval")[0]
            stored_binding = approval.data["approved_invocations"][0]
            self.assertEqual(stored_binding["prompt_sha256"], binding["prompt_sha256"])
            self.assertEqual(stored_binding["command_sha256"], command_sha256(command))
            self.assertEqual(stored_binding["artifact_sha256_or_null"], binding["artifact_sha256_or_null"])
            self.assertTrue(check_integrity(run).ok)

            prompt.write_text("Different instructions.\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "drifted after OperatorApproval"):
                verify_invocation_approval(
                    run,
                    actor_identity="reviewer",
                    player_id="generic-cli",
                    phase="reviewer",
                    round_id="round-1",
                    prompt_path=prompt,
                    command=command,
                )
            integrity = check_integrity(run)
            self.assertFalse(integrity.ok)
            self.assertTrue(any("approved prompt sha256 mismatch" in message for message in integrity.messages))

    def test_tampered_approval_binding_is_rejected_before_session_allocation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run, _artifact = _stage_run(tmp)
            prompt = round_dir(run, "round-1") / "prompts" / "reviewers" / "reviewer.md"
            prompt.write_text("Approved prompt.\n", encoding="utf-8")
            original_command = [sys.executable, "-c", "print('approved')"]
            marker = tmp / "substituted-command-started"
            substituted_command = [
                sys.executable,
                "-c",
                f"from pathlib import Path; Path({str(marker)!r}).touch()",
            ]
            binding = approval_binding(
                run,
                parse_run_records(run),
                actor_identity="reviewer",
                player_id="generic-cli",
                phase="reviewer",
                round_id="round-1",
                prompt_path=prompt,
                command=original_command,
                artifact_version_id="v1",
                working_directory=tmp,
            )
            approval_path = stamp_operator_approval(
                run,
                round_id="round-1",
                phase="reviewer",
                bindings=[binding],
                mechanism="cli_approved_flag",
                operator_identity="human",
            )
            approval_path.write_text(
                approval_path.read_text(encoding="utf-8").replace(
                    command_sha256(original_command),
                    command_sha256(substituted_command),
                ),
                encoding="utf-8",
            )
            args = InvocationCommandInput(
                run=str(run),
                round="round-1",
                phase="reviewer",
                actor="reviewer",
                player="generic-cli",
                prompt=str(prompt),
                raw_output=str(round_dir(run, "round-1") / "raw" / "reviewers" / "reviewer.out"),
                approved=True,
                command=substituted_command,
                cwd=str(tmp),
                idle_timeout_seconds=5.0,
                stale_timeout_seconds=10.0,
                heartbeat_interval_seconds=0.05,
                require_existing_approval=True,
            )

            self.assertEqual(cmd_invoke_agent(args), 3)

            sessions = list((round_dir(run, "round-1") / "agents" / "reviewer").glob("session-*"))
        self.assertFalse(marker.exists())
        self.assertEqual(sessions, [])

    def test_approval_hashes_readable_artifact_when_recorded_hash_is_null(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run, artifact = _stage_run(Path(tmp_name))
            artifact_record_path = run / "artifacts" / "v1.md"
            lines = artifact_record_path.read_text(encoding="utf-8").splitlines()
            artifact_record_path.write_text(
                "\n".join(
                    "content_hash_or_null: null" if line.startswith("content_hash_or_null:") else line
                    for line in lines
                )
                + "\n",
                encoding="utf-8",
            )
            prompt = round_dir(run, "round-1") / "prompts" / "reviewers" / "reviewer.md"
            prompt.write_text("Review the artifact.\n", encoding="utf-8")
            command = ["reviewer-cli"]
            binding = approval_binding(
                run,
                parse_run_records(run),
                actor_identity="reviewer",
                player_id="generic-cli",
                phase="reviewer",
                round_id="round-1",
                prompt_path=prompt,
                command=command,
                artifact_version_id="v1",
            )
            self.assertEqual(binding["artifact_sha256_or_null"], sha256_file(artifact))
            stamp_operator_approval(
                run,
                round_id="round-1",
                phase="reviewer",
                bindings=[binding],
                mechanism="cli_approved_flag",
                operator_identity="human",
            )
            artifact.write_text("changed after approval\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "drifted after OperatorApproval"):
                verify_invocation_approval(
                    run,
                    actor_identity="reviewer",
                    player_id="generic-cli",
                    phase="reviewer",
                    round_id="round-1",
                    prompt_path=prompt,
                    command=command,
                )

    def test_reviewer_capture_records_and_rechecks_payload_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run, _artifact = _stage_run(tmp)
            source = tmp / "review.txt"
            source.write_text("No material findings.\n", encoding="utf-8")
            args = CaptureCommandInput(
                run=str(run),
                phase="reviewer",
                actor="reviewer",
                review_batch="review-batch-round-1-fresh_review",
                artifact_version="v1",
                source_file=str(source),
                source_mode="file",
                source_command=None,
                provider="manual",
                round="round-1",
                validator_id=None,
                result=None,
                waiver_authority=None,
                waiver_rationale=None,
                no_append_record=False,
                no_narrative_extract=False,
            )

            self.assertEqual(cmd_capture(args), 0)
            output = records_by_type(parse_run_records(run), "RawReviewerOutput")[0]
            self.assertEqual(output.data["capture_origin"], "manual_import")
            self.assertIsNone(output.data["session_id_or_null"])
            self.assertIsInstance(output.data["raw_payload_sha256"], str)
            self.assertTrue(check_integrity(run).ok)

            payload = run / str(output.data["raw_payload_path"])
            payload.write_text("tampered\n", encoding="utf-8")
            result = check_integrity(run)

        self.assertFalse(result.ok)
        self.assertTrue(any("raw payload sha256 mismatch" in message for message in result.messages))

    def test_supervised_capture_links_exact_session_and_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run, _artifact = _stage_run(tmp)
            prompt = round_dir(run, "round-1") / "prompts" / "reviewers" / "reviewer.md"
            prompt.write_text("Return a short review.\n", encoding="utf-8")
            raw_output = round_dir(run, "round-1") / "raw" / "reviewers" / "reviewer.out"
            invoke_args = InvocationCommandInput(
                run=str(run),
                round="round-1",
                phase="reviewer",
                actor="reviewer",
                player="generic-cli",
                prompt=str(prompt),
                raw_output=str(raw_output),
                approved=True,
                command=[sys.executable, "-c", "print('review complete')"],
                cwd=str(tmp),
                idle_timeout_seconds=5.0,
                stale_timeout_seconds=10.0,
                heartbeat_interval_seconds=0.05,
            )
            self.assertEqual(cmd_invoke_agent(invoke_args), 0)

            capture_args = CaptureCommandInput(
                run=str(run),
                phase="reviewer",
                actor="reviewer",
                review_batch="review-batch-round-1-fresh_review",
                artifact_version="v1",
                source_file=str(raw_output),
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
            output = records_by_type(parse_run_records(run), "RawReviewerOutput")[0]
            integrity = check_integrity(run)
            self.assertTrue(integrity.ok, integrity.messages)
            command_path = run / str(output.data["session_path_or_null"]) / "command.json"
            command_payload = json.loads(command_path.read_text(encoding="utf-8"))
            command_payload["argv"].append("--changed-after-launch")
            command_path.write_text(json.dumps(command_payload), encoding="utf-8")
            tampered = check_integrity(run)
            self.assertFalse(tampered.ok)
            self.assertTrue(any("no exact-input OperatorApproval" in message for message in tampered.messages))

        self.assertEqual(output.data["capture_origin"], "live_cli")
        self.assertEqual(output.data["session_id_or_null"], "session-001")
        self.assertIn("session-001", str(output.data["session_path_or_null"]))
        self.assertIsInstance(output.data["prompt_sha256_or_null"], str)

    def test_supervised_capture_rejects_replaced_raw_output_after_marker_downgrade(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run, _artifact = _stage_run(tmp)
            prompt = round_dir(run, "round-1") / "prompts" / "reviewers" / "reviewer.md"
            prompt.write_text("Return a short review.\n", encoding="utf-8")
            raw_output = round_dir(run, "round-1") / "raw" / "reviewers" / "reviewer.out"
            invoke_args = InvocationCommandInput(
                run=str(run),
                round="round-1",
                phase="reviewer",
                actor="reviewer",
                player="generic-cli",
                prompt=str(prompt),
                raw_output=str(raw_output),
                approved=True,
                command=[sys.executable, "-c", "print('review complete')"],
                cwd=str(tmp),
                idle_timeout_seconds=5.0,
                stale_timeout_seconds=10.0,
                heartbeat_interval_seconds=0.05,
            )
            self.assertEqual(cmd_invoke_agent(invoke_args), 0)
            session = round_dir(run, "round-1") / "agents" / "reviewer" / "session-001"
            exit_payload = json.loads((session / "exit.json").read_text(encoding="utf-8"))
            del exit_payload["evidence_digest_version"]
            (session / "exit.json").write_text(json.dumps(exit_payload), encoding="utf-8")
            raw_output.write_text("forged after session completion\n", encoding="utf-8")
            capture_args = CaptureCommandInput(
                run=str(run),
                phase="reviewer",
                actor="reviewer",
                review_batch="review-batch-round-1-fresh_review",
                artifact_version="v1",
                source_file=str(raw_output),
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

            self.assertEqual(cmd_capture(capture_args), 1)
            self.assertEqual(records_by_type(parse_run_records(run), "RawReviewerOutput"), [])

    def test_session_evidence_marker_downgrade_fails_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run, _artifact = _stage_run(tmp)
            prompt = round_dir(run, "round-1") / "prompts" / "reviewers" / "reviewer.md"
            prompt.write_text("Return a short review.\n", encoding="utf-8")
            raw_output = round_dir(run, "round-1") / "raw" / "reviewers" / "reviewer.out"
            invoke_args = InvocationCommandInput(
                run=str(run),
                round="round-1",
                phase="reviewer",
                actor="reviewer",
                player="generic-cli",
                prompt=str(prompt),
                raw_output=str(raw_output),
                approved=True,
                command=[sys.executable, "-c", "print('review complete')"],
                cwd=str(tmp),
                idle_timeout_seconds=5.0,
                stale_timeout_seconds=10.0,
                heartbeat_interval_seconds=0.05,
            )
            self.assertEqual(cmd_invoke_agent(invoke_args), 0)
            capture_args = CaptureCommandInput(
                run=str(run),
                phase="reviewer",
                actor="reviewer",
                review_batch="review-batch-round-1-fresh_review",
                artifact_version="v1",
                source_file=str(raw_output),
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
            output = records_by_type(parse_run_records(run), "RawReviewerOutput")[0]
            session = run / str(output.data["session_path_or_null"])
            exit_payload = json.loads((session / "exit.json").read_text(encoding="utf-8"))
            del exit_payload["evidence_digest_version"]
            (session / "exit.json").write_text(json.dumps(exit_payload), encoding="utf-8")
            (session / "prompt.md").write_text("tampered session prompt\n", encoding="utf-8")

            integrity = check_integrity(run)

        self.assertFalse(integrity.ok)
        self.assertTrue(any("session evidence marker mismatch" in message for message in integrity.messages))

    def test_macro_invocation_rejects_prompt_drift_before_process_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run, _artifact = _stage_run(tmp)
            prompt = round_dir(run, "round-1") / "prompts" / "reviewers" / "reviewer.md"
            prompt.write_text("Approved prompt.\n", encoding="utf-8")
            marker = tmp / "process-started"
            command = [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"]
            binding = approval_binding(
                run,
                parse_run_records(run),
                actor_identity="reviewer",
                player_id="generic-cli",
                phase="reviewer",
                round_id="round-1",
                prompt_path=prompt,
                command=command,
                artifact_version_id="v1",
            )
            stamp_operator_approval(
                run,
                round_id="round-1",
                phase="reviewer",
                bindings=[binding],
                mechanism="cli_approved_flag",
                operator_identity="human",
            )
            prompt.write_text("Changed after approval.\n", encoding="utf-8")
            args = InvocationCommandInput(
                run=str(run),
                round="round-1",
                phase="reviewer",
                actor="reviewer",
                player="generic-cli",
                prompt=str(prompt),
                raw_output=str(round_dir(run, "round-1") / "raw" / "reviewers" / "reviewer.out"),
                approved=True,
                command=command,
                cwd=str(tmp),
                idle_timeout_seconds=5.0,
                stale_timeout_seconds=10.0,
                heartbeat_interval_seconds=0.05,
                require_existing_approval=True,
            )

            self.assertEqual(cmd_invoke_agent(args), 3)
            self.assertFalse(marker.exists())


class RunAuditTests(unittest.TestCase):
    def test_parallel_init_allocates_distinct_run_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run_root = tmp / "runs"
            command = [
                str(PACKAGE_ROOT / "scripts" / "consensus"),
                "init",
                "--no-config",
                "--task",
                "parallel allocation",
                "--artifact-locator",
                str(tmp / "artifact.md"),
                "--author",
                "author",
                "--orchestrator",
                "orchestrator",
                "--reviewer",
                "reviewer",
                "--allow-reviewer-config-override",
                "--human-supervisor",
                "human",
                "--run-root",
                str(run_root),
            ]
            (tmp / "artifact.md").write_text("content\n", encoding="utf-8")
            processes = [
                subprocess.Popen(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                for _ in range(2)
            ]
            completed = [process.communicate(timeout=10) + (process.returncode,) for process in processes]
            run_names = sorted(path.name for path in run_root.iterdir() if path.is_dir())

        self.assertEqual([item[2] for item in completed], [0, 0], completed)
        self.assertEqual(
            run_names,
            ["parallel-allocation-consensus-001", "parallel-allocation-consensus-002"],
        )

    def test_exclusive_file_lock_serializes_threads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            lock_path = Path(tmp_name) / "run.lock"
            entries: list[str] = []

            def mutate(label: str) -> None:
                with exclusive_file_lock(lock_path):
                    entries.append(f"{label}-start")
                    time.sleep(0.03)
                    entries.append(f"{label}-end")

            threads = [threading.Thread(target=mutate, args=(label,)) for label in ("first", "second")]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertIn(
            entries,
            [
                ["first-start", "first-end", "second-start", "second-end"],
                ["second-start", "second-end", "first-start", "first-end"],
            ],
        )

    def test_run_events_record_phase_and_reject_invalid_transition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run, _artifact = _stage_run(Path(tmp_name))
            self.assertEqual(derive_run_phase(parse_run_records(run)), "awaiting_review")
            events_path = run / "events.jsonl"
            append_jsonl(
                events_path,
                {
                    "schema_version": RUN_EVENT_SCHEMA,
                    "sequence": 1,
                    "created_at": "2026-07-13T00:00:00Z",
                    "run_id": run.name,
                    "actor_identity": "operator",
                    "event_type": "invalid_test_transition",
                    "phase_before": "terminated",
                    "phase_after": "awaiting_review",
                    "details": {},
                },
            )

            self.assertEqual(len(read_run_events(run)), 1)
            messages = run_event_messages(run)

        self.assertTrue(any("invalid run phase transition terminated -> awaiting_review" in message for message in messages))

    def test_event_anchor_detects_suffix_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run, _artifact = _stage_run(Path(tmp_name))
            append_run_event_locked(
                run,
                "run_initialized",
                actor_identity="orchestrator",
                phase_before="not_initialized",
                phase_after="awaiting_review",
            )
            append_run_event_locked(
                run,
                "status_recorded",
                actor_identity="orchestrator",
                phase_before="awaiting_review",
                phase_after="awaiting_review",
            )
            self.assertEqual(run_event_messages(run), [])
            events_path = run / "events.jsonl"
            first_line = events_path.read_text(encoding="utf-8").splitlines()[0]
            events_path.write_text(first_line + "\n", encoding="utf-8")

            messages = run_event_messages(run)

        self.assertTrue(any("event_count mismatch" in message for message in messages))
        self.assertTrue(any("events.jsonl sha256 mismatch" in message for message in messages))

    def test_event_schema_downgrade_cannot_select_legacy_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run, _artifact = _stage_run(Path(tmp_name))
            append_run_event_locked(
                run,
                "run_initialized",
                actor_identity="orchestrator",
                phase_before="not_initialized",
                phase_after="awaiting_review",
            )
            append_run_event_locked(
                run,
                "status_recorded",
                actor_identity="orchestrator",
                phase_before="awaiting_review",
                phase_after="awaiting_review",
            )
            events_path = run / "events.jsonl"
            first_event = json.loads(events_path.read_text(encoding="utf-8").splitlines()[0])
            first_event["schema_version"] = LEGACY_RUN_EVENT_SCHEMA
            events_path.write_text(json.dumps(first_event, sort_keys=True) + "\n", encoding="utf-8")

            messages = run_event_messages(run)

        self.assertTrue(any("version-2 journals cannot mix" in message for message in messages))
        self.assertTrue(any("event_count mismatch" in message for message in messages))

    def test_versioned_run_requires_event_journal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run, _artifact = _stage_run(Path(tmp_name))

            messages = run_event_messages(run)

        self.assertTrue(any("required for runs created" in message for message in messages))

    def test_first_mutation_of_pre_0_10_run_keeps_legacy_event_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run, _artifact = _stage_run(Path(tmp_name))
            run_md = run / "run.md"
            run_md.write_text(
                run_md.read_text(encoding="utf-8").replace(
                    "- `cross_agent_consensus_version`: `0.11.0`",
                    "- `cross_agent_consensus_version`: `0.9.2`",
                ),
                encoding="utf-8",
            )

            append_run_event_locked(
                run,
                "status_recorded",
                actor_identity="orchestrator",
                phase_before="awaiting_review",
                phase_after="awaiting_review",
            )
            events = read_run_events(run)
            messages = run_event_messages(run)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["schema_version"], LEGACY_RUN_EVENT_SCHEMA)
        self.assertEqual(messages, [])


if __name__ == "__main__":
    unittest.main()
