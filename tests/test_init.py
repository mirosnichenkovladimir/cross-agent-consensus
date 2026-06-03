from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "skills" / "cross-agent-consensus"
sys.path.insert(0, str(PACKAGE_ROOT))

from cross_agent_consensus.init import build_init_files, infer_validators


class InitTests(unittest.TestCase):
    def test_document_consensus_infers_default_validators(self) -> None:
        validators = infer_validators("document-consensus", [])

        self.assertIn("artifact_exists", validators)
        self.assertIn("final_report_exists", validators)

    def test_build_init_files_rejects_participant_collisions(self) -> None:
        args = argparse.Namespace(
            run_root="runs",
            profile="document-consensus",
            validator=[],
            orchestrator="same",
            author="same",
            reviewer=["reviewer"],
            artifact_locator="README.md",
            success_criterion=[],
            task="Do work",
            run_id=None,
            max_fresh_review_rounds=1,
            max_fresh_review_rounds_without_human_approval=2,
            max_remediation_rounds=2,
            material_by_default=[],
            non_blocking_by_default=[],
            escalation_policy="policy",
            waiver_authority=None,
            unattended_invocation=False,
            unattended_scope=[],
            human_supervisor="none",
            review_objective=None,
            in_scope=[],
            out_of_scope=[],
            promotion_policy=None,
            config_resolution=None,
        )

        with self.assertRaisesRegex(ValueError, "orchestrator must be distinct"):
            build_init_files(args, "sample-consensus-001", "2026-06-01T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
