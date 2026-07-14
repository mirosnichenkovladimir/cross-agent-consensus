"""Version-gated decoding for historical CAC finding records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


CURRENT_RECORD_SCHEMA_VERSION = "m2-markdown-2"
LEGACY_RECORD_SCHEMA_VERSIONS = frozenset({"m2-markdown-1"})
SUPPORTED_RECORD_SCHEMA_VERSIONS = LEGACY_RECORD_SCHEMA_VERSIONS | {
    CURRENT_RECORD_SCHEMA_VERSION
}

CURRENT_FINDING_RECORD_TYPE = "NormalizedFinding"
CURRENT_FINDING_ID_FIELD = "normalized_finding_id"

# These two values exist only at the historical load boundary. Current record
# factories, lifecycle evaluators, prompts, reports, and validators must not
# import or emit them.
HISTORICAL_FINDING_RECORD_TYPE = "CanonicalFinding"
HISTORICAL_FINDING_ID_FIELD = "canonical_finding_id"

FINDING_REFERENCE_RECORD_TYPES = frozenset(
    {
        "NormalizationRecord",
        "MaterialityChallenge",
        "AuthorResponse",
        "ClarificationRecord",
        "ReReviewDecision",
    }
)


@dataclass(frozen=True)
class DecodedRecord:
    record_type: str
    data: dict[str, Any]
    finding_schema_origin: str | None


def recognized_record_type(record_type: str, current_record_types: set[str]) -> bool:
    return record_type in current_record_types or record_type == HISTORICAL_FINDING_RECORD_TYPE


def decode_record(
    heading_record_type: str,
    data: dict[str, Any],
    current_record_types: set[str],
) -> DecodedRecord:
    """Return one current record or reject a schema/name combination.

    Historical identifiers are copied byte-for-byte into
    ``normalized_finding_id``. No compatibility alias survives in the returned
    mapping.
    """

    schema_version = data.get("schema_version")
    declared_record_type = str(data.get("record_type") or heading_record_type)
    uses_historical_type = (
        heading_record_type == HISTORICAL_FINDING_RECORD_TYPE
        or declared_record_type == HISTORICAL_FINDING_RECORD_TYPE
    )
    uses_current_type = (
        heading_record_type == CURRENT_FINDING_RECORD_TYPE
        or declared_record_type == CURRENT_FINDING_RECORD_TYPE
    )
    has_historical_id = HISTORICAL_FINDING_ID_FIELD in data
    has_current_id = CURRENT_FINDING_ID_FIELD in data

    if uses_historical_type and uses_current_type:
        raise ValueError("finding heading and frontmatter mix historical and current record names")
    if has_historical_id and has_current_id:
        raise ValueError("finding record contains both historical and current identifier fields")

    if schema_version == CURRENT_RECORD_SCHEMA_VERSION:
        if uses_historical_type or has_historical_id:
            raise ValueError(
                f"historical finding names are not valid under {CURRENT_RECORD_SCHEMA_VERSION}"
            )
        if declared_record_type not in current_record_types:
            raise ValueError(f"frontmatter declares unknown record type {declared_record_type}")
        origin = (
            "current"
            if uses_current_type
            or (
                declared_record_type in FINDING_REFERENCE_RECORD_TYPES
                and has_current_id
            )
            else None
        )
        return DecodedRecord(declared_record_type, data, origin)

    if schema_version in LEGACY_RECORD_SCHEMA_VERSIONS:
        if uses_current_type or has_current_id:
            raise ValueError(
                f"current finding names are not valid under historical schema {schema_version}"
            )
        if uses_historical_type:
            if not has_historical_id:
                raise ValueError(
                    f"historical {HISTORICAL_FINDING_RECORD_TYPE} lacks "
                    f"{HISTORICAL_FINDING_ID_FIELD}"
                )
            decoded = dict(data)
            decoded["record_type"] = CURRENT_FINDING_RECORD_TYPE
            decoded[CURRENT_FINDING_ID_FIELD] = decoded.pop(HISTORICAL_FINDING_ID_FIELD)
            return DecodedRecord(CURRENT_FINDING_RECORD_TYPE, decoded, "legacy")
        if has_historical_id:
            if declared_record_type not in FINDING_REFERENCE_RECORD_TYPES:
                raise ValueError(
                    f"historical identifier field is not valid for {declared_record_type}"
                )
            decoded = dict(data)
            decoded[CURRENT_FINDING_ID_FIELD] = decoded.pop(HISTORICAL_FINDING_ID_FIELD)
            return DecodedRecord(declared_record_type, decoded, "legacy")
        if declared_record_type not in current_record_types:
            raise ValueError(f"frontmatter declares unknown record type {declared_record_type}")
        return DecodedRecord(declared_record_type, data, None)

    if uses_historical_type or has_historical_id:
        raise ValueError(
            f"historical finding names require one of {sorted(LEGACY_RECORD_SCHEMA_VERSIONS)}"
        )
    if declared_record_type not in current_record_types:
        raise ValueError(f"frontmatter declares unknown record type {declared_record_type}")
    return DecodedRecord(declared_record_type, data, None)
