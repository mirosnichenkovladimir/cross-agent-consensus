"""Agent session status and watch helpers."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from cross_agent_consensus.io import eprint, read_json_file
from cross_agent_consensus.models import AgentSessionPaths

from .readiness import padded_round_id
from .session_paths import latest_agent_session
from .telemetry import AGENT_STATUS_SCHEMA, event_tail, read_state_without_schema


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
