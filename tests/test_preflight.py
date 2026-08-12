from pathlib import Path

import pytest

from seoulkit_studio.preflight import (
    SEVERITY_MAPPING,
    run_preflight,
    severity_to_execution,
    worst_severity,
)


def base_plan() -> dict:
    return {
        "schema_version": "1.0",
        "project": "preflight-test",
        "format": "MINI",
        "status": "READY",
        "timing_grade_default": "EXACT",
        "voice": {
            "audio_file": "clips/audio/voice_final.wav",
            "aligned": True,
            "alignment_file": None,
            "total_duration_ms": 9600,
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
            },
            {
                "beat": 1,
                "shot": "1B",
                "start_ms": 4800,
                "end_ms": 9600,
                "timing_grade": "EXACT",
                "source_clip": "clips/shot_1b_flow.mp4",
                "source_duration_ms": 6000,
                "usable_start_ms": 0,
                "usable_end_ms": 5600,
                "key_event_end_ms": 3800,
                "settle_start_ms": 4400,
                "clip_in_ms": 200,
                "clip_out_ms": 5000,
                "camera_behavior": "locked-off",
                "hold_strategy": "none",
                "hold_ms": 0,
                "trim_reason": None,
                "voice_text": "To understand what happened next, look at the tariff tables.",
                "status": "OK",
                "status_note": None,
            },
        ],
        "overlays": [],
        "subtitles": [],
        "audio_layers": {
            "voice": "authoritative",
            "sfx_source": {
                "mode": "stage3_optional",
                "clips": [{"shot": "1A", "file": "clips/audio/sfx/shot_1a_source.wav", "action": "adopted"}],
            },
            "bgm": {"mode": "selected", "file": "clips/audio/bgm/bgm_01.mp3", "reference_db": -18.0},
        },
        "warnings": [],
    }


def touch(project_dir: Path, rel_path: str) -> None:
    path = project_dir / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake")


def make_project_dir(tmp_path: Path, plan: dict) -> Path:
    """Create every file the plan currently references. Tests opt out of a
    file existing by mutating/removing it from `plan` (or deleting it after
    this call), not by hand-picking which files to create."""
    project_dir = tmp_path / "project"
    if plan.get("voice", {}).get("audio_file"):
        touch(project_dir, plan["voice"]["audio_file"])
    for segment in plan.get("segments", []):
        if segment.get("source_clip"):
            touch(project_dir, segment["source_clip"])
    bgm = plan.get("audio_layers", {}).get("bgm", {})
    if bgm.get("mode") == "selected" and bgm.get("file"):
        touch(project_dir, bgm["file"])
    for clip in plan.get("audio_layers", {}).get("sfx_source", {}).get("clips", []):
        if clip.get("action") == "adopted" and clip.get("file"):
            touch(project_dir, clip["file"])
    return project_dir


def test_pass_when_all_files_exist_and_timing_is_consistent(tmp_path):
    plan = base_plan()
    project_dir = make_project_dir(tmp_path, plan)

    result = run_preflight(plan, project_dir)

    assert result.preflight_result == "PASS"
    assert result.issues == []


def test_declared_warnings_are_folded_into_the_issue_list(tmp_path):
    # ch. 12: an SFX candidate Stage 4 hasn't decided on yet can't be
    # represented inside sfx_source.clips[] at all (the action enum
    # excludes "candidate") - the only schema-legal way to flag it is a
    # top-level warnings[] entry. Pre-flight doesn't special-case SFX; it
    # just folds every declared warning into the issue list generically.
    plan = base_plan()
    plan["warnings"] = [
        {
            "code": "SFX_CANDIDATE_UNRESOLVED",
            "message": "shot=2B has an SFX candidate that has not been adopted or discarded",
            "severity": "warning",
            "segment_ref": "shot=2B",
        }
    ]
    project_dir = make_project_dir(tmp_path, plan)

    result = run_preflight(plan, project_dir)

    assert result.preflight_result == "PASS"  # warning, not blocking
    assert any(
        issue.code == "SFX_CANDIDATE_UNRESOLVED"
        and issue.severity == "warning"
        and issue.segment_ref == "shot=2B"
        for issue in result.issues
    )


