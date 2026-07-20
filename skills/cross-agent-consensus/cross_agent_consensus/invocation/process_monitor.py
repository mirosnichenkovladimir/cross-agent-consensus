"""Process monitoring and cancellation for supervised CAC invocation."""

from __future__ import annotations

import argparse
import ctypes
import math
import os
import selectors
import signal
import socket
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, cast

from cross_agent_consensus.io import (
    append_jsonl,
    atomic_write_new,
    eprint,
    read_json_file,
    sha256_file,
    utc_now,
    write_bytes_new,
)
from cross_agent_consensus.approval import ensure_invocation_approval, verify_invocation_approval
from cross_agent_consensus.execution_attempts import (
    append_attempt_observation,
    assert_execution_attempt_retry_authorized,
    resolved_retry_safety,
    start_execution_attempt,
)
from cross_agent_consensus.models import (
    AgentInvocation,
    AgentSessionPaths,
    CommandSpec,
    InvocationCommandInput,
    InvocationReadyInput,
    Record,
)
from cross_agent_consensus.profiles import bind_recorded_invocation_profile
from cross_agent_consensus.provider_sessions import (
    capture_provider_session,
    resolve_provider_session_continuation,
)
from cross_agent_consensus.records import parse_run_records
from cross_agent_consensus.layout import record_round_number, round_number
from cross_agent_consensus.prompts import review_batch_by_id
from cross_agent_consensus.records import records_by_type
from cross_agent_consensus.review_budget import register_review_batch_launch

from .adapters import GenericCliPlayer, ManualPlayer, ProviderOutputError, StructuredJsonCliPlayer, get_player_adapter
from .readiness import (
    codex_trusted_dir_errors,
    command_for_display,
    invocation_ready_errors,
    invoke_agent_round_path_errors,
    padded_round_id,
    runtime_command,
    secret_argv_errors,
)
from .session_paths import (
    allocate_agent_session,
    final_output_mirror_path,
    latest_agent_session,
    session_relative,
)
from .telemetry import (
    append_agent_event,
    append_agent_log_from_stream,
    event_type_seen,
    flush_agent_log_from_stream,
    record_failed_agent_session,
    write_agent_exit,
    write_agent_state,
    write_command_json,
    write_invocation_json,
    write_rejected_command_json,
)

DEFAULT_IDLE_TIMEOUT_SECONDS = 300.0
DEFAULT_STALE_TIMEOUT_SECONDS = 1200.0
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 30.0
DEFAULT_CANCEL_GRACE_SECONDS = 10.0
PROC_PIDTBSDINFO = 3


