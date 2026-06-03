# Cross-Agent Consensus Refactor Implementation Plan

Status: proposed
Date: 2026-06-01
Worktree: `worktrees/cross-model-consensus-refactor-simplification-plan`
Branch: `codex/refactor-simplification-plan`

Implementation progress on this branch:

- Completed package bootstrap and installed-entrypoint smoke coverage.
- Extracted shared models, IO helpers, record schema, Markdown record parsing,
  run record lookup, layout helpers, run id allocation, config resolution, run
  initialization, prompt/round selection, capture helpers, validation checks,
  and termination report body generation.
- Added focused tests for config, init, Markdown records, layout, prompts,
  capture, validation, and termination.
- Left provider invocation/process monitoring and installer modularization for
  the deferred TODO note.

## Summary

Implement the refactor as a behavior-preserving extraction of the
`skills/cross-agent-consensus/scripts/cac_tool.py` monolith into a small
importable package inside the installable skill. The first implementation goal
is not to redesign the protocol. It is to make existing behavior easier to
change safely by giving records, layout, config, validation, prompts, capture,
invocation, termination, and installer logic explicit homes.

The current public entrypoint stays:

```text
skills/cross-agent-consensus/scripts/consensus
```

The command surface, run layout, managed install behavior, and current tests
must stay compatible throughout the migration.

## Inputs Reviewed

- Local source analysis:
  `repos/cross-model-consensus/docs/plans_and_designs/refactor-simplification-analysis/refactor-simplification-analysis.md`
- Runtime contract:
  `skills/cross-agent-consensus/SKILL.md`
- Record contract:
  `skills/cross-agent-consensus/references/record-contract.md`
- Protocol:
  `specs/protocol.md`
- CLI implementation:
  `skills/cross-agent-consensus/scripts/cac_tool.py`
- Installer:
  `scripts/install-cac`
- Test baseline:
  `tests/test_cac_tool.py`

Baseline command on 2026-06-01:

```bash
python -m pytest tests/test_cac_tool.py -q
```

Result:

```text
44 passed in 13.47s
```

## Current State

The repository shape is already broadly sensible: `specs/`, `profiles/`,
`skills/`, `implementations/`, `schemas/`, `examples/`, `runners/`, `runs/`,
and `tests/` separate product-level concerns. The implementation problem is
concentrated inside the installable skill.

Important size signals:

```text
skills/cross-agent-consensus/scripts/cac_tool.py  4905 lines
tests/test_cac_tool.py                            1609 lines
scripts/install-cac                                453 lines
specs/protocol.md                                  313 lines
skills/cross-agent-consensus/references/record-contract.md 198 lines
skills/cross-agent-consensus/SKILL.md              229 lines
```

`cac_tool.py` currently owns all of these responsibilities:

- record schema constants and enum definitions;
- Markdown frontmatter parsing and rendering;
- config file discovery, merge, validation, and provenance recording;
- run id allocation and run tree creation;
- run layout detection and path policy;
- protocol record parsing and link validation;
- prompt construction and prompt path selection;
- raw output capture and capture record creation;
- artifact version creation;
- response and re-review skeleton generation;
- invocation readiness checks;
- runtime player adapters for generic CLI, Claude CLI, Codex CLI, and manual;
- process supervision, stream parsing, telemetry, heartbeat, status, watch,
  cancellation, and final output extraction;
- terminal validation and final report generation;
- argparse command registration.

The current tests are valuable, but they mirror the monolith. One
`unittest.TestCase` covers manifest checks, init/config, layout compatibility,
prompt generation, capture, artifact creation, invocation readiness, player
telemetry, process cancellation, and termination.

## Non-Goals

- Do not change the public protocol semantics.
- Do not change the command names or stable flag behavior unless a separate
  compatibility decision is made.
- Do not introduce a full orchestration framework.
- Do not make provider/model selection a protocol responsibility.
- Do not delete historical run evidence as part of this refactor.
- Do not replace all end-to-end CLI tests with unit tests.
- Do not move the skill package out of `skills/cross-agent-consensus/`.

## Target Internal Shape

Create an importable package under the installable skill directory:

```text
skills/cross-agent-consensus/
  scripts/
    consensus
    cac_tool.py                 # compatibility shim for historical callers
  cross_agent_consensus/
    __init__.py
    cli.py
    errors.py
    io.py
    models.py
    record_schema.py
    markdown_records.py
    records.py
    layout.py
    run_store.py
    config.py
    init.py
    prompts.py
    capture.py
    artifacts.py
    skeletons.py
    validation.py
    termination.py
    invocation/
      __init__.py
      adapters.py
      readiness.py
      session_paths.py
      telemetry.py
      process_monitor.py
      status.py
```

