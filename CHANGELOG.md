# Changelog

All notable changes to the **cross-agent-consensus** (CAC) skill package are
documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The authoritative version is `skills/cross-agent-consensus/VERSION`; each entry
below corresponds to the value committed at that point.

## [0.19.2] - 2026-07-18

### Fixed
- Kimi-hosted configuration discovery now reads
  `$KIMI_CODE_HOME/skills/cross-agent-consensus/config/config.local.yaml`,
  defaulting to `~/.kimi-code` like the installer and self-test commands.
- Protocol record discovery excludes supervised `agents/` session evidence, so
  reviewer headings such as `## Finding ...` cannot be parsed as CAC records.
- The RunJournal accepts late structured findings returning a run from
  validation through normalization and classification before validation
  resumes.
- The defaults-focused review-lens test uses `--no-config`; an operator's
  persistent reviewer selection no longer changes the expected assertion.

## [0.19.1] - 2026-07-18

### Added
- `install-cac --target kimi` installs CAC under
  `$KIMI_CODE_HOME/skills/cross-agent-consensus`, defaulting to
  `~/.kimi-code/skills/cross-agent-consensus`.
- `consensus selftest --invocation --host kimi` checks the Kimi-hosted package,
  its routing description, and every managed-file digest.

### Changed
- `install-cac --target all` now installs Hermes, Codex, Claude, and Kimi host
  copies. `--allow-missing-first-class` treats a missing Kimi CLI like missing
  Hermes or Codex.

## [0.19.0] - 2026-07-18

### Added
- `kimi-cli` and `kimi-reviewer-default` add a first-class Kimi Code CLI
  connector. CAC writes the recorded prompt to a stable stdin bridge; the
  bridge supplies Kimi's required `--prompt` argument and relays
  `stream-json` assistant, tool, and terminal records.
- Kimi capability probing records `kimi --version`. Declarative
  ExecutionProfile `model` maps to Kimi `--model`; the connector conformance
  suite covers the configured `kimi-code/k3` alias.
- `session.resume_hint` records supply Kimi provider-session identifiers.
  Continuation uses `--session <id>` under CAC's existing ParticipantIdentity,
  ExecutionProfile, run, and ArtifactVersion-lineage rules.

### Changed
- `run_generic_agent()` now closes provider stdin, stdout, stderr, and selector
  resources after every completed, failed, or cancelled invocation.
- Kimi authentication and provider configuration remain under
  `KIMI_CODE_HOME`; Execution Profiles allowlist environment-variable names
  without recording credential values.

## [0.18.0] - 2026-07-14

### Added
- `hermes-cli` and `hermes-reviewer-default` provide one first-class connector
  iteration. The stdin bridge invokes Hermes quiet mode and converts the final
  text plus provider session ID into CAC JSONL events.
- Hermes capability probing resolves the installed executable and records the
  bounded `hermes --version` result. Declarative ExecutionProfile `model` maps
  to Hermes `--model`.
- Connector conformance covers fresh and resumed sessions, cancellation,
  timeout, malformed output, and missing or conflicting session identifiers.

### Changed
- `provider_session_captured` continuation now supports Hermes session IDs
  under the same ParticipantIdentity, profile, run, and ArtifactVersion lineage
  rules already used for Codex and Claude. When Hermes rotates an ID after
  context compression, the successor capture becomes the next resumable leaf.
- Hermes authentication, provider installation, and credential storage remain
  outside CAC. The built-in ExecutionProfile passes named environment variables
  without persisting their values.

### Fixed
- The historical `cac_tool.py` launcher exports the installed skill root to
  child `PYTHONPATH`, matching `scripts/consensus`.
- macOS cancellation reads process start time through `libproc`; sandboxed
  runs no longer depend on permission to execute `ps`.

## [0.17.0] - 2026-07-14

### Added
- `consensus remediate --json` derives a byte-stable
  `BoundedRemediationPlan` from the validated `NextActionPlan` for an opt-in
  `Policy.profile: bounded-remediation` run.
- `consensus remediate --execute --approved --operator-identity <identity>`
  dispatches at most one Author, Reviewer, or Validator phase through the
  existing prompt, readiness, approval, invocation, and capture commands.

### Changed
- Bounded-profile `OperatorApproval` records bind a checkpoint identifier and
  the digest of phase, runnable actions, required records, pending choices, and
  the RunJournal leaf shown to the operator.
- Semantic normalization, AuthorResponse, ReReviewDecision, HumanDecision,
  escalation, and terminal records remain explicit stop points. The bounded
  dispatcher never converts provider verdict prose into lifecycle state.

