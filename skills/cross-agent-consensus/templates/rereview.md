# Re-Review Round <n>: <reviewer_identity>

Use this file for one reviewer in one re-review batch. The reviewer may see linked RawFindings, CanonicalFindings, AuthorResponses, relevant revisions, and ValidationEvidence.

Before creating or invoking another re-review for the same finding and reviewer, verify that existing ReReviewDecision records are complete and that unresolved attempts are below `max_remediation_rounds_per_finding`. At the cap, record an EscalationRecord instead of continuing remediation.

## ReReviewDecision rereview-round-<n>-<reviewer_identity>-001
---
record_type: ReReviewDecision
schema_version: m2-markdown-1
run_id: <run_id>
actor_identity: <reviewer_identity>
created_at: <ISO-8601>
re_review_decision_id: rereview-round-<n>-<reviewer_identity>-001
canonical_finding_id: canonical-finding-001
reviewer_identity: <reviewer_identity>
decision: verified
rationale: <reviewer-rationale>
artifact_version_id_or_null: <artifact-version-id-or-null>
review_batch_id: <review_batch_id>
---

### Decision Notes

- Evidence checked:
- Remaining material risk:
- Direct regressions observed:

Allowed `decision` values: `verified`, `rejection_accepted`, `still_valid`, `disputed`, `needs_human`.
