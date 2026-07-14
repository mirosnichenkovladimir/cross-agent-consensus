# Hermes Connector

Hermes is both a first-class install target and a resumable CAC participant
runtime. Package 0.18.0 adds the stable `hermes-cli` adapter and the
`hermes-reviewer-default` ExecutionProfile.

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
- The built-in `hermes` ParticipantIdentity uses `reviewer-default` and
  `hermes-reviewer-default`; it is available for explicit selection but is not
  added to `participants.reviewers` in package defaults.

## ExecutionProfile

```yaml
execution_profiles:
  hermes-reviewer-default:
    adapter: hermes-cli
    command:
      - python3
      - -m
      - cross_agent_consensus.hermes_cli
      - --ignore-rules
    prompt_transport: stdin
    output_mode: stream_json
    supports_resume: true
    env:
      - HOME
      - PATH
      - PYTHONPATH
      - HERMES_HOME
      - HERMES_INFERENCE_MODEL

participant_identities:
  hermes:
    participant_profile_id: reviewer-default
    execution_profile_id: hermes-reviewer-default
```

Set `model:` in this ExecutionProfile to make the bridge add Hermes
`--model <model>`. Hermes has no corresponding `reasoning_effort` option, so
CAC rejects that field for `hermes-cli`.

## Connector Boundary

Hermes 0.16 quiet mode writes final response text to stdout and
`session_id: <id>` to stderr. `cross_agent_consensus.hermes_cli` reads the CAC
prompt from stdin, calls `hermes chat --query ... --quiet --source tool`, and
emits two JSONL event types:

```json
{"resumed": false, "session_id": "20260715_015302_0fb6ea", "type": "session.started"}
{"result": "review text", "session_id": "20260715_015302_0fb6ea", "type": "result"}
```

The prompt remains outside the recorded ExecutionProfile argv and
`command.json`. Hermes itself requires the prompt in `--query`, so the prompt
is visible transiently in the child process argv on operating systems that
expose process arguments.

CAC extracts the Hermes session ID from the JSONL envelope and writes a
`provider_session_captured` RunJournal entry. A later
`--resume-provider-session-entry <entry-id>` makes the adapter add
`--resume <Hermes-session-id>` to the bridge command. The existing provider
session ownership rules still require the same ParticipantIdentity,
ParticipantProfile role, ExecutionProfile, run, and ArtifactVersion lineage.
Hermes may replace the session ID after mid-turn context compression. The
`hermes-cli` adapter declares this rotation capability, links the new ID to the
selected predecessor entry, and makes the new capture the only resumable leaf.

`agent-cancel` and stale timeout terminate the entire process group containing
the bridge and Hermes child. A zero Hermes exit without exactly one session ID
fails as `missing_session_identifier`; conflicting session IDs and nonzero
Hermes exits do not create a provider-session entry.
On macOS, CAC reads the child start time through `libproc` rather than `ps`, so
PID-reuse protection remains available when a sandbox denies process listing.

## Authentication And Version Detection

CAC does not install Hermes providers and does not store credentials. Complete
Hermes setup with `hermes setup`, `hermes login`, or `hermes auth` outside CAC.
The built-in ExecutionProfile passes `HOME` and `HERMES_HOME`, so Hermes can
read its own configuration and secret store. Add only required environment
variable names to a project ExecutionProfile; values stay out of CAC config
and protocol records.

`consensus players probe --player hermes-cli --command -- python3 -m
cross_agent_consensus.hermes_cli --ignore-rules` resolves the `hermes` binary
on `PATH` and runs `hermes --version` with a five-second limit. The capability
record returns the detected path and version string. The 0.18.0 connector was
dogfooded with Hermes Agent 0.16.0.

Both `scripts/consensus` and the historical `scripts/cac_tool.py` launcher add
the installed skill root to child `PYTHONPATH`; the bridge therefore imports
from an unrelated artifact working directory even when the parent environment
did not define `PYTHONPATH`.
