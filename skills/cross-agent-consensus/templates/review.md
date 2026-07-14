# Review Round <n>: <reviewer_identity>

Place this file at `rounds/round-NNN/reviews/<reviewer_identity>.md`. Preserve raw output after capture. Append RawFinding sections without rewriting the raw block. If the raw output was first captured outside this file, copy it into this round folder first and record the copied path below.

## RawReviewerOutput raw-output-round-<n>-<reviewer_identity>
---
record_type: RawReviewerOutput
schema_version: m2-markdown-2
run_id: <run_id>
actor_identity: <orchestrator_identity>
created_at: <ISO-8601>
raw_output_id: raw-output-round-<n>-<reviewer_identity>
reviewer_identity: <reviewer_identity>
review_batch_id: <review_batch_id>
artifact_version_id: <artifact_version_id>
raw_finding_ids:
  - raw-finding-round-<n>-<reviewer_identity>-001
is_first_round_independent: true
---

### Immutable Raw Reviewer Output

- prompt_payload_path: `rounds/round-NNN/prompts/reviewers/<reviewer_identity>.md`
- raw_payload_path_or_embedded: `rounds/round-NNN/reviews/<reviewer_identity>.md#immutable-raw-reviewer-output`

Do not edit this fenced block after first capture.

```text
<paste reviewer output verbatim>
```

## RawFinding raw-finding-round-<n>-<reviewer_identity>-001
---
record_type: RawFinding
schema_version: m2-markdown-2
run_id: <run_id>
actor_identity: <orchestrator_identity>
created_at: <ISO-8601>
raw_finding_id: raw-finding-round-<n>-<reviewer_identity>-001
reviewer_identity: <reviewer_identity>
artifact_version_id: <artifact_version_id>
review_batch_id: <review_batch_id>
location: <section-or-line>
claim: <claim>
evidence: <evidence>
severity_or_materiality_claim: <blocker-important-minor-taste-question-or-materiality-claim>
scope_classification: in_scope
blocking_status: blocking
suggested_fix_or_null: <suggested-fix-or-null>
---

### Finding Notes

- Scope reason:
- Confidence:
- Related raw local id:

Allowed `scope_classification` values: `in_scope`, `out_of_scope`, `unclear_scope`.
Allowed `blocking_status` values: `blocking`, `non_blocking`, `deferred`, `promoted_by_human`.
