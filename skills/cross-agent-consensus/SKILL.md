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

## M2 Boundary

This package is a design/manual-protocol implementation only. Do not create an automatic cross-runtime runner that executes without explicit participant selection, authorization, prompt capture, and raw-output capture. Do not invent model/provider selection, create JSON schemas, or apply reviewer suggestions directly to an artifact. When the user or Policy explicitly names a runtime/CLI as a participant, the Orchestrator MUST invoke that CLI/tool directly only after the run folder and exact prompt are recorded and either the operator has approved the command for this run or Policy declares `unattended_invocation: true` with scope limits. A CLI is available when its binary is executable and its output can be captured to the run folder; authentication failures, timeouts, and non-zero exits are runtime errors to record, not reasons to skip evidence. If these conditions are not met, write the exact manual command or prompt into the run folder and ask the operator to run it.

## Out-Of-Box Invocation Contract

Installed hosts should be able to load this skill and act on `CAC ...`, `cac ...`, `cac@X.Y.Z: ...`, or `cross-agent-consensus ...` without any host-specific harness rule. When invoked through the alias, immediately follow `references/invocation.md`: create the run folder first, write initial records, save exact prompts under the active round's `prompts/`, capture raw outputs under the active round's `raw/`, and continue the manual lifecycle from this package.

When `scripts/consensus` is present in the installed skill package, prefer it for deterministic bookkeeping:

- run `scripts/consensus --version` to inspect the installed package version when needed;
- run `scripts/consensus config show` to inspect layered defaults before relying on saved configuration;
- run `scripts/consensus init` to create the canonical run folder and initial records;
- run `scripts/consensus validate --pre-execution` before generating prompts or invoking any actor;
- run `scripts/consensus prompt` to create exact prompt payload files, using manual prompt files only as fallback;
- run `scripts/consensus conclusion-validation` after normalization when recalled reviewers must validate proposed Canonical Finding conclusions;
- run `scripts/consensus invocation-ready` before any direct external CLI/runtime invocation;
- run `scripts/consensus capture` after command/manual output to preserve raw evidence in the run folder;
- run `scripts/consensus status` before remediation loops to inspect ReReviewDecision attempt counts and agent session accounting;
- run `scripts/consensus validate --terminal` and `scripts/consensus terminate` before making a terminal consensus claim.

If the script is unavailable or fails for a host-specific reason, follow the existing template workflow manually and record the gap in the run folder.

### Helper Required Flags Quick Reference

Each helper has a required-flags minimum that `--help` documents; the most common ones for first-run orchestrators are listed here so they need not be discovered by trial:

- `consensus init`: `--task` or `--task-file`, `--artifact-locator`, `--author`, `--orchestrator`, `--reviewer` (or supplied by config).
- `consensus prompt`: `--run`, `--phase`. Phase-specific: `--actor` for reviewer/validator/author-response/rereview; `--review-batch` for rereview.
- `consensus capture`: `--run`, `--phase`, `--source-file` or `--source-mode`. Reviewer phase also needs `--actor` and (when ambiguous) `--review-batch`, `--artifact-version`.
- `consensus invocation-ready`: `--run`, `--actor`, `--prompt`, `--raw-output`, `--command -- <argv>`.
- `consensus invoke-agent`: `--run`, `--actor`, `--player`, `--phase`, `--prompt`, `--raw-output`, `--command -- <argv>`.
- `consensus agent-status` / `agent-peek` / `agent-watch` / `agent-cancel`: `--run`, `--actor`.
- `consensus normalize`: `--run`, `--round`.
- `consensus report` / `terminate`: `--run`, `--terminal-condition`.

When a participant is named as a CLI/runtime reviewer or validator, such as Codex CLI, Claude CLI, Hermes, or another external agent process, the Orchestrator MUST start that participant with `scripts/consensus invoke-agent`. Do not satisfy a named external reviewer by using the host's internal subagent feature, an in-chat self-review, or an ordinary shell command followed by `capture`; those paths can preserve output, but they bypass supervised process telemetry and will not create `rounds/<round>/agents/<actor>/session-*` artifacts. Direct `capture` remains valid only for manual/imported evidence and must not be described as live invocation telemetry.

For first-class runtime telemetry, use structured stream modes:

- Claude CLI: `claude -p --verbose --output-format stream-json --include-partial-messages ...`
- Codex CLI: `codex exec --json -`

Before normalizing a named CLI reviewer result, check that `scripts/consensus agent-status --run <run> --actor <actor>` succeeds and points to the expected session. If it reports a missing session, rerun that reviewer through `invoke-agent` or explicitly record that the evidence was direct/manual capture without runtime telemetry.

When ConfigResolution records a `reviewer_clis.<reviewer>.command` for a reviewer, that reviewer is a configured CLI reviewer. RawReviewerOutput for configured CLI reviewers is not terminally valid unless a completed `rounds/<round>/agents/<reviewer>/session-*` invocation session exists.

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

Persistent installed, user-local, and project config must not enable unattended invocation. Reviewer CLI mappings are argv arrays and are defaults for explicit invocation only; `invocation-ready` must still pass and the prompt/raw-output paths must be recorded before any external reviewer CLI is run. `consensus init` records a `ConfigResolution` section in `run.md` before any config-derived value is used.

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

Run records are part of this skill package's contract, not a host-harness convention. Any Orchestrator implementing this skill must create the run folder before invoking an Author, Reviewer, validator, or external agent CLI. The canonical prompts and raw outputs live in the active round folder; host temp paths such as `/tmp` are scratch-only and are not audit evidence unless copied into the run folder and referenced from a record.

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
6. Normalize Raw Findings into Canonical Findings in `rounds/round-NNN/normalization.md`.
7. When the normalized superset needs reviewer confirmation, run `scripts/consensus conclusion-validation` to append a `scope_triage` ReviewBatch with `source_finding_ids` set to the Canonical Finding ids, then capture recalled reviewer outputs before changing conclusions.
8. Require Author Responses for every in-scope blocking material Canonical Finding that remains confirmed as an Author-facing blocker.
9. Create a new ArtifactVersion for each author revision.
10. Run linked re-review against Canonical Findings, Author Responses, revisions, and relevant Validation Evidence. Before generating another re-review prompt or skeleton, all existing protocol records must pass record/link validation and the finding must be below `max_remediation_rounds_per_finding`. If an unresolved `still_valid`, `disputed`, or `needs_human` decision has reached the cap, stop remediation, record or use the generated EscalationRecord in `escalations.md`, request a HumanDecision, and terminate as `escalated_to_human` or follow the human/policy decision.
11. Append ValidationEvidence for required validators to `rounds/round-NNN/validation.md`, using `scripts/consensus capture --phase validator` when available. Root `validation.md` is a run-level summary.
12. Escalate or record Human Decisions in `escalations.md` when needed.
13. Terminate with `scripts/consensus terminate` when available, otherwise create `report.md`. The file must start with human-readable finding blocks using `Problem`, `Explanation`, and `Required action`, then include parseable TerminationRecord and FinalReport sections.

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
- unresolved canonical finding IDs, if any;
- backlog location for non-blocking, deferred, and out-of-scope items.

## Reference Files

- `references/invocation.md`: portable `CAC`/`cac` alias behavior and out-of-box host contract.
- `references/protocol-checklist.md`: manual checklist, role contracts, and `document-consensus` defaults.
- `references/record-contract.md`: grouped Markdown record contract, run layout, and managed install rules.
- `templates/prompts.md`: manual Author, Reviewer, Author Response, Re-Review, and Final Report prompts.
