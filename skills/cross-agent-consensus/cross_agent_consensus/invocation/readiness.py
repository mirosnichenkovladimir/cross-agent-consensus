"""Fail-closed readiness policy for explicit CAC invocation."""

from __future__ import annotations

import argparse
import re
import shlex
import shutil
from pathlib import Path

from cross_agent_consensus.io import eprint, slugify
from cross_agent_consensus.layout import DEFAULT_LAYOUT, detect_run_layout, round_dir, round_number
from cross_agent_consensus.models import Record
from cross_agent_consensus.records import first_record, parse_run_records, records_by_type
from cross_agent_consensus.validation import check_participants, check_pre_execution

INVOCATION_READY_BOUNDARY_WARNING = (
    "invocation-ready validates whether a direct external CLI invocation is allowed, but it does "
    "not start or monitor the command and does not create agent session telemetry. Use invoke-agent "
    "for supervised execution."
)

SECRET_FLAG_RE = re.compile(r"(^|[-_])(api[-_]?key|key|token|secret|password|authorization)($|[-_])")


def policy_allows_unattended(records: list[Record]) -> bool:
    policy = first_record(records, "Policy")
    if not policy:
        return False
    unattended = policy.data.get("unattended_invocation")
    if isinstance(unattended, dict):
        return unattended.get("enabled") is True
    return unattended is True


def policy_allows_unattended_scoped(
    records: list[Record],
    *,
    run_id: str,
    round_id: str,
    phase: str,
    actor: str,
) -> bool:
    """Scope-aware variant of :func:`policy_allows_unattended` for the run macro.

    The legacy helper only checks ``enabled``; this one enforces the scope
    contract documented in ``SKILL.md`` §Operator Approval Handshake:

    - missing Policy or ``unattended_invocation`` -> ``False``
    - bare ``unattended_invocation: true`` -> ``True`` (no scope limit;
      acceptable only in task-file / CLI per SKILL.md §Configuration)
    - dict form requires ``enabled: true`` AND a non-empty ``scope``
    - scope can be a list of ``key:value`` tokens or a dict; absence of a
      key means "any". Match is exact-string (case-sensitive).
    """
    policy = first_record(records, "Policy")
    if not policy:
        return False
    unattended = policy.data.get("unattended_invocation")
    if unattended is True:
        return True
    if not isinstance(unattended, dict):
        return False
    if unattended.get("enabled") is not True:
        return False
    scope = unattended.get("scope")
    if not scope:
        return False
    return _scope_matches(scope, run_id=run_id, round_id=round_id, phase=phase, actor=actor)


def _scope_matches(scope: object, *, run_id: str, round_id: str, phase: str, actor: str) -> bool:
    context = {"run": run_id, "round": round_id, "phase": phase, "actor": actor}
    if isinstance(scope, list):
        # Token form: ["phase:reviewer", "actor:codex", "actor:claude"]. Keys with no
        # listed value match "any"; if any token is present for a key, the call must
        # match one of them.
        by_key: dict[str, list[str]] = {}
        for token in scope:
            if not isinstance(token, str) or ":" not in token:
                return False  # malformed -> fail closed
            key, _, value = token.partition(":")
            by_key.setdefault(key.strip(), []).append(value.strip())
        for key, allowed in by_key.items():
            if key not in context:
                return False  # unknown key -> fail closed
            if context[key] not in allowed:
                return False
        return True
    if isinstance(scope, dict):
        # Dict form: {phase: [...], actors: [...], rounds: [...], runs: [...]}
        plural_to_key = {"phases": "phase", "actors": "actor", "rounds": "round", "runs": "run"}
        for raw_key, allowed in scope.items():
            key = plural_to_key.get(raw_key, raw_key)
            if key not in context:
                return False
            if not isinstance(allowed, list) or context[key] not in allowed:
                return False
        return True
    return False


def path_under_any(path: Path, roots: list[Path]) -> bool:
    resolved = path.resolve()
    for root in roots:
        try:
            resolved.relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False


def allowed_prompt_roots(run: Path) -> list[Path]:
    if detect_run_layout(run) == DEFAULT_LAYOUT:
        return sorted(path for path in (run / "rounds").glob("round-*/prompts") if path.exists())
    return [run / "payloads" / "prompts"]


def allowed_raw_roots(run: Path) -> list[Path]:
    if detect_run_layout(run) == DEFAULT_LAYOUT:
        return sorted(path for path in (run / "rounds").glob("round-*/raw") if path.exists())
    return [run / "payloads" / "raw"]


def padded_round_id(value: str | None) -> str:
    return f"round-{round_number(value):03d}"


def runtime_command(raw_command: list[str] | None) -> list[str]:
    if not raw_command:
        return []
    command = list(raw_command)
    if command and command[0] == "--":
        command = command[1:]
    return command


