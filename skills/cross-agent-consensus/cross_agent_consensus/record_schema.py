"""Executable record schema metadata for cross-agent-consensus records."""

from __future__ import annotations

from types import NoneType


COMMON_FIELDS = [
    "record_type",
    "schema_version",
    "run_id",
    "actor_identity",
    "created_at",
]

REQUIRED_FIELDS = {
    "TaskBrief": [
        "task_brief_id",
        "artifact_locator",
        "objective",
        "success_criteria",
        "profile",
        "human_supervisor_identity_or_null",
    ],
    "Policy": [
        "policy_id",
        "profile",
        "required_validator_ids",
        "round_limits",
        "materiality_rules",
        "escalation_policy",
        "waiver_authority_or_null",
    ],
    "Participants": [
        "participants_record_id",
        "orchestrator_identity",
        "author_identity",
        "reviewer_identities",
        "human_supervisor_identity_or_null",
    ],
    "ReviewScope": [
        "review_scope_id",
        "objective",
        "in_scope",
        "out_of_scope",
        "review_modes_allowed",
        "max_fresh_review_rounds",
        "max_remediation_rounds_per_finding",
        "promotion_policy_or_null",
    ],
    "ReviewBatch": [
        "review_batch_id",
        "review_scope_id",
        "review_mode",
        "target_artifact_version_id",
        "source_finding_ids",
        "round_id",
    ],
    "ArtifactVersion": [
        "artifact_version_id",
        "predecessor_id_or_null",
        "content_locator",
        "content_hash_or_null",
        "produced_by",
    ],
    "RawReviewerOutput": [
        "raw_output_id",
        "reviewer_identity",
        "review_batch_id",
        "artifact_version_id",
        "raw_finding_ids",
        "is_first_round_independent",
    ],
    "RawFinding": [
        "raw_finding_id",
        "reviewer_identity",
        "artifact_version_id",
        "review_batch_id",
        "location",
        "claim",
        "evidence",
        "severity_or_materiality_claim",
        "scope_classification",
        "blocking_status",
        "suggested_fix_or_null",
    ],
    "NormalizationRecord": [
        "normalization_record_id",
        "source_raw_finding_ids",
        "normalizer_identity",
        "classifier_identity",
        "materiality",
        "scope_classification",
        "blocking_status",
        "rationale",
        "normalized_finding_id",
    ],
    "NormalizedFinding": [
        "normalized_finding_id",
        "target_artifact_version_id",
        "source_raw_finding_ids",
        "normalization_record_id",
        "materiality",
        "materiality_status",
        "scope_classification",
        "blocking_status",
        "lifecycle_state",
        "claim",
        "rationale_or_summary",
        "clarification_pending",
    ],
    "MaterialityChallenge": [
        "materiality_challenge_id",
        "normalized_finding_id",
        "claimed_materiality",
        "rationale",
        "supporting_record_ids",
    ],
    "AuthorResponse": [
        "author_response_id",
        "normalized_finding_id",
        "response_type",
        "rationale",
        "resulting_artifact_version_id_or_null",
        "clarification_request_or_null",
    ],
    "ClarificationRecord": [
        "clarification_record_id",
        "normalized_finding_id",
        "requested_by",
        "responded_by",
        "question",
        "answer_or_reason_unavailable",
    ],
    "ReReviewDecision": [
        "re_review_decision_id",
        "normalized_finding_id",
        "reviewer_identity",
        "decision",
        "rationale",
        "artifact_version_id_or_null",
        "review_batch_id",
    ],
    "ValidationEvidence": [
        "validation_evidence_id",
        "validator_id",
        "target_artifact_version_id",
        "result",
        "payload_reference",
        "produced_by",
        "waiver_authority_or_null",
        "waiver_rationale_or_null",
    ],
    "EscalationRecord": [
        "escalation_record_id",
        "affected_finding_ids",
        "reason",
        "requested_authority",
    ],
    "HumanDecision": [
        "human_decision_id",
        "affected_finding_ids_or_validator_ids",
        "decision_type",
        "rationale",
        "binding_authority",
        "requires_new_artifact_version",
    ],
    "AbortRecord": [
        "abort_record_id",
        "trigger_actor",
        "reason",
        "artifact_version_id_or_null",
        "unresolved_finding_ids",
    ],
    "TerminationRecord": [
        "termination_record_id",
        "terminal_condition",
        "reason",
        "final_artifact_version_id_or_null",
        "unresolved_finding_ids",
        "supporting_record_ids",
    ],
    "FinalReport": [
        "final_report_id",
        "termination_record_id",
        "terminal_condition",
        "final_artifact_version_id_or_null",
        "validator_status",
        "unresolved_finding_ids",
        "backlog_path",
    ],
    "ConfigResolution": [
        "config_resolution_id",
        "config_schema_version",
        "sources",
        "effective_values",
        "diagnostics",
        "redactions",
    ],
    "OperatorApproval": [
        "operator_approval_id",
        "approved_actors",
        "scope_run_id",
        "scope_round_id",
        "scope_phase",
        "mechanism",
        "operator_identity_or_null",
    ],
}

