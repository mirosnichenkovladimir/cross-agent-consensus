"""Shared data models for the cross-agent-consensus helper CLI."""

from __future__ import annotations

from dataclasses import dataclass, field
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
    finding_schema_origin: str | None = None


@dataclass(frozen=True)
class RecordParseDiagnostic:
    path: Path
    heading_line: int
    message: str
    code: str | None = None


@dataclass
class ParsedRecordFile:
    records: list[Record]
    diagnostics: list[RecordParseDiagnostic]


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


@dataclass(frozen=True)
class ParticipantProfile:
    participant_profile_id: str
    role: str
    instructions: list[str]


@dataclass(frozen=True)
class ExecutionProfile:
    execution_profile_id: str
    adapter_id: str
    command: list[str]
    model_id_or_null: str | None
    reasoning_effort_or_null: str | None
    prompt_transport: str
    output_mode: str
    supports_resume: bool
    env_allowlist: list[str]


@dataclass(frozen=True)
class ParticipantIdentity:
    participant_identity: str
    participant_profile_id: str
    execution_profile_id: str


@dataclass(frozen=True)
class CheckpointChoice:
    choice_id: str
    consequence: str


@dataclass(frozen=True)
class PendingCheckpoint:
    checkpoint_id: str
    checkpoint_type: str
    record_id: str
    choices: tuple[CheckpointChoice, ...]


@dataclass(frozen=True)
class NextActionPlan:
    schema_version: str
    run_id: str
    phase: str
    plan_status: str
    terminal_status: str
    runnable_actions: tuple[str, ...]
    blockers: tuple[str, ...]
    required_records: tuple[str, ...]
    pending_checkpoints: tuple[PendingCheckpoint, ...]
    record_journal_sha256: str


@dataclass(frozen=True)
class BoundedRemediationPlan:
    schema_version: str
    run_id: str
    phase: str
    plan_status: str
    terminal_status: str
    record_journal_sha256: str
    checkpoint_id_or_null: str | None
    checkpoint_input_sha256: str
    checkpoint_status: str
    dispatch_phase_or_null: str | None
    round_id_or_null: str | None
    participant_identities: tuple[str, ...]
    execution_allowed: bool
    publication_authorized: bool
    blockers: tuple[str, ...]
    required_records: tuple[str, ...]


@dataclass(frozen=True)
class ResolvedInvocationProfile:
    participant_profile_id: str
    execution_profile_id: str
    adapter_id: str
    command: list[str]
    prompt_transport: str
    output_mode: str
    supports_resume: bool
    env_allowlist: list[str]


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
    resume_conformance_suite_or_null: str | None = None


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
    participant_identity: str
    participant_profile_id: str
    execution_profile_id: str
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
    execution_attempt_id: str | None = None
    retry_safety: str = "read_only"
    resume_provider_session_entry_id: str | None = None
    provider_session_id: str | None = None
    artifact_lineage_root_id: str | None = None
    continuation_definition_sha256: str | None = None
    provider_session_definition_resolution: str | None = None
    execution_profile_supports_resume: bool = False
    provider_session_resume_reservation_id: str | None = None
    prompt_transport: str = "stdin"
    output_mode: str = "raw_stdout"
    env_allowlist: list[str] = field(default_factory=list)


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


@dataclass
class PromptCommandInput:
    run: str
    phase: str
    actor: str | None
    artifact_version: str | None
    round: str | None
    review_batch: str | None
    output: str | None
    force_draft: bool
    dry_run: bool


@dataclass
class CaptureCommandInput:
    run: str
    phase: str
    actor: str | None
    review_batch: str | None
    artifact_version: str | None
    source_file: str | None
    source_mode: str
    source_command: str | None
    provider: str | None
    round: str | None
    validator_id: str | None
    result: str | None
    waiver_authority: str | None
    waiver_rationale: str | None
    no_append_record: bool
    no_narrative_extract: bool


@dataclass
class InvocationReadyInput:
    run: str
    actor: str
    player: str
    participant_profile_id: str | None
    execution_profile_id: str | None
    prompt: str
    raw_output: str
    approved: bool
    command: list[str] | None


@dataclass
class InvocationCommandInput(InvocationReadyInput):
    round: str
    phase: str
    cwd: str
    idle_timeout_seconds: float
    stale_timeout_seconds: float
    heartbeat_interval_seconds: float
    require_existing_approval: bool = False
    retry_safety: str | None = None
    approve_ambiguous_retry: bool = False
    operator_identity: str | None = None
    resume_provider_session_entry_id: str | None = None
    definition_drift_resolution: str | None = None
    definition_drift_reference: str | None = None
    provider_session_id: str | None = None
    artifact_lineage_root_id: str | None = None
    continuation_definition_sha256: str | None = None
    provider_session_definition_resolution: str | None = None
    execution_profile_supports_resume: bool = False
    checkpoint_id: str | None = None
    checkpoint_input_sha256: str | None = None


@dataclass
class RunCommandInput:
    run: str
    round: str
    phase: str
    actors: str | None
    execute_reviewers: bool
    approved: bool
    sequential: bool
    cwd: str
    idle_timeout_seconds: float
    stale_timeout_seconds: float
    heartbeat_interval_seconds: float
    operator_identity: str | None
    checkpoint_id: str | None = None
    checkpoint_input_sha256: str | None = None
