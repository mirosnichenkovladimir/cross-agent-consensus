from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "skills" / "cross-agent-consensus"
sys.path.insert(0, str(PACKAGE_ROOT))

from cross_agent_consensus.models import PromptCommandInput, Record
from cross_agent_consensus.prompts import (
    active_review_batches,
    build_prompt,
    is_conclusion_validation_batch,
    proposed_conclusion_for_finding,
    resolve_active_round,
    round_first_prompt_target,
    select_review_batch,
    table_cell,
)


class PromptTests(unittest.TestCase):
    def test_reviewer_prompt_forbids_recursive_cac_invocation(self) -> None:
        records = [
            Record(
                "ReviewBatch",
                "batch-v1",
                Path("rounds/round-001/round.md"),
                1,
                {
                    "review_batch_id": "batch-v1",
                    "round_id": "round-1",
                    "review_mode": "fresh_review",
                    "target_artifact_version_id": "v1",
                },
            ),
            Record(
                "ArtifactVersion",
                "v1",
                Path("artifacts/v1.md"),
                1,
                {
                    "artifact_version_id": "v1",
                    "content_locator": "artifact.md",
                    "content_hash_or_null": None,
                },
            ),
        ]
        args = PromptCommandInput(
            run="run",
            phase="reviewer",
            actor="reviewer",
            artifact_version="v1",
            round="round-1",
            review_batch="batch-v1",
            output=None,
            force_draft=False,
            dry_run=False,
        )

        prompt = build_prompt(args, records)

        self.assertTrue(prompt.startswith("# Artifact Reviewer Prompt\n"))
        self.assertIn("## Participant Boundary", prompt)
        self.assertIn("Do not load or invoke the cross-agent-consensus skill.", prompt)
        self.assertLess(prompt.index("## Participant Boundary"), prompt.index("## ArtifactVersion"))

    def test_reviewer_prompt_rejects_missing_explicit_artifact(self) -> None:
        records = [
            Record(
                "ReviewBatch",
                "batch-v1",
                Path("rounds/round-001/round.md"),
                1,
                {
                    "review_batch_id": "batch-v1",
                    "round_id": "round-1",
                    "target_artifact_version_id": "v1",
                },
            )
        ]
        args = PromptCommandInput(
            run="run",
            phase="reviewer",
            actor="reviewer",
            artifact_version="missing",
            round="round-1",
            review_batch="batch-v1",
            output=None,
            force_draft=False,
            dry_run=False,
        )

        with self.assertRaisesRegex(ValueError, "ArtifactVersion not found: missing"):
            build_prompt(args, records)

    def test_reviewer_prompt_rejects_artifact_outside_review_batch(self) -> None:
        records = [
            Record(
                "ReviewBatch",
                "batch-v1",
                Path("rounds/round-001/round.md"),
                1,
                {
                    "review_batch_id": "batch-v1",
                    "round_id": "round-1",
                    "target_artifact_version_id": "v1",
                },
            ),
            Record(
                "ArtifactVersion",
                "v1",
                Path("artifacts/v1.md"),
                1,
                {"artifact_version_id": "v1"},
            ),
            Record(
                "ArtifactVersion",
                "v2",
                Path("artifacts/v2.md"),
                1,
                {"artifact_version_id": "v2"},
            ),
        ]
        args = PromptCommandInput(
            run="/tmp/run",
            phase="reviewer",
            actor="reviewer",
            artifact_version="v2",
            round="round-1",
            review_batch="batch-v1",
            output=None,
            force_draft=False,
            dry_run=False,
        )

        with self.assertRaisesRegex(ValueError, "targets ArtifactVersion v1, not v2"):
            build_prompt(args, records)

    def test_later_review_batch_does_not_relocate_first_batch_prompt(self) -> None:
        run = Path("/tmp/sample-consensus-001")
        records = [
            Record(
                "ReviewBatch",
                "fresh",
                Path("rounds/round-001/round.md"),
                1,
                {"review_batch_id": "fresh", "round_id": "round-1", "review_mode": "fresh_review"},
            ),
            Record(
                "ReviewBatch",
                "remediation",
                Path("rounds/round-001/round.md"),
                2,
                {
                    "review_batch_id": "remediation",
                    "round_id": "round-1",
                    "review_mode": "remediation_verification",
                },
            ),
        ]
        first_args = PromptCommandInput(
            run=str(run),
            phase="reviewer",
            actor="codex",
            artifact_version="v1",
            round="round-1",
            review_batch="fresh",
            output=None,
            force_draft=False,
            dry_run=False,
        )
        later_args = PromptCommandInput(
            run=str(run),
            phase="reviewer",
            actor="codex",
            artifact_version="v2",
            round="round-1",
            review_batch="remediation",
            output=None,
            force_draft=False,
            dry_run=False,
        )

        self.assertEqual(
            round_first_prompt_target(run, first_args, records),
            run / "rounds" / "round-001" / "prompts" / "reviewers" / "codex.md",
        )
        self.assertEqual(
            round_first_prompt_target(run, later_args, records),
            run / "rounds" / "round-001" / "prompts" / "reviewers" / "remediation" / "codex.md",
        )

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
            "NormalizedFinding",
            "normalized-finding-001",
            Path("normalization.md"),
            1,
            {
                "normalized_finding_id": "normalized-finding-001",
                "scope_classification": "in_scope",
                "blocking_status": "non_blocking",
                "materiality": "material",
                "lifecycle_state": "open",
            },
        )

        self.assertEqual(proposed_conclusion_for_finding(record), "unclear")

    def test_table_cell_escapes_backslashes_before_pipes(self) -> None:
        self.assertEqual(table_cell(r"foo\|bar"), r"foo\\\|bar")

    def test_active_review_batches_returns_only_max_round_when_round_is_none(self) -> None:
        records = [
            Record(
                "ReviewBatch",
                "rb-r1",
                Path("round-001/round.md"),
                1,
                {"review_batch_id": "rb-r1", "round_id": "round-1"},
            ),
            Record(
                "ReviewBatch",
                "rb-r2-a",
                Path("round-002/round.md"),
                1,
                {"review_batch_id": "rb-r2-a", "round_id": "round-2"},
            ),
            Record(
                "ReviewBatch",
                "rb-r2-b",
                Path("round-002/round.md"),
                2,
                {"review_batch_id": "rb-r2-b", "round_id": "round-2"},
            ),
        ]

        self.assertEqual(active_review_batches(records, None), ["rb-r2-a", "rb-r2-b"])

    def test_active_review_batches_filters_by_explicit_round(self) -> None:
        records = [
            Record(
                "ReviewBatch",
                "rb-r1",
                Path("round-001/round.md"),
                1,
                {"review_batch_id": "rb-r1", "round_id": "round-1"},
            ),
            Record(
                "ReviewBatch",
                "rb-r2",
                Path("round-002/round.md"),
                1,
                {"review_batch_id": "rb-r2", "round_id": "round-2"},
            ),
        ]

        self.assertEqual(active_review_batches(records, "round-1"), ["rb-r1"])
        self.assertEqual(active_review_batches(records, "round-002"), ["rb-r2"])

    def test_active_review_batches_returns_empty_when_no_batches(self) -> None:
        self.assertEqual(active_review_batches([], None), [])
        self.assertEqual(active_review_batches([], "round-1"), [])

    def test_scope_triage_requires_explicit_conclusion_validation_purpose(self) -> None:
        batch = Record(
            "ReviewBatch",
            "batch",
            Path("round.md"),
            1,
            {
                "review_batch_id": "batch",
                "review_mode": "scope_triage",
                "source_finding_ids": ["normalized-finding-001"],
            },
        )

        self.assertFalse(is_conclusion_validation_batch(batch, []))

        batch.data["batch_purpose"] = "conclusion_validation"
        self.assertTrue(is_conclusion_validation_batch(batch, []))


if __name__ == "__main__":
    unittest.main()
