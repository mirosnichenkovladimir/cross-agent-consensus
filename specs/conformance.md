# Conformance Checklist

An implementation is conforming if it satisfies all required checks below and produces an audit trail sufficient to reconstruct the run as defined in `specs/protocol.md` §10.

The numeric references below point at invariants in `specs/protocol.md` §5 and clauses in §6–§9.

## Required (invariants and lifecycle)

### Orchestration and recording

- [ ] The Orchestrator is logically distinct from any Author or Reviewer Agent within a run (§5 inv. 1).
- [ ] Every Orchestrator action that affects protocol state is recorded with actor identity (§5 inv. 2).

### Reviewer isolation and raw findings

- [ ] First-round Reviewer Agents do not see other reviewers' findings before producing their own Raw Findings (§5 inv. 3).
- [ ] Raw Findings are preserved as immutable audit records (§5 inv. 4).
- [ ] Every Raw Finding binds to exactly one Artifact Version (§5 inv. 5).
- [ ] Each Raw Finding classifies scope (`in_scope` / `out_of_scope` / `unclear_scope`) and blocking status (`blocking` / `non_blocking` / `deferred` / `promoted_by_human`) (§6.4).

### Normalized findings and normalization

- [ ] Normalized Findings preserve links to their source Raw Findings (§5 inv. 6).
- [ ] Each Normalized Finding references a Normalization Record that preserves source ids, materiality, scope, blocking status, classifier identity, and rationale (§6.5).
- [ ] Normalization does not delete or rewrite Raw Findings (§6.5).
- [ ] Materiality disputes set `materiality_status=disputed_materiality`; resolution by policy, Human Decision, or aggregated resolving Re-Review Decision sets `materiality_status=resolved_after_dispute` (§4, §6.5).

### Author response and revisions

- [ ] Every in-scope, blocking, material Normalized Finding receives an explicit Author Response (§5 inv. 7).
- [ ] Rejections include reasoning (§5 inv. 8).
- [ ] Reviewer comments are treated as claims, not commands (§5 inv. 9).
- [ ] Reviewers do not directly modify the artifact in the base protocol (§5 inv. 10).
- [ ] Reviewer suggestions are not applied without a recorded Author Response (§5 inv. 12).
- [ ] An author revision made in response to review is accompanied by Author Responses that motivate the change (§5 inv. 11).
- [ ] Every author revision creates a new Artifact Version record (§5 inv. 13).

### Validation evidence

- [ ] Required validators (Task Brief, Policy, or profile) are recorded (§4).
- [ ] Validation Evidence has higher authority than unsupported model opinion (§5 inv. 14).
- [ ] `waived` Validation Evidence identifies the granting policy or human authority (§4).

### Conclusion validation (when scheduled)

- [ ] Author Response for referenced findings waits until validation outputs are captured from every `expected_reviewer_identities` participant, or Policy explicitly records the batch id in `skipped_conclusion_validation_batch_ids` (§5 inv. 17).

### Reviewer identity and CLI evidence

- [ ] Reviewer identity comes from Participants; review focus/lens values are not used as reviewer identities (§5 inv. 18).
- [ ] When ConfigResolution records `reviewer_clis.<reviewer>.command`, that reviewer's RawReviewerOutput is backed by a completed `invoke-agent` session before terminal consensus or round-limit closure (§5 inv. 19).
- [ ] Recorded local ArtifactVersion digests are recomputed before invocation and termination (§5 inv. 20).
- [ ] External CLI approval binds the exact prompt, argv, working directory, and readable local artifact digest (§5 inv. 21).
- [ ] `capture_origin=live_cli` evidence links to the exact completed invocation session (§5 inv. 22).

### Consensus and termination

- [ ] Consensus is not declared while unresolved material findings remain (§5 inv. 15).
- [ ] No in-scope blocking finding has unresolved `materiality_status=disputed_materiality` at consensus (§8.2).
- [ ] Final report explains the terminal outcome and lists Normalized Findings by lifecycle state, separately from non-blocking, deferred, and out-of-scope suggestions (§8.7).
- [ ] Human decisions are binding (§5 inv. 16); `terminate_escalated_to_human` and `abort_run` decisions follow the recording rules in §9.

## Recommended

- [ ] Round limits are explicit and recorded with separate fresh-review and remediation budgets where the profile defines them (§6.1).
- [ ] Escalation triggers and human authority paths are explicit (§9).
- [ ] Non-material findings are retained separately from material findings as `closed_non_material` records (§6.6, §7).
- [ ] Validation Evidence can override unsupported model opinion (§5 inv. 14).
- [ ] The run can be resumed from persisted state.
- [ ] Run-record mutations are serialized and recorded in an append-only event journal.
