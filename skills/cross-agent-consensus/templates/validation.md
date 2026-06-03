# Validation

## Validator Plan

Required validators for `document-consensus`:

| validator_id | target_artifact_version_id | latest_result | evidence_id_or_null |
| --- | --- | --- | --- |
| artifact_exists | <artifact_version_id> | pending | null |
| review_scope_exists | <artifact_version_id> | pending | null |
| review_batch_mode_declared | <artifact_version_id> | pending | null |
| final_report_exists | <artifact_version_id> | pending | null |
| blocking_findings_have_author_responses | <artifact_version_id> | pending | null |
| final_report_unresolved_blockers_declared | <artifact_version_id> | pending | null |
| final_report_backlog_separated | <artifact_version_id> | pending | null |

Consensus requires every required validator to be `pass` or `waived`.

## ValidationEvidence validation-evidence-001
---
record_type: ValidationEvidence
schema_version: m2-markdown-1
run_id: <run_id>
actor_identity: <validator-or-orchestrator_identity>
created_at: <ISO-8601>
validation_evidence_id: validation-evidence-001
validator_id: artifact_exists
target_artifact_version_id: <artifact_version_id>
result: pass
payload_reference: <path-command-output-or-checklist-section>
produced_by: <validator-or-orchestrator_identity>
waiver_authority_or_null: null
waiver_rationale_or_null: null
---

### Evidence Notes

- Check performed:
- Result summary:
- Payload details:

Allowed `result` values: `pass`, `fail`, `error`, `waived`.
