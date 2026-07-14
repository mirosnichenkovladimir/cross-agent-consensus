"""`consensus run` macro: prompt finalization + readiness + invoke-agent + capture.

Single-command driver for one same-round phase (`reviewer` is the primary path).
The macro composes existing entry points (`cmd_prompt`, `cmd_invoke_agent`,
`cmd_capture`) and adds a single new audit record (``OperatorApproval``) — no
protocol-runtime changes. See
``plans_and_designs/cac-design-notes/feedback-notes-04-06/prioritization-opinion/tier-2-needs-design/DESIGN.md``
for the contract and truth table.
"""

from __future__ import annotations

import concurrent.futures
import shlex
from dataclasses import dataclass
from pathlib import Path

from cross_agent_consensus import capture
from cross_agent_consensus.approval import approval_binding, stamp_operator_approval
from cross_agent_consensus.config import legacy_adapter_for_command
from cross_agent_consensus.invocation import process_monitor
from cross_agent_consensus.invocation.process_monitor import (
    DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    DEFAULT_IDLE_TIMEOUT_SECONDS,
    DEFAULT_STALE_TIMEOUT_SECONDS,
)
from cross_agent_consensus.invocation.readiness import (
    invocation_ready_errors,
    policy_allows_unattended_scoped,
)
from cross_agent_consensus.io import eprint
from cross_agent_consensus.layout import normalize_round_id, record_round_number, round_number
from cross_agent_consensus.models import (
    CaptureCommandInput,
    InvocationCommandInput,
    InvocationReadyInput,
    PromptCommandInput,
    Record,
    RunCommandInput,
)
from cross_agent_consensus.prompt_command import cmd_prompt
from cross_agent_consensus.prompts import prompt_target, raw_output_target
from cross_agent_consensus.profiles import invocation_profile_from_records
from cross_agent_consensus.records import first_record, parse_run_records, records_by_type


@dataclass(frozen=True)
class ActorPlan:
    """One actor's resolved plan for a single phase of a single round.

    Built once per actor before any launch. Drives both execution
    (``_launch_all``) and manual fallback printing (``_emit_manual_fallback``)
    so the printed commands are byte-for-byte the argv the macro would call.
    """

    actor: str
    participant_profile_id: str
    execution_profile_id: str
    player: str
    phase: str
    round_id: str
    prompt_path: Path
    raw_output_path: Path
    cwd: str
    runtime_command: list[str]
    idle_timeout_seconds: float
    stale_timeout_seconds: float
    heartbeat_interval_seconds: float
    review_batch_id: str | None
    artifact_version_id: str | None


# ---------------------------------------------------------------------------
# Actor resolution (R5)
# ---------------------------------------------------------------------------


def _resolve_actors(
    records: list[Record],
    *,
    round_id: str,
    phase: str,
    requested: list[str] | None,
) -> list[str]:
    if requested:
        return list(requested)
    participants = first_record(records, "Participants")
    if phase == "author":
        return [str(participants.data["author_identity"])] if participants else []
    if phase == "validator":
        return [
            str(value)
            for value in (participants.data.get("validator_identities") or [])
        ] if participants else []
    if phase in ("reviewer", "rereview"):
        batches = _round_review_batches(records, round_id)
        active = batches[-1] if batches else None
        if active is not None:
            expected = active.data.get("expected_reviewer_identities") or []
            if expected:
                return [str(value) for value in expected]
        return [str(value) for value in (participants.data.get("reviewer_identities") or [])] if participants else []
    raise ValueError(f"unsupported phase: {phase}")


# ---------------------------------------------------------------------------
# Plan construction
# ---------------------------------------------------------------------------


