# Document Consensus Profile

Use this profile for planning documents, design docs, RFCs, architecture notes, protocol drafts, and implementation plans.

## Artifact type

Markdown or plain text document.

## Review dimensions

Reviewer Agents SHOULD inspect:

- clarity;
- completeness;
- internal contradictions;
- missing decisions;
- ungrounded assumptions;
- implementation leakage in normative sections;
- ambiguous human-in-the-loop or escalation rules;
- missing lifecycle or failure paths;
- unnecessary complexity.

## Review scope

Every document-consensus run SHOULD declare a Review Scope before review starts. The scope SHOULD include:

- objective;
- in-scope review dimensions;
- out-of-scope dimensions;
- maximum fresh-review rounds;
- maximum remediation-verification attempts per accepted blocking finding;
- promotion policy for optional suggestions.

If a run does not declare a scope, the default scope is the document's stated objective plus the Review dimensions above. Broad refactoring, polish, examples, and implementation work are out of scope unless explicitly listed.

## Finding classes

Reviewer Agents classify each observation with both materiality and scope/blocking status.

Scope classification:

- `in_scope`: directly affects the declared objective.
- `out_of_scope`: useful maybe, but outside this run.
- `unclear_scope`: reviewer cannot decide; Orchestrator or human must triage before it can block.

Blocking status:

- `blocking`: must be resolved before consensus in this scope.
- `non_blocking`: relevant but does not prevent consensus.
- `deferred`: intentionally recorded for later.
- `promoted_by_human`: was non-blocking/out-of-scope, then human promoted it.

Material and blocking by default:

- missing required section inside scope;
- contradiction that changes in-scope behavior;
- unclear consensus/termination rule inside scope;
- unclear responsibilities between roles inside scope;
- protocol statement that would cause unsafe automation inside scope.

Non-blocking or out-of-scope by default:

- wording preference;
- formatting preference;
- alternative naming with no semantic impact;
- schema completeness beyond the declared objective;
- implementation strategy, runner behavior, or prompt polish unless the scope includes them;
- suggestions outside current scope.

## Review modes

Document-consensus uses four review modes:

- `fresh_review`: search for new in-scope blocking findings.
- `remediation_verification`: check only whether accepted findings were fixed; unrelated observations must not block.
- `regression_check`: inspect changed areas for regressions caused by the fix.
- `scope_triage`: classify observations as blocking/non-blocking/out-of-scope/deferred. When `source_finding_ids` names Canonical Findings, use it as conclusion validation over the normalized superset, not as a fresh review.

A successful remediation verification does not automatically start a new fresh review. Starting another fresh review requires an explicit Orchestrator or Human Decision.

ReviewBatch `review_focus` values are optional emphasis areas for reviewer prompts. They must not replace configured reviewer identities.

## Conclusion validation

After normalization and before Author Response, the Orchestrator SHOULD recall participating reviewers for a `scope_triage` conclusion-validation batch when multiple reviewers produced overlapping or conflicting findings, or when the proposed classification materially affects whether the Author must respond.

The conclusion-validation ReviewBatch records `batch_purpose=conclusion_validation`, `source_finding_ids`, and `expected_reviewer_identities`. The Orchestrator provides each reviewer with the Canonical Finding table, proposed conclusion, rationale, and source Raw Finding ids. Reviewers answer only `agree`, `disagree`, or `needs_human` per Canonical Finding. Every answer must include rationale or argumentation. `disagree` requires a corrected conclusion, and `needs_human` requires the reason human authority is needed. Reviewers must not introduce unrelated fresh findings during this pass.

Author Response remains blocked until every expected reviewer output is captured. If a conclusion-validation pass is intentionally skipped, Policy records the batch id in `skipped_conclusion_validation_batch_ids`.

Confirmed non-material, duplicate, false-positive, deferred, or out-of-scope conclusions move to backlog or audit records and do not require Author Response unless Policy or HumanDecision promotes them. Confirmed valid blockers require Author Response.

## Required validators

For MVP:

- artifact exists;
- Review Scope exists;
- every review has a declared Review Batch mode;
- final report exists;
- all in-scope blocking material findings have author responses;
- final report lists unresolved blocking findings or states none remain;
- final report separately lists non-blocking, deferred, and out-of-scope suggestions.

## Default round limits

- Fresh-review rounds: 1 by default; 2 maximum without explicit human approval.
- Remediation-verification attempts per accepted blocking finding: 2 by default.
- Scope-triage passes: as needed, but they do not authorize new artifact work by themselves.

## Consensus predicate

Consensus is reached for the declared scope when:

- all in-scope blocking material findings are verified fixed, rejection accepted, or explicitly closed non-material;
- no reviewer has active `needs_human` for an in-scope blocking finding;
- non-blocking, deferred, and out-of-scope suggestions are listed separately for human review;
- final report is generated.
