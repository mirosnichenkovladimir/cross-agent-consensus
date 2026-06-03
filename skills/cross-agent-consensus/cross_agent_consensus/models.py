"""Shared data models for the cross-agent-consensus helper CLI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


CONCLUSION_VALIDATION_REVIEW_MODE = "scope_triage"
CONCLUSION_VALIDATION_BATCH_PURPOSE = "conclusion_validation"
FRESH_REVIEW_MODE = "fresh_review"
PROPOSED_CONCLUSIONS = [
    "valid_blocker",
    "duplicate",
    "non_material",
    "out_of_scope",
    "false_positive",
    "deferred",
    "needs_human",
    "unclear",
]


@dataclass
class Record:
    record_type: str
    record_id: str
    path: Path
    heading_line: int
    data: dict[str, Any]


@dataclass
class CheckResult:
    ok: bool
    messages: list[str]

    @classmethod
    def pass_(cls, message: str = "pass") -> "CheckResult":
        return cls(True, [message])

    @classmethod
    def fail(cls, messages: list[str]) -> "CheckResult":
        return cls(False, messages)


@dataclass
class ConfigResolution:
    effective: dict[str, Any]
    sources: list[dict[str, Any]]
    provenance: dict[str, str]
    warnings: list[str]
    errors: list[str]


@dataclass
class PlayerCapabilities:
    player_id: str
    executable: bool
    supports_json_events: bool
    supports_resume: bool
    supports_cancel: bool
    prompt_transports: list[str]
    output_modes: list[str]
    executable_path_or_null: str | None


@dataclass
class CommandSpec:
    argv: list[str]
    cwd: Path
    prompt_transport: str
    output_mode: str
    env_allowlist: list[str]
    executable_probe: dict[str, Any]


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
    idle_timeout_seconds: float
    stale_timeout_seconds: float
    heartbeat_interval_seconds: float
    session_id: str


@dataclass
class AgentSessionPaths:
    session: Path
    invocation: Path
    command: Path
    prompt: Path
    events: Path
    agent_log: Path
    stdout: Path
    stderr: Path
    state: Path
    exit: Path
    final_output: Path