### Fixed
- A changed finding set, ArtifactVersion, validator record, or RunJournal leaf
  marks an older bounded-profile checkpoint stale. Execution requires the
  displayed checkpoint values and recomputes them after prompt finalization,
  before readiness or session allocation.
- Ambiguous-retry and human-decision waits cannot dispatch. A waiting Reviewer
  plan may proceed only when every pending choice is the ordinary exact-input
  approval returned by the caller. Partial review and validation phases select
  one ParticipantIdentity whose executable receipt remains missing.
- The approval append is treated as a bounded-transition reservation. Exact
  approval verification reconstructs the pre-approval plan digest immediately
  before session allocation; any intervening record or RunJournal entry aborts
  the launch.
- `document-consensus` cannot enter the new dispatcher, and no M6 publication
  command exists without a separate exact-input Policy and approval contract.

## [0.16.0] - 2026-07-14

### Added
- `promote-draft` accepts content-only Author, Reviewer, Validator, and
  synthesis JSON drafts. The deterministic finalizer assigns protocol
  identifiers, participant bindings, timestamps, hashes, and captured-session
  provenance before atomically writing CAC-owned records.
- `snapshot-git` materializes the resolved repository root and revisions,
  binary staged/unstaged/target patches, untracked inventory, and exact
  untracked bytes under an immutable content-addressed snapshot directory.
- Git snapshot `ArtifactVersion` records bind both the manifest file hash and
  the complete change-snapshot digest.

### Changed
- Draft promotion rejects worker-supplied protocol identity or provenance
  fields. Exact duplicate findings are removed byte-for-byte while declared
  order remains unchanged.
- Semantic synthesis requires declared source record identifiers and, by
  default, a completed supervised participant session. Manual imports require
  the explicit `--allow-manual-source` boundary.

### Fixed
- Git snapshot capture repeats every Git read before publication and rejects a
  worktree or index mutation instead of mixing bytes from two repository states.
- Draft promotion recomputes the bound ArtifactVersion and Git snapshot hashes,
  so output from a stale review target cannot enter protocol records.
- Supervised promotion binds the provider stream and parsed content-only draft
  as separate digests; both files must match the captured session evidence.
- A retry after the promoted record was written completes or verifies the same
  execution-attempt receipt and restores the missing `draft_promoted` event.
- Artifact verification recomputes every Git snapshot member byte count and
  SHA-256, its declared inventory, and the canonical descriptor digest.
- Reviewer promotion rejects a `ReviewBatch` that differs from the supervised
  prompt path, and prompt generation rejects an unknown explicit
  `ArtifactVersion`.
- Reviewer deduplication compares exact source JSON tokens; semantic duplicate
  resolution remains a captured synthesis action.

## [0.15.0] - 2026-07-14

### Added
- Codex and Claude adapters extract provider conversation identifiers and build
  provider-specific resume argv. Built-in Execution Profiles declare resume
  only after passing `cross-agent-consensus-provider-conformance-1`.
- `provider_session_captured` RunJournal entries bind the provider identifier
  to its distinct CAC `session-NNN`, execution attempt, ParticipantIdentity,
  ParticipantProfile, ExecutionProfile, phase, ArtifactVersion lineage,
  package/protocol definitions, prompt, and effective argv.
- `invoke-agent --resume-provider-session-entry <entry-id>` resumes one
  predecessor entry. Definition drift accepts only a recorded profile,
  named migration, or documented compatibility rule with operator identity;
  new-run and abort decisions remain fail-closed.

### Changed
- Exact-input OperatorApproval bindings include the selected provider-session
  entry and provider identifier for resumed argv.
- A resumable structured provider must emit its provider conversation
  identifier. Missing identifiers fail as `missing_session_identifier` before
  terminal provider output is promoted.

### Fixed
- Provider conversation identifiers cannot be shared across distinct
  ParticipantIdentity values, even when participants use the same
  ExecutionProfile. RunJournal validation enforces predecessor ownership and
  execution-attempt linkage.
- Package, protocol, identity/profile, role, adapter, run, or ArtifactVersion
  lineage drift cannot silently resume an older provider conversation.
- Only the latest leaf provider-session entry may be resumed. A predecessor
  receives an atomic RunJournal reservation before provider launch, cannot
  acquire a second reservation or successor, and RunJournal validation rejects
  provider-session branches or captures written after an attempt terminated.
- Recorded Execution Profiles and direct base commands reject provider-native
  Codex/Claude resume selectors; only `--resume-provider-session-entry` may
  construct resumed argv.
- Historical `exact-inputs-1` approvals remain compatible with fresh
  invocations but cannot authorize a provider-session continuation.
- Resume construction fails when the existing RunJournal has transition,
  uniqueness, hash-chain, or provider-session diagnostics; audit errors cannot
  remain advisory at the launch boundary.

## [0.14.0] - 2026-07-14

### Added
- Every supervised provider launch appends `execution_attempt_started` to the
  hash-chained RunJournal before `subprocess.Popen()`. The record binds the
  action, ParticipantIdentity, ParticipantProfile, ExecutionProfile, prompt,
  protocol-record digest, ArtifactVersion digest, expected receipt, attempt
  number, predecessor attempt, provider session, and retry-safety class.
- Provider completion appends an explicit attempt observation: completed,
  failed, or ambiguous. A zero provider exit remains ambiguous until capture
  writes `RawReviewerOutput` or `ValidationEvidence` and binds its record hash.
- The fake-provider conformance fixture covers stdin/argv, structured events,
  stderr, nonzero exit, stalled and child processes, resume-shaped output,
  malformed streams, missing final output/session ID, and digest mismatch.

### Changed
- A stale provider process receives `SIGTERM` for its whole process group and,
  after the cancellation grace period, `SIGKILL`. The attempt records `timeout`
  separately from nonzero exit, process termination, and launch failure.
- `invoke-agent --retry-safety` classifies launches as `read_only`,
  `idempotent`, `mutating`, or `external_side_effect`. Retrying an unresolved
  mutating or external-side-effect attempt requires the recorded operator
  decision `--approve-ambiguous-retry --operator-identity <identity>`.
- `new-artifact --execution-attempt <attempt-id>` binds an Author-produced
  `ArtifactVersion` receipt to its exact mutating attempt.
- `invocation.json` now binds the RunJournal attempt ID and retry-safety class.

### Fixed
- A supervisor crash after provider launch no longer erases the launch intent:
  an unmatched `execution_attempt_started` record remains durable and blocks
  unsafe automatic repetition.
- Receipt capture correlates by globally unique execution-attempt ID rather
  than participant-local `session-NNN`, and the RunJournal rejects completion
  after a failed attempt.
- Structured providers must emit a final answer; malformed streams and
  final-answer omissions produce distinct failed attempt observations.
- Timeout and operator cancellation continue targeting the stored process
  group after its leader exits, so orphaned provider children cannot hold the
  supervisor open past the cancellation grace period.

## [0.13.0] - 2026-07-14

### Added
- `consensus next --run <path> --json` returns a byte-stable
  `NextActionPlan` derived from protocol records, the hash-chained RunJournal,
  `ConfigResolution`, and Execution Profiles. It launches no participant and
  writes no run files.
- `NextActionPlan` names runnable action IDs, missing or conflicting records,
  required records, operator/human checkpoint choices with consequences, and
  terminal success, failure, or unresolved status.
- Planner tests cover every `derive_run_phase()` value, missing-record
  blockers, conflicting singleton records, human checkpoints, terminal
  conditions, repeatable JSON, and read-only CLI execution.

### Changed
- A unanimously resolving `ReReviewDecision` set in the latest remediation
  `ReviewBatch` advances the derived phase to validation or termination.
- Configuration accepts only `cross-agent-consensus-config-2`.
  `cross-agent-consensus-config-1` and `reviewer_clis` now fail with migration
  diagnostics instead of being translated.

### Fixed
- Phase derivation and terminal validation now apply the same latest-batch and
  binding `HumanDecision` resolution rules.
- The first 0.13 mutation of a compatible 0.12 run records the explicit
  `awaiting_rereview` compatibility transition without breaking RunJournal
  adjacency or its SHA-256 chain.
- Public action and checkpoint identifiers add a stable digest when `slugify()`
  would lose information, so distinct protocol identifiers cannot collapse.
- A recorded `OperatorApproval` does not clear the planner checkpoint for an
  unspecified future prompt, player, or working directory; the concrete
  invocation still consumes the exact-input approval.
- Terminal records take precedence over stale per-finding checkpoints, while
  run-scoped abort and human-escalation decisions take precedence before
  termination is recorded.
- Artifact selection follows the unique `predecessor_id_or_null` chain head;
  duplicate identifiers, missing predecessors, branches, and cycles invalidate
  the plan instead of selecting the lexicographically last artifact file.
- A binding `waive_validator` decision requests waived `ValidationEvidence`
  instead of running the validator it names.
- Re-review invocation and approval IDs include their ReviewBatch, and scoped
  unattended policy is evaluated against that batch's round instead of the
  globally last round.
- Only each affected identifier's latest EscalationRecord can create a pending
  human checkpoint; a newer escalation does not resurrect older checkpoints.
- Fresh-review `invoke-*-reviewer` actions are bounded `consensus run` macros
  that finalize prompts, run pre-execution and invocation-readiness checks,
  consume approval, launch the Execution Profile, and append
  `RawReviewerOutput`. Other participant phases retain explicit
  record-producing actions until their macros can append the required protocol
  record.
