# Cross-Agent Consensus Skill Configuration Design

## Summary

Add explicit configuration support for the `cross-agent-consensus` skill package so repeat runs do not require every participant, run-root, round-limit, and reviewer CLI option to be restated on each `consensus init` or invocation.

The configuration model should be layered and conservative:

1. CLI flags
2. task-file config
3. project config
4. user-local config
5. installed skill defaults

Higher layers override lower layers. The design keeps the M2 boundary intact: config may supply deterministic defaults and explicit runtime commands, but it must not choose models/providers dynamically, invent participants, bypass prompt/raw-output capture, or weaken `invocation-ready` approval gates.

## Current State Analysis

The repo already has the core protocol package and helper CLI:

- `skills/cross-agent-consensus/SKILL.md` defines the manual CAC lifecycle and requires run folders, prompts, raw outputs, and validation evidence before terminal claims.
- `skills/cross-agent-consensus/scripts/consensus` exposes deterministic subcommands such as `init`, `prompt`, `capture`, `invocation-ready`, and `terminate`; the command implementation lives in `cross_agent_consensus.cli`.
- `consensus init` accepts participants, `run_root`, profile, review scope, and round limits as command-line flags.
- `consensus invocation-ready` checks that an actor is recorded, the prompt is in an allowed prompt directory, raw output is in an allowed raw directory, the command exists on `PATH`, and either `--approved` or recorded unattended policy permits invocation.
- `skills/cross-agent-consensus/managed-manifest.json` lists files managed by `scripts/install-cac`; updates overwrite only managed files when the target copy is unmodified.
- `skills/cross-agent-consensus/references/record-contract.md` explicitly allows user-local files in installed skill directories when those files are not listed as managed.

There is no configuration loader today. Defaults are split across CLI parser defaults, Python constants, profile documentation, templates, and operator memory.

## Gap

The missing behavior is not a cross-runtime runner. The missing behavior is deterministic default resolution:

- no standard location for package defaults;
- no standard location for user-local or project-local overrides;
- no schema for participants, run root, round limits, or reviewer CLI mappings;
- no way to inspect the effective config before creating a run;
- no way for `consensus init` to use saved defaults while preserving explicit CLI overrides;
- no installer rule explaining which config files are managed and which are preserved;
- no documented relationship between configured reviewer CLIs and the existing `invocation-ready` gate.

## Design Goals

- Make common CAC runs shorter and repeatable.
- Keep effective configuration auditable and inspectable.
- Preserve existing CLI behavior when no config exists.
- Separate managed package defaults from user-local state.
- Support explicit reviewer CLI commands without introducing automatic provider/model selection.
- Keep config parsing dependency-light, preferably Python standard library plus the existing minimal YAML renderer/parser strategy if possible.

## Non-Goals

- No implementation work before design approval.
- No unattended cross-runtime runner.
- No dynamic model/provider selection.
- No secrets in config files.
- No automatic application of reviewer feedback.
- No change to the consensus predicate or required validators.

## Configuration Locations

### Installed Skill Defaults

Add a managed default config file:

```text
skills/cross-agent-consensus/config/defaults.yaml
```

This file is shipped with the skill package and listed in `managed-manifest.json`. It is safe to overwrite on `install-cac --update` because it contains only package defaults, not user choices.

Use it for stable baseline defaults:

```yaml
schema_version: cross-agent-consensus-config-1
defaults:
  profile: document-consensus
  run_root: runs
  round_limits:
    max_fresh_review_rounds: 1
    max_fresh_review_rounds_without_human_approval: 2
    max_remediation_rounds_per_finding: 2
invocation:
  require_invocation_ready: true
  direct_reviewer_cli: explicit_only
```

### User-Local Config

User-local config is personal operator state and must not be listed in `managed-manifest.json`.

Default host-specific locations:

