# Cross-Model Consensus Protocol

Status: draft

## 1. Purpose

The protocol coordinates a Primary Author Agent and one or more Reviewer Agents around an artifact until there is auditable consensus, escalation, abort, or round-limit stop.

The protocol is runtime-neutral. Concrete systems such as CLIs, SDKs, skills, plugins, graph engines, or hosted agents are implementation profiles, not protocol requirements.

## 2. Non-goals

The protocol does not define:

- a specific model provider;
- a specific orchestration framework;
- a required file format;
- a required transport;
- automatic patch application from reviewer comments.

## 3. Normative keywords

`MUST`, `MUST NOT`, `SHOULD`, `SHOULD NOT`, and `MAY` are used as normative terms.

## 4. Protocol records

Every protocol record MUST include a stable identifier, `run_id`, `actor_identity`, and `created_at`, unless the record is an imported external artifact. The type-specific identifiers listed below satisfy the stable identifier requirement. Imported external artifacts MUST include a stable locator.

Minimum record types:

- `ReviewScope`: `review_scope_id`, `objective`, `in_scope`, `out_of_scope`, `review_modes_allowed`, `max_fresh_review_rounds`, `max_remediation_rounds_per_finding`, and optional `promotion_policy`. The Review Scope defines what can block consensus for the run.
- `ReviewBatch`: `review_batch_id`, `review_scope_id`, `review_mode`, `target_artifact_version_id`, and optional `source_finding_ids` and `review_focus`. Valid `review_mode` values are `fresh_review`, `remediation_verification`, `regression_check`, and `scope_triage`. `review_focus` values are prompt lenses, not participant identities. A conclusion-validation batch uses `review_mode=scope_triage`, `batch_purpose=conclusion_validation`, `source_finding_ids` that reference Canonical Findings, and `expected_reviewer_identities` naming the recalled reviewers.
- `ArtifactVersion`: `artifact_version_id`, `predecessor_id_or_null`, `content_locator`, and `content_hash_or_null`. The `artifact_version_id` MUST be unique within the run and stable once recorded.
- `RawFinding`: `raw_finding_id`, `reviewer_identity`, `artifact_version_id`, `review_batch_id`, `location`, `claim`, `evidence`, `severity_or_materiality_claim`, `scope_classification`, `blocking_status`, and optional `suggested_fix`. Valid `scope_classification` values are `in_scope`, `out_of_scope`, and `unclear_scope`. Valid `blocking_status` values are `blocking`, `non_blocking`, `deferred`, and `promoted_by_human`.
- `NormalizationRecord`: `normalization_record_id`, `source_raw_finding_ids`, `normalizer_identity`, `classifier_identity`, `materiality`, `scope_classification`, `blocking_status`, `rationale`, and `created_at`.
- `MaterialityChallenge`: `materiality_challenge_id`, `canonical_finding_id`, `claimed_materiality`, `rationale`, and optional `supporting_record_ids`.
- `CanonicalFinding`: `canonical_finding_id`, `target_artifact_version_id`, `source_raw_finding_ids`, `normalization_record_id`, `materiality`, `materiality_status`, `scope_classification`, `blocking_status`, `lifecycle_state`, `claim`, `rationale_or_summary`, and `clarification_pending`. Valid `materiality_status` values are `undisputed`, `disputed_materiality`, and `resolved_after_dispute`. Findings are created with `undisputed`. A materiality dispute sets `materiality_status=disputed_materiality`. Resolution by policy, Human Decision, or an aggregated resolving Re-Review Decision sets `materiality_status=resolved_after_dispute` and MUST be traceable to the resolving policy, Human Decision, or Re-Review Decision record. `clarification_pending` defaults to `false`.
- `AuthorResponse`: `author_response_id`, `canonical_finding_id`, `response_type`, `rationale`, `resulting_artifact_version_id_or_null`, and optional `clarification_request`.
- `ClarificationRecord`: `clarification_record_id`, `canonical_finding_id`, `requested_by`, `responded_by`, `question`, `answer_or_reason_unavailable`, and `created_at`.
- `ReReviewDecision`: `re_review_decision_id`, `canonical_finding_id`, `reviewer_identity`, `decision`, `rationale`, and `artifact_version_id_or_null`.
- `ValidationEvidence`: `validation_evidence_id`, `validator_id`, `target_artifact_version_id`, `result`, `payload_reference`, `produced_by`, and optional `waiver_authority` and `waiver_rationale`.
- `EscalationRecord`: `escalation_record_id`, `affected_finding_ids`, `reason`, `requested_authority`, and `created_at`.
- `HumanDecision`: `human_decision_id`, `affected_finding_ids_or_validator_ids`, `decision_type`, `rationale`, `binding_authority`, `requires_new_artifact_version`, and `created_at`. Valid `decision_type` values are `mark_resolved`, `accept_author_rejection`, `require_revision`, `mark_non_material`, `dispute_materiality`, `waive_validator`, `terminate_escalated_to_human`, and `abort_run`. For run-scoped `terminate_escalated_to_human` and `abort_run` decisions, `affected_finding_ids_or_validator_ids` MUST be `["__run_scope__"]`. Finding lifecycle state effects are defined in section 7.
- `AbortRecord`: `abort_record_id`, `trigger_actor`, `reason`, `artifact_version_id_or_null`, `unresolved_finding_ids`, and `created_at`.
- `TerminationRecord`: `termination_record_id`, `terminal_condition`, `reason`, `final_artifact_version_id_or_null`, `unresolved_finding_ids`, and references to supporting records.

