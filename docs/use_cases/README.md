# Consensus Protocol Use Cases

Minimal behavior-covering set. Each use case names only expected protocol outcome.

## UC1: clean review reaches consensus

Given Review Scope, Policy, participants, Review Batch `fresh_review`, and Artifact `v1` exist.
When first-round reviewers independently return no in-scope blocking material findings and pre-final validators pass.
Then the Orchestrator writes a final report, final-report validators pass, and the run terminates `consensus_reached`.

Covers: initialization, first-round reviewer independence, empty findings, validators, final report, consensus predicate.

## UC2: author accepts and fixes a blocker

Given a reviewer raises an in-scope blocking material Raw Finding on `v1`.
When the Orchestrator normalizes it, the Author accepts or partially accepts it, and publishes `v2`.
Then the finding moves `open -> fixed`.
When re-review verifies the fix.
Then it moves `fixed -> verified` and no longer blocks consensus.

Covers: raw/canonical finding, accept/partially_accept, artifact versioning, remediation verification.

## UC3: author rejects and reviewer accepts rejection

Given an in-scope blocking material Canonical Finding is `open`.
When the Author rejects it with rationale and no artifact change.
Then it moves `open -> rejected_by_author`.
When re-review accepts the rejection.
Then it moves `rejected_by_author -> rejection_accepted` and no longer blocks consensus.

Covers: reviewer comments as claims, rejected finding resolution.

## UC4: fix fails, retry budget or round limit stops the run

Given an accepted or partially accepted blocker is revised in `v2`.
When re-review says `still_valid`.
Then the finding remains unresolved and returns to Author phase if budget remains.
When changed areas need a `regression_check`.
Then only remediation-caused regressions may block; unrelated observations are out of scope.
When remediation/fresh-review limits are exhausted and the blocker remains unresolved.
Then the run terminates `round_limit_reached`, unless policy selects terminal human handling.

Covers: still_valid, regression_check, unrelated-observation suppression, unresolved states, limits, terminal precedence.

## UC5: clarification pauses normal response

Given a Canonical Finding is `open` and the Author cannot decide.
When the Author responds `request_clarification`.
Then `clarification_pending=true`, the finding stays `open`, and no normal re-review runs.
When clarification is answered or unavailable.
Then `clarification_pending=false` and the Author must give `accept`, `partially_accept`, or `reject` before re-review.

Covers: clarification sub-loop.

## UC6: scope triage controls what blocks

Given a reviewer reports a finding classified `out_of_scope`, `non_blocking`, `deferred`, or `unclear_scope`.
When it is normalized.
Then non-blocking/out-of-scope/deferred findings are recorded for the final report/backlog, but do not require Author Response or block consensus.
And `unclear_scope` must be triaged before it can block or be dismissed.
When a Human Decision promotes it.
Then `blocking_status=promoted_by_human`; once material/in-scope, it follows the blocker path.

Covers: scope control, unclear-scope triage, deferred reporting, human promotion.

## UC7: materiality dispute blocks until resolved

Given a finding is classified non-material and closed.
When a Materiality Challenge or Human Decision disputes that classification.
Then `materiality_status=disputed_materiality`, lifecycle returns to `open`, and it is treated as material.
When policy, Human Decision, or resolving re-review settles the dispute.
Then `materiality_status=resolved_after_dispute`; only then may it stop blocking.

Covers: materiality challenge, disputed materiality, close/reopen behavior.

## UC8: validation evidence overrides unsupported opinion

Given required validation evidence fails or errors.
When a reviewer says the artifact is acceptable without stronger evidence.
Then validation evidence has higher authority and consensus cannot be declared.
When validation passes or a Human Decision records a waiver with rationale.
Then the validator no longer blocks consensus.

Covers: validators, evidence authority, waiver.

## UC9: multiple re-review decisions are aggregated

Given multiple reviewers re-review the same material Canonical Finding.
When any reviewer says `needs_human`.
Then the finding becomes `escalated`.
When all reviewers give the same resolving decision.
Then the finding resolves as `verified` or `rejection_accepted`.
When all say `still_valid`.
Then it remains `still_valid`.
When decisions differ or include `disputed`.
Then the finding becomes `disputed` and cannot reach consensus without resolution.

Covers: independent re-review aggregation.

## UC10: escalation, human decision, or abort

Given a blocker is `disputed`, marked `needs_human`, conflicts with validation, or cannot be clarified.
When the Orchestrator escalates.
Then an Escalation Record is created and the finding remains unresolved.
When the Human Decision resolves it, requires revision, terminates as human-escalated, or aborts.
Then the Orchestrator resumes at the specified phase or writes the matching Termination/Abort records.

Covers: escalation, human authority, resumption, abort.
