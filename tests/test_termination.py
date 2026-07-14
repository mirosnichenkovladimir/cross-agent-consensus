from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "skills" / "cross-agent-consensus"
sys.path.insert(0, str(PACKAGE_ROOT))

from cross_agent_consensus.models import Record
from cross_agent_consensus.termination import terminal_body
from cross_agent_consensus.validation import check_terminal_records


class TerminationTests(unittest.TestCase):
    def test_terminal_body_starts_with_human_results_report(self) -> None:
        body = terminal_body(
            Path("runs/sample"),
            "consensus_reached",
            "v1",
            "Done.",
            [
                Record(
                    "RawFinding",
                    "raw-finding-001",
                    Path("rounds/round-001/reviews/codex.md"),
                    1,
                    {
                        "raw_finding_id": "raw-finding-001",
                        "reviewer_identity": "codex",
                        "location": "README.md:1",
                        "claim": "Missing terminal condition.",
                        "evidence": "The report omits a terminal condition.",
                        "severity_or_materiality_claim": "high",
                        "blocking_status": "blocking",
                        "suggested_fix_or_null": "Add an explicit terminal condition.",
                    },
                ),
                Record(
                    "NormalizedFinding",
                    "normalized-finding-001",
                    Path("rounds/round-001/normalization.md"),
                    1,
                    {
                        "normalized_finding_id": "normalized-finding-001",
                        "target_artifact_version_id": "v1",
                        "source_raw_finding_ids": ["raw-finding-001"],
                        "normalization_record_id": "normalization-001",
                        "materiality": "material",
                        "materiality_status": "undisputed",
                        "scope_classification": "in_scope",
                        "blocking_status": "blocking",
                        "lifecycle_state": "resolved",
                        "claim": "Missing terminal condition.",
                        "rationale_or_summary": "A human cannot see why the run ended.",
                        "clarification_pending": False,
                    },
                ),
                Record(
                    "ValidationEvidence",
                    "validation",
                    Path("validation.md"),
                    1,
                    {"validator_id": "smoke", "result": "pass"},
                )
            ],
        )

        self.assertTrue(body.startswith("# Report\n\n## Results"))
        self.assertIn("### normalized-finding-001: Missing terminal condition.", body)
        self.assertIn("Problem:\nMissing terminal condition.", body)
        self.assertIn("Explanation:\nA human cannot see why the run ended.", body)
        self.assertIn("Required action:\nAdd an explicit terminal condition.", body)
        self.assertIn("- raw-finding-001: README.md:1", body)
        self.assertIn("## Reviewer Stats", body)
        self.assertIn("### codex\n\nRaw findings: 1\nNormalized: 1\nDiscarded: 0", body)
        self.assertIn("## TerminationRecord termination-001", body)
        self.assertIn("## FinalReport final-report-001", body)
        self.assertIn("terminal_condition: consensus_reached", body)
        self.assertIn("smoke: pass", body)
        self.assertIn("report.md#finalreport-final-report-001", body)

    def test_escalated_to_human_terminal_check_allows_missing_validators_with_escalation(self) -> None:
        termination = Record(
            "TerminationRecord",
            "termination-001",
            Path("report.md"),
            1,
            {
                "termination_record_id": "termination-001",
                "terminal_condition": "escalated_to_human",
                "reason": "Remediation cap reached.",
                "final_artifact_version_id_or_null": "v1",
                "unresolved_finding_ids": ["CXR-001"],
                "supporting_record_ids": ["escalation-001"],
            },
        )
        final_report = Record(
            "FinalReport",
            "final-report-001",
            Path("report.md"),
            20,
            {
                "final_report_id": "final-report-001",
                "termination_record_id": "termination-001",
                "terminal_condition": "escalated_to_human",
                "final_artifact_version_id_or_null": "v1",
                "validator_status": {},
                "unresolved_finding_ids": ["CXR-001"],
                "backlog_path": "backlog.md",
            },
        )
        records = [
            Record(
                "Policy",
                "policy",
                Path("run.md"),
                1,
                {"required_validator_ids": ["smoke"]},
            ),
            Record(
                "EscalationRecord",
                "escalation-001",
                Path("escalations.md"),
                1,
                {
                    "escalation_record_id": "escalation-001",
                    "affected_finding_ids": ["CXR-001"],
                    "reason": "remediation cap reached for unresolved re-review finding(s): CXR-001",
                    "requested_authority": "human",
                },
            ),
            termination,
            final_report,
        ]

        result = check_terminal_records(Path("runs/sample"), records, termination, final_report)

        self.assertTrue(result.ok, result.messages)

    def test_consensus_terminal_check_still_requires_validators(self) -> None:
        termination = Record(
            "TerminationRecord",
            "termination-001",
            Path("report.md"),
            1,
            {
                "termination_record_id": "termination-001",
                "terminal_condition": "consensus_reached",
                "reason": "Done.",
                "final_artifact_version_id_or_null": "v1",
                "unresolved_finding_ids": [],
                "supporting_record_ids": [],
            },
        )
        final_report = Record(
            "FinalReport",
            "final-report-001",
            Path("report.md"),
            20,
            {
                "final_report_id": "final-report-001",
                "termination_record_id": "termination-001",
                "terminal_condition": "consensus_reached",
                "final_artifact_version_id_or_null": "v1",
                "validator_status": {},
                "unresolved_finding_ids": [],
                "backlog_path": "backlog.md",
            },
        )
        records = [
            Record(
                "Policy",
                "policy",
                Path("run.md"),
                1,
                {"required_validator_ids": ["smoke"]},
            ),
            termination,
            final_report,
        ]

        result = check_terminal_records(Path("runs/sample"), records, termination, final_report)

        self.assertFalse(result.ok)
        self.assertIn("required validator is not pass/waived: smoke=missing", result.messages)


if __name__ == "__main__":
    unittest.main()
