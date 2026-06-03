# CAC Agent Player Interface And Invocation Telemetry

## Summary

Add a small invocation layer to the `cross-agent-consensus` skill CLI so an Orchestrator can start, monitor, and collect evidence from explicitly selected agent runtimes.

The core idea is a `PlayerAdapter` abstraction. The CAC protocol continues to speak in terms of Authors, Reviewers, Validators, Artifact Versions, Raw Findings, and Validation Evidence. The CLI execution layer speaks to concrete players such as `generic-cli`, Codex CLI, Claude Code, DeepSeek CLI, or another future agent runtime.

This design intentionally does not define persistent player configuration. A parallel config feature can later map friendly player names to adapter defaults and commands. Until then, every invocation uses explicit command-line arguments and records the resolved command in the run folder.

## Current State Analysis

The repository already has the durable CAC protocol and deterministic helper scripts:

- `specs/protocol.md` is runtime-neutral. It explicitly treats CLIs, SDKs, skills, plugins, graph engines, and hosted agents as implementation profiles rather than protocol requirements.
- `skills/cross-agent-consensus/scripts/consensus` exposes `init`, `prompt`, `capture`, `invocation-ready`, `status`, `validate`, and `terminate`.
- `consensus prompt` writes exact role prompts under the active round folder.
- `consensus invocation-ready` checks participant identity, prompt location, raw-output destination, command availability, and approval or run-scoped unattended policy before direct external invocation.
- `consensus capture` copies raw output into the run folder and creates deterministic reviewer or validator records where supported.
- `docs/plans_and_designs/cac-skill-configuration.md` designs layered config, including future reviewer CLI mappings.
- `docs/plans_and_designs/cac-reviewer-conclusion-validation.md` mentions optional agent session metadata and file-backed deliberation, but it does not define a concrete process supervision interface.

What exists today is enough to preserve prompts and final raw outputs, but it does not give the Orchestrator a durable view of what invoked agent processes are doing while they run.

## Dogfood Feedback: Direct Capture Is Not Invocation Telemetry

A validation run for `KPT-6036-bugfixer-skill-integration-review-pipeline` exposed an important operator pitfall: reviewer CLIs were invoked directly and then captured as `RawReviewerOutput`. The run was still valid as a CAC manual/direct-capture run, but it produced no agent session telemetry because `consensus invoke-agent` was not used.

Observed direct-capture shape:

```text
rounds/round-001/prompts/<actor>-review.md
rounds/round-001/raw/<actor>.stdout.txt
rounds/round-001/reviews/<actor>.md
```

Missing monitored-invocation shape:

```text
rounds/round-001/agents/<actor>/session-001/invocation.json
rounds/round-001/agents/<actor>/session-001/command.json
rounds/round-001/agents/<actor>/session-001/events.jsonl
rounds/round-001/agents/<actor>/session-001/agent.log
rounds/round-001/agents/<actor>/session-001/stdout.raw
rounds/round-001/agents/<actor>/session-001/stderr.raw
rounds/round-001/agents/<actor>/session-001/state.json
rounds/round-001/agents/<actor>/session-001/exit.json
rounds/round-001/agents/<actor>/session-001/final-output.md
```

Root cause: `consensus capture` preserves protocol evidence after an agent or human has produced output. It does not supervise the producing process and cannot retroactively create honest live telemetry. Only `consensus invoke-agent` creates session directories, lifecycle events, heartbeats, state snapshots, cancellation metadata, detailed `agent.log` message streams, stdout/stderr stream files, and final-output extraction.

Design requirement: documentation, help text, and examples must make this boundary explicit. A direct CLI command followed by `consensus capture` must be described as a manual/direct-capture lane. When operators care about live status, heartbeat, idle/stale detection, cancellation, event tail, stdout/stderr stream files, or final-output extraction, the documented path must be `consensus invoke-agent`.

Fix suggestions:

- Add a prominent warning to `consensus capture --help`: capture does not create `rounds/*/agents/*` telemetry; use `invoke-agent` before capture when monitored invocation is desired.
- Add a prominent note to `consensus invocation-ready --help`: this command validates direct invocation readiness but does not start or monitor the command; prefer `invoke-agent` for supervised execution.
- Add `agent-status` remediation text when no session exists: "No monitored agent session exists for this actor/round. If output was captured directly, this is expected; use `invoke-agent` next time to record telemetry."
- Add a run-level optional note or validation warning for runs with `RawReviewerOutput` records but no `rounds/*/agents/*` sessions, so final reports can distinguish valid manual evidence from missing telemetry.
- Keep the boundary audit-safe: do not synthesize `events.jsonl`, `state.json`, or `exit.json` after the fact from raw stdout. At most, add an explanatory note to `run.md` or `termination.md`.

## Gap

The missing piece is live invocation control and process telemetry:

- no standard command starts an agent process after `invocation-ready`;
- no common representation of started, running, idle, stale, waiting, completed, failed, or cancelled agent sessions;
- no durable `events.jsonl` stream for process lifecycle and adapter-normalized events;
- no standard location for stdout, stderr, exit metadata, or session state under the round folder;
- no common way to ask "is reviewer X still working or stuck?";
- no adapter boundary for CLIs with different JSON stream formats, prompt transports, and resume semantics;
- no shared implementation for timeouts, stale detection, cancellation, or final-output extraction.

Without this layer, every Orchestrator must hand-roll process supervision around each agent CLI.

## Design Goals

- Keep CAC protocol records runtime-neutral and focused on review evidence.
- Add a deterministic CLI layer for explicitly selected players.
- Support different agent runtimes behind one normalized event and state model.
- Preserve exact prompts, raw stdout, raw stderr, command metadata, and exit metadata under the run folder.
- Detect idle and stale agents without assuming a provider-specific API.
- Make the first implementation useful with a generic CLI adapter.
- Add specialized Codex and Claude adapters for the structured runtime streams those CLIs expose, without changing the Orchestrator contract.
- Leave persistent config and player registry resolution to the parallel config feature.

## Non-Goals

- No automatic model or provider selection.
- No persistent YAML player registry in this feature.
- No direct agent-to-agent chat.
- No semantic normalization of reviewer findings by the invocation layer.
- No automatic application of reviewer suggestions.
- No replacement for `consensus capture`, protocol validation, or terminal checks.
- No requirement that every player support JSON events, resume, cancellation beyond process signals, or interactive input.

## Concepts

### Role

A protocol role describes why an actor participates:

```text
author | reviewer | validator | orchestrator
```

Roles remain protocol concepts.

### Actor

An actor is the run-specific identity recorded in `Participants`, for example:

```text
author-codex
reviewer-claude
reviewer-codex
```

Actors remain stable within a run and are used in protocol records.

### Player

A player is the concrete execution backend used to invoke an actor:

```text
generic-cli
codex-cli
claude-cli
deepseek-cli
manual
```

Players are implementation details. A reviewer can be `reviewer-claude` in the protocol while the invocation layer uses the `claude-cli` player to execute that actor.

### Player Session

A player session is one concrete invocation attempt for one actor in one phase. It records command metadata, process state, event stream, raw output, and exit result.

An actor can have multiple sessions across retries or rounds:

```text
rounds/round-001/agents/reviewer-claude/session-001/
rounds/round-001/agents/reviewer-claude/session-002/
```

Round folders use the canonical zero-padded `round-NNN` form on disk. CLI commands should accept `round-1` as a user-facing alias, normalize it to `round-001` before path construction, and persist the normalized form in telemetry.

## PlayerAdapter Interface

The shared interface should live in the CLI implementation layer, not the protocol spec.

Suggested Python shape:

```python
class PlayerAdapter:
    player_id: str

    def probe(self, command: list[str]) -> PlayerCapabilities:
        """Return executable availability and static capabilities."""

    def build_command(self, invocation: AgentInvocation) -> CommandSpec:
        """Return argv, env allowlist, cwd, prompt transport, and output mode."""

    def parse_stdout_event(self, line: bytes, telemetry: AgentTelemetry) -> AgentEvent | None:
        """Normalize one stdout line or chunk when the player exposes structured events."""

    def parse_stderr_event(self, line: bytes, telemetry: AgentTelemetry) -> AgentEvent | None:
        """Normalize one stderr line or chunk when useful."""

    def extract_final_output(self, session: AgentSessionPaths) -> FinalOutput:
        """Return final user-visible answer text or a locator to raw output."""

    def classify_state(self, telemetry: AgentTelemetry, policy: MonitorPolicy) -> AgentState:
        """Map process and activity facts to running, idle, stale, waiting, etc."""
```