def test_declared_warnings_can_be_blocking_too(tmp_path):
    # check_declared_warnings() doesn't hardcode a severity for anything -
    # it passes through exactly whatever Stage 4 declared, including
    # "blocking" if that's ever the right call for some future warning code.
    plan = base_plan()
    plan["warnings"] = [
        {"code": "SOME_FUTURE_BLOCKING_CASE", "message": "...", "severity": "blocking", "segment_ref": None}
    ]
    project_dir = make_project_dir(tmp_path, plan)

    result = run_preflight(plan, project_dir)

    assert result.preflight_result == "FAIL"
    assert any(issue.code == "SOME_FUTURE_BLOCKING_CASE" and issue.severity == "blocking" for issue in result.issues)


def test_empty_warnings_list_adds_no_issues(tmp_path):
    plan = base_plan()  # base_plan() already has warnings: []
    project_dir = make_project_dir(tmp_path, plan)

    result = run_preflight(plan, project_dir)

    assert result.preflight_result == "PASS"
    assert result.issues == []


def test_missing_clip_is_blocking(tmp_path):
    plan = base_plan()
    project_dir = make_project_dir(tmp_path, plan)
    (project_dir / plan["segments"][0]["source_clip"]).unlink()

    result = run_preflight(plan, project_dir)

    assert result.preflight_result == "FAIL"
    assert any(issue.code == "MISSING_CLIP" and issue.severity == "blocking" for issue in result.issues)


def test_missing_usable_range_is_warning_not_blocking(tmp_path):
    # No clip_manifest.json yet (Stage 3 hasn't produced usable-range data)
    # must not kill Pre-flight outright - ch. 07 principle.
    plan = base_plan()
    plan["segments"][0]["usable_start_ms"] = None
    plan["segments"][0]["usable_end_ms"] = None
    project_dir = make_project_dir(tmp_path, plan)

    result = run_preflight(plan, project_dir)

    assert result.preflight_result == "PASS"
    assert any(
        issue.code == "USABLE_RANGE_MISSING" and issue.severity == "warning" for issue in result.issues
    )


def test_clip_in_after_clip_out_is_blocking(tmp_path):
    plan = base_plan()
    plan["segments"][0]["clip_in_ms"] = 5000
    plan["segments"][0]["clip_out_ms"] = 1000
    project_dir = make_project_dir(tmp_path, plan)

    result = run_preflight(plan, project_dir)

    assert result.preflight_result == "FAIL"
    assert any(issue.code == "INVALID_CLIP_RANGE" for issue in result.issues)


def test_segment_overlap_is_blocking(tmp_path):
    plan = base_plan()
    # Shift shot 1B to start while 1A (ends at 4800) is still playing.
    plan["segments"][1]["start_ms"] = 2000
    plan["segments"][1]["end_ms"] = 2000 + (5000 - 200)  # keep clip_out-clip_in == duration
    project_dir = make_project_dir(tmp_path, plan)

    result = run_preflight(plan, project_dir)

    assert result.preflight_result == "FAIL"
    assert any(issue.code == "SEGMENT_OVERLAP" for issue in result.issues)


def test_total_duration_mismatch_is_warning(tmp_path):
    plan = base_plan()
    plan["voice"]["total_duration_ms"] = 20000  # segments actually sum to 9600ms
    project_dir = make_project_dir(tmp_path, plan)

    result = run_preflight(plan, project_dir)

    assert result.preflight_result == "PASS"
    assert any(
        issue.code == "TOTAL_DURATION_MISMATCH" and issue.severity == "warning" for issue in result.issues
    )


def test_schema_invalid_plan_short_circuits_before_file_or_time_checks(tmp_path):
    plan = base_plan()
    del plan["status"]  # required field missing -> 04-1 fails
    project_dir = tmp_path / "project"  # deliberately empty - no files created

    result = run_preflight(plan, project_dir)

    assert result.preflight_result == "FAIL"
    assert all(issue.code == "SCHEMA_VIOLATION" for issue in result.issues)
    # If 04-2/04-3 had run against this empty project_dir, MISSING_CLIP and
    # MISSING_VOICE_ASSET would also appear. Their absence proves they didn't run.
    assert not any(issue.code in ("MISSING_CLIP", "MISSING_VOICE_ASSET") for issue in result.issues)