def normalize_command_separator(argv: list[str]) -> list[str]:
    normalized: list[str] = []
    index = 0
    while index < len(argv):
        if argv[index] == "--command" and index + 1 < len(argv) and argv[index + 1] == "--":
            normalized.append(argv[index])
            index += 2
            continue
        normalized.append(argv[index])
        index += 1
    return normalized


def command_for_display(command: list[str]) -> str:
    return shlex.join(command)


def command_has_arg(command: list[str], *names: str) -> bool:
    return any(arg in names for arg in command)


def command_has_option_value(command: list[str], option: str, value: str) -> bool:
    for index, arg in enumerate(command):
        if arg == option and index + 1 < len(command) and command[index + 1] == value:
            return True
        if arg == f"{option}={value}":
            return True
    return False


def secret_argv_errors(command: list[str]) -> list[str]:
    errors: list[str] = []
    for index, arg in enumerate(command):
        lower = arg.lower()
        if "authorization:" in lower:
            errors.append(f"argv token {index} appears to contain an Authorization header")
        if lower == "bearer" and index + 1 < len(command):
            errors.append(f"argv token {index} appears to contain a bearer token")
        if re.search(r"\bbearer\s+\S+", arg, re.IGNORECASE):
            errors.append(f"argv token {index} appears to contain a bearer token")
        if not arg.startswith("--"):
            continue
        flag, separator, value = arg[2:].partition("=")
        if SECRET_FLAG_RE.search(flag.lower()):
            if separator and value:
                errors.append(f"argv token {index} passes a secret-looking value in --{flag}")
            elif index + 1 < len(command) and not command[index + 1].startswith("--"):
                errors.append(f"argv token {index} passes a secret-looking value via the next argv token")
    return errors


def reviewer_prompt_completeness_errors(run: Path, args: argparse.Namespace, participants: Record | None) -> list[str]:
    if participants is None:
        return []
    reviewers = [str(value) for value in participants.data.get("reviewer_identities") or []]
    if args.actor not in reviewers:
        return []
    batches = records_by_type(parse_run_records(run), "ReviewBatch")
    active_batch = batches[-1] if batches else None
    if active_batch is None or active_batch.data.get("review_mode") != "fresh_review":
        return []
    round_id = str(active_batch.data.get("round_id") or "round-1")
    if detect_run_layout(run) == DEFAULT_LAYOUT:
        prompt_dir = round_dir(run, round_id) / "prompts" / "reviewers"
        filename = lambda reviewer: f"{slugify(reviewer)}.md"
        pattern = lambda reviewer: f"{slugify(reviewer)}*.md"
    else:
        prompt_dir = run / "payloads" / "prompts"
        filename = lambda reviewer: f"{slugify(reviewer)}-reviewer-{round_id}.md"
        pattern = lambda reviewer: f"{slugify(reviewer)}-reviewer-{round_id}*.md"
    provided_prompt = Path(args.prompt).resolve()
    messages: list[str] = []
    for reviewer in reviewers:
        if reviewer == args.actor:
            try:
                provided_prompt.relative_to(prompt_dir.resolve())
                if "draft" not in provided_prompt.name:
                    continue
            except ValueError:
                pass
        exact = prompt_dir / filename(reviewer)
        candidates = list(prompt_dir.glob(pattern(reviewer)))
        finalized = [path for path in ([exact] + candidates) if path.is_file() and "draft" not in path.name]
        if not finalized:
            messages.append(f"fresh-review reviewer prompt is missing for same-round reviewer: {reviewer}")
    return messages


def player_command_telemetry_errors(player_id: str, command: list[str]) -> list[str]:
    if not command:
        return []
    messages: list[str] = []
    if player_id == "claude-cli":
        if not command_has_option_value(command, "--output-format", "stream-json"):
            messages.append("--player claude-cli requires --output-format stream-json for runtime message telemetry")
        if not command_has_arg(command, "-p", "--print"):
            messages.append("--player claude-cli requires -p/--print for non-interactive monitored invocation")
        if not command_has_arg(command, "--verbose"):
            messages.append("--player claude-cli with stream-json requires --verbose to emit message/tool events")
    if player_id == "codex-cli" and not command_has_arg(command, "--json"):
        messages.append("--player codex-cli requires --json for runtime message telemetry")
    return messages


