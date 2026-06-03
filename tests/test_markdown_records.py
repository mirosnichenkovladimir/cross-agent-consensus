from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "skills" / "cross-agent-consensus"
sys.path.insert(0, str(PACKAGE_ROOT))

from cross_agent_consensus.markdown_records import frontmatter, parse_records_from_file, parse_yaml_subset
from cross_agent_consensus.records import is_protocol_payload_path


class MarkdownRecordTests(unittest.TestCase):
    def test_yaml_subset_parses_scalars_lists_and_mappings(self) -> None:
        data = parse_yaml_subset(
            "\n".join(
                [
                    "name: reviewer-codex",
                    "enabled: true",
                    "attempts: 2",
                    "items:",
                    "  - first",
                    "  - second",
                    "nested:",
                    "  value: null",
                ]
            )
        )

        self.assertEqual(
            data,
            {
                "name": "reviewer-codex",
                "enabled": True,
                "attempts": 2,
                "items": ["first", "second"],
                "nested": {"value": None},
            },
        )

    def test_record_parser_uses_frontmatter_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            path = Path(tmp_name) / "run.md"
            path.write_text(
                "\n".join(
                    [
                        "# Run",
                        "",
                        "## TaskBrief heading-id",
                        frontmatter(
                            {
                                "record_type": "TaskBrief",
                                "schema_version": "m2-markdown-1",
                                "run_id": "sample",
                                "actor_identity": "orchestrator",
                                "created_at": "2026-06-01T00:00:00Z",
                                "task_brief_id": "task-from-frontmatter",
                                "artifact_locator": "README.md",
                                "objective": "test",
                                "success_criteria": ["pass"],
                                "profile": "document-consensus",
                                "human_supervisor_identity_or_null": None,
                            }
                        ),
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            records = parse_records_from_file(path)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].record_type, "TaskBrief")
        self.assertEqual(records[0].record_id, "task-from-frontmatter")

    def test_record_parser_keeps_human_decision_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            path = Path(tmp_name) / "escalations.md"
            path.write_text(
                "\n".join(
                    [
                        "# Escalations",
                        "",
                        "## HumanDecision human-decision-001",
                        frontmatter(
                            {
                                "record_type": "HumanDecision",
                                "schema_version": "m2-markdown-1",
                                "run_id": "sample",
                                "actor_identity": "human",
                                "created_at": "2026-06-02T00:00:00Z",
                                "human_decision_id": "human-decision-001",
                                "affected_finding_ids_or_validator_ids": ["CXR-001"],
                                "decision_type": "terminate_escalated_to_human",
                                "rationale": "Requires human scope decision.",
                                "binding_authority": "human",
                                "requires_new_artifact_version": False,
                            }
                        ),
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            records = parse_records_from_file(path)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].record_type, "HumanDecision")
        self.assertEqual(records[0].record_id, "human-decision-001")

    def test_prompt_and_raw_paths_are_protocol_payloads(self) -> None:
        self.assertTrue(
            is_protocol_payload_path(Path("runs/example/rounds/round-001/prompts/reviewers/reviewer.md"))
        )
        self.assertTrue(
            is_protocol_payload_path(Path("runs/example/rounds/round-001/raw/reviewers/reviewer.out"))
        )
        self.assertFalse(is_protocol_payload_path(Path("runs/example/rounds/round-001/reviews/reviewer.md")))


if __name__ == "__main__":
    unittest.main()