Validators are profile-defined or policy-defined. The base protocol does not require any validator unless the Task Brief, Policy, or implementation profile declares one required. Validation Evidence results are first-class protocol records. Valid `ValidationEvidence.result` values are `pass`, `fail`, `error`, and `waived`. A `waived` result MUST identify the policy or human authority that granted the waiver.

## 5. Core invariants

1. The Orchestrator MUST be logically distinct from any Author Agent or Reviewer Agent within a run.
2. Every Orchestrator action that affects protocol state MUST be recorded with actor identity.
3. First-round Reviewer Agents MUST NOT see other reviewers' findings before producing their own Raw Findings.
4. Raw Findings MUST be preserved as immutable audit records.
5. Every Raw Finding MUST bind to exactly one Artifact Version.
6. Canonical Findings MAY aggregate multiple Raw Findings but MUST preserve links to them.
7. Every in-scope blocking material Canonical Finding MUST receive an explicit Author Response.
8. A rejected finding MUST include reasoning.
9. Reviewer comments MUST be treated as claims, not commands.
10. Reviewers in the base protocol MUST NOT directly modify the artifact.
11. An author revision made in response to review MUST be accompanied by Author Responses that motivate the change.
12. Reviewer suggestions MUST NOT be applied without a recorded Author Response.
13. Every author revision MUST create a new Artifact Version record.
14. Validation Evidence has higher authority than unsupported model opinion.
15. Consensus MUST NOT be declared while unresolved material findings remain.
16. Human decisions are binding unless an implementation profile defines another governance model.
17. When a conclusion-validation batch is scheduled, Author Response for the referenced Canonical Findings MUST wait until validation outputs are captured from every `expected_reviewer_identities` participant, or Policy explicitly records the batch id in `skipped_conclusion_validation_batch_ids`.
18. Reviewer identity MUST come from Participants. Review focus/lens values MUST NOT be used as reviewer identities.
19. If ConfigResolution records `reviewer_clis.<reviewer>.command`, RawReviewerOutput from that reviewer MUST be backed by a completed `invoke-agent` session before terminal consensus or round-limit closure.

## 6. Round model

A run consists of one or more rounds.

### 6.1 Initialization

The Orchestrator records:

- Task Brief;
- Policy;
- Review Scope;
- participant roles and logical identities;
- required validators, if any;
- initial Artifact Version if one exists;
- round limits, including separate fresh-review and remediation limits where the profile defines them.

### 6.2 Scope and review modes

Every run MUST have a Review Scope before a Reviewer Agent is asked to review an Artifact Version. The Review Scope defines the run objective, what is in scope, what is out of scope, and the round budgets. A finding outside the Review Scope MUST NOT block consensus unless a Human Decision or policy explicitly promotes it.

Review batches MUST declare one review mode:

