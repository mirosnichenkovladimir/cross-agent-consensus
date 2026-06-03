# Cross-Agent Consensus Deferred Refactor Design

Status: backlog
Date: 2026-06-01
Related plan: `../refactor-simplification-analysis/implementation-plan.md`

This design extracts the deferred refactor backlog from the current
`codex/refactor-simplification-plan` slice. The current slice should stay
limited to behavior-preserving extraction of pure protocol and helper areas:
package bootstrap, records, Markdown parsing, layout, run store, config, init,
prompts, capture, validation, termination, focused tests, and installed-skill
smoke coverage.

Do not bundle these workstreams into the current MR unless there is a separate
scope decision.

## Summary

The deferred refactor work has five independent workstreams:

- invocation and process-monitor extraction;
- installer modularization;
- retiring the `cac_tool.py` compatibility monolith after behavior is
  module-owned;
- documentation and repository hygiene;
- profile, schema, and service follow-ups.

Each workstream should become its own PR slice so behavior preservation remains
auditable and failures point to one layer.

## Current State

The current refactor slice is extracting pure protocol and file-helper logic
from the CAC monolith. It intentionally leaves the highest-risk process
boundary and install path unchanged until the lower-level package structure is
stable.

The future work still needs to preserve these public constraints:

- `skills/cross-agent-consensus/scripts/consensus` remains the supported
  executable;
- installed skill copies run without external dependencies;
- command behavior and run layout stay compatible unless a separate
  compatibility decision changes them;
- supervised invocation remains telemetry-first and audit-safe;
- historical run evidence is not deleted as part of mechanical refactors.

## Workstream: Invocation Package Extraction

Goal: isolate the process boundary after pure protocol logic has already moved.

Target package:

```text
cross_agent_consensus/invocation/
  __init__.py
  adapters.py
  readiness.py
  session_paths.py
  telemetry.py
  process_monitor.py
  status.py
```

Implementation tasks:

- [ ] Create `cross_agent_consensus/invocation/adapters.py`.
- [ ] Move player adapters:
  - `GenericCliPlayer`;
  - `StructuredJsonCliPlayer`;
  - `ClaudeCliPlayer`;
  - `CodexCliPlayer`;
  - `ManualPlayer`;
  - adapter lookup and registration.
- [ ] Create `cross_agent_consensus/invocation/readiness.py`.
- [ ] Move invocation readiness policy checks:
  - unattended policy checks;
  - allowed prompt and raw roots;
  - command separator normalization;
  - display command rendering;
  - unsafe argv and secret flag checks;
  - reviewer prompt completeness checks;
  - invocation readiness errors.
- [ ] Create `cross_agent_consensus/invocation/session_paths.py`.
- [ ] Move session path helpers:
  - actor path sanitization;
  - session path allocation;
  - latest session lookup;
  - relative path formatting.
- [ ] Create `cross_agent_consensus/invocation/telemetry.py`.
- [ ] Move telemetry helpers:
  - invocation, command, rejected command, state, exit, event, and log writing;
  - stream log append and flush helpers;
  - event tail helpers.
- [ ] Create `cross_agent_consensus/invocation/process_monitor.py`.
- [ ] Move process-monitor behavior:
  - process identity checks;
  - process existence;
  - live and stale classification;
  - generic agent subprocess runner;
  - cancellation implementation.
- [ ] Create `cross_agent_consensus/invocation/status.py`.
- [ ] Move agent status and watch command internals.
- [ ] Keep `invocation-ready` policy outside provider adapters;
  authorization is protocol policy, not provider behavior.

Behavior to preserve:

- named CLI reviewer paths still require `invoke-agent`;
- generic CLI telemetry file shape remains compatible;
- secret argv rejection still records a failed session;
- stale detection and cancel behavior stay compatible;
- player adapters do not classify materiality, apply reviewer suggestions, or
  declare consensus.

Validation:

- [ ] Add `tests/test_invocation_ready.py`.
- [ ] Add `tests/test_invocation_players.py`.
- [ ] Add `tests/test_invocation_process.py`.
- [ ] Add `tests/test_agent_status_cancel.py`.
- [ ] Use deterministic local commands only for process tests.
- [ ] Keep Claude/Codex stream parser tests as pure parser tests.

## Workstream: Retire The Compatibility Layer

Goal: complete the migration after all behavior has module ownership.

Implementation tasks:

- [ ] Reduce `scripts/cac_tool.py` to a tiny compatibility wrapper importing
  `cross_agent_consensus.cli.main`, or remove it after a documented
  compatibility decision.
