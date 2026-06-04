"""Agent session status and watch helpers."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from cross_agent_consensus.capture import NARRATIVE_FINDING_ID_RE
from cross_agent_consensus.io import eprint, read_json_file
from cross_agent_consensus.models import AgentSessionPaths

from .readiness import padded_round_id
from .session_paths import latest_agent_session
from .telemetry import AGENT_STATUS_SCHEMA, event_tail, read_state_without_schema

# Event types in events.jsonl that indicate an agent error or abnormal terminal state;
# surfaced as `summary.event_errors` so the orchestrator can decide whether to rerun.
_AGENT_ERROR_EVENT_TYPES = {"failed", "cancelled", "timeout", "stale", "rejected", "error"}


def _file_line_count(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        return sum(1 for _ in fh)


def _narrative_finding_count(path: Path) -> int:
    if not path.is_file():
        return 0
    text = path.read_text(encoding="utf-8", errors="replace")
    return len({match.group(0).lower() for match in NARRATIVE_FINDING_ID_RE.finditer(text)})


def _event_error_count(path: Path) -> int:
    if not path.is_file():
        return 0
    errors = 0
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("type") in _AGENT_ERROR_EVENT_TYPES:
                errors += 1
    return errors


def agent_status_summary(paths: AgentSessionPaths) -> dict[str, int]:
    """Derived counts so callers can judge whether to proceed without reading files."""
    return {
        "final_output_lines": _file_line_count(paths.final_output),
        "narrative_findings": _narrative_finding_count(paths.final_output),
        "event_errors": _event_error_count(paths.events),
    }


def agent_session_state_counts(run: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for state_path in sorted(run.glob("rounds/round-*/agents/*/session-*/state.json")):
        state_payload = read_json_file(state_path)
        state = str(state_payload.get("state") or "unknown")
        counts[state] = counts.get(state, 0) + 1
    return counts


def format_agent_session_state_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{state}={count}" for state, count in sorted(counts.items()))


def agent_status_payload(paths: AgentSessionPaths, tail_count: int) -> dict[str, object]:
    state_schema, state = read_state_without_schema(paths.state)
    payload = {
        "schema_version": AGENT_STATUS_SCHEMA,
        "state_schema_version": state_schema,
        **state,
        "session_path": str(paths.session),
        "exit": read_json_file(paths.exit) if paths.exit.is_file() else None,
        "event_tail": event_tail(paths.events, tail_count),
        "agent_log_path": str(paths.agent_log) if paths.agent_log.is_file() else None,
        "summary": agent_status_summary(paths),
    }
    return payload


def missing_agent_status_payload(args: argparse.Namespace, message: str) -> dict[str, object]:
    return {
        "schema_version": AGENT_STATUS_SCHEMA,
        "state": "missing",
        "actor_identity": args.actor,
        "round_id": padded_round_id(args.round),
        "session_path": None,
        "exit": None,
        "event_tail": [],
        "agent_log_path": None,
        "summary": {"final_output_lines": 0, "narrative_findings": 0, "event_errors": 0},
        "message": message,
    }


def cmd_agent_status(args: argparse.Namespace) -> int:
    try:
        paths = latest_agent_session(Path(args.run), args.round, args.actor, args.session)
        payload = agent_status_payload(paths, args.tail)
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(f"actor: {args.actor}")
            print(f"session: {payload['session_path']}")
            print(f"player: {payload.get('player_id') or read_json_file(paths.invocation).get('player_id')}")
            print(f"state: {payload.get('state', 'unknown')}")
            print(f"pid: {payload.get('pid')}")
            print(f"started_at: {payload.get('started_at')}")
            print(f"last_agent_activity_at: {payload.get('last_agent_activity_at')}")
            print(f"idle_seconds: {payload.get('idle_seconds')}")
            exit_payload = payload.get("exit") or {}
            print(f"exit_code: {exit_payload.get('exit_code_or_null')}")
            print(f"stdout: {paths.stdout}")
            print(f"stderr: {paths.stderr}")
            print(f"agent_log: {paths.agent_log if paths.agent_log.exists() else None}")
            print(f"final_output: {paths.final_output if paths.final_output.exists() else None}")
            summary = payload["summary"]
            print(
                f"summary: final_output_lines={summary['final_output_lines']} "
                f"narrative_findings={summary['narrative_findings']} "
                f"event_errors={summary['event_errors']}"
            )
        return 0
    except FileNotFoundError:
        message = (
            f"No monitored agent session exists for actor {args.actor!r} in {padded_round_id(args.round)}. "
            "If output was captured directly with consensus capture, this is expected; use invoke-agent "
            "next time to record live telemetry."
        )
        if args.json:
            print(json.dumps(missing_agent_status_payload(args, message), indent=2, sort_keys=True))
        else:
            eprint(f"error: {message}")
        return 2
    except Exception as exc:
        eprint(f"error: {exc}")
        return 1


def cmd_agent_watch(args: argparse.Namespace) -> int:
    try:
        paths = latest_agent_session(Path(args.run), args.round, args.actor, args.session)
        offset = 0
        pending = ""
        while True:
            try:
                with paths.events.open("r", encoding="utf-8", errors="replace") as fh:
                    fh.seek(offset)
                    chunk = fh.read()
                    offset = fh.tell()
            except FileNotFoundError:
                chunk = ""
            if chunk:
                pending += chunk
                while True:
                    newline_index = pending.find("\n")
                    if newline_index == -1:
                        break
                    print(pending[:newline_index])
                    pending = pending[newline_index + 1 :]
            if not args.follow:
                if pending:
                    print(pending)
                break
            time.sleep(args.interval_seconds)
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        eprint(f"error: {exc}")
        return 1
