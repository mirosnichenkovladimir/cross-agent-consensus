"""Typed participant and execution-profile resolution."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from cross_agent_consensus.models import (
    ExecutionProfile,
    ParticipantIdentity,
    ParticipantProfile,
    Record,
    ResolvedInvocationProfile,
)
from cross_agent_consensus.records import first_record


PARTICIPANT_ROLES = {
    "orchestrator",
    "author",
    "reviewer",
    "validator",
    "human_supervisor",
}
PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PHASE_PARTICIPANT_ROLES = {
    "author": "author",
    "author-response": "author",
    "reviewer": "reviewer",
    "rereview": "reviewer",
    "validator": "validator",
    "final-report": "orchestrator",
}


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_list(value: object) -> list[str] | None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return list(value)


def _valid_profile_id(value: object) -> bool:
    return isinstance(value, str) and PROFILE_ID_RE.fullmatch(value) is not None


def _contains_option(command: list[str], names: set[str]) -> bool:
    return any(argument in names or any(argument.startswith(f"{name}=") for name in names) for argument in command)


def _insert_before_stdin_marker(command: list[str], arguments: list[str]) -> list[str]:
    result = list(command)
    index = len(result) - 1 if result and result[-1] == "-" else len(result)
    result[index:index] = arguments
    return result


def _codex_config_keys(command: list[str]) -> set[str]:
    keys: set[str] = set()
    index = 0
    while index < len(command):
        argument = command[index]
        value: str | None = None
        if argument in {"-c", "--config"} and index + 1 < len(command):
            value = command[index + 1]
            index += 1
        elif argument.startswith("--config=") or argument.startswith("-c="):
            value = argument.split("=", 1)[1]
        if value and "=" in value:
            keys.add(value.split("=", 1)[0].strip())
        index += 1
    return keys


def effective_execution_command(
    adapter_id: str,
    command: list[str],
    model_id: str | None,
    reasoning_effort: str | None,
) -> tuple[list[str], list[str]]:
    """Translate declarative model settings into deterministic provider argv."""

    errors: list[str] = []
    additions: list[str] = []
    codex_config_keys = _codex_config_keys(command) if adapter_id == "codex-cli" else set()
    if model_id is not None:
        if adapter_id not in {"codex-cli", "claude-cli"}:
            errors.append(f"model is not supported by adapter {adapter_id}")
        elif _contains_option(command, {"-m", "--model"}) or "model" in codex_config_keys:
            errors.append("model must be declared either in model or command, not both")
        else:
            additions.extend(["--model", model_id])
    if reasoning_effort is not None:
        if adapter_id == "codex-cli":
            if "model_reasoning_effort" in codex_config_keys:
                errors.append("reasoning_effort must be declared either in reasoning_effort or command, not both")
            else:
                additions.extend(["--config", f"model_reasoning_effort={json.dumps(reasoning_effort)}"])
        elif adapter_id == "claude-cli":
            if _contains_option(command, {"--effort"}):
                errors.append("reasoning_effort must be declared either in reasoning_effort or command, not both")
            else:
                additions.extend(["--effort", reasoning_effort])
        else:
            errors.append(f"reasoning_effort is not supported by adapter {adapter_id}")
    return _insert_before_stdin_marker(command, additions), errors


def parse_profile_definitions(
    effective: dict[str, Any],
) -> tuple[
    dict[str, ParticipantProfile],
    dict[str, ExecutionProfile],
    dict[str, ParticipantIdentity],
    list[str],
]:
    """Parse all profile definitions and return deterministic diagnostics."""

    errors: list[str] = []
    participant_profiles: dict[str, ParticipantProfile] = {}
    execution_profiles: dict[str, ExecutionProfile] = {}
    participant_identities: dict[str, ParticipantIdentity] = {}

    raw_participant_profiles = effective.get("participant_profiles", {})
    if not isinstance(raw_participant_profiles, dict):
        errors.append("participant_profiles must be a mapping")
        raw_participant_profiles = {}
    for profile_id, raw_profile in raw_participant_profiles.items():
        prefix = f"participant_profiles.{profile_id}"
        if not _valid_profile_id(profile_id):
            errors.append(f"{prefix}: identifier must use lowercase letters, digits, '.', '_', or '-'")
            continue
        if not isinstance(raw_profile, dict):
            errors.append(f"{prefix} must be a mapping")
            continue
        unknown = sorted(set(raw_profile) - {"role", "instructions"})
        if unknown:
            errors.append(f"{prefix} unknown keys: {', '.join(unknown)}")
        role = raw_profile.get("role")
        if role not in PARTICIPANT_ROLES:
            errors.append(f"{prefix}.role must be one of {', '.join(sorted(PARTICIPANT_ROLES))}")
            continue
        instructions = _string_list(raw_profile.get("instructions", []))
        if instructions is None:
            errors.append(f"{prefix}.instructions must be a list of strings")
            continue
        participant_profiles[profile_id] = ParticipantProfile(profile_id, str(role), instructions)

    raw_execution_profiles = effective.get("execution_profiles", {})
    if not isinstance(raw_execution_profiles, dict):
        errors.append("execution_profiles must be a mapping")
        raw_execution_profiles = {}
    for profile_id, raw_profile in raw_execution_profiles.items():
        prefix = f"execution_profiles.{profile_id}"
        if not _valid_profile_id(profile_id):
            errors.append(f"{prefix}: identifier must use lowercase letters, digits, '.', '_', or '-'")
            continue
        if not isinstance(raw_profile, dict):
            errors.append(f"{prefix} must be a mapping")
            continue
        allowed = {
            "adapter",
            "command",
            "model",
            "reasoning_effort",
            "prompt_transport",
            "output_mode",
            "supports_resume",
            "env",
        }
        unknown = sorted(set(raw_profile) - allowed)
        if unknown:
            errors.append(f"{prefix} unknown keys: {', '.join(unknown)}")
        adapter_id = raw_profile.get("adapter")
        command = _string_list(raw_profile.get("command", []))
        prompt_transport = raw_profile.get("prompt_transport")
        output_mode = raw_profile.get("output_mode")
        supports_resume = raw_profile.get("supports_resume")
        env_allowlist = _string_list(raw_profile.get("env", []))
        model_id = raw_profile.get("model")
        reasoning_effort = raw_profile.get("reasoning_effort")
        if not isinstance(adapter_id, str) or not adapter_id:
            errors.append(f"{prefix}.adapter must be a non-empty string")
            continue
        if command is None or (adapter_id != "manual" and not command):
            errors.append(f"{prefix}.command must be a non-empty argv list unless adapter is manual")
            continue
        if any(not argument or "\0" in argument for argument in command):
            errors.append(f"{prefix}.command entries must be non-empty strings without NUL bytes")
            continue
        if not isinstance(prompt_transport, str) or not prompt_transport:
            errors.append(f"{prefix}.prompt_transport must be a non-empty string")
            continue
        if not isinstance(output_mode, str) or not output_mode:
            errors.append(f"{prefix}.output_mode must be a non-empty string")
            continue
        if not isinstance(supports_resume, bool):
            errors.append(f"{prefix}.supports_resume must be a boolean")
            continue
        if env_allowlist is None or any(ENV_NAME_RE.fullmatch(name) is None for name in env_allowlist):
            errors.append(f"{prefix}.env must contain environment variable names only")
            continue
        if model_id is not None and not isinstance(model_id, str):
            errors.append(f"{prefix}.model must be a string when present")
            continue
        if reasoning_effort is not None and not isinstance(reasoning_effort, str):
            errors.append(f"{prefix}.reasoning_effort must be a string when present")
            continue
        from cross_agent_consensus.invocation.readiness import secret_argv_errors

        command_secret_errors = secret_argv_errors(command)
        if command_secret_errors:
            errors.extend(f"{prefix}.command: {message}" for message in command_secret_errors)
            continue
        try:
            from cross_agent_consensus.invocation.adapters import get_player_adapter

            adapter = get_player_adapter(adapter_id)
            capabilities = adapter.probe(command)
        except ValueError as exc:
            errors.append(f"{prefix}.adapter: {exc}")
            continue
        if adapter_id != capabilities.player_id:
            errors.append(
                f"{prefix}.adapter must use the stable adapter id {capabilities.player_id!r}, not alias {adapter_id!r}"
            )
        if prompt_transport not in capabilities.prompt_transports:
            errors.append(
                f"{prefix}.prompt_transport {prompt_transport!r} is not supported by {capabilities.player_id}"
            )
        if output_mode not in capabilities.output_modes:
            errors.append(f"{prefix}.output_mode {output_mode!r} is not supported by {capabilities.player_id}")
        if supports_resume and not capabilities.supports_resume:
            errors.append(f"{prefix}.supports_resume is true but adapter {capabilities.player_id} cannot resume")
        effective_command, command_setting_errors = effective_execution_command(
            capabilities.player_id,
            command,
            model_id,
            reasoning_effort,
        )
        if command_setting_errors:
            errors.extend(f"{prefix}.{message}" for message in command_setting_errors)
            continue
        if hasattr(adapter, "command_requests_json"):
            command_output_mode = (
                "stream_json"
                if adapter.command_requests_json(effective_command)
                else "raw_stdout"
            )
            if output_mode != command_output_mode:
                errors.append(
                    f"{prefix}.output_mode {output_mode!r} contradicts command output mode {command_output_mode!r}"
                )
                continue
        execution_profiles[profile_id] = ExecutionProfile(
            execution_profile_id=profile_id,
            adapter_id=capabilities.player_id,
            command=effective_command,
            model_id_or_null=model_id,
            reasoning_effort_or_null=reasoning_effort,
            prompt_transport=prompt_transport,
            output_mode=output_mode,
            supports_resume=supports_resume,
            env_allowlist=env_allowlist,
        )

    raw_identities = effective.get("participant_identities", {})
    if not isinstance(raw_identities, dict):
        errors.append("participant_identities must be a mapping")
        raw_identities = {}
    for identity, raw_binding in raw_identities.items():
        prefix = f"participant_identities.{identity}"
        if not _valid_profile_id(identity):
            errors.append(f"{prefix}: identifier must use lowercase letters, digits, '.', '_', or '-'")
            continue
        if not isinstance(raw_binding, dict):
            errors.append(f"{prefix} must be a mapping")
            continue
        unknown = sorted(set(raw_binding) - {"participant_profile_id", "execution_profile_id"})
        if unknown:
            errors.append(f"{prefix} unknown keys: {', '.join(unknown)}")
        participant_profile_id = raw_binding.get("participant_profile_id")
        execution_profile_id = raw_binding.get("execution_profile_id")
        if not isinstance(participant_profile_id, str) or participant_profile_id not in participant_profiles:
            errors.append(f"{prefix}.participant_profile_id references an unknown ParticipantProfile")
            continue
        if not isinstance(execution_profile_id, str) or execution_profile_id not in execution_profiles:
            errors.append(f"{prefix}.execution_profile_id references an unknown ExecutionProfile")
            continue
        participant_identities[identity] = ParticipantIdentity(
            participant_identity=identity,
            participant_profile_id=participant_profile_id,
            execution_profile_id=execution_profile_id,
        )

    participants = _mapping(effective.get("participants"))
    selected_roles: list[tuple[str, str]] = []
    for field, role in (("orchestrator", "orchestrator"), ("author", "author")):
        identity = participants.get(field)
        if isinstance(identity, str):
            selected_roles.append((identity, role))
    for field, role in (("reviewers", "reviewer"), ("validators", "validator")):
        values = participants.get(field, [])
        if isinstance(values, list):
            selected_roles.extend((identity, role) for identity in values if isinstance(identity, str))
    human_supervisor = participants.get("human_supervisor")
    if isinstance(human_supervisor, str) and human_supervisor != "none":
        selected_roles.append((human_supervisor, "human_supervisor"))
    selected_identity_counts = {
        identity: sum(candidate == identity for candidate, _ in selected_roles)
        for identity, _ in selected_roles
    }
    reported_duplicates: set[str] = set()
    for identity, expected_role in selected_roles:
        if selected_identity_counts[identity] > 1:
            if identity not in reported_duplicates:
                errors.append(
                    f"participant {identity!r} is selected more than once; participant identities must be distinct"
                )
                reported_duplicates.add(identity)
            continue
        binding = participant_identities.get(identity)
        if binding is None:
            errors.append(f"participants selects {identity!r} but participant_identities has no binding")
            continue
        profile = participant_profiles[binding.participant_profile_id]
        if profile.role != expected_role:
            errors.append(
                f"participant {identity!r} is selected as {expected_role} but ParticipantProfile "
                f"{profile.participant_profile_id!r} declares role {profile.role!r}"
            )

    return participant_profiles, execution_profiles, participant_identities, errors


def resolved_profile_payload(effective: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Return ConfigResolution payloads for identities and execution profiles."""

    participant_profiles, execution_profiles, identities, errors = parse_profile_definitions(effective)
    resolved_identities = {
        identity: {
            "participant_identity": binding.participant_identity,
            "participant_profile_id": binding.participant_profile_id,
            "role": participant_profiles[binding.participant_profile_id].role,
            "instructions": participant_profiles[binding.participant_profile_id].instructions,
            "execution_profile_id": binding.execution_profile_id,
        }
        for identity, binding in sorted(identities.items())
    }
    resolved_execution_profiles = {
        profile_id: {
            "execution_profile_id": profile.execution_profile_id,
            "adapter_id": profile.adapter_id,
            "command": profile.command,
            "model_id_or_null": profile.model_id_or_null,
            "reasoning_effort_or_null": profile.reasoning_effort_or_null,
            "prompt_transport": profile.prompt_transport,
            "output_mode": profile.output_mode,
            "supports_resume": profile.supports_resume,
            "env_allowlist": profile.env_allowlist,
        }
        for profile_id, profile in sorted(execution_profiles.items())
    }
    return resolved_identities, resolved_execution_profiles, errors


