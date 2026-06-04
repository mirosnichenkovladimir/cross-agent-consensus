from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "skills" / "cross-agent-consensus"
sys.path.insert(0, str(PACKAGE_ROOT))

from cross_agent_consensus.config import (
    CONFIG_SCHEMA_VERSION,
    canonical_config,
    resolve_config,
    validate_config_shape,
)


class ConfigTests(unittest.TestCase):
    def test_canonical_config_maps_legacy_top_level_defaults(self) -> None:
        data = canonical_config({"profile": "document-consensus", "run_root": "runs/custom"})

        self.assertEqual(
            data,
            {
                "defaults": {
                    "profile": "document-consensus",
                    "run_root": "runs/custom",
                }
            },
        )

    def test_persistent_config_rejects_unattended_invocation(self) -> None:
        _, errors = validate_config_shape(
            {
                "schema_version": CONFIG_SCHEMA_VERSION,
                "invocation": {
                    "unattended_invocation": {
                        "enabled": True,
                        "scope": ["reviewer"],
                    }
                },
            },
            source="project",
            persistent=True,
            strict=True,
        )

        self.assertIn("project: persistent config must not enable unattended_invocation", errors)

    def test_invocation_peek_config_accepts_valid_shape(self) -> None:
        warnings, errors = validate_config_shape(
            {
                "schema_version": CONFIG_SCHEMA_VERSION,
                "invocation": {
                    "peek": {
                        "interval_seconds": 180,
                        "tail": 80,
                        "snippet_chars": 160,
                        "monitor_stale_seconds": 30,
                    }
                },
            },
            source="project",
            persistent=True,
            strict=True,
        )

        self.assertEqual(warnings, [])
        self.assertEqual(errors, [])

    def test_invocation_peek_config_rejects_unknown_and_invalid_values(self) -> None:
        _, errors = validate_config_shape(
            {
                "schema_version": CONFIG_SCHEMA_VERSION,
                "invocation": {
                    "peek": {
                        "interval_seconds": 0,
                        "tail": 0,
                        "snippet_chars": 10,
                        "monitor_stale_seconds": -1,
                        "raw": True,
                    }
                },
            },
            source="project",
            persistent=True,
            strict=True,
        )

        self.assertIn("project: unknown invocation.peek keys: raw", errors)
        self.assertIn("project: invocation.peek.interval_seconds must be a number > 0", errors)
        self.assertIn("project: invocation.peek.tail must be an integer between 1 and 1000", errors)
        self.assertIn("project: invocation.peek.snippet_chars must be an integer between 40 and 500", errors)
        self.assertIn("project: invocation.peek.monitor_stale_seconds must be a number > 0", errors)

    def test_feedback_config_accepts_enabled_boolean(self) -> None:
        warnings, errors = validate_config_shape(
            {
                "schema_version": CONFIG_SCHEMA_VERSION,
                "feedback": {"enabled": True},
            },
            source="project",
            persistent=True,
            strict=True,
        )

        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_feedback_config_rejects_non_boolean_enabled(self) -> None:
        _, errors = validate_config_shape(
            {
                "schema_version": CONFIG_SCHEMA_VERSION,
                "feedback": {"enabled": "yes"},
            },
            source="project",
            persistent=True,
            strict=True,
        )

        self.assertIn("project: feedback.enabled must be a boolean", errors)

    def test_feedback_config_rejects_unknown_keys_in_strict_mode(self) -> None:
        _, errors = validate_config_shape(
            {
                "schema_version": CONFIG_SCHEMA_VERSION,
                "feedback": {"enabled": True, "destination": "/tmp/x"},
            },
            source="project",
            persistent=True,
            strict=True,
        )

        self.assertIn("project: unknown feedback keys: destination", errors)

    def test_cli_config_overrides_installed_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            resolution, task_data = resolve_config(
                cwd=Path(tmp_name),
                no_config=True,
                cli_config={"defaults": {"profile": "implementation-test"}},
                strict=True,
            )

        self.assertEqual(task_data, {})
        self.assertEqual(resolution.effective["defaults"]["profile"], "implementation-test")
        self.assertEqual(resolution.provenance["defaults.profile"], "cli")


if __name__ == "__main__":
    unittest.main()
