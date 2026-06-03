# Round <n>

Place this file at `rounds/round-NNN/round.md`. Do not ask reviewers to work until the active ReviewBatch is recorded.

## ReviewBatch review-batch-round-<n>-<mode>
---
record_type: ReviewBatch
schema_version: m2-markdown-1
run_id: <run_id>
actor_identity: <orchestrator_identity>
created_at: <ISO-8601>
review_batch_id: review-batch-round-<n>-<mode>
review_scope_id: review-scope-<run_id>
review_mode: fresh_review
target_artifact_version_id: v1
source_finding_ids: []
review_focus: []
round_id: round-<n>
round_path: rounds/round-NNN
---

### Dispatch Notes

- Reviewer identities:
- Review focus/lenses:
- Review focus values are prompt lenses only; they are not participant identities.
- Prompt section used:
- Isolation constraints:
- Source findings for non-fresh modes:

Allowed `review_mode` values: `fresh_review`, `remediation_verification`, `regression_check`, `scope_triage`.
