# Cross-Agent Consensus Refactor And Simplification Analysis

## Summary

The repository has a coherent product direction: a runtime-neutral protocol for auditable cross-agent consensus, plus a portable skill package and deterministic helper CLI. The main simplification opportunity is not to remove capability. It is to make the capability easier to reason about by separating stable protocol concepts from implementation helpers, run evidence, host-specific integration, and evolving design notes.

The most important refactor is to split `skills/cross-agent-consensus/scripts/cac_tool.py`. It currently combines record schema constants, YAML parsing/rendering, config resolution, run layout creation, validation, prompt generation, capture, external invocation supervision, process telemetry, status, cancellation, and termination into one 4,900+ line script. That violates high cohesion and Single Responsibility, and it makes otherwise good protocol boundaries harder to preserve.

The target architecture should keep the public CAC behavior stable while introducing a small internal package with explicit modules for records, layout, config, prompts, capture, validation, invocation, and CLI command adapters. The CLI can remain dependency-light and file-based, but each module should own one reason to change.

## Scope

Inspected areas:

- Repository structure and top-level documentation.
- `specs/`, `profiles/`, `schemas/`, `examples/`, `implementations/`, `runners/`, `skills/`, `scripts/`, `tests/`, and existing `runs/`.
- The installed skill contract in `skills/cross-agent-consensus/SKILL.md`.
- The helper CLI in `skills/cross-agent-consensus/scripts/cac_tool.py`.
- Current design notes under `docs/plans_and_designs/`.

Out of scope:

- Implementing the refactor.
- Changing public protocol behavior.
- Removing audit evidence from existing historical runs.
- Benchmarking other multi-agent systems.

Revalidation note from 2026-06-01:

- The analysis remains directionally current against the local checkout.
- The artifact itself and several adjacent design notes are local untracked files; `git ls-files docs/plans_and_designs` currently lists only `docs/plans_and_designs/cac-agent-player-interface.md`.
- `python -m pytest tests/test_cac_tool.py -q` passes with 43 tests in this checkout.

## Current Repository Shape

The repository currently separates broad concerns at the directory level:

```text
README.md
docs/
examples/
implementations/
profiles/
runners/
runs/
schemas/
scripts/
skills/
specs/
tests/
```

This is a reasonable starting shape. The problem is that the implementation concentration inside the skill package has outgrown the directory separation.

Relevant size signals from the 2026-06-01 checkout:

```text
specs/protocol.md                                  313 lines
profiles/document-consensus/profile.md            106 lines
skills/cross-agent-consensus/SKILL.md             229 lines
skills/cross-agent-consensus/scripts/cac_tool.py 4905 lines
tests/test_cac_tool.py                           1595 lines
scripts/install-cac                              ~450 lines
docs/plans_and_designs/*.md                      2200+ lines
```

The test suite is valuable and broad, but it mirrors the monolith: one large CLI test file exercises many unrelated behaviors.

## What Is Working

- The product boundary is clear: CAC is a protocol and manual-first skill package, not an automatic cross-runtime runner.
- The runtime-neutral protocol is documented in `specs/protocol.md`.
- The document-consensus profile has useful defaults and explicit validators.
- The run folder contract is auditable and durable.
- The helper CLI already covers important deterministic mechanics: `init`, `prompt`, `capture`, `validate`, config inspection, invocation readiness, monitored invocation, status, cancellation, and termination.
- Tests cover many critical safety rules, including participant distinctness, reviewer prompt isolation, config precedence, unsafe argv detection, run layout validation, and terminal validation.

These should be preserved.

## Main Problems

### 1. One Script Has Too Many Responsibilities

`cac_tool.py` contains unrelated axes of change:

- Protocol record field definitions and enum lists.
- Minimal YAML parser and renderer.
- Config discovery, validation, merge, and provenance.
- Run id and run layout creation.
- Markdown record parsing and cross-reference validation.
- Prompt construction.
- Raw output capture.
- Artifact version creation.
- Terminal report generation.
- Player adapters for generic CLI, Claude CLI, Codex CLI, and manual handoff.
- Process supervision, heartbeat, event tail, stale detection, cancellation, and final output extraction.
- CLI parser construction.

This makes each change riskier than it needs to be. For example, changing process state semantics can accidentally interact with command parsing or record validation because everything lives in one namespace.

Architecture principle impact:

- SOLID SRP: violated by a single module with many reasons to change.
- GRASP High Cohesion: weak, because process telemetry and protocol schema are not cohesive responsibilities.
- GRASP Low Coupling: weak internally, because command functions reach directly into parsing, path layout, records, and process helpers.

### 2. Protocol, Profile, Record Contract, And CLI Schema Are Duplicated

The required fields and enums appear in several places:

- `specs/protocol.md`
- `skills/cross-agent-consensus/references/record-contract.md`
- `profiles/document-consensus/profile.md`
- `cac_tool.py` constants such as `REQUIRED_FIELDS`, `ENUMS`, and `DOCUMENT_VALIDATORS`
- tests that encode expected strings and paths

Some duplication is acceptable because docs and executable validation serve different audiences. The risk is drift. The executable validator should have one machine-readable source of truth, while docs render or explain that source.

### 3. Historical Run Evidence Lives Beside Product Source

The top-level `runs/` directory contains many dogfood and validation runs. This is useful evidence, but it makes the source tree noisy and causes basic repository inspection to surface old protocol layouts, generated evidence, prompt files, and one-off scripts.

The repo should distinguish:

- committed minimal examples and fixtures;
- historical local run evidence;
- current design docs;
- implementation source.

This matters because new contributors will otherwise treat historical run artifacts as part of the maintained product surface.

### 4. Naming Still Leaks The Old Product Concept

The repository path and `specs/protocol.md` title still say "Cross-Model Consensus", while the README says current public artifacts should use "Cross-Agent Consensus". This is manageable during migration, but it should be treated as a deliberate compatibility issue.

Recommendation: keep the repository path if renaming is operationally expensive, but normalize public docs, package names, module names, and generated records around `cross-agent-consensus`.

### 5. Design Docs Are Valuable But Not Curated

`docs/plans_and_designs/` contains several large design documents for scriptability, config, reviewer conclusion validation, player invocation telemetry, and follow-up findings. They represent product history and intent, but there is no index that states:

- which designs are accepted;
- which designs are implemented;
- which are superseded;
- which are backlog only;
- which behavior is normative.

This increases cognitive load and makes it easy to re-open already-settled questions.

As of the 2026-06-01 revalidation, most of those design notes are still local untracked files in this checkout. That makes the need for an index or explicit status metadata more important, not less, because the current filesystem contains useful context that is not all part of the committed product surface.

### 6. Tests Are Broad But Not Layered

`tests/test_cac_tool.py` is valuable but too broad for long-term maintenance. It mixes unit-level tests, record parsing tests, config resolution tests, CLI tests, process supervision tests, and terminal validation tests.

When the implementation is split, tests should be split along the same boundaries. This will make refactors safer because failures will point to the layer that changed.

### 7. Installer Logic Is More Complex Than Its Shell Wrapper Suggests

`scripts/install-cac` is a Bash script with a large embedded Python installer. The embedded Python owns manifest validation, conflict detection, hash checking, copying, and state writing.

That logic is real product code. It should eventually move to a Python module with a small shell wrapper, so it can be tested and reused without parsing heredoc script content.

## Target Architecture

Keep the external CLI entrypoint:

```text
skills/cross-agent-consensus/scripts/consensus
```

Move implementation into importable Python modules:

```text
skills/cross-agent-consensus/
  scripts/
    consensus
  py/
    cross_agent_consensus/
      __init__.py
      cli.py
      models.py
      records.py
      record_schema.py
      markdown_records.py
      config.py
      layout.py
      run_store.py
      prompts.py
      capture.py
      validation.py
      termination.py
      invocation/
        __init__.py
        adapters.py
        process_monitor.py
        status.py
      install/
        managed_manifest.py
```

The exact folder name can differ. A sibling package such as `skills/cross-agent-consensus/cross_agent_consensus/` is simpler to bootstrap from `scripts/consensus`; a conventional `src/` layout is more familiar to Python tooling but requires an explicit import path setup inside the installed skill. The boundary is more important than the exact folder:

- `cli.py`: argparse only, thin dispatch, no protocol decisions.
- `models.py`: dataclasses and typed value objects.
- `record_schema.py`: required fields, enums, validator ids, schema version constants.
- `markdown_records.py`: frontmatter parsing and rendering.
- `config.py`: config discovery, merge, validation, provenance.
- `layout.py`: run layout detection and path policy.
- `run_store.py`: read/write run records and allocate paths.
- `prompts.py`: prompt construction from records.
- `capture.py`: raw output capture and deterministic capture records.
- `validation.py`: pre-execution, record, link, participant, reviewer isolation, and terminal checks.
- `termination.py`: TerminationRecord and FinalReport generation.
- `invocation/`: player adapters and process/session telemetry.
- `install/`: managed manifest install and update behavior.