- `fresh_review`: search for new in-scope findings against the target Artifact Version.
- `remediation_verification`: verify whether previously accepted or partially accepted findings were fixed. This mode MUST NOT introduce unrelated new findings. It MAY report regressions caused by the remediation.
- `regression_check`: inspect changed areas for new in-scope regressions caused by a specific revision.
- `scope_triage`: classify existing observations as in-scope, out-of-scope, blocking, non-blocking, deferred, or promoted.

Starting a new `fresh_review` after remediation is an explicit Orchestrator or Human Decision. It is not implied by successful remediation verification.

### 6.3 Author phase

The Author Agent produces or revises an Artifact Version.

The Author output SHOULD include:

- artifact content or diff reference;
- summary of decisions;
- assumptions;
- known limitations.

### 6.4 Independent review phase

Each Reviewer Agent receives:

- Task Brief;
- Policy;
- Review Scope;
- Review Batch mode;
- target Artifact Version;
- role-specific review instructions.

For first-round review, a Reviewer MUST NOT receive other reviewers' findings.

Reviewers produce Raw Findings. Each Raw Finding MUST classify whether it is in scope for the active Review Scope and whether it is blocking under the active policy. Out-of-scope suggestions MAY be recorded for human review or backlog, but they do not block consensus unless promoted.

### 6.5 Normalization phase

The Orchestrator converts Raw Findings into Canonical Findings.

Normalization MAY merge duplicates, split compound findings, classify materiality, classify scope, assign blocking status, and assign lifecycle state.

Normalization MUST NOT delete or rewrite Raw Findings.

Each Canonical Finding MUST reference a Normalization Record. The Normalization Record MUST preserve the source Raw Finding identifiers, materiality classification, scope classification, blocking status, classifier identity, and rationale.

A `MaterialityChallenge` MAY be created during the Normalization phase after the relevant Canonical Finding exists, during the Author Response phase, or during the Re-Review phase. It MUST NOT be created after the Termination phase begins. Any Reviewer Agent participating in the run MAY challenge a Canonical Finding's materiality classification by creating a `MaterialityChallenge`. The Orchestrator MAY also create a `MaterialityChallenge` when it detects an internal contradiction or unsupported materiality classification. A Human Decision MAY set, dispute, or resolve a finding's materiality. If a `MaterialityChallenge` disputes a classification, or a Human Decision marks materiality as disputed, the Canonical Finding's `materiality_status` MUST become `disputed_materiality`. A finding with `disputed_materiality` MUST be treated as material and MUST NOT transition to `closed_non_material` until resolved by policy or Human Decision.

### 6.5.1 Conclusion validation

After Raw Findings are sealed and normalized, the Orchestrator MAY schedule a conclusion-validation batch before Author Response. This batch uses `review_mode=scope_triage`, sets `batch_purpose=conclusion_validation`, sets `source_finding_ids` to the Canonical Finding ids in the normalized superset, records `expected_reviewer_identities`, and gives reviewers the proposed conclusion table.

Allowed proposed conclusions are `valid_blocker`, `duplicate`, `non_material`, `out_of_scope`, `false_positive`, `deferred`, `needs_human`, and `unclear`. The Orchestrator SHOULD preserve the rationale and source Raw Finding ids used for each proposed conclusion.

Reviewer validation output is not a fresh review. For each referenced Canonical Finding, a reviewer may record `agree`, `disagree`, or `needs_human`. Every reviewer decision MUST include rationale or argumentation and SHOULD include evidence references to Canonical Finding fields or source Raw Finding ids. A disagreement MUST name the corrected conclusion. A `needs_human` decision MUST explain the ambiguity, policy question, or evidence gap that requires human authority. Raw validation outputs MUST be captured before the Orchestrator changes any conclusion based on them.

If all participating reviewers agree, the proposed conclusion is confirmed. If any reviewer marks `needs_human`, the finding becomes human-facing under the run's escalation policy. If reviewers disagree, the Orchestrator either records an updated NormalizationRecord or MaterialityChallenge and asks only for targeted validation, or records the disagreement for Human Supervisor resolution.

### 6.6 Author response phase

The Author Agent responds to every material Canonical Finding whose `scope_classification=in_scope` and whose `blocking_status` is `blocking` or `promoted_by_human`. Profiles MAY require responses for non-blocking or out-of-scope findings, but those findings do not block consensus by default.

