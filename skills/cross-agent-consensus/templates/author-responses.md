# Author Responses Round <n>

Append one AuthorResponse for every in-scope blocking material NormalizedFinding. ClarificationRecord sections may be added when a response requests clarification.

## AuthorResponse author-response-round-<n>-001
---
record_type: AuthorResponse
schema_version: m2-markdown-2
run_id: <run_id>
actor_identity: <author_identity>
created_at: <ISO-8601>
author_response_id: author-response-round-<n>-001
normalized_finding_id: nf-round-1-001
response_type: accept
rationale: <author-rationale>
resulting_artifact_version_id_or_null: <artifact-version-id-or-null>
clarification_request_or_null: null
---

### Response Notes

- Planned or completed change:
- Rejected portion, if any:
- New artifact version:

Allowed `response_type` values: `accept`, `reject`, `partially_accept`, `request_clarification`.

## ClarificationRecord clarification-round-<n>-001
---
record_type: ClarificationRecord
schema_version: m2-markdown-2
run_id: <run_id>
actor_identity: <orchestrator_identity>
created_at: <ISO-8601>
clarification_record_id: clarification-round-<n>-001
normalized_finding_id: nf-round-1-001
requested_by: <requester_identity>
responded_by: <responder_identity-or-null>
question: <clarification-question>
answer_or_reason_unavailable: <answer-or-reason-unavailable>
---

### Clarification Notes

- Follow-up AuthorResponse required:
- Deadline or escalation note:
