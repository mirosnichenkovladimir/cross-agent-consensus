---
name: cross-agent-consensus
description: "Manual protocol package for auditable cross-agent task execution and validation loops. Invocation aliases: CAC, cac."
aliases:
  - CAC
  - cac
---

# Cross-Agent Consensus

Use this skill when the user asks for an auditable cross-agent loop where a main/Author Agent does a task and one or more Reviewer/Validator Agents check the result until consensus, escalation, or abort.

Public triggers:

```text
cac: <task_description>
CAC: <task_description>
cac: do design for <feature>
cac: implement this feature and do review
Use CAC to do <task> with main <author> and validators <reviewers>.
Use cross-agent-consensus with task file <path>.
```

The default mental model is: the current/main model acts as Orchestrator, one Author Agent performs the requested task, and separate Reviewer/Validator Agents validate the output. A pure review request is just one special case where the task is `review <artifact>`.

## Versioning

The installed package exposes a strict semantic version in `MAJOR.MINOR.PATCH` form. `scripts/consensus --version` prints the installed version string. New run metadata records `cross_agent_consensus_version`, `protocol_version`, and `layout_version`.

The supported pinned invocation spelling is `cac@X.Y.Z: <task>` when a host can dispatch multiple installed versions. If host-level dispatch is unavailable, a pinned invocation must fail clearly when `X.Y.Z` differs from the installed version.

## Quick Path (first 30 seconds)