- `record_journal_sha256` replaces the ambiguous `input_sha256` field and names
  its exact protocol-frontmatter and RunJournal coverage.
- A remediation ReviewBatch derives `awaiting_rereview` before decisions exist,
  and the planner checks the per-reviewer remediation cap before proposing the
  next batch invocation.
- `abort_run` and `terminate_escalated_to_human` decisions must target exactly
  `__run_scope__`; finding- or validator-scoped terminal decisions invalidate
  the plan.
- An invocation awaiting OperatorApproval is withheld from `runnable_actions`
  and reported through its checkpoint and required `OperatorApproval` record.
- A CLI command that writes a protocol record before returning nonzero now
  appends a RunJournal mutation event with the return code; the next event uses
  the preceding journal phase so manual protocol edits cannot break adjacency.
- Binding HumanDecision records can require a newer ArtifactVersion regardless
  of decision type, and `dispute_materiality` reopens a closed non-material
  NormalizedFinding.
- Validator status and terminal readiness use only ValidationEvidence targeting
  the current ArtifactVersion predecessor-chain head.
- Record validation and termination reject duplicate, missing, branched,
  cyclic, or stale ArtifactVersion chains through the same resolver used by the
  planner; consensus termination must name the unique chain head.
- Protocol timestamps are compared as timezone-aware UTC instants, with durable
  record order breaking equal-timestamp ties, and malformed timestamps fail
  record validation.
- `dispute_materiality` starts a new finding epoch: AuthorResponse,
  remediation ReviewBatch, and ReReviewDecision records from before the binding
  decision cannot satisfy the reopened finding.
- The latest binding `dispute_materiality` boundary persists across later
  non-resolving HumanDecision records such as `require_revision`; only a later
  resolving decision closes the materiality dispute.
- A participant-scoped fresh-review macro finalizes prompts for every expected
  same-round reviewer before invocation readiness, so the first reviewer in a
  multi-reviewer batch is runnable.
- Record and RunJournal diagnostics short-circuit malformed inputs before phase
  derivation; an existing corrupt run cannot be reported as `initialize-run`.

## [0.12.0] - 2026-07-14

### Added
- Configuration schema `cross-agent-consensus-config-2` defines typed
  `ParticipantIdentity`, `ParticipantProfile`, and `ExecutionProfile` values.
  Execution Profiles record a stable identifier, adapter, argv, optional model
  and reasoning effort, prompt transport, output mode, resume declaration, and
  environment-variable allowlist.
- `ConfigResolution` records resolved identity-to-profile mappings and the
  effective Execution Profile commands. `invocation.json`, agent events, and
  agent logs use version-2 schemas and record `participant_identity`,
  `participant_profile_id`, and `execution_profile_id`.
- Configuration rejects duplicate YAML identifiers, missing identity bindings,
  role mismatches, unknown adapters, empty or NUL-bearing argv entries,
  unsupported resume declarations, and secret-looking persisted argv.

### Changed
- Current exact-input approvals use `approval_binding_version=exact-inputs-2`
  and bind `participant_identity`, `participant_profile_id`, and
  `execution_profile_id` in addition to the prompt, argv, working directory,
  and ArtifactVersion digest.
- `consensus run`, `invocation-ready`, and `invoke-agent` take their adapter and
  argv from the Execution Profile recorded when the run was initialized.
  Changing the identity-to-profile binding changes how CAC invokes a participant
  without changing its Participant Identity.

### Fixed
- Rejected secret-bearing argv is redacted from both `command.json` and
  `invocation.json`; failed-session evidence no longer persists the rejected
  secret value.
- Child CLIs receive only the environment-variable names listed by their
  Execution Profile, and exact-input approval hashes the resolved profile.
- Integrity recomputes and compares the resolved Execution Profile digest;
  schema-1 reviewer CLI migration records the inherited environment names.
- Schema-2 runs reject missing profile bindings, manual-profile command
  overrides, adapter mismatches, argv mismatches, participant role/phase
  mismatches, and secret-bearing environment assignments in argv.
- Provider-specific argv now carries declared model and reasoning effort;
  `command.json` uses the declared prompt transport and output mode, and Codex
  `-c`/`--config` duplicates are rejected.
- `Participants` records validator identities, Participant Profile instructions
  appear in finalized prompts, and 0.11 Codex/Claude commands retain their
  structured adapters during `consensus run` migration.
- Built-in deterministic validator IDs remain Policy requirements; only
  configured validator participants appear in `Participants.validator_identities`.

### Compatibility
- `cross-agent-consensus-config-1` and `reviewer_clis` are translated into
  version-2 profiles with deprecation warnings during the 0.12.x window. They
  are scheduled for removal in 0.13.0.
