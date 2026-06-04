from __future__ import annotations

import sys
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "skills" / "cross-agent-consensus"
sys.path.insert(0, str(PACKAGE_ROOT))

from cross_agent_consensus.invocation.readiness import (
    codex_trusted_dir_errors,
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


class CodexTrustedDirTests(unittest.TestCase):
    def test_missing_skip_flag_for_real_codex_binary_returns_actionable_error(self) -> None:
        messages = codex_trusted_dir_errors("codex-cli", ["codex", "exec", "--json", "-"])
        self.assertEqual(len(messages), 1)
        self.assertIn("--skip-git-repo-check", messages[0])
        # Suggestion preserves the original argv with the flag inserted after `codex exec`.
        self.assertIn("codex exec --skip-git-repo-check --json -", messages[0])

    def test_skip_flag_present_passes(self) -> None:
        self.assertEqual(
            codex_trusted_dir_errors("codex-cli", ["codex", "exec", "--skip-git-repo-check", "--json", "-"]),
            [],
        )

    def test_path_invocation_of_codex_binary_still_requires_flag(self) -> None:
        messages = codex_trusted_dir_errors("codex-cli", ["/usr/local/bin/codex", "exec", "--json", "-"])
        self.assertEqual(len(messages), 1)
        self.assertIn("--skip-git-repo-check", messages[0])

    def test_python_stub_under_codex_cli_player_is_not_checked(self) -> None:
        # The trusted-dir trap is specific to the real codex binary; wrappers /
        # test stubs that happen to be launched under the codex-cli player are
        # not subject to it and must not block on the flag.
        self.assertEqual(
            codex_trusted_dir_errors("codex-cli", [sys.executable, "-c", "import sys"]),
            [],
        )

    def test_non_codex_player_is_ignored(self) -> None:
        self.assertEqual(codex_trusted_dir_errors("claude-cli", ["claude", "-p"]), [])
        self.assertEqual(codex_trusted_dir_errors("generic-cli", ["codex", "exec"]), [])

    def test_empty_command_is_ignored(self) -> None:
        self.assertEqual(codex_trusted_dir_errors("codex-cli", []), [])


if __name__ == "__main__":
    unittest.main()