- [ ] Keep `scripts/consensus` as the stable executable.
- [ ] Update `managed-manifest.json`.
- [ ] Update references that mention `cac_tool.py` as the implementation file.
- [ ] Verify the installed skill copy still works without external
  dependencies.

## Workstream: Modularize Installer Logic

Goal: make managed install behavior testable without embedded heredoc parsing.

Design:

- Move embedded Python from `scripts/install-cac` into a Python module, for
  example `scripts/cac_install.py` or a package-owned install module.
- Keep shell handling for argument parsing and target home detection unless
  moving all installer logic to Python is explicitly approved.
- Preserve first-class target behavior for Hermes and Codex and best-effort
  behavior for Claude.

Validation:

- [ ] Add installer tests for manifest source hash mismatch.
- [ ] Add installer tests for target path conflict.
- [ ] Add installer tests for managed update.
- [ ] Add installer tests for update available without `--update`.
- [ ] Add installer tests for local modification preservation.
- [ ] Add installer tests for target state package mismatch.

## Workstream: Documentation And Repository Hygiene

Goal: clarify what is normative, historical, implemented, or backlog-only.

Implementation tasks:

- [ ] Add `docs/plans_and_designs/README.md` with design status metadata:
  proposed, accepted, implemented, superseded, backlog.
- [ ] Add `runs/README.md` explaining committed evidence, examples, fixtures,
  and local run guidance.
- [ ] Add `schemas/README.md` declaring whether existing JSON schemas are
  current public API or legacy artifacts.
- [ ] Update `runners/file-based-mvp/README.md` to distinguish deterministic
  helper CLI, direct capture, supervised explicit invocation, and future
  orchestration.
- [ ] Normalize public wording from Cross-Model Consensus to Cross-Agent
  Consensus where not constrained by repository path compatibility.
- [ ] Do not delete historical run evidence without a separate repository
  hygiene decision.

## Workstream: Profile, Schema, And Service Follow-Ups

Goal: clean up extension points after the extraction boundaries are stable.

Implementation tasks:

- [ ] Move document-consensus required validators and round defaults into
  profile metadata.
- [ ] Have `infer_validators()` read profile metadata rather than hard-code
  document validators.
- [ ] Decide whether existing JSON schemas are active public API or historical
  support; mark or move them accordingly.
- [ ] Replace broad internal `argparse.Namespace` plumbing with request
  dataclasses only when it reduces real command/service coupling.
- [ ] Resolve the validation evidence id collision risk noted in earlier design
  work with a deterministic collision suffix or monotonic allocator.
- [ ] Add developer guides for adding a new player adapter and adding a new
  profile after those extension points are stable.

## Future PR Slices

- [ ] `refactor: extract CAC invocation layer`
  - adapters, readiness, telemetry, process monitor, status, and cancel;
  - invocation-focused tests.
- [ ] `refactor: modularize CAC installer`
  - installer module;
  - installer tests.
- [ ] `chore: retire CAC monolith compatibility layer`
  - reduce or remove `cac_tool.py`;
  - update docs and manifest.
- [ ] `docs: clarify CAC design and evidence status`
  - design index;
  - runs and schema documentation;
  - runner wording cleanup.
- [ ] `refactor: move CAC profile defaults to metadata`
  - executable profile metadata;
  - unchanged document-consensus validator inference.
- [ ] `fix: make CAC validation evidence ids collision-safe`
  - deterministic suffix or monotonic allocation;
  - collision-focused tests.
- [ ] `docs: declare CAC schema lifecycle`
  - active API, compatibility artifact, or historical status.
- [ ] `refactor: narrow CAC command request objects`
  - dataclasses only where they reduce command/service coupling.
- [ ] `docs: add CAC extension guides`
  - player adapter guide;
  - profile guide;
  - managed package update guide.

## Implementation Plan

Use separate branches for each workstream. Each branch should start from a
clean baseline, keep public CLI behavior compatible, and include focused tests
before deleting the source behavior from `scripts/cac_tool.py`.

Slice ordering:

- Slice 1 must land before Slice 3.
- Slice 2 must land before Slice 3 unless `scripts/cac_tool.py` retirement is
  explicitly scoped to CLI code only.
- Slice 4 can run independently if it does not touch managed skill files.
- Slices 5a-5e should run after Slice 1, because extension guides and profile
  cleanup should describe the module-owned shape.
- Slice 5d is optional cleanup and should not block Slices 5a, 5b, 5c, or 5e.

