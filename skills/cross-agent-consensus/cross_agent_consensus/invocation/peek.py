"""Read-only operator peek for supervised CAC agent sessions."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shlex
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cross_agent_consensus.config import PEEK_CONFIG_DEFAULTS, get_nested, resolve_config
from cross_agent_consensus.io import content_text_from_message, eprint, read_json_file
from cross_agent_consensus.models import AgentSessionPaths

from .readiness import padded_round_id
from .session_paths import latest_agent_session
from .telemetry import event_tail, read_state_without_schema

TERMINAL_STATES = {"completed", "failed", "cancelled", "timed_out"}
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
SECRET_ASSIGNMENT_RE = re.compile(
    r"""(?ix)
    \b(api[-_]?key|apikey|token|password|passwd|secret|authorization)\b
    \s*[:=]\s*
    (?:"[^"]*"|'[^']*'|`[^`]*`|bearer\s+\S+|\S+)
    """
)
BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
# Excludes "/" and "." so realistic file paths and dotted module names are not redacted.
LONG_SECRET_RE = re.compile(r"\b[A-Za-z0-9_+=-]{32,}\b")


@dataclass(frozen=True)
class PeekSettings:
    interval_seconds: float
    tail: int
    snippet_chars: int
    monitor_stale_seconds: float


@dataclass(frozen=True)
class Activity:
    index: int
    ts: dt.datetime | None
    did_phrase: str | None
    now_phrase: str | None


def parse_utc(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def utc_now_dt() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def duration_seconds_since(value: Any, now: dt.datetime) -> float | None:
    parsed = parse_utc(value)
    if parsed is None:
        return None
    return max(0.0, (now - parsed).total_seconds())


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    rounded = int(max(0, round(seconds)))
    if rounded < 60:
        return f"{rounded}s"
    minutes, secs = divmod(rounded, 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def _redact_assignment(match: re.Match[str]) -> str:
    raw = match.group(0)
    eq_index = raw.find("=")
    colon_index = raw.find(":")
    candidates = [idx for idx in (eq_index, colon_index) if idx >= 0]
    if not candidates:
        return "<redacted>"
    sep_index = min(candidates)
    return raw[:sep_index] + raw[sep_index] + "<redacted>"


def _redact_long_secret(match: re.Match[str]) -> str:
    value = match.group(0)
    has_letter = any(c.isalpha() for c in value)
    has_digit = any(c.isdigit() for c in value)
    if has_letter and has_digit:
        return "<redacted>"
    return value


def clamp_snippet(text: str, max_chars: int) -> str:
    text = ANSI_ESCAPE_RE.sub("", text)
    text = CONTROL_CHARS_RE.sub("", text)
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = SECRET_ASSIGNMENT_RE.sub(_redact_assignment, text)
    text = BEARER_RE.sub("Bearer <redacted>", text)
    text = LONG_SECRET_RE.sub(_redact_long_secret, text)
    if len(text) > max_chars:
        return text[: max(0, max_chars - 14)].rstrip() + "...<truncated>"
    return text


def validate_peek_settings(settings: PeekSettings) -> None:
    if settings.interval_seconds <= 0:
        raise ValueError("invocation.peek.interval_seconds must be > 0")
    if not 1 <= settings.tail <= 1000:
        raise ValueError("invocation.peek.tail must be between 1 and 1000")
    if not 40 <= settings.snippet_chars <= 500:
        raise ValueError("invocation.peek.snippet_chars must be between 40 and 500")
    if settings.monitor_stale_seconds <= 0:
        raise ValueError("invocation.peek.monitor_stale_seconds must be > 0")


def resolve_peek_settings(args: argparse.Namespace) -> PeekSettings:
    cwd = Path(args.cwd).expanduser() if getattr(args, "cwd", None) else Path.cwd()
    resolution, _ = resolve_config(
        cwd=cwd,
        explicit_config=getattr(args, "config", None),
        no_config=getattr(args, "no_config", False),
        strict=False,
    )
    if resolution.errors:
        raise ValueError("; ".join(resolution.errors))
    peek = get_nested(resolution.effective, "invocation.peek", {})
    if not isinstance(peek, dict):
        peek = {}

    def configured(name: str) -> Any:
        return peek.get(name, PEEK_CONFIG_DEFAULTS[name])

    settings = PeekSettings(
        interval_seconds=float(args.interval_seconds if args.interval_seconds is not None else configured("interval_seconds")),
        tail=int(args.tail if args.tail is not None else configured("tail")),
        snippet_chars=int(args.snippet_chars if args.snippet_chars is not None else configured("snippet_chars")),
        monitor_stale_seconds=float(
            args.monitor_stale_seconds if args.monitor_stale_seconds is not None else configured("monitor_stale_seconds")
        ),
    )
    validate_peek_settings(settings)
    return settings


def read_json_optional(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return read_json_file(path)
    except Exception:
        return None


def current_sizes(paths: AgentSessionPaths) -> dict[str, int]:
    return {
        "stdout": paths.stdout.stat().st_size if paths.stdout.is_file() else 0,
        "stderr": paths.stderr.stat().st_size if paths.stderr.is_file() else 0,
    }


def shell_inner_command(command: str) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        return command
    if len(parts) >= 3 and parts[0].endswith(("sh", "zsh", "bash")) and parts[1] == "-lc":
        return parts[2]
    return command


def short_command_phrase(command: str, max_chars: int, *, running: bool) -> str:
    command = shell_inner_command(command)
    inspected = inspected_path_from_command(command)
    if inspected:
        return "inspecting " + clamp_snippet(inspected, max_chars) if running else "inspected " + clamp_snippet(inspected, max_chars)
    command = clamp_snippet(command, max_chars)
    return "running " + command if running else "ran " + command


def inspected_path_from_command(command: str) -> str | None:
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    if not parts:
        return None
    if parts[0] in {"sed", "nl", "cat"}:
        for part in reversed(parts[1:]):
            if part.startswith("-") or re.fullmatch(r"\d+(,\d+)?p?", part):
                continue
            if "/" in part or "." in Path(part).name:
                return part
    return None


def find_string_by_key(value: Any, keys: set[str]) -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key) in keys and isinstance(item, str) and item.strip():
                return item
        for item in value.values():
            found = find_string_by_key(item, keys)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = find_string_by_key(item, keys)
            if found:
                return found
    return None


def extract_message_text(value: Any) -> str | None:
    if isinstance(value, dict):
        item = value.get("item")
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            return item["text"]
        message = value.get("message")
        if isinstance(message, str):
            return message
        if isinstance(message, dict):
            text = content_text_from_message(message)
            if text:
                return text
        event = value.get("event")
        if isinstance(event, dict):
            delta = event.get("delta")
            if isinstance(delta, dict) and isinstance(delta.get("text"), str):
                return delta["text"]
            content_block = event.get("content_block")
            if isinstance(content_block, dict) and isinstance(content_block.get("text"), str):
                return content_block["text"]
        for key in ["text", "delta", "result", "output"]:
            item_value = value.get(key)
            if isinstance(item_value, str) and item_value.strip():
                return item_value
        for item_value in value.values():
            found = extract_message_text(item_value)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = extract_message_text(item)
            if found:
                return found
    return None


def tool_phrase(entry: dict[str, Any], settings: PeekSettings, *, running: bool) -> str:
    native = entry.get("native_event") if isinstance(entry.get("native_event"), dict) else entry
    command = find_string_by_key(native, {"command"})
    if command:
        return short_command_phrase(command, settings.snippet_chars, running=running)
    tool_name = find_string_by_key(native, {"name", "tool_name", "tool"})
    if tool_name:
        tool_name = clamp_snippet(tool_name, settings.snippet_chars)
        return f"using {tool_name}" if running else f"used {tool_name}"
    return "using tool" if running else "used tool"


def activity_from_entry(entry: dict[str, Any], index: int, settings: PeekSettings) -> Activity | None:
    event_type = str(entry.get("type") or "")
    normalized = str(entry.get("normalized_type") or "")
    ts = parse_utc(entry.get("ts"))
    if event_type == "heartbeat":
        return None
    if event_type == "waiting_for_input" or normalized == "waiting_for_input":
        return Activity(index, ts, None, "waiting for input")
    if normalized == "tool_call":
        return Activity(index, ts, None, tool_phrase(entry, settings, running=True))
    if normalized == "tool_result":
        return Activity(index, ts, tool_phrase(entry, settings, running=False), None)
    if normalized == "message":
        native = entry.get("native_event") if isinstance(entry.get("native_event"), dict) else entry
        text = extract_message_text(native)
        if text:
            return Activity(index, ts, "produced reviewer text", clamp_snippet(text, settings.snippet_chars))
        return Activity(index, ts, "produced reviewer text", "drafting or reasoning")
    if event_type in {"started", "stdout", "stderr"}:
        return None
    if normalized in {"final", "runtime"}:
        return None
    return None


def combined_activity(paths: AgentSessionPaths, settings: PeekSettings) -> list[Activity]:
    activities: list[Activity] = []
    sequence = 0
    # Use stable composite indexes that preserve source-order within each file
    # and source-priority (events.jsonl before agent.log) at equal timestamps.
    for source_path in (paths.events, paths.agent_log):
        for entry in event_tail(source_path, settings.tail):
            activity = activity_from_entry(entry, sequence, settings)
            sequence += 1
            # Drop entries without a parseable timestamp; we cannot place them
            # accurately on the timeline and treating them as ordered progress
            # scrambles the did/now inference.
            if activity is not None and activity.ts is not None:
                activities.append(activity)
    return sorted(activities, key=lambda activity: (activity.ts, activity.index))


def derive_activity_phrases(activities: list[Activity]) -> tuple[str, str]:
    if not activities:
        return "activity not observed", "details unknown"
    now_index = -1
    now_phrase: str | None = None
    for index in range(len(activities) - 1, -1, -1):
        if activities[index].now_phrase:
            now_index = index
            now_phrase = activities[index].now_phrase
            break
    did_phrase: str | None = None
    # Only scan strictly before now_index so did always precedes now.
    search_limit = now_index if now_index >= 0 else len(activities)
    for index in range(search_limit - 1, -1, -1):
        if activities[index].did_phrase:
            did_phrase = activities[index].did_phrase
            break
    return did_phrase or "activity observed", now_phrase or "details unknown"


def terminal_state(
    persisted_state: str,
    exit_payload: dict[str, Any] | None,
    *,
    exit_exists: bool,
) -> str | None:
    # The locked design says exit.json presence is terminal. Treat any existing
    # exit.json as terminal even if its payload is empty or malformed.
    if exit_exists:
        if exit_payload:
            final_state = exit_payload.get("final_state")
            if isinstance(final_state, str) and final_state:
                return final_state
        return persisted_state if persisted_state in TERMINAL_STATES else "completed"
    if persisted_state in TERMINAL_STATES:
        return persisted_state
    return None


def agent_peek_snapshot(
    paths: AgentSessionPaths,
    actor_identity: str,
    settings: PeekSettings,
    *,
    previous_sizes: dict[str, int] | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    now = now or utc_now_dt()
    _, state = read_state_without_schema(paths.state)
    exit_exists = paths.exit.is_file()
    exit_payload = read_json_optional(paths.exit)
    persisted_state = str(state.get("state") or "unknown")
    monitor_age = duration_seconds_since(state.get("last_monitor_heartbeat_at"), now)
    idle_seconds = duration_seconds_since(state.get("last_agent_activity_at"), now)
    if idle_seconds is None:
        raw_idle = state.get("idle_seconds")
        idle_seconds = float(raw_idle) if isinstance(raw_idle, (int, float)) else None
    terminal = terminal_state(persisted_state, exit_payload, exit_exists=exit_exists)
    monitor_fresh = monitor_age is not None and monitor_age <= settings.monitor_stale_seconds
    if terminal:
        derived_state = terminal
    elif not monitor_fresh:
        derived_state = "monitor_stale"
    else:
        derived_state = persisted_state

    sizes = current_sizes(paths)
    activities = combined_activity(paths, settings)
    if previous_sizes:
        for stream_name, current_size in sizes.items():
            previous_size = previous_sizes.get(stream_name, current_size)
            if current_size > previous_size:
                activities.append(
                    Activity(
                        len(activities),
                        now,
                        None,
                        f"{stream_name} produced {current_size - previous_size} bytes",
                    )
                )
    activities = sorted(activities, key=lambda activity: (activity.ts, activity.index))
    did_phrase, now_phrase = derive_activity_phrases(activities)
    if terminal:
        now_phrase = terminal
    elif derived_state == "monitor_stale":
        now_phrase = f"monitor heartbeat stale for {format_duration(monitor_age)}"

    return {
        "actor_identity": actor_identity,
        "session_path": str(paths.session),
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "persisted_state": persisted_state,
        "derived_state": derived_state,
        "terminal": bool(terminal),
        "monitor_age_seconds": monitor_age,
        "monitor_fresh": monitor_fresh,
        "derived_idle_seconds": idle_seconds,
        "did": did_phrase,
        "now": now_phrase,
        "sizes": sizes,
    }


def format_peek_snapshot(snapshot: dict[str, Any]) -> str:
    generated_at = str(snapshot.get("generated_at") or "")
    clock = generated_at[11:16] if len(generated_at) >= 16 else "--:--"
    actor = snapshot.get("actor_identity")
    state = snapshot.get("derived_state")
    idle = format_duration(snapshot.get("derived_idle_seconds") if isinstance(snapshot.get("derived_idle_seconds"), (int, float)) else None)
    heartbeat = format_duration(snapshot.get("monitor_age_seconds") if isinstance(snapshot.get("monitor_age_seconds"), (int, float)) else None)
    return (
        f"[{clock}] {actor} {state} "
        f"did: {snapshot.get('did')} "
        f"now: {snapshot.get('now')} "
        f"idle={idle} heartbeat={heartbeat}"
    )


def missing_agent_peek_message(args: argparse.Namespace) -> str:
    return (
        f"No monitored agent session exists for actor {args.actor!r} in {padded_round_id(args.round)}. "
        "Use invoke-agent to record live telemetry before agent-peek can inspect a reviewer."
    )


def cmd_agent_peek(args: argparse.Namespace) -> int:
    try:
        settings = resolve_peek_settings(args)
        paths = latest_agent_session(Path(args.run), args.round, args.actor, args.session)
        previous_sizes: dict[str, int] | None = None
        while True:
            snapshot = agent_peek_snapshot(paths, args.actor, settings, previous_sizes=previous_sizes)
            print(format_peek_snapshot(snapshot))
            previous_sizes = snapshot["sizes"]
            if not args.follow or snapshot["terminal"] or snapshot["derived_state"] == "monitor_stale":
                break
            time.sleep(settings.interval_seconds)
        return 0
    except FileNotFoundError:
        eprint(f"error: {missing_agent_peek_message(args)}")
        return 2
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        eprint(f"error: {exc}")
        return 1
