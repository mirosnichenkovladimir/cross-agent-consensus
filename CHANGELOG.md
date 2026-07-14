# Changelog

All notable changes to the **cross-agent-consensus** (CAC) skill package are
documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The authoritative version is `skills/cross-agent-consensus/VERSION`; each entry
below corresponds to the value committed at that point.

## [0.11.0] - 2026-07-14

### Changed
- Current protocol records, Python APIs, lifecycle evaluators, prompts,
  reports, templates, and schemas use only `NormalizedFinding` and
  `normalized_finding_id`. Newly normalized identifiers use the
  `nf-round-<n>-<sequence>` form.
- Current record output uses schema `m2-markdown-2`.

### Compatibility
- The version-gated load boundary converts historical `m2-markdown-1`
  `CanonicalFinding` records and `canonical_finding_id` references into the
  current model without changing identifier values.
- Current-schema historical names, historical-schema current names, and runs
  mixing historical and current finding records are rejected.

## [0.10.0] - 2026-07-14

### Added
- Exact-input `OperatorApproval` bindings record the SHA-256 digest of each
  approved prompt, runtime argv, working directory, and locally readable ArtifactVersion.
  `invoke-agent` records a binding before launch, and `consensus run` records
  one binding per selected actor.
- `consensus validate --integrity` recomputes ArtifactVersion, raw reviewer
  payload, validation payload, and approved prompt digests. `terminate` now
  rejects drift before writing `report.md`.
- `RawReviewerOutput` and `ValidationEvidence` written by `capture` include
  `capture_origin`, payload digest, prompt digest, and exact supervised session
  identity when the source came from `invoke-agent`. Completed sessions hash
  `invocation.json`, `command.json`, the prompt, stdout, stderr, copied raw
  output, and extracted final output; capture rejects substituted source bytes.
- Successful run mutations append hash-chained `events.jsonl` entries with a
  sequence, actor, event type, and derived lifecycle phase before and after the mutation.
  `.cac-events-anchor.json` records the event count, tail digest, and journal
  digest so deletion and suffix truncation fail validation. Approval events
  also anchor the canonical `OperatorApproval` record digest.
  `consensus status` prints the derived phase and event count;
  `consensus validate --run-events` checks event sequences and phase
  transitions.

### Changed
- Commands that mutate protocol records acquire the run-scoped `.cac.lock`.
  Markdown and JSONL append helpers also take an advisory file lock.
- Concurrent `consensus init` processes serialize run-id allocation and create
  distinct `-NNN` directories instead of racing on an existence check. The
  allocation lock lives inside the ignored run root rather than the repository root.
- Local ArtifactVersion records preserve the directory used to resolve a
  relative `content_locator`, allowing later validation from another working
  directory.

### Fixed
- Hermes invocation self-tests request a wide enabled-only skill table so the
  `cross-agent-consensus` name is not truncated and falsely reported missing.
- `consensus run` selects the latest same-round ReviewBatch, uses its target
  ArtifactVersion, and writes batch-scoped prompt/raw-output paths. A
  conclusion-validation or remediation batch no longer reuses the first
  fresh-review prompt and artifact.
- Reviewer dispatch compares numeric round identities, so a ReviewBatch that
  records `round-001` still limits `round-1` execution to its
  `expected_reviewer_identities` instead of falling back to every participant.
- Live-session integrity compares all ten `OperatorApproval` binding fields,
  including round, prompt path, and artifact digest. An approval from another
  round or prompt path cannot authorize a captured session.
- Session and run-event integrity choose the 0.10 evidence contract from run
  provenance and anchor presence, not from one mutable schema marker. Removing
  `session-evidence-1` or relabeling version-2 events as legacy now fails
  capture and validation instead of downgrading the integrity checks.
- `invoke-agent --require-existing-approval` verifies the matching
  `OperatorApproval` digest against the hash-chained run journal before it
  allocates a session. Approval also hashes readable artifact bytes when an
  `ArtifactVersion` omits `content_hash_or_null`.
