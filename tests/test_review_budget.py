from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cross_agent_consensus.models import Record
from cross_agent_consensus.review_budget import (
    register_review_batch_launch,
    review_budget_event_messages,
    review_budget_status,
)


def record(record_type: str, record_id: str, **data: object) -> Record:
    return Record(record_type, record_id, Path("run.md"), 1, dict(data))


def run_records(
    run: Path,
    *,
    review_budget_id: str = "shared-review-budget",
    decisions: list[Record] | None = None,
) -> list[Record]:
    return [
        record(
            "ReviewBudget",
            review_budget_id,
            run_id=run.name,
            review_budget_id=review_budget_id,
            max_launched_review_batches=3,
            max_fresh_review_batches=1,
            ledger_path=f"../.cac-review-budgets/{review_budget_id}/events.jsonl",
        ),
        record(
            "Participants",
            f"participants-{run.name}",
            human_supervisor_identity_or_null="human-supervisor",
        ),
        *(decisions or []),
    ]


def batch(batch_id: str, mode: str) -> Record:
    return record(
        "ReviewBatch",
        batch_id,
        review_batch_id=batch_id,
        round_id="round-1",
        review_mode=mode,
    )


class ReviewBudgetTests(unittest.TestCase):
    def test_shared_ledger_limits_replacement_runs_to_one_fresh_and_three_total_batches(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            root = Path(tmp_name)
            first_run = root / "first-run"
            replacement_run = root / "replacement-run"
            first_run.mkdir()
            replacement_run.mkdir()
            first_records = run_records(first_run)
            replacement_records = run_records(replacement_run)

            register_review_batch_launch(
                first_run, first_records, batch("fresh-1", "fresh_review")
            )
            with self.assertRaisesRegex(ValueError, "max_fresh_review_batches=1"):
                register_review_batch_launch(
                    replacement_run,
                    replacement_records,
                    batch("fresh-2", "fresh_review"),
                )

            register_review_batch_launch(
                replacement_run,
                replacement_records,
                batch("remediation-1", "remediation_verification"),
            )
            register_review_batch_launch(
                replacement_run,
                replacement_records,
                batch("regression-1", "regression_check"),
            )
            with self.assertRaisesRegex(ValueError, "max_launched_review_batches=3"):
                register_review_batch_launch(
                    replacement_run,
                    replacement_records,
                    batch("remediation-2", "remediation_verification"),
                )

            status = review_budget_status(replacement_run, replacement_records)

        self.assertIsNotNone(status)
        assert status is not None
        self.assertEqual(status.launched_review_batches, 3)
        self.assertEqual(status.launched_fresh_review_batches, 1)
        self.assertEqual(status.remaining_review_batches, 0)

    def test_exact_human_decision_authorizes_only_its_review_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = Path(tmp_name) / "run"
            run.mkdir()
            records = run_records(run)
            for batch_id, mode in (
                ("fresh-1", "fresh_review"),
                ("remediation-1", "remediation_verification"),
                ("regression-1", "regression_check"),
            ):
                register_review_batch_launch(run, records, batch(batch_id, mode))

            decision = record(
                "HumanDecision",
                "authorize-remediation-2",
                decision_type="authorize_review_budget_overrun",
                review_budget_id="shared-review-budget",
                approved_review_batch_id="remediation-2",
                binding_authority="human-supervisor",
                affected_finding_ids_or_validator_ids=["__run_scope__"],
            )
            authorized_records = run_records(run, decisions=[decision])
            with self.assertRaisesRegex(ValueError, "exact HumanDecision"):
                register_review_batch_launch(
                    run,
                    authorized_records,
                    batch("another-batch", "remediation_verification"),
                )

            register_review_batch_launch(
                run,
                authorized_records,
                batch("remediation-2", "remediation_verification"),
            )
            status = review_budget_status(run, authorized_records)

        assert status is not None
        self.assertEqual(status.launched_review_batches, 4)

    def test_hash_chain_detects_ledger_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = Path(tmp_name) / "run"
            run.mkdir()
            records = run_records(run)
            register_review_batch_launch(
                run, records, batch("fresh-1", "fresh_review")
            )
            ledger = (
                run.parent
                / ".cac-review-budgets"
                / "shared-review-budget"
                / "events.jsonl"
            )
            ledger.write_text(
                ledger.read_text(encoding="utf-8").replace(
                    '"review_mode": "fresh_review"',
                    '"review_mode": "regression_check"',
                ),
                encoding="utf-8",
            )

            messages = review_budget_event_messages(run, records)

        self.assertTrue(any("digest changed" in message for message in messages))
        self.assertTrue(any("anchor ledger digest differs" in message for message in messages))


if __name__ == "__main__":
    unittest.main()
