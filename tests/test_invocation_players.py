from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "skills" / "cross-agent-consensus"
sys.path.insert(0, str(PACKAGE_ROOT))

from cross_agent_consensus.invocation.adapters import (
    PLAYER_ALIASES,
    ClaudeCliPlayer,
    CodexCliPlayer,
    GenericCliPlayer,
    HermesCliPlayer,
    detected_hermes_version,
    get_player_adapter,
)
from cross_agent_consensus.models import AgentInvocation, AgentSessionPaths


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


def invocation(tmp: Path, command: list[str], player_id: str = "generic-cli") -> AgentInvocation:
    return AgentInvocation(
        run=tmp / "run",
        round_id="round-001",
        phase="reviewer",
        participant_identity="reviewer-a",
        participant_profile_id="reviewer-default",
        execution_profile_id="test-execution",
        player_id=player_id,
        prompt_path=tmp / "prompt.md",
        raw_output_path=tmp / "raw.out",
        command=command,
        cwd=tmp,
        approved=True,
        idle_timeout_seconds=1.0,
        stale_timeout_seconds=2.0,
        heartbeat_interval_seconds=0.1,
        session_id="session-001",
    )


class InvocationPlayerTests(unittest.TestCase):
    def test_generic_player_probe_and_command_spec(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            adapter = GenericCliPlayer()
            command = [sys.executable, "--version"]
            capabilities = adapter.probe(command)
            self.assertTrue(capabilities.executable)
            self.assertEqual(capabilities.output_modes, ["raw_stdout"])

            spec = adapter.build_command(invocation(tmp, command))
            self.assertEqual(spec.argv, command)
            self.assertEqual(spec.prompt_transport, "stdin")
            self.assertEqual(spec.output_mode, "raw_stdout")

    def test_claude_and_codex_adapters_detect_json_commands(self) -> None:
        self.assertTrue(ClaudeCliPlayer().command_requests_json(["claude", "-p", "--output-format=stream-json"]))
        self.assertTrue(ClaudeCliPlayer().command_requests_json(["claude", "-p", "--output-format", "stream-json"]))
        self.assertFalse(ClaudeCliPlayer().command_requests_json(["claude", "-p"]))
        self.assertTrue(CodexCliPlayer().command_requests_json(["codex", "exec", "--json", "-"]))
        self.assertFalse(CodexCliPlayer().command_requests_json(["codex", "exec", "-"]))

    def test_codex_provider_session_capture_and_resume_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            paths = session_paths(Path(tmp_name) / "session-001")
            paths.session.mkdir()
            paths.stdout.write_text(
                json.dumps({"type": "thread.started", "thread_id": "thread-123"}) + "\n",
                encoding="utf-8",
            )
            adapter = CodexCliPlayer()

            self.assertEqual(adapter.extract_provider_session_id(paths), "thread-123")
            self.assertEqual(
                adapter.build_resume_command(
                    ["codex", "exec", "--skip-git-repo-check", "--json", "-"],
                    "thread-123",
                ),
                [
                    "codex",
                    "exec",
                    "resume",
                    "--skip-git-repo-check",
                    "--json",
                    "thread-123",
                    "-",
                ],
            )
            self.assertTrue(adapter.probe(["codex"]).supports_resume)
            self.assertTrue(
                adapter.has_native_resume_selector(
                    ["codex", "exec", "--json", "resume", "thread-123", "-"]
                )
            )
            self.assertTrue(
                adapter.has_native_resume_selector(
                    ["codex", "--profile", "default", "exec", "resume", "thread-123", "-"]
                )
            )
            self.assertFalse(
                adapter.has_native_resume_selector(
                    ["codex", "exec", "--profile", "resume", "--json", "-"]
                )
            )
            self.assertEqual(
                adapter.build_resume_command(
                    ["codex", "--profile", "default", "exec", "--json", "-"],
                    "thread-123",
                ),
                [
                    "codex",
                    "--profile",
                    "default",
                    "exec",
                    "resume",
                    "--json",
                    "thread-123",
                    "-",
                ],
            )

    def test_claude_provider_session_capture_and_resume_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            paths = session_paths(Path(tmp_name) / "session-001")
            paths.session.mkdir()
            paths.stdout.write_text(
                json.dumps({"type": "system", "session_id": "claude-123"}) + "\n",
                encoding="utf-8",
            )
            adapter = ClaudeCliPlayer()

            self.assertEqual(adapter.extract_provider_session_id(paths), "claude-123")
            self.assertEqual(
                adapter.build_resume_command(
                    ["claude", "-p", "--output-format", "stream-json"],
                    "claude-123",
                ),
                [
                    "claude",
                    "-p",
                    "--output-format",
                    "stream-json",
                    "--resume",
                    "claude-123",
                ],
            )
            with self.assertRaisesRegex(ValueError, "already contains"):
                adapter.build_resume_command(["claude", "-p", "--resume", "old"], "new")
            self.assertTrue(adapter.probe(["claude"]).supports_resume)
            self.assertTrue(adapter.has_native_resume_selector(["claude", "-p", "-r", "old"]))
            self.assertTrue(
                adapter.has_native_resume_selector(["claude", "-p", "-rold"])
            )
            self.assertTrue(adapter.has_native_resume_selector(["claude", "-p", "--continue"]))
            self.assertTrue(
                adapter.has_native_resume_selector(["claude", "-p", "--from-pr", "123"])
            )
            self.assertTrue(
                adapter.has_native_resume_selector(
                    ["/usr/bin/env", "claude", "-p", "-c"]
                )
            )
            self.assertFalse(
                adapter.has_native_resume_selector(
                    [sys.executable, "-c", "print('not claude')"]
                )
            )

    def test_hermes_provider_session_capture_resume_and_version_detection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            paths = session_paths(Path(tmp_name) / "session-001")
            paths.session.mkdir()
            paths.stdout.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "session.started",
                                "session_id": "hermes-123",
                            }
                        ),
                        json.dumps(
                            {
                                "type": "result",
                                "result": "FINAL",
                                "session_id": "hermes-123",
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            adapter = HermesCliPlayer()
            command = [
                "python3",
                "-m",
                "cross_agent_consensus.hermes_cli",
                "--ignore-rules",
            ]

            self.assertEqual(adapter.extract_provider_session_id(paths), "hermes-123")
            self.assertEqual(
                adapter.build_resume_command(command, "hermes-123"),
                [*command, "--resume", "hermes-123"],
            )
            self.assertEqual(adapter.profile_command_errors(command), [])
            self.assertTrue(adapter.command_requests_json(command))
            self.assertTrue(adapter.probe(command).supports_resume)
            self.assertTrue(
                adapter.probe(command).supports_session_id_rotation
            )
            detected_hermes_version.cache_clear()
            self.assertTrue(
                (detected_hermes_version(sys.executable) or "").startswith("Python")
            )

    def test_structured_player_parses_events_and_final_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            paths = session_paths(tmp / "session-001")
            paths.session.mkdir()
            inv = invocation(tmp, ["codex", "exec", "--json", "-"], "codex-cli")
            adapter = get_player_adapter("codex-cli")

            events = adapter.parse_stream_events(
                "stdout",
                (
                    json.dumps({"type": "item.started", "item": {"type": "command_execution", "status": "in_progress"}})
                    + "\n"
                    + json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "FINAL"}})
                    + "\n"
                ).encode(),
                {"stdout": ""},
                inv,
            )
            self.assertEqual([event["normalized_type"] for event in events], ["tool_call", "message"])

            paths.stdout.write_text(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "FINAL"}}) + "\n")
            adapter.extract_final_output(paths)
            self.assertEqual(paths.final_output.read_text(encoding="utf-8"), "FINAL\n")