- Configured-CLI reviewer evidence must link to the exact successful session,
  prompt, payload digest, artifact version, and `ReviewBatch` for 0.10 runs.
  A completed session from another batch no longer satisfies terminal review
  evidence.
- Terminal blocker calculation requires unanimous decisions from every
  expected reviewer in the latest applicable ReviewBatch. Decisions from
  different batches cannot be combined, and a newer applicable batch keeps the
  finding open before its first decision arrives. Mixed resolving decisions
  remain disputed, and a later unresolved decision reopens a finding whose
  earlier lifecycle value was resolved.
- Re-review prompts and raw outputs use distinct batch-scoped `rereviews/`
  paths. Re-review skeleton paths and record identifiers also include the
  ReviewBatch identifier, allowing multiple remediation batches in one round.
  ReviewBatch lookup and incremental normalization normalize `round-1` and
  `round-001` before selecting records. Appending a later ReviewBatch no longer
  relocates the first batch's prompt during session-evidence validation.
- The first 0.10 mutation of a pre-0.10 run without `events.jsonl` retains the
  legacy event schema instead of creating a version-2 journal without its
  required `run_initialized` event.
- `consensus terminate` atomically replaces the validated `report.md` skeleton,
  so the documented `report` then `terminate` sequence completes instead of
  failing because the report path already exists.
- Repeated reviewer captures batch-qualify colliding `RawFinding` identifiers
  and extract narrative findings from the supervised final answer instead of
  the CLI JSON event stream.
- `consensus normalize` appends only previously unnormalized `RawFinding`
  records, so later review batches do not duplicate earlier normalized
  findings or require overwriting `normalization.md`.

## [0.9.2] - 2026-07-13

### Fixed
- Markdown frontmatter now quotes strings that resemble booleans, nulls,
  integers, lists, or mappings. `TaskBrief.objective: "true"` remains a string
  after a write/read cycle, and escaped quotes now decode symmetrically.
- Required-field placeholder detection now recognizes only a complete
  `<placeholder>` token. Objectives containing comparison operators such as
  `0 < retries > -1` are accepted.
- Record parsing reports invalid UTF-8, unknown frontmatter record types, and
  known record headings without frontmatter. Required record fields are also
  checked against their declared string, list, mapping, boolean, integer, or
  nullable-string type.

### Changed
- `consensus validate` parses each run into one `RunSnapshot`; all selected
  validators reuse the same records and parser diagnostics.
- Cross-record reference checks moved to `link_validation.py` and are grouped
  by review batches, reviewer records, normalization, finding lifecycle,
  decisions, and terminal records.
- Prompt policy moved to `prompt_command.py`, capture owns `cmd_capture`, and
  `run_macro.py` calls typed command-input dataclasses. The previous
  `cli.py`/`run_macro.py` late-import cycle is removed.
- Added pinned pytest/mypy development dependencies, mypy configuration, and
  a GitHub Actions Python 3.11/3.13 quality job.

## [0.9.1] - 2026-06-04

### Added
- Codex trusted-directory preflight in `invoke-agent` and `invocation-ready`.
  When `--player codex-cli` is launched with the real `codex` binary but argv
  is missing `--skip-git-repo-check`, both commands now surface the exact
  command-line fix and exit `3` **before** allocating a session. Operators
  outside Codex's trusted-dir list previously got a `failed` session record
  and a noisy `failed=` count in `consensus status` for a fully recoverable
  environment problem. The check is restricted to the real `codex` binary
  (matches `codex` or `*/codex`); wrappers and test stubs under the
  `codex-cli` player are not blocked.
- `--skip-git-repo-check` added to the default `reviewer_clis.codex.command`
  in `config/defaults.yaml` and the installed-default fallback in
  `cross_agent_consensus/cli.py`. Existing local overrides should add the
  flag (the preflight surfaces the fix automatically).
- Failed-session supersession bookkeeping. When `allocate_agent_session`
  creates a new session in an actor directory that already contains a
  previously failed attempt, the older `state.json` is atomically stamped
  with `superseded_by: <new-session-name>` and `superseded_at`. The new
  helper `mark_state_superseded_by` only acts on `state == "failed"`
  and is idempotent.
