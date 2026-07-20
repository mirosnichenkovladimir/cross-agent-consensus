# Cross-Agent Consensus Record Contract

M2 records are Markdown files or auditable Markdown sections inside lifecycle-grouped files. Frontmatter is authoritative for protocol fields. Body text may explain, tabulate, or preserve raw output, but must not contradict frontmatter.

## Common Frontmatter

Every record file or record section must include:

```yaml
record_type: <record type>
schema_version: m2-markdown-2
run_id: <run id>
actor_identity: <actor identity that created the record>
created_at: <ISO-8601 timestamp>
```

Every record also needs a stable type-specific id. Actor identities are lowercase kebab-case and stable for the run, for example `orchestrator-hermes-agent`, `author-codex-cli-gpt-5-5`, `reviewer-claude-code-2-1-104`, or `human-supervisor-user`.

## Package And Layout Versioning

The installed package version is strict semantic `MAJOR.MINOR.PATCH`; `scripts/consensus --version` prints it. New `run.md` metadata records:

- `cross_agent_consensus_version`: installed package version;
- `protocol_version`: record schema family, currently `m2-markdown-2`;
- `layout_version`: run tree contract, currently `round-first-1`.

Individual protocol records keep `schema_version: m2-markdown-2`.

### Finding-name compatibility

Current records use only `NormalizedFinding` and `normalized_finding_id`.
The load boundary accepts `CanonicalFinding` and `canonical_finding_id` only
from historical `m2-markdown-1` records, preserves the identifier value, and
returns the current record model. A current-schema record containing either
historical token is invalid. A run containing both historical and current
finding records is invalid; migrate the whole run boundary instead of mixing
the two contracts.

## Multi-Record Files

Grouped Markdown files may contain multiple protocol records when that improves manual usability and preserves auditability. Each record section must have:

- a stable heading containing record type and record id;
- its own `---`-delimited YAML frontmatter block;
- a unique record id within the run;
- required type-specific frontmatter;
- cross-reference ids instead of relying on directory position.

Grouping is allowed for append-only lifecycle ledgers and same-actor/same-round bundles. Grouping is not allowed when separation preserves a version boundary, reviewer isolation, terminal audit visibility, or bulky/non-Markdown evidence.

For grouped files, put any summary or plan section first, then record sections in monotonic `created_at` order. Append new record sections; do not reorder existing sections after other records reference them. If a manual correction is needed, append a corrective record section instead of rewriting older record sections.

## Run Folder Layout

New runs use the round-first layout. The root contains run-wide records and summaries; every round has a self-contained folder for prompts, raw outputs, review records, normalization, author responses, rereviews, validation evidence, and round-local backlog.

```text
runs/<run_id>/
  run.md
  artifacts/
    <artifact_version_id>.md
  rounds/
    round-001/
      round.md
      prompts/
        author.md
        reviewers/
          <reviewer_identity>.md
        validators/
          <validator_id>.md
      raw/
        author.out
        reviewers/
          <reviewer_identity>.out
        validators/
          <validator_id>.out
      reviews/
        <reviewer_identity>.md
      normalization.md
      author-responses.md
      rereviews/
        <reviewer_identity>.md
      validation.md
      backlog.md
  validation.md
  escalations.md
  report.md
  backlog.md
```

The run folder is a protocol artifact required by this package. It must exist before any Author/Reviewer/validator invocation. Exact prompts and raw host/CLI output must be copied into the run folder and referenced from protocol records; host scratch locations such as `/tmp` are not recorded evidence.

`rounds/round-NNN/reviews/<reviewer_identity>.md` preserves immutable raw reviewer output and that reviewer's RawFinding sections for that round. If the same reviewer is recalled for another ReviewBatch in the same round, use a qualified name such as `rounds/round-NNN/reviews/<reviewer_identity>-<review_batch_id>.md` so the earlier review remains immutable. The raw output must be copied into a clearly delimited fenced block and never rewritten after first capture.

`report.md` is the terminal human verification artifact. It starts with per-finding result blocks that separate `Problem`, `Explanation`, and `Required action`, followed by reviewer statistics, agreement/discarded summaries, validation evidence, and terminal outcome. It then contains both TerminationRecord and FinalReport sections for protocol validation. Root `backlog.md` summarizes non-blocking, deferred, and out-of-scope findings or suggestions across the run. Large command outputs, binaries, screenshots, or other bulky evidence go under the active round's `raw/` directory and are referenced from record frontmatter.

Existing ledger-layout runs are supported for reading and validation during the compatibility period:

