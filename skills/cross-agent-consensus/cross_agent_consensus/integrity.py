"""Content digests and drift diagnostics for CAC records."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from cross_agent_consensus.io import read_json_file, safe_relative_path, sha256_file
from cross_agent_consensus.layout import normalize_round_id
from cross_agent_consensus.models import PromptCommandInput, Record
from cross_agent_consensus.prompts import prompt_target
from cross_agent_consensus.records import records_by_type
from cross_agent_consensus.run_audit import (
    read_run_events,
    recorded_run_version,
    run_event_messages,
    run_requires_event_integrity_v2,
)


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def canonical_json_sha256(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def command_sha256(argv: list[str]) -> str:
    return canonical_json_sha256(argv)


def content_locator_base(locator: str, cwd: Path) -> str | None:
    return None if Path(locator).is_absolute() else str(cwd.resolve())


def resolve_artifact_path(run: Path, record: Record) -> Path | None:
    locator = str(record.data.get("content_locator") or "")
    if not locator:
        return None
    locator_path = Path(locator)
    if locator_path.is_absolute():
        return locator_path
    base = record.data.get("content_locator_base_or_null")
    candidates = []
    if isinstance(base, str) and base:
        candidates.append(Path(base) / locator_path)
    candidates.extend([Path.cwd() / locator_path, run / locator_path])
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0] if candidates else None


def artifact_by_id(records: list[Record], artifact_version_id: str) -> Record | None:
    for record in records_by_type(records, "ArtifactVersion"):
        if str(record.data.get("artifact_version_id")) == artifact_version_id:
            return record
    return None


def verified_artifact_sha256(run: Path, record: Record) -> str | None:
    recorded = record.data.get("content_hash_or_null")
    path = resolve_artifact_path(run, record)
    if path is None or not path.is_file():
        if recorded is None:
            return None
        raise ValueError(
            f"ArtifactVersion {record.record_id} records sha256 {recorded} but its content_locator "
            "cannot be resolved to a file"
        )
    current = sha256_file(path)
    if recorded is not None and current != recorded:
        raise ValueError(
            f"ArtifactVersion {record.record_id} drifted: recorded sha256={recorded}, current sha256={current}"
        )
    snapshot_sha256 = record.data.get("git_change_snapshot_sha256")
    if snapshot_sha256 is not None:
        if not isinstance(snapshot_sha256, str):
            raise ValueError(
                f"ArtifactVersion {record.record_id} Git snapshot sha256 is not a string"
            )
        from cross_agent_consensus.git_snapshot import verify_git_change_snapshot

        verify_git_change_snapshot(path, snapshot_sha256)
    return current


def _payload_path(run: Path, value: Any, field: str) -> Path:
    relative = safe_relative_path(str(value), field)
    return run / relative


def resolved_execution_profile_sha256(
    records: list[Record], execution_profile_id: str
) -> str | None:
    """Hash the resolved ExecutionProfile recorded for one participant."""

    for record in records_by_type(records, "ConfigResolution"):
        profiles = record.data.get("resolved_execution_profiles")
        if not isinstance(profiles, dict):
            continue
        profile = profiles.get(execution_profile_id)
        if isinstance(profile, dict):
            return canonical_json_sha256(profile)
    return None


def config_uses_execution_profiles(records: list[Record]) -> bool:
    return any(
        record.data.get("config_schema_version") == "cross-agent-consensus-config-2"
        for record in records_by_type(records, "ConfigResolution")
    )


def _approval_covers_session(
    records: list[Record],
    *,
    participant_identity: str,
    participant_profile_id: str | None = None,
    execution_profile_id: str | None = None,
    player_id: str,
    phase: str,
    round_id: str,
    prompt_path: str,
    prompt_sha256: str,
    command_digest: str,
    working_directory: str,
    artifact_version_id: str | None,
    artifact_sha256: str | None,
    resume_provider_session_entry_id: str | None = None,
    provider_session_id: str | None = None,
) -> bool:
    session_binding = {
        "participant_identity": participant_identity,
        "player_id": player_id,
        "phase": phase,
        "round_id": normalize_round_id(round_id),
        "prompt_path": prompt_path,
        "prompt_sha256": prompt_sha256,
        "command_sha256": command_digest,
        "working_directory": working_directory,
        "artifact_version_id_or_null": artifact_version_id,
        "artifact_sha256_or_null": artifact_sha256,
        "resume_provider_session_entry_id_or_null": resume_provider_session_entry_id,
        "provider_session_id_or_null": provider_session_id,
    }
    if participant_profile_id is not None:
        session_binding["participant_profile_id"] = participant_profile_id
    if execution_profile_id is not None:
        session_binding["execution_profile_id"] = execution_profile_id
        execution_profile_digest = resolved_execution_profile_sha256(
            records, execution_profile_id
        )
        if execution_profile_digest is not None:
            session_binding["execution_profile_sha256_or_null"] = execution_profile_digest
    for approval in records_by_type(records, "OperatorApproval"):
        bindings = approval.data.get("approved_invocations")
        if not isinstance(bindings, list):
            continue
        for binding in bindings:
            if not isinstance(binding, dict):
                continue
            comparable_binding = dict(binding)
            if "participant_identity" not in comparable_binding and "actor_identity" in comparable_binding:
                comparable_binding["participant_identity"] = comparable_binding["actor_identity"]
            if all(comparable_binding.get(field) == value for field, value in session_binding.items()):
                return True
    return False


def live_session_messages(
    run: Path,
    records: list[Record],
    record: Record,
    *,
    participant_identity: str,
    phase: str,
    artifact_version_id: str,
) -> list[str]:
    if record.data.get("capture_origin") != "live_cli":
        return []
    prefix = f"{record.path}:{record.heading_line}"
    session_id = record.data.get("session_id_or_null")
    session_path = record.data.get("session_path_or_null")
    evidence_prompt_sha = record.data.get("prompt_sha256_or_null")
    if not all(isinstance(value, str) and value for value in (session_id, session_path, evidence_prompt_sha)):
        return [f"{prefix}: live_cli evidence requires session id/path and prompt sha256"]
    try:
        session = _payload_path(run, session_path, "session_path_or_null")
    except ValueError as exc:
        return [f"{prefix}: {exc}"]
    if not session.is_dir() or session.name != session_id:
        return [f"{prefix}: supervised session not found: {session_path}"]
    try:
        invocation = read_json_file(session / "invocation.json")
        command_payload = read_json_file(session / "command.json")
        exit_payload = read_json_file(session / "exit.json")
    except (OSError, ValueError) as exc:
        return [f"{prefix}: supervised session records are unreadable: {exc}"]
    messages: list[str] = []
    recorded_identity = invocation.get("participant_identity", invocation.get("actor_identity"))
    if invocation.get("session_id") != session_id or recorded_identity != participant_identity:
        messages.append(f"{prefix}: supervised session identity does not match evidence")
    if invocation.get("phase") != phase:
        messages.append(f"{prefix}: supervised session phase does not match evidence")
    session_round = session.parent.parent.parent.name
    try:
        normalized_session_round = normalize_round_id(session_round)
        normalized_invocation_round = normalize_round_id(str(invocation.get("round_id") or ""))
    except ValueError as exc:
        messages.append(f"{prefix}: supervised session round is invalid: {exc}")
        normalized_session_round = ""
        normalized_invocation_round = ""
    if normalized_invocation_round != normalized_session_round:
        messages.append(f"{prefix}: supervised session round does not match its path")
    session_prompt_sha = invocation.get("prompt_sha256")
    if session_prompt_sha != evidence_prompt_sha:
        messages.append(f"{prefix}: supervised session prompt sha256 does not match evidence")
    if exit_payload.get("final_state") != "completed" or exit_payload.get("exit_code_or_null") != 0:
        messages.append(f"{prefix}: live_cli evidence session did not complete successfully")
    prompt_source = invocation.get("prompt_source_path")
    prompt_path_for_approval = ""
    source_prompt: Path | None = None
    try:
        prompt_relative_path = safe_relative_path(str(prompt_source), "prompt_source_path")
    except ValueError as exc:
        messages.append(f"{prefix}: {exc}")
    else:
        prompt_path_for_approval = str(prompt_relative_path)
        source_prompt = run / prompt_relative_path
    review_batch_id = record.data.get("review_batch_id")
    if phase == "reviewer" and isinstance(review_batch_id, str):
        batch = next(
            (
                candidate
                for candidate in records_by_type(records, "ReviewBatch")
                if candidate.data.get("review_batch_id") == review_batch_id
            ),
            None,
        )
        if batch is None:
            messages.append(f"{prefix}: ReviewBatch {review_batch_id} is missing")
        else:
            expected_prompt = prompt_target(
                run,
                PromptCommandInput(
                    run=str(run),
                    phase="reviewer",
                    actor=participant_identity,
                    round=str(batch.data.get("round_id") or "round-1"),
                    review_batch=review_batch_id,
                    artifact_version=artifact_version_id,
                    output=None,
                    force_draft=False,
                    dry_run=False,
                ),
                records,
            )
            if source_prompt is not None and source_prompt.resolve() != expected_prompt.resolve():
                messages.append(
                    f"{prefix}: supervised session prompt does not belong to ReviewBatch {review_batch_id}"
                )
    argv = command_payload.get("argv")
    if not isinstance(argv, list) or not all(isinstance(value, str) for value in argv):
        messages.append(f"{prefix}: supervised session command argv is invalid")
        return messages
    digest = command_sha256(argv)
    session_player = str(invocation.get("player_id") or "")
    participant_profile_id = invocation.get("participant_profile_id")
    execution_profile_id = invocation.get("execution_profile_id")
    session_cwd = str(command_payload.get("cwd") or "")
    run_version = recorded_run_version(run)
    requires_session_evidence = (
        record.data.get("session_exit_sha256_or_null") is not None
        or exit_payload.get("evidence_digest_version") is not None
        or (run_version is not None and run_version >= (0, 10, 0))
    )
    if requires_session_evidence:
        if exit_payload.get("evidence_digest_version") != "session-evidence-1":
            messages.append(f"{prefix}: supervised session evidence marker mismatch")
        exit_sha = record.data.get("session_exit_sha256_or_null")
        if not isinstance(exit_sha, str) or sha256_file(session / "exit.json") != exit_sha:
            messages.append(f"{prefix}: supervised session exit record sha256 mismatch")
        session_files = {
            "invocation_sha256": session / "invocation.json",
            "command_sha256": session / "command.json",
            "prompt_sha256": session / "prompt.md",
            "stdout_sha256": session / "stdout.raw",
            "stderr_sha256": session / "stderr.raw",
        }
        for digest_field, path in session_files.items():
            expected_digest = exit_payload.get(digest_field)
            if not isinstance(expected_digest, str) or not path.is_file() or sha256_file(path) != expected_digest:
                messages.append(f"{prefix}: supervised session {digest_field} mismatch")
        raw_value = invocation.get("raw_output_path")
        try:
            raw_output = _payload_path(run, raw_value, "raw_output_path")
        except ValueError as exc:
            messages.append(f"{prefix}: {exc}")
        else:
            raw_digest = exit_payload.get("raw_output_sha256")
            if not isinstance(raw_digest, str) or not raw_output.is_file() or sha256_file(raw_output) != raw_digest:
                messages.append(f"{prefix}: supervised session raw output sha256 mismatch")
            if record.data.get("raw_payload_sha256") != raw_digest and record.data.get("payload_sha256") != raw_digest:
                messages.append(f"{prefix}: captured payload does not match supervised session raw output")
        final_digest = exit_payload.get("final_output_sha256_or_null")
        if final_digest is not None and (
            not isinstance(final_digest, str)
            or not (session / "final-output.md").is_file()
            or sha256_file(session / "final-output.md") != final_digest
        ):
            messages.append(f"{prefix}: supervised session final output sha256 mismatch")
        if source_prompt is None or not source_prompt.is_file() or sha256_file(source_prompt) != evidence_prompt_sha:
            messages.append(f"{prefix}: approved prompt source sha256 mismatch")
    artifact_sha256: str | None = None
    artifact = artifact_by_id(records, artifact_version_id)
    if artifact is None:
        messages.append(f"{prefix}: ArtifactVersion {artifact_version_id} is missing")
    else:
        try:
            artifact_sha256 = verified_artifact_sha256(run, artifact)
        except ValueError as exc:
            messages.append(f"{prefix}: {exc}")
    if not _approval_covers_session(
        records,
        participant_identity=participant_identity,
        participant_profile_id=(
            str(participant_profile_id) if isinstance(participant_profile_id, str) else None
        ),
        execution_profile_id=(
            str(execution_profile_id) if isinstance(execution_profile_id, str) else None
        ),
        player_id=session_player,
        phase=phase,
        round_id=normalized_session_round,
        prompt_path=prompt_path_for_approval,
        prompt_sha256=str(evidence_prompt_sha),
        command_digest=digest,
        working_directory=session_cwd,
        artifact_version_id=artifact_version_id,
        artifact_sha256=artifact_sha256,
        resume_provider_session_entry_id=(
            str(invocation.get("resume_provider_session_entry_id_or_null"))
            if invocation.get("resume_provider_session_entry_id_or_null")
            else None
        ),
        provider_session_id=(
            str(invocation.get("provider_session_id_or_null"))
            if invocation.get("resume_provider_session_entry_id_or_null")
            and invocation.get("provider_session_id_or_null")
            else None
        ),
    ):
        messages.append(f"{prefix}: no exact-input OperatorApproval covers the supervised session")
    return messages


def artifact_integrity_messages(run: Path, records: list[Record]) -> list[str]:
    messages: list[str] = []
    for record in records_by_type(records, "ArtifactVersion"):
        if record.data.get("content_hash_or_null") is None:
            continue
        try:
            verified_artifact_sha256(run, record)
        except ValueError as exc:
            messages.append(f"{record.path}:{record.heading_line}: {exc}")
    return messages


def integrity_messages(run: Path, records: list[Record]) -> list[str]:
    messages = artifact_integrity_messages(run, records)

    for record in records:
        draft_path = record.data.get("draft_payload_path")
        draft_sha256 = record.data.get("draft_payload_sha256")
        if draft_path is None and draft_sha256 is None:
            continue
        if not isinstance(draft_path, str) or not isinstance(draft_sha256, str):
            messages.append(
                f"{record.path}:{record.heading_line}: draft payload path and sha256 must both be strings"
            )
            continue
        try:
            captured_draft = _payload_path(run, draft_path, "draft_payload_path")
        except ValueError as exc:
            messages.append(f"{record.path}:{record.heading_line}: {exc}")
            continue
        if not captured_draft.is_file():
            messages.append(
                f"{record.path}:{record.heading_line}: draft payload not found: {draft_path}"
            )
        elif sha256_file(captured_draft) != draft_sha256:
            messages.append(
                f"{record.path}:{record.heading_line}: draft payload sha256 mismatch: {draft_path}"
            )

    for record in records_by_type(records, "RawReviewerOutput"):
        payload_path = record.data.get("raw_payload_path")
        payload_sha = record.data.get("raw_payload_sha256")
        if payload_path is None and payload_sha is None:
            continue
        if not isinstance(payload_path, str) or not isinstance(payload_sha, str):
            messages.append(
                f"{record.path}:{record.heading_line}: RawReviewerOutput raw payload path and sha256 must both be strings"
            )
            continue
        try:
            path = _payload_path(run, payload_path, "raw_payload_path")
        except ValueError as exc:
            messages.append(f"{record.path}:{record.heading_line}: {exc}")
            continue
        if not path.is_file():
            messages.append(f"{record.path}:{record.heading_line}: raw payload not found: {payload_path}")
        elif sha256_file(path) != payload_sha:
            messages.append(f"{record.path}:{record.heading_line}: raw payload sha256 mismatch: {payload_path}")
        messages.extend(
            live_session_messages(
                run,
                records,
                record,
                participant_identity=str(record.data.get("reviewer_identity") or ""),
                phase="reviewer",
                artifact_version_id=str(record.data.get("artifact_version_id") or ""),
            )
        )

    for record in records_by_type(records, "ValidationEvidence"):
        payload_path = record.data.get("payload_reference")
        payload_sha = record.data.get("payload_sha256")
        if payload_sha is None:
            continue
        if not isinstance(payload_path, str) or not isinstance(payload_sha, str):
            messages.append(
                f"{record.path}:{record.heading_line}: ValidationEvidence payload_reference and payload_sha256 must be strings"
            )
            continue
        try:
            path = _payload_path(run, payload_path, "payload_reference")
        except ValueError as exc:
            messages.append(f"{record.path}:{record.heading_line}: {exc}")
            continue
        if not path.is_file():
            messages.append(f"{record.path}:{record.heading_line}: validation payload not found: {payload_path}")
        elif sha256_file(path) != payload_sha:
            messages.append(f"{record.path}:{record.heading_line}: validation payload sha256 mismatch: {payload_path}")
        messages.extend(
            live_session_messages(
                run,
                records,
                record,
                participant_identity=str(record.data.get("produced_by") or ""),
                phase="validator",
                artifact_version_id=str(record.data.get("target_artifact_version_id") or ""),
            )
        )

    for record in records_by_type(records, "OperatorApproval"):
        checkpoint_id = record.data.get("checkpoint_id")
        checkpoint_sha256 = record.data.get("checkpoint_input_sha256")
        if checkpoint_id is not None or checkpoint_sha256 is not None:
            if not isinstance(checkpoint_id, str) or not checkpoint_id:
                messages.append(
                    f"{record.path}:{record.heading_line}: bounded checkpoint id is missing"
                )
            if not isinstance(checkpoint_sha256, str) or not SHA256_RE.fullmatch(
                checkpoint_sha256
            ):
                messages.append(
                    f"{record.path}:{record.heading_line}: bounded checkpoint input sha256 is invalid"
                )
        bindings = record.data.get("approved_invocations")
        if bindings is None:
            continue
        if not isinstance(bindings, list):
            messages.append(f"{record.path}:{record.heading_line}: approved_invocations must be a list")
            continue
        for binding in bindings:
            if not isinstance(binding, dict):
                messages.append(f"{record.path}:{record.heading_line}: approved_invocations entries must be mappings")
                continue
            participant = str(
                binding.get("participant_identity") or binding.get("actor_identity") or "unknown"
            )
            required_binding_fields = [
                "player_id",
                "phase",
                "round_id",
                "prompt_path",
                "prompt_sha256",
                "command_sha256",
                "working_directory",
            ]
            if record.data.get("approval_binding_version") == "exact-inputs-2":
                required_binding_fields.extend(
                    ["participant_identity", "participant_profile_id", "execution_profile_id"]
                )
                if config_uses_execution_profiles(records):
                    required_binding_fields.append("execution_profile_sha256_or_null")
            else:
                required_binding_fields.append("actor_identity")
            for field in required_binding_fields:
                if not isinstance(binding.get(field), str) or not binding.get(field):
                    messages.append(
                        f"{record.path}:{record.heading_line}: approval binding for {participant} lacks {field}"
                    )
            prompt_path = binding.get("prompt_path")
            prompt_sha = binding.get("prompt_sha256")
            if not isinstance(prompt_path, str) or not isinstance(prompt_sha, str):
                messages.append(f"{record.path}:{record.heading_line}: approval binding for {participant} lacks prompt path/sha256")
                continue
            if not SHA256_RE.fullmatch(prompt_sha) or not SHA256_RE.fullmatch(str(binding.get("command_sha256") or "")):
                messages.append(f"{record.path}:{record.heading_line}: approval binding for {participant} has invalid sha256")
            execution_profile_id = binding.get("execution_profile_id")
            approved_execution_profile_sha = binding.get("execution_profile_sha256_or_null")
            if isinstance(execution_profile_id, str) and config_uses_execution_profiles(records):
                current_execution_profile_sha = resolved_execution_profile_sha256(
                    records, execution_profile_id
                )
                if (
                    not isinstance(approved_execution_profile_sha, str)
                    or not SHA256_RE.fullmatch(approved_execution_profile_sha)
                    or current_execution_profile_sha != approved_execution_profile_sha
                ):
                    messages.append(
                        f"{record.path}:{record.heading_line}: approval ExecutionProfile sha256 differs "
                        f"for {participant}: {execution_profile_id}"
                    )
            try:
                approved_prompt = _payload_path(run, prompt_path, "prompt_path")
            except ValueError as exc:
                messages.append(f"{record.path}:{record.heading_line}: {exc}")
                continue
            if not approved_prompt.is_file():
                messages.append(
                    f"{record.path}:{record.heading_line}: approved prompt not found for {participant}: {prompt_path}"
                )
            elif sha256_file(approved_prompt) != prompt_sha:
                messages.append(
                    f"{record.path}:{record.heading_line}: approved prompt sha256 mismatch for {participant}: {prompt_path}"
                )
            artifact_id = binding.get("artifact_version_id_or_null")
            approved_artifact_sha = binding.get("artifact_sha256_or_null")
            if approved_artifact_sha is not None and (
                not isinstance(approved_artifact_sha, str) or not SHA256_RE.fullmatch(approved_artifact_sha)
            ):
                messages.append(f"{record.path}:{record.heading_line}: approval artifact sha256 is invalid for {participant}")
                continue
            if isinstance(artifact_id, str) and approved_artifact_sha is not None:
                artifact = artifact_by_id(records, artifact_id)
                if artifact is None:
                    messages.append(
                        f"{record.path}:{record.heading_line}: approved artifact not found for {participant}: {artifact_id}"
                    )
                    continue
                try:
                    current_artifact_sha = verified_artifact_sha256(run, artifact)
                except ValueError as exc:
                    messages.append(f"{record.path}:{record.heading_line}: {exc}")
                    continue
                if current_artifact_sha != approved_artifact_sha:
                    messages.append(
                        f"{record.path}:{record.heading_line}: approved artifact sha256 differs for {participant}: {artifact_id}"
                    )

    messages.extend(approval_anchor_messages(run, records))
    return messages


def approval_anchor_messages(
    run: Path,
    records: list[Record],
    selected_records: list[Record] | None = None,
) -> list[str]:
    """Verify approval record digests against the validated run-event journal."""
    targets = selected_records if selected_records is not None else records_by_type(records, "OperatorApproval")
    if not targets:
        return []
    if not run_requires_event_integrity_v2(run):
        return []
    journal_messages = run_event_messages(run)
    messages = [f"approval journal is invalid: {message}" for message in journal_messages]
    anchored_approvals: dict[str, str] = {}
    run_events = read_run_events(run)
    for event in run_events:
        if event.get("event_type") != "operator_approval_recorded":
            continue
        details = event.get("details")
        if not isinstance(details, dict):
            continue
        approval_id = details.get("operator_approval_id")
        approval_sha = details.get("operator_approval_sha256")
        if isinstance(approval_id, str) and isinstance(approval_sha, str):
            anchored_approvals[approval_id] = approval_sha
    for record in targets:
        recorded_sha = anchored_approvals.get(record.record_id)
        if recorded_sha is None:
            messages.append(
                f"{record.path}:{record.heading_line}: OperatorApproval has no run-event digest anchor"
            )
        elif canonical_json_sha256(record.data) != recorded_sha:
            messages.append(
                f"{record.path}:{record.heading_line}: OperatorApproval sha256 differs from run-event anchor"
            )
    return messages
