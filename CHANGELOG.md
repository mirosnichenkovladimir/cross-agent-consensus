# Changelog

All notable changes to the **cross-agent-consensus** (CAC) skill package are
documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The authoritative version is `skills/cross-agent-consensus/VERSION`; each entry
below corresponds to the value committed at that point.

## [0.8.2] - 2026-06-04

### Fixed
- `consensus selftest --invocation` now reads the installer's state file
  `.cross-agent-consensus-managed.json` (the file `scripts/install-cac`
  actually writes) instead of looking for the source-side
  `managed-manifest.json` at the install path. The 0.7.3 selftest had
  asserted the wrong filename, so every post-install selftest after 0.8.1
  wired the check into the installer was reporting a hard failure (exit 3)
  with `managed-manifest.json not found` despite a successful install.
- Each managed file is now re-hashed in place and compared against
  `installed_sha256` from the state file — matching the integrity contract
  the installer establishes at write time.

### Tests
- `tests/test_selftest.py::_stage_install` updated to stage the state-file
  schema (`installed_sha256` / `source_sha256`) instead of the source
  manifest. All 191 tests pass.

## [0.8.1] - 2026-06-04

### Added
- `scripts/install-cac` now wires post-install verification via
  `consensus selftest --invocation --host <target>`.
  - New flags: `--selftest` (default on) and `--no-selftest`.
  - Per target, after a successful managed-file copy the selftest runs against
    the just-installed package via `CAC_SELFTEST_HOME_OVERRIDE`.
  - Only the conventional layout (`basename(home) == .target`) is exercised;
    non-conventional homes (custom `HERMES_HOME`, etc.) are skipped with a
    warning.
  - Per-target summary line carries `[selftest:ok|warning|failed|skipped_*]`.

### Changed
- Selftest exit code 3 (hard failure) flips the overall installer exit code to
  1; exit 2 (mixed/warning) preserves the install but logs a warning.
- `managed-manifest.json` hashes regenerated.

## [0.8.0] - 2026-06-04

### Added
- `consensus run --phase reviewer --execute-reviewers` — single-command driver
  for a same-round phase. Composes existing `cmd_prompt` / `cmd_invoke_agent` /
  `cmd_capture` without forking the consensus CLI.
- New `OperatorApproval` record (REQUIRED_FIELDS + ID_FIELDS + mechanism enum),
  stamped per run with `mechanism=cli_approved_flag` or `policy_unattended`.
- New helper `policy_allows_unattended_scoped()` — fail-closed scope check
  (list-of-tokens or dict form). Legacy `policy_allows_unattended` retained for
  invocation-ready back-compat.
- `run_macro.py`: `ActorPlan` dataclass drives both execution and manual
  fallback; fallback commands round-trip through `invoke-agent` argparse.

### Changed
- Failed sessions use `cmd_capture --no-append-record`; siblings still run
  (round-level isolation contract preserved).

### Tests
- 28 new tests for the run macro; 191 total passing.