Any added module inside the installable skill package must be listed in `skills/cross-agent-consensus/managed-manifest.json`, and `scripts/consensus` must bootstrap imports from the chosen package location after install.

This structure preserves the current file-based architecture while reducing coupling.

## Recommended Domain Objects

### `RecordRepository`

Responsibility:

- Parse protocol records from a run folder.
- Ignore payload paths that must not be parsed as records.
- Index records by type and id.
- Provide link lookup helpers.

Principles:

- GRASP Information Expert: it owns record lookup because it has the record index.
- SRP: it does not validate business rules beyond record parsing integrity.

### `RunLayout`

Responsibility:

- Detect `round-first` versus legacy ledger layout.
- Return canonical paths for prompts, raw outputs, reviews, validation, backlog, and artifacts.
- Normalize round ids.

Principles:

- GRASP Pure Fabrication: a path service reduces coupling between commands and filesystem details.
- DIP: command handlers depend on a layout abstraction, not path string construction.

### `ConfigResolver`

Responsibility:

- Discover installed defaults, user-local config, project config, task-file config, and CLI overrides.
- Merge layers.
- Validate persistent config safety rules.
- Produce `ConfigResolution`.

Principles:

- SRP: config logic is not mixed with run creation.
- OCP: new config layers or fields can be added without changing unrelated commands.

### `RunInitializer`

Responsibility:

- Create a valid initial run folder from explicit input and resolved config.
- Create initial TaskBrief, Policy, Participants, ReviewScope, ReviewBatch, ArtifactVersion, validation summary, backlog, and escalation files.

Principles:

- GRASP Creator: it creates run records because it aggregates their initial data.
- Low Coupling: it receives config and schema services rather than discovering everything itself.

### `ValidationService`

Responsibility:

- Execute named validation groups.
- Return structured validation results.
- Keep terminal validation deterministic.

Principles:

- ISP: callers can request `pre_execution`, `records`, `links`, `participants`, `reviewer_isolation`, or `terminal` checks independently.
- SRP: validation does not write records.

### `PromptBuilder`

Responsibility:

- Build role-specific prompts from a selected TaskBrief, Policy, ReviewScope, ReviewBatch, and ArtifactVersion.
- Enforce prompt visibility rules such as first-round reviewer isolation.

Principles:

- OCP: new phases can be added as prompt builders without changing record parsing or process invocation.

### `ArtifactService`

Responsibility:

- Create new ArtifactVersion records.
- Compute content hashes when locators are local and readable.
- Enforce predecessor links and no-overwrite behavior.

Principles:

- GRASP Creator: it creates ArtifactVersion records because it owns version allocation inputs.
- SRP: it manages artifact metadata, not review findings or prompt text.

### `CaptureService`

Responsibility:

- Copy raw outputs into canonical raw paths.
- Allocate unique file names.
- Create deterministic wrapper records where the protocol supports it.

Principles:

- Information Expert: it owns raw payload provenance because it owns raw payload placement.
- SRP: it does not normalize findings.

### `InvocationService`

Responsibility:

- Prepare and supervise one explicitly authorized agent process or manual handoff.
- Write invocation/session telemetry.
- Delegate provider-specific stream parsing to `PlayerAdapter`.

Principles:

- DIP: the invocation service depends on `PlayerAdapter`, not direct Claude/Codex logic.
- LSP: each adapter must support the same expected operations or clearly report unsupported capability.
- ISP: manual handoff should not need process-control methods.

## Proposed Internal Dependency Direction

Dependencies should mostly point inward to protocol data and outward to adapters:

```text
CLI commands
  -> services
    -> records/config/layout/schema
    -> invocation adapters
      -> external commands
```

Avoid these dependencies:

- record parsing importing CLI command code;
- config resolution importing process invocation;
- protocol schema importing provider-specific adapter code;
- validation writing run state except through explicit terminal or capture commands;
- player adapters deciding protocol materiality or consensus state.

## Prioritized Refactor Plan

### Phase 0: Freeze Public Behavior

Goal: create confidence before extraction.

Actions:

- Add a short "public behavior contract" section to the README or a dedicated `docs/public-contract.md`.
- Record current CLI commands, stable flags, run layout paths, config precedence, and terminal output expectations.
- Run the existing test suite and treat it as the initial compatibility gate.

