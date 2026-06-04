from __future__ import annotations

import argparse
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "skills" / "cross-agent-consensus"
sys.path.insert(0, str(PACKAGE_ROOT))

from cross_agent_consensus.markdown_records import frontmatter, parse_records_from_file
from cross_agent_consensus.normalize import build_normalization_skeleton, cmd_normalize
from cross_agent_consensus.records import records_by_type


def _write_run(tmp_root: Path, raw_finding_specs: list[dict]) -> Path:
    """Create a minimal run with one ReviewBatch and N RawFindings under round-1."""
    run = tmp_root / "sample"
    rounds_dir = run / "rounds" / "round-001"
    rounds_dir.mkdir(parents=True)
    (rounds_dir / "reviews").mkdir()
    (run / "run.md").write_text(
        "\n".join(
            [
                "## ReviewBatch rb-001",
                frontmatter(
                    {
                        "record_type": "ReviewBatch",
                        "schema_version": "m2-markdown-1",
                        "run_id": "sample",
                        "actor_identity": "orchestrator",
                        "created_at": "2026-06-01T00:00:00Z",
                        "review_batch_id": "rb-001",
                        "review_scope_id": "rs-001",
                        "review_mode": "fresh_review",
                        "target_artifact_version_id": "v1",
                        "source_finding_ids": [],
                        "round_id": "round-1",
                    }
                ),
                "",
            ]
        ),
        encoding="utf-8",
    )
    sections: list[str] = ["# Reviews round-1: codex", ""]
    for spec in raw_finding_specs:
        sections.append(f"## RawFinding {spec['raw_finding_id']}")
        sections.append(
            frontmatter(
                {
                    "record_type": "RawFinding",
                    "schema_version": "m2-markdown-1",
                    "run_id": "sample",
                    "actor_identity": "orchestrator",
                    "created_at": "2026-06-01T00:00:00Z",
                    "raw_finding_id": spec["raw_finding_id"],
                    "reviewer_identity": spec.get("reviewer_identity", "reviewer-codex"),
                    "artifact_version_id": "v1",
                    "review_batch_id": "rb-001",
                    "location": spec.get("location", "loc"),
                    "claim": spec.get("claim", "problem"),
                    "evidence": spec.get("evidence", "evidence"),
                    "severity_or_materiality_claim": "high",
                    "scope_classification": "in_scope",
                    "blocking_status": "blocking",
                    "suggested_fix_or_null": None,
                }
            )
        )
        sections.append("")
    (rounds_dir / "reviews" / "codex.md").write_text("\n".join(sections), encoding="utf-8")
    return run


class NormalizeTests(unittest.TestCase):
    def test_skeleton_emits_one_normalization_and_canonical_per_raw_finding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            specs = [
                {"raw_finding_id": f"rf-{index:03d}", "claim": f"problem {index}", "location": f"file-{index}"}
                for index in range(1, 6)
            ]
            run = _write_run(Path(tmp_name), specs)

            body = build_normalization_skeleton(run, "round-1")

        self.assertEqual(body.count("## NormalizationRecord "), 5)
        self.assertEqual(body.count("## CanonicalFinding "), 5)
        for index in range(1, 6):
            self.assertIn(f"canonical_finding_id: cf-round-1-{index:03d}", body)
            self.assertIn(f"normalization_record_id: normalization-cf-round-1-{index:03d}", body)

    def test_skeleton_passes_record_parser(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            specs = [{"raw_finding_id": "rf-001"}, {"raw_finding_id": "rf-002"}]
            run = _write_run(Path(tmp_name), specs)

            body = build_normalization_skeleton(run, "round-1")
            target = run / "rounds" / "round-001" / "normalization.md"
            target.write_text(body, encoding="utf-8")
            records = parse_records_from_file(target)

        normalization = records_by_type(records, "NormalizationRecord")
        canonical = records_by_type(records, "CanonicalFinding")
        self.assertEqual(len(normalization), 2)
        self.assertEqual(len(canonical), 2)
        for record in normalization:
            for field in [
                "normalization_record_id",
                "source_raw_finding_ids",
                "normalizer_identity",
                "classifier_identity",
                "scope_classification",
                "blocking_status",
                "rationale",
                "canonical_finding_id",
            ]:
                self.assertIn(field, record.data, f"{field} missing in {record.data}")
        for record in canonical:
            self.assertEqual(record.data["lifecycle_state"], "open")
            self.assertEqual(record.data["materiality_status"], "undisputed")

    def test_merge_overlap_buckets_findings_with_same_location_and_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            specs = [
                {"raw_finding_id": "rf-001", "location": "file-a", "claim": "Same issue"},
                {"raw_finding_id": "rf-002", "location": "file-a", "claim": "same issue"},
                {"raw_finding_id": "rf-003", "location": "file-b", "claim": "Different"},
            ]
            run = _write_run(Path(tmp_name), specs)

            body = build_normalization_skeleton(run, "round-1", merge_overlap=True)
            target = run / "rounds" / "round-001" / "normalization.md"
            target.write_text(body, encoding="utf-8")
            records = parse_records_from_file(target)

        canonical = records_by_type(records, "CanonicalFinding")
        self.assertEqual(len(canonical), 2)
        bucketed = [record for record in canonical if len(record.data["source_raw_finding_ids"]) == 2]
        self.assertEqual(len(bucketed), 1)
        self.assertEqual(set(bucketed[0].data["source_raw_finding_ids"]), {"rf-001", "rf-002"})

    def test_cmd_normalize_writes_file_and_overwrite_replaces_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = _write_run(Path(tmp_name), [{"raw_finding_id": "rf-001"}])
            args = argparse.Namespace(
                run=str(run),
                round="round-1",
                actor="orchestrator-consensus-tool",
                merge_overlap=False,
                overwrite=False,
            )

            self.assertEqual(cmd_normalize(args), 0)
            target = run / "rounds" / "round-001" / "normalization.md"
            self.assertTrue(target.exists())

            # Without --overwrite the second run must refuse.
            self.assertEqual(cmd_normalize(args), 1)

            args.overwrite = True
            self.assertEqual(cmd_normalize(args), 0)

    def test_skeleton_with_no_raw_findings_returns_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = _write_run(Path(tmp_name), [])

            body = build_normalization_skeleton(run, "round-1")

        self.assertIn("No RawFinding records exist for this round yet.", body)
        self.assertNotIn("## NormalizationRecord", body)


if __name__ == "__main__":
    unittest.main()
