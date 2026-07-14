# Glossary

Terms used in `specs/protocol.md`. See section references for normative definitions.

## Agent roles

- **Orchestrator**: Coordinates rounds, preserves state, enforces policy, and decides when to continue, stop, or escalate. MUST be logically distinct from any Author or Reviewer Agent within a run (invariant 1).
- **Author Agent**: Produces or revises an artifact and responds to findings.
- **Reviewer Agent**: Reviews an artifact version and emits Raw Findings. Reviewers MUST NOT directly modify the artifact in the base protocol (invariant 10).
- **Validator**: Produces external evidence such as tests, lint, schema checks, screenshots, or structural checks. Validation Evidence has higher authority than unsupported model opinion (invariant 14).
- **Human Supervisor**: Resolves escalations and approves final outcomes when required. Human decisions are binding unless an implementation profile defines another governance model (invariant 16).
- **Participant Identity**: Stable name for one Orchestrator, Author, Reviewer, Validator, or Human Supervisor in a run. Changing how CAC invokes the participant does not change this identity.
- **Participant Profile**: Role and instruction set assigned to a Participant Identity. The instructions refine the role but cannot override CAC Policy, ReviewScope, or phase output requirements.
- **Execution Profile**: Versioned invocation settings assigned to a Participant Identity: adapter, argv, optional model and reasoning effort, prompt transport, output mode, resume declaration, and environment-variable allowlist. CAC passes only the listed environment variables to the child CLI.

## Artifacts and policy

- **Artifact**: The work product being produced or reviewed: document, design, code diff, UI, plan, etc.
- **Artifact Version**: Immutable reference to one version of an artifact (`ArtifactVersion` record, §4).
- **Task Brief**: Original task context and success criteria.
- **Policy**: Per-run rules for severity, materiality, round limits, validators, and escalation.
- **Review Scope**: Defines what can block consensus for the run — objective, in/out-of-scope items, review modes allowed, and round budgets (`ReviewScope` record, §4). A finding outside the Review Scope MUST NOT block consensus unless a Human Decision or policy explicitly promotes it.
- **Review Batch**: Declares one review mode and the target Artifact Version (`ReviewBatch` record, §4). Valid `review_mode` values are `fresh_review`, `remediation_verification`, `regression_check`, and `scope_triage`. `review_focus` is prompt lensing, not participant identity (invariant 18).
- **ConfigResolution**: Records the resolved Participant Identity bindings, Participant Profiles, Execution Profiles, commands, source layers, and diagnostics used to initialize a run. A non-manual reviewer Execution Profile requires supervised `invoke-agent` evidence (invariant 19).

## Finding objects

- **Raw Finding**: Immutable reviewer output tied to exactly one Artifact Version (`RawFinding` record, §4; invariants 4–5).
- **Normalized Finding**: Normalized lifecycle object that may aggregate related Raw Findings while preserving links to them (`NormalizedFinding` record, §4; invariant 6).
- **Normalization Record**: Audits the mapping from Raw Findings to a Normalized Finding (`NormalizationRecord` record, §4).
- **Materiality Challenge**: Disputes a Normalized Finding's materiality classification (`MaterialityChallenge` record, §4); may be raised by any participating reviewer, the Orchestrator, or a Human Decision.
- **Author Response**: Author's explicit response to a material in-scope blocking Normalized Finding (`AuthorResponse` record, §4; invariant 7). Response types: `accept`, `reject`, `partially_accept`, `request_clarification`.
- **Clarification Record**: Records a clarification sub-loop opened by `request_clarification` (`ClarificationRecord` record, §4).
- **Re-Review Decision**: Reviewer decision after inspecting Author Response and any revised artifact (`ReReviewDecision` record, §4). Decisions: `verified`, `rejection_accepted`, `still_valid`, `disputed`, `needs_human`.
- **Validation Evidence**: Non-opinion evidence produced by validators (`ValidationEvidence` record, §4). Results: `pass`, `fail`, `error`, `waived`. A `waived` result MUST identify the granting policy or human authority.

## Lifecycle objects

- **Conclusion Validation Batch**: A `scope_triage` Review Batch with `batch_purpose=conclusion_validation`, recalled reviewers, and proposed conclusions (`valid_blocker`, `duplicate`, `non_material`, `out_of_scope`, `false_positive`, `deferred`, `needs_human`, `unclear`). Per-finding decisions are `agree`, `disagree`, or `needs_human` with rationale. See §6.5.1.
- **Escalation Record**: Captures that human handling is needed (`EscalationRecord` record, §4). An open Escalation Record does not by itself terminate a run (§6.8).
- **Human Decision**: A binding decision recorded for the run (`HumanDecision` record, §4). Valid `decision_type` values are `mark_resolved`, `accept_author_rejection`, `require_revision`, `mark_non_material`, `dispute_materiality`, `waive_validator`, `terminate_escalated_to_human`, and `abort_run`.
- **Abort Record**: Created when the Orchestrator, Author, or human supervisor aborts the run (`AbortRecord` record, §4). An aborted run does not imply consensus.
- **Termination Record**: Records the terminal condition and links to supporting records (`TerminationRecord` record, §4).

## Outcomes (terminal conditions, §6.8)

- **consensus_reached**: All in-scope blocking material findings are resolved and the consensus predicate (§8) is satisfied.
- **escalated_to_human**: Either a Human Decision explicitly terminates the run as `escalated_to_human`, or an escalation deadline expires and the escalation policy selects terminal human handling.
- **aborted**: An Abort Record was created; an Abort Record and a Termination Record MUST both exist.
- **round_limit_reached**: The just-completed round equals the round limit and consensus is not reached, with no terminal human-handling path selected.
