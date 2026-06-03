from __future__ import annotations

import sys
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "skills" / "cross-agent-consensus"
sys.path.insert(0, str(PACKAGE_ROOT))

from cross_agent_consensus.invocation.readiness import (
    command_for_display,
    command_has_option_value,
    normalize_command_separator,
    player_command_telemetry_errors,
    runtime_command,
    secret_argv_errors,
)


class InvocationReadyTests(unittest.TestCase):
    def test_runtime_command_and_separator_normalization(self) -> None:
        self.assertEqual(runtime_command(["--", sys.executable, "--version"]), [sys.executable, "--version"])
        self.assertEqual(
            normalize_command_separator(["invoke-agent", "--command", "--", sys.executable, "--version"]),
            ["invoke-agent", "--command", sys.executable, "--version"],
        )
        self.assertIn(sys.executable, command_for_display([sys.executable, "--version"]))

    def test_secret_argv_detection_rejects_sensitive_values(self) -> None:
        messages = secret_argv_errors(["tool", "--api-key=abc123", "Authorization: Bearer abc123"])
        self.assertTrue(any("secret-looking" in message for message in messages))
        self.assertTrue(any("Authorization header" in message for message in messages))

    def test_provider_telemetry_policy_is_separate_from_adapters(self) -> None:
        self.assertTrue(command_has_option_value(["claude", "--output-format=stream-json"], "--output-format", "stream-json"))
        self.assertEqual(
            player_command_telemetry_errors("codex-cli", ["codex", "exec", "-"]),
            ["--player codex-cli requires --json for runtime message telemetry"],
        )
        self.assertIn(
            "--verbose",
            " ".join(player_command_telemetry_errors("claude-cli", ["claude", "-p", "--output-format=stream-json"])),
        )


if __name__ == "__main__":
    unittest.main()