- `agent_session_state_counts` buckets superseded sessions under
  `"superseded"` instead of `"failed"`. A Codex first-attempt that the
  operator successfully retried no longer inflates the `failed=` count
  surfaced by `consensus status`.
- `prepare_agent_session` writes an initial `state.json` with
  `state="prepared"` **before** `subprocess.Popen`. A pre-exec failure
  (executable not on PATH, cwd missing, interpreter set-up errors) now
  leaves durable launch evidence on disk instead of an empty session dir.

### References
- Triage: `plans_and_designs/cac-design-notes/feedback-notes-04-06/prioritization-opinion/tier-5-phase-7-followup/README.md`
  (bundle T5-A; covers Phase-7 items #1, #2 partial, #3).

## [0.9.0] - 2026-06-04

### Added
- Optional run-feedback artifact. With `feedback.enabled: true` (top-level key
  in `config/defaults.yaml` or a local override), `scripts/consensus report`
  also writes `runs/<run_dir>/cac-run-feedback.md` — a skeleton with four fixed
  H2 sections (`Performance anomalies`, `Critical errors`,
  `Small bugs / rough edges`, `Logic gaps`). The orchestrator-agent overwrites
  the bullets before `terminate`; empty sections keep `_none_` so the artifact
  shape is uniform across runs. Off by default; opt-in for debugging and
  skill-improvement sessions.
- `feedback.enabled` is now part of `ConfigResolution.effective_values`, so the
  flag's effective layer (installed_defaults vs user_local vs project) is
  recorded alongside other config provenance.

### References
- Design: `plans_and_designs/cac-design-notes/cac-feedback-debug.md`.

## [0.8.3] - 2026-06-04

### Changed
- `normalize_round_id` canonicalizes all input forms (`"1"`, `"round-1"`,
  `"round-001"`) to the short `round-N` id used in records. The on-disk
  directory format remains zero-padded (`round-001`) via `round_dir`.
- `--round` help text on `invoke-agent`, `agent-status`, `agent-watch`,
  `agent-peek`, `agent-cancel`, `run`, and `normalize` documents the
  accepted forms explicitly.
- `capture.py` uses `normalize_round_id` instead of an ad-hoc `round-` prefix
  check; the prior code accepted `"round-1"` and `"1"` but not `"round-001"`.

### References
- Closes friction-log item #11 / CF-006 (CLI `--round round-1` default vs
  on-disk `round-001` layout).

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

[0.8.3]: https://gitlab.corp.cloudlinux.com/kc-python-automation/cross-model-consensus/-/compare/v0.8.2...v0.8.3
[0.8.2]: https://gitlab.corp.cloudlinux.com/kc-python-automation/cross-model-consensus/-/compare/v0.8.1...v0.8.2
[0.8.1]: https://gitlab.corp.cloudlinux.com/kc-python-automation/cross-model-consensus/-/compare/v0.8.0...v0.8.1
[0.8.0]: https://gitlab.corp.cloudlinux.com/kc-python-automation/cross-model-consensus/-/compare/v0.7.3...v0.8.0
[0.7.3]: https://gitlab.corp.cloudlinux.com/kc-python-automation/cross-model-consensus/-/compare/v0.7.2...v0.7.3
[0.7.2]: https://gitlab.corp.cloudlinux.com/kc-python-automation/cross-model-consensus/-/compare/v0.7.1...v0.7.2
[0.7.1]: https://gitlab.corp.cloudlinux.com/kc-python-automation/cross-model-consensus/-/compare/v0.7.0...v0.7.1
[0.7.0]: https://gitlab.corp.cloudlinux.com/kc-python-automation/cross-model-consensus/-/compare/v0.6.0...v0.7.0
[0.6.0]: https://gitlab.corp.cloudlinux.com/kc-python-automation/cross-model-consensus/-/compare/v0.5.1...v0.6.0
[0.5.1]: https://gitlab.corp.cloudlinux.com/kc-python-automation/cross-model-consensus/-/tags/v0.5.1
