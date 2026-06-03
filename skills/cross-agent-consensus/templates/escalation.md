# Escalations And Human Decisions

Append EscalationRecord, HumanDecision, and AbortRecord sections here. Human decisions must be recorded before they affect consensus, waiver, materiality, revision, or terminal state.

## EscalationRecord escalation-001
---
record_type: EscalationRecord
schema_version: m2-markdown-1
run_id: <run_id>
actor_identity: <orchestrator_identity>
created_at: <ISO-8601>
escalation_record_id: escalation-001
affected_finding_ids:
  - canonical-finding-001
reason: <escalation-reason>
requested_authority: <human-supervisor-or-policy-authority>
---

### Escalation Notes

- Requested decision:
- Deadline:
- Current blocking state:

## HumanDecision human-decision-001
---
record_type: HumanDecision
schema_version: m2-markdown-1
run_id: <run_id>
actor_identity: <human_supervisor_identity>
created_at: <ISO-8601>
human_decision_id: human-decision-001
affected_finding_ids_or_validator_ids:
  - canonical-finding-001
decision_type: mark_resolved
rationale: <human-rationale>
binding_authority: <human_supervisor_identity-or-policy>
requires_new_artifact_version: false
---

### Decision Notes

- Lifecycle effect:
- Validator waiver effect:
- Terminal effect:

Allowed `decision_type` values: `mark_resolved`, `accept_author_rejection`, `require_revision`, `mark_non_material`, `dispute_materiality`, `waive_validator`, `terminate_escalated_to_human`, `abort_run`.

## AbortRecord abort-001
---
record_type: AbortRecord
schema_version: m2-markdown-1
run_id: <run_id>
actor_identity: <orchestrator-author-or-human_identity>
created_at: <ISO-8601>
abort_record_id: abort-001
trigger_actor: <orchestrator-author-or-human_identity>
reason: <abort-reason>
artifact_version_id_or_null: <artifact-version-id-or-null>
unresolved_finding_ids:
  - canonical-finding-001
---

### Abort Notes

- Matching TerminationRecord id:
- Resources unavailable or policy conflict:
