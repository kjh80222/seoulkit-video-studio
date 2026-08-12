import copy
import json
from pathlib import Path

import pytest

from seoulkit_studio.schema import validate_edit_plan

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text())


def test_valid_minimal_passes():
    result = validate_edit_plan(load_fixture("valid_minimal.json"))
    assert result.passed, result.issues
    assert result.issues == []


def test_valid_settle_frame_hold_passes():
    result = validate_edit_plan(load_fixture("valid_settle_frame_hold.json"))
    assert result.passed, result.issues


def test_missing_required_field_fails_schema():
    result = validate_edit_plan(load_fixture("invalid_missing_field.json"))
    assert not result.passed
    assert any(issue.code == "SCHEMA_VIOLATION" for issue in result.issues)


def test_duration_invariant_violation_is_detected():
    result = validate_edit_plan(load_fixture("invalid_duration_mismatch.json"))
    assert not result.passed
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.code == "DURATION_INVARIANT_VIOLATION"
    assert issue.segment_ref == "beat=1 shot=1A"


def test_source_hold_with_nonzero_hold_ms_is_rejected_by_schema():
    # hold_strategy in {none, source_hold} must have hold_ms == 0 (Stage 5 spec, ch. 03).
    # This is a structural constraint expressible in JSON Schema, so it is caught
    # as a SCHEMA_VIOLATION rather than reaching the Duration Invariant check.
    result = validate_edit_plan(load_fixture("invalid_source_hold_nonzero_hold_ms.json"))
    assert not result.passed
    assert any(issue.code == "SCHEMA_VIOLATION" for issue in result.issues)


def test_duration_invariant_tolerance_boundary():
    plan = load_fixture("valid_minimal.json")
    segment = plan["segments"][0]

    # Exactly at the default 50ms tolerance: still passes.
    at_tolerance = copy.deepcopy(plan)
    at_tolerance["segments"][0]["end_ms"] = segment["start_ms"] + (
        segment["clip_out_ms"] - segment["clip_in_ms"]
    ) + 50
    result = validate_edit_plan(at_tolerance)
    assert result.passed, result.issues

    # One ms past tolerance: fails.
    over_tolerance = copy.deepcopy(plan)
    over_tolerance["segments"][0]["end_ms"] = segment["start_ms"] + (
        segment["clip_out_ms"] - segment["clip_in_ms"]
    ) + 51
    result = validate_edit_plan(over_tolerance)
    assert not result.passed
    assert result.issues[0].code == "DURATION_INVARIANT_VIOLATION"


def test_artificial_stretch_is_not_a_valid_hold_strategy():
    # hold_strategy enum intentionally excludes "artificial_stretch" (Stage 4/5 spec):
    # the forbidden Hold type C has no representation in the schema at all.
    plan = load_fixture("valid_minimal.json")
    plan["segments"][0]["hold_strategy"] = "artificial_stretch"

    result = validate_edit_plan(plan)
    assert not result.passed
    assert any(issue.code == "SCHEMA_VIOLATION" for issue in result.issues)


@pytest.mark.parametrize("fixture_name", ["valid_minimal.json", "valid_settle_frame_hold.json"])
def test_valid_fixtures_are_schema_only_clean(fixture_name):
    from seoulkit_studio.schema import validate_schema

    assert validate_schema(load_fixture(fixture_name)) == []
