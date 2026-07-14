# Cross-Agent Consensus Invocation Contract

This package is meant to behave the same after install in Hermes, Codex, Claude, or another skill-aware host.

## Accepted User Triggers

Treat these as equivalent requests for this package:

```text
cac: <task_description>
CAC: <task_description>
cac@0.3.5: <task_description>
cac: do design for <feature>
cac: implement this feature and do review
Use CAC to do <task> with main <author> and validators <reviewers>.
Use cross-agent-consensus with task file <path>.
```

`CAC` and `cac` are invocation aliases only. Protocol records, run ids, run folders, installed package names, and user-facing final reports use `cross-agent-consensus`. `cac@X.Y.Z:` is the pinned-version spelling when a host supports version dispatch.

## Intended Meaning

`cac: <task_description>` means: the current/main model orchestrates an auditable run, an Author/main-task agent performs the requested work, and separate Reviewer/Validator agents validate the result before consensus is declared.

A pure review request is a special case where the task is only to review an existing artifact. CAC is not limited to review. It can cover design, implementation, writing, fixing, analysis, migration, planning, and other tasks as long as the result can be recorded as an ArtifactVersion and validated.

Examples:

```text
cac: do design for feature X
cac: implement this feature and do review
cac: write the migration plan and validate it with two reviewers
cac: fix the bug described in KPT-1234, then validate with tests and reviewer feedback
```

## Out-Of-Box Host Behavior

When a host loads this skill and the user invokes `CAC`:

1. Select this `cross-agent-consensus` package; do not require a separate host-specific harness rule.
2. Treat the text after `cac:` / `CAC:` / `cac@X.Y.Z:` as the TaskBrief objective unless a task file supplies richer fields. If `X.Y.Z` is present and differs from the installed version reported by `scripts/consensus --version`, fail clearly unless the host can dispatch that exact version.
3. Resolve configuration deterministically before initialization. The supported order is installed defaults, user-local config, project config, task-file config, then CLI flags. Use `scripts/consensus config show` or `scripts/consensus config paths` when an operator needs to inspect the effective values. Do not use review areas or lenses as reviewer identities. If configured reviewers need replacement, `scripts/consensus init --reviewer ...` requires `--allow-reviewer-config-override`; otherwise record review areas with `--review-focus`.
4. Resolve or create a `run_id` and create `runs/<run_id>/` under the current workspace or Git root before any Author, Reviewer, Validator, or external agent CLI is invoked. Use `scripts/consensus init` when present; otherwise use `templates/run-init.md`, `templates/review-batch.md`, and `templates/artifact-version.md` manually. New runs use `run.md` plus `rounds/round-001/`. When config-derived values are used, record a `ConfigResolution` section in `run.md`.
5. Run `scripts/consensus validate --pre-execution` when present before prompt generation or actor invocation. If scripts are unavailable, manually check the same required init records and paths.
6. If the task produces or changes something, run or request Author execution first and record the output as an ArtifactVersion. If the task is review-only, start from the review batch after initialization.
7. For Markdown or plain-text outputs, default to `profile=document-consensus`; for code/implementation tasks, require explicit validators in Policy before declaring consensus.
8. If the user supplied only a terse command, infer safe defaults documented in `SKILL.md` and stop only for missing material policy/scope/participant/validator decisions.
9. Store the exact prompt that will be sent to each Author/Reviewer/Validator under `rounds/round-NNN/prompts/` before invocation and reference it from the related record or notes. Use `scripts/consensus prompt` when present; it must run the equivalent pre-execution validation before writing non-draft prompts.
10. If an Author/Reviewer/Validator Participant Identity is bound to an explicitly named and authorized non-manual Execution Profile, run `scripts/consensus invocation-ready` when present, then invoke it with `scripts/consensus invoke-agent` after saving the prompt; do not stop at a manual handoff merely because it is cross-runtime. Do not replace a named Codex/Claude/Hermes reviewer with a host-internal subagent or in-chat review when the CLI can be run, because that bypasses `rounds/<round>/agents/<actor>/session-*` telemetry. A reviewer Execution Profile with argv requires a completed reviewer invocation session for RawReviewerOutput from that Participant Identity. Authorization means the operator has approved the exact command and profile binding for this run, or Policy declares `unattended_invocation: true` with scope limits. `invoke-agent` binds the Participant Identity, Participant Profile, Execution Profile, exact prompt and argv digests, working directory, and readable local ArtifactVersion digest to OperatorApproval; drift requires a new approval. Persistent config files must not contain secret values or enable unattended invocation. Store raw stdout/stderr or raw model output under `rounds/round-NNN/raw/` and/or the appropriate lifecycle record with `scripts/consensus capture` when present before normalization. A CLI is available when its binary is executable and its output can be captured to the run folder; authentication failures, timeouts, and non-zero exits are runtime errors to record, not availability failures.
11. Treat `/tmp`, host process logs, chat transcripts, and terminal scrollback as scratch unless copied into `runs/<run_id>/` and referenced from records.
12. Materialize every participant output as a durable run artifact before using it. This applies no matter how the participant was invoked: local subagent, host tool, external CLI, manual human handoff, background session, or another mechanism. The run must contain the relevant prompt when applicable, raw output under `rounds/round-NNN/raw/` or an immutable lifecycle record, and the corresponding protocol record such as `RawReviewerOutput`, `ValidationEvidence`, `ArtifactVersion`, or `ReReviewDecision`.
13. Preserve first-round reviewer isolation: reviewers must not see other reviewers' findings before producing Raw Findings. When invoking multiple Reviewers directly in the same fresh-review round, all same-round Reviewer prompts must be finalized and written to `rounds/round-001/prompts/reviewers/` before the first Reviewer CLI is invoked. Same-round Reviewer outputs must not be referenced in, or readable by, prompt construction for any other Reviewer in that round.
14. Before any remediation re-review prompt, skeleton, or invocation, enforce `max_remediation_rounds_per_finding`. `scripts/consensus prompt --phase rereview` and `scripts/consensus rereview-skeleton` are fail-closed gates: they validate existing records, refuse another re-review when unresolved `still_valid`, `disputed`, or `needs_human` decisions hit the cap, and write or reuse an EscalationRecord. Manual hosts must do the same and must not continue patch/re-review cycles after the cap.
15. Finish with `scripts/consensus validate --integrity`, `scripts/consensus validate --terminal`, and `scripts/consensus terminate` when present, or manually create `report.md`. The integrity pass recomputes readable ArtifactVersion, approved prompt, raw reviewer payload, and validation payload digests and verifies exact `live_cli` session provenance. The report must start with human-readable finding blocks that separate `Problem`, `Explanation`, and `Required action`, then include reviewer statistics, TerminationRecord, and FinalReport sections. Record an AbortRecord if the run cannot be made valid. For `escalated_to_human`, pending validators are reported in the FinalReport but do not block termination when an EscalationRecord or terminal HumanDecision supports the condition.