The adapter must not choose the model or provider dynamically. It only interprets an explicitly supplied command and normalizes telemetry from that command.

## Shared Data Shapes

### AgentInvocation

```python
@dataclass
class AgentInvocation:
    run: Path
    round_id: str
    phase: str
    actor_identity: str
    player_id: str
    prompt_path: Path
    raw_output_path: Path
    command: list[str]
    cwd: Path
    approved: bool
    idle_timeout_seconds: int
    stale_timeout_seconds: int
```

The process is started only after the existing `invocation-ready` checks pass. `invoke-agent` still allocates a session before fallible readiness checks so missing executables, invalid paths, and policy failures can be recorded as durable failed sessions.

### PlayerCapabilities

```python
@dataclass
class PlayerCapabilities:
    player_id: str
    executable: bool
    supports_json_events: bool
    supports_resume: bool
    supports_cancel: bool
    prompt_transports: list[str]
    output_modes: list[str]
```

Capabilities are reported facts, not permissions. Permission still comes from `invocation-ready` and run policy.

### CommandSpec

`PlayerAdapter.build_command()` returns the command that will actually be supervised:

```python
@dataclass
class CommandSpec:
    argv: list[str]
    cwd: Path
    prompt_transport: str
    output_mode: str
    env_allowlist: list[str]
    stdin_path: Path
    stdout_path: Path
    stderr_path: Path
```

`prompt_transport` v1 supports only `stdin`. `output_mode` v1 supports `raw_stdout` and adapter-specific modes such as `codex_last_message` only when the explicit command asks for them.

### Persisted JSON Shapes

Every persisted JSON file in the session directory must include a top-level `schema_version`.

Minimal `invocation.json`:

```json
{
  "schema_version": "cross-agent-consensus-invocation-1",
  "run_id": "example-consensus-001",
  "round_id": "round-001",
  "phase": "reviewer",
  "actor_identity": "reviewer-codex",
  "player_id": "generic-cli",
  "session_id": "session-001",
  "prompt_source_path": "rounds/round-001/prompts/reviewers/reviewer-codex.md",
  "prompt_sha256": "...",
  "raw_output_path": "rounds/round-001/raw/reviewers/reviewer-codex.out",
  "idle_timeout_seconds": 300,
  "stale_timeout_seconds": 1200,
  "approved": true
}
```

Minimal `command.json`:

```json
{
  "schema_version": "cross-agent-consensus-command-1",
  "argv": ["codex", "exec", "--json", "-"],
  "cwd": "/repo",
  "prompt_transport": "stdin",
  "output_mode": "raw_stdout",
  "env_allowlist": [],
  "env_names_recorded_only": true,
  "executable_probe": {"executable": true, "path": "/usr/local/bin/codex"}
}
```

Minimal `state.json`:

```json
{
  "schema_version": "cross-agent-consensus-state-1",
  "state": "running",
  "pid": 12345,
  "process_group_id": 12345,
  "host": "worker-host",
  "process_start_time": "2026-05-29T13:00:00Z",
  "started_at": "2026-05-29T13:00:00Z",
  "last_agent_activity_at": "2026-05-29T13:00:15Z",
  "last_monitor_heartbeat_at": "2026-05-29T13:00:30Z",
  "idle_seconds": 15,
  "stdout_path": "stdout.raw",
  "stderr_path": "stderr.raw",
  "final_output_path_or_null": null
}
```

Minimal `exit.json`:

```json
{
  "schema_version": "cross-agent-consensus-exit-1",
  "final_state": "completed",
  "exit_code_or_null": 0,
  "signal_or_null": null,
  "duration_seconds": 42,
  "completed_at": "2026-05-29T13:00:42Z",
  "failure_reason_or_null": null
}
```

## Secrets And Environment Policy

The invocation layer writes durable audit files. It must therefore treat command metadata as potentially sensitive.

Rules:

- argv must not contain secrets;
- `invoke-agent` should reject obvious secret-bearing argv tokens before process start, including `--*-key=...`, `--*-token=...`, `--password=...`, `Authorization:` headers, and bearer-token patterns;
- credentials should flow only through environment variables explicitly named in `CommandSpec.env_allowlist`;
- `command.json` records environment variable names only, never values;
- the default environment policy is deny-by-default for additional variables beyond the parent process environment that the CLI already inherits;
- redaction is not a substitute for rejection when a secret is visible in argv.

If a player cannot be invoked without passing secrets in argv, it is not eligible for direct `invoke-agent` in v1. Use the `manual` player or a wrapper command that obtains credentials from a safe local source without exposing them in the recorded argv.

### AgentEvent

The Orchestrator consumes a normalized JSONL stream. Required fields:

```json
{
  "schema_version": "cross-agent-consensus-agent-event-1",
  "ts": "2026-05-29T13:00:00Z",
  "run_id": "example-consensus-001",
  "round_id": "round-001",
  "actor_identity": "reviewer-codex",
  "player_id": "codex-cli",
  "session_id": "session-001",
  "type": "started"
}
```

Recommended event types:

- `prepared`: session directory and command metadata written.
- `started`: process created.
- `stdout`: stdout chunk or byte count recorded.
- `stderr`: stderr chunk or byte count recorded.
- `agent_event`: adapter-normalized native event, such as tool call, thought delta, or final message.
- `waiting_for_input`: adapter detected an input prompt or blocked permission state.
- `idle`: process alive but no activity for the idle threshold.
- `stale`: process alive but no activity for the stale threshold.
- `heartbeat`: periodic monitor update.
- `completed`: process exited with code `0`.
- `failed`: process exited non-zero or adapter reported unrecoverable error.
- `cancel_requested`: Orchestrator requested cancellation.
- `cancelled`: process was terminated by the invocation layer.

The generic adapter can emit only process and stream events. Specialized adapters may add richer `agent_event` entries.

### AgentState

`state.json` should expose one of:

```text
prepared
starting
running
idle
stale
waiting_for_input
completed
failed
cancel_requested
cancelled
unknown
```

State classification should use conservative rules:

- `running`: process is alive and recent stdout, stderr, or adapter event exists.
- `idle`: process is alive and no activity occurred for `idle_timeout_seconds`.
- `stale`: process is alive and no activity occurred for `stale_timeout_seconds`.
- `waiting_for_input`: adapter-specific detection of a prompt, permission request, login request, or input wait.
- `completed`: process exited with `0`.
- `failed`: process exited non-zero, command could not start, or adapter failed before invocation.
- `cancelled`: cancellation was requested and the process is no longer alive.

`waiting_for_input` should override `idle` and `stale` when the adapter can detect it reliably.

## Run-Folder Layout

Add invocation telemetry under the active round:

```text
runs/<run_id>/
  rounds/
    round-001/
      agents/
        <actor_identity>/
          session-001/
            invocation.json
            command.json
            prompt.md
            events.jsonl
            agent.log
            stdout.raw
            stderr.raw
            state.json
            exit.json
            final-output.md
```

File purposes:

- `invocation.json`: normalized invocation request, including actor, phase, player, prompt path, raw-output path, timeouts, and approval mode.
- `command.json`: vetted argv, cwd, environment allowlist, prompt transport, output mode, and executable probe result.
- `prompt.md`: mandatory copy of the exact prompt used for this invocation.
- `events.jsonl`: append-only normalized event stream.
- `agent.log`: append-only detailed native message/event log for supervised sessions.
- `stdout.raw`: raw stdout bytes from the process.
- `stderr.raw`: raw stderr bytes from the process.
- `state.json`: latest state snapshot for polling.
- `exit.json`: exit code, signal, duration, and final state.
- `final-output.md`: adapter-extracted final answer when available.

`invocation.json` also records `prompt_source_path` and `prompt_sha256` so the copied prompt can be linked back to the canonical role prompt.

Write rules:

- `events.jsonl` is append-only.
- `state.json` and `exit.json` are written with a temp file plus atomic rename.
- The active session monitor is the only writer for `state.json` and `exit.json`.
- Other commands, including `agent-cancel`, request state transitions by appending events; the active monitor performs the snapshot update.
- If the active monitor has already exited, a follow-up command may write `exit.json` only after taking an advisory session lock and recording that recovery action in `events.jsonl`.