### Shared Preparation

1. Reconfirm the current branch baseline:
   - `skills/cross-agent-consensus/scripts/consensus --version`;
   - `skills/cross-agent-consensus/scripts/consensus config show`;
   - `python -m pytest -q`.
2. Record a comparable command-surface baseline before each slice. Store it
   under
   `docs/plans_and_designs/deferred-refactor-todos/baselines/<slice>/cli-help.txt`
   and diff it after the slice. Any changed help text, command name, default,
   or exit behavior requires an explicit compatibility note.

   ```bash
   {
     skills/cross-agent-consensus/scripts/consensus --help
     skills/cross-agent-consensus/scripts/consensus config --help
     skills/cross-agent-consensus/scripts/consensus init --help
     skills/cross-agent-consensus/scripts/consensus prompt --help
     skills/cross-agent-consensus/scripts/consensus capture --help
     skills/cross-agent-consensus/scripts/consensus validate --help
     skills/cross-agent-consensus/scripts/consensus terminate --help
     skills/cross-agent-consensus/scripts/consensus invocation-ready --help
     skills/cross-agent-consensus/scripts/consensus invoke-agent --help
     skills/cross-agent-consensus/scripts/consensus agent-status --help
     skills/cross-agent-consensus/scripts/consensus agent-watch --help
     skills/cross-agent-consensus/scripts/consensus agent-cancel --help
     skills/cross-agent-consensus/scripts/consensus players probe --help
   } > docs/plans_and_designs/deferred-refactor-todos/baselines/<slice>/cli-help.txt
   ```
3. Keep `scripts/consensus` as the command-level compatibility gate for every
   slice. Direct imports are useful for unit tests, but they are not enough.
4. Update `skills/cross-agent-consensus/managed-manifest.json` in the same
   commit as any managed skill file addition, deletion, or content change.
5. Run a base installed-copy smoke test before each branch is considered ready:

   ```bash
   tmp="$(mktemp -d)"
   CODEX_HOME="$tmp/codex" scripts/install-cac --target codex
   "$tmp/codex/skills/cross-agent-consensus/scripts/consensus" --version
   "$tmp/codex/skills/cross-agent-consensus/scripts/consensus" config show
   ```

   Add slice-specific installed-copy command smokes for every moved command.
   For example, Slice 1 must smoke `prompt`, `invocation-ready`,
   `invoke-agent`, `agent-status`, and `agent-cancel` against an installed copy
   with deterministic local commands.
6. Rollback unit for every slice: revert the slice merge commit, reinstall the
   prior managed package with `scripts/install-cac --target codex --update`,
   and rerun the base installed-copy smoke test.

### Slice 1: Extract Invocation Layer

Goal: move invocation behavior into `cross_agent_consensus/invocation/` without
changing command behavior, telemetry shape, or authorization rules.

Steps:

1. Add package files with no behavior change:
   - `cross_agent_consensus/invocation/__init__.py`;
   - `cross_agent_consensus/invocation/adapters.py`;
   - `cross_agent_consensus/invocation/readiness.py`;
   - `cross_agent_consensus/invocation/session_paths.py`;
   - `cross_agent_consensus/invocation/telemetry.py`;
   - `cross_agent_consensus/invocation/process_monitor.py`;
   - `cross_agent_consensus/invocation/status.py`.
2. Move pure data helpers and path helpers first:
   - adapters in `invocation/adapters.py` import `PlayerCapabilities` from
     `cross_agent_consensus.models`; do not introduce an invocation-owned
     capability type in this slice;
   - actor/session path sanitization and session allocation move before process
     behavior;
   - event-tail and state-file readers move before writers.
3. Move player adapter classes:
   - `GenericCliPlayer`;
   - `StructuredJsonCliPlayer`;
   - `ClaudeCliPlayer`;
   - `CodexCliPlayer`;
   - `ManualPlayer`;
   - `get_player_adapter`.
4. Move `invocation-ready` policy checks after adapters but keep policy
   separate from provider behavior:
   - prompt and raw-output root checks;
   - same-round checks;
   - reviewer prompt completeness checks;
   - unattended policy checks;
   - unsafe argv and secret flag rejection.
5. Move telemetry writers:
   - invocation metadata;
   - command metadata;
   - rejected command records;
   - lifecycle events;
   - stream logs;
   - state snapshots;
   - exit records.
6. Move process monitor and cancellation behavior last:
   - subprocess runner;
   - process identity checks;
   - stale and idle classification;
   - status/watch payload generation;
   - cancellation and terminal-session refusal.
