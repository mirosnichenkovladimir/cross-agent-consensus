"""Telemetry file writers and readers for CAC agent sessions."""

from __future__ import annotations

import collections
import json
import socket
import time
from pathlib import Path
from typing import Any

from cross_agent_consensus.io import (
    append_jsonl,
    atomic_write_json,
    read_json_file,
    sha256_file,
    utc_now,
)
from cross_agent_consensus.models import AgentInvocation, AgentSessionPaths, CommandSpec

from .session_paths import path_for_json, session_relative

AGENT_INVOCATION_SCHEMA = "cross-agent-consensus-invocation-3"
AGENT_COMMAND_SCHEMA = "cross-agent-consensus-command-1"
AGENT_STATE_SCHEMA = "cross-agent-consensus-state-1"
AGENT_EXIT_SCHEMA = "cross-agent-consensus-exit-1"
AGENT_EVENT_SCHEMA = "cross-agent-consensus-agent-event-2"
AGENT_LOG_SCHEMA = "cross-agent-consensus-agent-log-2"
AGENT_STATUS_SCHEMA = "cross-agent-consensus-agent-status-1"


def agent_event(invocation: AgentInvocation, event_type: str, **extra: Any) -> dict[str, Any]:
    event = {
        "schema_version": AGENT_EVENT_SCHEMA,
        "ts": utc_now(),
        "run_id": invocation.run.name,
        "round_id": invocation.round_id,
        "participant_identity": invocation.participant_identity,
        "participant_profile_id": invocation.participant_profile_id,
        "execution_profile_id": invocation.execution_profile_id,
        "player_id": invocation.player_id,
        "session_id": invocation.session_id,
        "type": event_type,
    }
    event.update(extra)
    return event


def append_agent_event(paths: AgentSessionPaths, invocation: AgentInvocation, event_type: str, **extra: Any) -> None:
    append_jsonl(paths.events, agent_event(invocation, event_type, **extra))


def append_agent_log_entry(
    paths: AgentSessionPaths,
    invocation: AgentInvocation,
    *,
    stream: str,
    native_type: str,
    normalized_type: str,
    native_event: dict[str, Any] | None = None,
    text: str | None = None,
) -> None:
    entry: dict[str, Any] = {
        "schema_version": AGENT_LOG_SCHEMA,
        "ts": utc_now(),
        "run_id": invocation.run.name,
        "round_id": invocation.round_id,
        "participant_identity": invocation.participant_identity,
        "participant_profile_id": invocation.participant_profile_id,
        "execution_profile_id": invocation.execution_profile_id,
        "player_id": invocation.player_id,
        "session_id": invocation.session_id,
        "stream": stream,
        "native_type": native_type,
        "normalized_type": normalized_type,
    }
    if native_event is not None:
        entry["native_event"] = native_event
    if text is not None:
        entry["text"] = text
    append_jsonl(paths.agent_log, entry)


def append_agent_log_from_stream(
    paths: AgentSessionPaths,
    invocation: AgentInvocation,
    adapter: Any,
    stream_name: str,
    data: bytes,
    buffers: dict[str, str],
) -> None:
    text = buffers.get(stream_name, "") + data.decode("utf-8", errors="replace")
    parts = text.splitlines(keepends=True)
    if parts and not parts[-1].endswith(("\n", "\r")):
        buffers[stream_name] = parts.pop()
    else:
        buffers[stream_name] = ""
    for line in parts:
        append_agent_log_line(paths, invocation, adapter, stream_name, line)


def flush_agent_log_from_stream(
    paths: AgentSessionPaths,
    invocation: AgentInvocation,
    adapter: Any,
    buffers: dict[str, str],
) -> None:
    for stream_name, pending in list(buffers.items()):
        if pending:
            append_agent_log_line(paths, invocation, adapter, stream_name, pending)
            buffers[stream_name] = ""


def append_agent_log_line(
    paths: AgentSessionPaths,
    invocation: AgentInvocation,
    adapter: Any,
    stream_name: str,
    line: str,
) -> None:
    text = line.strip()
    if not text:
        return
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        append_agent_log_entry(
            paths,
            invocation,
            stream=stream_name,
            native_type=f"{stream_name}_text",
            normalized_type=stream_name,
            text=text,
        )
        return
    if not isinstance(payload, dict):
        append_agent_log_entry(
            paths,
            invocation,
            stream=stream_name,
            native_type="json_value",
            normalized_type="runtime",
            text=json.dumps(payload, sort_keys=True),
        )
        return
    if hasattr(adapter, "native_event_type") and hasattr(adapter, "normalized_event_type"):
        native_type = adapter.native_event_type(payload)
        normalized_type = adapter.normalized_event_type(payload, native_type)
    else:
        native_type = str(payload.get("type") or payload.get("event") or "json")
        normalized_type = "runtime"
    append_agent_log_entry(
        paths,
        invocation,
        stream=stream_name,
        native_type=native_type,
        normalized_type=normalized_type,
        native_event=payload,
    )