- Historical invocation JSON and `exact-inputs-1` approvals that use
  `actor_identity` remain readable. Current invocation and approval evidence
  uses only `participant_identity` for the invoked participant.

## [0.11.0] - 2026-07-14

### Changed
- Current protocol records, Python APIs, lifecycle evaluators, prompts,
  reports, templates, and schemas use only `NormalizedFinding` and
  `normalized_finding_id`. Newly normalized identifiers use the
  `nf-round-<n>-<sequence>` form.
- Current record output uses schema `m2-markdown-2`.

### Compatibility
- The version-gated load boundary converts historical `m2-markdown-1`
  `CanonicalFinding` records and `canonical_finding_id` references into the
  current model without changing identifier values.
- Current-schema historical names, historical-schema current names, and runs
  mixing historical and current finding records are rejected.

## [0.10.0] - 2026-07-14

### Added
- Exact-input `OperatorApproval` bindings record the SHA-256 digest of each
  approved prompt, runtime argv, working directory, and locally readable ArtifactVersion.
  `invoke-agent` records a binding before launch, and `consensus run` records
  one binding per selected actor.
- `consensus validate --integrity` recomputes ArtifactVersion, raw reviewer
  payload, validation payload, and approved prompt digests. `terminate` now
  rejects drift before writing `report.md`.
- `RawReviewerOutput` and `ValidationEvidence` written by `capture` include
  `capture_origin`, payload digest, prompt digest, and exact supervised session
  identity when the source came from `invoke-agent`. Completed sessions hash
  `invocation.json`, `command.json`, the prompt, stdout, stderr, copied raw
  output, and extracted final output; capture rejects substituted source bytes.
- Successful run mutations append hash-chained `events.jsonl` entries with a
  sequence, actor, event type, and derived lifecycle phase before and after the mutation.
  `.cac-events-anchor.json` records the event count, tail digest, and journal
  digest so deletion and suffix truncation fail validation. Approval events
  also anchor the canonical `OperatorApproval` record digest.
  `consensus status` prints the derived phase and event count;
  `consensus validate --run-events` checks event sequences and phase
  transitions.

### Changed
- Commands that mutate protocol records acquire the run-scoped `.cac.lock`.
  Markdown and JSONL append helpers also take an advisory file lock.
- Concurrent `consensus init` processes serialize run-id allocation and create
  distinct `-NNN` directories instead of racing on an existence check. The
  allocation lock lives inside the ignored run root rather than the repository root.
- Local ArtifactVersion records preserve the directory used to resolve a
  relative `content_locator`, allowing later validation from another working
  directory.

### Fixed
- Hermes invocation self-tests request a wide enabled-only skill table so the
  `cross-agent-consensus` name is not truncated and falsely reported missing.
- `consensus run` selects the latest same-round ReviewBatch, uses its target
  ArtifactVersion, and writes batch-scoped prompt/raw-output paths. A
  conclusion-validation or remediation batch no longer reuses the first
  fresh-review prompt and artifact.
- Reviewer dispatch compares numeric round identities, so a ReviewBatch that
  records `round-001` still limits `round-1` execution to its
  `expected_reviewer_identities` instead of falling back to every participant.
- Live-session integrity compares all ten `OperatorApproval` binding fields,
  including round, prompt path, and artifact digest. An approval from another
  round or prompt path cannot authorize a captured session.
- Session and run-event integrity choose the 0.10 evidence contract from run
  provenance and anchor presence, not from one mutable schema marker. Removing
  `session-evidence-1` or relabeling version-2 events as legacy now fails
  capture and validation instead of downgrading the integrity checks.
- `invoke-agent --require-existing-approval` verifies the matching
  `OperatorApproval` digest against the hash-chained run journal before it
  allocates a session. Approval also hashes readable artifact bytes when an
  `ArtifactVersion` omits `content_hash_or_null`.
- Configured-CLI reviewer evidence must link to the exact successful session,
  prompt, payload digest, artifact version, and `ReviewBatch` for 0.10 runs.
  A completed session from another batch no longer satisfies terminal review
  evidence.
- Terminal blocker calculation requires unanimous decisions from every
  expected reviewer in the latest applicable ReviewBatch. Decisions from
  different batches cannot be combined, and a newer applicable batch keeps the
  finding open before its first decision arrives. Mixed resolving decisions
  remain disputed, and a later unresolved decision reopens a finding whose
  earlier lifecycle value was resolved.
- Re-review prompts and raw outputs use distinct batch-scoped `rereviews/`
  paths. Re-review skeleton paths and record identifiers also include the
  ReviewBatch identifier, allowing multiple remediation batches in one round.
  ReviewBatch lookup and incremental normalization normalize `round-1` and
  `round-001` before selecting records. Appending a later ReviewBatch no longer
  relocates the first batch's prompt during session-evidence validation.
- The first 0.10 mutation of a pre-0.10 run without `events.jsonl` retains the
  legacy event schema instead of creating a version-2 journal without its
  required `run_initialized` event.
- `consensus terminate` atomically replaces the validated `report.md` skeleton,
  so the documented `report` then `terminate` sequence completes instead of
  failing because the report path already exists.
- Repeated reviewer captures batch-qualify colliding `RawFinding` identifiers
  and extract narrative findings from the supervised final answer instead of
  the CLI JSON event stream.
- `consensus normalize` appends only previously unnormalized `RawFinding`
  records, so later review batches do not duplicate earlier normalized
  findings or require overwriting `normalization.md`.

## [0.9.2] - 2026-07-13

### Fixed
- Markdown frontmatter now quotes strings that resemble booleans, nulls,
  integers, lists, or mappings. `TaskBrief.objective: "true"` remains a string
  after a write/read cycle, and escaped quotes now decode symmetrically.
- Required-field placeholder detection now recognizes only a complete
  `<placeholder>` token. Objectives containing comparison operators such as
  `0 < retries > -1` are accepted.
- Record parsing reports invalid UTF-8, unknown frontmatter record types, and
  known record headings without frontmatter. Required record fields are also
  checked against their declared string, list, mapping, boolean, integer, or
  nullable-string type.

### Changed
- `consensus validate` parses each run into one `RunSnapshot`; all selected
  validators reuse the same records and parser diagnostics.
- Cross-record reference checks moved to `link_validation.py` and are grouped
  by review batches, reviewer records, normalization, finding lifecycle,
  decisions, and terminal records.
- Prompt policy moved to `prompt_command.py`, capture owns `cmd_capture`, and
  `run_macro.py` calls typed command-input dataclasses. The previous
  `cli.py`/`run_macro.py` late-import cycle is removed.
- Added pinned pytest/mypy development dependencies, mypy configuration, and
  a GitHub Actions Python 3.11/3.13 quality job.

## [0.9.1] - 2026-06-04

### Added
- Codex trusted-directory preflight in `invoke-agent` and `invocation-ready`.
  When `--player codex-cli` is launched with the real `codex` binary but argv
  is missing `--skip-git-repo-check`, both commands now surface the exact
  command-line fix and exit `3` **before** allocating a session. Operators
  outside Codex's trusted-dir list previously got a `failed` session record
  and a noisy `failed=` count in `consensus status` for a fully recoverable
  environment problem. The check is restricted to the real `codex` binary
  (matches `codex` or `*/codex`); wrappers and test stubs under the
  `codex-cli` player are not blocked.
- `--skip-git-repo-check` added to the default `reviewer_clis.codex.command`
  in `config/defaults.yaml` and the installed-default fallback in
  `cross_agent_consensus/cli.py`. Existing local overrides should add the
  flag (the preflight surfaces the fix automatically).
- Failed-session supersession bookkeeping. When `allocate_agent_session`
  creates a new session in an actor directory that already contains a
  previously failed attempt, the older `state.json` is atomically stamped
  with `superseded_by: <new-session-name>` and `superseded_at`. The new
  helper `mark_state_superseded_by` only acts on `state == "failed"`
  and is idempotent.
- `agent_session_state_counts` buckets superseded sessions under
  `"superseded"` instead of `"failed"`. A Codex first-attempt that the
  operator successfully retried no longer inflates the `failed=` count
  surfaced by `consensus status`.
- `prepare_agent_session` writes an initial `state.json` with
  `state="prepared"` **before** `subprocess.Popen`. A pre-exec failure
  (executable not on PATH, cwd missing, interpreter set-up errors) now
  leaves durable launch evidence on disk instead of an empty session dir.