Later, move installer implementation into a repo-level package:

```text
scripts/install-cac              # thin shell wrapper
scripts/cac_install.py           # or tools/cac_install.py
```

The exact package location can be adjusted, but it must satisfy these
constraints:

- installed skill copies must run without external dependencies;
- `scripts/consensus --version` must still work from both repo and installed
  copies;
- every new managed skill file must be listed in
  `skills/cross-agent-consensus/managed-manifest.json`;
- manifest hashes must be regenerated after each managed-file change;
- tests must cover execution through the shell entrypoint, not only direct
  imports.

## Dependency Direction

Use this direction:

```text
cli.py
  -> use-case modules
    -> records/config/layout/schema/io
    -> invocation adapters at process boundary
```

Avoid these dependencies:

- record parsing importing CLI or argparse;
- schema constants importing provider-specific invocation code;
- config importing process supervision;
- validation writing run state except through explicit command services;
- player adapters deciding materiality, consensus, or terminal protocol state.

## Implementation Sequence

### Phase 0: Compatibility Harness

Goal: make future extraction failures obvious.

Actions:

1. Add a small compatibility note in `docs/public-contract.md` or
   `docs/plans_and_designs/refactor-simplification-analysis/public-contract.md`.
2. Record stable command categories:
   `--version`, `config`, `init`, `status`, `validate`, `prompt`, `capture`,
   `new-artifact`, `response-skeleton`, `rereview-skeleton`,
   `invocation-ready`, `invoke-agent`, `agent-status`, `agent-watch`,
   `agent-cancel`, `players probe`, and `terminate`.
3. Keep `python -m pytest tests/test_cac_tool.py -q` as the compatibility gate.
4. Add a smoke test script or pytest test that installs the skill into a
   temporary target and runs `scripts/consensus --version`, `config show`,
   `init`, `prompt`, `validate`, and `terminate`.

Exit criteria:

- Existing 44 tests pass.
- Temporary install smoke test passes.
- Public behavior contract is documented.

### Phase 1: Bootstrap Package And Thin CLI

Goal: introduce the new package without moving behavior yet.

Actions:

1. Add `cross_agent_consensus/__init__.py` with package version helpers or
   minimal exports.
2. Add `cross_agent_consensus/cli.py` and move only `build_parser()` and
   `main()` into it, initially importing command functions from
   `scripts/cac_tool.py`.
3. Update `scripts/consensus` to put the skill root on `PYTHONPATH` and call
   `cross_agent_consensus.cli:main`.
4. Keep `scripts/cac_tool.py` executable as a temporary compatibility module.
5. Add new package files to `managed-manifest.json` and regenerate hashes.

Exit criteria:

- `scripts/consensus --version` prints the same semver.
- Existing tests pass through the shell entrypoint.
- `test_managed_manifest_hashes_match_source_files` passes.

### Phase 2: Extract Shared IO And Models

Goal: remove low-level utilities and simple data containers from the monolith.

Move to `models.py`:

- `Record`
- `CheckResult`
- `ConfigResolution`
- `PlayerCapabilities`
- `CommandSpec`
- `AgentInvocation`
- `AgentSessionPaths`

Move to `io.py`:

- `utc_now`
- `skill_root`
- `repo_root_from_skill`
- `read_cac_version`
- `eprint`
- `slugify`
- `safe_relative_path`
- `sha256_file`
- `hash_locator`
- atomic text/json/write helpers
- JSONL append helpers
- compact JSON helpers

Testing:

- Keep existing CLI tests unchanged.
- Add focused tests for atomic no-overwrite behavior and hash helpers only if
  they are not already covered through CLI paths.

Exit criteria:

- No CLI output change.
- Existing tests pass.

### Phase 3: Extract Record Schema And Markdown Records

Goal: isolate executable protocol metadata and frontmatter parsing.

Move to `record_schema.py`:

- `COMMON_FIELDS`
- `REQUIRED_FIELDS`
- `ENUMS`
- `KNOWN_RECORD_TYPES`
- `ID_FIELDS`
- schema/version constants related to records
- helpers such as `required_field_missing`

Move to `markdown_records.py`:

- YAML subset parser and renderer:
  `parse_scalar`, `parse_list`, `parse_mapping`, `parse_yaml_subset`,
  `render_scalar`, `render_yaml`, `frontmatter`
- Markdown record discovery:
  `RECORD_HEADING_RE`, `find_frontmatter_after`,
  `parse_records_from_file`

Move to `records.py`:

- `is_protocol_payload_path`
- `parse_run_records`
- `records_by_type`
- `first_record`
- duplicate id detection helpers when they become explicit