### Phase 1: Curate Repository Documentation

Goal: reduce cognitive load without touching runtime behavior.

Actions:

- Add `docs/plans_and_designs/README.md` with status for each design: proposed, accepted, implemented, superseded, or backlog.
- Add `runs/README.md` explaining whether top-level runs are committed evidence, examples, or local artifacts.
- Move stable minimal fixtures to `examples/`.
- Keep historical runs if they are intentionally committed, but make them clearly non-normative.
- Normalize public text from "Cross-Model" to "Cross-Agent" where compatibility allows.

### Phase 2: Extract Record Schema And Markdown Parsing

Goal: isolate the protocol record substrate.

Actions:

- Move `COMMON_FIELDS`, `REQUIRED_FIELDS`, `ENUMS`, record id fields, frontmatter parsing, and YAML rendering to `record_schema.py` and `markdown_records.py`.
- Add focused tests for:
  - scalar/list/map parsing;
  - record section discovery;
  - payload path exclusion;
  - enum validation;
  - duplicate record ids.

Keep the CLI output unchanged.

### Phase 3: Extract Layout And Run Store

Goal: make path policy explicit.

Actions:

- Move layout detection, round id normalization, required paths, prompt paths, raw paths, and artifact paths to `layout.py`.
- Introduce `RunStore` for read/write operations.
- Centralize allocation rules for unique raw payload and agent session paths.
- Add tests for round aliases, legacy layout compatibility, wrong-round review files, and reviewer prompt locations.

### Phase 4: Extract Config Resolution

Goal: remove config coupling from `cmd_init`.

Actions:

- Move config parsing, layer discovery, shape validation, merge rules, and consumed-value provenance into `config.py`.
- Keep persistent config safety rules there.
- Keep task-file shape validation near config, not in CLI parser code.
- Add tests for each config source and precedence rule.

### Phase 5: Extract Commands Into Thin Controllers

Goal: align with GRASP Controller.

Actions:

- Keep argparse handlers small.
- Each command should:
  1. parse args;
  2. call one service;
  3. render result or error.
- Move command logic into service methods:
  - `RunInitializer.init`
  - `PromptBuilder.write_prompt`
  - `CaptureService.capture`
  - `ArtifactService.new_artifact`
  - `ValidationService.run_checks`
  - `TerminationService.terminate`

### Phase 6: Extract Invocation Layer

Goal: isolate process telemetry and player adapters.

Actions:

- Move `PlayerCapabilities`, `CommandSpec`, `AgentInvocation`, `AgentSessionPaths`, `GenericCliPlayer`, `StructuredJsonCliPlayer`, `ClaudeCliPlayer`, `CodexCliPlayer`, and `ManualPlayer` under `invocation/`.
- Move process monitoring, heartbeat, stale detection, cancellation, status, and event tail there.
- Define a small adapter protocol:

```python
class PlayerAdapter(Protocol):
    player_id: str
    def probe(self, command: list[str]) -> PlayerCapabilities: ...
    def build_command(self, invocation: AgentInvocation) -> CommandSpec: ...
    def parse_stream_events(self, stream_name: str, data: bytes, buffers: dict[str, str], invocation: AgentInvocation) -> list[AgentEvent]: ...
    def extract_final_output(self, paths: AgentSessionPaths) -> Path: ...
```

- `ManualPlayer` should satisfy only the non-process parts of this interface and report unsupported stream parsing/process capabilities explicitly.
- Keep `invocation-ready` policy checks outside provider adapters, because authorization is protocol policy rather than provider behavior.

### Phase 7: Extract Installer Logic

Goal: make installer behavior testable.

Actions:

- Move the embedded Python in `scripts/install-cac` into a module such as `install/managed_manifest.py`.
- Keep `scripts/install-cac` as a small shell wrapper.
- Regenerate or validate `managed-manifest.json` whenever the module split adds, removes, or moves managed skill files.
- Add tests for manifest hash mismatch, target conflict, managed update, update available, and local modification preservation.

## Simplification Opportunities By Area

### Specs

Keep `specs/protocol.md` normative, but avoid using it as the only source for machine rules.

Recommendations:

- Add generated or manually synchronized non-JSON record metadata under `skills/cross-agent-consensus/config/record-schema.yaml`, or make a separate M2 boundary decision before introducing new JSON schema artifacts.
- Keep `specs/conformance.md` short and link to executable validation groups.
- Treat `specs/lifecycle.md` as a summary, not a competing lifecycle definition.

