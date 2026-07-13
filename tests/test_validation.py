from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "skills" / "cross-agent-consensus"
sys.path.insert(0, str(PACKAGE_ROOT))

from cross_agent_consensus.markdown_records import frontmatter
from cross_agent_consensus.models import Record
from cross_agent_consensus.validation import (
    check_records,
    remediation_cap_blockers,
    required_field_missing,
    unresolved_blockers,
    validator_status,
)


class ValidationTests(unittest.TestCase):
    def test_required_field_missing_allows_or_null_none(self) -> None:
        self.assertFalse(required_field_missing({"value_or_null": None}, "value_or_null"))
        self.assertTrue(required_field_missing({"value": None}, "value"))
        self.assertTrue(required_field_missing({"value": "<placeholder>"}, "value"))
        self.assertFalse(required_field_missing({"value": "Require 0 < retries > -1"}, "value"))

    def test_validator_status_uses_latest_seen_result(self) -> None:
        records = [
            Record("ValidationEvidence", "one", Path("validation.md"), 1, {"validator_id": "smoke", "result": "fail"}),
            Record("ValidationEvidence", "two", Path("validation.md"), 2, {"validator_id": "smoke", "result": "pass"}),
        ]

        self.assertEqual(validator_status(records), {"smoke": "pass"})

    def test_unresolved_blockers_filters_non_blocking_findings(self) -> None:
        records = [
            Record(
                "CanonicalFinding",
                "blocking",
                Path("normalization.md"),
                1,
                {
                    "canonical_finding_id": "blocking",
                    "scope_classification": "in_scope",
                    "blocking_status": "blocking",
                    "materiality": "material",
                    "materiality_status": "undisputed",
                    "lifecycle_state": "open",
                },
            ),
            Record(
                "CanonicalFinding",
                "non-blocking",
                Path("normalization.md"),
                2,
                {
                    "canonical_finding_id": "non-blocking",
                    "scope_classification": "in_scope",
                    "blocking_status": "non_blocking",
                    "materiality": "material",
                    "materiality_status": "undisputed",
                    "lifecycle_state": "open",
                },
            ),
        ]

        self.assertEqual(unresolved_blockers(records), ["blocking"])

    def test_remediation_cap_blocks_unresolved_latest_re_review(self) -> None:
        records = [
            Record(
                "ReviewScope",
                "scope",
                Path("run.md"),
                1,
                {"max_remediation_rounds_per_finding": 2},
            ),
            Record(
                "ReReviewDecision",
                "attempt-1",
                Path("rereviews.md"),
                1,
                {
                    "canonical_finding_id": "CXR-001",
                    "reviewer_identity": "reviewer-codex",
                    "decision": "still_valid",
                },
            ),
            Record(
                "ReReviewDecision",
                "attempt-2",
                Path("rereviews.md"),
                2,
                {
                    "canonical_finding_id": "CXR-001",
                    "reviewer_identity": "reviewer-codex",
                    "decision": "still_valid",
                },
            ),
        ]

        blockers = remediation_cap_blockers(records, ["CXR-001"], "reviewer-codex")

        self.assertEqual(blockers, [("CXR-001", "reviewer-codex", 2, "still_valid", 2)])


    def test_check_records_emits_deprecation_for_field_alias(self) -> None:
        """Records using aliased field names should surface deprecation: warnings in check_records output."""
        with tempfile.TemporaryDirectory() as tmp_name:
            run = Path(tmp_name) / "sample"
            run.mkdir()
            path = run / "raw.md"
            path.write_text(
                "\n".join(
                    [
                        "## RawFinding rf-001",
                        frontmatter(
                            {
                                "record_type": "RawFinding",
                                "schema_version": "m2-markdown-1",
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

            result = check_records(run)

        deprecation_lines = [msg for msg in result.messages if msg.startswith("deprecation:")]
        self.assertTrue(
            any("RawFinding.severity -> severity_or_materiality_claim" in msg for msg in deprecation_lines),
            f"severity deprecation missing from {deprecation_lines!r}",
        )
        self.assertTrue(
            any("RawFinding.suggested_fix -> suggested_fix_or_null" in msg for msg in deprecation_lines),
            f"suggested_fix deprecation missing from {deprecation_lines!r}",
        )

    def test_check_records_rejects_wrong_required_field_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = Path(tmp_name) / "sample"
            run.mkdir()
            (run / "run.md").write_text(
                "\n".join(
                    [
                        "## TaskBrief task-001",
                        frontmatter(
                            {
                                "record_type": "TaskBrief",
                                "schema_version": "m2-markdown-1",
                                "run_id": "sample",
                                "actor_identity": "orchestrator",
                                "created_at": "2026-06-01T00:00:00Z",
                                "task_brief_id": "task-001",
                                "artifact_locator": "README.md",
                                "objective": True,
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

            result = check_records(run)

        self.assertFalse(result.ok)
        self.assertTrue(
            any("TaskBrief.objective must be str, got bool" in message for message in result.messages),
            result.messages,
        )


if __name__ == "__main__":
    unittest.main()