Testing:

- Split or add tests for:
  - scalar/list/map parsing;
  - section frontmatter discovery;
  - payload path exclusion for prompts/raw evidence;
  - enum validation;
  - duplicate ids.

Exit criteria:

- `validate --records --links` behavior remains unchanged.
- Existing tests pass.
- New parser tests pass without invoking the CLI when practical.

### Phase 4: Extract Layout And Run Store

Goal: make filesystem policy explicit and reusable.

Move to `layout.py`:

- `DEFAULT_LAYOUT`
- `ROUND_FIRST_LAYOUT_VERSION`
- `LEDGER_LAYOUT_VERSION`
- `detect_run_layout`
- `normalize_round_id`
- `round_number`
- `round_id_from_number`
- `round_dir`
- `record_path_round_number`
- `record_round_number`
- `required_run_paths`
- `make_run_tree`
- prompt/raw path root helpers that are pure layout policy

Move to `run_store.py`:

- run id allocation:
  `run_id_base_from_task`, `run_id_from_task`
- record read/write convenience over a run root;
- unique path allocation for raw payloads and agent sessions when not
  invocation-specific.

Testing:

- Extract tests for:
  - round aliases `1`, `round-1`, `round-001`;
  - legacy ledger layout validation;
  - wrong-round review record rejection;
  - reviewer prompt location policy.

Exit criteria:

- Round-first and legacy layout tests pass.
- Existing prompt/capture tests pass.

### Phase 5: Extract Config Resolution

Goal: isolate deterministic layered configuration.

Move to `config.py`:

- `CONFIG_SCHEMA_VERSION`
- `TASK_SCHEMA_VERSION`
- deep merge, flatten/get/set nested helpers;
- `canonical_config`
- source tracking;
- config file discovery;
- persistent config safety checks;
- config/task shape validation;
- `resolve_config`
- `init_cli_config`
- `task_file_fields`
- `apply_config_to_init_args`
- `consumed_config_values`
- `config_resolution_record`
- config command helpers where they are not CLI rendering.

Keep in `cli.py` or command controller:

- argparse definitions;
- stdout/stderr formatting for `config show`, `config validate`,
  `config paths`, and `config setup`.

Testing:

- Move config tests into `tests/test_config.py`:
  - installed defaults;
  - user-local override;
  - missing environment config;
  - persistent unattended invocation rejection;
  - secret-like persistent values;
  - invalid round-limit type;
  - task-file precedence.

Exit criteria:

- Config resolution written into `run.md` is byte-for-byte compatible unless a
  test-approved timestamp/path normalization makes exact matching impossible.
- Existing tests pass.

### Phase 6: Extract Init, Prompt, Capture, Artifact, And Skeleton Services

Goal: turn command functions into small controllers.

Move to `init.py`:

- `infer_validators`
- `build_init_files`
- `cmd_init` internals as `RunInitializer`

Move to `prompts.py`:

- `select_artifact`
- `select_review_batch`
- `review_batch_by_id`
- `resolve_active_round`
- `prompt_filename`
- prompt target selection
- `build_prompt`

Move to `capture.py`:

- phase record target selection;
- raw payload target allocation;
- raw payload copying;
- reviewer and validator capture record append logic.

Move to `artifacts.py`:

- artifact version creation;
- predecessor/content hash behavior;
- no-overwrite enforcement.

Move to `skeletons.py`:

- response skeleton generation;
- re-review skeleton generation.

Testing:

- Move tests by behavior area:
  - `tests/test_init.py`
  - `tests/test_prompt.py`
  - `tests/test_capture.py`
  - `tests/test_artifacts.py`
  - `tests/test_skeletons.py` if skeleton behavior gets direct coverage.
- Keep CLI smoke coverage for each command.

Exit criteria:

- `init`, `prompt`, `capture`, `new-artifact`, `response-skeleton`, and
  `rereview-skeleton` outputs and files match existing behavior.
- Existing tests pass.

### Phase 7: Extract Validation And Termination

Goal: isolate protocol validation and terminal report generation.

Move to `validation.py`:

- pre-execution checks;
- record checks;
- participant checks;
- link checks;
- reviewer isolation checks;
- validator status helpers;
- unresolved finding helpers;
- terminal record checks;
- check result formatting helpers where reusable.

Move to `termination.py`:

- `terminal_body`
- terminal condition support logic;
- final report generation;
- write-before-fail protections for invalid waived validators.

Testing:

- Move tests into:
  - `tests/test_validation.py`
  - `tests/test_termination.py`
- Add direct unit tests for terminal predicate inputs where easy.
- Keep at least one end-to-end CLI termination test.

