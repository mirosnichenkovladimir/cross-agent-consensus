"""Schema-surface assertions — see DESIGN.md §OperatorApproval record contract (R3)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "skills" / "cross-agent-consensus"
sys.path.insert(0, str(PACKAGE_ROOT))

from cross_agent_consensus.record_schema import (  # noqa: E402
    ENUMS,
    ID_FIELDS,
    KNOWN_RECORD_TYPES,
    REQUIRED_FIELDS,
)


class OperatorApprovalSchemaTests(unittest.TestCase):

    def test_review_budget_contract_and_overrun_decision_are_registered(self) -> None:
        self.assertEqual(
            REQUIRED_FIELDS["ReviewBudget"],
            [
                "review_budget_id",
                "max_launched_review_batches",
                "max_fresh_review_batches",
                "ledger_path",
            ],
        )
        self.assertEqual(ID_FIELDS["ReviewBudget"], "review_budget_id")
        self.assertIn("authorize_review_budget_overrun", ENUMS["decision_type"])

    def test_operator_approval_in_required_fields(self) -> None:
        self.assertIn("OperatorApproval", REQUIRED_FIELDS)
        fields = REQUIRED_FIELDS["OperatorApproval"]
        for required in [
            "operator_approval_id",
            "approved_actors",
            "scope_run_id",
            "scope_round_id",
            "scope_phase",
            "mechanism",
            "operator_identity_or_null",
        ]:
            self.assertIn(required, fields, f"OperatorApproval missing field: {required}")

    def test_operator_approval_in_id_fields(self) -> None:
        self.assertEqual(ID_FIELDS["OperatorApproval"], "operator_approval_id")

    def test_operator_approval_auto_derived_into_known_record_types(self) -> None:
        self.assertIn("OperatorApproval", KNOWN_RECORD_TYPES)

    def test_mechanism_enum_present(self) -> None:
        self.assertIn("mechanism", ENUMS)
        self.assertEqual(ENUMS["mechanism"], {"cli_approved_flag", "policy_unattended"})


if __name__ == "__main__":
    unittest.main()
