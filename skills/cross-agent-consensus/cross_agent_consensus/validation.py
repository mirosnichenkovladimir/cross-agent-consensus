"""Deterministic validation checks for cross-agent-consensus runs."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from cross_agent_consensus.io import read_json_file, slugify
from cross_agent_consensus.layout import (
    DEFAULT_LAYOUT,
    REPORT_FILENAME,
    detect_run_layout,
    record_path_round_number,
    record_round_number,
    required_run_paths,
    round_id_from_number,
)
from cross_agent_consensus.markdown_records import parse_records_from_file
from cross_agent_consensus.models import (
    CONCLUSION_VALIDATION_BATCH_PURPOSE,
    CONCLUSION_VALIDATION_REVIEW_MODE,
    FRESH_REVIEW_MODE,
    CheckResult,
    Record,
)
from cross_agent_consensus.record_schema import COMMON_FIELDS, ENUMS, FIELD_ALIASES, REQUIRED_FIELDS
from cross_agent_consensus.records import (
    canonical_finding_ids,
    first_record,
    parse_run_records,
    records_by_type,
)
from cross_agent_consensus.invocation.session_paths import safe_actor_component

RESOLVING_REREVIEW_DECISIONS = {"verified", "rejection_accepted"}
UNRESOLVED_REREVIEW_DECISIONS = {"still_valid", "disputed", "needs_human"}
RUN_SCOPE_SENTINEL = "__run_scope__"


def required_field_missing(data: dict[str, Any], field: str) -> bool:
    if field not in data:
        return True
    value = data.get(field)
    if value is None:
        return not field.endswith("_or_null")
    if value == "":
        return True
    if isinstance(value, list) and not value and field not in {
        "source_finding_ids",
        "raw_finding_ids",
        "unresolved_finding_ids",
        "supporting_record_ids",
    }:
        return True
    if isinstance(value, dict) and not value:
        return True
    if isinstance(value, str) and "<" in value and ">" in value:
        return True
    return False


def check_pre_execution(run: Path) -> CheckResult:
    messages: list[str] = []
    for path in required_run_paths(run):
        if not path.exists():
            messages.append(f"missing required path: {path.relative_to(run) if path.is_relative_to(run) else path}")
    records = parse_run_records(run)
    for record_type in ["TaskBrief", "Policy", "Participants", "ReviewScope", "ReviewBatch", "ArtifactVersion"]:
        if first_record(records, record_type) is None:
            messages.append(f"missing required record type: {record_type}")
    for record in records:
        if record.record_type in {"TaskBrief", "Policy", "Participants", "ReviewScope", "ReviewBatch", "ArtifactVersion"}:
            for field in COMMON_FIELDS + REQUIRED_FIELDS.get(record.record_type, []):
                if required_field_missing(record.data, field):
                    messages.append(f"{record.path}:{record.heading_line}: {record.record_type} missing field {field}")
    return CheckResult(not messages, messages or ["pre-execution checks passed"])


def conclusion_validation_batches(records: list[Record]) -> list[Record]:
    canonical_ids = canonical_finding_ids(records)
    batches: list[Record] = []
    for record in records_by_type(records, "ReviewBatch"):
        source_ids = [str(value) for value in record.data.get("source_finding_ids") or []]
        if (
            record.data.get("review_mode") == CONCLUSION_VALIDATION_REVIEW_MODE
            and record.data.get("batch_purpose") == CONCLUSION_VALIDATION_BATCH_PURPOSE
            and source_ids
            and all(source_id in canonical_ids for source_id in source_ids)
        ):
            batches.append(record)
    return batches


def expected_conclusion_validation_reviewers(records: list[Record], batch: Record) -> set[str]:
    expected = batch.data.get("expected_reviewer_identities")
    if isinstance(expected, list):
        return {str(value) for value in expected if value}
    participants = first_record(records, "Participants")
    if participants is None:
        return set()
    reviewers = participants.data.get("reviewer_identities")
    return {str(value) for value in reviewers if value} if isinstance(reviewers, list) else set()


def skipped_conclusion_validation_batch_ids(records: list[Record]) -> set[str]:
    skipped: set[str] = set()
    for record in records_by_type(records, "Policy"):
        values = record.data.get("skipped_conclusion_validation_batch_ids")
        if isinstance(values, list):
            skipped.update(str(value) for value in values)
    return skipped


def conclusion_validation_reviewers_by_batch(records: list[Record]) -> dict[str, set[str]]:
    captured: dict[str, set[str]] = {}
    for record in records_by_type(records, "RawReviewerOutput"):
        batch_id = record.data.get("review_batch_id")
        reviewer = record.data.get("reviewer_identity")
        if batch_id and reviewer:
            captured.setdefault(str(batch_id), set()).add(str(reviewer))
    return captured


def conclusion_validation_batch_complete(records: list[Record], batch: Record) -> bool:
    batch_id = str(batch.data.get("review_batch_id"))
    captured = conclusion_validation_reviewers_by_batch(records).get(batch_id, set())
    expected = expected_conclusion_validation_reviewers(records, batch)
    return expected <= captured if expected else bool(captured)


def pending_conclusion_validation_batches(records: list[Record], finding_ids: Iterable[str]) -> list[str]:
    wanted = {str(finding_id) for finding_id in finding_ids}
    skipped = skipped_conclusion_validation_batch_ids(records)
    pending: list[str] = []
    for batch in conclusion_validation_batches(records):
        batch_id = str(batch.data.get("review_batch_id"))
        source_ids = {str(value) for value in batch.data.get("source_finding_ids") or []}
        if wanted & source_ids and batch_id not in skipped and not conclusion_validation_batch_complete(records, batch):
            pending.append(batch_id)
    return pending


def conclusion_validation_ordering_messages(records: list[Record]) -> list[str]:
    messages: list[str] = []
    skipped = skipped_conclusion_validation_batch_ids(records)
    raw_outputs_by_batch: dict[str, list[Record]] = {}
    for record in records_by_type(records, "RawReviewerOutput"):
        batch_id = record.data.get("review_batch_id")
        if batch_id:
            raw_outputs_by_batch.setdefault(str(batch_id), []).append(record)
    responses_by_finding: dict[str, list[Record]] = {}
    for record in records_by_type(records, "AuthorResponse"):
        finding_id = record.data.get("canonical_finding_id")
        if finding_id:
            responses_by_finding.setdefault(str(finding_id), []).append(record)

    for batch in conclusion_validation_batches(records):
        batch_id = str(batch.data.get("review_batch_id"))
        if batch_id in skipped:
            continue
        source_ids = [str(value) for value in batch.data.get("source_finding_ids") or []]
        responses = [response for finding_id in source_ids for response in responses_by_finding.get(finding_id, [])]
        if not responses:
            continue
        raw_outputs = raw_outputs_by_batch.get(batch_id, [])
        expected = expected_conclusion_validation_reviewers(records, batch)
        captured = {str(record.data.get("reviewer_identity")) for record in raw_outputs}
        missing_reviewers = sorted(expected - captured) if expected else []
        if not raw_outputs or missing_reviewers:
            missing_text = f" missing reviewer output: {', '.join(missing_reviewers)}" if missing_reviewers else ""
            for response in responses:
                messages.append(
                    f"{response.path}:{response.heading_line}: AuthorResponse for "
                    f"{response.data.get('canonical_finding_id')} exists before conclusion-validation output "
                    f"for ReviewBatch {batch_id}{missing_text}"
                )
            continue
        latest_output_time = max(str(record.data.get("created_at")) for record in raw_outputs)
        for response in responses:
            response_time = str(response.data.get("created_at"))
            if response_time < latest_output_time:
                messages.append(
                    f"{response.path}:{response.heading_line}: AuthorResponse created_at {response_time} is earlier "
                    f"than conclusion-validation output {latest_output_time} for ReviewBatch {batch_id}"
                )
    return messages


def cli_mapped_reviewer_identities(records: list[Record]) -> set[str]:
    reviewers: set[str] = set()
    for record in records_by_type(records, "ConfigResolution"):
        values = record.data.get("effective_values")
        if not isinstance(values, dict):
            continue
        for key, payload in values.items():
            if not isinstance(key, str):
                continue
            if not key.startswith("reviewer_clis.") or not key.endswith(".command"):
                continue
            command = payload.get("value") if isinstance(payload, dict) else None
            if isinstance(command, list) and command:
                reviewers.add(key[len("reviewer_clis.") : -len(".command")])
    return reviewers


def completed_reviewer_agent_session_exists(run: Path, round_id: str, reviewer_identity: str) -> bool:
    try:
        round_number = record_round_number_value(round_id)
    except ValueError:
        return False
    canonical_round_id = f"round-{round_number:03d}"
    actor_dir = run / "rounds" / canonical_round_id / "agents" / safe_actor_component(reviewer_identity)
    if not actor_dir.is_dir():
        return False
    expected_rounds = {round_id, canonical_round_id}
    for session in sorted(actor_dir.glob("session-*")):
        if not session.is_dir():
            continue
        try:
            invocation = read_json_file(session / "invocation.json")
            state = read_json_file(session / "state.json")
            exit_payload = read_json_file(session / "exit.json")
        except (OSError, ValueError):
            continue
        if invocation.get("actor_identity") != reviewer_identity:
            continue
        if invocation.get("phase") != "reviewer":
            continue
        if invocation.get("round_id") not in expected_rounds:
            continue
        if state.get("state") != "completed":
            continue
        if exit_payload.get("final_state") != "completed":
            continue
        if exit_payload.get("exit_code_or_null") != 0:
            continue
        return True
    return False


def record_round_number_value(round_id: str) -> int:
    match = re.fullmatch(r"round-(\d+)", str(round_id))
    if not match:
        raise ValueError(f"invalid round id: {round_id}")
    return int(match.group(1))


def reviewer_cli_invocation_messages(run: Path, records: list[Record]) -> list[str]:
    cli_reviewers = cli_mapped_reviewer_identities(records)
    if not cli_reviewers:
        return []
    batch_rounds: dict[str, str] = {}
    for batch in records_by_type(records, "ReviewBatch"):
        batch_id = batch.data.get("review_batch_id")
        round_id = batch.data.get("round_id")
        if batch_id and round_id:
            batch_rounds[str(batch_id)] = str(round_id)
    messages: list[str] = []
    for record in records_by_type(records, "RawReviewerOutput"):
        reviewer = str(record.data.get("reviewer_identity") or "")
        if reviewer not in cli_reviewers:
            continue
        batch_id = str(record.data.get("review_batch_id") or "")
        round_id = batch_rounds.get(batch_id)
        if round_id is None:
            continue
        if completed_reviewer_agent_session_exists(run, round_id, reviewer):
            continue
        messages.append(
            f"{record.path}:{record.heading_line}: CLI reviewer {reviewer!r} has RawReviewerOutput for "
            f"ReviewBatch {batch_id} without a completed invoke-agent session; use consensus invoke-agent "
            "instead of direct capture, host subagents, or in-chat review for configured CLI reviewers"
        )
    return messages


def check_records(run: Path) -> CheckResult:
    messages: list[str] = []
    warnings: list[str] = []
    seen_ids: set[str] = set()
    records = parse_run_records(run)
    if not records:
        messages.append("no protocol records found")
    for record in records:
        consumed = record.data.get("_aliases_consumed")
        if isinstance(consumed, list):
            aliases = FIELD_ALIASES.get(record.record_type, {})
            for old_field in consumed:
                new_field = aliases.get(str(old_field), "")
                warnings.append(
                    f"deprecation: {record.path}:{record.heading_line}: "
                    f"{record.record_type}.{old_field} -> {new_field}"
                )
        for field in COMMON_FIELDS:
            if required_field_missing(record.data, field):
                messages.append(f"{record.path}:{record.heading_line}: missing common field {field}")
        if record.data.get("schema_version") != "m2-markdown-1":
            messages.append(f"{record.path}:{record.heading_line}: schema_version must be m2-markdown-1")
        for field in REQUIRED_FIELDS.get(record.record_type, []):
            if required_field_missing(record.data, field):
                messages.append(f"{record.path}:{record.heading_line}: {record.record_type} missing field {field}")
        unique = f"{record.record_type}:{record.record_id}"
        if unique in seen_ids:
            messages.append(f"{record.path}:{record.heading_line}: duplicate record id {unique}")
        seen_ids.add(unique)
        for field, allowed in ENUMS.items():
            if field in record.data and record.data[field] is not None and record.data[field] not in allowed:
                messages.append(
                    f"{record.path}:{record.heading_line}: {field}={record.data[field]!r} is not one of {sorted(allowed)}"
                )
        if record.record_type == "ValidationEvidence" and record.data.get("result") == "waived":
            if record.data.get("waiver_authority_or_null") is None:
                messages.append(f"{record.path}:{record.heading_line}: waived validator missing authority")
            if record.data.get("waiver_rationale_or_null") is None:
                messages.append(f"{record.path}:{record.heading_line}: waived validator missing rationale")
    messages.extend(conclusion_validation_ordering_messages(records))
    ok = not messages
    return CheckResult(ok, (warnings + messages) or ["record checks passed"])


def check_participants(run: Path) -> CheckResult:
    records = parse_run_records(run)
    participants = first_record(records, "Participants")
    if participants is None:
        return CheckResult.fail(["missing Participants record"])
    data = participants.data
    orchestrator = data.get("orchestrator_identity")
    author = data.get("author_identity")
    reviewers = data.get("reviewer_identities") or []
    messages: list[str] = []
    if orchestrator == author:
        messages.append("orchestrator_identity must be distinct from author_identity")
    if orchestrator in reviewers:
        messages.append("orchestrator_identity must be distinct from reviewer identities")
    if len(set(reviewers)) != len(reviewers):
        messages.append("reviewer identities must be unique")
    return CheckResult(not messages, messages or ["participant checks passed"])


def check_links(run: Path) -> CheckResult:
    records = parse_run_records(run)
    messages: list[str] = []
    artifacts = {record.data.get("artifact_version_id") for record in records_by_type(records, "ArtifactVersion")}
    scopes = {record.data.get("review_scope_id") for record in records_by_type(records, "ReviewScope")}
    batches = {record.data.get("review_batch_id") for record in records_by_type(records, "ReviewBatch")}
    batch_rounds: dict[str, int] = {}
    raw_findings = {record.data.get("raw_finding_id") for record in records_by_type(records, "RawFinding")}
    normalizations = {
        record.data.get("normalization_record_id") for record in records_by_type(records, "NormalizationRecord")
    }
    canonical_findings = {record.data.get("canonical_finding_id") for record in records_by_type(records, "CanonicalFinding")}
    termination_records = {
        record.data.get("termination_record_id") for record in records_by_type(records, "TerminationRecord")
    }
    validators = set(required_validators(records))
    participants = first_record(records, "Participants")
    reviewers = set(participants.data.get("reviewer_identities") or []) if participants else set()

    for record in records_by_type(records, "ArtifactVersion"):
        predecessor = record.data.get("predecessor_id_or_null")
        if predecessor and predecessor not in artifacts:
            messages.append(f"{record.path}:{record.heading_line}: predecessor artifact not found: {predecessor}")
    for record in records_by_type(records, "ReviewBatch"):
        if record.data.get("review_scope_id") not in scopes:
            messages.append(f"{record.path}:{record.heading_line}: review_scope_id not found")
        if record.data.get("target_artifact_version_id") not in artifacts:
            messages.append(f"{record.path}:{record.heading_line}: target artifact not found")
        try:
            batch_round = record_round_number(record)
            if record.data.get("review_batch_id"):
                batch_rounds[str(record.data.get("review_batch_id"))] = batch_round
            path_round = record_path_round_number(record.path)
            if path_round is not None and path_round != batch_round:
                messages.append(
                    f"{record.path}:{record.heading_line}: ReviewBatch round_id "
                    f"{round_id_from_number(batch_round)} does not match path round-{path_round:03d}"
                )
        except ValueError as exc:
            messages.append(f"{record.path}:{record.heading_line}: invalid round_id: {exc}")

    def check_record_batch_round(record: Record) -> None:
        review_batch_id = record.data.get("review_batch_id")
        if not review_batch_id:
            return
        batch_round = batch_rounds.get(str(review_batch_id))
        path_round = record_path_round_number(record.path)
        if batch_round is not None and path_round is not None and path_round != batch_round:
            messages.append(
                f"{record.path}:{record.heading_line}: record path round-{path_round:03d} "
                f"does not match ReviewBatch {review_batch_id} round_id {round_id_from_number(batch_round)}"
            )
    for record in records_by_type(records, "RawReviewerOutput"):
        if record.data.get("artifact_version_id") not in artifacts:
            messages.append(f"{record.path}:{record.heading_line}: artifact_version_id not found")
        if record.data.get("review_batch_id") not in batches:
            messages.append(f"{record.path}:{record.heading_line}: review_batch_id not found")
        check_record_batch_round(record)
        if reviewers and record.data.get("reviewer_identity") not in reviewers:
            messages.append(f"{record.path}:{record.heading_line}: reviewer_identity not found in Participants")
        for raw_id in record.data.get("raw_finding_ids") or []:
            if raw_id not in raw_findings:
                messages.append(f"{record.path}:{record.heading_line}: raw_finding_id not found: {raw_id}")
    for record in records_by_type(records, "RawFinding"):
        if record.data.get("artifact_version_id") not in artifacts:
            messages.append(f"{record.path}:{record.heading_line}: artifact_version_id not found")
        if record.data.get("review_batch_id") not in batches:
            messages.append(f"{record.path}:{record.heading_line}: review_batch_id not found")
        check_record_batch_round(record)
        if reviewers and record.data.get("reviewer_identity") not in reviewers:
            messages.append(f"{record.path}:{record.heading_line}: reviewer_identity not found in Participants")
    for record in records_by_type(records, "NormalizationRecord"):
        for raw_id in record.data.get("source_raw_finding_ids") or []:
            if raw_id not in raw_findings:
                messages.append(f"{record.path}:{record.heading_line}: source raw finding not found: {raw_id}")
        if record.data.get("canonical_finding_id") not in canonical_findings:
            messages.append(f"{record.path}:{record.heading_line}: canonical_finding_id not found")
    for record in records_by_type(records, "CanonicalFinding"):
        if record.data.get("target_artifact_version_id") not in artifacts:
            messages.append(f"{record.path}:{record.heading_line}: target artifact not found")
        for raw_id in record.data.get("source_raw_finding_ids") or []:
            if raw_id not in raw_findings:
                messages.append(f"{record.path}:{record.heading_line}: source raw finding not found: {raw_id}")
        if record.data.get("normalization_record_id") not in normalizations:
            messages.append(f"{record.path}:{record.heading_line}: normalization_record_id not found")
    for record in records_by_type(records, "MaterialityChallenge"):
        if record.data.get("canonical_finding_id") not in canonical_findings:
            messages.append(f"{record.path}:{record.heading_line}: canonical finding not found")
    for record in records_by_type(records, "AuthorResponse"):
        if record.data.get("canonical_finding_id") not in canonical_findings:
            messages.append(f"{record.path}:{record.heading_line}: canonical finding not found")
        artifact = record.data.get("resulting_artifact_version_id_or_null")
        if artifact and artifact not in artifacts:
            messages.append(f"{record.path}:{record.heading_line}: resulting artifact not found: {artifact}")
    for record in records_by_type(records, "ClarificationRecord"):
        if record.data.get("canonical_finding_id") not in canonical_findings:
            messages.append(f"{record.path}:{record.heading_line}: canonical finding not found")
    for record in records_by_type(records, "ReReviewDecision"):
        if record.data.get("canonical_finding_id") not in canonical_findings:
            messages.append(f"{record.path}:{record.heading_line}: canonical finding not found")
        if record.data.get("review_batch_id") not in batches:
            messages.append(f"{record.path}:{record.heading_line}: review_batch_id not found")
        check_record_batch_round(record)
        artifact = record.data.get("artifact_version_id_or_null")
        if artifact and artifact not in artifacts:
            messages.append(f"{record.path}:{record.heading_line}: artifact_version_id_or_null not found")
        if reviewers and record.data.get("reviewer_identity") not in reviewers:
            messages.append(f"{record.path}:{record.heading_line}: reviewer_identity not found in Participants")
    for record in records_by_type(records, "ValidationEvidence"):
        if record.data.get("target_artifact_version_id") not in artifacts:
            messages.append(f"{record.path}:{record.heading_line}: target_artifact_version_id not found")
    for record in records_by_type(records, "EscalationRecord"):
        for finding_id in record.data.get("affected_finding_ids") or []:
            if finding_id != RUN_SCOPE_SENTINEL and finding_id not in canonical_findings:
                messages.append(f"{record.path}:{record.heading_line}: affected finding not found: {finding_id}")
    for record in records_by_type(records, "HumanDecision"):
        for target_id in record.data.get("affected_finding_ids_or_validator_ids") or []:
            if target_id != RUN_SCOPE_SENTINEL and target_id not in canonical_findings and target_id not in validators:
                messages.append(f"{record.path}:{record.heading_line}: affected finding or validator not found: {target_id}")
    for record in records_by_type(records, "AbortRecord"):
        artifact = record.data.get("artifact_version_id_or_null")
        if artifact and artifact not in artifacts:
            messages.append(f"{record.path}:{record.heading_line}: artifact_version_id_or_null not found")
        for finding_id in record.data.get("unresolved_finding_ids") or []:
            if finding_id not in canonical_findings:
                messages.append(f"{record.path}:{record.heading_line}: unresolved finding not found: {finding_id}")
    for record in records_by_type(records, "TerminationRecord"):
        artifact = record.data.get("final_artifact_version_id_or_null")
        if artifact and artifact not in artifacts:
            messages.append(f"{record.path}:{record.heading_line}: final artifact not found: {artifact}")
        for finding_id in record.data.get("unresolved_finding_ids") or []:
            if finding_id not in canonical_findings:
                messages.append(f"{record.path}:{record.heading_line}: unresolved finding not found: {finding_id}")
    for record in records_by_type(records, "FinalReport"):
        if record.data.get("termination_record_id") not in termination_records:
            messages.append(f"{record.path}:{record.heading_line}: termination_record_id not found")
        artifact = record.data.get("final_artifact_version_id_or_null")
        if artifact and artifact not in artifacts:
            messages.append(f"{record.path}:{record.heading_line}: final artifact not found: {artifact}")
        for finding_id in record.data.get("unresolved_finding_ids") or []:
            if finding_id not in canonical_findings:
                messages.append(f"{record.path}:{record.heading_line}: unresolved finding not found: {finding_id}")
        backlog_path = record.data.get("backlog_path")
        if backlog_path and not (run / str(backlog_path)).exists():
            messages.append(f"{record.path}:{record.heading_line}: backlog_path not found")
    return CheckResult(not messages, messages or ["link checks passed"])


def check_reviewer_isolation(run: Path) -> CheckResult:
    messages: list[str] = []
    all_records = parse_run_records(run)
    batches = {str(record.data.get("review_batch_id")): record for record in records_by_type(all_records, "ReviewBatch")}
    if detect_run_layout(run) == DEFAULT_LAYOUT:
        review_files = sorted((run / "rounds").glob("round-*/reviews/*.md")) if (run / "rounds").exists() else []
    else:
        review_files = sorted((run / "reviews").glob("round-*-*.md")) if (run / "reviews").exists() else []
    seen: set[tuple[str, str, str]] = set()
    for path in review_files:
        if detect_run_layout(run) == DEFAULT_LAYOUT:
            match = re.fullmatch(r"round-(?P<round>\d+)", path.parent.parent.name)
            if not match:
                messages.append(f"{path}: review file must be under rounds/round-NNN/reviews/")
                continue
            round_id = f"round-{int(match.group('round'))}"
            reviewer = path.stem
        else:
            match = re.match(r"round-(?P<round>\d+)-(?P<reviewer>.+)\.md$", path.name)
            if not match:
                messages.append(f"{path}: review file name must be round-<n>-<reviewer>.md")
                continue
            round_id = f"round-{match.group('round')}"
            reviewer = match.group("reviewer")
        raw_records = records_by_type(parse_records_from_file(path), "RawReviewerOutput")
        if not raw_records:
            messages.append(f"{path}: missing RawReviewerOutput record")
            continue
        for record in raw_records:
            reviewer_slug = slugify(str(record.data.get("reviewer_identity")))
            batch_id = str(record.data.get("review_batch_id"))
            expected_batch_suffix = slugify(batch_id)
            if reviewer not in {reviewer_slug, f"{reviewer_slug}-{expected_batch_suffix}"}:
                messages.append(f"{path}:{record.heading_line}: reviewer_identity does not match filename")
            key = (round_id, str(record.data.get("reviewer_identity")), batch_id)
            if key in seen:
                messages.append(
                    f"{path}:{record.heading_line}: duplicate RawReviewerOutput for "
                    f"{round_id} {record.data.get('reviewer_identity')} {batch_id}"
                )
            seen.add(key)
            batch = batches.get(batch_id)
            is_first_fresh_review = (
                round_id == "round-1" and batch is not None and batch.data.get("review_mode") == FRESH_REVIEW_MODE
            )
            if is_first_fresh_review and record.data.get("is_first_round_independent") is not True:
                messages.append(f"{path}:{record.heading_line}: first-round output must be independent")
    messages.extend(reviewer_cli_invocation_messages(run, all_records))
    return CheckResult(not messages, messages or ["reviewer isolation checks passed"])


def required_validators(records: list[Record]) -> list[str]:
    policy = first_record(records, "Policy")
    if not policy:
        return []
    validators = policy.data.get("required_validator_ids")
    return validators if isinstance(validators, list) else []


def validator_status(records: list[Record]) -> dict[str, str]:
    status: dict[str, str] = {}
    for record in records_by_type(records, "ValidationEvidence"):
        validator = record.data.get("validator_id")
        result = record.data.get("result")
        if validator and result:
            status[str(validator)] = str(result)
    return status


def latest_validator_evidence(records: list[Record]) -> dict[str, Record]:
    latest: dict[str, Record] = {}
    for record in records_by_type(records, "ValidationEvidence"):
        validator = record.data.get("validator_id")
        if validator:
            latest[str(validator)] = record
    return latest


def unresolved_blockers(records: list[Record]) -> list[str]:
    unresolved: list[str] = []
    resolved_states = {"resolved", "verified", "rejection_accepted", "closed"}
    for record in records_by_type(records, "CanonicalFinding"):
        data = record.data
        in_scope = data.get("scope_classification") == "in_scope"
        blocking = data.get("blocking_status") in {"blocking", "promoted_by_human"}
        material = data.get("materiality") == "material" or data.get("materiality_status") in {
            "undisputed",
            "disputed_materiality",
        }
        lifecycle = data.get("lifecycle_state")
        if in_scope and blocking and material and lifecycle not in resolved_states:
            unresolved.append(str(data.get("canonical_finding_id")))
    return unresolved


def unresolved_needs_human(records: list[Record]) -> list[str]:
    finding_ids: list[str] = []
    for record in records_by_type(records, "ReReviewDecision"):
        if record.data.get("decision") == "needs_human":
            finding_ids.append(str(record.data.get("canonical_finding_id")))
    return finding_ids


def max_remediation_rounds_per_finding(records: list[Record]) -> int:
    scope = first_record(records, "ReviewScope")
    if scope is not None:
        value = scope.data.get("max_remediation_rounds_per_finding")
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    policy = first_record(records, "Policy")
    if policy is not None:
        round_limits = policy.data.get("round_limits")
        if isinstance(round_limits, dict):
            value = round_limits.get("max_remediation_rounds_per_finding")
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.isdigit():
                return int(value)
    return 2


def rereview_attempt_records(
    records: list[Record],
    finding_id: str,
    reviewer_identity: str | None = None,
) -> list[Record]:
    attempts: list[Record] = []
    for record in records_by_type(records, "ReReviewDecision"):
        if str(record.data.get("canonical_finding_id")) != finding_id:
            continue
        if reviewer_identity is not None and str(record.data.get("reviewer_identity")) != reviewer_identity:
            continue
        attempts.append(record)
    return attempts


def rereview_attempt_counts(records: list[Record]) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for record in records_by_type(records, "ReReviewDecision"):
        finding_id = str(record.data.get("canonical_finding_id"))
        reviewer_identity = str(record.data.get("reviewer_identity"))
        counts[(finding_id, reviewer_identity)] = counts.get((finding_id, reviewer_identity), 0) + 1
    return counts


def remediation_cap_blockers(
    records: list[Record],
    finding_ids: list[str],
    reviewer_identity: str | None = None,
) -> list[tuple[str, str | None, int, str, int]]:
    max_attempts = max_remediation_rounds_per_finding(records)
    blockers: list[tuple[str, str | None, int, str, int]] = []
    seen: set[str] = set()
    for finding_id in finding_ids:
        finding_id = str(finding_id)
        if finding_id in seen:
            continue
        seen.add(finding_id)
        attempts = rereview_attempt_records(records, finding_id, reviewer_identity)
        if not attempts:
            if max_attempts <= 0:
                blockers.append((finding_id, reviewer_identity, 0, "no_attempts_allowed", max_attempts))
            continue
        latest_decision = str(attempts[-1].data.get("decision"))
        if latest_decision == "needs_human":
            blockers.append((finding_id, reviewer_identity, len(attempts), latest_decision, max_attempts))
        elif latest_decision in UNRESOLVED_REREVIEW_DECISIONS and len(attempts) >= max_attempts:
            blockers.append((finding_id, reviewer_identity, len(attempts), latest_decision, max_attempts))
    return blockers


def terminal_records(run: Path) -> tuple[Record | None, Record | None]:
    path = run / REPORT_FILENAME
    if not path.is_file():
        return None, None
    records = parse_records_from_file(path)
    return first_record(records, "TerminationRecord"), first_record(records, "FinalReport")


def check_terminal(run: Path) -> CheckResult:
    records = parse_run_records(run)
    termination, final_report = terminal_records(run)
    return check_terminal_records(run, records, termination, final_report)


def check_terminal_records(
    run: Path,
    records: list[Record],
    termination: Record | None,
    final_report: Record | None,
) -> CheckResult:
    messages: list[str] = []
    if termination is None:
        messages.append(f"missing {REPORT_FILENAME} TerminationRecord")
    if final_report is None:
        messages.append(f"missing {REPORT_FILENAME} FinalReport")
    if termination and final_report:
        if termination.data.get("terminal_condition") != final_report.data.get("terminal_condition"):
            messages.append("TerminationRecord and FinalReport terminal_condition differ")
        if termination.data.get("final_artifact_version_id_or_null") != final_report.data.get(
            "final_artifact_version_id_or_null"
        ):
            messages.append("TerminationRecord and FinalReport final_artifact_version_id_or_null differ")
    terminal_condition = termination.data.get("terminal_condition") if termination else None
    if terminal_condition in {"consensus_reached", "round_limit_reached"}:
        messages.extend(reviewer_cli_invocation_messages(run, records))
    status = validator_status(records)
    if terminal_condition == "consensus_reached":
        required = required_validators(records)
        latest = latest_validator_evidence(records)
        for validator in required:
            result = status.get(validator)
            if result not in {"pass", "waived"}:
                messages.append(f"required validator is not pass/waived: {validator}={result or 'missing'}")
            if result == "waived":
                evidence = latest.get(validator)
                if evidence is None:
                    messages.append(f"waived validator has no ValidationEvidence: {validator}")
                else:
                    if evidence.data.get("waiver_authority_or_null") is None:
                        messages.append(f"waived validator missing authority: {validator}")
                    if evidence.data.get("waiver_rationale_or_null") is None:
                        messages.append(f"waived validator missing rationale: {validator}")
        blockers = unresolved_blockers(records)
        if blockers:
            messages.append(f"unresolved in-scope blocking material findings: {', '.join(blockers)}")
        needs_human = unresolved_needs_human(records)
        if needs_human:
            messages.append(f"unresolved needs_human rereview decisions: {', '.join(needs_human)}")
    if terminal_condition == "escalated_to_human":
        has_escalation = bool(records_by_type(records, "EscalationRecord"))
        has_terminal_decision = any(
            record.data.get("decision_type") == "terminate_escalated_to_human"
            for record in records_by_type(records, "HumanDecision")
        )
        if not has_escalation and not has_terminal_decision:
            messages.append("escalated_to_human termination requires EscalationRecord or terminal HumanDecision")
    if terminal_condition == "aborted" and not records_by_type(records, "AbortRecord"):
        messages.append("aborted termination requires AbortRecord")
    return CheckResult(not messages, messages or ["terminal checks passed"])