def invocation_profile_from_records(
    records: list[Record],
    participant_identity: str,
) -> ResolvedInvocationProfile | None:
    """Resolve participant profile, execution profile, adapter, and argv."""

    resolution = first_record(records, "ConfigResolution")
    if resolution is None:
        return None
    identities = resolution.data.get("resolved_participant_identities")
    profiles = resolution.data.get("resolved_execution_profiles")
    if not isinstance(identities, dict) or not isinstance(profiles, dict):
        return None
    binding = identities.get(participant_identity)
    if not isinstance(binding, dict):
        return None
    participant_profile_id = binding.get("participant_profile_id")
    execution_profile_id = binding.get("execution_profile_id")
    if not isinstance(participant_profile_id, str) or not isinstance(execution_profile_id, str):
        return None
    execution = profiles.get(execution_profile_id)
    if not isinstance(execution, dict):
        return None
    adapter_id = execution.get("adapter_id")
    command = execution.get("command")
    prompt_transport = execution.get("prompt_transport")
    output_mode = execution.get("output_mode")
    env_allowlist = execution.get("env_allowlist")
    if (
        not isinstance(adapter_id, str)
        or not isinstance(command, list)
        or not isinstance(prompt_transport, str)
        or not isinstance(output_mode, str)
        or not isinstance(env_allowlist, list)
    ):
        return None
    if not all(isinstance(item, str) for item in command + env_allowlist):
        return None
    return ResolvedInvocationProfile(
        participant_profile_id=participant_profile_id,
        execution_profile_id=execution_profile_id,
        adapter_id=adapter_id,
        command=list(command),
        prompt_transport=prompt_transport,
        output_mode=output_mode,
        env_allowlist=list(env_allowlist),
    )


