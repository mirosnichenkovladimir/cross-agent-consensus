from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "skills" / "cross-agent-consensus"
sys.path.insert(0, str(PACKAGE_ROOT))

from cross_agent_consensus.invocation.adapters import HermesCliPlayer
from cross_agent_consensus.invocation.process_monitor import (
    cmd_agent_cancel,
    cmd_invoke_agent,
    current_process_identity,
)
from cross_agent_consensus.layout import round_dir
from cross_agent_consensus.models import AgentSessionPaths, InvocationCommandInput
from cross_agent_consensus.run_audit import read_run_events
from test_integrity_audit import _stage_run


FAKE_HERMES = Path(__file__).parent / "fixtures" / "fake_hermes.py"
HERMES_BRIDGE_COMMAND = [
    sys.executable,
    "-m",
    "cross_agent_consensus.hermes_cli",
    "--ignore-rules",
]


def invocation_args(
    run: Path,
    working_directory: Path,
    **overrides: object,
) -> InvocationCommandInput:
    prompt = round_dir(run, "round-1") / "prompts" / "reviewers" / "reviewer.md"
    prompt.write_text("inspect Hermes connector", encoding="utf-8")
    values: dict[str, object] = {
        "run": str(run),
        "round": "round-1",
        "phase": "reviewer",
        "actor": "reviewer",
        "player": "hermes-cli",
        "participant_profile_id": None,
        "execution_profile_id": None,
        "prompt": str(prompt),
        "raw_output": str(
            round_dir(run, "round-1") / "raw" / "reviewers" / "reviewer.out"
        ),
        "approved": True,
        "command": HERMES_BRIDGE_COMMAND,
        "cwd": str(working_directory),
        "idle_timeout_seconds": 1.0,
        "stale_timeout_seconds": 2.0,
        "heartbeat_interval_seconds": 0.02,
    }
    values.update(overrides)
    return InvocationCommandInput(**values)  # type: ignore[arg-type]


def connector_environment(mode: str = "complete") -> dict[str, str]:
    FAKE_HERMES.chmod(0o755)
    return {
        "PYTHONPATH": str(PACKAGE_ROOT),
        "CAC_HERMES_EXECUTABLE": str(FAKE_HERMES),
        "FAKE_HERMES_MODE": mode,
    }


def provider_captures(run: Path) -> list[dict[str, object]]:
    return [
        event
        for event in read_run_events(run)
        if event.get("event_type") == "provider_session_captured"
    ]


def attempt_failures(run: Path) -> list[dict[str, object]]:
    return [
        event
        for event in read_run_events(run)
        if event.get("event_type") == "execution_attempt_failed"
    ]


def session_paths(session: Path) -> AgentSessionPaths:
    return AgentSessionPaths(
        session=session,
        invocation=session / "invocation.json",
        command=session / "command.json",
        prompt=session / "prompt.md",
        events=session / "events.jsonl",
        agent_log=session / "agent.log",
        stdout=session / "stdout.raw",
        stderr=session / "stderr.raw",
        state=session / "state.json",
        exit=session / "exit.json",
        final_output=session / "final-output.md",
    )