```text
${CODEX_HOME:-$HOME/.codex}/skills/cross-agent-consensus/config/config.local.yaml
${CLAUDE_HOME:-$HOME/.claude}/skills/cross-agent-consensus/config/config.local.yaml
${HERMES_HOME:-$HOME/.hermes}/skills/cross-agent-consensus/config/config.local.yaml
```

The installed skill directory is acceptable because the current installer preserves files not listed as managed. The docs should explicitly warn that `config/defaults.yaml` is managed, while `config/config.local.yaml` is user-owned. If a legacy `config.local.yaml` exists at the skill root, `consensus config validate` should warn and ignore it unless the operator passes `--config` explicitly.

Support an environment override for unusual installs:

```text
CROSS_AGENT_CONSENSUS_CONFIG=/path/to/config.yaml
```

When set, this path acts as the user-local config layer and should be shown in config diagnostics.

### Project Config

Project config is checked into a repository when a team wants shared defaults:

```text
.cross-agent-consensus.yaml
```

The CLI should search from the current working directory upward until a VCS root or filesystem root. Project config overrides user-local config and installed defaults.

Project config is the right place for team-standard identities, run roots, round limits, and reviewer command presets that are not secrets.

Discovery details:

- load at most one project config file;
- start from `cwd` and stop at the first `.cross-agent-consensus.yaml` found while walking upward;
- treat a directory containing `.git` as a VCS root whether `.git` is a directory or a worktree file;
- when no project file is found before the VCS or filesystem root, continue without project config;
- `consensus config paths` must show the selected file or the reason no project config was loaded.

### Task-File Config

Task files are run-specific inputs. A later `consensus init --task-file <path>` should allow task-level config to override project and user defaults for one run.

The task file can include:

```yaml
schema_version: cross-agent-consensus-task-1
task:
  objective: Design configuration support for the CAC skill.
  artifact_locator: docs/plans_and_designs/cac-skill-configuration.md
  success_criteria:
    - Design document exists and passes document-consensus review.
config:
  run_root: runs
  participants:
    author: codex-implementer
    reviewers:
      - claude-reviewer
      - codex-independent-reviewer
```

The current `--task "..."` path remains supported. Task-file support can be added after the base config loader if needed.

## Precedence And Merge Rules

Effective config resolution:

```text
installed defaults
  -> user-local config
  -> project config
  -> task-file config
  -> CLI flags
```

Merge rules:

- Scalars replace lower-precedence values.
- Maps deep-merge.
- Lists replace wholesale unless a specific field documents keyed merge behavior.
- `null` means "unset this inherited value" where the field is optional.
- CLI flags always win, including repeated flags such as `--reviewer`.

For participants, prefer replacement over implicit merging. Combining reviewer lists from multiple layers could accidentally invoke more reviewers than intended.

## Config Schema

Suggested schema:

```yaml
schema_version: cross-agent-consensus-config-1

defaults:
  profile: document-consensus
  run_root: runs
  round_limits:
    max_fresh_review_rounds: 1
    max_fresh_review_rounds_without_human_approval: 2
    max_remediation_rounds_per_finding: 2

participants:
  orchestrator: orchestrator-codex-default
  author: codex-implementer
  reviewers:
    - claude-reviewer
    - codex-independent-reviewer
  human_supervisor: none

reviewer_clis:
  claude-reviewer:
    command:
      - claude
      - --print
    prompt_transport: stdin
    stdout_capture: raw_output
    stderr_capture: raw_error
  codex-independent-reviewer:
    command:
      - codex
      - exec
      - "-"
    prompt_transport: stdin
    stdout_capture: raw_output
    stderr_capture: raw_error

invocation:
  require_invocation_ready: true
  direct_reviewer_cli: explicit_only
```

Field notes:

- `reviewer_clis.<identity>.command` is an argv array, not a shell string.
- The command identity must match a recorded reviewer identity before use.
- Config should not store API keys, tokens, or secret environment values.
- If environment is needed later, allow env var names only, not values.
- `direct_reviewer_cli: explicit_only` means config can provide commands, but direct invocation still requires prompt creation, raw-output path reservation, and `consensus invocation-ready`.
- Persistent installed, user-local, and project config layers must not enable unattended invocation. `unattended_invocation.enabled: true` is valid only in explicit run-scoped input, such as a task file or CLI option, and only with non-empty scope limits. `config validate` must fail if a persistent config file tries to enable it.
- `reviewer_clis` keys may be a superset of `participants.reviewers`; unused entries are allowed with a warning. A reviewer without a matching CLI entry falls back to manual handoff. A run must record whether a CLI mapping was resolved before direct invocation.
- `prompt_transport` v1 supports only `stdin`. Future placeholder modes such as `arg:{prompt_path}` or `file:{prompt_path}` are out of scope until specified.
- `stdout_capture: raw_output` means stdout is captured to the reserved raw output path passed to `invocation-ready`.
- `stderr_capture: raw_error` means stderr is captured to a sibling raw evidence file and referenced from the reviewer capture metadata. `stderr_capture: raw_output` may be added later, but v1 should keep stdout and stderr separate.

## CLI Changes

Add config-aware options to commands that need defaults:

```bash
consensus init \
  --config .cross-agent-consensus.yaml \
  --task "Design config support" \
  --artifact-locator docs/plans_and_designs/cac-skill-configuration.md
```

```bash
consensus init --no-config ...
```

Add an inspection command:

```bash
consensus config show
consensus config show --json
consensus config validate
consensus config paths
consensus config setup
```

`config show` should print:

- loaded config files in precedence order;
- ignored missing config locations;
- effective values after merge;
- warnings for invalid or unknown keys.

`consensus init` should use effective config to fill omitted arguments:

- `--run-root` from `defaults.run_root`;
- `--profile` from `defaults.profile`;
- `--max-fresh-review-rounds`, `--max-fresh-review-rounds-without-human-approval`, and `--max-remediation-rounds` from `defaults.round_limits`;
- `--author`, `--orchestrator`, `--reviewer`, and `--human-supervisor` from `participants`.

Any value that remains required after config resolution should fail with the same style of actionable error as today.

`--no-config` skips user-local, project, and task-file config layers. Installed defaults still apply because they are package behavior; explicit CLI flags remain the way to override those defaults.

Persistent config must not fill the run's `ReviewScope` objective or in-scope/out-of-scope content. Those are run-scoped protocol decisions and must come from CLI flags, a task file, or the document-consensus profile defaults. If a non-default ReviewScope is required and no run-scoped source supplies it, `consensus init` should fail with an actionable message. Task files may include `review_scope` because they are explicit inputs to a single run.

## Interactive Setup

Add a user-friendly setup wizard:

```bash
consensus config setup
```

The wizard should guide users through safe choices and write a config file without requiring them to know the YAML shape upfront.

Default behavior:

- ask where to write config: `user-local` or `project`;
- default to `user-local` for personal setup;
- show the exact output path before writing;
- detect available reviewer CLIs from `PATH`, such as `claude` and `codex`;
- let the user pick reviewer identities from detected presets or enter a custom identity;
- let the user pick common round-limit presets;
- preview the generated YAML;
- run `consensus config validate` before saving;
- never enable unattended invocation from the wizard;
- never ask for or write secrets;
- refuse to overwrite an existing config unless the user confirms.

Suggested prompt flow:

```text
Where should config be saved?
  1. User-local config (recommended)
  2. Project config in this repository

Which reviewers should be configured?
  [x] claude-reviewer  command: claude --print
  [x] codex-independent-reviewer  command: codex exec -
  [ ] Add custom reviewer

Run root:
  1. runs (recommended)
  2. .cac/runs
  3. custom

Fresh review rounds:
  1. 1 (recommended)
  2. 2

Save this config? [y/N]
```

Output targets:

- user-local setup writes `config/config.local.yaml`;
- project setup writes `.cross-agent-consensus.yaml`;
- `--output <path>` writes to an explicit path after confirmation;
- `--dry-run` prints the generated config without writing;
- `--yes` may skip final confirmation only when the target file does not already exist.

