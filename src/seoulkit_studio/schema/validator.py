"""edit_plan.json schema + Duration Invariant validation (Stage 5 spec, ch. 03-04).

Two checks, run independently because the Duration Invariant is an arithmetic
constraint across sibling fields that plain JSON Schema cannot express:

1. JSON Schema structural validation (edit_plan.schema.json).
2. Duration Invariant, per segment:

     segment_duration_ms = end_ms - start_ms
     source_used_ms      = clip_out_ms - clip_in_ms

     hold_strategy in {none, source_hold}:
         segment_duration_ms == source_used_ms                (+/- tolerance)
     hold_strategy == settle_frame_hold:
         segment_duration_ms == source_used_ms + hold_ms       (+/- tolerance)

This module only validates. Pre-flight concerns beyond this (file existence,
severity -> effective_status mapping) belong to a later phase.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib import resources
from typing import Any

import jsonschema

DEFAULT_DURATION_TOLERANCE_MS = 50

_SCHEMA_FILENAME = "edit_plan.schema.json"


def _load_schema() -> dict[str, Any]:
    schema_text = resources.files(__package__).joinpath(_SCHEMA_FILENAME).read_text()
    return json.loads(schema_text)


_SCHEMA = _load_schema()
_VALIDATOR = jsonschema.Draft7Validator(_SCHEMA)


@dataclass
class Issue:
    code: str
    message: str
    segment_ref: str | None = None


@dataclass
class ValidationResult:
    passed: bool
    issues: list[Issue] = field(default_factory=list)


def _segment_ref(segment: dict[str, Any]) -> str:
    return f"beat={segment.get('beat')} shot={segment.get('shot')}"


def validate_schema(data: dict[str, Any]) -> list[Issue]:
    issues = []
    for error in sorted(_VALIDATOR.iter_errors(data), key=str):
        path = "/".join(str(p) for p in error.absolute_path) or "<root>"
        issues.append(Issue(code="SCHEMA_VIOLATION", message=f"{path}: {error.message}"))
    return issues


def segment_expected_duration_ms(segment: dict[str, Any]) -> int | None:
    """The Duration Invariant's expected segment_duration_ms, derived purely
    from the segment's own clip_in_ms/clip_out_ms/hold_strategy/hold_ms
    fields - extracted so callers other than validate_duration_invariant()
    (e.g. a post-render aggregate QC check comparing this sum against a real
    ffprobe-measured render, added in Phase 10 - see render/report.py) can
    reuse the exact same formula instead of re-deriving it independently.
    Returns None if a required field is missing or hold_strategy isn't a
    recognized value - both cases the caller can treat as "already reported
    by schema validation," same as validate_duration_invariant() does.
    """
    try:
        source_used_ms = segment["clip_out_ms"] - segment["clip_in_ms"]
        hold_strategy = segment["hold_strategy"]
        hold_ms = segment["hold_ms"]
    except KeyError:
        return None

    if hold_strategy in ("none", "source_hold"):
        return source_used_ms
    elif hold_strategy == "settle_frame_hold":
        return source_used_ms + hold_ms
    else:
        return None


def validate_duration_invariant(
    data: dict[str, Any], tolerance_ms: int = DEFAULT_DURATION_TOLERANCE_MS
) -> list[Issue]:
    issues: list[Issue] = []
    for segment in data.get("segments", []):
        ref = _segment_ref(segment)
        try:
            segment_duration_ms = segment["end_ms"] - segment["start_ms"]
            source_used_ms = segment["clip_out_ms"] - segment["clip_in_ms"]
            hold_ms = segment["hold_ms"]
        except KeyError:
            # Missing fields are already reported by schema validation.
            continue

        expected_ms = segment_expected_duration_ms(segment)
        if expected_ms is None:
            # Missing fields or an invalid hold_strategy value - already
            # reported by schema validation.
            continue

        diff_ms = abs(segment_duration_ms - expected_ms)
        if diff_ms > tolerance_ms:
            issues.append(
                Issue(
                    code="DURATION_INVARIANT_VIOLATION",
                    message=(
                        f"segment_duration_ms={segment_duration_ms} != "
                        f"expected {expected_ms} (source_used_ms={source_used_ms}, "
                        f"hold_ms={hold_ms}), diff={diff_ms}ms > "
                        f"tolerance({tolerance_ms}ms)"
                    ),
                    segment_ref=ref,
                )
            )
    return issues


def validate_edit_plan(
    data: dict[str, Any], tolerance_ms: int = DEFAULT_DURATION_TOLERANCE_MS
) -> ValidationResult:
    issues = validate_schema(data)
    # Duration Invariant depends on fields the schema check may have found
    # missing/malformed; only run it once the plan is structurally valid.
    if not issues:
        issues = validate_duration_invariant(data, tolerance_ms=tolerance_ms)
    return ValidationResult(passed=not issues, issues=issues)
