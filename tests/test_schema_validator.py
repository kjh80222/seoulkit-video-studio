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


def test_settle_frame_hold_with_zero_hold_ms_is_rejected_by_schema():
    # hold_strategy == settle_frame_hold must have hold_ms >= 1 (Stage 5 spec, ch. 03) -
    # the other direction of the same allOf constraint tested above. Without this,
    # a segment could claim a freeze-frame hold that actually holds for 0ms.
    result = validate_edit_plan(load_fixture("invalid_settle_frame_hold_zero_hold_ms.json"))
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


# --- segment_expected_duration_ms() (extracted Phase 10, ch. 15/17) --------


def test_segment_expected_duration_ms_none_hold():
    from seoulkit_studio.schema import segment_expected_duration_ms

    segment = {"clip_in_ms": 0, "clip_out_ms": 2000, "hold_strategy": "none", "hold_ms": 0}
    assert segment_expected_duration_ms(segment) == 2000


def test_segment_expected_duration_ms_source_hold():
    from seoulkit_studio.schema import segment_expected_duration_ms

    segment = {"clip_in_ms": 0, "clip_out_ms": 2000, "hold_strategy": "source_hold", "hold_ms": 0}
    assert segment_expected_duration_ms(segment) == 2000


def test_segment_expected_duration_ms_settle_frame_hold_adds_hold_ms():
    from seoulkit_studio.schema import segment_expected_duration_ms

    segment = {"clip_in_ms": 0, "clip_out_ms": 2000, "hold_strategy": "settle_frame_hold", "hold_ms": 600}
    assert segment_expected_duration_ms(segment) == 2600


def test_segment_expected_duration_ms_missing_field_returns_none():
    from seoulkit_studio.schema import segment_expected_duration_ms

    segment = {"clip_in_ms": 0, "clip_out_ms": 2000, "hold_strategy": "none"}  # no hold_ms
    assert segment_expected_duration_ms(segment) is None


def test_segment_expected_duration_ms_invalid_hold_strategy_returns_none():
    from seoulkit_studio.schema import segment_expected_duration_ms

    segment = {"clip_in_ms": 0, "clip_out_ms": 2000, "hold_strategy": "artificial_stretch", "hold_ms": 0}
    assert segment_expected_duration_ms(segment) is None


def test_validate_duration_invariant_unaffected_by_the_extraction():
    # Pure refactor check: the same fixtures that exercised
    # validate_duration_invariant() before the extraction still produce the
    # exact same pass/fail verdict and issue codes.
    assert validate_edit_plan(load_fixture("valid_minimal.json")).passed
    assert validate_edit_plan(load_fixture("valid_settle_frame_hold.json")).passed
    result = validate_edit_plan(load_fixture("invalid_duration_mismatch.json"))
    assert not result.passed
    assert result.issues[0].code == "DURATION_INVARIANT_VIOLATION"
