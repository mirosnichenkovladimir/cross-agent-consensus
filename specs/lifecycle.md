# Lifecycle Reference

Companion to `specs/protocol.md`. This file summarises round phases, finding states, and aggregation rules; the normative source is `protocol.md` §6–§9.

## Round phases

Each round walks the phases below in order. A phase MAY be skipped when no work remains for it (e.g. no Author Response required), but MUST NOT be reordered.

1. **Initialization** (first round only) — record Task Brief, Policy, Review Scope, participant roles, validators, initial Artifact Version (if any), and round limits (§6.1).
2. **Author phase** — Author Agent produces or revises an Artifact Version (§6.3). Every revision creates a new `ArtifactVersion` record (inv. 13).
3. **Independent review phase** — each Reviewer Agent emits Raw Findings against the target Artifact Version. First-round reviewers MUST NOT see other reviewers' findings (inv. 3). Each Raw Finding classifies scope and blocking status (§6.4).
4. **Normalization phase** — Raw Findings become Normalized Findings via a Normalization Record. Raw Findings are immutable (inv. 4, §6.5). Materiality challenges MAY be opened here.
5. **Conclusion validation** (optional, §6.5.1) — a `scope_triage` batch with `batch_purpose=conclusion_validation` recalls reviewers to validate the proposed conclusions before Author Response (inv. 17).
6. **Author response phase** — Author responds to every in-scope blocking material Normalized Finding (inv. 7, §6.6). Response types: `accept`, `reject`, `partially_accept`, `request_clarification`.
7. **Optional revision** — if the Author response includes a revision, the Orchestrator returns to the Author phase to record the new Artifact Version, then proceeds to re-review.
8. **Re-review phase** — Reviewer Agents inspect the Author Response and any revised Artifact Version and emit `ReReviewDecision` records (§6.7). Decisions: `verified`, `rejection_accepted`, `still_valid`, `disputed`, `needs_human`.
9. **Termination evaluation** — the Orchestrator evaluates terminal conditions (§6.8) and either declares a terminal outcome or starts another round.

### Sub-loops

- **Clarification sub-loop** — `request_clarification` opens a Clarification Record. The finding remains `open` with `clarification_pending=true`. The sub-loop does not consume a new round unless a profile says otherwise (§6.6).
- **Materiality challenge** — a `MaterialityChallenge` MAY be raised by any participating reviewer, the Orchestrator, or a Human Decision during Normalization, Author Response, or Re-Review (§6.5). It MUST NOT be raised after Termination begins. While unresolved, the finding's `materiality_status=disputed_materiality` and the finding is treated as material.

## Normalized finding lifecycle

States (§7):

- `open` — awaits Author Response, clarification, or further action.
- `accepted` — Author accepts or partially accepts; no resolving Artifact Version recorded yet.
- `fixed` — Author recorded an Artifact Version intended to resolve the finding; pending reviewer verification.
- `verified` — reviewers or a Human Decision confirmed the finding is resolved.
- `rejected_by_author` — Author rejected with reasoning; pending reviewer or human acceptance.
- `rejection_accepted` — reviewers or a Human Decision accepted the rejection.
- `still_valid` — reviewers determine the response or revision does not resolve the finding.
- `disputed` — reviewers disagree, or a reviewer disputes without requesting human handling.
- `escalated` — awaiting or has received human handling.
- `closed_non_material` — recorded but does not block consensus.

### Resolved vs unresolved

A material finding is **resolved** only when it is `verified`, `rejection_accepted`, or `closed_non_material` (by policy or Human Decision). `open`, `accepted`, `fixed`, `rejected_by_author`, `still_valid`, `disputed`, and `escalated` are unresolved for consensus.

### Materiality status

Findings are created with `materiality_status=undisputed`. A `MaterialityChallenge` or Human Decision sets `disputed_materiality`. Resolution by policy, Human Decision, or an aggregated resolving Re-Review Decision sets `resolved_after_dispute` and MUST be traceable to the resolving record.

### Multi-reviewer aggregation

When multiple reviewers re-review the same material Normalized Finding in the same batch, evaluate the rules below in order; the first match determines the next state (§7):

1. If any reviewer records `needs_human`, the next state is `escalated`.
2. If all reviewers record the same resolving decision, the next state is that decision's mapped state.
3. If all reviewers record `still_valid`, the next state is `still_valid`.
4. Any non-unanimous set, or any set containing `disputed`, results in `disputed`.

## Review focus

Review focus/lens values are optional `ReviewBatch` prompt-lensing metadata. They MUST NOT be used as reviewer identities (inv. 18) and do not change participant selection.

## Conclusion validation

After Raw Findings are sealed and normalized, the Orchestrator MAY schedule a conclusion-validation batch before Author Response (§6.5.1). The batch is a `scope_triage` batch with `batch_purpose=conclusion_validation`, `source_finding_ids` listing the Normalized Findings under review, and `expected_reviewer_identities` naming the recalled reviewers.

Proposed conclusions: `valid_blocker`, `duplicate`, `non_material`, `out_of_scope`, `false_positive`, `deferred`, `needs_human`, `unclear`.

Per-finding decisions: `agree`, `disagree`, or `needs_human`. Every decision MUST include rationale; a `disagree` MUST name the corrected conclusion; a `needs_human` MUST explain the ambiguity. Raw validation outputs MUST be captured before any conclusion changes.

Author Response for the referenced findings MUST wait until validation outputs are captured from every `expected_reviewer_identities` participant, or Policy explicitly records the batch id in `skipped_conclusion_validation_batch_ids` (inv. 17).

## Termination

Terminal outcomes (§6.8):

- `consensus_reached` — all in-scope blocking material findings resolved and the consensus predicate (§8) is satisfied.
- `escalated_to_human` — a Human Decision explicitly terminates as `terminate_escalated_to_human`, or an escalation deadline expires and the escalation policy selects terminal human handling, or the round limit is reached with unresolved material findings and the escalation policy provides a human path.
- `aborted` — an Abort Record was created; both an Abort Record and a Termination Record MUST exist.
- `round_limit_reached` — the just-completed round equals the round limit and consensus is not reached, with no terminal human-handling path selected.

An open Escalation Record does not by itself terminate a run. Consensus MAY be declared after the final allowed round if the consensus predicate is satisfied; the round-limit stop is applied only after the consensus check.

## Human decisions and abort

A `HumanDecision` record carries one of: `mark_resolved`, `accept_author_rejection`, `require_revision`, `mark_non_material`, `dispute_materiality`, `waive_validator`, `terminate_escalated_to_human`, `abort_run`. Run-scoped `terminate_escalated_to_human` and `abort_run` decisions set `affected_finding_ids_or_validator_ids=["__run_scope__"]` (§4).

`terminate_escalated_to_human` MUST be paired with a Termination Record. `abort_run` MUST be paired with both an Abort Record and a Termination Record. An aborted run does not imply consensus.

If a Human Decision promotes a finding, the finding's `blocking_status` becomes `promoted_by_human` and the Orchestrator MUST record whether a new Author phase or Review Scope revision is required (§9). If the Human Decision requires a new Artifact Version, the Orchestrator resumes at the Author phase; if it resolves all blockers without a new Artifact Version, the Orchestrator resumes at the consensus check; if it resolves only some blockers, the Orchestrator MUST start a new round at the Author phase for the unresolved findings.