Valid response types:

- accept;
- reject;
- partially_accept;
- request_clarification.

If the Author revises the artifact, the revision MUST become a new Artifact Version.

A `request_clarification` response opens a clarification sub-loop. The Orchestrator routes the request to the source Reviewer or other policy-defined authority and records a Clarification Record. The finding remains `open` with `clarification_pending=true`; this sub-loop does not consume a new round unless a profile explicitly says otherwise. The base protocol does not define a wall-clock response deadline. Profiles MAY define a deadline, after which the Orchestrator MUST either escalate or record why the run can continue. After clarification is answered or declared unavailable, the Author MUST provide a non-clarification Author Response before normal re-review proceeds.

Non-material Canonical Findings are recorded for audit as `closed_non_material` on creation and remain `closed_non_material` unless materiality is disputed. The Author MAY respond to them or revise the artifact in response, but that response or revision does not change their lifecycle state unless materiality is disputed. Non-material, non-blocking, deferred, and out-of-scope findings do not block consensus and do not require re-review unless a profile requires it or a Human Decision promotes them.

### 6.7 Re-review phase

Reviewer Agents inspect:

- original Canonical Finding;
- linked Raw Findings;
- Author Response;
- revised Artifact Version if any;
- relevant Validation Evidence.

In re-review, a Reviewer MAY see linked Raw Findings from other reviewers because those links are part of the Canonical Finding. A Reviewer MUST NOT see other reviewers' Re-Review Decisions for the same re-review batch before producing its own decision, unless a profile explicitly disables independent re-review. In `remediation_verification`, the Reviewer MUST limit evaluation to the referenced findings, their Author Responses, and direct regressions introduced by the remediation. Unrelated observations MUST be emitted only as out-of-scope suggestions or omitted.

Valid Re-Review Decisions:

- verified;
- rejection_accepted;
- still_valid;
- disputed;
- needs_human.

### 6.8 Termination phase

The Orchestrator evaluates terminal conditions.

The run terminates with one of:

- consensus_reached;
- escalated_to_human;
- aborted;
- round_limit_reached.

Abort can terminate a run immediately when an Abort Record is created. Otherwise, after each completed round the Orchestrator MUST evaluate consensus before applying the round-limit stop. Consensus MAY be declared after the final allowed round if the consensus predicate is satisfied. If consensus is not reached and the just-completed round equals the round limit, the run MUST terminate as `round_limit_reached`, unless the escalation policy selects terminal human handling for unresolved material findings or open escalations, in which case the run MUST terminate as `escalated_to_human`.

An open Escalation Record does not by itself terminate a run. Apart from the round-limit terminal human handling path above, the run MUST terminate as `escalated_to_human` only when one of these conditions applies:

- a Human Decision explicitly terminates the run as `escalated_to_human`;
- a Policy-defined or profile-defined escalation deadline expires and the escalation policy selects terminal human handling.

Without one of these conditions, the Orchestrator MUST NOT create a Termination Record solely because escalation is open. The run remains active, either awaiting human handling or proceeding with actions permitted by Policy or profile.

If no terminal condition applies and the round limit is not reached, the Orchestrator MAY start another round. A new `fresh_review` round after all blocking in-scope findings are resolved requires an explicit Orchestrator or Human Decision and MUST record why the additional review is still inside the Review Scope.

## 7. Finding lifecycle

Canonical Finding states:

- open;
- accepted;
- fixed;
- verified;
- rejected_by_author;
- rejection_accepted;
- still_valid;
- disputed;
- escalated;
- closed_non_material.

State meanings:

- `open`: the finding awaits an Author Response, clarification, or further action.
- `accepted`: the Author accepts or partially accepts the claim, but no resolving Artifact Version has been recorded yet.
- `fixed`: the Author has recorded an Artifact Version intended to resolve the finding, pending reviewer verification.
- `verified`: reviewers or a Human Decision have confirmed the finding is resolved.
- `rejected_by_author`: the Author rejects the claim with reasoning, pending reviewer or human acceptance.
- `rejection_accepted`: reviewers or a Human Decision accept the Author's rejection.
- `still_valid`: reviewers determine that the response or revision does not resolve the finding.
- `disputed`: reviewers disagree, or a reviewer disputes the Author response without requesting human handling.
- `escalated`: the finding is awaiting or has received human handling.
- `closed_non_material`: the finding is recorded but does not block consensus.