Exit criteria:

- `validate --pre-execution`, `--records`, `--links`, `--participants`,
  `--reviewer-isolation`, and `--terminal` behavior remains compatible.
- `terminate` refuses invalid waiver data before writing.
- Existing tests pass.

## Deferred TODOs

The following topics are intentionally out of scope for the current
`codex/refactor-simplification-plan` slice and are tracked separately in
`../deferred-refactor-todos/design.md`:

- invocation package extraction and process-monitor restructuring;
- `cac_tool.py` compatibility-layer retirement;
- installer modularization;
- documentation and repository hygiene;
- profile/schema/service follow-ups not required for behavior preservation.

## Current Pull Request Scope

This branch should remain a behavior-preserving package and pure-protocol
extraction slice:

- `docs: define CAC refactor contract`
  - public behavior contract;
  - implementation plan;
  - deferred TODO note.
- `refactor: bootstrap CAC Python package`
  - package skeleton;
  - `cli.py` wrapper;
  - manifest updates.
- `refactor: extract CAC records and layout`
  - models, IO, record schema, Markdown parser, layout, run store;
  - focused parser/layout tests.
- `refactor: extract CAC config and init`
  - config resolver module;
  - init service;
  - config/init tests split.
- `refactor: extract CAC prompt and capture services`
  - prompt and capture modules;
  - focused tests.
- `refactor: extract CAC validation and termination`
  - validation module;
  - termination module;
  - terminal tests.

Future PR slices for invocation extraction, installer modularization,
compatibility-layer retirement, and repository hygiene live in
`../deferred-refactor-todos/design.md`.

## Test Layout Target For This Slice

The current slice should split tests for the modules it extracts while retaining
end-to-end CLI coverage:

```text
tests/
  test_markdown_records.py
  test_run_layout.py
  test_config.py
  test_init.py
  test_prompt.py
  test_capture.py
  test_validation.py
  test_termination.py
```

Keep one or two high-value full CLI flows:

- init -> validate pre-execution -> prompt -> capture -> validate records/links;
- init -> validator evidence -> terminate.

Invocation, agent status/cancel, and installer test splits are deferred TODOs.

## Validation Gates

Run after every extraction phase:

```bash
python -m pytest tests/test_cac_tool.py -q
```

After tests are split:

```bash
python -m pytest -q
```

Run these command-level checks before each PR:

```bash
skills/cross-agent-consensus/scripts/consensus --version
skills/cross-agent-consensus/scripts/consensus config show
python -m pytest -q
```

Add install smoke coverage before the first manifest-affecting package split:

```bash
tmp="$(mktemp -d)"
CODEX_HOME="$tmp/codex" scripts/install-cac --target codex
"$tmp/codex/skills/cross-agent-consensus/scripts/consensus" --version
```

## Risk Register

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Manifest drift after adding modules | Installed skill is incomplete or tests fail | Regenerate hashes in the same commit as every managed file change |
| Import bootstrapping differs between repo and installed copy | CLI works in repo but fails after install | Add temporary install smoke tests early |
| Extracted parser changes Markdown rendering subtly | Existing run validation or records change | Keep golden CLI tests and add parser-focused tests |
| Layout helpers accidentally drop ledger compatibility | Historical runs fail validation | Preserve explicit legacy layout tests |
| Config precedence changes during extraction | Run initialization becomes nondeterministic | Split config tests before deeper rewrites |
| Test split hides integration bugs | Unit tests pass while CLI breaks | Keep CLI smoke tests through `scripts/consensus` |

Invocation and installer risks are tracked in
`../deferred-refactor-todos/design.md`.

## Detailed Acceptance Criteria

This slice is complete when:

- `scripts/consensus` remains the supported executable entrypoint.
- `scripts/consensus --version` returns the same semver format.
- `managed-manifest.json` includes all managed package files with correct hashes.
- The installed skill copy works without external Python package dependencies.
- The current 44 baseline behaviors remain covered in split tests or CLI smoke
  tests.
- `cac_tool.py` remains compatibility-only; command behavior is owned by
  `cross_agent_consensus.cli`.
- `config`, `init`, `prompt`, `capture`, `validate`, and `terminate` have
  module-owned pure protocol/helper services.
- Round-first layout remains canonical.
- Legacy ledger layout remains readable/validatable until explicitly sunset.
- Supervised named CLI reviewer invocation behavior is preserved, but its
  extraction is deferred.
- Direct capture is still represented as imported/manual evidence, not live
  invocation telemetry.
- Out-of-scope refactor topics are captured as TODOs in
  `../deferred-refactor-todos/design.md`.