The existing prompt directories remain canonical for role prompts:

```text
rounds/round-001/prompts/...
```

The existing raw directories remain canonical for protocol capture:

```text
rounds/round-001/raw/...
```

The agent session directory is operational telemetry. `consensus capture` still copies or references final raw output into protocol evidence locations.

## CLI Surface

### `consensus invoke-agent`

Starts one explicit player invocation after readiness checks.

```bash
consensus invoke-agent \
  --run runs/example-consensus-001 \
  --round round-001 \
  --phase reviewer \
  --actor reviewer-codex \
  --player codex-cli \
  --prompt runs/example-consensus-001/rounds/round-001/prompts/reviewers/reviewer-codex.md \
  --raw-output runs/example-consensus-001/rounds/round-001/raw/reviewers/reviewer-codex.out \
  --idle-timeout-seconds 300 \
  --stale-timeout-seconds 900 \
  --approved \
  --command -- codex exec --json --sandbox read-only --skip-git-repo-check -C /repo -
```

For Claude, prefer stream JSON when the Claude CLI and credentials are available:

```bash
consensus invoke-agent \
  --run runs/example-consensus-001 \
  --round round-001 \
  --phase reviewer \
  --actor reviewer-claude \
  --player claude-cli \
  --prompt runs/example-consensus-001/rounds/round-001/prompts/reviewers/reviewer-claude.md \
  --raw-output runs/example-consensus-001/rounds/round-001/raw/reviewers/reviewer-claude.out \
  --idle-timeout-seconds 300 \
  --stale-timeout-seconds 900 \
  --approved \
  --command -- claude -p --verbose --output-format=stream-json --include-partial-messages
```

Structured-output commands are required under specialized adapters because they preserve native runtime events as normalized `agent_event` entries and detailed `agent.log` entries. `codex-cli` requires `--json`; `claude-cli` requires `-p/--print --verbose --output-format stream-json`.

Behavior:

1. Allocate the next session directory for the actor.
2. Write `invocation.json` with the requested actor, player, prompt, raw-output destination, and timeouts.
3. Probe and vet the command, write `command.json`, and copy the exact prompt into `prompt.md`.
4. Run the same checks as `consensus invocation-ready`.
5. If readiness fails, append a `failed` event, write `state.json` and `exit.json`, and do not start the process.
6. Start the process with the configured prompt transport only after readiness passes.
7. Stream stdout and stderr into `stdout.raw`, `stderr.raw`, `events.jsonl`, and `agent.log`.
8. Update `state.json` periodically and after every meaningful event.
9. Write `exit.json` when the process exits.

V1 can run in the foreground. A later version can add `--background` when process management semantics are fully specified.

Operator guidance: do not replace `invoke-agent` with a raw shell command plus `consensus capture` when telemetry is required. Raw shell invocation followed by capture is the manual/direct-capture lane and has no live state, heartbeat, idle/stale detection, cancellation, or event tail.

### `consensus agent-status`

Reads `state.json`, `exit.json`, and the tail of `events.jsonl`.

```bash
consensus agent-status \
  --run runs/example-consensus-001 \
  --actor reviewer-codex \
  --round round-001 \
  --json
```

Human output should show:

- actor;
- player;
- session id;
- current state;
- pid if alive;
- started time;
- last activity time;
- idle seconds;
- exit code if any;
- raw stdout and stderr paths;
- final output path if any.

JSON output should include the full `state.json` fields plus `session_path`, `exit` when present, and an optional `event_tail` array. Its top-level `schema_version` is `cross-agent-consensus-agent-status-1`.

### `consensus agent-watch`

Tails normalized events and state changes.

```bash
consensus agent-watch \
  --run runs/example-consensus-001 \
  --actor reviewer-codex \
  --round round-001
```

This is mostly operator-facing. Orchestrators should prefer `agent-status --json`.

### `consensus agent-cancel`

Requests cancellation for a live session.

```bash
consensus agent-cancel \
  --run runs/example-consensus-001 \
  --actor reviewer-codex \
  --round round-001 \
  --reason "stale for 30 minutes"
```