7. Keep CLI parser construction and command dispatch thin in `cli.py`.
   Temporary wrapper calls back into `cac_tool.py` are acceptable only inside
   the same slice and must be removed before the slice is complete.
8. Split tests from `tests/test_cac_tool.py` into invocation-focused files:
   - `tests/test_invocation_ready.py`;
   - `tests/test_invocation_players.py`;
   - `tests/test_invocation_process.py`;
   - `tests/test_agent_status_cancel.py`.
9. Delete moved tests from `tests/test_cac_tool.py` rather than duplicating
   them. The remaining `test_cac_tool.py` coverage must be limited to
   command-level compatibility and behavior not yet owned by split files:
   manifest/install smoke, command parser smoke, new-artifact and skeleton
   commands, and any residual config/init/prompt/capture/validation/termination
   compatibility checks not already covered in their focused test modules.
10. Preserve at least one command-level supervised invocation test through
   `skills/cross-agent-consensus/scripts/consensus invoke-agent`.

Validation gates:

```bash
python -m pytest tests/test_invocation_ready.py tests/test_invocation_players.py -q
python -m pytest tests/test_invocation_process.py tests/test_agent_status_cancel.py -q
python -m pytest tests/test_cac_tool.py -q
python -m pytest -q
tmp="$(mktemp -d)"
CODEX_HOME="$tmp/codex" scripts/install-cac --target codex
"$tmp/codex/skills/cross-agent-consensus/scripts/consensus" --version
"$tmp/codex/skills/cross-agent-consensus/scripts/consensus" init --run-root "$tmp/runs" --run-id smoke-invocation --task "smoke" --artifact-locator README.md --reviewer reviewer-a --allow-existing
"$tmp/codex/skills/cross-agent-consensus/scripts/consensus" prompt --run "$tmp/runs/smoke-invocation" --phase reviewer --actor reviewer-a --artifact-version v1
"$tmp/codex/skills/cross-agent-consensus/scripts/consensus" invocation-ready --run "$tmp/runs/smoke-invocation" --actor reviewer-a --prompt "$tmp/runs/smoke-invocation/rounds/round-001/prompts/reviewers/reviewer-a.md" --raw-output "$tmp/runs/smoke-invocation/rounds/round-001/raw/reviewer-a.out" --approved --command python -c "print('ok')"
```

Acceptance criteria:

- `invocation-ready` remains fail-closed for missing actors, unsafe argv,
  incomplete prompts, prompt path escapes, and raw-output path escapes.
- `invoke-agent` still writes compatible `invocation.json`, `command.json`,
  `events.jsonl`, `agent.log`, raw streams, `state.json`, `exit.json`, and
  `final-output.md`.
- Secret argv rejection still records a failed session.
- `agent-status`, `agent-watch`, and `agent-cancel` remain compatible for
  live, stale, completed, and terminal sessions.
- Claude and Codex stream parsing stays covered as parser behavior, not as a
  network or provider integration test.

### Slice 2: Modularize Installer

Goal: make installer behavior testable as Python code while keeping
`scripts/install-cac` as the stable operator entrypoint.

Steps:

1. Introduce a Python module for managed install behavior, for example
   `scripts/cac_install.py`.
2. Move embedded Python from `scripts/install-cac` into the module without
   changing shell arguments or target-home detection.
3. Keep Bash responsible only for:
   - usage text;
   - argument forwarding;
   - locating the source package;
   - invoking Python with the module path.
4. Add unit tests around module-level operations:
   - source manifest validation;
   - source hash mismatch;
   - target path conflict;
   - managed update;
   - update available without `--update`;
   - local modification preservation;
   - target state package mismatch.
5. Keep one end-to-end installer test that executes `scripts/install-cac`
   rather than importing the module.

Validation gates:

```bash
python -m pytest tests/test_install_cac.py -q
python -m pytest tests/test_cac_tool.py -q
python -m pytest -q
tmp="$(mktemp -d)"
CODEX_HOME="$tmp/codex" scripts/install-cac --target codex
"$tmp/codex/skills/cross-agent-consensus/scripts/consensus" --version
HERMES_HOME="$tmp/hermes" scripts/install-cac --target hermes
CLAUDE_HOME="$tmp/claude" scripts/install-cac --target claude --allow-missing-first-class
CODEX_HOME="$tmp/codex-all" HERMES_HOME="$tmp/hermes-all" CLAUDE_HOME="$tmp/claude-all" scripts/install-cac --target all --allow-missing-first-class
```

