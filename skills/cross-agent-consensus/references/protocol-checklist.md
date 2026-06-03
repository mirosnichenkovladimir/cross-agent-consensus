# Cross-Agent Consensus Protocol Checklist

Use this checklist before review starts, after each lifecycle phase, and before terminal output. It is intentionally manual and audit-oriented.

## Initialization

- [ ] Run id follows `<task-slug>-consensus-NNN`, for example `layout-simplification-consensus-001`, or another unique lowercase slug accepted by the operator.
- [ ] `run.md` exists before any reviewer, author, validator, or external agent CLI work starts.
- [ ] `rounds/round-001/prompts/` and `rounds/round-001/raw/` exist before any host/manual invocation that can produce prompt or raw-output evidence.
- [ ] `run.md` contains TaskBrief, Policy, Participants, and ReviewScope record sections.
- [ ] Run metadata records `cross_agent_consensus_version`, `protocol_version`, and `layout_version`.
- [ ] If saved configuration supplied defaults, `run.md` contains a ConfigResolution section with source paths, hashes for present config files, effective consumed values, and diagnostics.
- [ ] Required inputs are recorded: artifact locator, objective, success criteria, profile, scope, participants, round limits, required validators, Human Supervisor identity or `none`.
- [ ] Orchestrator identity is distinct from Author and Reviewer identities.
- [ ] `rounds/round-001/round.md` contains a ReviewBatch section with `review_mode`.
- [ ] Any review focus/lens values are recorded as ReviewBatch `review_focus`, not as reviewer identities.
- [ ] `artifacts/v1.md` or the selected initial ArtifactVersion exists.
- [ ] `document-consensus` defaults are confirmed or overridden before reviewer work starts.

## Reviewer Isolation

- [ ] Exact first-round reviewer prompts are stored under `rounds/round-001/prompts/reviewers/` before reviewer invocation.
- [ ] All same-round reviewer prompts are finalized before invoking the first reviewer CLI in a fresh-review batch.
- [ ] Each first-round reviewer received only TaskBrief, Policy, ReviewScope, ReviewBatch mode, target ArtifactVersion, and role-specific prompt.
- [ ] First-round reviewers did not receive other reviewers' findings before Raw Findings were emitted.
- [ ] Every review file is scoped to one round and one reviewer: `rounds/round-NNN/reviews/<reviewer_identity>.md`.
- [ ] If ConfigResolution lists `reviewer_clis.<reviewer>.command`, that reviewer's output came through `scripts/consensus invoke-agent` and has a completed `rounds/round-NNN/agents/<reviewer>/session-*` session.

## Raw And Canonical Findings

- [ ] Raw reviewer output is retained in a clearly delimited fenced block and is not edited after first capture.
- [ ] If raw output first appeared in host logs, terminal scrollback, or `/tmp`, it was copied into `runs/<run_id>/` before normalization.
- [ ] Every RawFinding section has its own YAML frontmatter block.
- [ ] Every RawFinding binds to exactly one ArtifactVersion through `artifact_version_id`.
- [ ] Every RawFinding records `review_batch_id`, `location`, `claim`, `evidence`, `severity_or_materiality_claim`, `scope_classification`, and `blocking_status`.
- [ ] NormalizationRecord sections preserve all source RawFinding ids.
- [ ] CanonicalFinding sections preserve RawFinding links and the NormalizationRecord id.
- [ ] MaterialityChallenge sections, when present, are attached to the relevant CanonicalFinding and recorded before termination begins.

## Conclusion Validation

