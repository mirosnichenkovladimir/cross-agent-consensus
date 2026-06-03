from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "skills" / "cross-agent-consensus"
sys.path.insert(0, str(PACKAGE_ROOT))

from cross_agent_consensus.models import Record
from cross_agent_consensus.validation import (
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


if __name__ == "__main__":
    unittest.main()