### References
- Triage: `plans_and_designs/cac-design-notes/feedback-notes-04-06/prioritization-opinion/tier-5-phase-7-followup/README.md`
  (bundle T5-A; covers Phase-7 items #1, #2 partial, #3).

## [0.9.0] - 2026-06-04

### Added
- Optional run-feedback artifact. With `feedback.enabled: true` (top-level key
  in `config/defaults.yaml` or a local override), `scripts/consensus report`
  also writes `runs/<run_dir>/cac-run-feedback.md` — a skeleton with four fixed
  H2 sections (`Performance anomalies`, `Critical errors`,
  `Small bugs / rough edges`, `Logic gaps`). The orchestrator-agent overwrites
  the bullets before `terminate`; empty sections keep `_none_` so the artifact
  shape is uniform across runs. Off by default; opt-in for debugging and
  skill-improvement sessions.
- `feedback.enabled` is now part of `ConfigResolution.effective_values`, so the
  flag's effective layer (installed_defaults vs user_local vs project) is
  recorded alongside other config provenance.

### References
- Design: `plans_and_designs/cac-design-notes/cac-feedback-debug.md`.

## [0.8.3] - 2026-06-04

### Changed
- `normalize_round_id` canonicalizes all input forms (`"1"`, `"round-1"`,
  `"round-001"`) to the short `round-N` id used in records. The on-disk
  directory format remains zero-padded (`round-001`) via `round_dir`.
- `--round` help text on `invoke-agent`, `agent-status`, `agent-watch`,
  `agent-peek`, `agent-cancel`, `run`, and `normalize` documents the
  accepted forms explicitly.
- `capture.py` uses `normalize_round_id` instead of an ad-hoc `round-` prefix
  check; the prior code accepted `"round-1"` and `"1"` but not `"round-001"`.

### References
- Closes friction-log item #11 / CF-006 (CLI `--round round-1` default vs
  on-disk `round-001` layout).

## [0.8.2] - 2026-06-04

### Fixed
- `consensus selftest --invocation` now reads the installer's state file
  `.cross-agent-consensus-managed.json` (the file `scripts/install-cac`
  actually writes) instead of looking for the source-side
  `managed-manifest.json` at the install path. The 0.7.3 selftest had
  asserted the wrong filename, so every post-install selftest after 0.8.1
  wired the check into the installer was reporting a hard failure (exit 3)
  with `managed-manifest.json not found` despite a successful install.
- Each managed file is now re-hashed in place and compared against
  `installed_sha256` from the state file — matching the integrity contract
  the installer establishes at write time.

### Tests
- `tests/test_selftest.py::_stage_install` updated to stage the state-file
  schema (`installed_sha256` / `source_sha256`) instead of the source
  manifest. All 191 tests pass.

## [0.8.1] - 2026-06-04

### Added
- `scripts/install-cac` now wires post-install verification via
  `consensus selftest --invocation --host <target>`.
  - New flags: `--selftest` (default on) and `--no-selftest`.
  - Per target, after a successful managed-file copy the selftest runs against
    the just-installed package via `CAC_SELFTEST_HOME_OVERRIDE`.
  - Only the conventional layout (`basename(home) == .target`) is exercised;
    non-conventional homes (custom `HERMES_HOME`, etc.) are skipped with a
    warning.
  - Per-target summary line carries `[selftest:ok|warning|failed|skipped_*]`.

### Changed
- Selftest exit code 3 (hard failure) flips the overall installer exit code to
  1; exit 2 (mixed/warning) preserves the install but logs a warning.
- `managed-manifest.json` hashes regenerated.

## [0.8.0] - 2026-06-04

### Added
- `consensus run --phase reviewer --execute-reviewers` — single-command driver
  for a same-round phase. Composes existing `cmd_prompt` / `cmd_invoke_agent` /
  `cmd_capture` without forking the consensus CLI.
- New `OperatorApproval` record (REQUIRED_FIELDS + ID_FIELDS + mechanism enum),
  stamped per run with `mechanism=cli_approved_flag` or `policy_unattended`.
- New helper `policy_allows_unattended_scoped()` — fail-closed scope check
  (list-of-tokens or dict form). Legacy `policy_allows_unattended` retained for
  invocation-ready back-compat.
- `run_macro.py`: `ActorPlan` dataclass drives both execution and manual
  fallback; fallback commands round-trip through `invoke-agent` argparse.

### Changed
- Failed sessions use `cmd_capture --no-append-record`; siblings still run
  (round-level isolation contract preserved).

### Tests
- 28 new tests for the run macro; 191 total passing.