def _legacy_runtime_command_for_actor(records: list[Record], actor: str) -> list[str]:
    """Extract ``reviewer_clis.<actor>.command`` from the ConfigResolution record.

    ConfigResolution.effective_values is a dict keyed by ``<group>.<key>`` with
    ``{"value": ..., "source_layer": ...}`` shape (see config.consumed_config_values).
    Returns an empty list when no entry is recorded — callers treat that as
    a blocker.
    """

    resolution = first_record(records, "ConfigResolution")
    if resolution is None:
        return []
    effective = resolution.data.get("effective_values") or {}
    if not isinstance(effective, dict):
        return []
    key = f"reviewer_clis.{actor}.command"
    entry = effective.get(key)
    if isinstance(entry, dict):
        value = entry.get("value")
    else:
        value = entry
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def _invocation_profile_for_actor(
    records: list[Record], actor: str
) -> tuple[str, str, str, list[str]]:
    profile = invocation_profile_from_records(records, actor)
    if profile is not None:
        return (
            profile.participant_profile_id,
            profile.execution_profile_id,
            profile.adapter_id,
            profile.command,
        )
    command = _legacy_runtime_command_for_actor(records, actor)
    player = "manual" if actor == "manual" else legacy_adapter_for_command(command)[0]
    return (
        "legacy-inline-participant-profile",
        f"legacy-inline-{actor}-execution-profile",
        player,
        command,
    )


def _single_or_none(records: list[Record], record_type: str, id_field: str) -> str | None:
    matches = records_by_type(records, record_type)
    if len(matches) == 1:
        value = matches[0].data.get(id_field)
        return str(value) if value else None
    return None


def _round_review_batches(records: list[Record], round_id: str) -> list[Record]:
    target_round = round_number(round_id)
    return [
        record
        for record in records_by_type(records, "ReviewBatch")
        if record_round_number(record) == target_round
    ]


def _active_review_batch(records: list[Record], round_id: str) -> Record | None:
    batches = _round_review_batches(records, round_id)
    if not batches:
        return None
    return batches[-1]


def _build_plan(
    records: list[Record],
    run: Path,
    *,
    round_id: str,
    phase: str,
    actor: str,
    cwd: str,
    idle_timeout_seconds: float,
    stale_timeout_seconds: float,
    heartbeat_interval_seconds: float,
) -> ActorPlan:
    batch = _active_review_batch(records, round_id)
    batch_id_value = batch.data.get("review_batch_id") if batch else None
    review_batch_id = str(batch_id_value) if batch_id_value else None
    batch_artifact_value = batch.data.get("target_artifact_version_id") if batch else None
    artifact_version_id = (
        str(batch_artifact_value)
        if batch_artifact_value
        else _single_or_none(records, "ArtifactVersion", "artifact_version_id")
    )
    prompt_args = PromptCommandInput(
        run=str(run),
        phase=phase,
        actor=actor,
        round=round_id,
        review_batch=review_batch_id,
        artifact_version=artifact_version_id,
        output=None,
        force_draft=False,
        dry_run=False,
    )
    participant_profile_id, execution_profile_id, player, runtime_command = (
        _invocation_profile_for_actor(records, actor)
    )
    return ActorPlan(
        actor=actor,
        participant_profile_id=participant_profile_id,
        execution_profile_id=execution_profile_id,
        player=player,
        phase=phase,
        round_id=round_id,
        prompt_path=prompt_target(run, prompt_args, records),
        raw_output_path=raw_output_target(run, prompt_args, records),
        cwd=cwd,
        runtime_command=runtime_command,
        idle_timeout_seconds=idle_timeout_seconds,
        stale_timeout_seconds=stale_timeout_seconds,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
        review_batch_id=review_batch_id,
        artifact_version_id=artifact_version_id,
    )


# ---------------------------------------------------------------------------
# Prompt finalization
# ---------------------------------------------------------------------------


def _finalize_prompts(run: Path, *, plans: list[ActorPlan]) -> list[str]:
    """Ensure every actor's prompt file exists and is not a draft.

    Returns a list of human-readable error messages for the rare unfixable case
    (e.g. ``cmd_prompt`` itself fails). Missing prompts are written via
    ``cmd_prompt``; existing finalized prompts are left untouched.
    """

    errors: list[str] = []
    for plan in plans:
        if plan.prompt_path.is_file() and "draft" not in plan.prompt_path.name:
            continue
        prompt_args = PromptCommandInput(
            run=str(run),
            phase=plan.phase,
            actor=plan.actor,
            round=plan.round_id,
            review_batch=plan.review_batch_id,
            artifact_version=plan.artifact_version_id,
            output=None,
            force_draft=False,
            dry_run=False,
        )
        rc = cmd_prompt(prompt_args)
        if rc != 0:
            errors.append(f"prompt finalization failed for actor {plan.actor}: cmd_prompt returned {rc}")
            continue
        if not plan.prompt_path.is_file():
            errors.append(f"prompt finalization wrote no file at expected path: {plan.prompt_path}")
    return errors


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------


