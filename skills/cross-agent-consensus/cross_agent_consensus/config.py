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


CONFIG_SCHEMA_VERSION = "cross-agent-consensus-config-1"
TASK_SCHEMA_VERSION = "cross-agent-consensus-task-1"
CAC_VERSION = read_cac_version()


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
    data = parse_yaml_subset(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a YAML mapping")
    return data


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
        if len(compact) >= 32 and len(set(compact)) >= 16:
            warnings.append(f"{path}: value looks like a high-entropy secret")
    elif isinstance(data, list):
        for index, item in enumerate(data):
            warnings.extend(find_secret_like_values(item, f"{path}[{index}]"))
    return warnings


def validate_config_shape(data: dict[str, Any], *, source: str, persistent: bool, strict: bool) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    errors: list[str] = []
    allowed_top = {"schema_version", "defaults", "participants", "reviewer_clis", "invocation"}
    schema_version = data.get("schema_version")
    if schema_version != CONFIG_SCHEMA_VERSION:
        errors.append(f"{source}: schema_version must be {CONFIG_SCHEMA_VERSION}")
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
        allowed_participants = {"orchestrator", "author", "reviewers", "human_supervisor"}
        unknown_participants = sorted(set(participants) - allowed_participants)
        if unknown_participants:
            message = f"{source}: unknown participants keys: {', '.join(unknown_participants)}"
            (errors if strict else warnings).append(message)
        reviewers = participants.get("reviewers")
        if reviewers is not None and not (isinstance(reviewers, list) and all(isinstance(item, str) for item in reviewers)):
            errors.append(f"{source}: participants.reviewers must be a list of strings")
        for key in ["orchestrator", "author", "human_supervisor"]:
            value = participants.get(key)
            if value is not None and not isinstance(value, str):
                errors.append(f"{source}: participants.{key} must be a string")
    reviewer_clis = data.get("reviewer_clis", {})
    if reviewer_clis is not None and not isinstance(reviewer_clis, dict):
        errors.append(f"{source}: reviewer_clis must be a mapping")
    elif isinstance(reviewer_clis, dict):
        for identity, mapping in reviewer_clis.items():
            if not isinstance(mapping, dict):
                errors.append(f"{source}: reviewer_clis.{identity} must be a mapping")
                continue
            allowed_cli_keys = {"command", "prompt_transport", "stdout_capture", "stderr_capture", "env"}
            unknown_cli_keys = sorted(set(mapping) - allowed_cli_keys)
            if unknown_cli_keys:
                message = f"{source}: reviewer_clis.{identity} unknown keys: {', '.join(unknown_cli_keys)}"
                (errors if strict else warnings).append(message)
            command = mapping.get("command")
            if command is not None and not (isinstance(command, list) and command and all(isinstance(item, str) for item in command)):
                errors.append(f"{source}: reviewer_clis.{identity}.command must be a non-empty argv list")
            env = mapping.get("env")
            if env is not None and not (isinstance(env, list) and all(isinstance(item, str) for item in env)):
                errors.append(f"{source}: reviewer_clis.{identity}.env must be a list of environment variable names, not values")
            if mapping.get("prompt_transport", "stdin") != "stdin":
                errors.append(f"{source}: reviewer_clis.{identity}.prompt_transport v1 supports only stdin")
            if mapping.get("stdout_capture", "raw_output") != "raw_output":
                errors.append(f"{source}: reviewer_clis.{identity}.stdout_capture v1 supports only raw_output")
            if mapping.get("stderr_capture", "raw_error") != "raw_error":
                errors.append(f"{source}: reviewer_clis.{identity}.stderr_capture v1 supports only raw_error")
    invocation = data.get("invocation", {})
    if invocation is not None and not isinstance(invocation, dict):
        errors.append(f"{source}: invocation must be a mapping")
    elif isinstance(invocation, dict):
        allowed_invocation = {"require_invocation_ready", "direct_reviewer_cli", "unattended_invocation"}
        unknown_invocation = sorted(set(invocation) - allowed_invocation)
        if unknown_invocation:
            message = f"{source}: unknown invocation keys: {', '.join(unknown_invocation)}"
            (errors if strict else warnings).append(message)
        if invocation.get("direct_reviewer_cli") not in {None, "explicit_only"}:
            errors.append(f"{source}: invocation.direct_reviewer_cli must be explicit_only")
        unattended = invocation.get("unattended_invocation")
        if isinstance(unattended, dict) and unattended.get("enabled") is True:
            scope = unattended.get("scope")
            if not (isinstance(scope, list) and scope and all(isinstance(item, str) for item in scope)):
                errors.append(f"{source}: invocation.unattended_invocation.scope must be a non-empty string list when enabled")
    if persistent and contains_enabled_unattended(data):
        errors.append(f"{source}: persistent config must not enable unattended_invocation")
    secret_messages = [f"{source}: {message}" for message in find_secret_like_values(data)]
    if persistent:
        errors.extend(secret_messages)
    else:
        warnings.extend(secret_messages)
    reviewers = get_nested(data, "participants.reviewers", [])
    cli_keys = set(reviewer_clis) if isinstance(reviewer_clis, dict) else set()
    if reviewers and cli_keys:
        unused = sorted(cli_keys - set(reviewers))
        missing = sorted(set(reviewers) - cli_keys)
        if unused:
            warnings.append(f"{source}: reviewer_clis has unused entries: {', '.join(unused)}")
        if missing:
            warnings.append(f"{source}: reviewers without CLI mapping fall back to manual handoff: {', '.join(missing)}")
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
            config = {"schema_version": CONFIG_SCHEMA_VERSION, **canonical_config(data["config"])}
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
            project_path, reason = find_project_config(cwd)
            if project_path is None:
                sources.append(source_record("project", None, False, {}, reason))
            else:
                try:
                    add_layer("project", project_path, load_yaml_mapping(project_path), present=True, persistent=True)
                except Exception as exc:
                    sources.append(source_record("project", project_path, True, {}))
                    errors.append(f"project: failed to load {project_path}: {exc}")

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
                    task_config = raw_task.get("config") if isinstance(raw_task.get("config"), dict) else {}
                    config = {"schema_version": CONFIG_SCHEMA_VERSION, **canonical_config(task_config)}
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
        sources.append(source_record("cli", None, True, cli_config, "command-line flags"))

    return ConfigResolution(effective, sources, provenance, warnings, errors), task_data


def init_cli_config(args: argparse.Namespace) -> dict[str, Any]:
    config: dict[str, Any] = {}
    flag_map = {
        "profile": "defaults.profile",
        "run_root": "defaults.run_root",
        "max_fresh_review_rounds": "defaults.round_limits.max_fresh_review_rounds",
        "max_fresh_review_rounds_without_human_approval": "defaults.round_limits.max_fresh_review_rounds_without_human_approval",
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
    if args.author is None:
        args.author = participants.get("author")
    if args.orchestrator is None:
        args.orchestrator = participants.get("orchestrator")
    if args.reviewer is None:
        reviewers = participants.get("reviewers")
        args.reviewer = reviewers if isinstance(reviewers, list) else None
    if args.human_supervisor is None:
        args.human_supervisor = participants.get("human_supervisor", "none")
    invocation = effective.get("invocation", {}) if isinstance(effective.get("invocation"), dict) else {}
    unattended = invocation.get("unattended_invocation") if isinstance(invocation.get("unattended_invocation"), dict) else {}
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
        "defaults.round_limits.max_remediation_rounds_per_finding": args.max_remediation_rounds,
        "participants.orchestrator": args.orchestrator,
        "participants.author": args.author,
        "participants.reviewers": args.reviewer,
        "participants.human_supervisor": args.human_supervisor,
    }
    invocation = resolution.effective.get("invocation", {})
    if isinstance(invocation, dict):
        for key in ["require_invocation_ready", "direct_reviewer_cli"]:
            if key in invocation:
                consumed[f"invocation.{key}"] = invocation[key]
    reviewer_clis = resolution.effective.get("reviewer_clis", {})
    if isinstance(reviewer_clis, dict):
        for reviewer in args.reviewer or []:
            if reviewer in reviewer_clis:
                consumed[f"reviewer_clis.{reviewer}.command"] = get_nested(reviewer_clis, f"{reviewer}.command")
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
    data = {
        "record_type": "ConfigResolution",
        "schema_version": "m2-markdown-1",
        "run_id": run_id,
        "actor_identity": args.orchestrator,
        "created_at": created_at,
        "config_resolution_id": f"config-resolution-{run_id}",
        "config_schema_version": CONFIG_SCHEMA_VERSION,
        "cross_agent_consensus_version": CAC_VERSION,
        "sources": resolution.sources,
        "effective_values": consumed_config_values(args, resolution),
        "diagnostics": {
            "warnings": resolution.warnings,
            "errors": resolution.errors,
        },
        "redactions": [
            {
                "field": "reviewer_clis.*.env",
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
