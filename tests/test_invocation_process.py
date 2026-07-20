from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "skills" / "cross-agent-consensus"
sys.path.insert(0, str(PACKAGE_ROOT))

from cross_agent_consensus.invocation.process_monitor import (
    current_process_identity,
    prepare_agent_session,
    process_exists,
    process_identity_matches,
    run_generic_agent,
)
from cross_agent_consensus.models import InvocationCommandInput, RateLimitCircuitBreaker


FAKE_PROVIDER = Path(__file__).parent / "fixtures" / "fake_provider.py"


def prepared_invocation(
    root: Path,
    mode: str,
    *,
    player: str = "generic-cli",
    max_runtime_seconds: float | None = None,
    rate_limit_circuit_breaker: RateLimitCircuitBreaker | None = None,
):
    run = root / "run"
    run.mkdir()
    prompt = root / "prompt.md"
    prompt.write_text("review", encoding="utf-8")
    command = [
        sys.executable,
        str(FAKE_PROVIDER),
        mode,
        "--delay-seconds",
        "2",
    ]
    args = InvocationCommandInput(
        run=str(run),
        round="round-1",
        phase="reviewer",
        actor="reviewer",
        player=player,
        participant_profile_id="reviewer-profile",
        execution_profile_id="reviewer-execution",
        prompt=str(prompt),
        raw_output=str(root / "reviewer.out"),
        approved=True,
        command=command,
        cwd=str(root),
        idle_timeout_seconds=2,
        stale_timeout_seconds=4,
        heartbeat_interval_seconds=0.02,
        max_runtime_seconds=max_runtime_seconds,
        rate_limit_circuit_breaker=rate_limit_circuit_breaker,
    )
    return prepare_agent_session(args, command)


class InvocationProcessTests(unittest.TestCase):
    def test_max_runtime_stops_provider_that_continues_emitting_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            invocation, paths, command = prepared_invocation(
                Path(tmp_name), "active_delay", max_runtime_seconds=0.12
            )

            rc = run_generic_agent(invocation, paths, command)
            state = json.loads(paths.state.read_text(encoding="utf-8"))
            events = paths.events.read_text(encoding="utf-8")

        self.assertEqual(rc, 4)
        self.assertEqual(state["state"], "timed_out")
        self.assertEqual(state["failure_reason_or_null"], "max_runtime_exceeded")
        self.assertIn('"type": "max_runtime_exceeded"', events)

    def test_kimi_http_429_circuit_stops_after_three_consecutive_retries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            invocation, paths, command = prepared_invocation(
                Path(tmp_name),
                "kimi_429_loop",
                player="kimi-cli",
                rate_limit_circuit_breaker=RateLimitCircuitBreaker(3, 120),
            )

            rc = run_generic_agent(invocation, paths, command)
            state = json.loads(paths.state.read_text(encoding="utf-8"))
            events = paths.events.read_text(encoding="utf-8")

        self.assertEqual(rc, 4)
        self.assertEqual(state["state"], "failed")
        self.assertEqual(state["failure_reason_or_null"], "provider_rate_limited")
        self.assertEqual(events.count('"type": "provider_rate_limit_retry"'), 3)
        self.assertIn('"type": "provider_rate_limit_circuit_opened"', events)

    def test_process_identity_matches_live_process_and_not_wrong_identity(self) -> None:
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
        try:
            deadline = time.monotonic() + 2
            identity = None
            while time.monotonic() < deadline:
                identity = current_process_identity(child.pid)
                if identity is not None:
                    break
                time.sleep(0.05)
            self.assertIsNotNone(identity)
            self.assertTrue(process_exists(child.pid))
            self.assertTrue(process_identity_matches(child.pid, identity))
            wrong_identity = dict(identity or {})
            wrong_identity["pid"] = child.pid + 1
            self.assertFalse(process_identity_matches(child.pid, wrong_identity))
        finally:
            child.terminate()
            child.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
