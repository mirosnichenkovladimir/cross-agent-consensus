# Hermes Runtime Notes

Hermes is a first-class install target for the `cross-agent-consensus` skill package and is best mapped to the Orchestrator role.

## Install And Discovery

```bash
./scripts/install-cac --target hermes
./scripts/install-cac --target hermes --update
```

Detection order:

- `$HERMES_HOME`;
- existing `$HOME/.hermes`;
- `hermes` command on `PATH`, installing to `$HOME/.hermes`.

Install path:

```text
${HERMES_HOME:-$HOME/.hermes}/skills/cross-agent-consensus
```

Trigger examples after install:

```text
cac: do design for <feature>
cac: implement this feature and do review
Use CAC to do <task> with main <author> and validators <reviewers>.
```

`CAC`/`cac` are invocation aliases for generic task execution plus validation. Installed package and protocol records stay named `cross-agent-consensus`. A pure review is only one task shape, not the whole feature.

## Role Mapping

- Preferred: Orchestrator.
- Also possible: Author or Reviewer only in isolated sessions with distinct actor identities.
- Protocol semantics live in the installed skill package; this file only describes Hermes install and role discovery.
