# Codex Runtime Notes

Codex is a first-class install target for the `cross-agent-consensus` skill package and can act as Author Agent, Reviewer Agent, or Orchestrator in separate isolated sessions.

## Install And Discovery

```bash
./scripts/install-cac --target codex
./scripts/install-cac --target codex --update
```

Detection order:

- `$CODEX_HOME`;
- existing `$HOME/.codex`;
- `codex` command on `PATH`, installing to `$HOME/.codex`.

Install path:

```text
${CODEX_HOME:-$HOME/.codex}/skills/cross-agent-consensus
```

Trigger examples after install:

```text
cac: do design for <feature>
cac: implement this feature and do review
Use CAC to do <task> with main <author> and validators <reviewers>.
```

`CAC`/`cac` are invocation aliases for generic task execution plus validation. Installed package and protocol records stay named `cross-agent-consensus`. A pure review is only one task shape, not the whole feature.

## Role Mapping

- Preferred first dogfood use: Author Agent, with heterogeneous reviewers.
- Reviewer and Orchestrator roles are supported when sessions and actor identities are distinct.
- Protocol semantics live in the installed skill package; this file only describes Codex install and role discovery.