Acceptance criteria:

- Existing `scripts/install-cac --target hermes|codex|claude|all` behavior is
  preserved.
- User-owned `config/config.local.yaml` remains unmanaged and preserved.
- Managed source hash and target-state checks remain fail-closed.
- Missing first-class targets still follow the documented
  `--allow-missing-first-class` behavior.
- The target matrix covers `codex`, `hermes`, `claude`, and `all`.

### Slice 3: Retire `cac_tool.py` Compatibility Layer

Goal: remove the monolith only after all public behavior is module-owned.

Steps:

1. Confirm no command implementation still depends on `scripts/cac_tool.py`.
2. Reduce `scripts/cac_tool.py` to a compatibility shim importing
   `cross_agent_consensus.cli.main`, or remove it if compatibility references
   have been explicitly retired.
3. Update docs and comments that identify `cac_tool.py` as the implementation
   location.
4. Create or update
   `docs/plans_and_designs/deferred-refactor-todos/allowlists/cac-tool-references.txt`
   with the exact remaining historical or compatibility-only references.
5. Update `managed-manifest.json` for the reduced or removed file.
6. Run installed-copy smoke tests against the stable `scripts/consensus`
   executable.

Validation gates:

```bash
rg -n "cac_tool.py|cac_tool" \
  -g '!docs/plans_and_designs/deferred-refactor-todos/allowlists/cac-tool-references.txt' \
  README.md docs specs profiles skills tests scripts | sort > /tmp/cac-tool-refs.actual
diff -u docs/plans_and_designs/deferred-refactor-todos/allowlists/cac-tool-references.txt /tmp/cac-tool-refs.actual
python -m pytest -q
tmp="$(mktemp -d)"
CODEX_HOME="$tmp/codex" scripts/install-cac --target codex
"$tmp/codex/skills/cross-agent-consensus/scripts/consensus" --version
"$tmp/codex/skills/cross-agent-consensus/scripts/consensus" config show
```

Acceptance criteria:

- `scripts/consensus` remains the only supported executable path.
- Any remaining `cac_tool.py` reference is explicitly historical or
  compatibility-only.
- Installed skill copies work without external Python package dependencies.

### Slice 4: Documentation And Repository Hygiene

Goal: make the repository easier to navigate without changing protocol
semantics or deleting evidence.

Steps:

1. Add `docs/plans_and_designs/README.md` with status labels and links to
   current, implemented, superseded, and backlog designs.
2. Add `runs/README.md` explaining the difference between committed examples,
   fixtures, historical evidence, and local run output.
3. Add `schemas/README.md` declaring whether existing schemas are active public
   API, compatibility artifacts, or historical references.
4. Update `runners/file-based-mvp/README.md` so it distinguishes:
   - deterministic helper CLI;
   - direct/manual capture;
   - supervised explicit invocation;
   - future orchestration.
5. Normalize public-facing wording to Cross-Agent Consensus where repository
   path compatibility does not require Cross-Model Consensus.
6. Do not move or delete historical run evidence in this slice.
7. Do not touch files under `skills/cross-agent-consensus/` in this slice
   unless the branch is reclassified as a managed package change and runs the
   shared installed-copy smoke tests.
8. Create or update
   `docs/plans_and_designs/deferred-refactor-todos/allowlists/docs-status-references.txt`
   with the exact compatibility wording and historical evidence references
   that are expected to remain.

Validation gates:

```bash
rg -n "Cross-Model Consensus|cac_tool.py|runs/" README.md docs specs profiles runners skills | sort > /tmp/docs-status-refs.actual
diff -u docs/plans_and_designs/deferred-refactor-todos/allowlists/docs-status-references.txt /tmp/docs-status-refs.actual
python -m pytest -q
```

Acceptance criteria:

- Design status is discoverable from `docs/plans_and_designs/README.md`.
- Historical evidence is documented rather than silently removed.
- Public wording is consistent except where compatibility constraints are
  stated and enforced by the allowlist diff.

### Slice 5a: Move Profile Defaults To Metadata

Goal: give profile defaults one executable source of truth without changing
document-consensus behavior.

Steps:

1. Decide whether profile metadata should be Markdown frontmatter, a sidecar
   YAML file, or a package-owned defaults module.
2. Move document-consensus validator defaults and round defaults into the
   selected metadata source.
3. Change `infer_validators()` to read profile metadata rather than hard-code
   document validators.
4. Keep the old validator set as a regression fixture.

