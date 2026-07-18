# Cross-Agent Consensus

Runtime-neutral protocol and implementation profiles for coordinating multiple AI agents until they reach auditable consensus on an artifact.

The repository path still carries the historical `cross-model-consensus` name, but new public/protocol artifacts use `cross-agent-consensus`.

This repo separates:

1. `specs/` — normative protocol contract. No concrete model or tool names.
2. `skills/` — installable manual protocol packages. M2 ships `skills/cross-agent-consensus/`.
3. `implementations/` — runtime discovery and role-mapping notes for Hermes, Codex, Claude, Kimi, and later runtimes.
4. `schemas/` — portable structured data shapes from earlier protocol work.

Core rule: reviewer comments are claims, not commands. An author may accept, reject, partially accept, or ask for clarification, but every material finding must be explicitly handled and auditable.

## Current scope

The first target profile is still `document-consensus`, but the installed CAC package now supports a broader manual workflow for design, documentation, and implementation review artifacts. It remains an auditable manual protocol package, not an automatic cross-runtime code-modification runner.

## CAC skill package

Install the manual `cross-agent-consensus` skill package with the terse `cac` installer alias:

```bash
./scripts/install-cac --target hermes
./scripts/install-cac --target codex
./scripts/install-cac --target claude
./scripts/install-cac --target all --update
```

The current package version is recorded in `skills/cross-agent-consensus/VERSION`; `scripts/consensus --version` prints the installed version. The installer writes managed files from `skills/cross-agent-consensus/managed-manifest.json` and preserves local target modifications. Version 0.19.0 adds a first-class `kimi-cli` connector: `python3 -m cross_agent_consensus.kimi_cli` reads CAC's finalized prompt from stdin, invokes Kimi headless mode, parses Kimi JSONL, captures and resumes Kimi session IDs, inherits CAC cancellation, and reports `kimi --version`. Provider conversations still resume only through a bound `provider_session_captured` RunJournal entry.

Trigger examples after install:

```text
cac: do design for <feature>
cac: implement this feature and do review
Use CAC to do <task> with main <author> and validators <reviewers>.
```

`CAC`/`cac` are invocation aliases for generic task execution plus validation. Installed package and protocol records stay named `cross-agent-consensus`. A pure review is only one task shape, not the whole feature.

## Helper CLI

When `skills/cross-agent-consensus/scripts/consensus` is available, use it for deterministic run bookkeeping:

- `init` creates the run folder, participant records, first review batch, artifact record, and config-resolution record.
- `config show|validate|paths|setup` handles installed, user-local, project, task-file, and CLI configuration.
- `prompt` writes exact reviewer, author, validator, re-review, and final-report prompts into the run folder.
- `capture` preserves raw reviewer, validator, and manual evidence output as protocol records.
- `promote-draft` rejects worker-supplied protocol identity fields, captures the
  content-only JSON source, and writes CAC-owned Author, Reviewer, Validator,
  or synthesis output with deterministic identifiers and provenance.
- `snapshot-git` captures resolved revisions, staged/unstaged/target binary
  patches, and exact untracked bytes into a content-addressed snapshot, then
  creates the bound `ArtifactVersion`.
- `remediate --json` returns a byte-stable `BoundedRemediationPlan` for an
  opt-in `bounded-remediation` Policy. `--execute --approved` dispatches at
  most one phase and binds the plan-input digest to `OperatorApproval`.
- `validate --integrity` recomputes recorded artifact, prompt, and evidence
  digests; `validate --run-events` checks the hash-chained mutation journal and
  `.cac-events-anchor.json` against deletion, edits, and suffix truncation.
- `conclusion-validation` creates a `scope_triage` batch where the same reviewers validate proposed Normalized Finding conclusions. This is not a fresh review; reviewers answer `agree`, `disagree`, or `needs_human` and must include rationale.
- `invoke-agent`, `agent-status`, `agent-watch`, and `agent-cancel` run and monitor explicitly configured external reviewer CLIs with session telemetry.
- `next --json` derives a byte-stable `NextActionPlan` from validated protocol records, `events.jsonl`, `ConfigResolution`, and Execution Profiles. It names runnable actions, missing/conflicting records, pending checkpoint choices, and terminal status; it launches nothing and writes nothing. `record_journal_sha256` hashes only the ordered protocol-record frontmatter and RunJournal entries; integrity diagnostics separately cover artifact, prompt, evidence, and session files.
- `terminate` writes the terminal human verification artifact, `report.md`.

