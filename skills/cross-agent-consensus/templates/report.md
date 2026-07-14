# Report

## Results

Put human verification result blocks first.

### <NormalizedFinding id>: <finding title>

Status: <resolved/unresolved/etc>
Level: <scope/blocking/materiality/severity>
Found by: <reviewer identities>
Source:
- <RawFinding id>: <location>

Problem:
<one-sentence issue>

Explanation:
<why/how it happens, with causal flow>

Required action:
<what must be fixed>

---

## Summary

- terminal condition:
- reason:
- final artifact version:
- unresolved NormalizedFinding ids:
- validators:
- agent session states:

## Reviewer Stats

### <reviewer identity>

Raw findings:
Normalized:
Discarded:
Blocking:
Non-blocking:
Normalized findings:
Agreed with another reviewer:

## Reviewer Agreement

### <NormalizedFinding id>

Reviewers:
Source raw findings:
Problem:

## Discarded Raw Findings

### <RawFinding id>: <finding title>

Reviewer:
Level:
Reason:

## Validation Evidence

## Agent Invocation Summary

- session states:
- failed or missing agent sessions are not reviewer decisions unless a Review or ReReviewDecision record exists.

## Terminal Outcome

Required terminal output fields:

- run folder path:
- `terminal_condition`:
- `termination_record_id` and `report.md` path:
- `final_artifact_version_id_or_null`:
- final artifact path or null:
- validator status summary and evidence paths:
- agent session summary:
- FinalReport section path or anchor:
- unresolved NormalizedFinding ids:
- backlog location:

## TerminationRecord termination-001
---
record_type: TerminationRecord
schema_version: m2-markdown-2
run_id: <run_id>
actor_identity: <orchestrator_identity>
created_at: <ISO-8601>
termination_record_id: termination-001
terminal_condition: consensus_reached
reason: <termination-reason>
final_artifact_version_id_or_null: <artifact-version-id-or-null>
unresolved_finding_ids: []
supporting_record_ids:
  - <record_id>
---

### Termination Notes

- Consensus predicate result:
- Round limit result:
- Human decision or abort support:

Allowed `terminal_condition` values: `consensus_reached`, `round_limit_reached`, `escalated_to_human`, `aborted`.

## FinalReport final-report-001
---
record_type: FinalReport
schema_version: m2-markdown-2
run_id: <run_id>
actor_identity: <orchestrator_identity>
created_at: <ISO-8601>
final_report_id: final-report-001
termination_record_id: termination-001
terminal_condition: consensus_reached
final_artifact_version_id_or_null: <artifact-version-id-or-null>
validator_status:
  artifact_exists: pass
  review_scope_exists: pass
  review_batch_mode_declared: pass
  final_report_exists: pass
  blocking_findings_have_author_responses: pass
  final_report_unresolved_blockers_declared: pass
  final_report_backlog_separated: pass
unresolved_finding_ids: []
backlog_path: backlog.md
---

### Task

### Participants

### Artifact Versions

### Review Scope And Review Batch Modes

### Findings Summary By State

See the human report sections above.

### Accepted, Fixed, And Verified Blocking Findings

### Rejected Findings And Accepted Rejections

### Disputed Or Escalated Blocking Findings

### Validation Evidence

### Agent Invocation Summary

- session states:
- failed or missing agent sessions are not reviewer decisions unless a Review or ReReviewDecision record exists.

### Non-Blocking, Deferred, And Out-Of-Scope Backlog

### Terminal Outcome