class DarwinProcessBsdInfo(ctypes.Structure):
    """Subset-compatible layout of macOS ``struct proc_bsdinfo``."""

    _fields_ = [
        ("pbi_flags", ctypes.c_uint32),
        ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_uint32),
        ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32),
        ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32),
        ("pbi_ruid", ctypes.c_uint32),
        ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32),
        ("pbi_svgid", ctypes.c_uint32),
        ("pbi_rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * 16),
        ("pbi_name", ctypes.c_char * 32),
        ("pbi_nfiles", ctypes.c_uint32),
        ("pbi_pgid", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32),
        ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


def darwin_process_identity(pid: int) -> dict[str, Any] | None:
    """Read one process start time through libproc without invoking ``ps``."""

    try:
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        proc_pidinfo = libproc.proc_pidinfo
        proc_pidinfo.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint64,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        proc_pidinfo.restype = ctypes.c_int
        process_info = DarwinProcessBsdInfo()
        copied = proc_pidinfo(
            pid,
            PROC_PIDTBSDINFO,
            0,
            ctypes.byref(process_info),
            ctypes.sizeof(process_info),
        )
    except (AttributeError, OSError):
        return None
    if copied != ctypes.sizeof(process_info):
        return None
    return {
        "method": "darwin_proc_bsdinfo_starttime",
        "pid": pid,
        "started_seconds": int(process_info.pbi_start_tvsec),
        "started_microseconds": int(process_info.pbi_start_tvusec),
    }


def _sha256_or_none(path: Path) -> str | None:
    return sha256_file(path) if path.is_file() else None


def completed_session_evidence_digests(
    invocation: AgentInvocation,
    paths: AgentSessionPaths,
) -> dict[str, str | None]:
    """Hash the immutable inputs and outputs of a completed supervised session."""

    return {
        "invocation_sha256": _sha256_or_none(paths.invocation),
        "command_sha256": _sha256_or_none(paths.command),
        "prompt_sha256": _sha256_or_none(paths.prompt),
        "stdout_sha256": _sha256_or_none(paths.stdout),
        "stderr_sha256": _sha256_or_none(paths.stderr),
        "raw_output_sha256": _sha256_or_none(invocation.raw_output_path),
        "final_output_sha256_or_null": _sha256_or_none(paths.final_output),
    }


def current_process_identity(pid: int) -> dict[str, Any] | None:
    proc_stat = Path("/proc") / str(pid) / "stat"
    if proc_stat.is_file():
        try:
            text = proc_stat.read_text(encoding="utf-8")
            tail = text.rsplit(") ", 1)[1].split()
            return {
                "method": "proc_stat_starttime",
                "pid": pid,
                "starttime_ticks": tail[19],
            }
        except (IndexError, OSError):
            return None
    if sys.platform == "darwin":
        darwin_identity = darwin_process_identity(pid)
        if darwin_identity is not None:
            return darwin_identity
    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError:
        return None
    started = result.stdout.strip()
    if result.returncode == 0 and started:
        return {
            "method": "ps_lstart",
            "pid": pid,
            "started": started,
        }
    return None


def process_identity_matches(pid: int, expected: Any) -> bool:
    if not isinstance(expected, dict):
        return False
    current = current_process_identity(pid)
    return current is not None and current == expected


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def process_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def classify_live_agent_state(last_activity_monotonic: float, invocation: AgentInvocation) -> str:
    idle_for = time.monotonic() - last_activity_monotonic
    if idle_for >= invocation.stale_timeout_seconds:
        return "stale"
    if idle_for >= invocation.idle_timeout_seconds:
        return "idle"
    return "running"


def build_agent_invocation(args: InvocationCommandInput, paths: AgentSessionPaths, command: list[str]) -> AgentInvocation:
    round_id = padded_round_id(args.round)
    prompt_path = Path(args.prompt)
    raw_output_path = Path(args.raw_output)
    cwd = Path(args.cwd).expanduser().resolve()
    return AgentInvocation(
        run=Path(args.run),
        round_id=round_id,
        phase=args.phase,
        participant_identity=args.actor,
        participant_profile_id=args.participant_profile_id or "legacy-inline-participant-profile",
        execution_profile_id=args.execution_profile_id or f"legacy-inline-{args.player}-execution-profile",
        player_id=args.player,
        prompt_path=prompt_path,
        raw_output_path=raw_output_path,
        command=command,
        cwd=cwd,
        approved=args.approved,
        idle_timeout_seconds=args.idle_timeout_seconds,
        stale_timeout_seconds=args.stale_timeout_seconds,
        heartbeat_interval_seconds=args.heartbeat_interval_seconds,
        max_runtime_seconds=getattr(args, "max_runtime_seconds", None),
        rate_limit_circuit_breaker=getattr(args, "rate_limit_circuit_breaker", None),
        session_id=paths.session.name,
        retry_safety=resolved_retry_safety(args.phase, args.retry_safety),
        resume_provider_session_entry_id=getattr(
            args, "resume_provider_session_entry_id", None
        ),
        provider_session_id=getattr(args, "provider_session_id", None),
        artifact_lineage_root_id=getattr(args, "artifact_lineage_root_id", None),
        continuation_definition_sha256=getattr(
            args, "continuation_definition_sha256", None
        ),
        provider_session_definition_resolution=getattr(
            args, "provider_session_definition_resolution", None
        ),
        prompt_transport=getattr(args, "prompt_transport", "stdin"),
        output_mode=getattr(args, "output_mode", "raw_stdout"),
        env_allowlist=list(getattr(args, "env_allowlist", [])),
    )


def copy_prompt_for_session(paths: AgentSessionPaths, prompt: Path) -> None:
    if prompt.is_file():
        data = prompt.read_bytes()
        write_bytes_new(paths.prompt, data)


def prepare_agent_session(args: InvocationCommandInput, command: list[str]) -> tuple[AgentInvocation, AgentSessionPaths, CommandSpec]:
    run = Path(args.run)
    paths = allocate_agent_session(run, args.round, args.actor)
    invocation = build_agent_invocation(args, paths, command)
    adapter = get_player_adapter(invocation.player_id)
    if isinstance(adapter, ManualPlayer):
        command_spec = CommandSpec(
            argv=command,
            cwd=invocation.cwd,
            prompt_transport="manual",
            output_mode="manual_handoff",
            env_allowlist=invocation.env_allowlist,
            executable_probe={"executable": False, "path": None},
        )
    else:
        command_spec = adapter.build_command(invocation)
    write_invocation_json(paths, invocation)
    write_command_json(paths, command_spec)
    copy_prompt_for_session(paths, invocation.prompt_path)
    # Write an initial state.json BEFORE subprocess.Popen so durable launch
    # evidence exists even if exec itself raises (executable not on PATH, cwd
    # missing, etc.). The exception handler in run_generic_agent only writes
    # state via record_failed_agent_session AFTER the try-block enters; a
    # pre-Popen failure during interpreter setup would leave nothing without
    # this write.
    write_agent_state(
        paths,
        invocation,
        "prepared",
        pid=None,
        process_group_id=None,
        started_at=None,
        process_start_time=None,
        last_agent_activity_at=None,
        last_monitor_heartbeat_at=utc_now(),
        idle_seconds=0.0,
    )
    append_agent_event(paths, invocation, "prepared", command_path=session_relative(paths.command, paths.session))
    return invocation, paths, command_spec


def prepare_rejected_agent_session(
    args: InvocationCommandInput,
    command: list[str],
    reason: str,
) -> tuple[AgentInvocation, AgentSessionPaths]:
    run = Path(args.run)
    paths = allocate_agent_session(run, args.round, args.actor)
    invocation = build_agent_invocation(args, paths, command)
    write_invocation_json(paths, invocation)
    write_rejected_command_json(paths, reason)
    copy_prompt_for_session(paths, invocation.prompt_path)
    append_agent_event(paths, invocation, "prepared", command_path=session_relative(paths.command, paths.session))
    return invocation, paths


def run_generic_agent(invocation: AgentInvocation, paths: AgentSessionPaths, command_spec: CommandSpec) -> int:
    started_monotonic: float | None = None
    started_at: str | None = None
    proc: subprocess.Popen[bytes] | None = None
    selector: selectors.BaseSelector | None = None
    adapter = get_player_adapter(invocation.player_id)
    if not isinstance(adapter, GenericCliPlayer):
        raise TypeError(f"player {invocation.player_id} cannot run as a monitored CLI")
    stream_buffers = {"stdout": "", "stderr": ""}
    agent_log_buffers = {"stdout": "", "stderr": ""}
    timeout_requested_at: float | None = None
    stop_reason: str | None = None
    timeout_termination_sent = False
    timeout_force_kill_sent = False
    consecutive_429_events = 0
    cumulative_retry_delay_seconds = 0.0
    try:
        started_monotonic = time.monotonic()
        started_at = utc_now()
        proc = subprocess.Popen(
            command_spec.argv,
            cwd=str(command_spec.cwd),
            env={
                name: os.environ[name]
                for name in command_spec.env_allowlist
                if name in os.environ
            },
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        process_group_id = os.getpgid(proc.pid)
        process_identity = current_process_identity(proc.pid)
        append_agent_event(
            paths,
            invocation,
            "started",
            pid=proc.pid,
            process_group_id=process_group_id,
            process_identity=process_identity,
        )
        prompt_bytes = paths.prompt.read_bytes() if paths.prompt.is_file() else b""
        try:
            if proc.stdin:
                proc.stdin.write(prompt_bytes)
                proc.stdin.close()
        except BrokenPipeError:
            append_agent_event(paths, invocation, "stderr", byte_count=0, note="stdin closed before prompt was written")

        last_activity_monotonic = time.monotonic()
        last_activity_at = utc_now()
        last_heartbeat_at = utc_now()
        current_state = "running"
        write_agent_state(
            paths,
            invocation,
            current_state,
            pid=proc.pid,
            process_group_id=process_group_id,
            started_at=started_at,
            process_start_time=started_at,
            last_agent_activity_at=last_activity_at,
            last_monitor_heartbeat_at=last_heartbeat_at,
            idle_seconds=0.0,
            process_identity_or_null=process_identity,
        )
        selector = selectors.DefaultSelector()
        assert proc.stdout is not None
        assert proc.stderr is not None
        selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
        selector.register(proc.stderr, selectors.EVENT_READ, "stderr")
        next_heartbeat = time.monotonic() + max(0.05, invocation.heartbeat_interval_seconds)
        with paths.stdout.open("ab") as stdout_fh, paths.stderr.open("ab") as stderr_fh:
            while True:
                timeout = max(0.05, min(0.2, next_heartbeat - time.monotonic()))
                for key, _ in selector.select(timeout):
                    stream_name = str(key.data)
                    stream = cast(Any, key.fileobj)
                    data = stream.read1(65536)
                    if data:
                        target = stdout_fh if stream_name == "stdout" else stderr_fh
                        target.write(data)
                        target.flush()
                        last_activity_monotonic = time.monotonic()
                        last_activity_at = utc_now()
                        append_agent_event(paths, invocation, stream_name, byte_count=len(data))
                        if isinstance(adapter, GenericCliPlayer):
                            append_agent_log_from_stream(
                                paths,
                                invocation,
                                adapter,
                                stream_name,
                                data,
                                agent_log_buffers,
                            )
                            for event in adapter.parse_stream_events(stream_name, data, stream_buffers, invocation):
                                append_jsonl(paths.events, event)
                                native_event = event.get("native_event")
                                native_type = str(event.get("native_type") or "unknown")
                                retry_seconds = (
                                    adapter.provider_rate_limit_retry_seconds(
                                        native_event, native_type
                                    )
                                    if isinstance(native_event, dict)
                                    else None
                                )
                                if retry_seconds is not None:
                                    consecutive_429_events += 1
                                    cumulative_retry_delay_seconds += retry_seconds
                                    append_agent_event(
                                        paths,
                                        invocation,
                                        "provider_rate_limit_retry",
                                        consecutive_429_events=consecutive_429_events,
                                        cumulative_retry_delay_seconds=round(
                                            cumulative_retry_delay_seconds, 3
                                        ),
                                    )
                                    breaker = invocation.rate_limit_circuit_breaker
                                    if (
                                        breaker is not None
                                        and timeout_requested_at is None
                                        and (
                                            consecutive_429_events
                                            >= breaker.max_consecutive_429_events
                                            or cumulative_retry_delay_seconds
                                            >= breaker.max_cumulative_retry_delay_seconds
                                        )
                                    ):
                                        timeout_requested_at = time.monotonic()
                                        stop_reason = "provider_rate_limited"
                                        append_agent_event(
                                            paths,
                                            invocation,
                                            "provider_rate_limit_circuit_opened",
                                            consecutive_429_events=consecutive_429_events,
                                            cumulative_retry_delay_seconds=round(
                                                cumulative_retry_delay_seconds, 3
                                            ),
                                        )
                                elif event.get("normalized_type") in {
                                    "message",
                                    "tool_call",
                                    "tool_result",
                                    "final",
                                }:
                                    consecutive_429_events = 0
                    else:
                        selector.unregister(stream)
                now = time.monotonic()
                if (
                    invocation.max_runtime_seconds is not None
                    and now - started_monotonic >= invocation.max_runtime_seconds
                    and timeout_requested_at is None
                ):
                    timeout_requested_at = now
                    stop_reason = "max_runtime_exceeded"
                    append_agent_event(
                        paths,
                        invocation,
                        "max_runtime_exceeded",
                        max_runtime_seconds=invocation.max_runtime_seconds,
                    )
                if now >= next_heartbeat:
                    new_state = classify_live_agent_state(last_activity_monotonic, invocation)
                    if new_state != current_state:
                        current_state = new_state
                        append_agent_event(paths, invocation, current_state)
                    last_heartbeat_at = utc_now()
                    append_agent_event(paths, invocation, "heartbeat", state=current_state)
                    write_agent_state(
                        paths,
                        invocation,
                        current_state,
                        pid=proc.pid,
                        process_group_id=process_group_id,
                        started_at=started_at,
                        process_start_time=started_at,
                        last_agent_activity_at=last_activity_at,
                        last_monitor_heartbeat_at=last_heartbeat_at,
                        idle_seconds=time.monotonic() - last_activity_monotonic,
                        process_identity_or_null=process_identity,
                    )
                    next_heartbeat = now + max(0.05, invocation.heartbeat_interval_seconds)
                    if new_state == "stale" and timeout_requested_at is None:
                        timeout_requested_at = now
                        stop_reason = "stale_timeout"
                        append_agent_event(paths, invocation, "timeout_requested")
                if (
                    timeout_requested_at is not None
                    and not timeout_termination_sent
                    and now - timeout_requested_at >= 0.5
                ):
                    timeout_termination_sent = True
                    append_agent_event(
                        paths,
                        invocation,
                        "provider_rate_limit_terminate"
                        if stop_reason == "provider_rate_limited"
                        else "timeout_terminate",
                    )
                    try:
                        os.killpg(process_group_id, signal.SIGTERM)
                    except OSError:
                        pass
                if (
                    timeout_requested_at is not None
                    and not timeout_force_kill_sent
                    and now - timeout_requested_at >= DEFAULT_CANCEL_GRACE_SECONDS
                ):
                    timeout_force_kill_sent = True
                    append_agent_event(
                        paths,
                        invocation,
                        "provider_rate_limit_force_kill"
                        if stop_reason == "provider_rate_limited"
                        else "timeout_force_kill",
                    )
                    try:
                        os.killpg(process_group_id, signal.SIGKILL)
                    except OSError:
                        pass
                if proc.poll() is not None and not selector.get_map():
                    if timeout_requested_at is None or timeout_force_kill_sent:
                        break
                    if timeout_termination_sent and not process_group_exists(process_group_id):
                        break
                    # The provider leader may exit while an orphaned child keeps
                    # running after closing inherited stdout/stderr. Keep the
                    # supervisor alive through the process-group grace period.
                    continue
        if isinstance(adapter, GenericCliPlayer):
            flush_agent_log_from_stream(paths, invocation, adapter, agent_log_buffers)
            for event in adapter.flush_stream_events(stream_buffers, invocation):
                append_jsonl(paths.events, event)
        return_code = proc.wait()
        cancel_requested = event_type_seen(paths.events, "cancel_requested")
        signal_number = -return_code if return_code < 0 else None
        final_output_path: str | None = None
        failure_reason: str | None = None
        if cancel_requested:
            final_state = "cancelled"
            if paths.stdout.is_file():
                invocation.raw_output_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(paths.stdout, invocation.raw_output_path)
            append_agent_event(paths, invocation, "cancelled", exit_code=return_code, signal=signal_number)
        elif timeout_requested_at is not None:
            final_state = "failed" if stop_reason == "provider_rate_limited" else "timed_out"
            failure_reason = (
                "provider_rate_limited"
                if stop_reason == "provider_rate_limited"
                else "max_runtime_exceeded"
                if stop_reason == "max_runtime_exceeded"
                else "provider emitted no output before stale timeout"
            )
            if paths.stdout.is_file():
                invocation.raw_output_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(paths.stdout, invocation.raw_output_path)
            append_agent_event(
                paths,
                invocation,
                "provider_rate_limited"
                if stop_reason == "provider_rate_limited"
                else "timed_out",
                exit_code=return_code,
                signal=signal_number,
            )
        elif return_code == 0:
            final_state = "completed"
            require_structured_output = (
                isinstance(adapter, StructuredJsonCliPlayer)
                and (
                    invocation.output_mode == "stream_json"
                    or adapter.command_requests_json(command_spec.argv)
                )
            )
            if (
                require_structured_output
                and isinstance(adapter, StructuredJsonCliPlayer)
                and not adapter.stream_has_terminal_event(paths.stdout)
            ):
                raise ProviderOutputError(adapter.structured_output_failure(paths.stdout))
            capabilities = adapter.probe(command_spec.argv)
            if capabilities.supports_resume:
                provider_session_id = adapter.extract_provider_session_id(paths)
                if provider_session_id is None:
                    raise ProviderOutputError("missing_session_identifier")
                try:
                    capture_provider_session(
                        invocation,
                        provider_session_id=provider_session_id,
                        effective_command=command_spec.argv,
                    )
                except ValueError as exc:
                    raise ProviderOutputError(
                        "receipt_integrity_failure", str(exc)
                    ) from exc
                write_invocation_json(paths, invocation)
            invocation.raw_output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(paths.stdout, invocation.raw_output_path)
            final_output = adapter.extract_final_output(
                paths,
                require_structured=require_structured_output,
            )
            final_output_path = session_relative(final_output, paths.session)
            # Mirror the extracted final-output beside --raw-output so the path the
            # orchestrator pre-declared also leads to the parsed result, not only
            # the raw stdout event stream.
            if final_output.is_file():
                shutil.copyfile(final_output, final_output_mirror_path(invocation.raw_output_path))
            append_agent_event(paths, invocation, "completed", exit_code=return_code)
        else:
            final_state = "failed"
            failure_reason = f"process exited with code {return_code}"
            if paths.stdout.is_file():
                invocation.raw_output_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(paths.stdout, invocation.raw_output_path)
            append_agent_event(paths, invocation, "failed", exit_code=return_code, signal=signal_number)
        last_heartbeat_at = utc_now()
        write_agent_state(
            paths,
            invocation,
            final_state,
            pid=proc.pid,
            process_group_id=process_group_id,
            started_at=started_at,
            process_start_time=started_at,
            last_agent_activity_at=last_activity_at,
            last_monitor_heartbeat_at=last_heartbeat_at,
            idle_seconds=time.monotonic() - last_activity_monotonic,
            final_output_path_or_null=final_output_path,
            failure_reason_or_null=failure_reason,
            process_identity_or_null=process_identity,
        )
        write_agent_exit(
            paths,
            final_state,
            exit_code_or_null=return_code if return_code >= 0 else None,
            signal_or_null=signal_number,
            started_monotonic=started_monotonic,
            failure_reason_or_null=failure_reason,
            evidence_digests=(
                completed_session_evidence_digests(invocation, paths)
                if final_state == "completed"
                else None
            ),
        )
        if final_state == "completed":
            append_attempt_observation(
                invocation,
                "execution_attempt_ambiguous",
                failure_mode="missing_receipt",
                exit_code=return_code,
            )
        else:
            failure_mode = (
                "provider_rate_limited"
                if stop_reason == "provider_rate_limited"
                else "timeout"
                if final_state == "timed_out"
                else "process_termination"
                if final_state == "cancelled" or signal_number is not None
                else "nonzero_exit"
            )
            append_attempt_observation(
                invocation,
                "execution_attempt_failed",
                failure_mode=failure_mode,
                exit_code=return_code if return_code >= 0 else None,
                signal_number=signal_number,
            )
        return 0 if final_state == "completed" else 4
    except Exception as exc:
        reason = str(exc)
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except OSError:
                pass
        record_failed_agent_session(paths, invocation, reason, started_at=started_at, started_monotonic=started_monotonic)
        append_attempt_observation(
            invocation,
            "execution_attempt_failed",
            failure_mode=(
                exc.failure_mode
                if isinstance(exc, ProviderOutputError)
                else "launch_failure"
                if proc is None
                else "process_termination"
            ),
        )
        return 1
    finally:
        if selector is not None:
            selector.close()
        if proc is not None:
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream is not None and not stream.closed:
                    stream.close()


def exact_invocation_approval(
    args: InvocationCommandInput,
    command: list[str],
    *,
    require_existing: bool,
) -> dict[str, Any]:
    if require_existing:
        return verify_invocation_approval(
            Path(args.run),
            participant_identity=args.actor,
            participant_profile_id=args.participant_profile_id or "legacy-inline-participant-profile",
            execution_profile_id=args.execution_profile_id or f"legacy-inline-{args.player}-execution-profile",
            player_id=args.player,
            phase=args.phase,
            round_id=args.round,
            prompt_path=Path(args.prompt),
            command=command,
            working_directory=args.cwd,
            resume_provider_session_entry_id=args.resume_provider_session_entry_id,
            provider_session_id=getattr(args, "provider_session_id", None),
            checkpoint_id=getattr(args, "checkpoint_id", None),
            checkpoint_input_sha256=getattr(
                args, "checkpoint_input_sha256", None
            ),
        )
    return ensure_invocation_approval(
        Path(args.run),
        participant_identity=args.actor,
        participant_profile_id=args.participant_profile_id or "legacy-inline-participant-profile",
        execution_profile_id=args.execution_profile_id or f"legacy-inline-{args.player}-execution-profile",
        player_id=args.player,
        phase=args.phase,
        round_id=args.round,
        prompt_path=Path(args.prompt),
        command=command,
        working_directory=args.cwd,
        resume_provider_session_entry_id=args.resume_provider_session_entry_id,
        provider_session_id=getattr(args, "provider_session_id", None),
        checkpoint_id=getattr(args, "checkpoint_id", None),
        checkpoint_input_sha256=getattr(args, "checkpoint_input_sha256", None),
        mechanism="cli_approved_flag" if args.approved else "policy_unattended",
    )


def _review_batch_for_invocation(
    records: list[Record], args: InvocationCommandInput
) -> Record | None:
    review_batch_id = getattr(args, "review_batch_id", None)
    if review_batch_id:
        batch = review_batch_by_id(records, review_batch_id)
        if batch is None:
            raise ValueError(f"review_batch_id not found: {review_batch_id}")
        if record_round_number(batch) != round_number(args.round):
            raise ValueError(
                f"ReviewBatch {review_batch_id} does not belong to {padded_round_id(args.round)}"
            )
        return batch
    matching = [
        record
        for record in records_by_type(records, "ReviewBatch")
        if record_round_number(record) == round_number(args.round)
    ]
    return matching[-1] if matching else None


def cmd_invoke_agent(args: InvocationCommandInput) -> int:
    run = Path(args.run)
    records = parse_run_records(run)
    command = runtime_command(args.command)
    command, profile_messages = bind_recorded_invocation_profile(
        records, args, command
    )
    execution_profile_command = list(command)
    require_existing_approval = bool(getattr(args, "require_existing_approval", False))
    try:
        if profile_messages:
            for message in profile_messages:
                eprint(f"error: {message}")
            return 3
        resume_entry_id = getattr(args, "resume_provider_session_entry_id", None)
        if resume_entry_id:
            adapter = get_player_adapter(args.player)
            if not isinstance(adapter, GenericCliPlayer):
                raise ValueError(f"player {args.player} cannot resume a provider session")
            if adapter.has_native_resume_selector(command):
                raise ValueError(
                    "ExecutionProfile command must be fresh argv; remove the provider-native resume selector"
                )
            capabilities = adapter.probe(command)
            if (
                not capabilities.supports_resume
                or not getattr(args, "execution_profile_supports_resume", False)
            ):
                raise ValueError(
                    f"ExecutionProfile adapter {args.player} has not passed the provider resume conformance suite"
                )
            (
                provider_session_id,
                lineage_root,
                definition_digest,
                accepted_resolution,
            ) = resolve_provider_session_continuation(
                run,
                records,
                provider_session_entry_id=resume_entry_id,
                participant_identity=args.actor,
                participant_profile_id=(
                    args.participant_profile_id or "legacy-inline-participant-profile"
                ),
                execution_profile_id=(
                    args.execution_profile_id
                    or f"legacy-inline-{args.player}-execution-profile"
                ),
                player_id=args.player,
                phase=args.phase,
                definition_drift_resolution=getattr(
                    args, "definition_drift_resolution", None
                ),
                operator_identity=getattr(args, "operator_identity", None),
                definition_drift_reference=getattr(
                    args, "definition_drift_reference", None
                ),
            )
            command = adapter.build_resume_command(command, provider_session_id)
            args.provider_session_id = provider_session_id
            args.artifact_lineage_root_id = lineage_root
            args.continuation_definition_sha256 = definition_digest
            args.provider_session_definition_resolution = accepted_resolution
        else:
            adapter = get_player_adapter(args.player)
            if (
                isinstance(adapter, GenericCliPlayer)
                and adapter.has_native_resume_selector(command)
            ):
                raise ValueError(
                    "provider-native resume selector requires --resume-provider-session-entry"
                )
        if args.stale_timeout_seconds < args.idle_timeout_seconds:
            raise ValueError("--stale-timeout-seconds must be greater than or equal to --idle-timeout-seconds")
        max_runtime_seconds = getattr(args, "max_runtime_seconds", None)
        if max_runtime_seconds is not None and (
            not isinstance(max_runtime_seconds, (int, float))
            or isinstance(max_runtime_seconds, bool)
            or not math.isfinite(float(max_runtime_seconds))
            or float(max_runtime_seconds) <= 0
        ):
            raise ValueError("--max-runtime-seconds must be a finite number greater than zero")
        secret_messages = secret_argv_errors(command)
        if secret_messages:
            reason = "; ".join(secret_messages)
            invocation, paths = prepare_rejected_agent_session(args, [], reason)
            print(f"session: {paths.session}")
            record_failed_agent_session(paths, invocation, reason)
            for message in secret_messages:
                eprint(f"error: {message}")
            return 3
        # Fail before session allocation when a player-specific runtime trap is
        # detectable from argv alone (e.g. Codex without --skip-git-repo-check).
        # Allocating a session for an environment problem inflates the failed=
        # count in `consensus status` for what is operator-fixable.
        trusted_dir_messages = codex_trusted_dir_errors(args.player, command)
        if trusted_dir_messages:
            for message in trusted_dir_messages:
                eprint(f"error: {message}")
            return 3
        preverified_approval: dict[str, Any] | None = None
        if require_existing_approval:
            try:
                preverified_approval = exact_invocation_approval(
                    args,
                    command,
                    require_existing=True,
                )
            except ValueError as exc:
                eprint(f"error: {exc}")
                return 3
        if args.player == "manual":
            invocation, paths, command_spec = prepare_agent_session(args, command)
            print(f"session: {paths.session}")
            approval_binding: dict[str, Any] | None = None
            if args.approved:
                try:
                    approval_binding = preverified_approval or exact_invocation_approval(
                        args, command, require_existing=False
                    )
                except ValueError as exc:
                    record_failed_agent_session(paths, invocation, str(exc))
                    eprint(f"error: {exc}")
                    return 3
            if (
                approval_binding is not None
                and paths.prompt.is_file()
                and sha256_file(paths.prompt) != approval_binding["prompt_sha256"]
            ):
                reason = "session prompt copy does not match OperatorApproval"
                record_failed_agent_session(paths, invocation, reason)
                eprint(f"error: {reason}")
                return 3
            manual_command = command_for_display(command) if command else "(no command supplied)"
            atomic_write_new(paths.session / "manual-command.md", manual_command + "\n")
            if paths.prompt.is_file():
                shutil.copyfile(paths.prompt, paths.session / "manual-prompt.md")
            write_agent_state(
                paths,
                invocation,
                "prepared",
                pid=None,
                process_group_id=None,
                started_at=None,
                process_start_time=None,
                last_agent_activity_at=None,
                last_monitor_heartbeat_at=utc_now(),
                idle_seconds=0.0,
            )
            return 0
        readiness_args = InvocationReadyInput(
            run=str(run),
            actor=args.actor,
            player=args.player,
            participant_profile_id=args.participant_profile_id,
            execution_profile_id=args.execution_profile_id,
            prompt=args.prompt,
            raw_output=args.raw_output,
            approved=args.approved,
            command=(execution_profile_command if resume_entry_id else command),
        )
        messages = invocation_ready_errors(
            run,
            readiness_args,
            execution_profile_command if resume_entry_id else command,
        )
        messages.extend(invoke_agent_round_path_errors(run, args))
        if messages:
            for message in messages:
                eprint(f"error: {message}")
            return 3
        invocation_retry_safety = resolved_retry_safety(args.phase, args.retry_safety)
        try:
            assert_execution_attempt_retry_authorized(
                run,
                participant_identity=args.actor,
                phase=args.phase,
                retry_safety=invocation_retry_safety,
                approve_ambiguous_retry=bool(
                    getattr(args, "approve_ambiguous_retry", False)
                ),
                approve_provider_rate_limit_retry=bool(
                    getattr(args, "approve_provider_rate_limit_retry", False)
                ),
                operator_identity=getattr(args, "operator_identity", None),
            )
        except ValueError as exc:
            eprint(f"error: {exc}")
            return 3
        try:
            approval_binding = preverified_approval or exact_invocation_approval(
                args, command, require_existing=False
            )
        except ValueError as exc:
            eprint(f"error: {exc}")
            return 3
        if args.phase == "reviewer":
            batch = _review_batch_for_invocation(records, args)
            if batch is None:
                eprint(
                    f"error: no ReviewBatch found for reviewer invocation in {padded_round_id(args.round)}"
                )
                return 3
            try:
                register_review_batch_launch(run, records, batch)
            except ValueError as exc:
                eprint(f"error: review budget rejected launch: {exc}")
                return 3
        invocation, paths, command_spec = prepare_agent_session(args, command)
        print(f"session: {paths.session}")
        if not paths.prompt.is_file() or sha256_file(paths.prompt) != approval_binding["prompt_sha256"]:
            reason = "session prompt copy does not match OperatorApproval"
            record_failed_agent_session(paths, invocation, reason)
            eprint(f"error: {reason}")
            return 3
        invocation.execution_attempt_id = start_execution_attempt(
            invocation,
            retry_safety=invocation.retry_safety,
            approve_ambiguous_retry=bool(getattr(args, "approve_ambiguous_retry", False)),
            ambiguous_retry_operator_identity=getattr(args, "operator_identity", None),
            approve_provider_rate_limit_retry=bool(
                getattr(args, "approve_provider_rate_limit_retry", False)
            ),
        )
        write_invocation_json(paths, invocation)
        return run_generic_agent(invocation, paths, command_spec)
    except Exception as exc:
        eprint(f"error: {exc}")
        return 1


def cmd_agent_cancel(args: argparse.Namespace) -> int:
    try:
        run = Path(args.run)
        paths = latest_agent_session(run, args.round, args.actor, args.session)
        invocation_data = read_json_file(paths.invocation)
        invocation = AgentInvocation(
            run=run,
            round_id=str(invocation_data.get("round_id") or padded_round_id(args.round)),
            phase=str(invocation_data.get("phase") or "unknown"),
            participant_identity=str(
                invocation_data.get("participant_identity")
                or invocation_data.get("actor_identity")
                or args.actor
            ),
            participant_profile_id=str(
                invocation_data.get("participant_profile_id") or "legacy-inline-participant-profile"
            ),
            execution_profile_id=str(
                invocation_data.get("execution_profile_id") or "legacy-inline-execution-profile"
            ),
            player_id=str(invocation_data.get("player_id") or "generic-cli"),
            prompt_path=run / str(invocation_data.get("prompt_source_path") or ""),
            raw_output_path=run / str(invocation_data.get("raw_output_path") or ""),
            command=[],
            cwd=Path.cwd(),
            approved=bool(invocation_data.get("approved")),
            idle_timeout_seconds=DEFAULT_IDLE_TIMEOUT_SECONDS,
            stale_timeout_seconds=DEFAULT_STALE_TIMEOUT_SECONDS,
            heartbeat_interval_seconds=DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
            session_id=paths.session.name,
            execution_attempt_id=(
                str(invocation_data.get("execution_attempt_id_or_null"))
                if invocation_data.get("execution_attempt_id_or_null")
                else None
            ),
            retry_safety=str(invocation_data.get("retry_safety") or "read_only"),
        )
        state = read_json_file(paths.state)
        if state.get("state") in {"completed", "failed", "cancelled"} or paths.exit.is_file():
            eprint(f"error: session already terminal: {state.get('state', 'unknown')}")
            return 2
        pid = state.get("pid")
        pgid = state.get("process_group_id")
        expected_identity = state.get("process_identity")
        if (
            state.get("host") != socket.gethostname()
            or not pid
            or not pgid
            or not state.get("process_start_time")
            or not isinstance(expected_identity, dict)
        ):
            append_agent_event(paths, invocation, "failed", failure_reason="pid_unverifiable", reason=args.reason)
            eprint("error: pid_unverifiable")
            return 3
        try:
            pid_int = int(pid)
            pgid_int = int(pgid)
            current_pgid = os.getpgid(pid_int)
            if current_pgid != pgid_int:
                raise ProcessLookupError("process group mismatch")
            if not process_identity_matches(pid_int, expected_identity):
                raise ProcessLookupError("process identity mismatch")
            append_agent_event(paths, invocation, "cancel_requested", reason=args.reason)
            try:
                os.killpg(pgid_int, signal.SIGTERM)
            except PermissionError:
                os.kill(pid_int, signal.SIGTERM)
            time.sleep(max(0.0, args.grace_seconds))
            try:
                os.killpg(pgid_int, 0)
            except ProcessLookupError:
                pass
            else:
                try:
                    os.killpg(pgid_int, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            append_agent_event(paths, invocation, "cancelled", reason=args.reason)
            print(f"cancel requested: {paths.session}")
            return 0
        except ProcessLookupError as exc:
            append_agent_event(paths, invocation, "failed", failure_reason="pid_unverifiable", reason=str(exc))
            eprint("error: pid_unverifiable")
            return 3
        except PermissionError as exc:
            append_agent_event(paths, invocation, "failed", failure_reason="permission_denied", reason=str(exc))
            eprint("error: permission_denied")
            return 3
    except Exception as exc:
        eprint(f"error: {exc}")
        return 1