### Profiles

The `document-consensus` profile is good. It should become a data-backed profile plus explanatory docs.

Recommendations:

- Move required validators and default round limits into a profile data file.
- Have `infer_validators()` read profile metadata rather than hard-code document validators.
- Add profile tests that prove profile defaults produce the same Policy frontmatter as today.

### Schemas

The current `schemas/finding.schema.json` and `schemas/author-response.schema.json` look like earlier portable shapes. Decide whether they are current public API or historical support.

Recommendations:

- Add a `schemas/README.md` with status and ownership.
- If they remain active, map them to protocol record types.
- If they are historical, move them under `schemas/legacy/` or mark them as legacy in place.

### Runs

Top-level `runs/` should not be the default place for every future dogfood artifact unless the repo intentionally versions run evidence.

Recommendations:

- Add `.gitignore` guidance for local run output if runs should be local.
- Keep only curated example runs in `examples/` or `runs/fixtures/`.
- Add a README explaining historical run folders and layout differences.
- Do not delete current run evidence without a separate decision; it may be valuable audit history.

### Skills

The skill package is the deliverable. Keep it self-contained, but split internals.

Recommendations:

- Keep `SKILL.md`, `references/`, `templates/`, `config/`, and `scripts/consensus`.
- Move Python implementation into an importable package under the skill directory.
- Keep generated local config out of the managed manifest.
- Add package-level tests that run from an installed skill copy, not only the repo checkout.

### Implementations

The runtime notes are thin and useful. They should not grow into protocol definitions.

Recommendations:

- Keep implementation docs as role-mapping and install-discovery notes.
- Move any runtime-specific command parsing into `invocation/adapters.py`.
- Avoid duplicating lifecycle semantics in implementation docs.

### Runner

`runners/file-based-mvp/README.md` says not to automate model calls until the protocol is dogfooded manually. The helper CLI now includes monitored invocation. This creates a documentation mismatch.

Recommendations:

- Update runner docs to distinguish deterministic helper CLI, direct capture, monitored invocation, and future automatic orchestration.
- State that `invoke-agent` is supervised explicit invocation, not an automatic cross-runtime runner.

### Tests

Split tests to match modules:

```text
tests/
  test_cli_smoke.py
  test_config.py
  test_markdown_records.py
  test_run_layout.py
  test_init.py
  test_prompt.py
  test_capture.py
  test_validation.py
  test_invocation_ready.py
  test_invocation_process.py
  test_termination.py
  test_install_cac.py
```

Keep some end-to-end CLI tests. Do not replace them entirely with unit tests.

## SOLID Mapping

### Single Responsibility Principle

Current issue: `cac_tool.py` has many reasons to change.

Refactor target:

- `config.py` changes for config rules.
- `records.py` changes for record parsing and schema.
- `layout.py` changes for folder layout.
- `invocation/` changes for process supervision and player adapters.
- `termination.py` changes for final report generation.

### Open/Closed Principle

Current issue: adding a new profile, record type, or player adapter likely requires editing broad central logic.

Refactor target:

- Add profiles through profile metadata.
- Add players through `PlayerAdapter` registration.
- Add validators as named validation functions in a registry.
- Add prompt phases through a prompt builder registry.

### Liskov Substitution Principle

Current issue: `ManualPlayer` and CLI players have different operational semantics but share nearby code paths.

Refactor target:

- Define a minimal adapter interface with explicit unsupported capability reporting.
- Ensure status/cancel code handles manual sessions without assuming a PID.

### Interface Segregation Principle

Current issue: helpers and commands operate on large `argparse.Namespace` objects, and some functions need only a few fields.

Refactor target:

- Use small request dataclasses such as `InitRequest`, `PromptRequest`, `CaptureRequest`, `InvocationRequest`, and `TerminateRequest`.
- Avoid fake `argparse.Namespace` objects for internal reuse.

### Dependency Inversion Principle

Current issue: command handlers depend directly on filesystem paths, record parsing details, and concrete process behavior.

Refactor target:

- Commands depend on services.
- Services depend on abstractions such as `RunStore`, `RunLayout`, and `PlayerAdapter`.
- Provider-specific code stays at the edge.

## GRASP Mapping

### Information Expert

Assign responsibilities to the object with the necessary information:

