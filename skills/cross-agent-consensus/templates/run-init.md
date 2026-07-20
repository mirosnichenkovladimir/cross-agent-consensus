# Cross-Agent Consensus Run

Fill this file before reviewer work starts. If a required field is unknown, stop and request it from the Human Supervisor or task owner.

## Run Metadata

- `run_id`: `<run_id>`
- `run_root`: `runs/<run_id>/`
- `cross_agent_consensus_version`: `<major.minor.patch>`
- `protocol_version`: `m2-markdown-2`
- `layout_version`: `round-first-1`
- prompt payload root: `rounds/round-001/prompts/`
- raw-output payload root: `rounds/round-001/raw/`
- run id source: `<generated-or-user-supplied>`
- initial artifact version id: `v1`
- review budget id: `review-budget-<run_id>`
- first review batch id: `review-batch-round-1-fresh_review`
- first round path: `rounds/round-001/`
- first review batch path: `rounds/round-001/round.md`
- initial artifact record path: `artifacts/v1.md`

## TaskBrief task-brief-<run_id>
---
record_type: TaskBrief
schema_version: m2-markdown-2
run_id: <run_id>
actor_identity: <orchestrator_identity>
created_at: <ISO-8601>
task_brief_id: task-brief-<run_id>
artifact_locator: <path-or-content-locator>
objective: <objective>
success_criteria:
  - <criterion>
profile: document-consensus
human_supervisor_identity_or_null: <human-supervisor-identity-or-none>
---

### Notes

- Artifact type:
- Known assumptions:
- Questions that must be resolved before review:

## Policy policy-<run_id>
---
record_type: Policy
schema_version: m2-markdown-2
run_id: <run_id>
actor_identity: <orchestrator_identity>
created_at: <ISO-8601>
policy_id: policy-<run_id>
profile: document-consensus
required_validator_ids:
  - artifact_exists
  - review_scope_exists
  - review_batch_mode_declared
  - final_report_exists
  - blocking_findings_have_author_responses
  - final_report_unresolved_blockers_declared
  - final_report_backlog_separated
round_limits:
  max_fresh_review_rounds: 1
  max_fresh_review_rounds_without_human_approval: 2
  max_launched_review_batches: 3
  max_remediation_rounds_per_finding: 2
materiality_rules:
  material_by_default:
    - missing required section inside scope
    - contradiction that changes in-scope behavior
    - unclear consensus or termination rule inside scope
    - unclear responsibilities between roles inside scope
    - unsafe automation statement inside scope
  non_blocking_or_out_of_scope_by_default:
    - wording preference
    - formatting preference
    - naming preference with no semantic impact
    - implementation strategy unless scope includes it
escalation_policy: <escalation-policy>
waiver_authority_or_null: <policy-or-human-decision-or-null>
---

### Policy Notes

- Validator waiver rules:
- Human terminal handling:
- Scope promotion rules:

## Participants participants-<run_id>
---
record_type: Participants
schema_version: m2-markdown-2
run_id: <run_id>
actor_identity: <orchestrator_identity>
created_at: <ISO-8601>
participants_record_id: participants-<run_id>
orchestrator_identity: <orchestrator_identity>
author_identity: <author_identity>
reviewer_identities:
  - <reviewer_identity>
human_supervisor_identity_or_null: <human-supervisor-identity-or-none>
---

### Isolation Notes

- Orchestrator identity is distinct from Author and Reviewers:
- First-round reviewer isolation plan:
- Prompt payload paths prepared before invocation:
- Raw-output capture paths prepared before invocation:
- Runtime/session version notes:

## ReviewScope review-scope-<run_id>
---
record_type: ReviewScope
schema_version: m2-markdown-2
run_id: <run_id>
actor_identity: <orchestrator_identity>
created_at: <ISO-8601>
review_scope_id: review-scope-<run_id>
objective: <objective>
in_scope:
  - clarity
  - completeness
  - internal contradictions
  - missing decisions
  - ungrounded assumptions
  - implementation leakage in normative sections
  - ambiguous human-in-the-loop or escalation rules
  - missing lifecycle or failure paths
  - unnecessary complexity
out_of_scope:
  - broad refactoring unless explicitly listed
  - polish-only wording unless it changes meaning
  - implementation work unless explicitly listed
review_modes_allowed:
  - fresh_review
  - remediation_verification
  - regression_check
  - scope_triage
max_fresh_review_rounds: 1
max_remediation_rounds_per_finding: 2
promotion_policy_or_null: <promotion-policy-or-null>
---

### Scope Confirmation

- Human/objective confirmation:
- In-scope overrides:
- Out-of-scope overrides:
- Round-limit overrides:

## ReviewBudget review-budget-<run_id>
---
record_type: ReviewBudget
schema_version: m2-markdown-2
run_id: <run_id>
actor_identity: <orchestrator_identity>
created_at: <ISO-8601>
review_budget_id: review-budget-<run_id>
max_launched_review_batches: 3
max_fresh_review_batches: 1
ledger_path: ../.cac-review-budgets/review-budget-<run_id>/events.jsonl
---

### Review Budget Notes

- Replacement runs for the same objective reuse this `review_budget_id`.
- Initialization spends no batch; admission immediately before reviewer launch does.

## Required Follow-Up Files

Create these records and directories before review starts:

- `rounds/round-001/prompts/` for exact prompts that will be sent to each actor.
- `rounds/round-001/raw/` for raw host/CLI outputs that are not directly embedded in lifecycle records.
- `rounds/round-001/round.md` from `templates/review-batch.md`, with first `review_mode: fresh_review`.
- `rounds/round-001/normalization.md`, `author-responses.md`, `validation.md`, and `backlog.md`.
- `artifacts/v1.md` from `templates/artifact-version.md`, unless a different initial artifact version id is explicitly recorded.
