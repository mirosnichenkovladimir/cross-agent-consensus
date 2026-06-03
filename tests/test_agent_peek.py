from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "skills" / "cross-agent-consensus"
sys.path.insert(0, str(PACKAGE_ROOT))

from cross_agent_consensus.invocation.peek import (
    Activity,
    PeekSettings,
    agent_peek_snapshot,
    clamp_snippet,
    cmd_agent_peek,
    combined_activity,
    derive_activity_phrases,
    format_peek_snapshot,
    terminal_state,
)
from cross_agent_consensus.invocation.session_paths import agent_session_paths


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class AgentPeekTests(unittest.TestCase):
    def make_session(self, tmp: Path, *, heartbeat: str = "2026-06-03T12:00:03Z") -> Path:
        session = tmp / "run" / "rounds" / "round-001" / "agents" / "reviewer-a" / "session-001"
        session.mkdir(parents=True)
        paths = agent_session_paths(session)
        paths.state.write_text(
            json.dumps(
                {
                    "schema_version": "cross-agent-consensus-state-1",
                    "state": "running",
                    "last_agent_activity_at": "2026-06-03T12:00:02Z",
                    "last_monitor_heartbeat_at": heartbeat,
                    "idle_seconds": 1,
                }
            ),
            encoding="utf-8",
        )
        paths.events.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "ts": "2026-06-03T12:00:01Z",
                            "type": "agent_event",
                            "normalized_type": "tool_result",
                            "native_event": {
                                "item": {
                                    "type": "command_execution",
                                    "command": "/bin/zsh -lc \"sed -n '1,80p' validation.py\"",
                                }
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "ts": "2026-06-03T12:00:02Z",
                            "type": "agent_event",
                            "normalized_type": "message",
                            "native_event": {
                                "item": {
                                    "type": "agent_message",
                                    "text": "writing concern about config schema token=supersecretvalue1234567890",
                                }
                            },
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        paths.agent_log.write_text("", encoding="utf-8")
        paths.stdout.write_text("stdout", encoding="utf-8")
        paths.stderr.write_text("", encoding="utf-8")
        return session

    def test_snapshot_includes_content_peek_and_redacts_obvious_secret(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            session = self.make_session(Path(tmp_name))
            paths = agent_session_paths(session)
            settings = PeekSettings(interval_seconds=180, tail=20, snippet_chars=120, monitor_stale_seconds=30)

            snapshot = agent_peek_snapshot(
                paths,
                "reviewer-a",
                settings,
                now=dt.datetime(2026, 6, 3, 12, 0, 4, tzinfo=dt.timezone.utc),
            )
            line = format_peek_snapshot(snapshot)

        self.assertIn("reviewer-a running", line)
        self.assertIn("did: inspected validation.py", line)
        self.assertIn("now: writing concern about config schema", line)
        self.assertIn("token=<redacted>", line)
        self.assertNotIn("supersecretvalue", line)

    def test_snapshot_reports_monitor_stale_from_heartbeat_age(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            session = self.make_session(Path(tmp_name), heartbeat="2026-06-03T12:00:00Z")
            paths = agent_session_paths(session)
            settings = PeekSettings(interval_seconds=180, tail=20, snippet_chars=120, monitor_stale_seconds=3)

            snapshot = agent_peek_snapshot(
                paths,
                "reviewer-a",
                settings,
                now=dt.datetime(2026, 6, 3, 12, 0, 10, tzinfo=dt.timezone.utc),
            )

        self.assertEqual(snapshot["derived_state"], "monitor_stale")
        self.assertFalse(snapshot["terminal"])
        self.assertIn("monitor heartbeat stale", str(snapshot["now"]))

    def test_cmd_agent_peek_missing_session_returns_two(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            args = SimpleNamespace(
                run=str(Path(tmp_name) / "run"),
                actor="reviewer-a",
                round="1",
                session=None,
                tail=None,
                snippet_chars=None,
                monitor_stale_seconds=None,
                follow=False,
                interval_seconds=None,
                config=None,
                no_config=True,
                cwd=None,
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = cmd_agent_peek(args)

        self.assertEqual(result, 2)
        self.assertIn("No monitored agent session exists", stderr.getvalue())

    def test_cmd_agent_peek_does_not_mutate_session_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            session = self.make_session(tmp, heartbeat=dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"))
            paths = agent_session_paths(session)
            watched = [paths.state, paths.events, paths.agent_log, paths.stdout, paths.stderr]
            before = {path: sha256(path) for path in watched}
            args = SimpleNamespace(
                run=str(tmp / "run"),
                actor="reviewer-a",
                round="1",
                session=None,
                tail=20,
                snippet_chars=120,
                monitor_stale_seconds=30,
                follow=False,
                interval_seconds=None,
                config=None,
                no_config=True,
                cwd=None,
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = cmd_agent_peek(args)
            after = {path: sha256(path) for path in watched}

        self.assertEqual(result, 0)
        self.assertIn("did: inspected validation.py", stdout.getvalue())
        self.assertEqual(before, after)


class ClampSnippetTests(unittest.TestCase):
    def test_strips_ansi_escape_sequences(self) -> None:
        text = "\x1b[31mERROR\x1b[0m something failed"
        self.assertEqual(clamp_snippet(text, 120), "ERROR something failed")

    def test_strips_non_whitespace_control_chars(self) -> None:
        text = "before\x00\x07\x1f after"
        self.assertEqual(clamp_snippet(text, 120), "before after")

    def test_redacts_double_quoted_secret_assignment(self) -> None:
        text = 'config password="my secret value" loaded'
        result = clamp_snippet(text, 120)
        self.assertIn("password=<redacted>", result)
        self.assertNotIn("my secret value", result)

    def test_redacts_single_quoted_secret_assignment(self) -> None:
        text = "api_key='abc123def456' set"
        result = clamp_snippet(text, 120)
        self.assertIn("api_key=<redacted>", result)
        self.assertNotIn("abc123def456", result)

    def test_redacts_bearer_token_inside_authorization_header(self) -> None:
        text = "Authorization: Bearer abc123XYZdef456token789value"
        result = clamp_snippet(text, 200)
        self.assertNotIn("abc123XYZdef456token789value", result)
        # value is collapsed; key marker survives
        self.assertIn("authorization", result.lower())

    def test_redacts_standalone_bearer_token(self) -> None:
        text = "Use Bearer abc123def456ghi789jkl012mno345 for auth"
        result = clamp_snippet(text, 200)
        self.assertIn("Bearer <redacted>", result)
        self.assertNotIn("abc123def456ghi789jkl012mno345", result)

    def test_preserves_realistic_file_paths(self) -> None:
        text = "inspected /home/user/very/long/path/to/some/module.py file"
        result = clamp_snippet(text, 200)
        self.assertIn("/home/user/very/long/path/to/some/module.py", result)

    def test_preserves_dotted_module_names(self) -> None:
        text = "loaded cross_agent_consensus.invocation.peek.combined_activity helper"
        result = clamp_snippet(text, 200)
        self.assertIn("cross_agent_consensus.invocation.peek.combined_activity", result)

    def test_long_alphabetic_tokens_without_digits_are_not_redacted(self) -> None:
        text = "the very long all letters word aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa is fine"
        result = clamp_snippet(text, 200)
        self.assertIn("a" * 34, result)

    def test_long_mixed_alphanumeric_token_is_redacted(self) -> None:
        text = "trailing blob abcdef0123456789ABCDEF0123456789xyz appended"
        result = clamp_snippet(text, 200)
        self.assertIn("<redacted>", result)
        self.assertNotIn("abcdef0123456789ABCDEF0123456789xyz", result)


class TerminalStateTests(unittest.TestCase):
    def test_no_exit_file_and_non_terminal_state_returns_none(self) -> None:
        self.assertIsNone(terminal_state("running", None, exit_exists=False))

    def test_no_exit_file_with_terminal_persisted_state_returns_state(self) -> None:
        self.assertEqual(terminal_state("completed", None, exit_exists=False), "completed")

    def test_exit_file_present_but_empty_payload_is_terminal(self) -> None:
        # Empty/malformed exit.json must still be treated as terminal so the
        # peek loop does not spin on a session that has already exited.
        self.assertEqual(terminal_state("running", None, exit_exists=True), "completed")

    def test_exit_file_present_with_truthy_persisted_terminal_state(self) -> None:
        self.assertEqual(terminal_state("failed", {}, exit_exists=True), "failed")

    def test_exit_file_present_uses_final_state_when_available(self) -> None:
        self.assertEqual(
            terminal_state("running", {"final_state": "cancelled"}, exit_exists=True),
            "cancelled",
        )


class DeriveActivityPhrasesTests(unittest.TestCase):
    def test_returns_default_when_no_did_precedes_now(self) -> None:
        ts = dt.datetime(2026, 6, 3, 12, 0, 0, tzinfo=dt.timezone.utc)
        activities = [
            Activity(0, ts, None, "using Bash"),  # tool_call, no did
            Activity(1, ts + dt.timedelta(seconds=1), "ran something later", None),
        ]
        did, now = derive_activity_phrases(activities)
        # now_phrase is "using Bash" at index 0; no did before it -> default phrase
        self.assertEqual(now, "using Bash")
        self.assertEqual(did, "activity observed")

    def test_does_not_pick_did_phrase_after_now(self) -> None:
        ts = dt.datetime(2026, 6, 3, 12, 0, 0, tzinfo=dt.timezone.utc)
        activities = [
            Activity(0, ts, None, "waiting for input"),
            Activity(1, ts + dt.timedelta(seconds=1), "ran tool", None),
        ]
        did, now = derive_activity_phrases(activities)
        # The trailing did at index 1 must NOT be paired with the now at index 0.
        self.assertEqual(now, "waiting for input")
        self.assertEqual(did, "activity observed")

    def test_picks_did_before_now(self) -> None:
        ts = dt.datetime(2026, 6, 3, 12, 0, 0, tzinfo=dt.timezone.utc)
        activities = [
            Activity(0, ts, "ran sed", None),
            Activity(1, ts + dt.timedelta(seconds=1), None, "using Bash"),
        ]
        did, now = derive_activity_phrases(activities)
        self.assertEqual(did, "ran sed")
        self.assertEqual(now, "using Bash")


class CombinedActivityTests(unittest.TestCase):
    def test_drops_entries_without_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            session = Path(tmp_name) / "session"
            session.mkdir()
            paths = agent_session_paths(session)
            paths.state.write_text("{}", encoding="utf-8")
            paths.events.write_text(
                "\n".join(
                    [
                        # no ts field -> should be dropped
                        json.dumps(
                            {
                                "type": "agent_event",
                                "normalized_type": "tool_call",
                                "native_event": {"name": "Bash"},
                            }
                        ),
                        # valid ts -> kept
                        json.dumps(
                            {
                                "ts": "2026-06-03T12:00:01Z",
                                "type": "agent_event",
                                "normalized_type": "tool_call",
                                "native_event": {"name": "Bash"},
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            paths.agent_log.write_text("", encoding="utf-8")
            paths.stdout.write_text("", encoding="utf-8")
            paths.stderr.write_text("", encoding="utf-8")
            settings = PeekSettings(interval_seconds=180, tail=20, snippet_chars=120, monitor_stale_seconds=30)
            activities = combined_activity(paths, settings)

        self.assertEqual(len(activities), 1)
        self.assertIsNotNone(activities[0].ts)

    def test_events_jsonl_sorts_before_agent_log_at_equal_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            session = Path(tmp_name) / "session"
            session.mkdir()
            paths = agent_session_paths(session)
            paths.state.write_text("{}", encoding="utf-8")
            same_ts = "2026-06-03T12:00:01Z"
            paths.events.write_text(
                json.dumps(
                    {
                        "ts": same_ts,
                        "type": "agent_event",
                        "normalized_type": "tool_result",
                        "native_event": {"item": {"command": "sed -n '1,80p' a.py"}},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            paths.agent_log.write_text(
                json.dumps(
                    {
                        "ts": same_ts,
                        "type": "agent_event",
                        "normalized_type": "tool_call",
                        "native_event": {"name": "Bash"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            paths.stdout.write_text("", encoding="utf-8")
            paths.stderr.write_text("", encoding="utf-8")
            settings = PeekSettings(interval_seconds=180, tail=20, snippet_chars=120, monitor_stale_seconds=30)
            activities = combined_activity(paths, settings)

        # events.jsonl first, agent.log second; stable across runs.
        self.assertEqual(len(activities), 2)
        self.assertEqual(activities[0].did_phrase, "inspected a.py")
        self.assertEqual(activities[1].now_phrase, "using Bash")


class AgentPeekTerminalIntegrationTests(unittest.TestCase):
    def test_empty_exit_file_is_treated_as_terminal_in_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            session = Path(tmp_name) / "session"
            session.mkdir()
            paths = agent_session_paths(session)
            paths.state.write_text(
                json.dumps({"state": "running", "last_monitor_heartbeat_at": "2026-06-03T12:00:00Z"}),
                encoding="utf-8",
            )
            paths.events.write_text("", encoding="utf-8")
            paths.agent_log.write_text("", encoding="utf-8")
            paths.stdout.write_text("", encoding="utf-8")
            paths.stderr.write_text("", encoding="utf-8")
            # Empty exit.json — payload parses to {} (falsy) but file exists.
            paths.exit.write_text("{}", encoding="utf-8")
            settings = PeekSettings(interval_seconds=180, tail=20, snippet_chars=120, monitor_stale_seconds=30)

            snapshot = agent_peek_snapshot(
                paths,
                "reviewer-a",
                settings,
                now=dt.datetime(2026, 6, 3, 12, 0, 4, tzinfo=dt.timezone.utc),
            )

        self.assertTrue(snapshot["terminal"])
        self.assertEqual(snapshot["derived_state"], "completed")


if __name__ == "__main__":
    unittest.main()
