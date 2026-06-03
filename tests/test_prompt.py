from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "skills" / "cross-agent-consensus"
sys.path.insert(0, str(PACKAGE_ROOT))

from cross_agent_consensus.models import Record
from cross_agent_consensus.prompts import (
    is_conclusion_validation_batch,
    proposed_conclusion_for_finding,
    resolve_active_round,
    select_review_batch,
    table_cell,
)


class PromptTests(unittest.TestCase):
    def test_select_review_batch_matches_numeric_round_alias(self) -> None:
        records = [
            Record("ReviewBatch", "round-1", Path("round-001/round.md"), 1, {"round_id": "round-1"}),
            Record("ReviewBatch", "round-2", Path("round-002/round.md"), 1, {"round_id": "round-2"}),
        ]

        selected = select_review_batch(records, "round-002")

        self.assertIsNotNone(selected)
        self.assertEqual(selected.record_id, "round-2")

    def test_resolve_active_round_rejects_mismatched_review_batch_round(self) -> None:
        records = [
            Record(
                "ReviewBatch",
                "batch",
                Path("round-002/round.md"),
                1,
                {"review_batch_id": "batch", "round_id": "round-2"},
            )
        ]

        with self.assertRaisesRegex(ValueError, "does not match ReviewBatch"):
            resolve_active_round(records, "round-1", "batch")

    def test_select_review_batch_requires_id_when_round_has_multiple_batches(self) -> None:
        records = [
            Record(
                "ReviewBatch",
                "fresh",
                Path("round-001/round.md"),
                1,
                {"review_batch_id": "fresh", "round_id": "round-1"},
            ),
            Record(
                "ReviewBatch",
                "validation",
                Path("round-001/round.md"),
                10,
                {"review_batch_id": "validation", "round_id": "round-1"},
            ),
        ]

        with self.assertRaisesRegex(ValueError, "--review-batch is required"):
            select_review_batch(records, "round-1")

        selected = select_review_batch(records, "round-1", "validation")

        self.assertIsNotNone(selected)
        self.assertEqual(selected.record_id, "validation")

    def test_resolve_active_round_can_use_review_batch_without_round(self) -> None:
        records = [
            Record(
                "ReviewBatch",
                "validation",
                Path("round-002/round.md"),
                1,
                {"review_batch_id": "validation", "round_id": "round-2"},
            )
        ]

        self.assertEqual(resolve_active_round(records, None, "validation"), "round-2")

    def test_proposed_conclusion_defaults_ambiguous_material_findings_to_unclear(self) -> None:
        record = Record(
            "CanonicalFinding",
            "canonical-finding-001",
            Path("normalization.md"),
            1,
            {
                "canonical_finding_id": "canonical-finding-001",
                "scope_classification": "in_scope",
                "blocking_status": "non_blocking",
                "materiality": "material",
                "lifecycle_state": "open",
            },
        )

        self.assertEqual(proposed_conclusion_for_finding(record), "unclear")

    def test_table_cell_escapes_backslashes_before_pipes(self) -> None:
        self.assertEqual(table_cell(r"foo\|bar"), r"foo\\\|bar")

    def test_scope_triage_requires_explicit_conclusion_validation_purpose(self) -> None:
        batch = Record(
            "ReviewBatch",
            "batch",
            Path("round.md"),
            1,
            {
                "review_batch_id": "batch",
                "review_mode": "scope_triage",
                "source_finding_ids": ["canonical-finding-001"],
            },
        )

        self.assertFalse(is_conclusion_validation_batch(batch, []))

        batch.data["batch_purpose"] = "conclusion_validation"
        self.assertTrue(is_conclusion_validation_batch(batch, []))


if __name__ == "__main__":
    unittest.main()