- `RecordRepository` answers record lookup and link questions.
- `RunLayout` answers path and round questions.
- `ConfigResolver` answers provenance and effective config questions.
- `InvocationSession` answers session file paths and state.

### Creator

Use creators that aggregate initialization data:

- `RunInitializer` creates initial run files.
- `CaptureService` creates raw payload records.
- `TerminationService` creates TerminationRecord and FinalReport.

### Controller

CLI command handlers should be controllers only. They should coordinate one use case and delegate the work.

### Low Coupling

Provider adapters should not know protocol materiality rules. Validation should not know process stdout parsing. Config should not know terminal consensus rules.

### High Cohesion

Each module should group functions that change together:

- config discovery and validation;
- Markdown record parsing;
- run path policy;
- process telemetry;
- terminal report generation.

### Protected Variations

Use interfaces around unstable areas:

- Run layout: legacy ledger versus round-first.
- Runtime players: generic CLI, Codex CLI, Claude CLI, manual.
- Profile defaults: document-consensus now, other artifact profiles later.
- Config layers: user-local, project, task-file, CLI.

## Concrete Backlog

### High Priority

- Split `cac_tool.py` into record schema, Markdown record parser, layout, config, validation, prompt, capture, termination, and invocation modules.
- Add `docs/plans_and_designs/README.md` with design statuses.
- Add `runs/README.md` clarifying historical evidence versus maintained fixtures.
- Move installer embedded Python into a testable Python module.
- Replace broad internal `argparse.Namespace` plumbing with request dataclasses.
- Resolve the known validation evidence ID collision risk from `cac-simplify-follow-up-findings.md`; prefer a deterministic collision suffix or monotonic allocator so repeated captures cannot produce ambiguous `ValidationEvidence` ids.

### Medium Priority

- Convert document-consensus defaults into profile metadata.
- Add non-JSON protocol record metadata or make the existing JSON schemas clearly historical.
- Split the test file by behavior area.
- Normalize public naming to `cross-agent-consensus`.
- Add a compatibility sunset note for legacy ledger layout.
- Separate stable agent session state from heartbeat churn if status/cancel freshness permits it.

### Low Priority

- Add generated docs from schema metadata.
- Add a developer guide for adding a new player adapter.
- Add a developer guide for adding a new profile.
- Add optional type checking after the module split.

## What Not To Do

- Do not introduce a heavyweight orchestration framework just to split this code.
- Do not make automatic model/provider selection part of the core protocol.
- Do not let player adapters classify materiality, apply reviewer suggestions, or declare consensus.
- Do not delete historical run evidence as part of the refactor unless there is a separate repository hygiene decision.
- Do not add broad abstractions before extracting the obvious module boundaries.

## Migration Strategy

The safest migration is mechanical and behavior-preserving:

1. Add modules while keeping `scripts/consensus` as the entrypoint.
2. Update `scripts/consensus` import bootstrapping and `managed-manifest.json` as soon as the first importable module is added.
3. Move pure helpers first: schema constants, YAML subset parsing, frontmatter rendering, record parsing.
4. Move path/layout helpers second.
5. Move config resolver third.
6. Move validation checks one group at a time.
7. Move prompt and capture commands.
8. Move invocation and status/cancel last, because process supervision has the most external behavior.
9. Keep old function names as wrappers during extraction if that reduces test churn.
10. Split tests only after the relevant module exists.

Each step should run the existing CLI tests. The goal is no visible behavior change until the internal structure is clean.

## Validation Plan

For the refactor implementation, use these gates:

- `python -m pytest tests/test_cac_tool.py -q` before and after each extraction step.
- Focused new unit tests for each extracted module.
- A smoke test that installs the skill into a temporary target and runs `scripts/consensus --version`, `config show`, `init`, `prompt`, `validate`, and `terminate`.
- A fixture run that validates both `round-first` and legacy ledger layouts during the compatibility period.
- An invocation test using a local deterministic command, not a network model call.

## Expected End State

After refactoring, the repository should feel like this:

- `specs/` explains the protocol.
- `profiles/` defines artifact-specific policy.
- `skills/cross-agent-consensus/` is the installable package.
- The helper CLI is still easy to run, but its internals are modular.
- Historical runs are clearly separated from maintained examples.
- Tests point directly at the layer that failed.
- New runtime adapters, profiles, and validators can be added without editing unrelated protocol and filesystem code.

That keeps the current CAC strengths while making the project simpler to maintain and safer to extend.