def event_type_seen(path: Path, event_type: str) -> bool:
    if not path.is_file():
        return False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            if json.loads(line).get("type") == event_type:
                return True
        except json.JSONDecodeError:
            continue
    return False


def write_invocation_json(paths: AgentSessionPaths, invocation: AgentInvocation) -> None:
    prompt_sha = sha256_file(invocation.prompt_path) if invocation.prompt_path.is_file() else None
    atomic_write_json(
        paths.invocation,
        {
            "schema_version": AGENT_INVOCATION_SCHEMA,
            "run_id": invocation.run.name,
            "round_id": invocation.round_id,
            "phase": invocation.phase,
            "participant_identity": invocation.participant_identity,
            "participant_profile_id": invocation.participant_profile_id,
            "execution_profile_id": invocation.execution_profile_id,
            "player_id": invocation.player_id,
            "effective_command": invocation.command,
            "session_id": invocation.session_id,
            "execution_attempt_id_or_null": invocation.execution_attempt_id,
            "retry_safety": invocation.retry_safety,
            "resume_provider_session_entry_id_or_null": (
                invocation.resume_provider_session_entry_id
            ),
            "provider_session_id_or_null": invocation.provider_session_id,
            "artifact_lineage_root_id_or_null": invocation.artifact_lineage_root_id,
            "continuation_definition_sha256_or_null": (
                invocation.continuation_definition_sha256
            ),
            "provider_session_definition_resolution_or_null": (
                invocation.provider_session_definition_resolution
            ),
            "provider_session_resume_reservation_id_or_null": (
                invocation.provider_session_resume_reservation_id
            ),
            "prompt_source_path": path_for_json(invocation.prompt_path, invocation.run),
            "prompt_sha256": prompt_sha,
            "raw_output_path": path_for_json(invocation.raw_output_path, invocation.run),
            "idle_timeout_seconds": invocation.idle_timeout_seconds,
            "stale_timeout_seconds": invocation.stale_timeout_seconds,
            "max_runtime_seconds_or_null": invocation.max_runtime_seconds,
            "rate_limit_circuit_breaker_or_null": (
                {
                    "max_consecutive_429_events": (
                        invocation.rate_limit_circuit_breaker.max_consecutive_429_events
                    ),
                    "max_cumulative_retry_delay_seconds": (
                        invocation.rate_limit_circuit_breaker.max_cumulative_retry_delay_seconds
                    ),
                }
                if invocation.rate_limit_circuit_breaker is not None
                else None
            ),
            "approved": invocation.approved,
        },
    )


def write_command_json(paths: AgentSessionPaths, command_spec: CommandSpec) -> None:
    atomic_write_json(
        paths.command,
        {
            "schema_version": AGENT_COMMAND_SCHEMA,
            "argv": command_spec.argv,
            "cwd": str(command_spec.cwd),
            "prompt_transport": command_spec.prompt_transport,
            "output_mode": command_spec.output_mode,
            "env_allowlist": command_spec.env_allowlist,
            "env_names_recorded_only": True,
            "executable_probe": command_spec.executable_probe,
            "stdin_path": session_relative(paths.prompt, paths.session),
            "stdout_path": session_relative(paths.stdout, paths.session),
            "stderr_path": session_relative(paths.stderr, paths.session),
            "agent_log_path": session_relative(paths.agent_log, paths.session),
        },
    )


def write_rejected_command_json(paths: AgentSessionPaths, reason: str) -> None:
    atomic_write_json(
        paths.command,
        {
            "schema_version": AGENT_COMMAND_SCHEMA,
            "argv": [],
            "argv_redacted": True,
            "cwd": None,
            "prompt_transport": None,
            "output_mode": None,
            "env_allowlist": [],
            "env_names_recorded_only": True,
            "executable_probe": {"executable": None, "path": None},
            "rejection_reason": reason,
            "agent_log_path": session_relative(paths.agent_log, paths.session),
        },
    )


