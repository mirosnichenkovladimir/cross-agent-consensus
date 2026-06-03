from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "skills" / "cross-agent-consensus"
sys.path.insert(0, str(PACKAGE_ROOT))

from cross_agent_consensus.invocation.session_paths import agent_session_paths
from cross_agent_consensus.invocation.status import agent_status_payload, missing_agent_status_payload


class Args:
    actor = "reviewer-a"
    round = "round-1"


class AgentStatusCancelTests(unittest.TestCase):
    def test_agent_status_payload_reads_state_exit_and_event_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            session = Path(tmp_name) / "session-001"
            session.mkdir()
            paths = agent_session_paths(session)
            paths.state.write_text(
                json.dumps({"schema_version": "cross-agent-consensus-state-1", "state": "completed", "pid": 123}),
                encoding="utf-8",
            )
            paths.exit.write_text(json.dumps({"final_state": "completed", "exit_code_or_null": 0}), encoding="utf-8")
            paths.events.write_text('{"type": "started"}\n{"type": "completed"}\n', encoding="utf-8")

            payload = agent_status_payload(paths, 1)
            self.assertEqual(payload["schema_version"], "cross-agent-consensus-agent-status-1")
            self.assertEqual(payload["state_schema_version"], "cross-agent-consensus-state-1")
            self.assertEqual(payload["state"], "completed")
            self.assertEqual(payload["event_tail"], [{"type": "completed"}])

    def test_missing_status_payload_matches_public_shape(self) -> None:
        payload = missing_agent_status_payload(Args(), "missing")
        self.assertEqual(payload["schema_version"], "cross-agent-consensus-agent-status-1")
        self.assertEqual(payload["state"], "missing")
        self.assertEqual(payload["round_id"], "round-001")
        self.assertEqual(payload["event_tail"], [])


if __name__ == "__main__":
    unittest.main()
