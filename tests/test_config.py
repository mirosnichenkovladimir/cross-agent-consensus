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