def codex_trusted_dir_errors(player_id: str, command: list[str]) -> list[str]:
    """Surface the trusted-dir trap before launching Codex CLI.

    Codex CLI refuses to operate outside its on-disk trust list unless
    ``--skip-git-repo-check`` is in argv. When the trap fires, the failure
    arrives only after a session has been allocated; ``state.json`` ends up
    ``failed`` and ``consensus status`` reports a noisy ``failed=`` count for
    what is really an environment problem.

    Pre-flight: require ``--skip-git-repo-check`` for any ``codex-cli`` launch.
    Operators with a Codex-trusted cwd lose nothing — the flag is a no-op for
    them. Operators outside trust get an actionable command instead of a
    failed session record.
    """
    if player_id != "codex-cli" or not command:
        return []
    if not _command_invokes_codex_binary(command):
        # Custom wrappers / test stubs that aren't the real codex binary don't
        # hit the trusted-dir trap; let the rest of readiness validate them.
        return []
    if command_has_arg(command, "--skip-git-repo-check"):
        return []
    suggestion = _suggest_codex_command_with_skip(command)
    return [
        "--player codex-cli requires --skip-git-repo-check to avoid the trusted-directory trap; "
        f"add the flag, e.g.: {suggestion}",
    ]


def _command_invokes_codex_binary(command: list[str]) -> bool:
    """True when ``command[0]`` is the real ``codex`` CLI.

    Matches either the bare basename ``codex`` or an absolute/relative path
    ending in ``/codex``. Wrappers, ``python``-stubs, and arbitrary scripts
    used in tests are correctly treated as not-codex.
    """
    if not command:
        return False
    head = command[0]
    return head == "codex" or head.endswith("/codex")


def _suggest_codex_command_with_skip(command: list[str]) -> str:
    """Build a copy-pasteable codex argv with ``--skip-git-repo-check`` inserted.

    Insert after the ``codex exec`` head if present, otherwise immediately
    after the ``codex`` entry. Falls back to appending when neither matches so
    the operator always gets a runnable command.
    """
    patched = list(command)
    flag = "--skip-git-repo-check"
    if len(patched) >= 2 and patched[0].endswith("codex") and patched[1] == "exec":
        patched.insert(2, flag)
    elif patched and patched[0].endswith("codex"):
        patched.insert(1, flag)
    else:
        patched.append(flag)
    return command_for_display(patched)


def invocation_ready_errors(run: Path, args: argparse.Namespace, command: list[str]) -> list[str]:
    records = parse_run_records(run)
    participants = first_record(records, "Participants")
    messages: list[str] = []
    for name, result in [
        ("pre-execution", check_pre_execution(run)),
        ("participants", check_participants(run)),
    ]:
        if not result.ok:
            messages.extend(f"{name}: {message}" for message in result.messages)
    known: set[str] = set()
    if participants:
        known.add(str(participants.data.get("orchestrator_identity")))
        known.add(str(participants.data.get("author_identity")))
        known.update(str(value) for value in participants.data.get("reviewer_identities") or [])
    if args.actor not in known:
        messages.append(f"actor is not recorded as a participant: {args.actor}")
    messages.extend(reviewer_prompt_completeness_errors(run, args, participants))
    prompt = Path(args.prompt)
    if not prompt.is_file():
        messages.append(f"prompt file not found: {prompt}")
    else:
        if not path_under_any(prompt, allowed_prompt_roots(run)):
            messages.append("--prompt must be under an active prompt payload directory")
        if "draft" in prompt.name:
            messages.append("draft prompts must not be used for invocation")
    if not args.approved and not policy_allows_unattended(records):
        messages.append("external invocation requires --approved or Policy unattended_invocation.enabled=true")
    if not command:
        messages.append("explicit runtime command/provider selection is required")
    messages.extend(player_command_telemetry_errors(getattr(args, "player", "generic-cli"), command))
    messages.extend(codex_trusted_dir_errors(getattr(args, "player", "generic-cli"), command))
    command_name = command[0] if command else None
    if command_name and shutil.which(command_name) is None:
        messages.append(f"command is not executable on PATH: {command_name}")
    if args.raw_output:
        raw_output = Path(args.raw_output)
        if not path_under_any(raw_output, allowed_raw_roots(run)):
            messages.append("--raw-output must be under an active raw-output payload directory")
    else:
        messages.append("--raw-output destination is required")
    return messages


def invoke_agent_round_path_errors(run: Path, args: argparse.Namespace) -> list[str]:
    messages: list[str] = []
    current_round = round_dir(run, args.round)
    prompt = Path(args.prompt)
    if prompt.is_file() and not path_under_any(prompt, [current_round / "prompts"]):
        messages.append("--prompt must be under the selected round prompt directory")
    raw_output = Path(args.raw_output)
    if not path_under_any(raw_output, [current_round / "raw"]):
        messages.append("--raw-output must be under the selected round raw-output directory")
    return messages


def cmd_invocation_ready(args: argparse.Namespace) -> int:
    run = Path(args.run)
    command = runtime_command(args.command)
    messages = invocation_ready_errors(run, args, command)
    if messages:
        for message in messages:
            eprint(f"error: {message}")
        if command:
            print("manual command:")
            print(command_for_display(command))
        return 3
    print("invocation ready")
    print(f"actor: {args.actor}")
    print(f"prompt: {Path(args.prompt)}")
    print(f"raw_output: {args.raw_output}")
    if command:
        print("command: " + command_for_display(command))
    return 0