class PlayerAliasTests(unittest.TestCase):
    def test_short_aliases_resolve_to_canonical_adapters(self) -> None:
        self.assertIsInstance(get_player_adapter("codex"), CodexCliPlayer)
        self.assertIsInstance(get_player_adapter("claude"), ClaudeCliPlayer)
        self.assertIsInstance(get_player_adapter("generic"), GenericCliPlayer)
        self.assertIsInstance(get_player_adapter("deepseek"), GenericCliPlayer)
        self.assertIsInstance(get_player_adapter("hermes"), HermesCliPlayer)

    def test_canonical_player_ids_still_resolve(self) -> None:
        self.assertIsInstance(get_player_adapter("codex-cli"), CodexCliPlayer)
        self.assertIsInstance(get_player_adapter("claude-cli"), ClaudeCliPlayer)
        self.assertIsInstance(get_player_adapter("generic-cli"), GenericCliPlayer)
        self.assertIsInstance(get_player_adapter("hermes-cli"), HermesCliPlayer)

    def test_unknown_player_error_lists_players_and_aliases(self) -> None:
        with self.assertRaises(ValueError) as exc:
            get_player_adapter("nope")
        message = str(exc.exception)
        for canonical in (
            "codex-cli",
            "claude-cli",
            "hermes-cli",
            "manual",
            "generic-cli",
            "deepseek-cli",
        ):
            self.assertIn(canonical, message)
        for alias, target in PLAYER_ALIASES.items():
            self.assertIn(alias, message)
            self.assertIn(target, message)


if __name__ == "__main__":
    unittest.main()
