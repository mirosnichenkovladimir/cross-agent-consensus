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
from cross_agent_consensus.records import records_by_type
from cross_agent_consensus.report import build_report_skeleton, cmd_report


def _write_run_with_canonical(tmp_root: Path, canonical_specs: list[dict]) -> Path:
    """Create a minimal run containing one ArtifactVersion and N CanonicalFindings."""
    run = tmp_root / "sample"
    run.mkdir()
    sections: list[str] = ["# Run sample", ""]
    for spec in canonical_specs:
        sections.append(f"## CanonicalFinding {spec['canonical_finding_id']}")
        sections.append(
            frontmatter(
                {
                    "record_type": "CanonicalFinding",
                    "schema_version": "m2-markdown-1",
                    "run_id": "sample",
                    "actor_identity": "orchestrator",
                    "created_at": "2026-06-01T00:00:00Z",
                    "canonical_finding_id": spec["canonical_finding_id"],
                    "target_artifact_version_id": "v1",
                    "source_raw_finding_ids": ["rf-001"],
                    "normalization_record_id": f"normalization-{spec['canonical_finding_id']}",
                    "materiality": "material",
                    "materiality_status": "undisputed",
                    "scope_classification": spec.get("scope_classification", "in_scope"),
                    "blocking_status": spec.get("blocking_status", "blocking"),
                    "lifecycle_state": spec.get("lifecycle_state", "open"),
                    "claim": spec.get("claim", "Something is wrong"),
                    "rationale_or_summary": spec.get("rationale_or_summary", "Because reasons"),
                    "clarification_pending": False,
                }
            )
        )
        sections.append("")
    (run / "run.md").write_text("\n".join(sections), encoding="utf-8")
    return run


class ReportTests(unittest.TestCase):
    def test_in_scope_blocking_finding_renders_blocker_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = _write_run_with_canonical(
                Path(tmp_name),
                [
                    {
                        "canonical_finding_id": "cf-001",
                        "claim": "Bug here",
                        "rationale_or_summary": "Detailed reasoning",
                    },
                    {
                        "canonical_finding_id": "cf-002",
                        "scope_classification": "out_of_scope",
                    },
                    {
                        "canonical_finding_id": "cf-003",
                        "blocking_status": "non_blocking",
                    },
                ],
            )

            body = build_report_skeleton(run, "consensus_reached", "v2")

        # Only the in-scope blocking finding becomes a blocker section.
        self.assertIn("### Blocker 1 — cf-001", body)
        self.assertNotIn("### Blocker 2", body)
        self.assertIn("Bug here", body)
        self.assertIn("Detailed reasoning", body)

    def test_escalated_to_human_emits_escalation_record_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = _write_run_with_canonical(
                Path(tmp_name),
                [{"canonical_finding_id": "cf-001"}],
            )

            body = build_report_skeleton(run, "escalated_to_human", None)
            target = Path(tmp_name) / "report.md"
            target.write_text(body, encoding="utf-8")
            records = parse_records_from_file(target)

        escalations = records_by_type(records, "EscalationRecord")
        self.assertEqual(len(escalations), 1)
        self.assertEqual(escalations[0].data["affected_finding_ids"], ["cf-001"])
        termination = records_by_type(records, "TerminationRecord")
        self.assertEqual(len(termination), 1)
        self.assertEqual(termination[0].data["terminal_condition"], "escalated_to_human")
        self.assertIn("cf-001", termination[0].data["unresolved_finding_ids"])
        self.assertIn("escalation-report-001", termination[0].data["supporting_record_ids"])

    def test_consensus_reached_omits_escalation_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = _write_run_with_canonical(
                Path(tmp_name),
                [{"canonical_finding_id": "cf-001"}],
            )

            body = build_report_skeleton(run, "consensus_reached", "v2")

        self.assertNotIn("## EscalationRecord ", body)
        self.assertIn("final_artifact_version_id_or_null: v2", body)

    def test_final_report_uses_canonical_field_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = _write_run_with_canonical(
                Path(tmp_name),
                [{"canonical_finding_id": "cf-001"}],
            )

            body = build_report_skeleton(run, "consensus_reached", "v3")
            target = Path(tmp_name) / "report.md"
            target.write_text(body, encoding="utf-8")
            records = parse_records_from_file(target)

        final = records_by_type(records, "FinalReport")
        self.assertEqual(len(final), 1)
        self.assertEqual(final[0].data["final_artifact_version_id_or_null"], "v3")
        self.assertNotIn("target_artifact_version_id", final[0].data)

    def test_cmd_report_writes_file_and_overwrite_replaces_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = _write_run_with_canonical(
                Path(tmp_name),
                [{"canonical_finding_id": "cf-001"}],
            )
            args = argparse.Namespace(
                run=str(run),
                terminal_condition="consensus_reached",
                final_artifact_version="v2",
                actor="orchestrator-consensus-tool",
                overwrite=False,
            )

            self.assertEqual(cmd_report(args), 0)
            self.assertTrue((run / "report.md").exists())

            self.assertEqual(cmd_report(args), 1)

            args.overwrite = True
            self.assertEqual(cmd_report(args), 0)


if __name__ == "__main__":
    unittest.main()
