# Normalization Round <n>

Append NormalizationRecord and NormalizedFinding sections in creation order. Add MaterialityChallenge sections only when materiality is disputed before termination begins.

## NormalizationRecord normalization-round-<n>-001
---
record_type: NormalizationRecord
schema_version: m2-markdown-2
run_id: <run_id>
actor_identity: <orchestrator_identity>
created_at: <ISO-8601>
normalization_record_id: normalization-round-<n>-001
source_raw_finding_ids:
  - <raw_finding_id>
normalizer_identity: <orchestrator_identity>
classifier_identity: <classifier_identity>
materiality: material
scope_classification: in_scope
blocking_status: blocking
rationale: <normalization-rationale>
normalized_finding_id: nf-round-1-001
---

### Normalization Notes

- Merge/split rationale:
- Evidence summary:
- Scope rationale:

## NormalizedFinding nf-round-1-001
---
record_type: NormalizedFinding
schema_version: m2-markdown-2
run_id: <run_id>
actor_identity: <orchestrator_identity>
created_at: <ISO-8601>
normalized_finding_id: nf-round-1-001
target_artifact_version_id: <artifact_version_id>
source_raw_finding_ids:
  - <raw_finding_id>
normalization_record_id: normalization-round-<n>-001
materiality: material
materiality_status: undisputed
scope_classification: in_scope
blocking_status: blocking
lifecycle_state: open
claim: <normalized-claim>
rationale_or_summary: <summary>
clarification_pending: false
---

### Normalized Finding Notes

- Required author action:
- Related records:

Allowed `materiality_status` values: `undisputed`, `disputed_materiality`, `resolved_after_dispute`.
Allowed `lifecycle_state` values: `open`, `accepted`, `fixed`, `verified`, `rejected_by_author`, `rejection_accepted`, `still_valid`, `disputed`, `escalated`, `closed_non_material`.

## MaterialityChallenge materiality-challenge-001
---
record_type: MaterialityChallenge
schema_version: m2-markdown-2
run_id: <run_id>
actor_identity: <challenger_identity>
created_at: <ISO-8601>
materiality_challenge_id: materiality-challenge-001
normalized_finding_id: nf-round-1-001
claimed_materiality: <material-or-non_material-or-other-policy-value>
rationale: <challenge-rationale>
supporting_record_ids:
  - <record_id>
---

### Challenge Notes

- Requested resolver:
- Effect on lifecycle:
