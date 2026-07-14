from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "skills" / "cross-agent-consensus"
sys.path.insert(0, str(PACKAGE_ROOT))

from cross_agent_consensus.markdown_records import frontmatter
from cross_agent_consensus.models import Record
from cross_agent_consensus.record_schema import KNOWN_RECORD_TYPES, REQUIRED_FIELDS
from cross_agent_consensus.validation import (
    check_links,
    check_records,
    remediation_cap_blockers,
    required_field_missing,
    unresolved_blockers,
    unresolved_needs_human,
    validator_status,
)


class ValidationTests(unittest.TestCase):
    def test_current_record_schema_exports_one_post_normalization_finding_name(self) -> None:
        self.assertIn("NormalizedFinding", KNOWN_RECORD_TYPES)
        self.assertNotIn("CanonicalFinding", KNOWN_RECORD_TYPES)
        for record_type in (
            "NormalizationRecord",
            "NormalizedFinding",
            "MaterialityChallenge",
            "AuthorResponse",
            "ClarificationRecord",
            "ReReviewDecision",
        ):
            fields = REQUIRED_FIELDS[record_type]
            self.assertIn("normalized_finding_id", fields)
            self.assertNotIn("canonical_finding_id", fields)

    def test_historical_finding_run_passes_record_and_link_validation_after_decode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = Path(tmp_name) / "sample"
            run.mkdir()
            sections: list[str] = ["# Historical run", ""]
            records = [
                (
                    "Participants participants-001",
                    {
                        "record_type": "Participants",
                        "participants_record_id": "participants-001",
                        "orchestrator_identity": "orchestrator",
                        "author_identity": "author",
                        "reviewer_identities": ["reviewer"],
                        "human_supervisor_identity_or_null": None,
                    },
                ),
                (
                    "ReviewScope scope-001",
                    {
                        "record_type": "ReviewScope",
                        "review_scope_id": "scope-001",
                        "objective": "review",
                        "in_scope": ["all changes"],
                        "out_of_scope": ["none"],
                        "review_modes_allowed": ["fresh_review"],
                        "max_fresh_review_rounds": 1,
                        "max_remediation_rounds_per_finding": 2,
                        "promotion_policy_or_null": None,
                    },
                ),
                (
                    "ReviewBatch batch-001",
                    {
                        "record_type": "ReviewBatch",
                        "review_batch_id": "batch-001",
                        "review_scope_id": "scope-001",
                        "review_mode": "fresh_review",
                        "target_artifact_version_id": "v1",
                        "source_finding_ids": [],
                        "round_id": "round-1",
                    },
                ),
                (
                    "ArtifactVersion v1",
                    {
                        "record_type": "ArtifactVersion",
                        "artifact_version_id": "v1",
                        "predecessor_id_or_null": None,
                        "content_locator": "artifact.md",
                        "content_hash_or_null": None,
                        "produced_by": "author",
                    },
                ),
                (
                    "RawFinding rf-001",
                    {
                        "record_type": "RawFinding",
                        "raw_finding_id": "rf-001",
                        "reviewer_identity": "reviewer",
                        "artifact_version_id": "v1",
                        "review_batch_id": "batch-001",
                        "location": "api.py:10",
                        "claim": "permission check missing",
                        "evidence": "delete path accepts any user",
                        "severity_or_materiality_claim": "material",
                        "scope_classification": "in_scope",
                        "blocking_status": "blocking",
                        "suggested_fix_or_null": "check ownership",
                    },
                ),
                (
                    "NormalizationRecord normalization-001",
                    {
                        "record_type": "NormalizationRecord",
                        "normalization_record_id": "normalization-001",
                        "source_raw_finding_ids": ["rf-001"],
                        "normalizer_identity": "orchestrator",
                        "classifier_identity": "orchestrator",
                        "materiality": "material",
                        "scope_classification": "in_scope",
                        "blocking_status": "blocking",
                        "rationale": "review evidence",
                        "normalized_finding_id": "cf-round-1-001",
                    },
                ),
                (
                    "NormalizedFinding cf-round-1-001",
                    {
                        "record_type": "NormalizedFinding",
                        "normalized_finding_id": "cf-round-1-001",
                        "target_artifact_version_id": "v1",
                        "source_raw_finding_ids": ["rf-001"],
                        "normalization_record_id": "normalization-001",
                        "materiality": "material",
                        "materiality_status": "undisputed",
                        "scope_classification": "in_scope",
                        "blocking_status": "blocking",
                        "lifecycle_state": "open",
                        "claim": "permission check missing",
                        "rationale_or_summary": "review evidence",
                        "clarification_pending": False,
                    },
                ),
            ]
            for heading, record_data in records:
                sections.extend(
                    [
                        f"## {heading}",
                        frontmatter(
                            {
                                **record_data,
                                "schema_version": "m2-markdown-2",
                                "run_id": "sample",
                                "actor_identity": "orchestrator",
                                "created_at": "2026-06-01T00:00:00Z",
                            }
                        ),
                        "",
                    ]
                )
            historical_body = (
                "\n".join(sections)
                .replace("m2-markdown-2", "m2-markdown-1")
                .replace("NormalizedFinding", "CanonicalFinding")
                .replace("normalized_finding_id", "canonical_finding_id")
            )
            (run / "run.md").write_text(historical_body, encoding="utf-8")

            record_result = check_records(run)
            link_result = check_links(run)

        self.assertTrue(record_result.ok, record_result.messages)
        self.assertTrue(link_result.ok, link_result.messages)

    def test_required_field_missing_allows_or_null_none(self) -> None:
        self.assertFalse(required_field_missing({"value_or_null": None}, "value_or_null"))
        self.assertTrue(required_field_missing({"value": None}, "value"))
        self.assertTrue(required_field_missing({"value": "<placeholder>"}, "value"))
        self.assertFalse(required_field_missing({"value": "Require 0 < retries > -1"}, "value"))

    def test_validator_status_uses_latest_seen_result(self) -> None:
        records = [
            Record("ValidationEvidence", "one", Path("validation.md"), 1, {"validator_id": "smoke", "result": "fail"}),
            Record("ValidationEvidence", "two", Path("validation.md"), 2, {"validator_id": "smoke", "result": "pass"}),
        ]

        self.assertEqual(validator_status(records), {"smoke": "pass"})

    def test_unresolved_blockers_filters_non_blocking_findings(self) -> None:
        records = [
            Record(
                "NormalizedFinding",
                "blocking",
                Path("normalization.md"),
                1,
                {
                    "normalized_finding_id": "blocking",
                    "scope_classification": "in_scope",
                    "blocking_status": "blocking",
                    "materiality": "material",
                    "materiality_status": "undisputed",
                    "lifecycle_state": "open",
                },
            ),
            Record(
                "NormalizedFinding",
                "non-blocking",
                Path("normalization.md"),
                2,
                {
                    "normalized_finding_id": "non-blocking",
                    "scope_classification": "in_scope",
                    "blocking_status": "non_blocking",
                    "materiality": "material",
                    "materiality_status": "undisputed",
                    "lifecycle_state": "open",
                },
            ),
        ]

        self.assertEqual(unresolved_blockers(records), ["blocking"])

    def test_latest_rereview_decisions_resolve_blocker(self) -> None:
        records = [
            Record(
                "NormalizedFinding",
                "blocking",
                Path("normalization.md"),
                1,
                {
                    "normalized_finding_id": "blocking",
                    "scope_classification": "in_scope",
                    "blocking_status": "blocking",
                    "materiality": "material",
                    "materiality_status": "undisputed",
                    "lifecycle_state": "open",
                },
            ),
            Record(
                "ReReviewDecision",
                "attempt-1",
                Path("rereviews.md"),
                1,
                {
                    "normalized_finding_id": "blocking",
                    "reviewer_identity": "reviewer-codex",
                    "decision": "still_valid",
                },
            ),
            Record(
                "ReReviewDecision",
                "attempt-2",
                Path("rereviews.md"),
                2,
                {
                    "normalized_finding_id": "blocking",
                    "reviewer_identity": "reviewer-codex",
                    "decision": "verified",
                },
            ),
        ]

        self.assertEqual(unresolved_blockers(records), [])
        self.assertEqual(unresolved_needs_human(records), [])

    def test_rereview_decisions_cannot_resolve_across_review_batches(self) -> None:
        records = [
            Record(
                "NormalizedFinding",
                "blocking",
                Path("normalization.md"),
                1,
                {
                    "normalized_finding_id": "blocking",
                    "scope_classification": "in_scope",
                    "blocking_status": "blocking",
                    "materiality": "material",
                    "materiality_status": "undisputed",
                    "lifecycle_state": "open",
                },
            ),
            Record(
                "ReviewBatch",
                "rb-1",
                Path("round.md"),
                1,
                {"review_batch_id": "rb-1", "expected_reviewer_identities": ["codex", "claude"]},
            ),
            Record(
                "ReviewBatch",
                "rb-2",
                Path("round.md"),
                2,
                {"review_batch_id": "rb-2", "expected_reviewer_identities": ["codex", "claude"]},
            ),
            Record(
                "ReReviewDecision",
                "codex-rb-1",
                Path("rereviews/rb-1/codex.md"),
                1,
                {
                    "normalized_finding_id": "blocking",
                    "review_batch_id": "rb-1",
                    "reviewer_identity": "codex",
                    "decision": "verified",
                },
            ),
            Record(
                "ReReviewDecision",
                "claude-rb-2",
                Path("rereviews/rb-2/claude.md"),
                1,
                {
                    "normalized_finding_id": "blocking",
                    "review_batch_id": "rb-2",
                    "reviewer_identity": "claude",
                    "decision": "verified",
                },
            ),
        ]

        self.assertEqual(unresolved_blockers(records), ["blocking"])
        records.append(
            Record(
                "ReReviewDecision",
                "codex-rb-2",
                Path("rereviews/rb-2/codex.md"),
                1,
                {
                    "normalized_finding_id": "blocking",
                    "review_batch_id": "rb-2",
                    "reviewer_identity": "codex",
                    "decision": "verified",
                },
            )
        )
        self.assertEqual(unresolved_blockers(records), [])

    def test_pending_newer_review_batch_reopens_older_verified_decision(self) -> None:
        records = [
            Record(
                "NormalizedFinding",
                "blocking",
                Path("normalization.md"),
                1,
                {
                    "normalized_finding_id": "blocking",
                    "scope_classification": "in_scope",
                    "blocking_status": "blocking",
                    "materiality": "material",
                    "materiality_status": "undisputed",
                    "lifecycle_state": "open",
                },
            ),
            Record(
                "ReviewBatch",
                "rb-1",
                Path("round.md"),
                1,
                {
                    "review_batch_id": "rb-1",
                    "source_finding_ids": ["blocking"],
                    "expected_reviewer_identities": ["codex"],
                },
            ),
            Record(
                "ReReviewDecision",
                "codex-rb-1",
                Path("rereviews/rb-1/codex.md"),
                1,
                {
                    "normalized_finding_id": "blocking",
                    "review_batch_id": "rb-1",
                    "reviewer_identity": "codex",
                    "decision": "verified",
                },
            ),
        ]
        self.assertEqual(unresolved_blockers(records), [])

        records.append(
            Record(
                "ReviewBatch",
                "rb-2",
                Path("round.md"),
                2,
                {
                    "review_batch_id": "rb-2",
                    "source_finding_ids": ["blocking"],
                    "expected_reviewer_identities": ["codex"],
                },
            )
        )

        self.assertEqual(unresolved_blockers(records), ["blocking"])

    def test_one_unresolved_reviewer_keeps_blocker_open(self) -> None:
        records = [
            Record(
                "NormalizedFinding",
                "blocking",
                Path("normalization.md"),
                1,
                {
                    "normalized_finding_id": "blocking",
                    "scope_classification": "in_scope",
                    "blocking_status": "blocking",
                    "materiality": "material",
                    "materiality_status": "undisputed",
                    "lifecycle_state": "open",
                },
            ),
            Record(
                "ReReviewDecision",
                "codex-verified",
                Path("rereviews.md"),
                1,
                {
                    "normalized_finding_id": "blocking",
                    "reviewer_identity": "reviewer-codex",
                    "decision": "verified",
                },
            ),
            Record(
                "ReReviewDecision",
                "claude-needs-human",
                Path("rereviews.md"),
                2,
                {
                    "normalized_finding_id": "blocking",
                    "reviewer_identity": "reviewer-claude",
                    "decision": "needs_human",
                },
            ),
        ]

        self.assertEqual(unresolved_blockers(records), ["blocking"])
        self.assertEqual(unresolved_needs_human(records), ["blocking"])

    def test_mixed_resolving_decisions_keep_blocker_open(self) -> None:
        records = [
            Record(
                "NormalizedFinding",
                "blocking",
                Path("normalization.md"),
                1,
                {
                    "normalized_finding_id": "blocking",
                    "scope_classification": "in_scope",
                    "blocking_status": "blocking",
                    "materiality": "material",
                    "materiality_status": "undisputed",
                    "lifecycle_state": "open",
                },
            ),
            Record(
                "ReReviewDecision",
                "codex-verified",
                Path("rereviews/codex.md"),
                1,
                {
                    "normalized_finding_id": "blocking",
                    "reviewer_identity": "codex",
                    "decision": "verified",
                },
            ),
            Record(
                "ReReviewDecision",
                "claude-rejected",
                Path("rereviews/claude.md"),
                1,
                {
                    "normalized_finding_id": "blocking",
                    "reviewer_identity": "claude",
                    "decision": "rejection_accepted",
                },
            ),
        ]

        self.assertEqual(unresolved_blockers(records), ["blocking"])

    def test_later_still_valid_decision_reopens_verified_lifecycle(self) -> None:
        records = [
            Record(
                "NormalizedFinding",
                "blocking",
                Path("normalization.md"),
                1,
                {
                    "normalized_finding_id": "blocking",
                    "scope_classification": "in_scope",
                    "blocking_status": "blocking",
                    "materiality": "material",
                    "materiality_status": "undisputed",
                    "lifecycle_state": "verified",
                },
            ),
            Record(
                "ReReviewDecision",
                "later-still-valid",
                Path("rereviews/codex.md"),
                2,
                {
                    "normalized_finding_id": "blocking",
                    "reviewer_identity": "codex",
                    "decision": "still_valid",
                },
            ),
        ]

        self.assertEqual(unresolved_blockers(records), ["blocking"])

    def test_remediation_cap_blocks_unresolved_latest_re_review(self) -> None:
        records = [
            Record(
                "ReviewScope",
                "scope",
                Path("run.md"),
                1,
                {"max_remediation_rounds_per_finding": 2},
            ),
            Record(
                "ReReviewDecision",
                "attempt-1",
                Path("rereviews.md"),
                1,
                {
                    "normalized_finding_id": "CXR-001",
                    "reviewer_identity": "reviewer-codex",
                    "decision": "still_valid",
                },
            ),
            Record(
                "ReReviewDecision",
                "attempt-2",
                Path("rereviews.md"),
                2,
                {
                    "normalized_finding_id": "CXR-001",
                    "reviewer_identity": "reviewer-codex",
                    "decision": "still_valid",
                },
            ),
        ]

        blockers = remediation_cap_blockers(records, ["CXR-001"], "reviewer-codex")

        self.assertEqual(blockers, [("CXR-001", "reviewer-codex", 2, "still_valid", 2)])


    def test_check_records_emits_deprecation_for_field_alias(self) -> None:
        """Records using aliased field names should surface deprecation: warnings in check_records output."""
        with tempfile.TemporaryDirectory() as tmp_name:
            run = Path(tmp_name) / "sample"
            run.mkdir()
            path = run / "raw.md"
            path.write_text(
                "\n".join(
                    [
                        "## RawFinding rf-001",
                        frontmatter(
                            {
                                "record_type": "RawFinding",
                                "schema_version": "m2-markdown-2",
                                "run_id": "sample",
                                "actor_identity": "orchestrator",
                                "created_at": "2026-06-01T00:00:00Z",
                                "raw_finding_id": "rf-001",
                                "reviewer_identity": "rev",
                                "artifact_version_id": "v1",
                                "review_batch_id": "rb-1",
                                "location": "loc",
                                "claim": "x",
                                "evidence": "e",
                                "severity": "high",
                                "scope_classification": "in_scope",
                                "blocking_status": "blocking",
                                "suggested_fix": "do the thing",
                            }
                        ),
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            result = check_records(run)

        deprecation_lines = [msg for msg in result.messages if msg.startswith("deprecation:")]
        self.assertTrue(
            any("RawFinding.severity -> severity_or_materiality_claim" in msg for msg in deprecation_lines),
            f"severity deprecation missing from {deprecation_lines!r}",
        )
        self.assertTrue(
            any("RawFinding.suggested_fix -> suggested_fix_or_null" in msg for msg in deprecation_lines),
            f"suggested_fix deprecation missing from {deprecation_lines!r}",
        )

    def test_check_records_rejects_wrong_required_field_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = Path(tmp_name) / "sample"
            run.mkdir()
            (run / "run.md").write_text(
                "\n".join(
                    [
                        "## TaskBrief task-001",
                        frontmatter(
                            {
                                "record_type": "TaskBrief",
                                "schema_version": "m2-markdown-2",
                                "run_id": "sample",
                                "actor_identity": "orchestrator",
                                "created_at": "2026-06-01T00:00:00Z",
                                "task_brief_id": "task-001",
                                "artifact_locator": "README.md",
                                "objective": True,
                                "success_criteria": ["pass"],
                                "profile": "document-consensus",
                                "human_supervisor_identity_or_null": None,
                            }
                        ),
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            result = check_records(run)

        self.assertFalse(result.ok)
        self.assertTrue(
            any("TaskBrief.objective must be str, got bool" in message for message in result.messages),
            result.messages,
        )


if __name__ == "__main__":
    unittest.main()