def make_settle_frame_hold_plan(hold_ms: int) -> dict:
    """A single-segment plan whose Duration Invariant holds for *any*
    hold_ms (segment_duration_ms is derived from it), so these tests only
    ever exercise MAX_HOLD_MS_EXCEEDED - never an incidental
    DURATION_INVARIANT_VIOLATION from an unrelated arithmetic mismatch."""
    plan = base_plan()
    segment = {
        "beat": 1,
        "shot": "1A",
        "start_ms": 0,
        "end_ms": 3500 + hold_ms,
        "timing_grade": "EXACT",
        "source_clip": "clips/shot_1a_flow.mp4",
        "source_duration_ms": 6000,
        "usable_start_ms": 0,
        "usable_end_ms": 5800,
        "key_event_end_ms": 3500,
        "settle_start_ms": 3500,
        "clip_in_ms": 0,
        "clip_out_ms": 3500,
        "camera_behavior": "locked-off",
        "hold_strategy": "settle_frame_hold",
        "hold_ms": hold_ms,
        "trim_reason": "settle frame hold test",
        "voice_text": "Test.",
        "status": "OK",
        "status_note": None,
    }
    plan["segments"] = [segment]
    plan["voice"]["total_duration_ms"] = segment["end_ms"]
    return plan


def test_hold_ms_within_default_max_is_allowed(tmp_path):
    plan = make_settle_frame_hold_plan(hold_ms=1500)  # exactly the default max
    project_dir = make_project_dir(tmp_path, plan)

    result = run_preflight(plan, project_dir)

    assert result.preflight_result == "PASS"
    assert not any(issue.code == "MAX_HOLD_MS_EXCEEDED" for issue in result.issues)


def test_hold_ms_exceeding_default_max_is_blocking(tmp_path):
    plan = make_settle_frame_hold_plan(hold_ms=2000)  # exceeds default max (1500)
    project_dir = make_project_dir(tmp_path, plan)

    result = run_preflight(plan, project_dir)

    assert result.preflight_result == "FAIL"
    assert any(
        issue.code == "MAX_HOLD_MS_EXCEEDED" and issue.severity == "blocking" for issue in result.issues
    )


def test_max_hold_ms_is_configurable_per_call(tmp_path):
    plan = make_settle_frame_hold_plan(hold_ms=1500)  # under the default, but not under a stricter override
    project_dir = make_project_dir(tmp_path, plan)

    result = run_preflight(plan, project_dir, max_settle_frame_hold_ms=1000)

    assert result.preflight_result == "FAIL"
    assert any(issue.code == "MAX_HOLD_MS_EXCEEDED" for issue in result.issues)


def test_max_hold_ms_check_ignores_non_settle_frame_hold_segments(tmp_path):
    # hold_strategy="none" always has hold_ms=0 (schema-enforced) - proves the
    # check only ever fires for settle_frame_hold, even with a limit of 0.
    plan = base_plan()
    project_dir = make_project_dir(tmp_path, plan)

    result = run_preflight(plan, project_dir, max_settle_frame_hold_ms=0)

    assert not any(issue.code == "MAX_HOLD_MS_EXCEEDED" for issue in result.issues)


@pytest.mark.parametrize("severity", ["blocking", "warning", "info"])
def test_severity_to_execution_matches_ch18_table(severity):
    permission = severity_to_execution(severity)
    expected = SEVERITY_MAPPING[severity]

    assert permission.effective_status == expected["effective_status"]
    assert permission.preview_allowed == expected["preview"]
    assert permission.final_allowed == expected["final"]


def test_severity_to_execution_with_no_issues_allows_everything():
    permission = severity_to_execution(None)

    assert permission.effective_status is None
    assert permission.preview_allowed is True
    assert permission.final_allowed is True


def test_worst_severity_picks_the_highest_rank():
    from seoulkit_studio.preflight import PreflightIssue

    issues = [
        PreflightIssue("A", "info", "..."),
        PreflightIssue("B", "warning", "..."),
    ]
    assert worst_severity(issues) == "warning"

    issues.append(PreflightIssue("C", "blocking", "..."))
    assert worst_severity(issues) == "blocking"

    assert worst_severity([]) is None