### References
- Addresses DESIGN.md R1–R8 (Tier 2 #4).

## [0.7.3] - 2026-06-04

### Added
- `consensus selftest --invocation` (Feature 2A of tier-2 design) — host-agnostic
  self-diagnostic for the installed CAC skill package.
  - Probes canonical install paths for Claude Code, Codex, and Hermes.
  - Parses `SKILL.md` frontmatter and asserts the literal phrase
    `Invocation aliases: CAC, cac` is in the description (LLM routing signal
    locked in by Feature 3's docs test; R6 — any future description change
    must be co-released with a selftest update).
  - Verifies `managed-manifest` SHA256s for the installed copy.
  - Hermes: best-effort `hermes skills list` check; warns if the skill is
    registered but disabled.
- `--write-suggested-rule` writes/replaces an idempotent
  `<!-- cac:begin --> ... <!-- cac:end -->` block in an opt-in target file.
  Never touches text outside the markers.

### Exit codes
- `0` — every detected host healthy.
- `2` — mix of healthy and broken on detected hosts.
- `3` — no install detected, or only detected host is broken.

## [0.7.2] - 2026-06-04

### Added
- `SKILL.md`: new "Quick Path (first 30 seconds)" section between Versioning
  and Long-Running Invocation, covering the current shipped 0.7.x lifecycle
  with every command's required flags.
- `tests/test_docs_consistency.py`: asserts the M2 contradiction is gone, that
  the Quick Path keeps its canonical commands, and pins the
  `Invocation aliases: CAC, cac` phrase used by the upcoming selftest check.

### Changed
- `templates/prompts.md`: replaced the unconditional "Do not automatically
  invoke external runtimes" ban (line 3) with a delivery-rules pointer to
  `SKILL.md` §M2 Boundary. The pre-delivery record-copy rule is preserved
  verbatim.
- `managed-manifest.json` regenerated.

### References
- Closes codex R7 (Quick Path runnability / required flags).
- Lays groundwork for codex R6 (selftest must match SKILL.md description
  verbatim).

## [0.7.1] - 2026-06-04

Bundles the followup-feedback.md fixes from the 0.7.0 friction log.

### Added
- `consensus prompt --dry-run` — resolves the target without writing.
- `consensus init --dry-run` — prints layout without writing.
- `invoke-agent` mirrors `final-output` beside `--raw-output`.
- `agent-status` emits derived summary counts.

### Changed
- Peek-loop is the documented default pattern for long-running `invoke-agent`
  (SKILL.md, references/invocation.md).
- Invocation-ready required-args quick reference added to SKILL.md.
- Skill rule: single-reviewer trigger overrides.
- Skill rule: operator approval handshake.

### Fixed
- `normalize` silently replaces the init-stub `normalization.md`.

### Internal
- Several refactors removing duplication and layering violations:
  - `NARRATIVE_FINDING_ID_RE` moved to shared `records` module.
  - `INIT_STUB_NORMALIZATION` and `EMPTY_AGENT_STATUS_SUMMARY` extracted as
    named constants and deduplicated across modules.
  - Final-output mirror path centralized in a reusable helper
    (`FINAL_OUTPUT_MIRROR_SUFFIX`).
  - Agent status summary file processing and deduplication optimized.
- `managed-manifest.json` hashes regenerated.

## [0.7.0] - 2026-06-04

### Added
- Tier-1 orchestrator optimizations for 2-reviewer CAC runs (PR 1+2+3 bundled),
  reducing orchestrator load from ~40 min to ~15 min without touching the M2
  supervised-invocation boundary.

## [0.6.0] - 2026-06-03

### Added
- `cac agent-peek` — read-only command for operators to inspect a single
  monitored agent session without mutating it. Snapshot includes derived
  state, idle and heartbeat age, and inferred did/now phrases drawn from
  `events.jsonl` and `agent.log` telemetry. Optional `--follow`.

### Removed
- Unused `runners/`, `profiles/`, `examples/`, and `docs/` directories.

### Changed
- Dropped `version` from `SKILL.md` frontmatter; `VERSION` is the single
  source of truth.

## [0.5.1] - 2026-06-03

### Added
- Initial published baseline: CAC review orchestration and reports.

[0.8.3]: https://gitlab.corp.cloudlinux.com/kc-python-automation/cross-model-consensus/-/compare/v0.8.2...v0.8.3
[0.8.2]: https://gitlab.corp.cloudlinux.com/kc-python-automation/cross-model-consensus/-/compare/v0.8.1...v0.8.2
[0.8.1]: https://gitlab.corp.cloudlinux.com/kc-python-automation/cross-model-consensus/-/compare/v0.8.0...v0.8.1
[0.8.0]: https://gitlab.corp.cloudlinux.com/kc-python-automation/cross-model-consensus/-/compare/v0.7.3...v0.8.0
[0.7.3]: https://gitlab.corp.cloudlinux.com/kc-python-automation/cross-model-consensus/-/compare/v0.7.2...v0.7.3
[0.7.2]: https://gitlab.corp.cloudlinux.com/kc-python-automation/cross-model-consensus/-/compare/v0.7.1...v0.7.2
[0.7.1]: https://gitlab.corp.cloudlinux.com/kc-python-automation/cross-model-consensus/-/compare/v0.7.0...v0.7.1
[0.7.0]: https://gitlab.corp.cloudlinux.com/kc-python-automation/cross-model-consensus/-/compare/v0.6.0...v0.7.0
[0.6.0]: https://gitlab.corp.cloudlinux.com/kc-python-automation/cross-model-consensus/-/compare/v0.5.1...v0.6.0
[0.5.1]: https://gitlab.corp.cloudlinux.com/kc-python-automation/cross-model-consensus/-/tags/v0.5.1