- [ ] If a conclusion-validation pass is needed, `rounds/round-NNN/round.md` contains a `scope_triage` ReviewBatch with `batch_purpose=conclusion_validation`, `source_finding_ids` naming Canonical Findings, and `expected_reviewer_identities`.
- [ ] Recalled reviewer prompts state that conclusion validation is not a fresh review.
- [ ] Each recalled reviewer answers only `agree`, `disagree`, or `needs_human` for each listed CanonicalFinding.
- [ ] Every recalled reviewer decision includes rationale or argumentation and evidence references.
- [ ] `disagree` decisions include a corrected conclusion; `needs_human` decisions include the reason human authority is needed.
- [ ] AuthorResponse sections for referenced CanonicalFindings wait until every expected conclusion-validation reviewer output is captured or Policy explicitly skips the batch with `skipped_conclusion_validation_batch_ids`.

## Author Response And Revision

- [ ] Every in-scope blocking material CanonicalFinding has an AuthorResponse.
- [ ] AuthorResponse `response_type` is one of `accept`, `reject`, `partially_accept`, or `request_clarification`.
- [ ] Rejections and partial acceptances include rationale.
- [ ] No reviewer suggestion is silently applied without an AuthorResponse.
- [ ] Every author revision creates a new ArtifactVersion record.
- [ ] Clarification requests have ClarificationRecord sections before normal re-review proceeds.

## Re-Review And Aggregation

- [ ] ReReviewDecision sections reference the CanonicalFinding, reviewer identity, artifact version if applicable, and ReviewBatch.
- [ ] Re-review decisions use only `verified`, `rejection_accepted`, `still_valid`, `disputed`, or `needs_human`.
- [ ] In `remediation_verification`, reviewers evaluated only linked findings, Author Responses, direct revisions, relevant ValidationEvidence, and direct regressions.
- [ ] Aggregation preserves unresolved `still_valid`, `disputed`, and `needs_human` outcomes.
- [ ] Before another re-review prompt, skeleton, or invocation is created, existing ReReviewDecision records are complete, `scripts/consensus status` or equivalent shows attempt counts, and each unresolved finding is still below `max_remediation_rounds_per_finding`.
- [ ] If an unresolved `still_valid`, `disputed`, or `needs_human` outcome reaches the remediation cap, no further re-review is launched; an EscalationRecord is recorded and the run proceeds to HumanDecision, `escalated_to_human`, or another explicit policy decision.

## Scope And Materiality

- [ ] Out-of-scope findings do not block consensus unless Policy or HumanDecision promotes them.
- [ ] Non-blocking and deferred items are retained in round-local `backlog.md`, root `backlog.md`, or the FinalReport backlog section.
- [ ] Disputed materiality is treated as material until resolved by Policy, HumanDecision, or an aggregated resolving ReReviewDecision.

## Validation

- [ ] `rounds/round-NNN/validation.md` records authoritative ValidationEvidence for the round; root `validation.md` records the run-level summary.
- [ ] Every required validator has a ValidationEvidence section.
- [ ] ValidationEvidence `result` is one of `pass`, `fail`, `error`, or `waived`.
- [ ] Failed or error validators block consensus unless a waiver is recorded.
- [ ] Waived validators include `waiver_authority_or_null` and `waiver_rationale_or_null`.
- [ ] Bulky evidence is stored under the active round's `raw/` directory and referenced from `payload_reference`.

## Configuration

- [ ] `config/defaults.yaml` is treated as managed package state.
- [ ] `config/config.local.yaml` is treated as user-owned state and is not listed in `managed-manifest.json`.
- [ ] Project config, when used, is `.cross-agent-consensus.yaml`.
- [ ] Persistent config does not enable unattended invocation or store secrets.
- [ ] Reviewer CLI mappings are explicit argv arrays and still require `invocation-ready`.
- [ ] CLI `--reviewer` was not used to replace configured `participants.reviewers` unless `--allow-reviewer-config-override` was deliberately used and recorded in ConfigResolution diagnostics.

## Escalation, Human Decision, And Abort

- [ ] EscalationRecord sections identify affected findings, reason, and requested authority.
- [ ] HumanDecision sections are recorded before they affect consensus, materiality, validation waiver, terminal state, or artifact revision requirements.
- [ ] AbortRecord sections live in `escalations.md`; terminating aborts are referenced by TerminationRecord.
- [ ] Human run-scope terminal decisions use `affected_finding_ids_or_validator_ids: ["__run_scope__"]`.

