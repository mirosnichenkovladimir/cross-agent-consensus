# Cross-Agent Consensus File-Based Scripts Design

## Summary

This design specifies the first automation layer for the `cross-agent-consensus` skill package: deterministic scripts that skills can invoke to create, inspect, and validate CAC run folders.

The goal is to reduce protocol bookkeeping errors while preserving the M2 boundary:

- scripts may create folders, copy or render templates, parse records, verify deterministic invariants, write prompt/evidence payload files, and report readiness;
- scripts must not choose models/providers, invent policy or participants, perform semantic normalization, apply reviewer suggestions, or invoke external agents unless all recorded M2 gates are satisfied.

This design is based on the completed CAC validation run:

- run: `runs/cross-agent-consensus-20260529T075548Z-codex-claude-scriptability/`
- reviewers: `codex/gpt-5.5` and `claude/opus4-7`
- terminal condition: `consensus_reached`
- final report: `runs/cross-agent-consensus-20260529T075548Z-codex-claude-scriptability/termination.md`

Both reviewers found no blocking issue with the proposed automation boundary. Their non-blocking corrections are incorporated below.

## Current State

The repo currently has the protocol, profile, templates, and installer, but no installed CLI scripts:

- `specs/protocol.md` defines runtime-neutral records, invariants, round model, finding lifecycle, and consensus predicate.
- `skills/cross-agent-consensus/references/record-contract.md` defines the M2 Markdown layout, required frontmatter, enum values, and managed-install rules.
- `skills/cross-agent-consensus/templates/` contains Markdown templates for run records.
- `runners/file-based-mvp/README.md` explicitly says the first useful command should only scaffold a run directory and copy templates.
- `skills/cross-agent-consensus/managed-manifest.json` installs docs/templates only; no scripts are available to installed skills.
- `scripts/install-cac` copies only manifest-listed managed files and preserves local target modifications.

The skill text already requires hosts to create the run folder, save exact prompts, and capture raw outputs before author/reviewer/validator work. Today that work is manual and easy to get subtly wrong.

## Gap

The missing automation is deterministic and local:

- no command creates a canonical run folder from the installed skill package;
- no command validates frontmatter, enum values, cross-record links, reviewer isolation, or terminal readiness;
- no command generates prompt payload files from recorded state while gating on required pre-execution records;
- no command captures raw output/evidence with provenance metadata;
- no installed-skill entrypoint exists in the managed manifest;
- the skill cannot portably invoke helper scripts after installation.

The design must also address reviewer corrections:

- add a preflight/invocation-readiness command;
- tighten external invocation gates;
- create or validate `backlog.md` and `escalations.md`;
- install script entrypoints through `managed-manifest.json`;
- validate participant identity distinctness and one-file-per-reviewer-per-round layout;
- require `termination.md` to contain both TerminationRecord and FinalReport;
- decide how `consensus prompt` relates to pre-execution validation.

## Design Goals

- Provide a small, portable CLI usable from installed skills and from the repo.
- Keep the CLI deterministic and file-based.
- Make every script action auditable in the run folder when it changes protocol state.
- Fail closed before unsafe external invocation or invalid terminal claims.
- Preserve raw evidence and existing records; append or create new records instead of rewriting immutable reviewer output.
- Keep implementation dependency-light, preferably Python standard library plus optional PyYAML if already available.

## Non-Goals

- No automatic model/provider selection.
- No unattended cross-runtime runner.
- No semantic classification or materiality decisions by script.
- No automatic patch application from reviewer suggestions.
- No replacement for the orchestrator's responsibility to normalize findings and evaluate policy.
- No global installation outside the managed skill package in this phase.

## Package Layout

Add scripts inside the installable skill package:

```text
skills/cross-agent-consensus/
  scripts/
    consensus
    cac_tool.py  # compatibility shim for historical callers
  cross_agent_consensus/
    cli.py
```

`scripts/consensus` is the supported executable entrypoint. It should be a small shell or Python wrapper that resolves its own skill root and runs `python -m cross_agent_consensus.cli`.

