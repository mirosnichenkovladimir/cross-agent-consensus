# CAC Skill Simplify Follow-Up Findings

**Context:** Follow-up notes from running Claude Code `/simplify skill` against the cross-agent-consensus skill changes in `repos/cross-model-consensus`.

**Scope:** Issues explicitly flagged as real concerns but intentionally not fixed during the small simplification pass.

---

## 1. Validation evidence ID collision risk

**File:** `skills/cross-agent-consensus/scripts/cac_tool.py`

**Area:** validation evidence capture, around the `evidence_id` generation logic.

**Finding:** The telemetry-related change altered validation evidence IDs from sequential IDs:

```text
validation-evidence-NNN
```

to content-derived IDs:

```text
validation-evidence-{validator_id}-{raw_path_stem}
```

Two captures with the same `validator_id` and `raw_path.stem` can now collide, where the old behavior would increment to the next sequential ID.

**Why it matters:** Validation evidence records are protocol evidence. ID collisions can overwrite, confuse, or make cross-references ambiguous.

**Recommended next step:** Decide whether the content-derived ID scheme is intentional. If not, revert to monotonic/sequential IDs or add a deterministic collision suffix while preserving stable references.

---

## 2. Inconsistent `PermissionError` semantics for process checks

**File:** `skills/cross-agent-consensus/scripts/cac_tool.py`

**Area:** `process_exists` and `process_identity_matches`.

**Finding:** `process_exists` treats `PermissionError` as evidence that the process exists, while `process_identity_matches` treats `/proc` `PermissionError` as no match.

**Why it matters:** Agent cancel/status behavior can be wrong on hosts where the PID exists but `/proc` access is restricted. A real process may be treated inconsistently across status and identity checks.

**Recommended next step:** Define one policy for restricted process metadata, then update both helpers and tests to match it. Prefer a tri-state result if identity is unknown rather than false.

---

## 3. Heartbeat rewrites `state.json` every interval

**File:** `skills/cross-agent-consensus/scripts/cac_tool.py`

**Area:** heartbeat/state update loop, around `write_agent_state` usage.

**Finding:** The heartbeat rewrites `state.json` every interval, even when no material state changed except idle/heartbeat timing fields.

**Why it matters:** This creates mtime churn for file watchers and can make status consumers react to noisy writes. It may also increase disk activity for long-running agent sessions.

**Why it was not fixed in the simplify pass:** Cancel/status logic may depend on fresh idle timing, so skipping writes needs a deliberate contract change and tests.

**Recommended next step:** Separate volatile heartbeat data from stable state, or write only when externally observable status fields change. Add tests for cancel/status freshness before changing behavior.

---

## 4. Helper duplication and JSON output boilerplate

**File:** `skills/cross-agent-consensus/scripts/cac_tool.py`

**Finding:** Several helpers or idioms overlap:

- `atomic_write_text` vs `atomic_write_new`
- `append_jsonl` vs `append_text`
- `padded_round_id` vs `normalize_round_id`
- repeated `print(json.dumps(..., indent=2, sort_keys=True))` call sites

**Why it matters:** Duplication increases maintenance cost and makes future behavior changes easy to apply inconsistently.

**Why it was not fixed in the simplify pass:** Consolidating these helpers is a broader refactor with wider test impact.

**Recommended next step:** Do a separate helper-consolidation pass with tests around file creation semantics, append behavior, round normalization, and JSON CLI output formatting.

---

## 5. `write_agent_state` argument sprawl and fake `argparse.Namespace`

**File:** `skills/cross-agent-consensus/scripts/cac_tool.py`

**Areas:**

- `write_agent_state` call sites
- `cmd_invoke_agent`, around the synthetic `argparse.Namespace` created for reuse

**Finding:** `write_agent_state` takes many positional arguments, and `cmd_invoke_agent` constructs a fake `argparse.Namespace` to call another command path.

**Why it matters:** Wide positional APIs are error-prone, and fake CLI namespaces couple internal logic to command-line parsing details.

**Recommended next step:** Introduce a small typed data object for agent state updates and extract reusable internal functions that do not require an `argparse.Namespace`.

---

## Verification from simplify pass

The small simplification pass that produced these notes completed with:

```text
python -m pytest tests/test_cac_tool.py -q
39 passed
```
