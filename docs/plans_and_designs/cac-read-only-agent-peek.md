# CAC Read-Only Agent Peek

## Summary

Add a small operator-facing peek command for long-running external reviewer sessions.

The command reads existing `invoke-agent` telemetry and prints a compact live trail:

```text
<reviewer> did ...
<reviewer> now ...
```

The point is not only to know whether the reviewer is alive. The operator also wants a small, bounded view into how the reviewer is thinking: files inspected, tools used, and short partial reasoning snippets.

Locked decision: this feature is read-only at runtime. It must not create protocol records, mutate telemetry files, append observation artifacts, or feed anything back to the reviewer process.

## Current State Analysis

The CAC invocation layer already writes the telemetry needed for an operator peek:

- `state.json` records persisted state, pid, timestamps, idle seconds, and monitor heartbeat time.
- `events.jsonl` records lifecycle events, heartbeats, stdout/stderr activity, normalized runtime events, and waiting-for-input signals.
- `agent.log` stores adapter-normalized CLI stream entries for structured players such as Codex CLI and Claude CLI.
- `stdout.raw` and `stderr.raw` provide byte growth and raw stream fallback when needed.
- `final-output.md` exists only after a completed session and remains the authoritative final reviewer output.
- `agent-status --json` exposes current session state and an event tail.
- `agent-watch` prints normalized agent events.

This is enough to inspect reviewer activity without creating another artifact surface.

## Problem

Long reviewer runs are sometimes opaque. The Orchestrator may not care about a full transcript while the reviewer is working, but still needs a quick answer to:

- is the reviewer alive or stuck;
- did it do anything recently;
- what concrete thing did it just inspect or run;
- what broad or partial reasoning does it appear to be doing now;
- whether the reviewer is drifting out of expected scope.

The existing `agent-status` output is mechanically useful, but it is too low-level and too status-oriented for this operator workflow.

## Non-Goals

- No new run files or observation trail files.
- No protocol evidence from peek output.
- No `RawReviewerOutput`, `ValidationEvidence`, `CanonicalFinding`, or other CAC record creation.
- No mutation of `state.json`, `events.jsonl`, `agent.log`, `stdout.raw`, `stderr.raw`, or `final-output.md`.
- No feedback to the reviewer process.
- No automatic cancellation, retry, escalation, or prompt injection.
- No hidden reviewer, judge model, or semantic quality reviewer.
- No full raw transcript dump in v1.
- No `--all-active` scan in v1; v1 peeks one explicit actor/session at a time.
- No `--json` API in v1; v1 is a human operator command.

## Design

Add a read-only command:

```bash
consensus agent-peek \
  --run runs/<run_id> \
  --round 1 \
  --actor claude \
  --follow
```

Supported v1 flags:

```text
--actor <identity>          required; reviewer/agent identity to inspect
--session <session-id>      optional; defaults to latest session for actor
--round <round>             default round-1; accepts agent-status round formats
--tail <count>              recent telemetry entries to inspect; default from config
--snippet-chars <count>     max chars in one displayed content snippet; default from config
--follow                    repeat until selected session becomes terminal or monitor-stale
--interval-seconds <float>  polling interval; default from config
```

Deferred flags:

```text
--all-active                defer until session discovery semantics are needed
--json                      defer until a stable schema is worth supporting
```

The command computes the display in memory from existing telemetry:

- select one session from `--actor`, `--round`, and optional `--session`;
- read `state.json` and `exit.json`;
- derive freshness from `last_monitor_heartbeat_at`;
- read a bounded tail of `events.jsonl`;
- read a bounded tail of `agent.log` for content snippets when useful;
- optionally compare stdout/stderr file sizes across follow polls using in-memory cursors only;
- derive short `did` and `now` phrases;
- print the current view;
- write nothing.

Example output:

```text
[12:03] claude running did: inspected docs/plans_and_designs/cac-read-only-agent-peek.md
[12:03] claude running now: writing concern about config schema defaults idle=6s heartbeat=2s

[12:06] codex running did: ran rg "event_tail|agent_status" skills/cross-agent-consensus
[12:06] codex running now: checking stale heartbeat handling in process_monitor.py idle=4s heartbeat=1s

[12:09] claude monitor_stale did: produced reviewer text  now: monitor heartbeat stale for 45s
```

When activity cannot be inferred, the command must say so:

```text
[12:12] claude running did: activity observed now: details unknown idle=22s heartbeat=3s
```

## Session Selection

V1 requires `--actor`.

Default session selection:

1. Resolve `--round` using the same accepted formats as `agent-status` (`1`, `001`, `round-001`).
2. If `--session` is present, select exactly that session under `rounds/<round>/agents/<actor>/`.
3. If `--session` is absent, use the latest session for the actor.
4. If no session exists, exit with code `2` and print a clear read-only status error.

`--all-active` is intentionally deferred. When it is added later, it must scan `rounds/<round>/agents/*/session-*`, read `invocation.json` for authoritative actor identity, read `state.json` and `exit.json`, and define active/terminal filtering before implementation.

## State And Stuck Detection

Peek must not blindly trust persisted `state.json` as current truth.

Derived state fields:

```text
persisted_state              state read from state.json
monitor_age_seconds          now - last_monitor_heartbeat_at
monitor_fresh                monitor_age_seconds <= monitor_stale_seconds
terminal                     persisted_state in {completed, failed, cancelled} or exit.json exists
derived_state                terminal state, monitor_stale, or persisted_state
derived_idle_seconds         now - last_agent_activity_at when available
```

Rules:

- `completed`, `failed`, and `cancelled` are terminal.
- Presence of `exit.json` is terminal even if `state.json` is stale.
- Non-terminal sessions with stale monitor heartbeat are reported as `monitor_stale`.
- `state.json.state` is the canonical runtime state only while monitor freshness is acceptable.
- Activity tails fill `did` and `now`; they do not override terminal or monitor-stale state.
- `--follow` stops when the session is terminal or becomes `monitor_stale`.

Default `monitor_stale_seconds` is configurable and defaults to `30`.

## Activity Inference Rules

The first version uses deterministic heuristics only.

Ordering:

- `now` is derived from the most recent meaningful non-terminal activity event.
- `did` is derived from the most recent completed activity before `now`.
- If the same event could produce both phrases, prefer `now` and choose the previous completed event for `did`.
- Heartbeats alone do not count as progress.

Suggested mappings:

| Telemetry signal | Derived phrase |
| --- | --- |
| recent tool start | `now: running <short tool/command snippet>` |
| recent tool result | `did: ran <short tool/command snippet>` |
| recent file/read/search evidence | `did: inspected <short path or search snippet>` |
| recent reviewer text delta | `now: <short partial reasoning snippet>` |
| recent waiting-for-input | `now: waiting for input` |
| stdout/stderr byte growth only | `now: process is producing output` |
| terminal completed | `now: completed` |
| terminal failed | `now: failed` |
| monitor stale | `now: monitor heartbeat stale for <age>` |

Content snippets are intentionally allowed in v1. Examples:

```text
did: inspected validation.py
now: writing concern about config schema
did: ran rg "latest_agent_session"
now: checking whether stale state can report running forever
```

Snippet rules:

- snippets are one line;
- control characters are stripped;
- whitespace is collapsed;
- snippets are truncated to `snippet_chars`;
- generic secret redaction still applies for obvious token/password patterns;
- snippets are operator display only and are not durable CAC evidence.

## Operator Isolation

Peek output is for the Orchestrator/operator only. It may expose partial reviewer reasoning, but it must remain outside the CAC evidence lifecycle.

Rules:

- do not feed peek output into reviewer prompts;
- do not feed peek output into normalization, author responses, re-review prompts, validation, or terminal reports;
- do not show one reviewer's peek output to another reviewer;
- do not treat peek output as a source raw finding;
- do not persist peek output unless the human operator separately captures terminal scrollback outside CAC protocol;
- final reviewer evidence remains `RawReviewerOutput` captured from the completed reviewer stream.

This is the key boundary: content peek is allowed for situational awareness, but it is not protocol evidence.

## Config

Config support is in v1 and is locked under `invocation.peek`.

```yaml
invocation:
  peek:
    interval_seconds: 180
    tail: 80
    snippet_chars: 160
    monitor_stale_seconds: 30
```

Schema rules:

- `interval_seconds`: number, `> 0`, default `180`.
- `tail`: integer, `1..1000`, default `80`.
- `snippet_chars`: integer, `40..500`, default `160`.
- `monitor_stale_seconds`: number, `> 0`, default `30`.
- Unknown keys under `invocation.peek` are config errors in strict mode.
- Unknown keys under `invocation` remain config errors in strict mode.

Resolution rules:

- `agent-peek` resolves layered config the same way `config show` does.
- CLI flags override config defaults.
- Persistent config may set peek defaults because peeking does not invoke agents or write run artifacts.
- Config resolution for `agent-peek` must not create a `ConfigResolution` protocol record.

## Integration Points

- Add parser entry next to `agent-status` and `agent-watch`.
- Implement in `cross_agent_consensus/invocation/peek.py`.
- Reuse `latest_agent_session`, `agent_session_paths`, `read_json_file`, and `event_tail`.
- Add a bounded text-tail helper for `agent.log` if no suitable helper exists.
- Add config validation for `invocation.peek`.
- Add a lightweight non-init config resolver path for `agent-peek`.
- Keep display code separate from inference code so tests can assert derived activity without terminal formatting noise.

## Validation Plan

Unit tests should cover:

- config accepts valid `invocation.peek` defaults and rejects unknown keys/range violations;
- `agent-peek` resolves config outside `init` and CLI flags override config;
- missing session exits `2` without writing files;
- explicit actor selects latest session by default;
- explicit `--session` selects the requested session;
- completed, failed, cancelled, running, idle, and monitor-stale formatting;
- stale monitor is derived from `last_monitor_heartbeat_at`, not only `state.json.state`;
- `--follow` stops on terminal or monitor-stale sessions;
- heartbeat-only tails do not fake progress;
- content snippets from message/tool telemetry are allowed, bounded, one-line, and truncated;
- obvious secret-like snippets are redacted;
- byte-growth comparison uses in-memory cursors and writes no cache file;
- before/after hash or mtime sentinels prove no mutation of `state.json`, `events.jsonl`, `agent.log`, `stdout.raw`, `stderr.raw`, or `final-output.md`;
- no `RawReviewerOutput`, `ValidationEvidence`, `CanonicalFinding`, or other protocol record is created by peek.

No protocol validation changes are required because this feature produces no protocol records.

## Deferred Work

- `--all-active` for scanning all active sessions in a round.
- `--json` once consumers need a stable `cross-agent-consensus-agent-peek-1` schema.
- Raw transcript tail mode for debugging, if ever needed, behind an explicit flag.
- Same-session resume or interaction is out of scope.

## Locked Decisions

- Command name is `agent-peek`.
- Runtime behavior is read-only.
- Content-bearing operator peek is allowed and is the point of v1.
- No new observation files.
- No state or telemetry mutation.
- No protocol evidence.
- No feedback to reviewer.
- No semantic judge in v1.
- V1 requires explicit `--actor`.
- V1 defers `--all-active`.
- V1 defers `--json`.
- Config lives under `invocation.peek`.
- Default interval is 180 seconds.
- Output should be a small `did` / `now` review trail.