Default lifecycle transitions:

| Event | Prior state | Next state | Notes |
| --- | --- | --- | --- |
| Canonical Finding created as material | none | `open` | Includes findings with `disputed_materiality`. |
| Canonical Finding created as non-material | none | `closed_non_material` | Reopens to `open` if materiality is disputed. |
| `MaterialityChallenge` disputes a non-material classification | `closed_non_material` | `open` | Sets `materiality_status=disputed_materiality`; Author Response is required. |
| `MaterialityChallenge` disputes another materiality classification | any state | unchanged | Sets `materiality_status=disputed_materiality`; the finding is treated as material until policy or Human Decision resolves the classification. |
| Author Response `accept` or `partially_accept` without a resolving Artifact Version | `open`, `still_valid`, `disputed` | `accepted` | Still unresolved. |
| Author Response `accept` or `partially_accept` with a resolving Artifact Version | `open`, `accepted`, `still_valid`, `disputed` | `fixed` | Requires a new Artifact Version. |
| Author Response `reject` | `open`, `still_valid`, `disputed` | `rejected_by_author` | Response MUST include reasoning. |
| Author Response `request_clarification` | `open` | `open` | Sets `clarification_pending=true`. |
| Clarification answered or unavailable | `open` | `open` | Clears `clarification_pending`; Author MUST provide a non-clarification response. |
| Re-Review Decision `verified` | `fixed`, `accepted` | `verified` | Resolves the finding. If `materiality_status=disputed_materiality`, the aggregated resolving decision sets `materiality_status=resolved_after_dispute`. |
| Re-Review Decision `rejection_accepted` | `rejected_by_author` | `rejection_accepted` | Resolves the finding. If `materiality_status=disputed_materiality`, the aggregated resolving decision sets `materiality_status=resolved_after_dispute`. |
| Re-Review Decision `still_valid` | `fixed`, `accepted`, `rejected_by_author` | `still_valid` | Finding remains unresolved. |
| Re-Review Decision `disputed` | any material state | `disputed` | Finding remains unresolved. |
| Re-Review Decision `needs_human` | any material state | `escalated` | Requires an Escalation Record. |
| Human Decision `mark_resolved` | any material state | `verified` | Resolves the finding. If `materiality_status=disputed_materiality`, sets `materiality_status=resolved_after_dispute`. |
| Human Decision `accept_author_rejection` | any material state | `rejection_accepted` | Resolves the finding. If `materiality_status=disputed_materiality`, sets `materiality_status=resolved_after_dispute`. |
| Human Decision `require_revision` | any material state | `still_valid` | Next action is a new Author phase. If `materiality_status=disputed_materiality`, the Human Decision MUST either keep the materiality dispute open with rationale or resolve the dispute by setting `materiality_status=resolved_after_dispute`. |
| Human Decision or policy `mark_non_material` | any state | `closed_non_material` | Requires recorded authority and rationale. If `materiality_status=disputed_materiality`, the decision or policy resolution sets `materiality_status=resolved_after_dispute`. |
| Policy or Human Decision upholds existing materiality classification | any state | unchanged | Requires recorded authority and rationale. If `materiality_status=disputed_materiality`, sets `materiality_status=resolved_after_dispute`. |
| Human Decision `dispute_materiality` | `closed_non_material` | `open` | Sets `materiality_status=disputed_materiality`; Author Response is required. |
| Human Decision `dispute_materiality` | any other state | unchanged | Sets `materiality_status=disputed_materiality`; the finding is treated as material until policy or Human Decision resolves the classification. |

A reviewer MAY record `verified` against an `accepted` finding when no artifact change was required, such as when the finding is resolved by clarification or by an Author Response that satisfies the claim without revision.

When multiple reviewers re-review the same material Canonical Finding in the same batch, the Orchestrator MUST aggregate decisions as follows. The rules are evaluated in order; the first matching rule determines the next state.

