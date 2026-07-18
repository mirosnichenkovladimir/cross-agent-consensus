from __future__ import annotations

import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "skills" / "cross-agent-consensus"
sys.path.insert(0, str(PACKAGE_ROOT))

from cross_agent_consensus.config import (
    CONFIG_SCHEMA_VERSION,
    canonical_config,
    default_user_config_candidates,
    load_yaml_mapping,
    resolve_config,
    validate_config_shape,
)
from cross_agent_consensus.profiles import parse_profile_definitions, resolved_profile_payload


class ConfigTests(unittest.TestCase):
    def resolved_defaults(self) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as tmp_name:
            resolution, _ = resolve_config(
                cwd=Path(tmp_name),
                no_config=True,
                strict=True,
            )
        self.assertEqual(resolution.errors, [])
        return resolution.effective

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

    def test_user_config_candidates_include_kimi_code_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            kimi_code_home = Path(tmp_name) / "kimi-code-home"
            with patch.dict(os.environ, {"KIMI_CODE_HOME": str(kimi_code_home)}):
                candidates = default_user_config_candidates()

        self.assertIn(
            kimi_code_home
            / "skills"
            / "cross-agent-consensus"
            / "config"
            / "config.local.yaml",
            candidates,
        )

    def test_defaults_parse_typed_participant_and_execution_profiles(self) -> None:
        participant_profiles, execution_profiles, identities, errors = parse_profile_definitions(
            self.resolved_defaults()
        )

        self.assertEqual(errors, [])
        self.assertEqual(participant_profiles["reviewer-default"].role, "reviewer")
        self.assertEqual(identities["codex"].participant_profile_id, "reviewer-default")
        self.assertEqual(identities["codex"].execution_profile_id, "codex-reviewer-default")
        self.assertEqual(execution_profiles["codex-reviewer-default"].adapter_id, "codex-cli")
        self.assertEqual(
            execution_profiles["codex-reviewer-default"].command,
            ["codex", "exec", "--skip-git-repo-check", "--json", "-"],
        )
        self.assertEqual(identities["kimi"].participant_profile_id, "reviewer-default")
        self.assertEqual(
            identities["kimi"].execution_profile_id, "kimi-reviewer-default"
        )
        self.assertEqual(
            execution_profiles["kimi-reviewer-default"].adapter_id, "kimi-cli"
        )
        self.assertEqual(
            execution_profiles["kimi-reviewer-default"].command,
            ["python3", "-m", "cross_agent_consensus.kimi_cli"],
        )

    def test_kimi_execution_profile_translates_model_alias(self) -> None:
        effective = self.resolved_defaults()
        execution_profiles = effective["execution_profiles"]
        assert isinstance(execution_profiles, dict)
        kimi_profile = execution_profiles["kimi-reviewer-default"]
        assert isinstance(kimi_profile, dict)
        kimi_profile["model"] = "kimi-code/k3"

        _, parsed_execution_profiles, _, errors = parse_profile_definitions(effective)

        self.assertEqual(errors, [])
        self.assertEqual(
            parsed_execution_profiles["kimi-reviewer-default"].command,
            [
                "python3",
                "-m",
                "cross_agent_consensus.kimi_cli",
                "--model",
                "kimi-code/k3",
            ],
        )

    def test_project_layer_can_switch_execution_profile_without_renaming_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            project = Path(tmp_name)
            config = project / ".cross-agent-consensus.yaml"
            config.write_text(
                "\n".join(
                    [
                        "schema_version: cross-agent-consensus-config-2",
                        "execution_profiles:",
                        "  codex-reviewer-economy:",
                        "    adapter: codex-cli",
                        "    command:",
                        "      - codex",
                        "      - exec",
                        "      - --skip-git-repo-check",
                        "      - --json",
                        "      - -",
                        "    model: gpt-5-mini",
                        "    reasoning_effort: low",
                        "    prompt_transport: stdin",
                        "    output_mode: stream_json",
                        "    supports_resume: false",
                        "    env: []",
                        "participant_identities:",
                        "  codex:",
                        "    execution_profile_id: codex-reviewer-economy",
                    ]
                ),
                encoding="utf-8",
            )

            resolution, _ = resolve_config(cwd=project, strict=True)

        self.assertEqual(resolution.errors, [])
        binding = resolution.effective["participant_identities"]["codex"]
        self.assertEqual(binding["participant_profile_id"], "reviewer-default")
        self.assertEqual(binding["execution_profile_id"], "codex-reviewer-economy")
        self.assertEqual(
            resolution.effective["execution_profiles"]["codex-reviewer-economy"]["model"],
            "gpt-5-mini",
        )
        _, resolved_execution_profiles, errors = resolved_profile_payload(resolution.effective)
        self.assertEqual(errors, [])
        self.assertEqual(
            resolved_execution_profiles["codex-reviewer-economy"]["command"],
            [
                "codex",
                "exec",
                "--skip-git-repo-check",
                "--json",
                "--model",
                "gpt-5-mini",
                "--config",
                'model_reasoning_effort="low"',
                "-",
            ],
        )
        self.assertEqual(resolution.provenance["participant_identities.codex.execution_profile_id"], "project")

    def test_v1_reviewer_cli_is_rejected_after_0_12(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            project = Path(tmp_name)
            config = project / ".cross-agent-consensus.yaml"
            config.write_text(
                "\n".join(
                    [
                        "schema_version: cross-agent-consensus-config-1",
                        "reviewer_clis:",
                        "  codex:",
                        "    command:",
                        "      - codex",
                        "      - exec",
                        "      - --skip-git-repo-check",
                        "      - --json",
                        "      - -",
                    ]
                ),
                encoding="utf-8",
            )

            resolution, _ = resolve_config(cwd=project, strict=True)

        self.assertTrue(any("schema_version must be cross-agent-consensus-config-2" in error for error in resolution.errors))
        self.assertTrue(any("reviewer_clis was removed in 0.13.0" in error for error in resolution.errors))
        self.assertNotIn("legacy-codex-execution", resolution.effective["execution_profiles"])

    def test_duplicate_yaml_identifier_is_rejected_before_mapping_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            config = Path(tmp_name) / "duplicate.yaml"
            config.write_text(
                "\n".join(
                    [
                        "schema_version: cross-agent-consensus-config-2",
                        "execution_profiles:",
                        "  codex-reviewer-default:",
                        "    adapter: codex-cli",
                        "  codex-reviewer-default:",
                        "    adapter: generic-cli",
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "execution_profiles.codex-reviewer-default"):
                load_yaml_mapping(config)

    def test_selected_identity_requires_a_binding(self) -> None:
        effective = copy.deepcopy(self.resolved_defaults())
        del effective["participant_identities"]["codex"]

        _, _, _, errors = parse_profile_definitions(effective)

        self.assertIn("participants selects 'codex' but participant_identities has no binding", errors)

    def test_execution_profile_rejects_unknown_adapter_invalid_argv_resume_and_secret(self) -> None:
        mutations = [
            (
                {"adapter": "unknown-cli"},
                "unknown player: 'unknown-cli'",
            ),
            (
                {"command": [""]},
                "command entries must be non-empty strings without NUL bytes",
            ),
            (
                {"supports_resume": True, "adapter": "generic-cli", "output_mode": "raw_stdout"},
                "supports_resume is true but adapter generic-cli cannot resume",
            ),
            (
                {"command": ["codex", "exec", "resume", "foreign-thread", "-"]},
                "command must be fresh argv; provider-native resume selectors",
            ),
            (
                {"command": ["codex", "--api-key=abc123"]},
                "passes a secret-looking value in --api-key",
            ),
            (
                {"command": ["env", "OPENAI_API_KEY=x", "/usr/bin/true"], "adapter": "generic-cli", "output_mode": "raw_stdout"},
                "secret-looking value in environment assignment OPENAI_API_KEY",
            ),
        ]
        for mutation, expected in mutations:
            with self.subTest(expected=expected):
                effective = copy.deepcopy(self.resolved_defaults())
                effective["execution_profiles"]["codex-reviewer-default"].update(mutation)

                _, _, _, errors = parse_profile_definitions(effective)

                self.assertTrue(any(expected in error for error in errors), errors)

    def test_execution_profile_rejects_output_mode_that_contradicts_command(self) -> None:
        effective = copy.deepcopy(self.resolved_defaults())
        effective["execution_profiles"]["codex-reviewer-default"]["command"].remove("--json")

        _, _, _, errors = parse_profile_definitions(effective)

        self.assertTrue(any("contradicts command output mode 'raw_stdout'" in error for error in errors), errors)

    def test_codex_declarative_provider_settings_reject_config_argv_duplicates(self) -> None:
        mutations = [
            (
                {"model": "gpt-5-mini", "command": ["codex", "exec", "--json", "-c", 'model="gpt-5"', "-"]},
                "model must be declared either in model or command",
            ),
            (
                {
                    "reasoning_effort": "low",
                    "command": ["codex", "exec", "--json", '--config=model_reasoning_effort="high"', "-"],
                },
                "reasoning_effort must be declared either in reasoning_effort or command",
            ),
        ]
        for mutation, expected in mutations:
            with self.subTest(expected=expected):
                effective = copy.deepcopy(self.resolved_defaults())
                effective["execution_profiles"]["codex-reviewer-default"].update(mutation)

                _, _, _, errors = parse_profile_definitions(effective)

                self.assertTrue(any(expected in error for error in errors), errors)

    def test_resolved_identity_includes_participant_profile_instructions(self) -> None:
        effective = copy.deepcopy(self.resolved_defaults())

        resolved_identities, _, errors = resolved_profile_payload(effective)

        self.assertEqual(errors, [])
        self.assertEqual(
            resolved_identities["codex"]["instructions"],
            ["review the declared artifact and emit evidence-backed findings"],
        )

    def test_participant_profile_role_must_match_selected_role(self) -> None:
        effective = copy.deepcopy(self.resolved_defaults())
        effective["participant_profiles"]["reviewer-default"]["role"] = "author"

        _, _, _, errors = parse_profile_definitions(effective)

        self.assertTrue(
            any("is selected as reviewer" in error and "declares role 'author'" in error for error in errors),
            errors,
        )


if __name__ == "__main__":
    unittest.main()