ENUMS = {
    "review_mode": {"fresh_review", "remediation_verification", "regression_check", "scope_triage"},
    "scope_classification": {"in_scope", "out_of_scope", "unclear_scope"},
    "blocking_status": {"blocking", "non_blocking", "deferred", "promoted_by_human"},
    "materiality_status": {"undisputed", "disputed_materiality", "resolved_after_dispute"},
    "response_type": {"accept", "reject", "partially_accept", "request_clarification"},
    "decision": {"verified", "rejection_accepted", "still_valid", "disputed", "needs_human"},
    "result": {"pass", "fail", "error", "waived"},
    "decision_type": {
        "mark_resolved",
        "accept_author_rejection",
        "require_revision",
        "mark_non_material",
        "dispute_materiality",
        "waive_validator",
        "terminate_escalated_to_human",
        "abort_run",
    },
    "terminal_condition": {"consensus_reached", "round_limit_reached", "escalated_to_human", "aborted"},
    "mechanism": {"cli_approved_flag", "policy_unattended"},
    "capture_origin": {"live_cli", "manual_import", "host_subagent", "stdin"},
}

FIELD_ALIASES: dict[str, dict[str, str]] = {
    "RawFinding": {
        "suggested_fix": "suggested_fix_or_null",
        "severity": "severity_or_materiality_claim",
        "severity_or_null": "severity_or_materiality_claim",
    },
    "FinalReport": {
        "target_artifact_version_id": "final_artifact_version_id_or_null",
    },
}

KNOWN_RECORD_TYPES = set(REQUIRED_FIELDS)

ID_FIELDS = {
    "TaskBrief": "task_brief_id",
    "Policy": "policy_id",
    "Participants": "participants_record_id",
    "ReviewScope": "review_scope_id",
    "ReviewBatch": "review_batch_id",
    "ArtifactVersion": "artifact_version_id",
    "RawReviewerOutput": "raw_output_id",
    "RawFinding": "raw_finding_id",
    "NormalizationRecord": "normalization_record_id",
    "NormalizedFinding": "normalized_finding_id",
    "MaterialityChallenge": "materiality_challenge_id",
    "AuthorResponse": "author_response_id",
    "ClarificationRecord": "clarification_record_id",
    "ReReviewDecision": "re_review_decision_id",
    "ValidationEvidence": "validation_evidence_id",
    "EscalationRecord": "escalation_record_id",
    "HumanDecision": "human_decision_id",
    "AbortRecord": "abort_record_id",
    "TerminationRecord": "termination_record_id",
    "FinalReport": "final_report_id",
    "ConfigResolution": "config_resolution_id",
    "OperatorApproval": "operator_approval_id",
}


LIST_FIELDS = {
    "affected_finding_ids",
    "affected_finding_ids_or_validator_ids",
    "approved_actors",
    "in_scope",
    "out_of_scope",
    "raw_finding_ids",
    "redactions",
    "required_validator_ids",
    "review_modes_allowed",
    "reviewer_identities",
    "source_finding_ids",
    "source_raw_finding_ids",
    "sources",
    "success_criteria",
    "supporting_record_ids",
    "unresolved_finding_ids",
}

MAPPING_FIELDS = {
    "diagnostics",
    "effective_values",
    "materiality_rules",
    "round_limits",
    "validator_status",
}

BOOLEAN_FIELDS = {
    "clarification_pending",
    "is_first_round_independent",
    "requires_new_artifact_version",
}

INTEGER_FIELDS = {
    "max_fresh_review_rounds",
    "max_remediation_rounds_per_finding",
}

NULLABLE_STRING_FIELDS = {
    field
    for fields in REQUIRED_FIELDS.values()
    for field in fields
    if field.endswith("_or_null")
}

REQUIRED_FIELD_TYPES: dict[str, tuple[type[object], ...]] = {}
for _field in {field for fields in REQUIRED_FIELDS.values() for field in fields}:
    if _field in LIST_FIELDS:
        REQUIRED_FIELD_TYPES[_field] = (list,)
    elif _field in MAPPING_FIELDS:
        REQUIRED_FIELD_TYPES[_field] = (dict,)
    elif _field in BOOLEAN_FIELDS:
        REQUIRED_FIELD_TYPES[_field] = (bool,)
    elif _field in INTEGER_FIELDS:
        REQUIRED_FIELD_TYPES[_field] = (int,)
    elif _field in NULLABLE_STRING_FIELDS:
        REQUIRED_FIELD_TYPES[_field] = (str, NoneType)
    else:
        REQUIRED_FIELD_TYPES[_field] = (str,)


def expected_type_label(field: str) -> str:
    expected = REQUIRED_FIELD_TYPES[field]
    return " or ".join(value.__name__ for value in expected)


OPTIONAL_FIELD_TYPES: dict[str, tuple[type[object], ...]] = {
    "content_locator_base_or_null": (str, NoneType),
    "raw_payload_path": (str,),
    "raw_payload_sha256": (str,),
    "payload_sha256": (str,),
    "capture_origin": (str,),
    "session_id_or_null": (str, NoneType),
    "session_path_or_null": (str, NoneType),
    "prompt_sha256_or_null": (str, NoneType),
    "approval_binding_version": (str,),
    "approved_invocations": (list,),
}


def optional_type_label(field: str) -> str:
    expected = OPTIONAL_FIELD_TYPES[field]
    return " or ".join(value.__name__ for value in expected)
