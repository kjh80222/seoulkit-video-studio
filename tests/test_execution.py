import hashlib
import json
from pathlib import Path

import pytest

from seoulkit_studio.execution import (
    LoadError,
    compute_effective_status,
    compute_execution_result,
    evaluate_plan,
    load_plan_file,
)
from seoulkit_studio.preflight import PreflightIssue, PreflightResult


def base_plan() -> dict:
    return {
        "schema_version": "1.0",
        "project": "execution-test",
        "format": "MINI",
        "status": "READY",
        "timing_grade_default": "EXACT",
        "voice": {
            "audio_file": "clips/audio/voice_final.wav",
            "aligned": True,
            "alignment_file": None,
            "total_duration_ms": 4800,
        },
        "segments": [
            {
                "beat": 1,
                "shot": "1A",
                "start_ms": 0,
                "end_ms": 4800,
                "timing_grade": "EXACT",
                "source_clip": "clips/shot_1a_flow.mp4",
                "source_duration_ms": 6000,
                "usable_start_ms": 0,
                "usable_end_ms": 5800,
                "key_event_end_ms": 4200,
                "settle_start_ms": 4800,
                "clip_in_ms": 0,
                "clip_out_ms": 4800,
                "camera_behavior": "movement",
                "hold_strategy": "none",
                "hold_ms": 0,
                "trim_reason": None,
                "voice_text": "In 1953, Seoul was mostly rubble.",
                "status": "OK",
                "status_note": None,
            }
        ],
        "overlays": [],
        "subtitles": [],
        "audio_layers": {
            "voice": "authoritative",
            "sfx_source": {"mode": "none", "clips": []},
            "bgm": {"mode": "none", "file": None, "reference_db": None},
        },
        "warnings": [],
    }


def touch(project_dir: Path, rel_path: str) -> None:
    path = project_dir / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake")


def make_project_dir(tmp_path: Path, plan: dict) -> Path:
    project_dir = tmp_path / "project"
    if plan.get("voice", {}).get("audio_file"):
        touch(project_dir, plan["voice"]["audio_file"])
    for segment in plan.get("segments", []):
        if segment.get("source_clip"):
            touch(project_dir, segment["source_clip"])
    return project_dir


# --- load_plan_file --------------------------------------------------------


def test_load_plan_file_success(tmp_path):
    plan_path = tmp_path / "edit_plan.json"
    plan_path.write_text(json.dumps(base_plan()))

    result = load_plan_file(plan_path)

    assert result.ok
    assert result.data["project"] == "execution-test"


def test_load_plan_file_missing_file(tmp_path):
    result = load_plan_file(tmp_path / "does_not_exist.json")

    assert not result.ok
    assert result.error.kind == "file_error"


def test_load_plan_file_malformed_json(tmp_path):
    plan_path = tmp_path / "edit_plan.json"
    plan_path.write_text("{ this is not valid json")

    result = load_plan_file(plan_path)

    assert not result.ok
    assert result.error.kind == "json_parse_error"


# --- compute_execution_result ----------------------------------------------


def test_compute_execution_result_error_on_load_error():
    load_error = LoadError("json_parse_error", "boom")
    assert compute_execution_result(load_error, None) == "ERROR"


def test_compute_execution_result_error_on_schema_violation():
    preflight_result = PreflightResult(
        preflight_result="FAIL", issues=[PreflightIssue("SCHEMA_VIOLATION", "blocking", "status: missing")]
    )
    assert compute_execution_result(None, preflight_result) == "ERROR"


def test_compute_execution_result_blocked_on_other_blocking_issue():
    preflight_result = PreflightResult(
        preflight_result="FAIL", issues=[PreflightIssue("MISSING_CLIP", "blocking", "clips/shot_1a_flow.mp4 not found")]
    )
    assert compute_execution_result(None, preflight_result) == "BLOCKED"


def test_compute_execution_result_pass_when_clean():
    preflight_result = PreflightResult(preflight_result="PASS", issues=[])
    assert compute_execution_result(None, preflight_result) == "PASS"


def test_compute_execution_result_pass_with_only_warnings():
    preflight_result = PreflightResult(
        preflight_result="PASS", issues=[PreflightIssue("USABLE_RANGE_MISSING", "warning", "...")]
    )
    assert compute_execution_result(None, preflight_result) == "PASS"


# --- cross-check: Phase 1's severity_to_execution() and Phase 2's ---------
# --- compute_effective_status() describe the same ch.18 ladder ------------