Behavior:

1. Append `cancel_requested`.
2. Verify `host`, `pid`, process start time, and process group before signaling.
3. Refuse to signal and append `failed` with reason `pid_unverifiable` when the process cannot be verified, such as a different host or reused pid.
4. Send a graceful signal when verification passes.
5. Wait a short grace period.
6. Escalate to process termination only for the invoked process tree.
7. Append `cancelled` or `failed`; the active monitor updates `state.json` and `exit.json`.

Cancellation is an invocation-layer event. It does not by itself create an AbortRecord or terminal protocol state. The Orchestrator decides whether to retry, use manual handoff, escalate, or abort.

### `consensus players probe`

V1 can support a minimal explicit probe command without config:

```bash
consensus players probe --player generic-cli --command -- claude --version
```

The command reports executable availability and adapter capabilities. It must not write protocol state.

### `consensus capture` Boundary

`consensus capture` remains the protocol-evidence command. It should not be overloaded into a process runner.

Required help/documentation wording:

```text
capture records output that already exists. It does not start, supervise, or monitor an agent process and it does not create rounds/<round>/agents/<actor>/session-* telemetry. Use invoke-agent when live status, heartbeats, cancellation, stdout/stderr stream files, events.jsonl, state.json, exit.json, or final-output extraction are required.
```

This prevents a common mistaken workflow:

```bash
# Valid protocol evidence, but no invocation telemetry:
codex exec --sandbox read-only --skip-git-repo-check -C /repo - < "$PROMPT" > "$RAW"
consensus capture \
  --phase reviewer \
  --run "$RUN" \
  --actor reviewer-codex \
  --review-batch "$REVIEW_BATCH" \
  --artifact-version "$ARTIFACT_VERSION" \
  --source-file "$RAW"
```

The monitored equivalent is:

```bash
consensus invoke-agent \
  --run "$RUN" \
  --round 1 \
  --phase reviewer \
  --actor reviewer-codex \
  --player codex-cli \
  --prompt "$PROMPT" \
  --raw-output "$RAW" \
  --approved \
  --command -- codex exec --json --sandbox read-only --skip-git-repo-check -C /repo -

consensus capture \
  --phase reviewer \
  --run "$RUN" \
  --actor reviewer-codex \
  --review-batch "$REVIEW_BATCH" \
  --artifact-version "$ARTIFACT_VERSION" \
  --source-file "$RAW"
```

## Prompt Transport

V1 should support only stdin transport:

```text
prompt.md -> process stdin
```

This keeps `generic-cli` simple and works for many CLIs:

- `codex exec --json -`
- `claude -p --verbose --output-format stream-json`
- other CLIs that accept stdin or a prompt argument wrapper

Additional transports can be added later:

- prompt as argv argument;
- prompt path as command argument;
- stream-json input;
- bidirectional interactive stdin.

Those modes need separate escaping and audit rules, so they are out of v1 scope.

## Adapters

### `generic-cli`

Required first adapter.

Responsibilities:

- verify the executable exists;
- run the vetted argv supplied after `--command --`;
- write prompt to stdin;
- copy stdout and stderr to raw files;
- emit lifecycle, stdout, stderr, idle, stale, completed, failed, and cancelled events;
- classify state using process liveness and last activity timestamp;
- write `final-output.md` as an exact copy of `stdout.raw` when the process exits successfully.

This adapter is enough for any command that can read a prompt from stdin and write a final answer to stdout.

V1 does not define generic stdout filtering. If a generic command emits progress logs or mixed structured output on stdout, the Orchestrator must choose the correct raw payload before calling `consensus capture`, or use a specialized adapter that can extract the final answer safely.

### `codex-cli`

Specialized adapter for explicit Codex CLI invocations.

Required v1 behavior:

- support explicit commands such as `codex exec --json --sandbox read-only --skip-git-repo-check -`;
- parse JSONL events into normalized `agent_event` entries;
- classify command execution events as tool calls/results;
- detect the final assistant message and write `final-output.md`;
- detect permission or login failures from structured events or known stderr text;
- preserve raw stdout in `stdout.raw` even when normalized events are redacted or compacted.

The adapter must still use an explicit command. It must not choose a model or profile unless the command already includes it.

