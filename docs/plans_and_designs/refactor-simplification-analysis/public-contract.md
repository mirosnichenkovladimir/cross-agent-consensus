# Cross-Agent Consensus Public Behavior Contract

Status: proposed
Date: 2026-06-01

This contract freezes the behavior that must remain stable while the
`cross-agent-consensus` helper internals are split into smaller modules.

## Stable Entrypoint

The supported executable entrypoint is:

```text
skills/cross-agent-consensus/scripts/consensus
```

It must work from both the repository checkout and an installed managed skill
copy. `scripts/cac_tool.py` may remain as a compatibility-only wrapper for
historical direct callers, but command behavior is owned by
`cross_agent_consensus.cli` and callers should not depend on importing or
executing `cac_tool.py` directly.

## Stable Command Surface

These command categories are compatibility-covered during the refactor:

- `--version`
- `config show`, `config validate`, `config paths`, `config setup`
- `init`
- `status`
- `validate`
- `prompt`
- `capture`
- `new-artifact`
- `response-skeleton`
- `rereview-skeleton`
- `invocation-ready`
- `invoke-agent`
- `agent-status`
- `agent-watch`
- `agent-cancel`
- `players probe`
- `terminate`

Existing required flags, enum choices, return-code conventions, run files, and
terminal validation behavior should remain compatible unless a later design
explicitly approves a breaking change.

## Stable Run Layout

New runs use the round-first layout documented in
`skills/cross-agent-consensus/references/record-contract.md`:

```text
runs/<run_id>/
  run.md
  artifacts/
  rounds/
    round-001/
      round.md
      prompts/
      raw/
      reviews/
      validation.md
  validation.md
  escalations.md
  termination.md
  backlog.md
```

Legacy ledger-layout runs remain readable and validatable during the
compatibility period.

## Stable Configuration Precedence

Configuration resolution order remains:

```text
installed defaults
  -> user-local config
  -> project config
  -> task-file config
  -> CLI flags
```

Persistent config must continue to reject unattended invocation and secret-like
values.

## Stable Invocation Boundary

Named external reviewers and validators must still go through supervised
`invoke-agent` when live invocation telemetry is claimed. Direct `capture`
remains valid for manual or imported evidence, but must not be represented as a
monitored runtime session.

## Compatibility Gate

Run this gate after each extraction step:

```bash
python -m pytest -q
```

At minimum, the historical behavior in `tests/test_cac_tool.py` must remain
covered until equivalent split tests replace it.