### References
- Addresses DESIGN.md R1–R8 (Tier 2 #4).

## [0.7.3] - 2026-06-04

### Added
- `consensus selftest --invocation` (Feature 2A of tier-2 design) — host-agnostic
  self-diagnostic for the installed CAC skill package.
  - Probes canonical install paths for Claude Code, Codex, and Hermes.
  - Parses `SKILL.md` frontmatter and asserts the literal phrase
    `Invocation aliases: CAC, cac` is in the description (LLM routing signal
    locked in by Feature 3's docs test; R6 — any future description change
    must be co-released with a selftest update).
  - Verifies `managed-manifest` SHA256s for the installed copy.
  - Hermes: best-effort `hermes skills list` check; warns if the skill is
    registered but disabled.
- `--write-suggested-rule` writes/replaces an idempotent
  `<!-- cac:begin --> ... <!-- cac:end -->` block in an opt-in target file.
  Never touches text outside the markers.

### Exit codes
- `0` — every detected host healthy.
- `2` — mix of healthy and broken on detected hosts.
- `3` — no install detected, or only detected host is broken.

## [0.7.2] - 2026-06-04

### Added
- `SKILL.md`: new "Quick Path (first 30 seconds)" section between Versioning
  and Long-Running Invocation, covering the current shipped 0.7.x lifecycle
  with every command's required flags.
- `tests/test_docs_consistency.py`: asserts the M2 contradiction is gone, that
  the Quick Path keeps its canonical commands, and pins the
  `Invocation aliases: CAC, cac` phrase used by the upcoming selftest check.

### Changed
- `templates/prompts.md`: replaced the unconditional "Do not automatically
  invoke external runtimes" ban (line 3) with a delivery-rules pointer to
  `SKILL.md` §M2 Boundary. The pre-delivery record-copy rule is preserved
  verbatim.
- `managed-manifest.json` regenerated.

### References
- Closes codex R7 (Quick Path runnability / required flags).
- Lays groundwork for codex R6 (selftest must match SKILL.md description
  verbatim).

## [0.7.1] - 2026-06-04

Bundles the followup-feedback.md fixes from the 0.7.0 friction log.

### Added
- `consensus prompt --dry-run` — resolves the target without writing.
- `consensus init --dry-run` — prints layout without writing.
- `invoke-agent` mirrors `final-output` beside `--raw-output`.
- `agent-status` emits derived summary counts.

### Changed
- Peek-loop is the documented default pattern for long-running `invoke-agent`
  (SKILL.md, references/invocation.md).
- Invocation-ready required-args quick reference added to SKILL.md.
- Skill rule: single-reviewer trigger overrides.
- Skill rule: operator approval handshake.

### Fixed
- `normalize` silently replaces the init-stub `normalization.md`.

### Internal
- Several refactors removing duplication and layering violations:
  - `NARRATIVE_FINDING_ID_RE` moved to shared `records` module.
  - `INIT_STUB_NORMALIZATION` and `EMPTY_AGENT_STATUS_SUMMARY` extracted as
    named constants and deduplicated across modules.
  - Final-output mirror path centralized in a reusable helper
    (`FINAL_OUTPUT_MIRROR_SUFFIX`).
  - Agent status summary file processing and deduplication optimized.
- `managed-manifest.json` hashes regenerated.

## [0.7.0] - 2026-06-04

### Added
- Tier-1 orchestrator optimizations for 2-reviewer CAC runs (PR 1+2+3 bundled),
  reducing orchestrator load from ~40 min to ~15 min without touching the M2
  supervised-invocation boundary.

## [0.6.0] - 2026-06-03

### Added
- `cac agent-peek` — read-only command for operators to inspect a single
  monitored agent session without mutating it. Snapshot includes derived
  state, idle and heartbeat age, and inferred did/now phrases drawn from
  `events.jsonl` and `agent.log` telemetry. Optional `--follow`.

### Removed
- Unused `runners/`, `profiles/`, `examples/`, and `docs/` directories.

### Changed
- Dropped `version` from `SKILL.md` frontmatter; `VERSION` is the single
  source of truth.

## [0.5.1] - 2026-06-03

### Added
- Initial published baseline: CAC review orchestration and reports.

[0.8.2]: https://gitlab.corp.cloudlinux.com/kc-python-automation/cross-model-consensus/-/compare/v0.8.1...v0.8.2
[0.8.1]: https://gitlab.corp.cloudlinux.com/kc-python-automation/cross-model-consensus/-/compare/v0.8.0...v0.8.1
[0.8.0]: https://gitlab.corp.cloudlinux.com/kc-python-automation/cross-model-consensus/-/compare/v0.7.3...v0.8.0
[0.7.3]: https://gitlab.corp.cloudlinux.com/kc-python-automation/cross-model-consensus/-/compare/v0.7.2...v0.7.3
[0.7.2]: https://gitlab.corp.cloudlinux.com/kc-python-automation/cross-model-consensus/-/compare/v0.7.1...v0.7.2
[0.7.1]: https://gitlab.corp.cloudlinux.com/kc-python-automation/cross-model-consensus/-/compare/v0.7.0...v0.7.1
[0.7.0]: https://gitlab.corp.cloudlinux.com/kc-python-automation/cross-model-consensus/-/compare/v0.6.0...v0.7.0
[0.6.0]: https://gitlab.corp.cloudlinux.com/kc-python-automation/cross-model-consensus/-/compare/v0.5.1...v0.6.0
[0.5.1]: https://gitlab.corp.cloudlinux.com/kc-python-automation/cross-model-consensus/-/tags/v0.5.1