```text
runs/<run_id>/
  init.md
  review-batches.md
  artifacts/
  reviews/
  normalization/
  author-responses/
  rereviews/
  validation.md
  payloads/
  escalations.md
  report.md
  backlog.md
```

## Record Mapping

| Protocol record | Round-first path |
| --- | --- |
| TaskBrief | `run.md` section |
| Policy | `run.md` section |
| Participants | `run.md` section |
| ReviewScope | `run.md` section |
| ReviewBudget | `run.md` section; shared ledger at the record's `ledger_path` |
| ReviewBatch | `rounds/round-NNN/round.md` section |
| ArtifactVersion | `artifacts/<artifact_version_id>.md` |
| RawReviewerOutput | `rounds/round-NNN/reviews/<reviewer_identity>.md` section |
| RawFinding | `rounds/round-NNN/reviews/<reviewer_identity>.md` sections |
| NormalizationRecord | `rounds/round-NNN/normalization.md` section |
| NormalizedFinding | `rounds/round-NNN/normalization.md` section |
| MaterialityChallenge | `rounds/round-NNN/normalization.md` section |
| AuthorResponse | `rounds/round-NNN/author-responses.md` section |
| ClarificationRecord | `rounds/round-NNN/author-responses.md` section |
| ReReviewDecision | `rounds/round-NNN/rereviews/<reviewer_identity>.md` section |
| ValidationEvidence | `rounds/round-NNN/validation.md` section |
| EscalationRecord | `escalations.md` section |
| HumanDecision | `escalations.md` section |
| AbortRecord | `escalations.md` section |
| TerminationRecord | `report.md` section |
| FinalReport | `report.md` section |
| Backlog / non-blocking deferred output | `rounds/round-NNN/backlog.md` and root `backlog.md` |
| ConfigResolution | `run.md` section |

## Type-Specific Frontmatter

| Record type | Required fields beyond common fields |
| --- | --- |
| `TaskBrief` | `task_brief_id`, `artifact_locator`, `objective`, `success_criteria`, `profile`, `human_supervisor_identity_or_null` |
| `Policy` | `policy_id`, `profile`, `required_validator_ids`, `round_limits`, `materiality_rules`, `escalation_policy`, `waiver_authority_or_null` |
| `Participants` | `participants_record_id`, `orchestrator_identity`, `author_identity`, `reviewer_identities`, `human_supervisor_identity_or_null` |
| `ReviewScope` | `review_scope_id`, `objective`, `in_scope`, `out_of_scope`, `review_modes_allowed`, `max_fresh_review_rounds`, `max_remediation_rounds_per_finding`, `promotion_policy_or_null` |
| `ReviewBudget` | `review_budget_id`, `max_launched_review_batches`, `max_fresh_review_batches`, `ledger_path` |
| `ReviewBatch` | `review_batch_id`, `review_scope_id`, `review_mode`, `target_artifact_version_id`, `source_finding_ids`, `round_id` |
| `ArtifactVersion` | `artifact_version_id`, `predecessor_id_or_null`, `content_locator`, `content_hash_or_null`, `produced_by`; CLI-created local records also use `content_locator_base_or_null` |
| `RawReviewerOutput` | `raw_output_id`, `reviewer_identity`, `review_batch_id`, `artifact_version_id`, `raw_finding_ids`, `is_first_round_independent`; CLI capture also records `raw_payload_path`, `raw_payload_sha256`, `capture_origin`, `session_id_or_null`, `session_path_or_null`, `prompt_sha256_or_null`, `session_exit_sha256_or_null` |
| `RawFinding` | `raw_finding_id`, `reviewer_identity`, `artifact_version_id`, `review_batch_id`, `location`, `claim`, `evidence`, `severity_or_materiality_claim`, `scope_classification`, `blocking_status`, `suggested_fix_or_null` |
| `NormalizationRecord` | `normalization_record_id`, `source_raw_finding_ids`, `normalizer_identity`, `classifier_identity`, `materiality`, `scope_classification`, `blocking_status`, `rationale`, `normalized_finding_id` |
| `NormalizedFinding` | `normalized_finding_id`, `target_artifact_version_id`, `source_raw_finding_ids`, `normalization_record_id`, `materiality`, `materiality_status`, `scope_classification`, `blocking_status`, `lifecycle_state`, `claim`, `rationale_or_summary`, `clarification_pending` |
| `MaterialityChallenge` | `materiality_challenge_id`, `normalized_finding_id`, `claimed_materiality`, `rationale`, `supporting_record_ids` |
| `AuthorResponse` | `author_response_id`, `normalized_finding_id`, `response_type`, `rationale`, `resulting_artifact_version_id_or_null`, `clarification_request_or_null` |
| `ClarificationRecord` | `clarification_record_id`, `normalized_finding_id`, `requested_by`, `responded_by`, `question`, `answer_or_reason_unavailable` |
| `ReReviewDecision` | `re_review_decision_id`, `normalized_finding_id`, `reviewer_identity`, `decision`, `rationale`, `artifact_version_id_or_null`, `review_batch_id` |
| `ValidationEvidence` | `validation_evidence_id`, `validator_id`, `target_artifact_version_id`, `result`, `payload_reference`, `produced_by`, `waiver_authority_or_null`, `waiver_rationale_or_null`; CLI capture also records `payload_sha256`, `capture_origin`, `session_id_or_null`, `session_path_or_null`, `prompt_sha256_or_null`, `session_exit_sha256_or_null` |
| `EscalationRecord` | `escalation_record_id`, `affected_finding_ids`, `reason`, `requested_authority` |
| `HumanDecision` | `human_decision_id`, `affected_finding_ids_or_validator_ids`, `decision_type`, `rationale`, `binding_authority`, `requires_new_artifact_version` |
| `AbortRecord` | `abort_record_id`, `trigger_actor`, `reason`, `artifact_version_id_or_null`, `unresolved_finding_ids` |
| `TerminationRecord` | `termination_record_id`, `terminal_condition`, `reason`, `final_artifact_version_id_or_null`, `unresolved_finding_ids`, `supporting_record_ids` |
| `FinalReport` | `final_report_id`, `termination_record_id`, `terminal_condition`, `final_artifact_version_id_or_null`, `validator_status`, `unresolved_finding_ids`, `backlog_path` |
| `ConfigResolution` | `config_resolution_id`, `config_schema_version`, `sources`, `effective_values`, `resolved_participant_identities`, `resolved_execution_profiles`, `diagnostics`, `redactions` |
| `OperatorApproval` | `operator_approval_id`, `approved_actors`, `scope_run_id`, `scope_round_id`, `scope_phase`, `mechanism`, `operator_identity_or_null`; current CLI-created approvals also record `approval_binding_version: exact-inputs-2` and `approved_invocations` keyed by `participant_identity`, `participant_profile_id`, and `execution_profile_id`; resumed bindings add the provider-session entry and provider identifier |

