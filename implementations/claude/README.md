# Claude Runtime Notes

Claude is a best-effort install target for the `cross-agent-consensus` skill package until local skill discovery is confirmed.

## Install And Discovery

```bash
./scripts/install-cac --target claude
./scripts/install-cac --target claude --update
```

Detection order:

- `$CLAUDE_HOME`;
- existing `$HOME/.claude`;
- `claude` command on `PATH`, installing to `$HOME/.claude`.

Install path:

```text
${CLAUDE_HOME:-$HOME/.claude}/skills/cross-agent-consensus
```

The installer prints that Claude discovery is best-effort. Missing Claude is a warning for `--target all` and a failure for explicit `--target claude`.

Trigger examples after install:

```text
cac: do design for <feature>
cac: implement this feature and do review
Use CAC to do <task> with main <author> and validators <reviewers>.
```

`CAC`/`cac` are invocation aliases for generic task execution plus validation. Installed package and protocol records stay named `cross-agent-consensus`. A pure review is only one task shape, not the whole feature.

## Role Mapping

- Preferred first dogfood use: independent Reviewer Agent for design/protocol documents and code plans.
- Author and re-review roles are possible when sessions and actor identities are distinct.
- Protocol semantics live in the installed skill package; this file only describes Claude install and role discovery.
