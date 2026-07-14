from __future__ import annotations

import argparse
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "skills" / "cross-agent-consensus"
sys.path.insert(0, str(PACKAGE_ROOT))

from cross_agent_consensus.capture import derive_raw_findings_from_narrative, raw_payload_target_base


class CaptureTests(unittest.TestCase):
    def test_round_first_reviewer_raw_path_uses_actor_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = Path(tmp_name) / "sample"
            (run / "rounds").mkdir(parents=True)
            args = argparse.Namespace(phase="reviewer", actor="Reviewer Codex", round="round-1")

            target = raw_payload_target_base(run, args)

        self.assertEqual(target, run / "rounds/round-001/raw/reviewers/reviewer-codex.out")

    def test_round_first_validator_raw_path_uses_validator_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = Path(tmp_name) / "sample"
            (run / "rounds").mkdir(parents=True)
            args = argparse.Namespace(
                phase="validator",
                actor="validator-local",
                validator_id="artifact_exists",
                round="round-1",
            )

            target = raw_payload_target_base(run, args)

        self.assertEqual(target, run / "rounds/round-001/raw/validators/artifact-exists.out")


    def test_narrative_extraction_emits_skeleton_per_unique_finding_id(self) -> None:
        """Narrative ids `R1-CODEX-01..03` produce one skeleton each, deduped, embedded in RawReviewerOutput."""
        narrative = "\n\n".join(
            [
                "Finding R1-CODEX-01 in module foo.",
                "Finding R1-CODEX-02 about bar.",
                "Repeat of R1-CODEX-01 should not duplicate.",
                "Finding R1-CODEX-03 about baz.",
            ]
        )

        finding_ids, sections = derive_raw_findings_from_narrative(
            raw_text=narrative,
            reviewer_identity="reviewer-codex",
            review_batch_id="rb-001",
            artifact_version_id="v1",
            run_id="sample",
            created_at="2026-06-01T00:00:00Z",
        )

        self.assertEqual(finding_ids, ["r1-codex-01", "r1-codex-02", "r1-codex-03"])
        self.assertEqual(len(sections), 3)
        for finding_id, section in zip(finding_ids, sections):
            self.assertIn(f"## RawFinding {finding_id}", section)
            self.assertIn("record_type: RawFinding", section)
            self.assertIn(f"raw_finding_id: {finding_id}", section)
            self.assertIn("review_batch_id: rb-001", section)
            self.assertIn("artifact_version_id: v1", section)
            self.assertIn("### Narrative Context", section)

    def test_narrative_extraction_no_ids_returns_empty(self) -> None:
        """A narrative with no `R<round>-<REVIEWER>-<NN>` tokens yields no skeletons."""
        finding_ids, sections = derive_raw_findings_from_narrative(
            raw_text="No findings claimed here, just prose.",
            reviewer_identity="reviewer-codex",
            review_batch_id="rb-001",
            artifact_version_id="v1",
            run_id="sample",
            created_at="2026-06-01T00:00:00Z",
        )

        self.assertEqual(finding_ids, [])
        self.assertEqual(sections, [])

    def test_narrative_extraction_batch_qualifies_colliding_ids(self) -> None:
        finding_ids, sections = derive_raw_findings_from_narrative(
            raw_text="R1-CODEX-001 first.\n\nR1-CODEX-002 second.",
            reviewer_identity="codex",
            review_batch_id="review-batch-round-1-regression-v2",
            artifact_version_id="v2",
            run_id="sample",
            created_at="2026-06-01T00:00:00Z",
            existing_finding_ids={"r1-codex-001"},
        )

        self.assertEqual(
            finding_ids,
            [
                "r1-codex-review-batch-round-1-regression-v2-001",
                "r1-codex-review-batch-round-1-regression-v2-002",
            ],
        )
        self.assertIn(f"raw_finding_id: {finding_ids[0]}", sections[0])

    def test_narrative_extraction_truncates_long_paragraph(self) -> None:
        """Paragraphs over the truncation limit get cut to keep the embedded context bounded."""
        long_paragraph = "R1-CODEX-01 details " + ("x" * 2000)
        finding_ids, sections = derive_raw_findings_from_narrative(
            raw_text=long_paragraph,
            reviewer_identity="reviewer-codex",
            review_batch_id="rb-001",
            artifact_version_id="v1",
            run_id="sample",
            created_at="2026-06-01T00:00:00Z",
        )

        self.assertEqual(finding_ids, ["r1-codex-01"])
        self.assertEqual(len(sections), 1)
        self.assertIn("...", sections[0])


if __name__ == "__main__":
    unittest.main()