def _run_readiness(run: Path, *, plans: list[ActorPlan]) -> dict[str, list[str]]:
    """Run ``invocation_ready_errors`` per actor; return per-actor error lists.

    Empty list per actor means ready. Macro aborts before any launch if any
    actor has a non-empty list (all-or-nothing round isolation).
    """

    per_actor: dict[str, list[str]] = {}
    for plan in plans:
        readiness_args = InvocationReadyInput(
            run=str(run),
            actor=plan.actor,
            player=plan.player,
            participant_profile_id=plan.participant_profile_id,
            execution_profile_id=plan.execution_profile_id,
            prompt=str(plan.prompt_path),
            raw_output=str(plan.raw_output_path),
            approved=True,  # macro passes its own --approved through to readiness
            command=plan.runtime_command,
        )
        errors = invocation_ready_errors(run, readiness_args, plan.runtime_command)
        if not plan.runtime_command:
            errors.append(
                f"no runtime command configured for actor {plan.actor!r}; "
                f"bind participant_identities.{plan.actor}.execution_profile_id to an ExecutionProfile with argv"
            )
        per_actor[plan.actor] = errors
    return per_actor


# ---------------------------------------------------------------------------
# Launch + capture
# ---------------------------------------------------------------------------


def _launch_all(
    run: Path,
    *,
    plans: list[ActorPlan],
    sequential: bool,
) -> dict[str, int]:
    """Invoke ``cmd_invoke_agent`` for every plan; collect per-actor exit codes.

    Failed actors do **not** cancel siblings (round-level isolation contract).
    """

    def _one(plan: ActorPlan) -> tuple[str, int]:
        invoke_args = InvocationCommandInput(
            run=str(run),
            round=plan.round_id,
            phase=plan.phase if plan.phase in ("author", "reviewer", "validator", "manual") else "reviewer",
            actor=plan.actor,
            player=plan.player,
            participant_profile_id=plan.participant_profile_id,
            execution_profile_id=plan.execution_profile_id,
            prompt=str(plan.prompt_path),
            raw_output=str(plan.raw_output_path),
            cwd=plan.cwd,
            approved=True,
            idle_timeout_seconds=plan.idle_timeout_seconds,
            stale_timeout_seconds=plan.stale_timeout_seconds,
            heartbeat_interval_seconds=plan.heartbeat_interval_seconds,
            command=list(plan.runtime_command),
            require_existing_approval=True,
        )
        try:
            rc = process_monitor.cmd_invoke_agent(invoke_args)
        except Exception as exc:  # defensive — invoke-agent should not throw, but isolate
            eprint(f"error: invoke-agent raised for actor {plan.actor}: {exc}")
            rc = 1
        return plan.actor, rc

    exit_codes: dict[str, int] = {}
    if sequential or len(plans) <= 1:
        for plan in plans:
            actor, rc = _one(plan)
            exit_codes[actor] = rc
        return exit_codes

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(plans))) as pool:
        futures = [pool.submit(_one, plan) for plan in plans]
        for future in concurrent.futures.as_completed(futures):
            actor, rc = future.result()
            exit_codes[actor] = rc
    return exit_codes