### `claude-cli`

Specialized adapter for explicit Claude CLI invocations.

Required v1 behavior:

- support explicit commands such as `claude -p --verbose --output-format=stream-json --include-partial-messages`;
- parse stream-json into normalized `agent_event` entries;
- detect assistant message, result/final, and tool-use style events when present;
- detect `waiting_for_input` for permission, auth, or workspace trust prompts;
- extract the final text response from JSON or text output.

The adapter must not infer model aliases or alter Claude settings unless the explicit command includes those choices.

### `deepseek-cli`

Optional placeholder adapter.

Until a specific local CLI contract is known, DeepSeek should use `generic-cli`. A specialized adapter can be added when the CLI exposes stable structured output, resume ids, or status events.

### `manual`

Manual player is not a process runner. It can reserve a session directory and write a handoff packet:

```text
manual-command.md
manual-prompt.md
```

The Orchestrator or human operator then runs the agent externally and uses `consensus capture` to preserve output. This keeps the same folder shape for unavailable or unauthenticated runtimes.

Manual sessions start in `prepared` state. They transition to `completed` only after the operator captures output and links that capture back to the manual session. Session numbering is always `max(existing session-NNN) + 1` regardless of whether previous sessions completed, failed, or remained manual handoffs.

## Relationship To Protocol Evidence

Agent telemetry is not a replacement for protocol records.

For reviewer output:

1. `invoke-agent` runs the player and writes telemetry.
2. The process stdout or extracted final answer is preserved under the session directory.
3. `consensus capture --phase reviewer` copies the selected raw output into `rounds/round-001/raw/reviewers/<actor>.out`.
4. `consensus capture` creates the `RawReviewerOutput` wrapper in `rounds/round-001/reviews/<actor>.md`.
5. The Orchestrator normalizes findings manually or with a later deterministic helper.

For validator output:

1. `invoke-agent` or a local command writes telemetry.
2. `consensus capture --phase validator` creates `ValidationEvidence`.

For author output:

1. `invoke-agent` captures author process telemetry.
2. The produced artifact is recorded as an `ArtifactVersion`.
3. If the author revises the artifact after findings, the Orchestrator creates a new Artifact Version and Author Responses.

The invocation layer can reference protocol paths, but protocol validators should not require agent telemetry to exist. Manual CAC runs must remain valid.

However, when telemetry is absent, final reporting should avoid implying that monitored execution occurred. If a run has reviewer or validator raw outputs but no matching `rounds/<round>/agents/<actor>/session-*` directory, the Orchestrator should record one of:

- direct CLI/manual capture was intentionally used, so telemetry is not expected;
- monitored invocation was expected but omitted, so the missing telemetry is an operational gap for future runs.

## Idle And Stale Detection

Use two thresholds:

```text
idle_timeout_seconds: process is quiet but not necessarily broken
stale_timeout_seconds: process is probably stuck or blocked
```

Default values should be conservative:

```text
idle_timeout_seconds = 300
stale_timeout_seconds = 1200
heartbeat_interval_seconds = 30
cancel_grace_seconds = 10
```

Activity sources:

- stdout bytes;
- stderr bytes;
- adapter-normalized events;
- process start;
- final output extraction.

Monitor heartbeats are not agent activity. The monitor records them separately as `last_monitor_heartbeat_at`; idle and stale classification uses `last_agent_activity_at`.

The monitor should not kill stale processes automatically in v1. It should record `stale` and let the Orchestrator decide whether to keep waiting, cancel, retry, or switch to manual handoff.

## Failure Handling

Common failure cases:

- command executable missing;
- command starts but exits non-zero;
- command writes no output;
- prompt transport fails;
- output file cannot be written;
- adapter parser fails;
- process becomes stale;
- cancellation fails;
- authentication or permission prompt blocks execution.

Failure records belong in invocation telemetry. Protocol-level consequences are separate:

- retry the same actor with a new session;
- switch to a different explicit player;
- use manual handoff;
- create an EscalationRecord;
- abort the CAC run if no auditable path remains.

## Integration With Future Config

This feature should expose stable CLI arguments first:

```bash
--actor reviewer-codex
--player codex-cli
--command -- codex exec --json -
```