`manual-protocol` means every step is explicit, recorded, and auditable. It does not forbid a capable host from running an explicitly named and authorized external agent CLI, validator, or tool when the run configuration asks for it.

## If A Host Cannot Execute A Step

A host may be unable to run external CLIs, write files, or discover skills automatically. In that case it must still preserve the protocol contract:

- create the run folder and records it can create;
- write the exact manual command or prompt for the human operator into the active round's `prompts/` directory;
- ask the human to run missing steps manually;
- copy returned raw output into the run folder before continuing;
- do not claim `consensus_reached` until records and validators support it.

## Minimal Happy Paths

For a design task:

```text
cac: do design for notification retry behavior
```

Expected first actions:

1. Create `runs/notification-retry-behavior-consensus-001/`.
2. Create `run.md`, `artifacts/v1.md`, `rounds/round-001/round.md`, `rounds/round-001/prompts/`, and `rounds/round-001/raw/`.
3. Save the Author/main-task prompt under `rounds/round-001/prompts/`, preferably with `scripts/consensus prompt`.
4. Produce or request the design artifact and record it as ArtifactVersion `v1`.
5. Save reviewer/validator prompts under `rounds/round-001/prompts/`.
6. Capture raw outputs in the run folder before normalizing findings, preferably with `scripts/consensus capture`.

For an implementation task:

```text
cac: implement this feature and do review
```

Expected first actions:

1. Create the run folder and initial records before invoking any coding agent or command.
2. Record the implementation objective, target files, validators, and review scope in `run.md`.
3. Save the implementation prompt under `rounds/round-001/prompts/`.
4. Capture raw implementation output and the resulting patch/artifact locator in the run folder.
5. Run or request validation/review and capture raw outputs before normalization.

## Long-Running Invocation: Peek-Loop Default

`scripts/consensus invoke-agent` blocks foreground while the named CLI runs. For reviewer/author/validator invocations expected to exceed ~60s, the supported pattern is background + peek so the orchestrator can stream progress and retain `agent-cancel`. Foreground blocking is permitted only for sub-60s invocations.

Sketch (per-host transport varies):

```text
# 1. Launch in background (host-specific; tmux/nohup/&).
scripts/consensus invoke-agent --run <run> --actor <actor> --player <player> \
  --phase reviewer --prompt <prompt> --raw-output <raw> \
  --approved --command -- <argv> &

# 2. Poll with peek (or agent-status) at the configured interval.
while true; do
  scripts/consensus agent-peek --run <run> --actor <actor> --tail 40
  scripts/consensus agent-status --run <run> --actor <actor> --json \
    | jq -r '.state' | grep -qE '^(completed|failed|cancelled)$' && break
  sleep "${PEEK_INTERVAL:-180}"
done

# 3. Treat completed/failed/cancelled as terminal; agent-cancel on operator interrupt.
```

`invocation.peek.interval_seconds` (default 180) defines the polling cadence. The orchestrator should print a one-line progress update each iteration so the operator is not left blind during multi-minute reviews.

## Configuration Quick Reference

Config files use `schema_version: cross-agent-consensus-config-2`. Package defaults are in `config/defaults.yaml`; personal config belongs in `config/config.local.yaml`; project config belongs in `.cross-agent-consensus.yaml`. Task files use `schema_version: cross-agent-consensus-task-1` and may contain a run-scoped `config:` mapping.

The three configuration mappings have separate meanings:

- `participant_profiles`: role and instructions;
- `execution_profiles`: adapter, argv, optional model/reasoning effort, prompt transport, output mode, resume declaration, and environment-variable names;
- `participant_identities`: one Participant Profile and one Execution Profile selected for each stable Participant Identity.

Execution Profile argv is an explicit command preset. It does not bypass `invocation-ready`, does not authorize launch, and does not permit dynamic provider substitution. Version 0.13.0 rejects schema `cross-agent-consensus-config-1` and `reviewer_clis`; use `cross-agent-consensus-config-2`, `execution_profiles`, and `participant_identities`.