The wizard should be deterministic and local. It may detect CLI executables, but it must not call reviewer CLIs, authenticate providers, select models, or start a CAC run. It only writes config and validates it.

## Reviewer CLI Behavior

Reviewer CLI mappings are defaults for explicit invocation, not an automatic runner.

Flow:

1. For a fresh-review batch, write every same-round reviewer prompt under `rounds/round-NNN/prompts/reviewers/` before invoking any reviewer CLI.
2. `consensus prompt --phase reviewer --actor <reviewer>` writes each exact reviewer prompt.
3. The orchestrator resolves `reviewer_clis.<reviewer>.command` from effective config.
4. `consensus invocation-ready --actor <reviewer> --prompt <prompt> --raw-output <raw> --command ...` verifies all gates.
5. The orchestrator invokes the configured command only if `invocation-ready` passes and operator approval or recorded run-scoped unattended policy permits it.
6. Raw stdout and stderr are captured under the run folder before normalization.

This keeps the user-requested direct reviewer CLI support while preserving auditability.

`invocation-ready` should either gain a same-round prompt-completeness check or be preceded by a new deterministic check such as `consensus validate --reviewer-invocation`. The check must fail for fresh-review batches when any recorded same-round reviewer lacks a finalized prompt.

## Config Audit Evidence

Before using any config-derived value to initialize a run or invoke a reviewer, the CLI must record a `ConfigResolution` section in `run.md` or a linked `config-resolution.md` record. The record shape should be stable and machine-readable:

```yaml
record_type: ConfigResolution
schema_version: m2-markdown-1
run_id: <run_id>
actor_identity: <orchestrator>
created_at: <timestamp>
config_resolution_id: config-resolution-001
config_schema_version: cross-agent-consensus-config-1
sources:
  - layer: installed_defaults
    path: skills/cross-agent-consensus/config/defaults.yaml
    present: true
    sha256_or_null: <hash>
  - layer: user_local
    path: <path-or-null>
    present: false
    sha256_or_null: null
  - layer: project
    path: .cross-agent-consensus.yaml
    present: true
    sha256_or_null: <hash>
effective_values:
  defaults.run_root:
    value: runs
    source_layer: installed_defaults
  participants.reviewers:
    value:
      - claude-reviewer
      - codex-independent-reviewer
    source_layer: project
diagnostics:
  warnings: []
  errors: []
redactions:
  - field: reviewer_clis.*.env
    rule: env var names only, values not recorded
```

No config-derived value should be used unless its source and effective consumed value are represented in this record. Security-sensitive fields, including reviewer commands and invocation policy, require per-field provenance.

## Installer Behavior

Update `managed-manifest.json` to include only package-owned config examples/defaults:

```text
config/defaults.yaml
config/config.local.example.yaml
```

Do not list `config/config.local.yaml` as managed.

`scripts/install-cac --update` should continue to preserve unmanaged files. No delete step should be added for the installed skill directory.

Documentation should state:

- edit `config/config.local.yaml` for personal defaults;
- do not edit `config/defaults.yaml` in an installed skill unless intentionally modifying a managed package file;
- team defaults belong in `.cross-agent-consensus.yaml` in the project repo.

## Implementation Plan

1. Add `config/defaults.yaml` and `config/config.local.example.yaml`.
2. Add config paths to `managed-manifest.json` and update hashes.
3. Implement a config loader in `cross_agent_consensus.config` and wire it through `cross_agent_consensus.cli`:
   - discover installed defaults from the skill root;
   - discover user-local config from `CROSS_AGENT_CONSENSUS_CONFIG` or host home;
   - discover project config by walking upward;
   - optionally load task-file config when `--task-file` exists;
   - merge layers and collect diagnostics.