The current shipped lifecycle, end-to-end. Every command lists its required flags in [Helper Required Flags Quick Reference](#helper-required-flags-quick-reference); refer there when adapting this skeleton.

```text
RUN_ID=retry-backoff-design-consensus-001
RUN=runs/$RUN_ID

scripts/consensus init \
  --task "design for retry-backoff" \
  --artifact-locator artifacts/v1.md \
  --author claude --orchestrator claude \
  --reviewer codex --reviewer claude \
  --run-id "$RUN_ID"

# 1. Author phase — write/finalize artifacts/v1.md, then:
scripts/consensus prompt --run "$RUN" --phase author --actor claude

# 2. Reviewer phase (per actor)
for ACTOR in codex claude; do
  scripts/consensus prompt --run "$RUN" --phase reviewer --actor "$ACTOR"
  scripts/consensus invocation-ready --run "$RUN" --actor "$ACTOR" \
    --prompt "$RUN/rounds/round-001/prompts/reviewers/$ACTOR.md" \
    --raw-output "$RUN/rounds/round-001/raw/reviewers/$ACTOR.out" \
    --command -- <runtime argv for $ACTOR>
  scripts/consensus invoke-agent --run "$RUN" --actor "$ACTOR" --player <player-id> \
    --phase reviewer \
    --prompt "$RUN/rounds/round-001/prompts/reviewers/$ACTOR.md" \
    --raw-output "$RUN/rounds/round-001/raw/reviewers/$ACTOR.out" \
    --approved --command -- <runtime argv for $ACTOR>
  scripts/consensus capture --run "$RUN" --phase reviewer --actor "$ACTOR" \
    --source-file "$RUN/rounds/round-001/raw/reviewers/$ACTOR.out"
done

# 3. Normalize, report, terminate
scripts/consensus normalize --run "$RUN" --round round-001
scripts/consensus report    --run "$RUN" --terminal-condition consensus_reached \
  --final-artifact-version v1
scripts/consensus terminate --run "$RUN" \
  --terminal-condition consensus_reached \
  --reason "consensus on v1; no blocking findings remain"
```

For long-running reviewers see [Long-Running Invocation: Peek-Loop Default](#long-running-invocation-peek-loop-default). For unattended runs see [M2 Boundary](#m2-boundary).

## Long-Running Invocation: Peek-Loop Default

`scripts/consensus invoke-agent` blocks foreground while the named CLI runs. For reviewer/author/validator runs expected to take more than ~60s (every realistic Codex or Claude review), blocking foreground discards the entire M2 supervised-invocation value: the orchestrator becomes blind to live state, cannot surface progress to the operator, and cannot `agent-cancel` if something goes wrong.

The default orchestrator pattern for long-running `invoke-agent` MUST be background + peek:

1. Launch `invoke-agent` in the background (e.g. via the host's background-task mechanism or `nohup ... &`).
2. Loop on `scripts/consensus agent-peek --run <run> --actor <actor>` (or `agent-status`) at the configured interval (`invocation.peek.interval_seconds`, default 180s). Report a one-line progress update to the operator each iteration.
3. Treat `state == completed`, `failed`, `cancelled`, or stale-monitor as terminal; act accordingly.
4. Keep `agent-cancel` available throughout; if the operator interrupts, cancel the session before continuing.

Foreground `invoke-agent` is acceptable only when the expected runtime is bounded under ~60s (e.g. a `--dry-run` invocation, or a deterministic validator). The `--heartbeat-interval-seconds` flag exists for foreground use but does not by itself satisfy the supervision requirement; the peek-loop pattern remains preferred.

See `references/invocation.md` for the per-host peek-loop sketch.

## Operator Approval Handshake

`scripts/consensus invoke-agent` is fail-closed without `--approved`; the Orchestrator decides whether to pass it. The skill rule is:

- If the user trigger explicitly names the participant CLI (e.g. `cac: do review with codex`, `cac: review this with claude`), the Orchestrator MAY pass `--approved` directly because the operator has already authorized the exact CLI for this run.
- If the trigger is indirect (e.g. `cac: review this`, `cac: validate it`) and the configured reviewer resolves to a CLI without explicit operator naming, the Orchestrator MUST print the exact `invoke-agent` command, including the resolved argv, and ask the operator to confirm before passing `--approved`. Policy may pre-approve via `unattended_invocation: true` with scope limits in run-scoped (not persistent) config; in that case the handshake is replaced by the recorded policy decision.

The approval gate lives in code (`--approved` is mandatory before any named CLI launch); the handshake lives here so the operator always knows what is about to run.

Before launch, the CLI records `approval_binding_version: exact-inputs-2` and
binds the Participant Identity, Participant Profile, Execution Profile,
approved prompt, argv, and working directory. A readable local ArtifactVersion
is bound by its current content digest. Editing the prompt, command, profile
binding, or artifact invalidates that approval; regenerate the prompt when
needed and ask for approval again. A resumed invocation also binds its
`provider_session_entry_id` and provider conversation identifier.

## M2 Boundary

This package is a design/manual-protocol implementation only. Do not create an automatic cross-runtime runner that executes without explicit participant selection, authorization, prompt capture, and raw-output capture. Do not infer or substitute a model/provider outside the recorded Execution Profile, and do not apply reviewer suggestions directly to an artifact. When the user or Policy explicitly names a runtime/CLI as a participant, the Orchestrator MUST invoke that CLI/tool directly only after the run folder and exact prompt are recorded and either the operator has approved the command for this run or Policy declares `unattended_invocation: true` with scope limits. A CLI is available when its binary is executable and its output can be captured to the run folder; authentication failures, timeouts, and non-zero exits are runtime errors to record, not reasons to skip evidence. If these conditions are not met, write the exact manual command or prompt into the run folder and ask the operator to run it.

## Out-Of-Box Invocation Contract

Installed hosts should be able to load this skill and act on `CAC ...`, `cac ...`, `cac@X.Y.Z: ...`, or `cross-agent-consensus ...` without any host-specific harness rule. When invoked through the alias, immediately follow `references/invocation.md`: create the run folder first, write initial records, save exact prompts under the active round's `prompts/`, capture raw outputs under the active round's `raw/`, and continue the manual lifecycle from this package.

When `scripts/consensus` is present in the installed skill package, prefer it for deterministic bookkeeping:

- run `scripts/consensus --version` to inspect the installed package version when needed;
- run `scripts/consensus config show` to inspect layered defaults before relying on saved configuration;
- run `scripts/consensus init` to create the protocol run folder and initial records;
- run `scripts/consensus validate --pre-execution` before generating prompts or invoking any actor;
- run `scripts/consensus validate --integrity` after copying evidence and before terminal output;
- run `scripts/consensus prompt` to create exact prompt payload files, using manual prompt files only as fallback;
- run `scripts/consensus conclusion-validation` after normalization when recalled reviewers must validate proposed Normalized Finding conclusions;
- run `scripts/consensus invocation-ready` before any direct external CLI/runtime invocation;
- run `scripts/consensus capture` after command/manual output to preserve raw evidence in the run folder;
- run `scripts/consensus status` before remediation loops to inspect ReReviewDecision attempt counts and agent session accounting;
- run `scripts/consensus next --run <run> --json` to derive the next action from validated run records and the RunJournal without launching a participant or writing a run file;
- run `scripts/consensus validate --terminal` and `scripts/consensus terminate` before making a terminal consensus claim.

In `NextActionPlan`, an `invoke-<participant>-reviewer` action means the bounded
fresh-review `scripts/consensus run` macro for that Participant Identity and
ReviewBatch, not a bare provider process call. The macro runs pre-execution
record checks while finalizing the exact prompt, checks invocation readiness,
consumes the scoped approval, invokes the recorded Execution Profile, and
appends RawReviewerOutput. Author, AuthorResponse, re-review, and validator
phases retain explicit record-producing actions until their macros can append
ArtifactVersion, AuthorResponse, ReReviewDecision, and ValidationEvidence. A
pending operator checkpoint withholds the `invoke-*` action; supervised
execution consumes explicit exact-input approval in the `scripts/consensus run`
command itself.
`record_journal_sha256` hashes the ordered protocol-record frontmatter and
RunJournal entries only; file-integrity checks supply separate blockers.

If the script is unavailable or fails for a host-specific reason, follow the existing template workflow manually and record the gap in the run folder.

### Helper Required Flags Quick Reference

Each helper has a required-flags minimum that `--help` documents; the most common ones for first-run orchestrators are listed here so they need not be discovered by trial:

- `consensus init`: `--task` or `--task-file`, `--artifact-locator`, `--author`, `--orchestrator`, `--reviewer` (or supplied by config).
- `consensus prompt`: `--run`, `--phase`. Phase-specific: `--actor` for reviewer/validator/author-response/rereview; `--review-batch` for rereview.
- `consensus capture`: `--run`, `--phase`, `--source-file` or `--source-mode`. Reviewer phase also needs `--actor` and (when ambiguous) `--review-batch`, `--artifact-version`.
- `consensus invocation-ready`: `--run`, `--actor`, `--prompt`, `--raw-output`, `--command -- <argv>`.
- `consensus invoke-agent`: `--run`, `--actor`, `--player`, `--phase`, `--prompt`, `--raw-output`, `--command -- <argv>`.
- `consensus agent-status` / `agent-peek` / `agent-watch` / `agent-cancel`: `--run`, `--actor`.
- `consensus next`: `--run`; add `--json` for the byte-stable `NextActionPlan` contract.
- `consensus normalize`: `--run`, `--round`.
- `consensus report` / `terminate`: `--run`, `--terminal-condition`.

When a participant is named as a CLI/runtime reviewer or validator, such as Codex CLI, Claude CLI, Hermes, or another external agent process, the Orchestrator MUST start that participant with `scripts/consensus invoke-agent`. Do not satisfy a named external reviewer by using the host's internal subagent feature, an in-chat self-review, or an ordinary shell command followed by `capture`; those paths can preserve output, but they bypass supervised process telemetry and will not create `rounds/<round>/agents/<actor>/session-*` artifacts. Direct `capture` remains valid only for manual/imported evidence and must not be described as live invocation telemetry.

For first-class runtime telemetry, use structured stream modes:

- Claude CLI: `claude -p --verbose --output-format stream-json --include-partial-messages ...`
- Codex CLI: `codex exec --skip-git-repo-check --json -` (the `--skip-git-repo-check` flag is required by `invoke-agent` so that Codex doesn't refuse to start in directories it has not been explicitly trusted; supervised launch surfaces the fix command and exits before allocating a session when it is missing.)

Before normalizing a named CLI reviewer result, check that `scripts/consensus agent-status --run <run> --actor <actor>` succeeds and points to the expected session. If it reports a missing session, rerun that reviewer through `invoke-agent` or explicitly record that the evidence was direct/manual capture without runtime telemetry.

When ConfigResolution binds a reviewer Participant Identity to a non-manual Execution Profile with argv, that reviewer is a configured CLI reviewer. RawReviewerOutput for configured CLI reviewers is not terminally valid unless a completed `rounds/<round>/agents/<reviewer>/session-*` invocation session exists.

No participant output is protocol evidence until it is materialized in the run folder. Regardless of how the Orchestrator invokes another entity, including a local subagent, host tool, external CLI, manual human handoff, or background session, the Orchestrator must create the appropriate durable artifact before using that output for normalization, author response, validation, re-review, or termination. At minimum, preserve the exact prompt when applicable, raw output under the active round's `raw/` directory or an immutable lifecycle record, and the corresponding protocol record such as `RawReviewerOutput`, `ValidationEvidence`, `ArtifactVersion`, or `ReReviewDecision`.

If the host cannot perform a step itself, it must write the manual prompt or command into the run folder and ask the operator to run it. If the host can perform the step itself, including invoking an explicitly named and authorized external agent CLI, it must do so instead of stopping at handoff. Do not treat host chat history, terminal scrollback, or `/tmp` files as protocol evidence unless copied into `runs/<run_id>/` and referenced from records.

`cac` and `CAC` are invocation aliases only. Protocol records, prompts, run folders, and user-facing protocol text use `cross-agent-consensus`.

## Required Inputs Before Task Execution

Do not ask any Author, Reviewer, or Validator to start until these inputs are known and recorded or explicitly requested in `runs/<run_id>/run.md`:

- artifact path, target output path, or artifact content locator, when known;
- task brief with objective and success criteria;
- policy or selected profile;
- review scope;
- review batch mode, defaulting to `fresh_review` only for the first review;
- author/main agent identity;
- reviewer agent identities;
- orchestrator identity;
- artifact version id, defaulting to `v1` only for the initial artifact;
- run id, or permission to generate one;
- run root, defaulting to `runs/<run_id>/` under the current workspace or Git root;
- required validators from the active profile;
- round limits;
- Human Supervisor identity or explicit `none`.

The Orchestrator identity must be distinct from the Author and every Reviewer identity. First-round reviewers must not see other reviewers' findings before emitting their own Raw Findings.

## Configuration

The helper CLI supports deterministic layered configuration for repeat runs. Resolution order is:

```text
installed defaults
  -> user-local config
  -> project config
  -> task-file config
  -> CLI flags
```

Installed defaults live at `config/defaults.yaml` and are managed package files. Personal defaults live at `config/config.local.yaml` in an installed skill directory or at the path named by `CROSS_AGENT_CONSENSUS_CONFIG`; that local file is not managed and is preserved by installer updates. Team defaults live in `.cross-agent-consensus.yaml` at a project root. `consensus init --task-file <path>` may use run-scoped task config, and CLI flags override lower config layers except that reviewer participant replacement requires the explicit reviewer override flag.

Reviewer identities are participants, not review lenses. If saved configuration supplies `participants.reviewers`, `consensus init --reviewer ...` MUST fail unless `--allow-reviewer-config-override` is present. Use `--review-focus` to record emphasis areas such as publication safety, API surface, or dispatcher policy without replacing configured reviewers.

When the user trigger names exactly one reviewer that is a subset of the configured reviewers (e.g. `cac: do review with codex` against a config with `reviewers: [codex, claude]`), the Orchestrator MUST treat that as an explicit override and pass `--allow-reviewer-config-override` together with the single `--reviewer` flag. Do not re-add the unmentioned reviewers to satisfy the configured list, because that silently runs a reviewer the operator did not ask for.

Use these commands for configuration:

- `scripts/consensus config show [--json]`;
- `scripts/consensus config validate`;
- `scripts/consensus config paths`;
- `scripts/consensus config setup [--dry-run]`.

Configuration schema `cross-agent-consensus-config-2` separates `participant_profiles`, `execution_profiles`, and `participant_identities`. A Participant Identity may select another Execution Profile without changing its protocol name. Persistent installed, user-local, and project config must not contain secret values or enable unattended invocation. `model` and `reasoning_effort` are translated into provider-specific argv; conflicting duplicate argv declarations are rejected. Participant Profile instructions are copied into finalized prompts and remain subordinate to CAC Policy, ReviewScope, and phase output requirements. Child CLIs receive only environment-variable names declared by the Execution Profile, while values remain unrecorded. Execution Profile argv is a preset for explicit invocation only; `invocation-ready` must still pass and the prompt/raw-output paths must be recorded before any external reviewer CLI is run. `consensus init` records the resolved identity/profile mapping and effective commands in `ConfigResolution` before any config-derived value is used. Version 0.13.0 rejects `cross-agent-consensus-config-1` and `reviewer_clis`; migrate them to schema 2 before loading the configuration.

Every supervised provider launch must create `execution_attempt_started` in the
RunJournal before `subprocess.Popen()`. A successful provider exit remains an
ambiguous attempt until capture writes and hashes the expected protocol
receipt. Never retry an unresolved `mutating` or `external_side_effect`
attempt without an explicit operator decision recorded through
`--approve-ambiguous-retry --operator-identity <identity>`.

Codex and Claude provider conversations are separate from CAC process sessions.
`session-NNN` names one supervised process; `provider_session_captured` stores
the Codex thread ID or Claude session UUID. Resume with
`invoke-agent --resume-provider-session-entry <entry-id>`. CAC resumes only the
same ParticipantIdentity, ParticipantProfile role, ExecutionProfile, adapter,
run, and ArtifactVersion lineage, and only when the selected entry is the
conversation's latest leaf. Execution Profiles contain fresh argv;
provider-native resume selectors are invalid unless the adapter constructs
them from the selected journal entry. The execution attempt atomically reserves
the selected leaf before provider launch, and provider capture consumes that
reservation. Existing RunJournal diagnostics block resume. Definition drift requires one
`--definition-drift-resolution`; accepting a recorded profile, named migration,
or compatibility rule also requires `--operator-identity`, and named choices
require `--definition-drift-reference`. Cross-reviewer provider-session reuse
is invalid even when both reviewers use the same ExecutionProfile.

Workers may emit content-only JSON drafts for Author artifacts, Reviewer
findings, Validator evidence, and semantic synthesis. Use `promote-draft`; do
not let worker JSON supply run IDs, record IDs, actor IDs, timestamps, hashes,
or provenance. The finalizer captures the exact draft bytes, validates the
declared content fields, assigns CAC-owned fields, and writes one
`draft_promoted` RunJournal entry. Exact duplicate Reviewer findings are
removed without semantic merging. Synthesis stays a named participant
invocation with declared source-record IDs.

For code review, use `snapshot-git --base-ref ... --artifact-version ...`
before generating participant prompts. The snapshot contains resolved Git
revisions, binary staged/unstaged or base/target patches, and exact untracked
bytes. `ArtifactVersion` binds its manifest and full snapshot digest. Never
substitute a live `git diff` after invocation; a repository mutation during
capture or a changed snapshot digest blocks promotion.

## Terse Invocation Behavior

For terse `cac: <task_description>` invocations, treat `<task_description>` as the TaskBrief objective. If the task asks to design, implement, write, fix, analyze, migrate, or otherwise produce work, the first phase is Author execution and the second phase is Reviewer/Validator validation. If the task only asks to review an existing artifact, skip Author execution and start from the review batch after initialization.

For Markdown and plain-text outputs, infer `profile=document-consensus`. For implementation/code tasks, use the same manual protocol records but require the user or Policy to identify validators before declaring consensus.

The skill may infer:

- `review_batch_mode=fresh_review` for the first review;
- `artifact_version_id=v1` for the first produced or reviewed artifact version;
- a generated `run_id` using `<task-slug>-consensus-NNN`, for example `layout-simplification-consensus-001`.

The skill must not invent material policy, review scope, participant identities, validator waivers, Human Supervisor decisions, or terminal decisions. If required fields are missing, create or request `runs/<run_id>/run.md` from `templates/run-init.md` and stop before task execution or reviewer work starts.

For terse review requests, do not convert review areas into participant identities. Example: for "review implementation, focus on publication and API surface", keep configured reviewers such as `codex` and `claude`, and record `publication` and `API surface` as ReviewBatch `review_focus` values.

The init record set must provide enough information to create:

- TaskBrief, Policy, Participants, and ReviewScope sections in `run.md`;
- the first ReviewBatch section in `rounds/round-001/round.md`;
- the initial ArtifactVersion record in `artifacts/v1.md`.

## Run Layout

Run records are part of this skill package's contract, not a host-harness convention. Any Orchestrator implementing this skill must create the run folder before invoking an Author, Reviewer, validator, or external agent CLI. The recorded prompts and raw outputs live in the active round folder; host temp paths such as `/tmp` are scratch-only and are not audit evidence unless copied into the run folder and referenced from a record.

Use the round-first grouped Markdown layout from `references/record-contract.md`:

```text
runs/<run_id>/
  run.md
  artifacts/
    <artifact_version_id>.md
  rounds/
    round-001/
      round.md
      prompts/
      raw/
      reviews/
      normalization.md
      author-responses.md
      rereviews/
      validation.md
      backlog.md
  validation.md
  escalations.md
  report.md
  backlog.md
```

Every protocol record section must include a stable heading, a `---`-delimited YAML frontmatter block, common record fields, type-specific fields, and cross-reference ids. Frontmatter is authoritative.

CLI mutations are serialized by root `.cac.lock` and appended to root
`events.jsonl`. `scripts/consensus status` derives the current phase from the
protocol records; never edit `events.jsonl` to change that phase.

## Document-Consensus Defaults

For `document-consensus`, enforce these defaults unless the user or Policy overrides them before review starts:

- supported artifacts: Markdown and plain text;
- default fresh-review rounds: `1`;
- maximum fresh-review rounds without explicit Human Supervisor approval: `2`;
- default remediation-verification attempts per accepted blocking finding: `2`;
- first review batch mode: `fresh_review`;
- initial artifact version id: `v1`.

Required `document-consensus` validators:

- `artifact_exists`;
- `review_scope_exists`;
- `review_batch_mode_declared`;
- `final_report_exists`;
- `blocking_findings_have_author_responses`;
- `final_report_unresolved_blockers_declared`;
- `final_report_backlog_separated`.

Consensus may not be declared unless every required validator has `ValidationEvidence.result` of `pass` or `waived`. A waiver must name `waiver_authority_or_null` and explain `waiver_rationale_or_null`.

## Manual Lifecycle

1. Initialize the run folder and records with `scripts/consensus init` when available, otherwise from `templates/run-init.md`, `templates/review-batch.md`, and `templates/artifact-version.md`. This step is mandatory before any Author, Reviewer, Validator, or external CLI invocation.
2. Write the exact Author/main-task, Reviewer, and Validator prompts into the active round folder with `scripts/consensus prompt` when available, otherwise manually under `rounds/round-NNN/prompts/`, and reference them from the relevant record before invocation.
3. Run or manually request Author execution when the task produces or changes an artifact; record the produced content, patch, or stable locator as ArtifactVersion `v1` or the next version.
4. Give each first-round reviewer only the TaskBrief, Policy, Participants needed for identity, ReviewScope, ReviewBatch mode, and target ArtifactVersion.
5. Capture each reviewer's raw output with `scripts/consensus capture` when available, otherwise in `rounds/round-NNN/reviews/<reviewer_identity>.md` from `templates/review.md`. Do not rewrite raw output after capture. If raw stdout/stderr was first captured by a host process, copy it into the run folder before normalization.
   CLI capture records the copied payload digest and its origin. A `live_cli`
   capture also records the exact completed session path and prompt digest.
6. Normalize Raw Findings into Normalized Findings in `rounds/round-NNN/normalization.md`.
7. When the normalized superset needs reviewer confirmation, run `scripts/consensus conclusion-validation` to append a `scope_triage` ReviewBatch with `source_finding_ids` set to the Normalized Finding ids, then capture recalled reviewer outputs before changing conclusions.
8. Require Author Responses for every in-scope blocking material Normalized Finding that remains confirmed as an Author-facing blocker.
9. Create a new ArtifactVersion for each author revision.
10. Run linked re-review against Normalized Findings, Author Responses, revisions, and relevant Validation Evidence. Before generating another re-review prompt or skeleton, all existing protocol records must pass record/link validation and the finding must be below `max_remediation_rounds_per_finding`. If an unresolved `still_valid`, `disputed`, or `needs_human` decision has reached the cap, stop remediation, record or use the generated EscalationRecord in `escalations.md`, request a HumanDecision, and terminate as `escalated_to_human` or follow the human/policy decision.
11. Append ValidationEvidence for required validators to `rounds/round-NNN/validation.md`, using `scripts/consensus capture --phase validator` when available. Root `validation.md` is a run-level summary.
12. Escalate or record Human Decisions in `escalations.md` when needed.
13. Run `scripts/consensus validate --integrity`; resolve every artifact,
    prompt, evidence, or session digest mismatch.
14. Terminate with `scripts/consensus terminate` when available, otherwise create `report.md`. The file must start with human-readable finding blocks using `Problem`, `Explanation`, and `Required action`, then include parseable TerminationRecord and FinalReport sections.

## Terminal Conditions

Use exactly one `terminal_condition`:

- `consensus_reached`: all in-scope blocking material findings are resolved, required validators pass or are waived, no in-scope `needs_human` remains, and FinalReport exists.
- `round_limit_reached`: round budget is exhausted and consensus is not reached, unless Policy selects terminal human handling.
- `escalated_to_human`: a HumanDecision, EscalationRecord, or policy-defined escalation deadline terminates the run as human-escalated. Pending or failing validators must be reported, but they do not block this non-consensus terminal condition.
- `aborted`: an AbortRecord exists and the Orchestrator creates a matching TerminationRecord.

## Required Terminal Output

At terminal state, report:

- run folder path;
- `terminal_condition`;
- `termination_record_id` and `report.md` path;
- `final_artifact_version_id_or_null`;
- final artifact path or explicit null;
- validator status summary and validation evidence paths;
- agent session summary, distinguishing failed invocations from completed review or re-review decisions;
- FinalReport section path or anchor inside `report.md`;
- unresolved normalized finding IDs, if any;
- backlog location for non-blocking, deferred, and out-of-scope items.

## Run Feedback (opt-in)

When `feedback.enabled: true` in `config/defaults.yaml` (or a local override), `scripts/consensus report` also writes `runs/<run_dir>/cac-run-feedback.md` — a skeleton with four fixed H2 sections (`Performance anomalies`, `Critical errors`, `Small bugs / rough edges`, `Logic gaps`). Before running `scripts/consensus terminate`, overwrite the bullets in each section with observations from this run. Leave a section's bullet as `_none_` when there is nothing to report; do not delete sections. This file is a feedback channel to skill maintainers; it is unrelated to the protocol records and is not consumed by validation.

## Reference Files

- `references/invocation.md`: portable `CAC`/`cac` alias behavior and out-of-box host contract.
- `references/protocol-checklist.md`: manual checklist, role contracts, and `document-consensus` defaults.
- `references/record-contract.md`: grouped Markdown record contract, run layout, and managed install rules.
- `templates/prompts.md`: manual Author, Reviewer, Author Response, Re-Review, and Final Report prompts.
