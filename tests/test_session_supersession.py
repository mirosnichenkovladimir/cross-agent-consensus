"""Tests for T5-A failed-session supersession bookkeeping."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "skills" / "cross-agent-consensus"
sys.path.insert(0, str(PACKAGE_ROOT))

from cross_agent_consensus.io import atomic_write_json
from cross_agent_consensus.invocation.session_paths import allocate_agent_session
from cross_agent_consensus.invocation.status import agent_session_state_counts
from cross_agent_consensus.invocation.telemetry import mark_state_superseded_by


def _write_state(path: Path, *, state: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        path,
        {
            "schema_version": "cross-agent-consensus-state-1",
            "state": state,
            "pid": None,
        },
    )


def _stage_run_with_rounds(tmp: Path) -> Path:
    run = tmp / "run-001"
    (run / "rounds" / "round-001" / "agents").mkdir(parents=True)
    return run


class MarkStateSupersededByTests(unittest.TestCase):
    def test_stamps_failed_state_with_by_session_and_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            state_path = Path(tmp_name) / "state.json"
            _write_state(state_path, state="failed")
            changed = mark_state_superseded_by(state_path, by_session="session-002")
            self.assertTrue(changed)
            payload = json.loads(state_path.read_text())
            self.assertEqual(payload["superseded_by"], "session-002")
            self.assertIn("superseded_at", payload)

    def test_idempotent_for_same_by_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            state_path = Path(tmp_name) / "state.json"
            _write_state(state_path, state="failed")
            self.assertTrue(mark_state_superseded_by(state_path, by_session="session-002"))
            # Second call must not modify the file.
            self.assertFalse(mark_state_superseded_by(state_path, by_session="session-002"))

    def test_skips_running_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            state_path = Path(tmp_name) / "state.json"
            _write_state(state_path, state="running")
            self.assertFalse(mark_state_superseded_by(state_path, by_session="session-002"))
            payload = json.loads(state_path.read_text())
            self.assertNotIn("superseded_by", payload)

    def test_missing_file_returns_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            self.assertFalse(
                mark_state_superseded_by(Path(tmp_name) / "missing.json", by_session="session-002")
            )


class AllocateAgentSessionSupersessionTests(unittest.TestCase):
    def test_new_session_stamps_prior_failed_attempts_in_same_actor_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = _stage_run_with_rounds(Path(tmp_name))
            first = allocate_agent_session(run, "round-001", "codex")
            _write_state(first.state, state="failed")

            second = allocate_agent_session(run, "round-001", "codex")
            self.assertEqual(first.session.name, "session-001")
            self.assertEqual(second.session.name, "session-002")
            payload = json.loads(first.state.read_text())
            self.assertEqual(payload["superseded_by"], "session-002")

    def test_does_not_supersede_sessions_in_other_actor_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = _stage_run_with_rounds(Path(tmp_name))
            codex_first = allocate_agent_session(run, "round-001", "codex")
            _write_state(codex_first.state, state="failed")
            allocate_agent_session(run, "round-001", "claude")
            payload = json.loads(codex_first.state.read_text())
            self.assertNotIn("superseded_by", payload)


class AgentSessionStateCountsTests(unittest.TestCase):
    def test_superseded_sessions_bucket_separately(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = _stage_run_with_rounds(Path(tmp_name))
            first = allocate_agent_session(run, "round-001", "codex")
            _write_state(first.state, state="failed")
            second = allocate_agent_session(run, "round-001", "codex")
            _write_state(second.state, state="completed")
            counts = agent_session_state_counts(run)
            self.assertEqual(counts.get("superseded"), 1)
            self.assertEqual(counts.get("completed"), 1)
            # Failed must not be inflated by the recovered first attempt.
            self.assertNotIn("failed", counts)


if __name__ == "__main__":
    unittest.main()