`ReviewBudget` is shared across replacement runs that reuse its ID. The ledger counts an exact ReviewBatch once per run, permits three launched batches and one fresh batch by default, and stores a hash chain plus digest anchor. An overrun requires a `HumanDecision` whose `decision_type` is `authorize_review_budget_overrun`, `review_budget_id` and `approved_review_batch_id` match exactly, `binding_authority` names the recorded Human Supervisor, and `affected_finding_ids_or_validator_ids` is exactly `["__run_scope__"]`.

ReviewBatch sections may use optional frontmatter `review_focus` to record review lenses or emphasis areas. `review_focus` never changes Participants and must not be used as `reviewer_identity`.

Conclusion-validation ReviewBatch sections use optional frontmatter `batch_purpose: conclusion_validation` and `expected_reviewer_identities` to name the recalled reviewers. A Policy section may use optional `skipped_conclusion_validation_batch_ids` to record conclusion-validation batches intentionally skipped by policy authority.

When a ConfigResolution section binds a reviewer Participant Identity to a non-manual Execution Profile with argv, RawReviewerOutput from that reviewer is only terminally valid when the active round also contains a completed `rounds/round-NNN/agents/<reviewer>/session-*` invocation session for phase `reviewer`.

`capture_origin` is one of `live_cli`, `manual_import`, `host_subagent`, or
`stdin`. `live_cli` requires `session_id_or_null`, `session_path_or_null`, and
`prompt_sha256_or_null`, and `session_exit_sha256_or_null` to identify the
completed invocation. `exit.json` binds the digests of `invocation.json`,
`command.json`, `prompt.md`, stdout, stderr, copied raw output, and extracted
final output. The command digest and working directory must match an
`approved_invocations` binding for the same Participant Identity, Participant
Profile, Execution Profile, phase, prompt, and ArtifactVersion.

## Run Mutation Journal

CLI-created runs append successful protocol mutations to root `events.jsonl`.
New journals use schema `cross-agent-consensus-run-event-2`. Each JSON line
records a monotonic `sequence`, `created_at`, `run_id`, `actor_identity`,
`event_type`, `phase_before`, `phase_after`, command-specific `details`, the
previous event digest, and its own digest. Root `.cac-events-anchor.json`
records the event count, last event digest, and full `events.jsonl` digest.
Validation requires `run_initialized`, adjacent phase continuity, a matching
anchor, and agreement between the last event phase and the phase derived from
protocol records. Version-1 journals remain readable for runs whose recorded
package version predates 0.10.0 and which have no version-2 anchor. A mutable
event `schema_version` value alone cannot select legacy validation.