`cross_agent_consensus.cli` contains the CLI implementation and should use paths relative to the installed skill root for templates and references. It must not assume the repo checkout exists after install. `scripts/cac_tool.py` may remain only as a compatibility shim for historical direct callers.

Update `skills/cross-agent-consensus/managed-manifest.json` to include:

- `scripts/consensus`
- `scripts/cac_tool.py`
- `cross_agent_consensus/cli.py`

The installer currently uses `shutil.copy2`, which preserves mode bits on normal files. Validation should confirm the copied `scripts/consensus` remains executable in target installs.

## CLI Surface

### `consensus init`

Purpose: create a run folder and initial records before any author, reviewer, validator, or external CLI invocation.

Suggested interface:

```bash
scripts/consensus init \
  --task "Analyze CAC scriptability" \
  --profile document-consensus \
  --artifact-locator docs/foo.md \
  --author author-codex-main \
  --orchestrator orchestrator-codex-gpt-5 \
  --reviewer reviewer-claude-opus4-7 \
  --reviewer reviewer-codex-gpt-5-5 \
  --human-supervisor none \
  --run-root runs
```

Behavior:

- generate `run_id` when not supplied;
- create the full canonical tree:

```text
runs/<run_id>/
  init.md
  review-batches.md
  artifacts/v1.md
  reviews/
  normalization/
  author-responses/
  rereviews/
  validation.md
  payloads/prompts/
  payloads/raw/
  escalations.md
  backlog.md
```

- do not create `termination.md` during init unless explicitly requested by a terminal command;
- hydrate safe defaults for `document-consensus`;
- leave unknown required material decisions as explicit placeholders or fail with actionable missing-field output;
- compute `content_hash_or_null` only when artifact content is locally readable or copied into the run; otherwise write `null`.

### `consensus status`

Purpose: show the current run state without modifying it.

Checks:

- required files and directories exist;
- required init sections exist;
- missing fields before author/reviewer/validator invocation;
- active review batches and reviewer prompt/output status;
- validator status summary;
- terminal readiness summary.

Output should be human-readable by default, with `--json` as an optional later addition.

### `consensus validate`

Purpose: deterministic conformance checks.

Validation groups:

- `--pre-execution`: run folder, init records, ReviewScope, Policy, Participants, ReviewBatch, ArtifactVersion, prompt/raw directories.
- `--records`: common frontmatter, type-specific fields, enum values, record ids, section headings.
- `--links`: referenced artifact versions, review batches, raw finding ids, canonical finding ids, validation evidence ids.
- `--reviewer-isolation`: first-round reviewers have one file per reviewer per round, same-round prompt files are finalized before invocation when timestamps are available, reviewer identity in filename matches frontmatter.
- `--participants`: orchestrator identity distinct from author and every reviewer; reviewer identities unique in the run or at least within a review batch.
- `--terminal`: terminal predicates and final report requirements.

Terminal validation for `consensus_reached` must require:

- no unresolved in-scope blocking material findings;
- no unresolved in-scope `needs_human`;
- every required validator is `pass` or `waived`;
- waived validators include authority and rationale;
- `termination.md` contains both TerminationRecord and FinalReport sections;
- TerminationRecord and FinalReport agree on `terminal_condition` and `final_artifact_version_id_or_null`;
- FinalReport declares unresolved blockers and separates backlog.

### `consensus prompt`

Purpose: generate exact prompt payload files under `payloads/prompts/`.

Policy:

- `consensus prompt` must call the equivalent of `consensus validate --pre-execution` before emitting prompts.
- If pre-execution validation fails, no prompt is written unless `--force-draft` is supplied; draft prompts must be clearly named and must not be used for invocation records.
- Generated prompts must include only the records permitted for the role and review mode.
- First-round reviewer prompts must not include other reviewers' findings or outputs.

Supported phases:

- `author`
- `reviewer`
- `validator`
- `author-response`
- `rereview`
- `final-report`

### `consensus capture`

Purpose: capture raw output or bulky evidence into the run folder with provenance.