def _capture_all(
    run: Path,
    *,
    plans: list[ActorPlan],
    exit_codes: dict[str, int],
) -> dict[str, int]:
    """Call ``cmd_capture`` for every plan; ``--no-append-record`` for failures.

    Returns per-actor capture exit codes (separate from invoke-agent rc).
    """

    capture_rcs: dict[str, int] = {}
    for plan in plans:
        if plan.phase not in ("reviewer", "validator", "author", "manual"):
            # rereview is not a capture phase; rereview decisions are recorded
            # via cmd_rereview_skeleton separately. Skip silently.
            capture_rcs[plan.actor] = 0
            continue
        if not plan.raw_output_path.is_file():
            # process never produced stdout — nothing to capture; surface in summary
            capture_rcs[plan.actor] = 0
            continue
        rc = exit_codes.get(plan.actor, 0)
        capture_args = CaptureCommandInput(
            run=str(run),
            phase=plan.phase if plan.phase in ("author", "reviewer", "validator", "manual") else "reviewer",
            actor=plan.actor,
            review_batch=plan.review_batch_id,
            artifact_version=plan.artifact_version_id,
            source_file=str(plan.raw_output_path),
            source_mode="file",
            source_command=None,
            provider=plan.player,
            round=plan.round_id,
            validator_id=None,
            result=None,
            waiver_authority=None,
            waiver_rationale=None,
            no_append_record=(rc != 0),
            no_narrative_extract=False,
        )
        try:
            capture_rcs[plan.actor] = capture.cmd_capture(capture_args)
        except Exception as exc:
            eprint(f"error: capture raised for actor {plan.actor}: {exc}")
            capture_rcs[plan.actor] = 1
    return capture_rcs


# ---------------------------------------------------------------------------
# Manual fallback printer
# ---------------------------------------------------------------------------


def _fallback_lines_for_plan(plan: ActorPlan) -> list[str]:
    """One copy-pasteable ``consensus invoke-agent`` line per actor (R4).

    The argv is the literal argv the macro would have passed to
    ``cmd_invoke_agent``. Round-trips through ``argparse`` cleanly — verified
    by parser-smoke test.
    """

    parts = [
        "scripts/consensus invoke-agent",
        f"--round {shlex.quote(plan.round_id)}",
        f"--phase {plan.phase if plan.phase in ('author', 'reviewer', 'validator', 'manual') else 'reviewer'}",
        f"--actor {shlex.quote(plan.actor)}",
        f"--player {shlex.quote(plan.player)}",
        f"--participant-profile {shlex.quote(plan.participant_profile_id)}",
        f"--execution-profile {shlex.quote(plan.execution_profile_id)}",
        f"--prompt {shlex.quote(str(plan.prompt_path))}",
        f"--raw-output {shlex.quote(str(plan.raw_output_path))}",
        f"--cwd {shlex.quote(plan.cwd)}",
        "--approved",
    ]
    if plan.runtime_command:
        parts.append("--command -- " + shlex.join(plan.runtime_command))
    else:
        parts.append("--command -- <REQUIRED: argv for the player CLI>")
    return [f"{plan.actor:>12} → " + " \\\n              ".join(parts)]


def _emit_manual_fallback(
    plans: list[ActorPlan],
    blockers: dict[str, list[str]] | None = None,
) -> None:
    """Print per-actor copy-pasteable manual commands to stdout.

    ``blockers`` (if provided) renders each actor's readiness errors immediately
    above its command — gives the operator everything needed to either fix the
    blocker or run the command by hand.
    """

    print("manual fallback commands (one per actor):")
    print()
    for plan in plans:
        if blockers:
            for message in blockers.get(plan.actor, []):
                print(f"  ! {plan.actor}: {message}")
        for line in _fallback_lines_for_plan(plan):
            print(line)
        print()


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def _summarize_results(
    plans: list[ActorPlan],
    *,
    invoke_rcs: dict[str, int],
    capture_rcs: dict[str, int],
) -> int:
    print()
    print("run summary:")
    overall = 0
    for plan in plans:
        invoke_rc = invoke_rcs.get(plan.actor, -1)
        capture_rc = capture_rcs.get(plan.actor, 0)
        status = "ok" if invoke_rc == 0 and capture_rc == 0 else "failed"
        print(
            f"  {plan.actor:>12}: status={status} invoke_rc={invoke_rc} "
            f"capture_rc={capture_rc} raw={plan.raw_output_path}"
        )
        if status != "ok":
            overall = 1
    return overall


