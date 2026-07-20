"""Layered configuration resolution for cross-agent-consensus."""

from __future__ import annotations

import argparse
import copy
import os
import re
from pathlib import Path
from typing import Any, Iterable

from cross_agent_consensus.io import read_cac_version, sha256_file, skill_root
from cross_agent_consensus.markdown_records import frontmatter, parse_yaml_subset
from cross_agent_consensus.models import ConfigResolution
from cross_agent_consensus.profiles import parse_profile_definitions, resolved_profile_payload


CONFIG_SCHEMA_VERSION = "cross-agent-consensus-config-2"
SUPPORTED_CONFIG_SCHEMA_VERSIONS = {CONFIG_SCHEMA_VERSION}
TASK_SCHEMA_VERSION = "cross-agent-consensus-task-1"
CAC_VERSION = read_cac_version()
PEEK_CONFIG_DEFAULTS = {
    "interval_seconds": 180.0,
    "tail": 80,
    "snippet_chars": 160,
    "monitor_stale_seconds": 30.0,
}


def is_plain_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def deep_merge_config(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge_config(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def flatten_values(data: Any, prefix: str = "") -> dict[str, Any]:
    if not isinstance(data, dict):
        return {prefix: data} if prefix else {}
    flattened: dict[str, Any] = {}
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            child = flatten_values(value, path)
            if child:
                flattened.update(child)
            else:
                flattened[path] = {}
        else:
            flattened[path] = value
    return flattened


def get_nested(data: dict[str, Any], path: str, default: Any = None) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def set_nested(data: dict[str, Any], path: str, value: Any) -> None:
    current = data
    parts = path.split(".")
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = value


def canonical_config(data: dict[str, Any]) -> dict[str, Any]:
    if not data:
        return {}
    result = copy.deepcopy(data)
    if "run_root" in result:
        set_nested(result, "defaults.run_root", result.pop("run_root"))
    if "profile" in result:
        set_nested(result, "defaults.profile", result.pop("profile"))
    if "round_limits" in result:
        set_nested(result, "defaults.round_limits", result.pop("round_limits"))
    return result


def _manual_identity_binding(result: dict[str, Any], identity: str, role: str) -> None:
    identities = result.setdefault("participant_identities", {})
    if not isinstance(identities, dict) or identity in identities:
        return
    identities[identity] = {
        "participant_profile_id": f"{role}-default",
        "execution_profile_id": "manual-default",
    }


def _translate_legacy_participants(result: dict[str, Any]) -> None:
    participants = result.get("participants")
    if not isinstance(participants, dict):
        return
    for field, role in (("orchestrator", "orchestrator"), ("author", "author")):
        identity = participants.get(field)
        if isinstance(identity, str):
            _manual_identity_binding(result, identity, role)
    for field, role in (("reviewers", "reviewer"), ("validators", "validator")):
        identities = participants.get(field)
        if isinstance(identities, list):
            for identity in identities:
                if isinstance(identity, str):
                    _manual_identity_binding(result, identity, role)
    human = participants.get("human_supervisor")
    if isinstance(human, str) and human != "none":
        _manual_identity_binding(result, human, "human_supervisor")


def legacy_adapter_for_command(command: list[str]) -> tuple[str, str]:
    """Classify argv stored by a pre-profile ConfigResolution record."""

    executable = Path(command[0]).name if command else ""
    if executable == "codex":
        return "codex-cli", "stream_json" if "--json" in command else "raw_stdout"
    if executable == "claude":
        has_stream_json = "--output-format=stream-json" in command or any(
            command[index] == "--output-format" and index + 1 < len(command) and command[index + 1] == "stream-json"
            for index in range(len(command))
        )
        return "claude-cli", "stream_json" if has_stream_json else "raw_stdout"
    if executable == "kimi":
        has_stream_json = "--output-format=stream-json" in command or any(
            command[index] == "--output-format"
            and index + 1 < len(command)
            and command[index + 1] == "stream-json"
            for index in range(len(command))
        )
        return "kimi-cli", "stream_json" if has_stream_json else "raw_stdout"
    return "generic-cli", "raw_stdout"


def source_record(layer: str, path: Path | None, present: bool, data: dict[str, Any], note: str | None = None) -> dict[str, Any]:
    file_hash = sha256_file(path) if path is not None and path.is_file() else None
    record: dict[str, Any] = {
        "layer": layer,
        "path": str(path) if path is not None else None,
        "present": present,
        "sha256_or_null": file_hash,
    }
    if note:
        record["note"] = note
    return record


def load_yaml_mapping(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    duplicates = duplicate_yaml_mapping_paths(text)
    if duplicates:
        raise ValueError(f"duplicate mapping identifiers: {', '.join(duplicates)}")
    data = parse_yaml_subset(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a YAML mapping")
    return data


def duplicate_yaml_mapping_paths(text: str) -> list[str]:
    """Return repeated mapping paths from the supported YAML subset."""

    parents: list[tuple[int, str]] = []
    seen: set[tuple[tuple[str, ...], str]] = set()
    duplicates: list[str] = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("-") or ":" not in stripped:
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        key = stripped.split(":", 1)[0].strip()
        while parents and parents[-1][0] >= indent:
            parents.pop()
        parent_path = tuple(parent_key for _, parent_key in parents)
        marker = (parent_path, key)
        full_path = ".".join((*parent_path, key))
        if marker in seen and full_path not in duplicates:
            duplicates.append(full_path)
        seen.add(marker)
        if not stripped.split(":", 1)[1].strip():
            parents.append((indent, key))
    return duplicates


def dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        expanded = path.expanduser()
        key = str(expanded.resolve()) if expanded.exists() else str(expanded)
        if key not in seen:
            unique.append(expanded)
            seen.add(key)
    return unique


def default_user_config_candidates() -> list[Path]:
    home = Path.home()
    candidates = [
        skill_root() / "config" / "config.local.yaml",
        Path(os.environ.get("CODEX_HOME", home / ".codex")) / "skills" / "cross-agent-consensus" / "config" / "config.local.yaml",
        Path(os.environ.get("CLAUDE_HOME", home / ".claude")) / "skills" / "cross-agent-consensus" / "config" / "config.local.yaml",
        Path(os.environ.get("KIMI_CODE_HOME", home / ".kimi-code")) / "skills" / "cross-agent-consensus" / "config" / "config.local.yaml",
        Path(os.environ.get("HERMES_HOME", home / ".hermes")) / "skills" / "cross-agent-consensus" / "config" / "config.local.yaml",
    ]
    return dedupe_paths(candidates)


def discover_user_config() -> tuple[Path | None, list[dict[str, Any]]]:
    explicit = os.environ.get("CROSS_AGENT_CONSENSUS_CONFIG")
    if explicit:
        path = Path(explicit).expanduser()
        return (path if path.is_file() else None), [
            source_record("user_local", path, path.is_file(), {}, "from CROSS_AGENT_CONSENSUS_CONFIG")
        ]
    diagnostics: list[dict[str, Any]] = []
    for path in default_user_config_candidates():
        present = path.is_file()
        diagnostics.append(source_record("user_local", path, present, {}))
        if present:
            return path, diagnostics
    return None, diagnostics


def find_project_config(start: Path) -> tuple[Path | None, str]:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    while True:
        candidate = current / ".cross-agent-consensus.yaml"
        if candidate.is_file():
            return candidate, "found"
        if (current / ".git").exists():
            return None, f"stopped at VCS root {current}"
        if current.parent == current:
            return None, "filesystem root reached"
        current = current.parent


def contains_enabled_unattended(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    for key, value in data.items():
        if key == "unattended_invocation":
            if value is True:
                return True
            if isinstance(value, dict) and value.get("enabled") is True:
                return True
        if contains_enabled_unattended(value):
            return True
    return False


def find_secret_like_values(data: Any, path: str = "") -> list[str]:
    warnings: list[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            child_path = f"{path}.{key}" if path else str(key)
            lower = str(key).lower()
            if any(token in lower for token in ["token", "password", "secret", "api_key", "apikey"]):
                warnings.append(f"{child_path}: secret-looking key must not be stored in CAC config")
            warnings.extend(find_secret_like_values(value, child_path))
    elif isinstance(data, str):
        compact = re.sub(r"[^A-Za-z0-9]", "", data)
        if not any(character.isspace() for character in data) and len(compact) >= 32 and len(set(compact)) >= 16:
            warnings.append(f"{path}: value looks like a high-entropy secret")
    elif isinstance(data, list):
        for index, item in enumerate(data):
            warnings.extend(find_secret_like_values(item, f"{path}[{index}]"))
    return warnings


def validate_config_shape(data: dict[str, Any], *, source: str, persistent: bool, strict: bool) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    errors: list[str] = []
    allowed_top = {
        "schema_version",
        "defaults",
        "participants",
        "participant_profiles",
        "execution_profiles",
        "participant_identities",
        "invocation",
        "feedback",
    }
    schema_version = data.get("schema_version")
    if schema_version not in SUPPORTED_CONFIG_SCHEMA_VERSIONS:
        errors.append(f"{source}: schema_version must be {CONFIG_SCHEMA_VERSION}")
    if "reviewer_clis" in data:
        errors.append(
            f"{source}: reviewer_clis was removed in 0.13.0; use execution_profiles and participant_identities"
        )
    unknown = sorted(set(data) - allowed_top)
    if unknown:
        message = f"{source}: unknown config keys: {', '.join(unknown)}"
        (errors if strict else warnings).append(message)
    defaults = data.get("defaults", {})
    if defaults is not None and not isinstance(defaults, dict):
        errors.append(f"{source}: defaults must be a mapping")
    elif isinstance(defaults, dict):
        allowed_defaults = {"profile", "run_root", "round_limits"}
        unknown_defaults = sorted(set(defaults) - allowed_defaults)
        if unknown_defaults:
            message = f"{source}: unknown defaults keys: {', '.join(unknown_defaults)}"
            (errors if strict else warnings).append(message)
        round_limits = defaults.get("round_limits", {})
        if round_limits is not None and not isinstance(round_limits, dict):
            errors.append(f"{source}: defaults.round_limits must be a mapping")
        elif isinstance(round_limits, dict):
            allowed_limits = {
                "max_fresh_review_rounds",
                "max_fresh_review_rounds_without_human_approval",
                "max_launched_review_batches",
                "max_remediation_rounds_per_finding",
            }
            unknown_limits = sorted(set(round_limits) - allowed_limits)
            if unknown_limits:
                message = f"{source}: unknown defaults.round_limits keys: {', '.join(unknown_limits)}"
                (errors if strict else warnings).append(message)
            for key in allowed_limits:
                value = round_limits.get(key)
                if value is not None and not isinstance(value, int):
                    errors.append(f"{source}: defaults.round_limits.{key} must be an integer")
    participants = data.get("participants", {})
    if participants is not None and not isinstance(participants, dict):
        errors.append(f"{source}: participants must be a mapping")
    elif isinstance(participants, dict):
        allowed_participants = {"orchestrator", "author", "reviewers", "validators", "human_supervisor"}
        unknown_participants = sorted(set(participants) - allowed_participants)
        if unknown_participants:
            message = f"{source}: unknown participants keys: {', '.join(unknown_participants)}"
            (errors if strict else warnings).append(message)
        for field in ["reviewers", "validators"]:
            identities = participants.get(field)
            if identities is not None and not (
                isinstance(identities, list) and all(isinstance(item, str) for item in identities)
            ):
                errors.append(f"{source}: participants.{field} must be a list of strings")
        for key in ["orchestrator", "author", "human_supervisor"]:
            value = participants.get(key)
            if value is not None and not isinstance(value, str):
                errors.append(f"{source}: participants.{key} must be a string")
    invocation = data.get("invocation", {})
    if invocation is not None and not isinstance(invocation, dict):
        errors.append(f"{source}: invocation must be a mapping")
    elif isinstance(invocation, dict):
        allowed_invocation = {"require_invocation_ready", "direct_reviewer_cli", "unattended_invocation", "peek"}
        unknown_invocation = sorted(set(invocation) - allowed_invocation)
        if unknown_invocation:
            message = f"{source}: unknown invocation keys: {', '.join(unknown_invocation)}"
            (errors if strict else warnings).append(message)
        if invocation.get("direct_reviewer_cli") not in {None, "explicit_only"}:
            errors.append(f"{source}: invocation.direct_reviewer_cli must be explicit_only")
        peek = invocation.get("peek", {})
        if peek is not None and not isinstance(peek, dict):
            errors.append(f"{source}: invocation.peek must be a mapping")
        elif isinstance(peek, dict):
            allowed_peek = {"interval_seconds", "tail", "snippet_chars", "monitor_stale_seconds"}
            unknown_peek = sorted(set(peek) - allowed_peek)
            if unknown_peek:
                message = f"{source}: unknown invocation.peek keys: {', '.join(unknown_peek)}"
                (errors if strict else warnings).append(message)
            interval = peek.get("interval_seconds")
            if interval is not None and (not is_plain_number(interval) or float(interval) <= 0):
                errors.append(f"{source}: invocation.peek.interval_seconds must be a number > 0")
            tail = peek.get("tail")
            if tail is not None and (not isinstance(tail, int) or isinstance(tail, bool) or not 1 <= tail <= 1000):
                errors.append(f"{source}: invocation.peek.tail must be an integer between 1 and 1000")
            snippet_chars = peek.get("snippet_chars")
            if (
                snippet_chars is not None
                and (not isinstance(snippet_chars, int) or isinstance(snippet_chars, bool) or not 40 <= snippet_chars <= 500)
            ):
                errors.append(f"{source}: invocation.peek.snippet_chars must be an integer between 40 and 500")
            stale = peek.get("monitor_stale_seconds")
            if stale is not None and (not is_plain_number(stale) or float(stale) <= 0):
                errors.append(f"{source}: invocation.peek.monitor_stale_seconds must be a number > 0")
        unattended = invocation.get("unattended_invocation")
        if isinstance(unattended, dict) and unattended.get("enabled") is True:
            scope = unattended.get("scope")
            if not (isinstance(scope, list) and scope and all(isinstance(item, str) for item in scope)):
                errors.append(f"{source}: invocation.unattended_invocation.scope must be a non-empty string list when enabled")
    feedback = data.get("feedback", {})
    if feedback is not None and not isinstance(feedback, dict):
        errors.append(f"{source}: feedback must be a mapping")
    elif isinstance(feedback, dict):
        allowed_feedback = {"enabled"}
        unknown_feedback = sorted(set(feedback) - allowed_feedback)
        if unknown_feedback:
            message = f"{source}: unknown feedback keys: {', '.join(unknown_feedback)}"
            (errors if strict else warnings).append(message)
        enabled = feedback.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            errors.append(f"{source}: feedback.enabled must be a boolean")
    for field in ["participant_profiles", "execution_profiles", "participant_identities"]:
        value = data.get(field, {})
        if value is not None and not isinstance(value, dict):
            errors.append(f"{source}: {field} must be a mapping")
    if persistent and contains_enabled_unattended(data):
        errors.append(f"{source}: persistent config must not enable unattended_invocation")
    secret_messages = [f"{source}: {message}" for message in find_secret_like_values(data)]
    if persistent:
        errors.extend(secret_messages)
    else:
        warnings.extend(secret_messages)
    return warnings, errors


def validate_task_file_shape(data: dict[str, Any], *, source: str, strict: bool) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    errors: list[str] = []
    allowed_top = {"schema_version", "task", "config", "review_scope"}
    if data.get("schema_version") != TASK_SCHEMA_VERSION:
        errors.append(f"{source}: schema_version must be {TASK_SCHEMA_VERSION}")
    unknown = sorted(set(data) - allowed_top)
    if unknown:
        message = f"{source}: unknown task-file keys: {', '.join(unknown)}"
        (errors if strict else warnings).append(message)
    if "task" in data and not isinstance(data["task"], dict):
        errors.append(f"{source}: task must be a mapping")
    if "config" in data:
        if not isinstance(data["config"], dict):
            errors.append(f"{source}: config must be a mapping")
        else:
            config = canonical_config({"schema_version": CONFIG_SCHEMA_VERSION, **data["config"]})
            config_warnings, config_errors = validate_config_shape(
                config,
                source=f"{source}:config",
                persistent=False,
                strict=strict,
            )
            warnings.extend(config_warnings)
            errors.extend(config_errors)
    if "review_scope" in data and not isinstance(data["review_scope"], dict):
        errors.append(f"{source}: review_scope must be a mapping")
    warnings.extend(f"{source}: {message}" for message in find_secret_like_values(data))
    return warnings, errors


def resolve_config(
    *,
    cwd: Path,
    explicit_config: str | None = None,
    no_config: bool = False,
    task_file: str | None = None,
    cli_config: dict[str, Any] | None = None,
    allow_reviewer_config_override: bool = False,
    strict: bool = False,
) -> tuple[ConfigResolution, dict[str, Any]]:
    effective: dict[str, Any] = {}
    provenance: dict[str, str] = {}
    warnings: list[str] = []
    errors: list[str] = []
    sources: list[dict[str, Any]] = []
    task_data: dict[str, Any] = {}

    def add_layer(layer: str, path: Path | None, data: dict[str, Any], *, present: bool, persistent: bool, note: str | None = None) -> None:
        nonlocal effective, provenance
        sources.append(source_record(layer, path, present, data, note))
        if not present:
            return
        config = canonical_config(data)
        layer_warnings, layer_errors = validate_config_shape(config, source=layer, persistent=persistent, strict=strict)
        warnings.extend(layer_warnings)
        errors.extend(layer_errors)
        effective = deep_merge_config(effective, {key: value for key, value in config.items() if key != "schema_version"})
        for field in flatten_values({key: value for key, value in config.items() if key != "schema_version"}):
            provenance[field] = layer

    defaults_path = skill_root() / "config" / "defaults.yaml"
    if defaults_path.is_file():
        try:
            add_layer("installed_defaults", defaults_path, load_yaml_mapping(defaults_path), present=True, persistent=True)
        except Exception as exc:
            sources.append(source_record("installed_defaults", defaults_path, True, {}))
            errors.append(f"installed_defaults: failed to load {defaults_path}: {exc}")
    else:
        sources.append(source_record("installed_defaults", defaults_path, False, {}))
        errors.append(f"installed_defaults: missing {defaults_path}")

    if no_config:
        sources.append(source_record("user_local", None, False, {}, "--no-config"))
        sources.append(source_record("project", None, False, {}, "--no-config"))
    else:
        user_path, user_diagnostics = discover_user_config()
        if user_path is None:
            sources.extend(user_diagnostics)
            if any(source.get("note") == "from CROSS_AGENT_CONSENSUS_CONFIG" for source in user_diagnostics):
                errors.append("user_local: CROSS_AGENT_CONSENSUS_CONFIG path not found")
        else:
            user_note = None
            for diagnostic in user_diagnostics:
                if diagnostic["path"] == str(user_path):
                    user_note = diagnostic.get("note")
                    break
                sources.append(diagnostic)
            try:
                add_layer("user_local", user_path, load_yaml_mapping(user_path), present=True, persistent=True, note=user_note)
            except Exception as exc:
                sources.append(source_record("user_local", user_path, True, {}, user_note))
                errors.append(f"user_local: failed to load {user_path}: {exc}")

        if explicit_config:
            project_path = Path(explicit_config).expanduser()
            if not project_path.is_absolute():
                project_path = cwd / project_path
            if project_path.is_file():
                try:
                    add_layer("project", project_path, load_yaml_mapping(project_path), present=True, persistent=True, note="from --config")
                except Exception as exc:
                    sources.append(source_record("project", project_path, True, {}, "from --config"))
                    errors.append(f"project: failed to load {project_path}: {exc}")
            else:
                sources.append(source_record("project", project_path, False, {}, "from --config"))
                errors.append(f"project: --config path not found: {project_path}")
        else:
            discovered_project_path, reason = find_project_config(cwd)
            if discovered_project_path is None:
                sources.append(source_record("project", None, False, {}, reason))
            else:
                try:
                    add_layer(
                        "project",
                        discovered_project_path,
                        load_yaml_mapping(discovered_project_path),
                        present=True,
                        persistent=True,
                    )
                except Exception as exc:
                    sources.append(source_record("project", discovered_project_path, True, {}))
                    errors.append(f"project: failed to load {discovered_project_path}: {exc}")

    if task_file:
        task_path = Path(task_file).expanduser()
        if not task_path.is_absolute():
            task_path = cwd / task_path
        if task_path.is_file():
            try:
                raw_task = load_yaml_mapping(task_path)
                task_warnings, task_errors = validate_task_file_shape(raw_task, source="task_file", strict=strict)
                warnings.extend(task_warnings)
                errors.extend(task_errors)
                task_data = raw_task
                sources.append(source_record("task_file", task_path, True, raw_task))
                if not no_config:
                    raw_task_config = raw_task.get("config")
                    task_config: dict[str, Any] = raw_task_config if isinstance(raw_task_config, dict) else {}
                    config = canonical_config({"schema_version": CONFIG_SCHEMA_VERSION, **task_config})
                    effective = deep_merge_config(effective, {key: value for key, value in config.items() if key != "schema_version"})
                    for field in flatten_values({key: value for key, value in config.items() if key != "schema_version"}):
                        provenance[field] = "task_file"
            except Exception as exc:
                sources.append(source_record("task_file", task_path, True, {}))
                errors.append(f"task_file: failed to load {task_path}: {exc}")
        else:
            sources.append(source_record("task_file", task_path, False, {}))
            errors.append(f"task_file: path not found: {task_path}")

    if cli_config:
        cli_reviewers = get_nested(cli_config, "participants.reviewers")
        configured_reviewers = get_nested(effective, "participants.reviewers")
        if (
            cli_reviewers is not None
            and configured_reviewers is not None
            and cli_reviewers != configured_reviewers
        ):
            message = (
                "cli: --reviewer would replace configured participants.reviewers "
                f"{configured_reviewers!r} with {cli_reviewers!r}; use "
                "--allow-reviewer-config-override for an intentional participant override, "
                "or use --review-focus for review lenses/focus areas"
            )
            if allow_reviewer_config_override:
                warnings.append("accepted reviewer config override: " + message)
            else:
                errors.append(message)
        effective = deep_merge_config(effective, cli_config)
        for field in flatten_values(cli_config):
            provenance[field] = "cli"
        cli_participants = cli_config.get("participants")
        if isinstance(cli_participants, dict):
            compatibility_layer = {"participants": cli_participants}
            _translate_legacy_participants(compatibility_layer)
            compatibility_identities = compatibility_layer.get("participant_identities")
            if isinstance(compatibility_identities, dict):
                effective_identities = effective.setdefault("participant_identities", {})
                if isinstance(effective_identities, dict):
                    for identity, binding in compatibility_identities.items():
                        if identity not in effective_identities:
                            effective_identities[identity] = binding
                            provenance[f"participant_identities.{identity}"] = "cli_compatibility"
        sources.append(source_record("cli", None, True, cli_config, "command-line flags"))

    _, _, _, profile_errors = parse_profile_definitions(effective)
    errors.extend(f"resolved_config: {message}" for message in profile_errors)

    return ConfigResolution(effective, sources, provenance, warnings, errors), task_data


def init_cli_config(args: argparse.Namespace) -> dict[str, Any]:
    config: dict[str, Any] = {}
    flag_map = {
        "profile": "defaults.profile",
        "run_root": "defaults.run_root",
        "max_fresh_review_rounds": "defaults.round_limits.max_fresh_review_rounds",
        "max_fresh_review_rounds_without_human_approval": "defaults.round_limits.max_fresh_review_rounds_without_human_approval",
        "max_launched_review_batches": "defaults.round_limits.max_launched_review_batches",
        "max_remediation_rounds": "defaults.round_limits.max_remediation_rounds_per_finding",
        "author": "participants.author",
        "orchestrator": "participants.orchestrator",
        "human_supervisor": "participants.human_supervisor",
    }
    for attr, path in flag_map.items():
        value = getattr(args, attr, None)
        if value is not None:
            set_nested(config, path, value)
    if getattr(args, "reviewer", None) is not None:
        set_nested(config, "participants.reviewers", args.reviewer)
    if getattr(args, "validator", None) is not None:
        set_nested(config, "participants.validators", args.validator)
    return config


def task_file_fields(task_data: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    task = task_data.get("task") if isinstance(task_data.get("task"), dict) else {}
    review_scope = task_data.get("review_scope") if isinstance(task_data.get("review_scope"), dict) else {}
    if isinstance(task, dict):
        mapping = {
            "objective": "task",
            "artifact_locator": "artifact_locator",
            "success_criteria": "success_criterion",
        }
        for source_key, target_key in mapping.items():
            if source_key in task:
                value = task[source_key]
                fields[target_key] = [value] if target_key == "success_criterion" and isinstance(value, str) else value
    if isinstance(review_scope, dict):
        for source_key, target_key in {
            "objective": "review_objective",
            "in_scope": "in_scope",
            "out_of_scope": "out_of_scope",
        }.items():
            if source_key in review_scope:
                value = review_scope[source_key]
                fields[target_key] = [value] if target_key in {"in_scope", "out_of_scope"} and isinstance(value, str) else value
    return fields


def apply_config_to_init_args(args: argparse.Namespace, resolution: ConfigResolution, task_data: dict[str, Any]) -> None:
    task_fields = task_file_fields(task_data)
    for attr, value in task_fields.items():
        current = getattr(args, attr, None)
        if current is None or current == []:
            setattr(args, attr, value)

    effective = resolution.effective
    defaults = effective.get("defaults", {}) if isinstance(effective.get("defaults"), dict) else {}
    participants = effective.get("participants", {}) if isinstance(effective.get("participants"), dict) else {}
    round_limits = defaults.get("round_limits", {}) if isinstance(defaults.get("round_limits"), dict) else {}

    if args.profile is None:
        args.profile = defaults.get("profile") or "document-consensus"
    if args.run_root is None:
        args.run_root = defaults.get("run_root") or "runs"
    if args.max_fresh_review_rounds is None:
        args.max_fresh_review_rounds = int(round_limits.get("max_fresh_review_rounds") or 1)
    if args.max_fresh_review_rounds_without_human_approval is None:
        args.max_fresh_review_rounds_without_human_approval = int(
            round_limits.get("max_fresh_review_rounds_without_human_approval") or 2
        )
    if args.max_remediation_rounds is None:
        args.max_remediation_rounds = int(round_limits.get("max_remediation_rounds_per_finding") or 2)
    if getattr(args, "max_launched_review_batches", None) is None:
        args.max_launched_review_batches = int(
            round_limits.get("max_launched_review_batches") or 3
        )
    if args.author is None:
        args.author = participants.get("author")
    if args.orchestrator is None:
        args.orchestrator = participants.get("orchestrator")
    if args.reviewer is None:
        reviewers = participants.get("reviewers")
        args.reviewer = reviewers if isinstance(reviewers, list) else None
    if args.human_supervisor is None:
        args.human_supervisor = participants.get("human_supervisor", "none")
    if args.validator is None:
        validators = participants.get("validators")
        args.validator = validators if isinstance(validators, list) else None
    raw_invocation = effective.get("invocation")
    invocation: dict[str, Any] = raw_invocation if isinstance(raw_invocation, dict) else {}
    raw_unattended = invocation.get("unattended_invocation")
    unattended: dict[str, Any] = raw_unattended if isinstance(raw_unattended, dict) else {}
    if not args.unattended_invocation and unattended.get("enabled") is True:
        args.unattended_invocation = True
        scope = unattended.get("scope")
        if (args.unattended_scope is None or args.unattended_scope == []) and isinstance(scope, list):
            args.unattended_scope = scope
    for attr in [
        "success_criterion",
        "validator",
        "in_scope",
        "out_of_scope",
        "review_focus",
        "material_by_default",
        "non_blocking_by_default",
        "unattended_scope",
    ]:
        if getattr(args, attr, None) is None:
            setattr(args, attr, [])


def consumed_config_values(args: argparse.Namespace, resolution: ConfigResolution) -> dict[str, Any]:
    consumed = {
        "defaults.profile": args.profile,
        "defaults.run_root": args.run_root,
        "defaults.round_limits.max_fresh_review_rounds": args.max_fresh_review_rounds,
        "defaults.round_limits.max_fresh_review_rounds_without_human_approval": args.max_fresh_review_rounds_without_human_approval,
        "defaults.round_limits.max_launched_review_batches": getattr(
            args, "max_launched_review_batches", 3
        ),
        "defaults.round_limits.max_remediation_rounds_per_finding": args.max_remediation_rounds,
        "participants.orchestrator": args.orchestrator,
        "participants.author": args.author,
        "participants.reviewers": args.reviewer,
        "participants.validators": args.validator,
        "participants.human_supervisor": args.human_supervisor,
    }
    invocation = resolution.effective.get("invocation", {})
    if isinstance(invocation, dict):
        for key in ["require_invocation_ready", "direct_reviewer_cli"]:
            if key in invocation:
                consumed[f"invocation.{key}"] = invocation[key]
    feedback = resolution.effective.get("feedback", {})
    if isinstance(feedback, dict) and "enabled" in feedback:
        consumed["feedback.enabled"] = bool(feedback["enabled"])
    for identity in [args.orchestrator, args.author, *(args.reviewer or []), *(args.validator or [])]:
        binding = get_nested(resolution.effective, f"participant_identities.{identity}")
        if isinstance(binding, dict):
            consumed[f"participant_identities.{identity}"] = binding
            participant_profile_id = binding.get("participant_profile_id")
            execution_profile_id = binding.get("execution_profile_id")
            if isinstance(participant_profile_id, str):
                consumed[f"participant_profiles.{participant_profile_id}"] = get_nested(
                    resolution.effective, f"participant_profiles.{participant_profile_id}"
                )
            if isinstance(execution_profile_id, str):
                consumed[f"execution_profiles.{execution_profile_id}"] = get_nested(
                    resolution.effective, f"execution_profiles.{execution_profile_id}"
                )
    return {
        key: {
            "value": value,
            "source_layer": resolution.provenance.get(key, "runtime_default"),
        }
        for key, value in consumed.items()
    }


def config_resolution_record(args: argparse.Namespace, run_id: str, created_at: str) -> str:
    resolution: ConfigResolution | None = getattr(args, "config_resolution", None)
    if resolution is None:
        return ""
    resolved_identities, resolved_execution_profiles, profile_errors = resolved_profile_payload(
        resolution.effective
    )
    if profile_errors:
        raise ValueError("invalid resolved participant profiles: " + "; ".join(profile_errors))
    data = {
        "record_type": "ConfigResolution",
        "schema_version": "m2-markdown-2",
        "run_id": run_id,
        "actor_identity": args.orchestrator,
        "created_at": created_at,
        "config_resolution_id": f"config-resolution-{run_id}",
        "config_schema_version": CONFIG_SCHEMA_VERSION,
        "cross_agent_consensus_version": CAC_VERSION,
        "sources": resolution.sources,
        "effective_values": consumed_config_values(args, resolution),
        "resolved_participant_identities": resolved_identities,
        "resolved_execution_profiles": resolved_execution_profiles,
        "diagnostics": {
            "warnings": resolution.warnings,
            "errors": resolution.errors,
        },
        "redactions": [
            {
                "field": "execution_profiles.*.env",
                "rule": "env var names only; secret values are not recorded",
            }
        ],
    }
    return "\n".join(
        [
            f"## ConfigResolution {data['config_resolution_id']}",
            frontmatter(data),
            "",
            "### Notes",
            "",
            "- Effective config was resolved before run initialization.",
            "- Persistent config layers cannot enable unattended invocation.",
            "",
        ]
    )