1. If any reviewer records `needs_human`, the next state is `escalated`.
2. If all reviewers record the same resolving decision, the next state is that decision's mapped lifecycle state.
3. If all reviewers record `still_valid`, the next state is `still_valid`.
4. Any non-unanimous set of decisions, or any decision set containing `disputed`, results in `disputed`.

A material finding is resolved only when one of these is true:

- it is `verified`;
- it is `rejection_accepted`;
- it is `closed_non_material` due to policy or Human Decision.

`open`, `accepted`, `fixed`, `rejected_by_author`, `still_valid`, `disputed`, and `escalated` are unresolved for consensus.

## 8. Consensus predicate

Default consensus requires:

1. all in-scope blocking material findings are resolved;
2. no in-scope blocking finding has unresolved `materiality_status=disputed_materiality`; every blocking materiality dispute MUST be resolved by policy, Human Decision, or an aggregated resolving Re-Review Decision before consensus;
3. required validators pass or have recorded `waived` Validation Evidence;
4. no participant has an active `needs_human` decision for an in-scope blocking finding;
5. no in-scope blocking material finding remains `disputed` or `escalated`;
6. the round limit has not been exceeded, with consensus checked before the round-limit stop after the final allowed round;
7. the final report lists Canonical Findings by lifecycle state and separately lists non-blocking, deferred, and out-of-scope suggestions.

Profiles MAY define stricter consensus predicates. Profiles MAY NOT make out-of-scope findings block consensus without a recorded Human Decision, policy rule, or scope promotion record.

## 9. Escalation, human decisions, and abort

The escalation policy is the Policy-defined or profile-defined rule set governing escalation triggers, deadlines, human authority, and terminal handling. The base protocol provides the default escalation triggers below.

The Orchestrator SHOULD escalate when:

- a material finding remains disputed after re-review;
- a reviewer marks `needs_human`;
- required validation evidence conflicts with model claims;
- the round limit is reached with unresolved material findings and the escalation policy provides a human path;
- clarification is unavailable and the missing clarification affects material outcome;
- the task or policy is ambiguous in a way that affects material outcome.

Escalation MUST create an Escalation Record. While human handling is pending, affected material findings remain unresolved.

A Human Decision MUST be represented by a Human Decision record. A Human Decision MAY close findings, require another Author phase, waive a validator, mark or dispute a finding's materiality, promote a non-blocking or out-of-scope finding into the active scope, terminate the run as `escalated_to_human`, or abort the run. If a Human Decision promotes a finding, the finding's `blocking_status` becomes `promoted_by_human` and the Orchestrator MUST record whether a new Author phase or Review Scope revision is required. If a Human Decision terminates the run as `escalated_to_human`, its `decision_type` MUST be `terminate_escalated_to_human` and the Orchestrator MUST create a Termination Record. If a Human Decision aborts the run, its `decision_type` MUST be `abort_run` and the Orchestrator MUST create an Abort Record and a Termination Record.

If the Human Decision requires a new Artifact Version, the Orchestrator resumes at the Author phase. If the Human Decision resolves all blockers without requiring a new Artifact Version, the Orchestrator resumes at the consensus check. If the Human Decision resolves only some blockers and leaves any material finding unresolved without requiring a new Artifact Version, the Orchestrator MUST start a new round at the Author phase for the unresolved findings, unless the same Human Decision requires an immediate terminal condition under section 6.8. Consensus MUST NOT be evaluated while material findings remain unresolved.

A run MAY be aborted by the Orchestrator, Author, or human supervisor when the task is withdrawn, policy forbids continuation, required resources are unavailable, or an unrecoverable protocol error prevents an auditable outcome. A Reviewer MAY request abort through escalation. Aborting MUST create an Abort Record and a Termination Record. An aborted run does not imply consensus.

## 10. Conformance

A conforming implementation MUST satisfy the invariants in section 5 and produce an audit trail sufficient to reconstruct:

- which artifact version was reviewed;
- which Review Scope and Review Batch mode governed each review;
- which reviewer raised each finding;
- how findings were normalized, including scope classification and blocking status;
- how the author responded;
- how reviewers re-reviewed responses;
- what validation evidence was considered;
- why the run terminated.

A conforming audit trail MUST include the protocol records from section 4 when applicable to the run. It MAY include additional profile-specific records, but those records MUST NOT replace the minimum records required by this protocol.