4. Add `consensus config show|validate|paths|setup`.
5. Implement the interactive setup wizard and keep it limited to local file generation, validation, and safe CLI detection.
6. Wire effective config into `consensus init` defaults without changing explicit flag behavior.
7. Wire reviewer CLI lookup into the orchestration guidance and, if a helper command is added, require `invocation-ready` before use.
8. Add `ConfigResolution` record creation before config-derived init or invocation behavior is used.
9. Bump `VERSION` according to semver. This feature is at least a minor version because it adds config files and CLI behavior; any default-value behavior change must be called out in release notes and reflected in `cross_agent_consensus_version` during `init`.
10. Update `SKILL.md`, `references/invocation.md`, and `references/record-contract.md` to document config behavior.
11. Add tests for resolution, precedence, manifest behavior, interactive setup, and backward compatibility.

## Validation Plan

Add focused tests in `tests/test_cac_tool.py` or a new config-specific test module:

- no config preserves current `consensus init` behavior;
- installed defaults are loaded from the skill root;
- user-local config overrides installed defaults;
- project config overrides user-local config;
- task-file config overrides project config;
- CLI flags override every config layer;
- reviewer list replacement does not accidentally merge lower-precedence reviewers;
- invalid schema version fails clearly;
- unknown keys produce warnings or validation errors according to the selected strictness;
- `reviewer_clis` commands are argv arrays, not shell strings;
- persistent config cannot enable unattended invocation;
- `ConfigResolution` records source paths, hashes, effective consumed values, diagnostics, and per-field provenance for reviewer commands and invocation-related fields;
- fresh-review reviewer invocation fails if any same-round reviewer prompt is missing;
- persistent config cannot silently fill ReviewScope objective or in-scope/out-of-scope lists;
- `defaults.round_limits.max_fresh_review_rounds_without_human_approval` is loaded and mapped;
- `--no-config` skips user-local, project, and task-file config while preserving installed defaults;
- reviewer CLI key mismatches warn, and missing reviewer CLI mappings fall back to manual handoff;
- project config discovery handles `.git` directories and worktree `.git` files;
- secret-looking keys or high-entropy values produce `config validate` warnings unless an explicit comment escape hatch is present;
- configured reviewer command still fails `invocation-ready` without approval or recorded unattended policy;
- installer manifest includes managed default/example config and excludes `config.local.yaml`.
- `consensus config setup --dry-run` prints valid YAML without writing;
- `consensus config setup` refuses to overwrite existing config without confirmation;
- user-local setup writes under `config/config.local.yaml`;
- project setup writes `.cross-agent-consensus.yaml`;
- setup detects available `claude` and `codex` commands but does not invoke them;
- setup output never contains unattended invocation, tokens, passwords, API keys, or secret values.

Manual validation:

```bash
skills/cross-agent-consensus/scripts/consensus config show
skills/cross-agent-consensus/scripts/consensus config setup --dry-run
skills/cross-agent-consensus/scripts/consensus init --task "Smoke config" --artifact-locator docs/foo.md
pytest tests/test_cac_tool.py
```

## Backward Compatibility

Existing command lines should continue to work. When no config file exists, parser defaults and document-consensus defaults should behave as they do today.

Config should only fill omitted values. It should not rewrite existing run folders or mutate previously recorded policy. The effective config used to initialize a run must be recorded through the `ConfigResolution` record described above.

The shipped `config/defaults.yaml` values must match the current parser defaults at the time this feature lands. A later package update that changes a default value is a semver-significant behavior change, should be visible in release notes, and should be inspectable through `consensus config show`.

## Open Decisions

- Whether task-file support should be implemented in the same change as base config loading or as a follow-up.
- Whether project config should support both `.cross-agent-consensus.yaml` and `.cross-agent-consensus.yml`; the design recommends starting with `.yaml` only.
- Whether unknown config keys should warn by default or fail; the design recommends `config validate` fails and `config show` warns.
- Whether to ship `consensus config migrate --from <version> --to <version>` for future config schema changes or require manual rewrites between schema versions. For v1, unsupported schema versions should fail clearly.

## Related Claude Sessions

- None yet.