Supported phases:

- `author`
- `reviewer`
- `validator`
- `manual`

Inputs:

```bash
scripts/consensus capture \
  --run runs/<run_id> \
  --phase reviewer \
  --actor reviewer-claude-opus4-7 \
  --review-batch review-batch-round-1-fresh_review \
  --artifact-version v1 \
  --source-file /tmp/reviewer.out
```

Behavior:

- copy raw payload into `payloads/raw/`;
- compute sha256 when local content exists;
- record provenance metadata:
  - actor identity;
  - role/phase;
  - source mode: stdin, file, command stdout, manual paste;
  - source command/provider when known;
  - captured timestamp;
  - payload sha256 or null;
  - target artifact version;
  - review batch when relevant;
- scaffold or append the appropriate lifecycle record when deterministic:
  - RawReviewerOutput wrapper for reviewer output;
  - ValidationEvidence shell for validator output;
  - ArtifactVersion shell for author output when requested.

The command must not parse raw reviewer text into canonical findings or decide materiality.

### `consensus new-artifact`

Purpose: create a new ArtifactVersion record.

Behavior:

- require unique `artifact_version_id`;
- link `predecessor_id_or_null`;
- require `content_locator`;
- compute `content_hash_or_null` only when content is locally readable or copied into the run;
- record `produced_by`;
- refuse to overwrite an existing ArtifactVersion.

### `consensus response-skeleton`

Purpose: scaffold AuthorResponse sections for known canonical findings.

Behavior:

- find in-scope blocking material CanonicalFindings that need AuthorResponse;
- generate appendable response sections with placeholders;
- never choose response type or rationale.

### `consensus rereview-skeleton`

Purpose: scaffold ReReviewDecision files for linked findings.

Behavior:

- create one rereview file per reviewer per round;
- include linked CanonicalFinding ids, AuthorResponse ids, and target ArtifactVersion ids;
- never decide `verified`, `still_valid`, `needs_human`, etc.

### `consensus invocation-ready`

Purpose: fail-closed readiness check before a host invokes an explicitly selected external CLI/runtime.

Checks:

- participant identity is recorded;
- runtime command/model/provider was explicitly selected by user or Policy;
- run folder and initial records exist;
- exact prompt file exists and is referenced or ready to be referenced;
- approval or scoped `unattended_invocation: true` policy is recorded;
- raw-output destination under `payloads/raw/` is known;
- binary is executable when a CLI is selected;
- authentication/runtime probe result is recorded when available.

If the host cannot execute directly, the command should emit the exact manual command and the expected raw-output destination. It must not substitute another model or provider.

### `consensus terminate`

Purpose: create terminal records only after deterministic terminal validation passes or after an explicit abort/escalation path.

Behavior:

- call `consensus validate --terminal`;
- require a terminal condition supplied by the orchestrator or human decision;
- for `consensus_reached`, refuse if any terminal predicate fails;
- write `termination.md` containing both TerminationRecord and FinalReport sections;
- include final artifact id, unresolved findings, validator summary, and backlog path;
- never invent a waiver, terminal condition, or human decision.

## Record Parsing

The first implementation can use a simple Markdown-section parser:

- scan headings beginning with `## <RecordType> <record_id>`;
- read the first `---` frontmatter block after that heading;
- parse YAML conservatively.

If no YAML library is added, support the subset used by templates:

- scalar strings;
- booleans;
- null;
- flat lists;
- nested mappings for known fields such as `round_limits`.

If PyYAML is already available in target hosts, use it; otherwise keep a small internal parser and validate only deterministic fields needed by the CLI.

Frontmatter is authoritative. Body text can be ignored for validation except for raw-output fenced-block presence checks.

## Error Handling

Every command should:

- exit `0` on success;
- exit `1` for invalid user input or missing required fields;
- exit `2` for protocol validation failures;
- exit `3` for runtime readiness failures;
- print actionable messages naming the file, record id, field, and expected value when possible.

Commands that write files must not partially overwrite existing records. Prefer writing to a temp file in the same directory and replacing only newly created files. For append operations, append new sections; do not rewrite immutable raw reviewer output.

