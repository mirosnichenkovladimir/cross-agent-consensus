# Cross-Agent Consensus

Runtime-neutral protocol and implementation profiles for coordinating multiple AI agents until they reach auditable consensus on an artifact.

The repository path still carries the historical `cross-model-consensus` name, but new public/protocol artifacts use `cross-agent-consensus`.

This repo separates:

1. `specs/` — normative protocol contract. No concrete model or tool names.
2. `skills/` — installable manual protocol packages. M2 ships `skills/cross-agent-consensus/`.
3. `implementations/` — runtime discovery and role-mapping notes for Hermes, Codex, Claude, and later runtimes.
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

The current package version is recorded in `skills/cross-agent-consensus/VERSION`; `scripts/consensus --version` prints the installed version. The installer writes managed files from `skills/cross-agent-consensus/managed-manifest.json` and preserves local target modifications.

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
- `conclusion-validation` creates a `scope_triage` batch where the same reviewers validate normalized Canonical Finding conclusions. This is not a fresh review; reviewers answer `agree`, `disagree`, or `needs_human` and must include rationale.
- `invoke-agent`, `agent-status`, `agent-watch`, and `agent-cancel` run and monitor explicitly configured external reviewer CLIs with session telemetry.
- `terminate` writes the terminal human verification artifact, `report.md`.

## Reviewers And Focus

Reviewer identities are participants. Review focus values are prompt lenses.

If saved configuration supplies reviewers such as `codex` and `claude`, `consensus init --reviewer ...` must not silently replace them. Use `--review-focus` for emphasis areas such as publication safety, API surface, or dispatcher policy. Use `--allow-reviewer-config-override` only when intentionally replacing configured reviewers.

Configured CLI reviewers must be invoked with `consensus invoke-agent` before their `RawReviewerOutput` is terminally valid. Direct `capture` is still valid for manual/imported evidence, but it is not live reviewer CLI telemetry.

## Terminal Report

Terminal output is `report.md`, not `termination.md`.

The report starts with human-readable result blocks for each Canonical Finding:

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