For resumable Codex, Claude, Hermes, and Kimi adapters, `provider_session_captured` binds a
provider conversation identifier to the distinct CAC `session-NNN`, execution
attempt, ParticipantIdentity, ParticipantProfile, ExecutionProfile, phase,
ArtifactVersion lineage root, definition digest, prompt digest, and effective
argv digest. A resumed entry names its earlier
`predecessor_provider_session_entry_id_or_null`. Validation rejects a
predecessor owned by another ParticipantIdentity or using another role,
ExecutionProfile, adapter, run, or ArtifactVersion lineage. Every predecessor
has at most one successor, and a provider capture must appear after its
execution-attempt start and before that attempt's terminal observation. A
resumed attempt appends `provider_session_resume_reserved` under the same lock
as `execution_attempt_started`; the later capture consumes that reservation.

The current phase is derived from protocol records; `events.jsonl` is an audit
journal, not a second source of lifecycle state. Mutating commands acquire
root `.cac.lock`. `.cac.lock`, `events.jsonl`, and `.cac-events-anchor.json`
are audit files, not protocol records.

## Enum Values

- `review_mode`: `fresh_review`, `remediation_verification`, `regression_check`, `scope_triage`.
- `scope_classification`: `in_scope`, `out_of_scope`, `unclear_scope`.
- `blocking_status`: `blocking`, `non_blocking`, `deferred`, `promoted_by_human`.
- `materiality_status`: `undisputed`, `disputed_materiality`, `resolved_after_dispute`.
- `response_type`: `accept`, `reject`, `partially_accept`, `request_clarification`.
- `re_review.decision`: `verified`, `rejection_accepted`, `still_valid`, `disputed`, `needs_human`.
- `ValidationEvidence.result`: `pass`, `fail`, `error`, `waived`.
- `HumanDecision.decision_type`: `mark_resolved`, `accept_author_rejection`, `require_revision`, `mark_non_material`, `dispute_materiality`, `waive_validator`, `terminate_escalated_to_human`, `abort_run`, `authorize_review_budget_overrun`.
- `terminal_condition`: `consensus_reached`, `round_limit_reached`, `escalated_to_human`, `aborted`.

## Installer And Managed Files

The repo installer is `scripts/install-cac`. The shell alias `cac` is only for installer terseness; installed protocol artifacts are named `cross-agent-consensus`.

First-class install targets:

- Hermes: detect `$HERMES_HOME`, existing `$HOME/.hermes`, then `hermes` on `PATH`; install to `${HERMES_HOME:-$HOME/.hermes}/skills/cross-agent-consensus`.
- Codex: detect `$CODEX_HOME`, existing `$HOME/.codex`, then `codex` on `PATH`; install to `${CODEX_HOME:-$HOME/.codex}/skills/cross-agent-consensus`.

Best-effort install target:

- Claude: detect `$CLAUDE_HOME`, existing `$HOME/.claude`, then `claude` on `PATH`; install to `${CLAUDE_HOME:-$HOME/.claude}/skills/cross-agent-consensus`.

Managed update rules:

- `managed-manifest.json` lists source-managed files, relative paths, and source hashes.
- Install writes target-side `.cross-agent-consensus-managed.json` with installed file hashes and source metadata.
- `--update` overwrites only managed files.
- If a managed target file hash differs from the previous installed hash, update reports a local modification conflict and preserves the file.
- Files not listed as managed are never deleted or overwritten.
- `config/defaults.yaml` and `config/config.local.example.yaml` are managed package files.
- `config/config.local.yaml` is user-local state and must not be listed as managed.
- User-local notes and config may live under the installed skill directory only if not listed as managed.

## Configuration Records

When `consensus init` uses installed defaults, user-local config, project config, task-file config, or CLI overrides, it writes a `ConfigResolution` section in `run.md`. The record lists loaded and missing sources, source hashes, effective consumed values, resolved Participant Identity and Execution Profile mappings, diagnostics, and redaction rules for future sensitive fields.

Persistent config files may provide Participant Profiles, Execution Profiles, and identity-to-profile bindings, but they must not contain secret values or enable unattended invocation. Run-scoped task files or CLI flags are the only valid places to request unattended invocation, and `invocation-ready` still gates external command execution.
