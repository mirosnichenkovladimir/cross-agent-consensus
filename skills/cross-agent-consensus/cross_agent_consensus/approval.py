"""Exact-input OperatorApproval records for external agent invocations."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from cross_agent_consensus.integrity import (
    artifact_by_id,
    approval_anchor_messages,
    canonical_json_sha256,
    command_sha256,
    resolved_execution_profile_sha256,
    verified_artifact_sha256,
)
from cross_agent_consensus.io import append_text, atomic_write_new, sha256_file, slugify, utc_now
from cross_agent_consensus.layout import DEFAULT_LAYOUT, detect_run_layout, normalize_round_id, round_dir
from cross_agent_consensus.markdown_records import frontmatter
from cross_agent_consensus.models import Record
from cross_agent_consensus.records import parse_run_records, records_by_type
from cross_agent_consensus.run_audit import append_run_event_locked, derive_run_phase, run_lock


def _path_inside_run(run: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(run.resolve()))
    except ValueError as exc:
        raise ValueError(f"approval prompt must be inside the run: {path}") from exc


def active_artifact_version_id(records: list[Record], round_id: str, explicit: str | None = None) -> str | None:
    if explicit:
        return explicit
    normalized_round = normalize_round_id(round_id)
    matching_batches = [
        record
        for record in records_by_type(records, "ReviewBatch")
        if normalize_round_id(str(record.data.get("round_id") or "round-1")) == normalized_round
    ]
    if matching_batches:
        value = matching_batches[-1].data.get("target_artifact_version_id")
        return str(value) if value else None
    artifacts = records_by_type(records, "ArtifactVersion")
    if artifacts:
        value = artifacts[-1].data.get("artifact_version_id")
        return str(value) if value else None
    return None


def approval_binding(
    run: Path,
    records: list[Record],
    *,
    participant_identity: str,
    participant_profile_id: str = "legacy-inline-participant-profile",
    execution_profile_id: str = "legacy-inline-execution-profile",
    player_id: str,
    phase: str,
    round_id: str,
    prompt_path: Path,
    command: list[str],
    artifact_version_id: str | None = None,
    working_directory: str | Path | None = None,
    resume_provider_session_entry_id: str | None = None,
    provider_session_id: str | None = None,
) -> dict[str, Any]:
    if not prompt_path.is_file():
        raise ValueError(f"approval prompt not found: {prompt_path}")
    artifact_id = active_artifact_version_id(records, round_id, artifact_version_id)
    artifact_sha: str | None = None
    if artifact_id is not None:
        artifact = artifact_by_id(records, artifact_id)
        if artifact is None:
            raise ValueError(f"approval artifact version not found: {artifact_id}")
        artifact_sha = verified_artifact_sha256(run, artifact)
        if artifact_sha is None:
            raise ValueError(
                f"approval artifact content cannot be resolved for hashing: {artifact_id}"
            )
    return {
        "participant_identity": participant_identity,
        "participant_profile_id": participant_profile_id,
        "execution_profile_id": execution_profile_id,
        "execution_profile_sha256_or_null": resolved_execution_profile_sha256(
            records, execution_profile_id
        ),
        "player_id": player_id,
        "phase": phase,
        "round_id": normalize_round_id(round_id),
        "prompt_path": _path_inside_run(run, prompt_path),
        "prompt_sha256": sha256_file(prompt_path),
        "command_sha256": command_sha256(command),
        "working_directory": str(Path(working_directory or Path.cwd()).expanduser().resolve()),
        "artifact_version_id_or_null": artifact_id,
        "artifact_sha256_or_null": artifact_sha,
        "resume_provider_session_entry_id_or_null": resume_provider_session_entry_id,
        "provider_session_id_or_null": provider_session_id,
    }


def _approval_data(
    *,
    record_id: str,
    run_id: str,
    round_id: str,
    phase: str,
    bindings: list[dict[str, Any]],
    mechanism: str,
    operator_identity: str | None,
    created_at: str,
) -> dict[str, Any]:
    actors = [str(binding["participant_identity"]) for binding in bindings]
    return {
        "record_type": "OperatorApproval",
        "schema_version": "m2-markdown-2",
        "run_id": run_id,
        "actor_identity": "orchestrator-approval-tool",
        "created_at": created_at,
        "operator_approval_id": record_id,
        "approved_actors": actors,
        "scope_run_id": run_id,
        "scope_round_id": normalize_round_id(round_id),
        "scope_phase": phase,
        "mechanism": mechanism,
        "operator_identity_or_null": operator_identity,
        "approval_binding_version": "exact-inputs-2",
        "approved_invocations": bindings,
    }


def _approval_text(record_id: str, data: dict[str, Any]) -> str:
    return "\n".join(["", f"## OperatorApproval {record_id}", frontmatter(data), ""])


def _approval_target(run: Path, round_id: str) -> Path:
    if detect_run_layout(run) == DEFAULT_LAYOUT:
        return round_dir(run, round_id) / "operator-approval.md"
    return run / "operator-approval.md"


def _binding_key(binding: dict[str, Any]) -> tuple[Any, ...]:
    return (
        binding.get("participant_identity", binding.get("actor_identity")),
        binding.get("participant_profile_id"),
        binding.get("execution_profile_id"),
        binding.get("execution_profile_sha256_or_null"),
        binding.get("player_id"),
        binding.get("phase"),
        binding.get("round_id"),
        binding.get("prompt_path"),
        binding.get("prompt_sha256"),
        binding.get("command_sha256"),
        binding.get("working_directory"),
        binding.get("artifact_version_id_or_null"),
        binding.get("artifact_sha256_or_null"),
        binding.get("resume_provider_session_entry_id_or_null"),
        binding.get("provider_session_id_or_null"),
    )


def _legacy_binding_key(binding: dict[str, Any]) -> tuple[Any, ...]:
    return (
        binding.get("participant_identity", binding.get("actor_identity")),
        binding.get("player_id"),
        binding.get("phase"),
        binding.get("round_id"),
        binding.get("prompt_path"),
        binding.get("prompt_sha256"),
        binding.get("command_sha256"),
        binding.get("working_directory"),
        binding.get("artifact_version_id_or_null"),
        binding.get("artifact_sha256_or_null"),
    )


def approval_binding_exists(records: list[Record], binding: dict[str, Any]) -> bool:
    return approval_record_for_binding(records, binding) is not None


def approval_record_for_binding(records: list[Record], binding: dict[str, Any]) -> Record | None:
    wanted = _binding_key(binding)
    legacy_wanted = _legacy_binding_key(binding)
    for record in records_by_type(records, "OperatorApproval"):
        bindings = record.data.get("approved_invocations")
        if not isinstance(bindings, list):
            continue
        for candidate in bindings:
            if not isinstance(candidate, dict):
                continue
            if _binding_key(candidate) == wanted:
                return record
            if (
                "execution_profile_id" not in candidate
                and binding.get("resume_provider_session_entry_id_or_null") is None
                and binding.get("provider_session_id_or_null") is None
                and _legacy_binding_key(candidate) == legacy_wanted
            ):
                return record
    return None


def stamp_operator_approval(
    run: Path,
    *,
    round_id: str,
    phase: str,
    bindings: list[dict[str, Any]],
    mechanism: str,
    operator_identity: str | None,
) -> Path:
    """Append one approval record while serializing mutations for ``run``."""

    with run_lock(run):
        records_before = parse_run_records(run)
        phase_before = derive_run_phase(records_before)
        created_at = utc_now()
        actor_suffix = "-".join(slugify(str(binding["participant_identity"])) for binding in bindings)
        record_id = (
            f"operator-approval-{slugify(round_id)}-{slugify(phase)}-{actor_suffix}-"
            f"{uuid.uuid4().hex[:12]}"
        )
        target = _approval_target(run, round_id)
        data = _approval_data(
            record_id=record_id,
            run_id=run.name,
            round_id=round_id,
            phase=phase,
            bindings=bindings,
            mechanism=mechanism,
            operator_identity=operator_identity,
            created_at=created_at,
        )
        text = _approval_text(record_id, data)
        if target.exists():
            append_text(target, text)
        else:
            atomic_write_new(target, text.lstrip("\n"))
        phase_after = derive_run_phase(parse_run_records(run))
        append_run_event_locked(
            run,
            "operator_approval_recorded",
            actor_identity=operator_identity or "operator",
            phase_before=phase_before,
            phase_after=phase_after,
            details={
                "operator_approval_id": record_id,
                "round": normalize_round_id(round_id),
                "phase": phase,
                "approved_actors": [binding["participant_identity"] for binding in bindings],
                "operator_approval_sha256": canonical_json_sha256(data),
            },
        )
        return target


def ensure_invocation_approval(
    run: Path,
    *,
    participant_identity: str,
    participant_profile_id: str = "legacy-inline-participant-profile",
    execution_profile_id: str = "legacy-inline-execution-profile",
    player_id: str,
    phase: str,
    round_id: str,
    prompt_path: Path,
    command: list[str],
    mechanism: str = "cli_approved_flag",
    working_directory: str | Path | None = None,
    resume_provider_session_entry_id: str | None = None,
    provider_session_id: str | None = None,
) -> dict[str, Any]:
    """Return an exact binding, recording it when no identical approval exists."""

    records = parse_run_records(run)
    binding = approval_binding(
        run,
        records,
        participant_identity=participant_identity,
        participant_profile_id=participant_profile_id,
        execution_profile_id=execution_profile_id,
        player_id=player_id,
        phase=phase,
        round_id=round_id,
        prompt_path=prompt_path,
        command=command,
        working_directory=working_directory,
        resume_provider_session_entry_id=resume_provider_session_entry_id,
        provider_session_id=provider_session_id,
    )
    if not approval_binding_exists(records, binding):
        stamp_operator_approval(
            run,
            round_id=round_id,
            phase=phase,
            bindings=[binding],
            mechanism=mechanism,
            operator_identity=None,
        )
    return binding


def verify_invocation_approval(
    run: Path,
    *,
    participant_identity: str,
    participant_profile_id: str = "legacy-inline-participant-profile",
    execution_profile_id: str = "legacy-inline-execution-profile",
    player_id: str,
    phase: str,
    round_id: str,
    prompt_path: Path,
    command: list[str],
    working_directory: str | Path | None = None,
    resume_provider_session_entry_id: str | None = None,
    provider_session_id: str | None = None,
) -> dict[str, Any]:
    """Require a previously recorded binding for the invocation's current inputs."""

    records = parse_run_records(run)
    binding = approval_binding(
        run,
        records,
        participant_identity=participant_identity,
        participant_profile_id=participant_profile_id,
        execution_profile_id=execution_profile_id,
        player_id=player_id,
        phase=phase,
        round_id=round_id,
        prompt_path=prompt_path,
        command=command,
        working_directory=working_directory,
        resume_provider_session_entry_id=resume_provider_session_entry_id,
        provider_session_id=provider_session_id,
    )
    approval_record = approval_record_for_binding(records, binding)
    if approval_record is None:
        raise ValueError(
            "prompt, command, or artifact drifted after OperatorApproval; "
            "stop this launch and request a new approval"
        )
    anchor_messages = approval_anchor_messages(run, records, [approval_record])
    if anchor_messages:
        raise ValueError("OperatorApproval integrity failed: " + "; ".join(anchor_messages))
    return binding
