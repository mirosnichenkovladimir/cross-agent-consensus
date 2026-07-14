from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "skills" / "cross-agent-consensus"
sys.path.insert(0, str(PACKAGE_ROOT))

from cross_agent_consensus.draft_promotion import cmd_promote_draft
from cross_agent_consensus.invocation.process_monitor import cmd_invoke_agent
from cross_agent_consensus.markdown_records import frontmatter
from cross_agent_consensus.records import parse_run_records, records_by_type
from cross_agent_consensus.run_audit import read_run_events
from cross_agent_consensus.validation import check_integrity
from test_integrity_audit import _stage_run
from test_provider_sessions import invoke_args


def promotion_args(run: Path, source: Path, **overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "run": str(run),
        "source_file": str(source),
        "actor": "reviewer",
        "round": "round-1",
        "artifact_version": "v1",
        "review_batch": "review-batch-round-1-fresh_review",
        "validator_id": None,
        "predecessor": None,
        "content_locator": None,
        "source_record": [],
        "allow_manual_source": True,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def finding() -> dict[str, object]:
    return {
        "location": "api/users.py:84",
        "claim": "A caller can delete another account.",
        "evidence": "delete_user() does not compare the target owner.",
        "severity_or_materiality_claim": "high",
        "scope_classification": "in_scope",
        "blocking_status": "blocking",
        "suggested_fix_or_null": "Compare the authenticated subject with the target owner.",
    }


class DraftPromotionTests(unittest.TestCase):
    def test_reviewer_draft_generates_ids_and_removes_exact_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run, _artifact = _stage_run(tmp)
            source = tmp / "reviewer-draft.json"
            source.write_text(
                json.dumps(
                    {
                        "kind": "reviewer_findings",
                        "review_text": "One permission boundary is missing.",
                        "findings": [finding(), finding()],
                    }
                ),
                encoding="utf-8",
            )
            args = promotion_args(run, source)

            self.assertEqual(cmd_promote_draft(args), 0)
            first_records = parse_run_records(run)
            self.assertEqual(cmd_promote_draft(promotion_args(run, source)), 0)
            events = [
                event
                for event in read_run_events(run)
                if event.get("event_type") == "draft_promoted"
            ]

        outputs = records_by_type(first_records, "RawReviewerOutput")
        findings = records_by_type(first_records, "RawFinding")
        self.assertEqual(len(outputs), 1)
        self.assertEqual(len(findings), 1)
        self.assertRegex(str(findings[0].data["raw_finding_id"]), r"^raw-finding-[0-9a-f]{12}-001$")
        self.assertEqual(outputs[0].data["actor_identity"], "orchestrator-draft-finalizer")
        self.assertEqual(outputs[0].data["capture_origin"], "manual_import")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["details"]["promoted_record_ids"], [
            outputs[0].data["raw_output_id"],
            findings[0].data["raw_finding_id"],
        ])

    def test_reviewer_draft_keeps_semantic_duplicates_with_different_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run, _artifact = _stage_run(tmp)
            first = json.dumps(finding(), separators=(",", ":"))
            second = json.dumps(
                dict(reversed(list(finding().items()))), separators=(",", ":")
            )
            source = tmp / "reviewer-draft.json"
            source.write_text(
                '{"kind":"reviewer_findings","review_text":"duplicates","findings":['
                + first
                + ","
                + second
                + "]}",
                encoding="utf-8",
            )

            self.assertEqual(cmd_promote_draft(promotion_args(run, source)), 0)
            findings = records_by_type(parse_run_records(run), "RawFinding")

        self.assertEqual(len(findings), 2)

    def test_worker_cannot_supply_protocol_identity_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run, _artifact = _stage_run(tmp)
            source = tmp / "invalid-draft.json"
            source.write_text(
                json.dumps(
                    {
                        "kind": "reviewer_findings",
                        "run_id": "forged-run",
                        "review_text": "forged",
                        "findings": [],
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(cmd_promote_draft(promotion_args(run, source)), 1)
            records = parse_run_records(run)

        self.assertEqual(records_by_type(records, "RawReviewerOutput"), [])

    def test_duplicate_json_field_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run, _artifact = _stage_run(tmp)
            source = tmp / "duplicate-field.json"
            source.write_text(
                '{"kind":"synthesis","text":"first","text":"second"}',
                encoding="utf-8",
            )

            self.assertEqual(
                cmd_promote_draft(
                    promotion_args(
                        run,
                        source,
                        actor="synthesizer",
                        review_batch=None,
                        source_record=["raw-finding-1"],
                    )
                ),
                1,
            )

    def test_supervised_reviewer_promotion_completes_execution_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run, _artifact = _stage_run(tmp)
            invocation = invoke_args(run, tmp, "draft_reviewer")
            self.assertEqual(cmd_invoke_agent(invocation), 0)
            source = Path(str(invocation.raw_output) + ".final-output.md")

            self.assertEqual(
                cmd_promote_draft(
                    promotion_args(
                        run,
                        source,
                        allow_manual_source=False,
                    )
                ),
                0,
            )
            events = read_run_events(run)
            integrity = check_integrity(run)

        self.assertEqual(events[-2]["event_type"], "execution_attempt_completed")
        self.assertEqual(events[-2]["details"]["receipt_record_type"], "RawReviewerOutput")
        self.assertEqual(events[-1]["event_type"], "draft_promoted")
        self.assertTrue(integrity.ok, integrity.messages)

    def test_retry_recovers_receipt_completion_after_target_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run, _artifact = _stage_run(tmp)
            invocation = invoke_args(run, tmp, "draft_reviewer")
            self.assertEqual(cmd_invoke_agent(invocation), 0)
            source = Path(str(invocation.raw_output) + ".final-output.md")
            args = promotion_args(run, source, allow_manual_source=False)

            with patch(
                "cross_agent_consensus.draft_promotion.complete_attempt_for_receipt_locked",
                side_effect=RuntimeError("simulated receipt append failure"),
            ):
                self.assertEqual(cmd_promote_draft(args), 1)
            self.assertEqual(
                cmd_promote_draft(
                    promotion_args(run, source, allow_manual_source=False)
                ),
                0,
            )
            events = read_run_events(run)

        self.assertEqual(
            [
                event["event_type"]
                for event in events
                if event["event_type"].startswith("execution_attempt_")
            ][-1],
            "execution_attempt_completed",
        )
        self.assertEqual(
            len([event for event in events if event["event_type"] == "draft_promoted"]),
            1,
        )

    def test_supervised_reviewer_cannot_relabel_review_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run, _artifact = _stage_run(tmp)
            invocation = invoke_args(run, tmp, "draft_reviewer")
            self.assertEqual(cmd_invoke_agent(invocation), 0)
            source = Path(str(invocation.raw_output) + ".final-output.md")
            first_batch = records_by_type(parse_run_records(run), "ReviewBatch")[0]
            second_batch_id = "review-batch-round-1-regression_check-alt"
            second_batch = dict(first_batch.data)
            second_batch.update(
                {
                    "review_batch_id": second_batch_id,
                    "review_mode": "regression_check",
                    "created_at": "2026-07-13T00:00:01Z",
                }
            )
            round_file = run / "rounds" / "round-001" / "round.md"
            round_file.write_text(
                round_file.read_text(encoding="utf-8")
                + f"\n## ReviewBatch {second_batch_id}\n"
                + frontmatter(second_batch)
                + "\n",
                encoding="utf-8",
            )

            code = cmd_promote_draft(
                promotion_args(
                    run,
                    source,
                    allow_manual_source=False,
                    review_batch=second_batch_id,
                )
            )

        self.assertEqual(code, 1)

    def test_author_validator_and_synthesis_drafts_have_separate_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run, artifact = _stage_run(tmp)
            author = tmp / "author.json"
            author.write_text(
                json.dumps(
                    {
                        "kind": "author_artifact",
                        "summary": "Document the permission comparison.",
                        "assumptions": ["Account IDs are stable."],
                        "known_limitations": [],
                    }
                ),
                encoding="utf-8",
            )
            validator = tmp / "validator.json"
            validator.write_text(
                json.dumps(
                    {
                        "kind": "validator_output",
                        "result": "pass",
                        "evidence": "The targeted unit test passed.",
                    }
                ),
                encoding="utf-8",
            )
            synthesis = tmp / "synthesis.json"
            synthesis.write_text(
                json.dumps(
                    {"kind": "synthesis", "text": "The duplicate claims describe one defect."}
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                cmd_promote_draft(
                    promotion_args(
                        run,
                        author,
                        actor="author",
                        artifact_version=None,
                        review_batch=None,
                        predecessor="v1",
                        content_locator=str(artifact),
                    )
                ),
                0,
            )
            self.assertEqual(
                cmd_promote_draft(
                    promotion_args(
                        run,
                        validator,
                        actor="validator-tests",
                        review_batch=None,
                        validator_id="tests",
                    )
                ),
                0,
            )
            self.assertEqual(
                cmd_promote_draft(
                    promotion_args(
                        run,
                        synthesis,
                        actor="synthesizer",
                        review_batch=None,
                        source_record=["raw-finding-1", "raw-finding-1", "raw-finding-2"],
                    )
                ),
                0,
            )
            records = parse_run_records(run)
            synthesis_text = next(
                (run / "rounds" / "round-001" / "synthesis").glob("*.md")
            ).read_text(encoding="utf-8")

        self.assertEqual(len(records_by_type(records, "ArtifactVersion")), 2)
        self.assertEqual(len(records_by_type(records, "ValidationEvidence")), 1)
        self.assertEqual(synthesis_text.count("raw-finding-1"), 1)
        self.assertLess(synthesis_text.index("raw-finding-1"), synthesis_text.index("raw-finding-2"))


if __name__ == "__main__":
    unittest.main()