@pytest.mark.parametrize(
    "severity,execution_result,issues,expected",
    [
        ("blocking", "BLOCKED", [], "NOT_READY"),
        ("blocking", "ERROR", [], "NOT_READY"),
        ("warning", "PASS", [PreflightIssue("X", "warning", "...")], "REVIEW_REQUIRED"),
    ],
)
def test_effective_status_matches_severity_to_execution_for_blocking_and_warning(
    severity, execution_result, issues, expected
):
    # These are two independently-written mappings (Phase 1's
    # severity_to_execution, driven by a single severity; Phase 2's
    # compute_effective_status, driven by execution_result + issues) that
    # are only supposed to agree because both implement ch.18's table by
    # hand. Nothing enforces that today (see docs/phase-plan.md Known
    # gaps) - this pins the agreement down so a future edit to either one
    # that breaks the correspondence fails a test instead of silently
    # drifting.
    from seoulkit_studio.preflight import severity_to_execution

    assert severity_to_execution(severity).effective_status == expected
    assert compute_effective_status(execution_result, "READY", issues) == expected


def test_effective_status_matches_severity_to_execution_for_the_clean_case():
    # severity_to_execution(None) returns effective_status=None - ch.18's
    # info row ("no forced status"). compute_effective_status's clean case
    # instead returns the plan's own declared status, defaulting to READY
    # when nothing was declared. Same meaning ("don't force a worse
    # status"), different representation - so None and "READY" are the
    # correct pair here, not a mismatch.
    from seoulkit_studio.preflight import severity_to_execution

    assert severity_to_execution(None).effective_status is None
    assert compute_effective_status("PASS", "READY", []) == "READY"


# --- compute_effective_status -----------------------------------------------


@pytest.mark.parametrize("execution_result", ["ERROR", "BLOCKED"])
def test_effective_status_is_always_not_ready_for_error_or_blocked(execution_result):
    # Declared status and issues must not matter here - ERROR/BLOCKED always wins.
    assert compute_effective_status(execution_result, "READY", []) == "NOT_READY"
    assert compute_effective_status(execution_result, "NOT_READY", []) == "NOT_READY"


def test_effective_status_pass_no_warnings_matches_declared_ready():
    assert compute_effective_status("PASS", "READY", []) == "READY"


def test_effective_status_pass_with_warning_upgrades_ready_to_review_required():
    issues = [PreflightIssue("TOTAL_DURATION_MISMATCH", "warning", "...")]
    assert compute_effective_status("PASS", "READY", issues) == "REVIEW_REQUIRED"


def test_effective_status_pass_never_downgrades_a_worse_declared_status():
    # Stage 4 already declared NOT_READY for a reason Stage 5's Pre-flight
    # can't see; a clean Pre-flight must not silently promote it to READY.
    assert compute_effective_status("PASS", "NOT_READY", []) == "NOT_READY"


def test_effective_status_defaults_missing_declared_status_to_ready():
    assert compute_effective_status("PASS", None, []) == "READY"


# --- immutability: PASS / BLOCKED / ERROR all leave the file untouched -----


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_evaluate_plan_never_modifies_the_file_on_pass(tmp_path):
    plan = base_plan()
    plan_path = tmp_path / "edit_plan.json"
    plan_path.write_text(json.dumps(plan))
    project_dir = make_project_dir(tmp_path, plan)

    before = _sha256(plan_path)
    result = evaluate_plan(plan_path, project_dir)
    after = _sha256(plan_path)

    assert result.studio_execution_result == "PASS"
    assert after == before


def test_evaluate_plan_never_modifies_the_file_on_blocked(tmp_path):
    plan = base_plan()  # source_clip referenced but never created -> MISSING_CLIP
    plan_path = tmp_path / "edit_plan.json"
    plan_path.write_text(json.dumps(plan))
    project_dir = tmp_path / "project"  # deliberately empty

    before = _sha256(plan_path)
    result = evaluate_plan(plan_path, project_dir)
    after = _sha256(plan_path)

    assert result.studio_execution_result == "BLOCKED"
    assert after == before


def test_evaluate_plan_never_modifies_the_file_on_error(tmp_path):
    plan_path = tmp_path / "edit_plan.json"
    plan_path.write_text("{ this is not valid json")
    project_dir = tmp_path / "project"

    before = _sha256(plan_path)
    result = evaluate_plan(plan_path, project_dir)
    after = _sha256(plan_path)

    assert result.studio_execution_result == "ERROR"
    assert after == before


def test_evaluate_plan_effective_status_end_to_end(tmp_path):
    plan = base_plan()
    plan_path = tmp_path / "edit_plan.json"
    plan_path.write_text(json.dumps(plan))
    project_dir = make_project_dir(tmp_path, plan)

    result = evaluate_plan(plan_path, project_dir)

    assert result.studio_execution_result == "PASS"
    assert result.effective_status == "READY"
    assert result.load_error is None
    assert result.preflight_result.preflight_result == "PASS"