def cmd_run(args: RunCommandInput) -> int:
    """`consensus run` macro entry point."""

    run = Path(args.run)
    round_id = normalize_round_id(args.round)
    phase = args.phase
    requested = [actor.strip() for actor in args.actors.split(",")] if args.actors else None

    if phase not in ("author", "reviewer", "rereview", "validator"):
        eprint(f"error: unsupported phase: {phase}")
        return 2

    records = parse_run_records(run)
    actors = _resolve_actors(records, round_id=round_id, phase=phase, requested=requested)
    if not actors:
        eprint(f"error: no actors resolved for phase={phase} round={round_id}")
        return 2

    cwd = getattr(args, "cwd", ".") or "."
    plans = [
        _build_plan(
            records,
            run,
            round_id=round_id,
            phase=phase,
            actor=actor,
            cwd=cwd,
            idle_timeout_seconds=getattr(args, "idle_timeout_seconds", DEFAULT_IDLE_TIMEOUT_SECONDS),
            stale_timeout_seconds=getattr(args, "stale_timeout_seconds", DEFAULT_STALE_TIMEOUT_SECONDS),
            heartbeat_interval_seconds=getattr(args, "heartbeat_interval_seconds", DEFAULT_HEARTBEAT_INTERVAL_SECONDS),
        )
        for actor in actors
    ]

    print(f"resolved actors for phase={phase} round={round_id}: {', '.join(actors)}")

    # Step 2 — prompt finalization (always, regardless of --execute-reviewers)
    prompt_errors = _finalize_prompts(run, plans=plans)
    if prompt_errors:
        for message in prompt_errors:
            eprint(f"error: {message}")
        return 2

    # Refresh records — prompt finalization may have written batch / artifact changes
    records = parse_run_records(run)

    # Step 3 — readiness per actor
    blockers = _run_readiness(run, plans=plans)
    has_blocker = any(messages for messages in blockers.values())

    if not args.execute_reviewers:
        # Dry-run path: print plan + readiness, exit 0 if clean, 3 if any blockers
        print("dry-run plan (--execute-reviewers not passed):")
        _emit_manual_fallback(plans, blockers=blockers)
        return 3 if has_blocker else 0

    # Step 1 — approval gate (truth table)
    scoped_matches = all(
        policy_allows_unattended_scoped(
            records,
            run_id=run.name,
            round_id=round_id,
            phase=phase,
            actor=plan.actor,
        )
        for plan in plans
    )
    if not args.approved:
        eprint("error: --execute-reviewers requires --approved (explicit operator approval)")
        _emit_manual_fallback(plans, blockers=blockers)
        return 1

    if has_blocker:
        eprint("error: invocation-ready failed for one or more actors; aborting before any launch")
        _emit_manual_fallback(plans, blockers=blockers)
        return 3

    # Step 7 (precedes launches per design §Authoritative gating contract)
    mechanism = "policy_unattended" if scoped_matches else "cli_approved_flag"
    operator_identity = getattr(args, "operator_identity", None) or None
    try:
        bindings = [
            approval_binding(
                run,
                records,
                participant_identity=plan.actor,
                participant_profile_id=plan.participant_profile_id,
                execution_profile_id=plan.execution_profile_id,
                player_id=plan.player,
                phase=plan.phase if plan.phase in ("author", "reviewer", "validator", "manual") else "reviewer",
                round_id=round_id,
                prompt_path=plan.prompt_path,
                command=plan.runtime_command,
                artifact_version_id=plan.artifact_version_id,
                working_directory=plan.cwd,
            )
            for plan in plans
        ]
        approval_path = stamp_operator_approval(
            run,
            round_id=round_id,
            phase=phase,
            bindings=bindings,
            mechanism=mechanism,
            operator_identity=operator_identity,
        )
    except ValueError as exc:
        eprint(f"error: approval binding failed: {exc}")
        return 3
    print(f"stamped OperatorApproval ({mechanism}): {approval_path}")

    # Steps 4–6 — launch + capture
    invoke_rcs = _launch_all(run, plans=plans, sequential=bool(getattr(args, "sequential", False)))
    capture_rcs = _capture_all(run, plans=plans, exit_codes=invoke_rcs)

    overall = _summarize_results(plans, invoke_rcs=invoke_rcs, capture_rcs=capture_rcs)
    return overall


__all__ = [
    "ActorPlan",
    "cmd_run",
    "_resolve_actors",
    "_build_plan",
    "_finalize_prompts",
    "_run_readiness",
    "_launch_all",
    "_capture_all",
    "_emit_manual_fallback",
    "_fallback_lines_for_plan",
]