An `invoke-<participant>-reviewer` action means the bounded fresh-review
`consensus run` macro for that Participant Identity and ReviewBatch, not a bare
provider process call. The macro runs pre-execution record checks while writing
the exact prompt, checks invocation readiness, consumes the scoped approval,
launches the recorded Execution Profile, and appends RawReviewerOutput. Author,
AuthorResponse, re-review, and validator phases retain explicit record-producing
actions until their macros can append ArtifactVersion, AuthorResponse,
ReReviewDecision, and ValidationEvidence respectively. A pending operator
checkpoint withholds the `invoke-*` action until Policy authorizes the batch and
round; supervised execution consumes explicit exact-input approval in the
`consensus run` command itself.

Every CLI-created run uses `.cac.lock` to serialize protocol-record writes and
appends successful mutations to `events.jsonl`. `status` derives the current
phase from protocol records; `events.jsonl` records transitions without adding
a second mutable run-state file. External invocation approval binds the exact
prompt, runtime argv, and locally readable artifact version by SHA-256.

## Reviewers And Focus

Reviewer identities are participants. Review focus values are prompt lenses.

If saved configuration supplies reviewers such as `codex` and `claude`, `consensus init --reviewer ...` must not silently replace them. Use `--review-focus` for emphasis areas such as publication safety, API surface, or dispatcher policy. Use `--allow-reviewer-config-override` only when intentionally replacing configured reviewers.

Configured CLI reviewers must be invoked with `consensus invoke-agent` before their `RawReviewerOutput` is terminally valid. Direct `capture` is still valid for manual/imported evidence, but it is not live reviewer CLI telemetry.

## Participant And Execution Profiles

Configuration schema `cross-agent-consensus-config-2` separates three names:

- `ParticipantIdentity` is who acts in the protocol;
- `ParticipantProfile` assigns that identity a role and instructions;
- `ExecutionProfile` defines how CAC invokes it: adapter, argv, optional model and reasoning effort, prompt transport, output mode, resume declaration, and environment-variable allowlist.

`participant_identities` binds each stable Participant Identity to one Participant Profile and one Execution Profile. Switching that binding changes the invocation without renaming the reviewer, author, or validator. `ConfigResolution`, `OperatorApproval`, and `invocation.json` record the selected profile identifiers and effective argv. CAC inserts `model` and `reasoning_effort` into provider-specific argv, includes Participant Profile instructions in finalized prompts below immutable CAC rules, and passes only environment-variable names listed by the Execution Profile. Exact-input approval hashes the complete resolved Execution Profile.

`hermes-reviewer-default` binds the `hermes-cli` adapter to `python3 -m
cross_agent_consensus.hermes_cli --ignore-rules`. It supports declarative
`model`, JSONL output, provider-session continuation, process-group
cancellation, and version detection. Hermes owns authentication and provider
configuration under its home directory; CAC records no credential values.

`kimi-reviewer-default` binds the `kimi-cli` adapter to `python3 -m
cross_agent_consensus.kimi_cli`. CAC writes the finalized prompt to bridge
stdin, leaving the approved ExecutionProfile argv unchanged. The bridge invokes
Kimi with `--output-format stream-json`; the adapter parses assistant and tool
records and treats `session.resume_hint` as the terminal provider-session
receipt. An optional ExecutionProfile `model`, such as `kimi-code/k3`, maps to
the bridge's `--model` option. Kimi owns authentication and provider
configuration under `KIMI_CODE_HOME`; CAC records no credential values.

Version 0.13.0 accepts only `cross-agent-consensus-config-2`; migrate `reviewer_clis` entries to `execution_profiles` plus `participant_identities` before loading the configuration.

## Terminal Report

Terminal output is `report.md`, not `termination.md`.

The report starts with human-readable result blocks for each Normalized Finding:

```text
Problem:
<one-sentence issue>

Explanation:
<why/how it happens, with causal flow>

Required action:
<what must be fixed>
```

After the human sections, `report.md` still contains parseable `TerminationRecord` and `FinalReport` sections so protocol validation remains deterministic.

## Suggested first dogfood run

Use the protocol to review its own `specs/protocol.md`:

- Author Agent drafts/revises the protocol.
- Reviewer Agents independently inspect the artifact.
- Orchestrator records findings and author responses.
- Re-reviewers verify fixes or accept/reject rebuttals.
- Human Supervisor is asked only at consensus or unresolved material disagreement.

## Development checks

The helper package supports Python 3.11 and 3.13. Install the pinned development tools and run both quality gates from the repository root:

```bash
python -m pip install --requirement requirements-dev.txt
python -m pytest
python -m mypy
```
