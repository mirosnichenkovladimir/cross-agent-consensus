"""Session path helpers for supervised CAC invocation."""

from __future__ import annotations

import re
from pathlib import Path

from cross_agent_consensus.io import slugify
from cross_agent_consensus.layout import round_dir
from cross_agent_consensus.models import AgentSessionPaths


# Suffix appended to --raw-output to locate the mirrored parsed final-output
# beside the raw stdout/event-stream capture.
FINAL_OUTPUT_MIRROR_SUFFIX = ".final-output.md"


def final_output_mirror_path(raw_output_path: Path) -> Path:
    """Sibling path of --raw-output that holds the extracted final-output mirror."""
    return raw_output_path.with_name(raw_output_path.name + FINAL_OUTPUT_MIRROR_SUFFIX)


def path_for_json(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(path)


def session_relative(path: Path, session: Path) -> str:
    try:
        return str(path.relative_to(session))
    except ValueError:
        return str(path)


def safe_actor_component(actor_identity: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9._-]+", actor_identity):
        return actor_identity
    return slugify(actor_identity, default="actor")


def agent_session_paths(session: Path) -> AgentSessionPaths:
    return AgentSessionPaths(
        session=session,
        invocation=session / "invocation.json",
        command=session / "command.json",
        prompt=session / "prompt.md",
        events=session / "events.jsonl",
        agent_log=session / "agent.log",
        stdout=session / "stdout.raw",
        stderr=session / "stderr.raw",
        state=session / "state.json",
        exit=session / "exit.json",
        final_output=session / "final-output.md",
    )


def allocate_agent_session(run: Path, round_value: str | None, actor_identity: str) -> AgentSessionPaths:
    actor_dir = round_dir(run, round_value) / "agents" / safe_actor_component(actor_identity)
    existing: list[int] = []
    for path in actor_dir.glob("session-*"):
        match = re.fullmatch(r"session-(\d+)", path.name)
        if match and path.is_dir():
            existing.append(int(match.group(1)))
    for index in range((max(existing) if existing else 0) + 1, 1000):
        session = actor_dir / f"session-{index:03d}"
        try:
            session.mkdir(parents=True, exist_ok=False)
            paths = agent_session_paths(session)
            _supersede_previous_failed_sessions(actor_dir, by_session=session.name)
            return paths
        except FileExistsError:
            continue
    raise FileExistsError(f"unable to allocate agent session under {actor_dir}")


def _supersede_previous_failed_sessions(actor_dir: Path, *, by_session: str) -> None:
    """Stamp any prior failed sessions in ``actor_dir`` as superseded by ``by_session``.

    A new session implies the operator chose to retry; previously failed
    attempts in the same actor directory are recovered evidence, not active
    failures. Imported here (not at module top) to avoid a circular import via
    ``telemetry`` -> ``session_paths``.
    """
    from .telemetry import mark_state_superseded_by

    for path in sorted(actor_dir.glob("session-*")):
        if not path.is_dir() or path.name == by_session:
            continue
        state_path = path / "state.json"
        mark_state_superseded_by(state_path, by_session=by_session)


def latest_agent_session(
    run: Path,
    round_value: str | None,
    actor_identity: str,
    session_id: str | None = None,
) -> AgentSessionPaths:
    actor_dir = round_dir(run, round_value) / "agents" / safe_actor_component(actor_identity)
    if session_id:
        session_name = session_id if session_id.startswith("session-") else f"session-{int(session_id):03d}"
        session = actor_dir / session_name
        if not session.is_dir():
            raise FileNotFoundError(f"agent session not found: {session}")
        return agent_session_paths(session)
    sessions = sorted(
        (path for path in actor_dir.glob("session-*") if path.is_dir()),
        key=lambda path: int(path.name.split("-", 1)[1]) if path.name.split("-", 1)[1].isdigit() else -1,
    )
    if not sessions:
        raise FileNotFoundError(f"no agent sessions found for {actor_identity} in {round_dir(run, round_value)}")
    return agent_session_paths(sessions[-1])

