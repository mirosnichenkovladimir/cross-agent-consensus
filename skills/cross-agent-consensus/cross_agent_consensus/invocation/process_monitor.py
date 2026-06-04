"""Process monitoring and cancellation for supervised CAC invocation."""

from __future__ import annotations

import argparse
import os
import selectors
import signal
import socket
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from cross_agent_consensus.io import append_jsonl, atomic_write_new, eprint, read_json_file, utc_now, write_bytes_new
from cross_agent_consensus.models import AgentInvocation, AgentSessionPaths, CommandSpec

from .adapters import GenericCliPlayer, ManualPlayer, get_player_adapter
from .readiness import (
    command_for_display,
    invocation_ready_errors,
    invoke_agent_round_path_errors,
    padded_round_id,
    runtime_command,
    secret_argv_errors,
)
from .session_paths import allocate_agent_session, latest_agent_session, session_relative
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


def classify_live_agent_state(last_activity_monotonic: float, invocation: AgentInvocation) -> str:
    idle_for = time.monotonic() - last_activity_monotonic
    if idle_for >= invocation.stale_timeout_seconds:
        return "stale"
    if idle_for >= invocation.idle_timeout_seconds:
        return "idle"
    return "running"


def build_agent_invocation(args: argparse.Namespace, paths: AgentSessionPaths, command: list[str]) -> AgentInvocation:
    round_id = padded_round_id(args.round)
    prompt_path = Path(args.prompt)
    raw_output_path = Path(args.raw_output)
    cwd = Path(args.cwd).expanduser()
    if not cwd.is_absolute():
        cwd = (Path.cwd() / cwd).resolve()
    return AgentInvocation(
        run=Path(args.run),
        round_id=round_id,
        phase=args.phase,
        actor_identity=args.actor,
        player_id=args.player,
        prompt_path=prompt_path,
        raw_output_path=raw_output_path,
        command=command,
        cwd=cwd,
        approved=args.approved,
        idle_timeout_seconds=args.idle_timeout_seconds,
        stale_timeout_seconds=args.stale_timeout_seconds,
        heartbeat_interval_seconds=args.heartbeat_interval_seconds,
        session_id=paths.session.name,
    )


def copy_prompt_for_session(paths: AgentSessionPaths, prompt: Path) -> None:
    if prompt.is_file():
        data = prompt.read_bytes()
        write_bytes_new(paths.prompt, data)


def prepare_agent_session(args: argparse.Namespace, command: list[str]) -> tuple[AgentInvocation, AgentSessionPaths, CommandSpec]:
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
            env_allowlist=[],
            executable_probe={"executable": False, "path": None},
        )
    else:
        command_spec = adapter.build_command(invocation)
    write_invocation_json(paths, invocation)
    write_command_json(paths, command_spec)
    copy_prompt_for_session(paths, invocation.prompt_path)
    append_agent_event(paths, invocation, "prepared", command_path=session_relative(paths.command, paths.session))
    return invocation, paths, command_spec


def prepare_rejected_agent_session(args: argparse.Namespace, command: list[str], reason: str) -> tuple[AgentInvocation, AgentSessionPaths]:
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
    adapter = get_player_adapter(invocation.player_id)
    stream_buffers = {"stdout": "", "stderr": ""}
    agent_log_buffers = {"stdout": "", "stderr": ""}
    try:
        started_monotonic = time.monotonic()
        started_at = utc_now()
        proc = subprocess.Popen(
            command_spec.argv,
            cwd=str(command_spec.cwd),
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
                    stream = key.fileobj
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
                    else:
                        selector.unregister(stream)
                now = time.monotonic()
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
                if proc.poll() is not None and not selector.get_map():
                    break
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
        elif return_code == 0:
            final_state = "completed"
            invocation.raw_output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(paths.stdout, invocation.raw_output_path)
            final_output = adapter.extract_final_output(paths)
            final_output_path = session_relative(final_output, paths.session)
            # Mirror the extracted final-output beside --raw-output so the path the
            # orchestrator pre-declared also leads to the parsed result, not only
            # the raw stdout event stream.
            if final_output.is_file():
                final_output_mirror = invocation.raw_output_path.with_suffix(
                    invocation.raw_output_path.suffix + ".final-output.md"
                )
                shutil.copyfile(final_output, final_output_mirror)
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
        return 1


def cmd_invoke_agent(args: argparse.Namespace) -> int:
    run = Path(args.run)
    command = runtime_command(args.command)
    try:
        if args.stale_timeout_seconds < args.idle_timeout_seconds:
            raise ValueError("--stale-timeout-seconds must be greater than or equal to --idle-timeout-seconds")
        secret_messages = secret_argv_errors(command)
        if secret_messages:
            reason = "; ".join(secret_messages)
            invocation, paths = prepare_rejected_agent_session(args, command, reason)
            print(f"session: {paths.session}")
            record_failed_agent_session(paths, invocation, reason)
            for message in secret_messages:
                eprint(f"error: {message}")
            return 3
        invocation, paths, command_spec = prepare_agent_session(args, command)
        print(f"session: {paths.session}")
        if invocation.player_id == "manual":
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
        readiness_args = argparse.Namespace(
            actor=args.actor,
            player=args.player,
            prompt=args.prompt,
            raw_output=args.raw_output,
            approved=args.approved,
            command=command,
        )
        messages = invocation_ready_errors(run, readiness_args, command)
        messages.extend(invoke_agent_round_path_errors(run, args))
        if messages:
            reason = "; ".join(messages)
            record_failed_agent_session(paths, invocation, reason)
            for message in messages:
                eprint(f"error: {message}")
            return 3
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
            actor_identity=str(invocation_data.get("actor_identity") or args.actor),
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
                current_pgid = os.getpgid(pid_int)
            except ProcessLookupError:
                current_pgid = None
            if current_pgid is not None:
                if current_pgid != pgid_int:
                    append_agent_event(
                        paths,
                        invocation,
                        "failed",
                        failure_reason="pid_unverifiable",
                        reason="process group mismatch before SIGKILL",
                    )
                    eprint("error: pid_unverifiable")
                    return 3
                if not process_identity_matches(pid_int, expected_identity):
                    if process_exists(pid_int):
                        append_agent_event(
                            paths,
                            invocation,
                            "failed",
                            failure_reason="pid_unverifiable",
                            reason="process identity mismatch before SIGKILL",
                        )
                        eprint("error: pid_unverifiable")
                        return 3
                else:
                    try:
                        os.killpg(pgid_int, signal.SIGKILL)
                    except PermissionError:
                        os.kill(pid_int, signal.SIGKILL)
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