Validation gates:

```bash
python -m pytest tests/test_init.py tests/test_config.py tests/test_validation.py -q
python -m pytest -q
tmp="$(mktemp -d)"
CODEX_HOME="$tmp/codex" scripts/install-cac --target codex
"$tmp/codex/skills/cross-agent-consensus/scripts/consensus" init --run-root "$tmp/runs" --run-id smoke-profile --task "smoke" --artifact-locator README.md --allow-existing
```

Acceptance criteria:

- Profile defaults have one executable source of truth.
- Existing document-consensus runs infer the same required validators.
- Installed copies can initialize a document-consensus run from the moved
  metadata.

### Slice 5b: Make Validation Evidence IDs Collision-Safe

Goal: prevent validation evidence id collisions without changing validator
semantics.

Steps:

1. Add a deterministic collision suffix or monotonic allocator for validation
   evidence ids.
2. Cover repeated validator captures for the same validator, round, and actor.
3. Preserve existing ids when there is no collision.

Validation gates:

```bash
python -m pytest tests/test_validation.py tests/test_capture.py -q
python -m pytest -q
```

Acceptance criteria:

- Repeated validation evidence writes do not overwrite or duplicate ids.
- Non-colliding validation evidence keeps its current id format.

### Slice 5c: Declare Schema Lifecycle

Goal: make schema status explicit before any schema files are moved or
rewritten.

Steps:

1. Add `schemas/README.md`.
2. Classify each schema as active public API, compatibility artifact, or
   historical reference.
3. Do not move schema files in this slice unless a compatibility note and
   migration path are added.

Validation gates:

```bash
test -s schemas/README.md
for schema in schemas/*.json; do grep -F "$(basename "$schema")" schemas/README.md >/dev/null; done
rg -q "active public API|compatibility artifact|historical reference" schemas/README.md
python -m pytest -q
```

Acceptance criteria:

- Every schema file has a documented lifecycle status.
- Schema movement or deletion remains out of scope unless separately approved.

### Slice 5d: Narrow Command Request Objects

Goal: replace broad `argparse.Namespace` plumbing only where it reduces real
command/service coupling.

Steps:

1. Pick one command family with clear service boundaries.
2. Add request dataclasses for that family only.
3. Keep argparse parsing in `cli.py`.
4. Do not combine request-object cleanup with behavior changes.

Validation gates:

```bash
python -m pytest tests/test_cac_tool.py -q
python -m pytest -q
```

Acceptance criteria:

- Request dataclasses simplify a named command/service boundary.
- No command behavior, flags, or defaults change.
- The slice can be reverted independently from profile and schema work.

### Slice 5e: Add Extension Guides

Goal: document stable extension points after the module boundaries exist.

Steps:

1. Add `docs/cross-agent-consensus/player-adapters.md`.
2. Add `docs/cross-agent-consensus/profiles.md`.
3. Add `docs/cross-agent-consensus/managed-package-updates.md`.
4. Link the guides from `docs/plans_and_designs/README.md` or another
   appropriate docs index.

Validation gates:

```bash
test -s docs/cross-agent-consensus/player-adapters.md
test -s docs/cross-agent-consensus/profiles.md
test -s docs/cross-agent-consensus/managed-package-updates.md
rg -q "player adapter" docs/cross-agent-consensus/player-adapters.md
rg -q "profile" docs/cross-agent-consensus/profiles.md
rg -q "managed package" docs/cross-agent-consensus/managed-package-updates.md
python -m pytest -q
```

Acceptance criteria:

- A contributor can find the player, profile, and managed package update
  procedures from the docs index.
- The guides match the module-owned implementation shape.

### Overall Completion Criteria

This deferred refactor set is complete when:

- invocation behavior is module-owned and has focused tests;
- installer behavior is module-owned and has focused tests;
- `cac_tool.py` is removed or compatibility-only;
- design, run, schema, and runner documentation explain current status;
- profile defaults and validators have an explicit executable source of truth;
- validation evidence ids are collision-safe;
- schema lifecycle status is documented;
- installed-copy smoke tests pass after every managed package change;
- no slice changes public protocol semantics without a separate compatibility
  decision.

## Explicit Non-Goals

- Do not change public protocol semantics as part of mechanical extraction.
- Do not make automatic model or provider selection part of the core protocol.
- Do not let player adapters classify materiality, apply reviewer suggestions,
  or declare consensus.
- Do not introduce a heavyweight orchestration framework just to split code.
- Do not delete historical run evidence without a separate decision.