When config lands, it can fill those same fields:

```yaml
players:
  codex-default:
    adapter: codex-cli
    command:
      - codex
      - exec
      - --json
      - "-"
```

Config integration rules:

- config may supply defaults;
- CLI flags override config;
- effective invocation must still be written to `invocation.json` and vetted command metadata must be written to `command.json`;
- `invocation-ready` still gates execution;
- persistent config must not enable unattended invocation by itself;
- player registry lookup must not change protocol participants.

The first implementation should not wait for config. It should use explicit commands and leave a clean attachment point for config later.

## Implementation Plan

1. Add invocation data classes to `skills/cross-agent-consensus/scripts/cac_tool.py`.
2. Add `PlayerAdapter`, `GenericCliPlayer`, and a small adapter registry containing only built-ins.
3. Add session path allocation under `rounds/<round>/agents/<actor>/session-NNN/`.
4. Add JSON writing helpers for `invocation.json`, `command.json`, `state.json`, and `exit.json`.
5. Add append-only `events.jsonl` writer.
6. Implement foreground `invoke-agent` for `generic-cli` with stdin prompt transport.
7. Implement process monitoring with non-blocking stdout/stderr readers, heartbeat updates, and separate agent-activity timestamps.
8. Implement `agent-status --json` and human output.
9. Implement `agent-watch` as a simple event tail.
10. Implement `agent-cancel` for live process trees where pid metadata is present.
11. Add tests for session allocation, JSON schemas, event schema, secret rejection, state transitions, stale detection, cancellation metadata, and `invocation-ready` integration.
12. Add `codex-cli` and `claude-cli` adapters for explicit structured-output commands.

## Validation Plan

Unit tests:

- `generic-cli` writes all required session files.
- direct CLI output followed by `consensus capture` creates protocol evidence but no `rounds/*/agents/*` session; `agent-status` explains that this is expected for direct-capture runs and suggests `invoke-agent` for telemetry.
- `events.jsonl` contains valid JSON objects with required fields.
- `state.json` transitions from `prepared` to `running` to `completed`.
- quiet long-running process becomes `idle` then `stale` without automatic cancellation.
- non-zero exit becomes `failed`.
- cancellation writes `cancel_requested` and `cancelled`.
- final output extraction preserves stdout.
- invalid actor, prompt path, raw-output path, secret-bearing argv, or command writes a failed session before process start.
- `codex-cli` parses `codex exec --json` JSONL into normalized runtime events and final output.
- `claude-cli` parses `claude -p --verbose --output-format=stream-json` into normalized runtime events and final output.
- `codex-cli` invoked without `--json` is rejected before process start and records a failed session.
- `claude-cli` invoked without `-p/--print --verbose --output-format stream-json` is rejected before process start and records a failed session.
- secret-bearing argv is absent from every file in a rejected session.
- selected-round prompt and raw-output paths cannot point at another round.

Integration tests:

- invoke a local shell command that echoes a reviewer response;
- capture that output with `consensus capture --phase reviewer`;
- validate records and reviewer isolation;
- run `agent-status --json` while a sleep command is active;
- verify manual player handoff writes prompt and command packets without starting a process.
- on the work VM, run real `claude-cli` stream-json and `codex-cli --json` smokes through `consensus invoke-agent` when credentials are available, otherwise record the blocker.

Dogfood run:

- Use this design as a document-consensus artifact.
- Review with independent Claude and Codex reviewers.
- Normalize findings and revise if blockers are found.
- Terminate only after required document validators pass.

## Open Questions

- Should `agent-status` scan process liveness live, or only report the last persisted monitor state?
- Should `stderr.raw` be linked from protocol capture metadata for all players, or only when non-empty?
- Should specialized adapters live in one `cac_tool.py` file initially, or split into a package once the script grows?

Resolved v1 decision: `invoke-agent` does not provide `--capture-on-success`. The Orchestrator calls `consensus capture` explicitly so protocol evidence creation is not a side effect of process invocation.

## Related CAC Run

- Run: `runs/design-cac-agent-player-interface-and-invocation-telemetry-consensus-001`
- Author: `author-codex`
- Reviewers: `reviewer-claude`, `reviewer-codex`
- Artifact version: `v1`