class HermesConnectorConformanceTests(unittest.TestCase):
    def test_fresh_and_resumed_invocations_preserve_provider_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name, patch.dict(
            os.environ, connector_environment(), clear=False
        ):
            working_directory = Path(tmp_name)
            run, _artifact = _stage_run(working_directory)
            self.assertEqual(
                cmd_invoke_agent(invocation_args(run, working_directory)), 0
            )
            first_capture = provider_captures(run)[0]["details"]
            assert isinstance(first_capture, dict)
            first_entry_id = str(first_capture["provider_session_entry_id"])
            resumed = invocation_args(
                run,
                working_directory,
                resume_provider_session_entry_id=first_entry_id,
            )

            self.assertEqual(cmd_invoke_agent(resumed), 0)
            captures = provider_captures(run)
            second_capture = captures[1]["details"]
            assert isinstance(second_capture, dict)
            resumed_output = Path(
                str(resumed.raw_output) + ".final-output.md"
            ).read_text(
                encoding="utf-8"
            )

        self.assertEqual(len(captures), 2)
        self.assertEqual(first_capture["provider_session_id"], "fake-hermes-001")
        self.assertEqual(second_capture["provider_session_id"], "fake-hermes-001")
        self.assertEqual(
            second_capture["predecessor_provider_session_entry_id_or_null"],
            first_entry_id,
        )
        self.assertIn(
            "resumed-from-fake-hermes-001:inspect Hermes connector",
            resumed_output,
        )

    def test_rotated_provider_session_becomes_next_resume_leaf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name, patch.dict(
            os.environ, connector_environment("rotate_on_resume"), clear=False
        ):
            working_directory = Path(tmp_name)
            run, _artifact = _stage_run(working_directory)
            self.assertEqual(
                cmd_invoke_agent(invocation_args(run, working_directory)), 0
            )
            first_details = provider_captures(run)[0]["details"]
            assert isinstance(first_details, dict)
            self.assertEqual(
                cmd_invoke_agent(
                    invocation_args(
                        run,
                        working_directory,
                        resume_provider_session_entry_id=str(
                            first_details["provider_session_entry_id"]
                        ),
                    )
                ),
                0,
            )
            second_details = provider_captures(run)[1]["details"]
            assert isinstance(second_details, dict)
            third = invocation_args(
                run,
                working_directory,
                resume_provider_session_entry_id=str(
                    second_details["provider_session_entry_id"]
                ),
            )
            self.assertEqual(cmd_invoke_agent(third), 0)
            captures = provider_captures(run)
            third_output = Path(
                str(third.raw_output) + ".final-output.md"
            ).read_text(encoding="utf-8")

        self.assertEqual(
            [
                capture["details"]["provider_session_id"]  # type: ignore[index]
                for capture in captures
            ],
            ["fake-hermes-001", "fake-hermes-002", "fake-hermes-002"],
        )
        self.assertIn("resumed-from-fake-hermes-002", third_output)

    def test_missing_session_identifier_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name, patch.dict(
            os.environ, connector_environment("missing_session"), clear=False
        ):
            working_directory = Path(tmp_name)
            run, _artifact = _stage_run(working_directory)
            return_code = cmd_invoke_agent(invocation_args(run, working_directory))
            failures = attempt_failures(run)

        self.assertEqual(return_code, 1)
        self.assertEqual(
            failures[-1]["details"]["failure_mode"],  # type: ignore[index]
            "missing_session_identifier",
        )

    def test_conflicting_session_identifiers_fail_as_nonzero_provider_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name, patch.dict(
            os.environ, connector_environment("conflicting_sessions"), clear=False
        ):
            working_directory = Path(tmp_name)
            run, _artifact = _stage_run(working_directory)
            return_code = cmd_invoke_agent(invocation_args(run, working_directory))
            failures = attempt_failures(run)

        self.assertEqual(return_code, 4)
        self.assertEqual(
            failures[-1]["details"]["failure_mode"],  # type: ignore[index]
            "nonzero_exit",
        )

    def test_stale_hermes_process_is_timed_out(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name, patch.dict(
            os.environ, connector_environment("delay"), clear=False
        ):
            working_directory = Path(tmp_name)
            run, _artifact = _stage_run(working_directory)
            args = invocation_args(
                run,
                working_directory,
                idle_timeout_seconds=0.05,
                stale_timeout_seconds=0.1,
            )
            return_code = cmd_invoke_agent(args)
            failures = attempt_failures(run)

        self.assertEqual(return_code, 4)
        self.assertEqual(
            failures[-1]["details"]["failure_mode"],  # type: ignore[index]
            "timeout",
        )

    def test_operator_cancellation_stops_hermes_process_group(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name, patch.dict(
            os.environ, connector_environment("delay"), clear=False
        ):
            working_directory = Path(tmp_name)
            run, _artifact = _stage_run(working_directory)
            args = invocation_args(
                run,
                working_directory,
                idle_timeout_seconds=5,
                stale_timeout_seconds=10,
            )
            invoke_codes: list[int] = []
            worker = threading.Thread(
                target=lambda: invoke_codes.append(cmd_invoke_agent(args))
            )
            worker.start()
            session = (
                round_dir(run, "round-1")
                / "agents"
                / "reviewer"
                / "session-001"
            )
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if (session / "state.json").is_file():
                    state = json.loads(
                        (session / "state.json").read_text(encoding="utf-8")
                    )
                    if state.get("state") == "running":
                        break
                time.sleep(0.02)
            cancel_args = argparse.Namespace(
                run=str(run),
                round="round-1",
                actor="reviewer",
                session=None,
                reason="Hermes conformance cancellation",
                grace_seconds=0.3,
            )

            self.assertEqual(cmd_agent_cancel(cancel_args), 0)
            worker.join(timeout=3)
            final_state = json.loads(
                (session / "state.json").read_text(encoding="utf-8")
            )

        self.assertFalse(worker.is_alive())
        self.assertEqual(invoke_codes, [4])
        self.assertEqual(final_state["state"], "cancelled")

    @unittest.skipUnless(sys.platform == "darwin", "macOS libproc contract")
    def test_darwin_process_identity_does_not_invoke_ps(self) -> None:
        with patch(
            "cross_agent_consensus.invocation.process_monitor.subprocess.run",
            side_effect=PermissionError("ps denied"),
        ):
            identity = current_process_identity(os.getpid())

        self.assertIsNotNone(identity)
        assert identity is not None
        self.assertEqual(identity["method"], "darwin_proc_bsdinfo_starttime")

    def test_installed_compatibility_entrypoint_exports_bridge_pythonpath(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            working_directory = Path(tmp_name)
            home = working_directory / "home"
            hermes_home = working_directory / "hermes-home"
            home.mkdir()
            install_environment = os.environ.copy()
            install_environment.update(
                {"HOME": str(home), "HERMES_HOME": str(hermes_home)}
            )
            install = subprocess.run(
                [
                    str(REPO_ROOT / "scripts" / "install-cac"),
                    "--target",
                    "hermes",
                    "--no-selftest",
                ],
                cwd=str(REPO_ROOT),
                env=install_environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(
                install.returncode, 0, install.stderr + install.stdout
            )
            installed_cac_tool = (
                hermes_home
                / "skills"
                / "cross-agent-consensus"
                / "scripts"
                / "cac_tool.py"
            )
            run, _artifact = _stage_run(working_directory)
            prompt = (
                round_dir(run, "round-1")
                / "prompts"
                / "reviewers"
                / "reviewer.md"
            )
            prompt.write_text("compatibility bridge", encoding="utf-8")
            raw_output = (
                round_dir(run, "round-1")
                / "raw"
                / "reviewers"
                / "reviewer.out"
            )
            invocation_environment = install_environment.copy()
            invocation_environment.update(connector_environment())
            invocation_environment.pop("PYTHONPATH", None)
            invocation_environment["PATH"] = os.environ["PATH"]
            invoke = subprocess.run(
                [
                    sys.executable,
                    str(installed_cac_tool),
                    "invoke-agent",
                    "--run",
                    str(run),
                    "--round",
                    "round-1",
                    "--phase",
                    "reviewer",
                    "--actor",
                    "reviewer",
                    "--player",
                    "hermes-cli",
                    "--prompt",
                    str(prompt),
                    "--raw-output",
                    str(raw_output),
                    "--approved",
                    "--cwd",
                    str(working_directory),
                    "--command",
                    sys.executable,
                    "-m",
                    "cross_agent_consensus.hermes_cli",
                    "--ignore-rules",
                ],
                cwd=str(working_directory),
                env=invocation_environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            final_output_exists = Path(
                str(raw_output) + ".final-output.md"
            ).is_file()

        self.assertEqual(invoke.returncode, 0, invoke.stderr + invoke.stdout)
        self.assertTrue(final_output_exists)

    def test_malformed_jsonl_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            paths = session_paths(Path(tmp_name) / "session-001")
            paths.session.mkdir()
            paths.stdout.write_text("{not-json\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "malformed stream"):
                HermesCliPlayer().extract_final_output(
                    paths, require_structured=True
                )


if __name__ == "__main__":
    unittest.main()
