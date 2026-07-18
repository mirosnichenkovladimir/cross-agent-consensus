from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "skills" / "cross-agent-consensus"
sys.path.insert(0, str(PACKAGE_ROOT))

from cross_agent_consensus.markdown_records import (
    frontmatter,
    parse_records_from_file,
    parse_records_with_diagnostics,
    parse_yaml_subset,
    render_yaml,
)
from cross_agent_consensus.records import (
    FindingSchemaError,
    is_protocol_payload_path,
    parse_run_records,
    parse_run_snapshot,
)
from cross_agent_consensus.validation import check_records


class MarkdownRecordTests(unittest.TestCase):
    @staticmethod
    def _finding_data(
        *,
        schema_version: str,
        record_type: str,
        identifier_field: str,
        identifier: str,
    ) -> dict[str, object]:
        return {
            "record_type": record_type,
            "schema_version": schema_version,
            "run_id": "sample",
            "actor_identity": "orchestrator",
            "created_at": "2026-06-01T00:00:00Z",
            identifier_field: identifier,
            "target_artifact_version_id": "v1",
            "source_raw_finding_ids": ["rf-001"],
            "normalization_record_id": "normalization-001",
            "materiality": "material",
            "materiality_status": "undisputed",
            "scope_classification": "in_scope",
            "blocking_status": "blocking",
            "lifecycle_state": "open",
            "claim": "missing permission check",
            "rationale_or_summary": "the endpoint lacks authorization",
            "clarification_pending": False,
        }

    def test_historical_finding_decodes_to_current_model_and_preserves_identifier(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            path = Path(tmp_name) / "normalization.md"
            path.write_text(
                "\n".join(
                    [
                        "## CanonicalFinding cf-round-1-001",
                        frontmatter(
                            self._finding_data(
                                schema_version="m2-markdown-1",
                                record_type="CanonicalFinding",
                                identifier_field="canonical_finding_id",
                                identifier="cf-round-1-001",
                            )
                        ),
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            parsed = parse_records_with_diagnostics(path)

        self.assertEqual(parsed.diagnostics, [])
        self.assertEqual(len(parsed.records), 1)
        record = parsed.records[0]
        self.assertEqual(record.record_type, "NormalizedFinding")
        self.assertEqual(record.record_id, "cf-round-1-001")
        self.assertEqual(record.data["normalized_finding_id"], "cf-round-1-001")
        self.assertNotIn("canonical_finding_id", record.data)
        self.assertEqual(record.finding_schema_origin, "legacy")

    def test_historical_lifecycle_reference_decodes_to_current_identifier_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            path = Path(tmp_name) / "author-responses.md"
            path.write_text(
                "\n".join(
                    [
                        "## AuthorResponse response-001",
                        frontmatter(
                            {
                                "record_type": "AuthorResponse",
                                "schema_version": "m2-markdown-1",
                                "run_id": "sample",
                                "actor_identity": "author",
                                "created_at": "2026-06-01T00:00:00Z",
                                "author_response_id": "response-001",
                                "canonical_finding_id": "cf-round-1-001",
                                "response_type": "accept",
                                "rationale": "fixed",
                                "resulting_artifact_version_id_or_null": "v2",
                                "clarification_request_or_null": None,
                            }
                        ),
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            parsed = parse_records_with_diagnostics(path)

        self.assertEqual(parsed.diagnostics, [])
        self.assertEqual(parsed.records[0].data["normalized_finding_id"], "cf-round-1-001")
        self.assertNotIn("canonical_finding_id", parsed.records[0].data)
        self.assertEqual(parsed.records[0].finding_schema_origin, "legacy")

    def test_current_schema_rejects_historical_finding_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            path = Path(tmp_name) / "normalization.md"
            path.write_text(
                "\n".join(
                    [
                        "## CanonicalFinding cf-round-1-001",
                        frontmatter(
                            self._finding_data(
                                schema_version="m2-markdown-2",
                                record_type="CanonicalFinding",
                                identifier_field="canonical_finding_id",
                                identifier="cf-round-1-001",
                            )
                        ),
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            parsed = parse_records_with_diagnostics(path)

        self.assertEqual(parsed.records, [])
        self.assertEqual(len(parsed.diagnostics), 1)
        self.assertIn("historical finding names are not valid", parsed.diagnostics[0].message)

    def test_historical_schema_rejects_current_finding_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            path = Path(tmp_name) / "normalization.md"
            path.write_text(
                "\n".join(
                    [
                        "## NormalizedFinding nf-round-1-001",
                        frontmatter(
                            self._finding_data(
                                schema_version="m2-markdown-1",
                                record_type="NormalizedFinding",
                                identifier_field="normalized_finding_id",
                                identifier="nf-round-1-001",
                            )
                        ),
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            parsed = parse_records_with_diagnostics(path)

        self.assertEqual(parsed.records, [])
        self.assertEqual(len(parsed.diagnostics), 1)
        self.assertIn("current finding names are not valid", parsed.diagnostics[0].message)

    def test_run_snapshot_rejects_mixed_historical_and_current_finding_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = Path(tmp_name)
            legacy = run / "legacy.md"
            current = run / "current.md"
            legacy.write_text(
                "\n".join(
                    [
                        "## CanonicalFinding cf-round-1-001",
                        frontmatter(
                            self._finding_data(
                                schema_version="m2-markdown-1",
                                record_type="CanonicalFinding",
                                identifier_field="canonical_finding_id",
                                identifier="cf-round-1-001",
                            )
                        ),
                    ]
                ),
                encoding="utf-8",
            )
            current.write_text(
                "\n".join(
                    [
                        "## NormalizedFinding nf-round-1-001",
                        frontmatter(
                            self._finding_data(
                                schema_version="m2-markdown-2",
                                record_type="NormalizedFinding",
                                identifier_field="normalized_finding_id",
                                identifier="nf-round-1-001",
                            )
                        ),
                    ]
                ),
                encoding="utf-8",
            )

            snapshot = parse_run_snapshot(run)
            result = check_records(run, snapshot)
            with self.assertRaisesRegex(FindingSchemaError, "mixes historical and current"):
                parse_run_records(run)

        self.assertFalse(result.ok)
        self.assertTrue(
            any("mixes historical and current finding records" in message for message in result.messages)
        )

    def test_historical_finding_record_passes_current_record_validation_after_decode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = Path(tmp_name)
            path = run / "normalization.md"
            path.write_text(
                "\n".join(
                    [
                        "## CanonicalFinding cf-round-1-001",
                        frontmatter(
                            self._finding_data(
                                schema_version="m2-markdown-1",
                                record_type="CanonicalFinding",
                                identifier_field="canonical_finding_id",
                                identifier="cf-round-1-001",
                            )
                        ),
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            result = check_records(run)

        self.assertTrue(result.ok, result.messages)

    def test_rendered_yaml_preserves_ambiguous_strings(self) -> None:
        values = {
            "boolean_text": "true",
            "null_text": "null",
            "integer_text": "123",
            "quoted_text": 'value: "quoted"',
            "items": ["true", "null", "123"],
        }

        self.assertEqual(parse_yaml_subset(render_yaml(values)), values)

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
                                "schema_version": "m2-markdown-2",
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
                                "schema_version": "m2-markdown-2",
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

    def test_record_parser_implicit_close_at_eof(self) -> None:
        """Last record without a closing `---` should still parse, with body ending at EOF."""
        with tempfile.TemporaryDirectory() as tmp_name:
            path = Path(tmp_name) / "run.md"
            path.write_text(
                "\n".join(
                    [
                        "## TaskBrief task-eof",
                        "---",
                        "record_type: TaskBrief",
                        "schema_version: m2-markdown-2",
                        "run_id: sample",
                        "actor_identity: orchestrator",
                        "created_at: 2026-06-01T00:00:00Z",
                        "task_brief_id: task-eof",
                        "artifact_locator: README.md",
                        "objective: test EOF close",
                        "success_criteria:",
                        "  - pass",
                        "profile: document-consensus",
                        "human_supervisor_identity_or_null: null",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            records = parse_records_from_file(path)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].record_type, "TaskBrief")
        self.assertEqual(records[0].data["objective"], "test EOF close")

    def test_record_parser_implicit_close_stops_at_next_heading(self) -> None:
        """A missing close fence must not swallow the next record block."""
        with tempfile.TemporaryDirectory() as tmp_name:
            path = Path(tmp_name) / "run.md"
            path.write_text(
                "\n".join(
                    [
                        "## TaskBrief task-001",
                        "---",
                        "record_type: TaskBrief",
                        "schema_version: m2-markdown-2",
                        "run_id: sample",
                        "actor_identity: orchestrator",
                        "created_at: 2026-06-01T00:00:00Z",
                        "task_brief_id: task-001",
                        "artifact_locator: README.md",
                        "objective: first",
                        "success_criteria:",
                        "  - pass",
                        "profile: document-consensus",
                        "human_supervisor_identity_or_null: null",
                        "",
                        "## Participants participants-001",
                        "---",
                        "record_type: Participants",
                        "schema_version: m2-markdown-2",
                        "run_id: sample",
                        "actor_identity: orchestrator",
                        "created_at: 2026-06-01T00:00:00Z",
                        "participants_record_id: participants-001",
                        "orchestrator_identity: orch",
                        "author_identity: author",
                        "reviewer_identities:",
                        "  - rev",
                        "human_supervisor_identity_or_null: null",
                        "---",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            records = parse_records_from_file(path)

        record_types = sorted(record.record_type for record in records)
        self.assertEqual(record_types, ["Participants", "TaskBrief"])

    def test_record_parser_reports_unknown_frontmatter_record_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            path = Path(tmp_name) / "run.md"
            path.write_text(
                "\n".join(
                    [
                        "## TaskBrieff task-001",
                        "---",
                        "record_type: TaskBrieff",
                        "schema_version: m2-markdown-2",
                        "---",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            parsed = parse_records_with_diagnostics(path)

        self.assertEqual(parsed.records, [])
        self.assertEqual(len(parsed.diagnostics), 1)
        self.assertIn("unknown record type TaskBrieff", parsed.diagnostics[0].message)

    def test_field_aliases_rewrite_raw_finding_keys(self) -> None:
        """RawFinding `suggested_fix` should map to `suggested_fix_or_null` and `severity` to claim."""
        with tempfile.TemporaryDirectory() as tmp_name:
            path = Path(tmp_name) / "raw.md"
            path.write_text(
                "\n".join(
                    [
                        "## RawFinding rf-001",
                        frontmatter(
                            {
                                "record_type": "RawFinding",
                                "schema_version": "m2-markdown-2",
                                "run_id": "sample",
                                "actor_identity": "orchestrator",
                                "created_at": "2026-06-01T00:00:00Z",
                                "raw_finding_id": "rf-001",
                                "reviewer_identity": "rev",
                                "artifact_version_id": "v1",
                                "review_batch_id": "rb-1",
                                "location": "loc",
                                "claim": "x",
                                "evidence": "e",
                                "severity": "high",
                                "scope_classification": "in_scope",
                                "blocking_status": "blocking",
                                "suggested_fix": "do the thing",
                            }
                        ),
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            records = parse_records_from_file(path)

        self.assertEqual(len(records), 1)
        data = records[0].data
        self.assertEqual(data["severity_or_materiality_claim"], "high")
        self.assertEqual(data["suggested_fix_or_null"], "do the thing")
        self.assertNotIn("severity", data)
        self.assertNotIn("suggested_fix", data)
        self.assertEqual(sorted(data["_aliases_consumed"]), ["severity", "suggested_fix"])

    def test_field_aliases_rewrite_final_report_target(self) -> None:
        """FinalReport.target_artifact_version_id should rewrite to final_artifact_version_id_or_null."""
        with tempfile.TemporaryDirectory() as tmp_name:
            path = Path(tmp_name) / "report.md"
            path.write_text(
                "\n".join(
                    [
                        "## FinalReport fr-001",
                        frontmatter(
                            {
                                "record_type": "FinalReport",
                                "schema_version": "m2-markdown-2",
                                "run_id": "sample",
                                "actor_identity": "orchestrator",
                                "created_at": "2026-06-01T00:00:00Z",
                                "final_report_id": "fr-001",
                                "termination_record_id": "tr-001",
                                "terminal_condition": "consensus_reached",
                                "target_artifact_version_id": "v3",
                                "validator_status": {},
                                "unresolved_finding_ids": [],
                                "backlog_path": "backlog.md",
                            }
                        ),
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            records = parse_records_from_file(path)

        self.assertEqual(records[0].data["final_artifact_version_id_or_null"], "v3")
        self.assertEqual(records[0].data["_aliases_consumed"], ["target_artifact_version_id"])

    def test_agent_prompt_and_raw_paths_are_protocol_payloads(self) -> None:
        self.assertTrue(
            is_protocol_payload_path(
                Path(
                    "runs/example/rounds/round-001/agents/claude/session-001/final-output.md"
                )
            )
        )
        self.assertTrue(
            is_protocol_payload_path(Path("runs/example/rounds/round-001/prompts/reviewers/reviewer.md"))
        )
        self.assertTrue(
            is_protocol_payload_path(Path("runs/example/rounds/round-001/raw/reviewers/reviewer.out"))
        )
        self.assertFalse(is_protocol_payload_path(Path("runs/example/rounds/round-001/reviews/reviewer.md")))


if __name__ == "__main__":
    unittest.main()
