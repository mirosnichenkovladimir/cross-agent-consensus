from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "skills" / "cross-agent-consensus"
sys.path.insert(0, str(PACKAGE_ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from cross_agent_consensus.invocation.process_monitor import cmd_invoke_agent
from cross_agent_consensus.layout import round_dir
from cross_agent_consensus.models import InvocationCommandInput
from cross_agent_consensus.run_audit import read_run_events
from test_integrity_audit import _stage_run


FAKE_KIMI = Path(__file__).parent / "fixtures" / "fake_kimi.py"
KIMI_BRIDGE_COMMAND = [
    sys.executable,
    "-m",
    "cross_agent_consensus.kimi_cli",
    "--model",
    "kimi-code/k3",
]


def invocation_args(
    run: Path,
    working_directory: Path,
    **overrides: object,
) -> InvocationCommandInput:
    prompt = round_dir(run, "round-1") / "prompts" / "reviewers" / "reviewer.md"
    prompt.write_text("inspect Kimi connector", encoding="utf-8")
    values: dict[str, object] = {
        "run": str(run),
        "round": "round-1",
        "phase": "reviewer",
        "actor": "reviewer",
        "player": "kimi-cli",
        "participant_profile_id": None,
        "execution_profile_id": None,
        "prompt": str(prompt),
        "raw_output": str(
            round_dir(run, "round-1") / "raw" / "reviewers" / "reviewer.out"
        ),
        "approved": True,
        "command": KIMI_BRIDGE_COMMAND,
        "cwd": str(working_directory),
        "idle_timeout_seconds": 1.0,
        "stale_timeout_seconds": 2.0,
        "heartbeat_interval_seconds": 0.02,
    }
    values.update(overrides)
    invocation = InvocationCommandInput(**values)  # type: ignore[arg-type]
    invocation.env_allowlist = [  # type: ignore[attr-defined]
        "PYTHONPATH",
        "CAC_KIMI_EXECUTABLE",
        "FAKE_KIMI_MODE",
    ]
    return invocation


def connector_environment(mode: str = "complete") -> dict[str, str]:
    FAKE_KIMI.chmod(0o755)
    return {
        "PYTHONPATH": str(PACKAGE_ROOT),
        "CAC_KIMI_EXECUTABLE": str(FAKE_KIMI),
        "FAKE_KIMI_MODE": mode,
    }


def provider_captures(run: Path) -> list[dict[str, object]]:
    return [
        event
        for event in read_run_events(run)
        if event.get("event_type") == "provider_session_captured"
    ]


class KimiConnectorConformanceTests(unittest.TestCase):
    def test_k3_fresh_and_resumed_invocations_preserve_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name, patch.dict(
            os.environ, connector_environment(), clear=False
        ):
            working_directory = Path(tmp_name)
            run, _artifact = _stage_run(working_directory)

            self.assertEqual(
                cmd_invoke_agent(invocation_args(run, working_directory)),
                0,
            )
            first_command = json.loads(
                (
                    run
                    / "rounds"
                    / "round-001"
                    / "agents"
                    / "reviewer"
                    / "session-001"
                    / "command.json"
                ).read_text(encoding="utf-8")
            )
            first_details = provider_captures(run)[0]["details"]
            assert isinstance(first_details, dict)
            first_entry_id = str(first_details["provider_session_entry_id"])
            resumed = invocation_args(
                run,
                working_directory,
                resume_provider_session_entry_id=first_entry_id,
            )

            self.assertEqual(cmd_invoke_agent(resumed), 0)
            resumed_command = json.loads(
                (
                    run
                    / "rounds"
                    / "round-001"
                    / "agents"
                    / "reviewer"
                    / "session-002"
                    / "command.json"
                ).read_text(encoding="utf-8")
            )
            captures = provider_captures(run)
            second_details = captures[1]["details"]
            assert isinstance(second_details, dict)
            resumed_output = Path(
                str(resumed.raw_output) + ".final-output.md"
            ).read_text(encoding="utf-8")

        self.assertEqual(first_command["argv"], KIMI_BRIDGE_COMMAND)
        self.assertEqual(first_command["prompt_transport"], "stdin")
        self.assertNotIn("--prompt", first_command["argv"])
        self.assertEqual(
            resumed_command["argv"],
            [*KIMI_BRIDGE_COMMAND, "--session", "fake-kimi-001"],
        )
        self.assertNotIn("--prompt", resumed_command["argv"])
        self.assertEqual(first_details["provider_session_id"], "fake-kimi-001")
        self.assertEqual(second_details["provider_session_id"], "fake-kimi-001")
        self.assertEqual(
            second_details["predecessor_provider_session_entry_id_or_null"],
            first_entry_id,
        )
        self.assertIn("model=kimi-code/k3", resumed_output)
        self.assertIn("session=fake-kimi-001", resumed_output)
        self.assertIn("prompt=inspect Kimi connector", resumed_output)

    def test_missing_terminal_resume_hint_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name, patch.dict(
            os.environ, connector_environment("missing_session"), clear=False
        ):
            working_directory = Path(tmp_name)
            run, _artifact = _stage_run(working_directory)
            return_code = cmd_invoke_agent(invocation_args(run, working_directory))
            failures = [
                event
                for event in read_run_events(run)
                if event.get("event_type") == "execution_attempt_failed"
            ]

        self.assertEqual(return_code, 1)
        self.assertEqual(
            failures[-1]["details"]["failure_mode"],  # type: ignore[index]
            "missing_final_output",
        )

    def test_conflicting_session_identifiers_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name, patch.dict(
            os.environ, connector_environment("conflicting_sessions"), clear=False
        ):
            working_directory = Path(tmp_name)
            run, _artifact = _stage_run(working_directory)
            return_code = cmd_invoke_agent(invocation_args(run, working_directory))
            failures = [
                event
                for event in read_run_events(run)
                if event.get("event_type") == "execution_attempt_failed"
            ]

        self.assertEqual(return_code, 1)
        self.assertEqual(
            failures[-1]["details"]["failure_mode"],  # type: ignore[index]
            "missing_session_identifier",
        )

    def test_malformed_stream_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name, patch.dict(
            os.environ, connector_environment("malformed"), clear=False
        ):
            working_directory = Path(tmp_name)
            run, _artifact = _stage_run(working_directory)
            return_code = cmd_invoke_agent(invocation_args(run, working_directory))
            failures = [
                event
                for event in read_run_events(run)
                if event.get("event_type") == "execution_attempt_failed"
            ]

        self.assertEqual(return_code, 1)
        self.assertEqual(
            failures[-1]["details"]["failure_mode"],  # type: ignore[index]
            "malformed_stream",
        )


if __name__ == "__main__":
    unittest.main()