def participant_profile_role_from_records(
    records: list[Record], participant_identity: str
) -> str | None:
    resolution = first_record(records, "ConfigResolution")
    identities = resolution.data.get("resolved_participant_identities") if resolution else None
    binding = identities.get(participant_identity) if isinstance(identities, dict) else None
    role = binding.get("role") if isinstance(binding, dict) else None
    return role if isinstance(role, str) else None


def participant_phase_role_errors(
    records: list[Record], participant_identity: str, phase: str | None
) -> list[str]:
    expected_role = PHASE_PARTICIPANT_ROLES.get(str(phase)) if phase else None
    if expected_role is None:
        return []
    actual_role = participant_profile_role_from_records(records, participant_identity)
    if actual_role is None or actual_role == expected_role:
        return []
    return [
        f"participant role mismatch: phase {phase!r} requires ParticipantProfile role "
        f"{expected_role!r}, but {participant_identity!r} is bound to {actual_role!r}"
    ]


def _uses_config_schema_2(records: list[Record]) -> bool:
    resolution = first_record(records, "ConfigResolution")
    return bool(
        resolution
        and resolution.data.get("config_schema_version") == "cross-agent-consensus-config-2"
    )


def bind_recorded_invocation_profile(
    records: list[Record],
    args: Any,
    command: list[str],
) -> tuple[list[str], list[str]]:
    """Bind CLI invocation arguments to the run's recorded profile."""

    profile = invocation_profile_from_records(records, str(args.actor))
    if profile is None:
        if _uses_config_schema_2(records):
            return command, [
                f"participant {args.actor!r} has no complete profile binding in ConfigResolution"
            ]
        from cross_agent_consensus.invocation.adapters import get_player_adapter

        legacy_adapter = get_player_adapter(str(args.player))
        args.prompt_transport = "manual" if args.player == "manual" else "stdin"
        args.output_mode = (
            "stream_json"
            if hasattr(legacy_adapter, "command_requests_json")
            and legacy_adapter.command_requests_json(command)
            else ("manual_handoff" if args.player == "manual" else "raw_stdout")
        )
        # Runs created before schema 2 inherited the complete parent environment.
        # Record the inherited names so command.json describes the compatibility launch.
        args.env_allowlist = sorted(os.environ)
        return command, []
    errors: list[str] = []
    explicit_participant_profile = getattr(args, "participant_profile_id", None)
    explicit_execution_profile = getattr(args, "execution_profile_id", None)
    if explicit_participant_profile not in {None, profile.participant_profile_id}:
        errors.append(
            f"participant profile mismatch: run binds {args.actor!r} to {profile.participant_profile_id!r}"
        )
    if explicit_execution_profile not in {None, profile.execution_profile_id}:
        errors.append(
            f"execution profile mismatch: run binds {args.actor!r} to {profile.execution_profile_id!r}"
        )
    if args.player != profile.adapter_id:
        errors.append(
            f"adapter mismatch: ExecutionProfile {profile.execution_profile_id!r} requires {profile.adapter_id!r}, got {args.player!r}"
        )
    if command and command != profile.command:
        errors.append(
            f"command mismatch: argv must come from ExecutionProfile {profile.execution_profile_id!r}"
        )
    args.participant_profile_id = profile.participant_profile_id
    args.execution_profile_id = profile.execution_profile_id
    args.prompt_transport = profile.prompt_transport
    args.output_mode = profile.output_mode
    args.env_allowlist = profile.env_allowlist
    errors.extend(
        participant_phase_role_errors(
            records,
            str(args.actor),
            getattr(args, "phase", None),
        )
    )
    return (profile.command if not command else command), errors