## Skill Integration

Update `skills/cross-agent-consensus/SKILL.md` and `references/invocation.md` to say:

- when invoked through `cac`, the host should run `scripts/consensus init` when available;
- before generating prompts, run `scripts/consensus prompt` or equivalent pre-execution validation;
- before direct external CLI invocation, run `scripts/consensus invocation-ready`;
- after command/manual output, run `scripts/consensus capture`;
- before terminal claims, run `scripts/consensus validate --terminal` and `scripts/consensus terminate`.

The skill must still describe manual fallback: if scripts are unavailable, the host follows the existing template workflow.

## Managed Installation

After adding scripts:

1. Compute sha256 for each new managed file.
2. Add manifest entries under `managed_files`.
3. Confirm `scripts/install-cac --target codex --update` preserves executable bit for `scripts/consensus`.
4. Confirm updates preserve local modifications by using the existing state-file conflict mechanism.
5. Avoid deleting unmanaged files in installed skill directories.

No target-specific wrapper is required in this phase; installed hosts invoke the script relative to the installed skill root.

## Execution Plan

1. Add the CLI skeleton:
   - `skills/cross-agent-consensus/scripts/consensus`
   - `skills/cross-agent-consensus/cross_agent_consensus/cli.py`
   - `skills/cross-agent-consensus/scripts/cac_tool.py` as a compatibility shim only
   - `argparse` subcommands with `--help`.
2. Implement shared helpers:
   - skill-root/template resolution;
   - run-id generation and slugging;
   - Markdown frontmatter section parsing;
   - sha256 calculation;
   - atomic file creation and append helpers.
3. Implement `init`:
   - create canonical tree;
   - hydrate `init.md`, `review-batches.md`, `artifacts/v1.md`, `validation.md`, `backlog.md`, `escalations.md`.
4. Implement `status` and `validate --pre-execution`.
5. Implement `validate --records`, `--links`, `--participants`, and reviewer-file checks.
6. Implement `prompt` with pre-execution gating.
7. Implement `capture` with provenance metadata and validator evidence scaffolding.
8. Implement `new-artifact`, `response-skeleton`, and `rereview-skeleton`.
9. Implement `invocation-ready`.
10. Implement `validate --terminal` and `terminate`.
11. Update skill docs and managed manifest.
12. Add tests and run install smoke checks.

## Test Plan

Use fixture-style tests around temporary run directories:

- `init` creates every expected file and directory.
- `init` refuses unsafe paths and duplicate run ids unless explicitly allowed.
- generated `init.md` contains TaskBrief, Policy, Participants, and ReviewScope sections.
- `validate --pre-execution` passes for an initialized document-consensus run and fails when each required section is removed.
- participant validation catches orchestrator/author/reviewer identity collisions.
- reviewer-file validation catches filename/frontmatter mismatch and permits multiple reviewers in the same round.
- `prompt` refuses incomplete init records.
- `capture` copies raw output, computes sha256, and records provenance without rewriting raw blocks.
- `new-artifact` respects `content_hash_or_null: null` for non-local locators.
- `terminate` refuses `consensus_reached` with unresolved in-scope blocking material findings.
- `terminate` writes both TerminationRecord and FinalReport sections when predicates pass.
- manifest validation catches missing new scripts or stale hashes.
- install smoke test confirms installed `scripts/consensus --help` runs.

## Open Decisions

- Whether to require PyYAML or keep a strict local subset parser.
- Whether `consensus capture` should append lifecycle records by default or require `--append-record`.
- Whether `consensus prompt --force-draft` is useful enough to include in M2 automation.
- Whether `invocation-ready` should execute lightweight auth probes or only validate recorded probe evidence.

## Related Sessions

- 2026-05-29, CAC run `cross-agent-consensus-20260529T075548Z-codex-claude-scriptability`: validated scriptability plan with `codex/gpt-5.5` and `claude/opus4-7`; no blockers, nine non-blocking canonical findings incorporated into this design.