def write_agent_state(
    paths: AgentSessionPaths,
    invocation: AgentInvocation,
    state: str,
    *,
    pid: int | None,
    process_group_id: int | None,
    started_at: str | None,
    process_start_time: str | None,
    last_agent_activity_at: str | None,
    last_monitor_heartbeat_at: str | None,
    idle_seconds: float,
    final_output_path_or_null: str | None = None,
    failure_reason_or_null: str | None = None,
    process_identity_or_null: dict[str, Any] | None = None,
) -> None:
    atomic_write_json(
        paths.state,
        {
            "schema_version": AGENT_STATE_SCHEMA,
            "state": state,
            "pid": pid,
            "process_group_id": process_group_id,
            "process_identity": process_identity_or_null,
            "host": socket.gethostname(),
            "process_start_time": process_start_time,
            "started_at": started_at,
            "last_agent_activity_at": last_agent_activity_at,
            "last_monitor_heartbeat_at": last_monitor_heartbeat_at,
            "idle_seconds": round(idle_seconds, 3),
            "stdout_path": session_relative(paths.stdout, paths.session),
            "stderr_path": session_relative(paths.stderr, paths.session),
            "agent_log_path": session_relative(paths.agent_log, paths.session),
            "final_output_path_or_null": final_output_path_or_null,
            "failure_reason_or_null": failure_reason_or_null,
        },
    )


def write_agent_exit(
    paths: AgentSessionPaths,
    final_state: str,
    *,
    exit_code_or_null: int | None,
    signal_or_null: int | None,
    started_monotonic: float | None,
    failure_reason_or_null: str | None,
    evidence_digests: dict[str, str | None] | None = None,
) -> None:
    duration = 0.0 if started_monotonic is None else max(0.0, time.monotonic() - started_monotonic)
    payload: dict[str, Any] = {
        "schema_version": AGENT_EXIT_SCHEMA,
        "final_state": final_state,
        "exit_code_or_null": exit_code_or_null,
        "signal_or_null": signal_or_null,
        "duration_seconds": round(duration, 3),
        "completed_at": utc_now(),
        "failure_reason_or_null": failure_reason_or_null,
    }
    if evidence_digests is not None:
        payload["evidence_digest_version"] = "session-evidence-1"
        payload.update(evidence_digests)
    atomic_write_json(paths.exit, payload)


def mark_state_superseded_by(state_path: Path, *, by_session: str) -> bool:
    """Stamp an existing state.json with ``superseded_by`` / ``superseded_at``.

    Returns True when the file was modified, False when it was missing, not in a
    failed terminal state, or already superseded. Atomic via :func:`atomic_write_json`.

    Idempotent: re-running with the same ``by_session`` does not change the file.
    """
    if not state_path.is_file():
        return False
    state = read_json_file(state_path)
    if state.get("state") != "failed":
        return False
    if state.get("superseded_by") == by_session:
        return False
    state["superseded_by"] = by_session
    state["superseded_at"] = utc_now()
    atomic_write_json(state_path, state)
    return True


def record_failed_agent_session(
    paths: AgentSessionPaths,
    invocation: AgentInvocation,
    reason: str,
    *,
    started_at: str | None = None,
    started_monotonic: float | None = None,
    exit_code_or_null: int | None = None,
    signal_or_null: int | None = None,
) -> None:
    append_agent_event(paths, invocation, "failed", failure_reason=reason)
    heartbeat = utc_now()
    write_agent_state(
        paths,
        invocation,
        "failed",
        pid=None,
        process_group_id=None,
        started_at=started_at,
        process_start_time=started_at,
        last_agent_activity_at=started_at,
        last_monitor_heartbeat_at=heartbeat,
        idle_seconds=0.0,
        failure_reason_or_null=reason,
    )
    write_agent_exit(
        paths,
        "failed",
        exit_code_or_null=exit_code_or_null,
        signal_or_null=signal_or_null,
        started_monotonic=started_monotonic,
        failure_reason_or_null=reason,
    )


def event_tail(path: Path, count: int) -> list[dict[str, Any]]:
    if count <= 0:
        return []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            lines = list(collections.deque(fh, maxlen=count))
    except FileNotFoundError:
        return []
    events: list[dict[str, Any]] = []
    for line in lines:
        line = line.rstrip("\n")
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            events.append({"malformed_event_line": line})
    return events


def read_state_without_schema(path: Path) -> tuple[str | None, dict[str, Any]]:
    state = read_json_file(path) if path.is_file() else {}
    state_schema = state.pop("schema_version", None)
    return state_schema, state