## Terminal Outcome

- [ ] `report.md` starts with human-readable result blocks for each CanonicalFinding.
- [ ] Each result block separates `Problem`, `Explanation`, and `Required action`.
- [ ] `report.md` includes reviewer statistics showing what each reviewer found, what was canonicalized, what was discarded, and what was independently agreed.
- [ ] `report.md` contains both TerminationRecord and FinalReport sections after the human-readable report sections.
- [ ] TerminationRecord and FinalReport agree on `terminal_condition`.
- [ ] FinalReport lists validator status, unresolved blockers, and non-blocking/deferred/out-of-scope backlog separately.
- [ ] Consensus is not declared while unresolved in-scope blocking material findings remain.
- [ ] Consensus is not declared unless required validators pass or are waived.
- [ ] `escalated_to_human` termination reports pending or failed validators without requiring them to pass or be waived.
- [ ] FinalReport distinguishes failed agent sessions from completed reviewer decisions.
- [ ] Terminal output reports the run folder, terminal condition, termination record id, final artifact, validator summary, FinalReport anchor, unresolved CanonicalFinding ids, and backlog location.

## Document-Consensus Defaults

Use `profile=document-consensus` by default for `.md`, `.markdown`, `.txt`, and other clearly plain-text artifacts. Request a profile for other artifact types.

Required validators:

- `artifact_exists`;
- `review_scope_exists`;
- `review_batch_mode_declared`;
- `final_report_exists`;
- `blocking_findings_have_author_responses`;
- `final_report_unresolved_blockers_declared`;
- `final_report_backlog_separated`.

Default limits:

- fresh-review rounds: `1`;
- maximum fresh-review rounds without explicit Human Supervisor approval: `2`;
- remediation-verification attempts per accepted blocking finding: `2`.

Default in-scope dimensions when confirmed by the user:

- clarity;
- completeness;
- internal contradictions;
- missing decisions;
- ungrounded assumptions;
- implementation leakage in normative sections;
- ambiguous human-in-the-loop or escalation rules;
- missing lifecycle or failure paths;
- unnecessary complexity.

## Role Contracts

Orchestrator owns run state, record creation, reviewer isolation, raw-output preservation, finding normalization, validation evidence collection, terminal-condition evaluation, and terminal records.

Author owns artifact creation or revision and explicit responses to CanonicalFindings. The Author may accept, reject, partially accept, or request clarification, but must not silently apply reviewer suggestions.

Reviewer owns independent review claims, evidence, scope classification, blocking status, and re-review decisions. Reviewers do not directly modify artifacts.

Human Supervisor owns binding human judgment, validator waivers, materiality resolution, required revisions, accepted rejections, human-escalated termination, and abort decisions.

## Protocol And Use Case Coverage

| Checklist area | Protocol coverage | Use case coverage |
| --- | --- | --- |
| Initialization completeness | Protocol sections 4, 6.1, 10 | UC1 |
| First-round reviewer isolation | Protocol section 5 invariant 3, 6.4 | UC1 |
| Raw and canonical finding audit trail | Protocol sections 4, 5, 6.5, 10 | UC2, UC6 |
| Author response and revision discipline | Protocol sections 5, 6.6, 7 | UC2, UC3, UC5 |
| Re-review and aggregation | Protocol sections 6.7, 7 | UC2, UC3, UC4, UC9 |
| Scope and materiality handling | Protocol sections 6.2, 6.5, 8 | UC6, UC7 |
| Validation evidence authority | Protocol sections 4, 5, 8 | UC1, UC8 |
| Escalation, human decision, abort | Protocol sections 6.8, 9 | UC10 |
| Terminal outcome and final report | Protocol sections 6.8, 8, 10 | UC1, UC4, UC10 |
