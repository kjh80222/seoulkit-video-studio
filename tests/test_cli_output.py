import json
from pathlib import Path

from seoulkit_studio.cli.output import (
    format_no_renders,
    format_render,
    format_report,
    format_report_not_found,
    format_status,
    format_usage_error,
    format_validate,
)
from seoulkit_studio.cli.report_lookup import DiscoveredReport
from seoulkit_studio.execution import EvaluationResult
from seoulkit_studio.preflight import PreflightIssue, PreflightResult
from seoulkit_studio.render.pipeline import RenderResult
from seoulkit_studio.render.report import RenderReport


def _evaluation(**overrides) -> EvaluationResult:
    defaults = dict(
        load_error=None,
        preflight_result=PreflightResult(
            preflight_result="PASS",
            issues=[PreflightIssue(code="SOME_WARNING", severity="warning", detail="...", segment_ref="1A")],
        ),
        clip_manifest_issues=[],
        studio_execution_result="PASS",
        effective_status="REVIEW_REQUIRED",
        plan_status_as_declared="READY",
    )
    defaults.update(overrides)
    return EvaluationResult(**defaults)


def _render_report(**overrides) -> RenderReport:
    defaults = dict(
        render_type="preview",
        render_version="v001",
        studio_execution_result="PASS",
        effective_status="READY",
        plan_status_as_declared="READY",
        started_at="2026-01-01T00:00:00+00:00",
        completed_at="2026-01-01T00:00:01+00:00",
        duration_ms=1000,
        input_edit_plan_hash="sha256:abc",
        output_file="preview/preview_v001.mp4",
    )
    defaults.update(overrides)
    return RenderReport(**defaults)


def _render_result(**overrides) -> RenderResult:
    defaults = dict(
        ok=True,
        output_path=Path("preview/preview_v001.mp4"),
        gate_error=None,
        studio_execution_result="PASS",
        effective_status="READY",
        plan_status_as_declared="READY",
        preflight_issues=[],
        report_path=Path("preview/preview_v001.render_report.json"),
        log_path=Path("logs/render_v001.log"),
    )
    defaults.update(overrides)
    return RenderResult(**defaults)


# --- format_validate -----------------------------------------------------


def test_format_validate_json_is_valid_json_and_carries_effective_status():
    result = _evaluation()

    payload = json.loads(format_validate(result, json_output=True))

    assert payload["effective_status"] == "REVIEW_REQUIRED"
    assert payload["issues"][0]["code"] == "SOME_WARNING"


def test_format_validate_human_mentions_status_and_issue():
    result = _evaluation()

    text = format_validate(result, json_output=False)

    assert "REVIEW_REQUIRED" in text
    assert "SOME_WARNING" in text


def test_format_validate_human_says_none_when_no_issues():
    result = _evaluation(
        preflight_result=PreflightResult(preflight_result="PASS", issues=[]),
        effective_status="READY",
    )

    text = format_validate(result, json_output=False)

    assert "Issues: none" in text


# --- format_render ---------------------------------------------------------


def test_format_render_json_exposes_report_path_and_log_path():
    result = _render_result()
    report = _render_report()

    payload = json.loads(format_render(result, report, json_output=True))

    assert payload["report_path"] == "preview/preview_v001.render_report.json"
    assert payload["log_path"] == "logs/render_v001.log"
    assert payload["render_version"] == "v001"
    assert payload["command"] == "preview"


def test_format_render_human_shows_review_required_even_when_ok():
    # Preview succeeding under REVIEW_REQUIRED must still surface that
    # status clearly (Phase 11 exit-code agreement).
    result = _render_result(effective_status="REVIEW_REQUIRED")
    report = _render_report(effective_status="REVIEW_REQUIRED")

    text = format_render(result, report, json_output=False)

    assert "REVIEW_REQUIRED" in text
    assert "OK: True" in text


def test_format_render_human_shows_gate_rejection():
    result = _render_result(
        ok=False, output_path=None, gate_error="review_required",
        effective_status="REVIEW_REQUIRED", report_path=Path("logs/render_v002.render_report.json"),
    )
    report = _render_report(render_type="final", output_file=None, effective_status="REVIEW_REQUIRED")

    text = format_render(result, report, json_output=False)

    assert "Gate rejected: review_required" in text
    assert "OK: False" in text


# --- status / report --------------------------------------------------------


def test_format_no_renders_json_round_trips(tmp_path):
    payload = json.loads(format_no_renders(tmp_path, json_output=True))
    assert payload["has_renders"] is False


def test_format_status_json_matches_discovered_report_data(tmp_path):
    report_path = tmp_path / "output" / "final_v004.render_report.json"
    discovered = DiscoveredReport(
        version=4,
        report_path=report_path,
        data={
            "render_version": "v004", "render_type": "final", "effective_status": "READY",
            "studio_execution_result": "PASS", "output_file": "output/final_v004.mp4",
        },
    )

    payload = json.loads(format_status(discovered, log_path=tmp_path / "logs" / "render_v004.log", json_output=True))

    assert payload["render_version"] == "v004"
    assert payload["output_file"] == "output/final_v004.mp4"
    assert payload["report_path"] == str(report_path)


def test_format_report_not_found_json_round_trips(tmp_path):
    payload = json.loads(format_report_not_found(tmp_path, 999, json_output=True))
    assert payload["found"] is False
    assert payload["version"] == "v999"


def test_format_report_json_returns_the_persisted_data_verbatim(tmp_path):
    data = {
        "render_version": "v001", "render_type": "preview", "effective_status": "READY",
        "studio_execution_result": "PASS", "output_file": "preview/preview_v001.mp4",
        "started_at": "t0", "completed_at": "t1", "duration_ms": 1000,
        "qc_results": [{"check": "duration_match", "result": "PASS", "detail": "..."}],
        "errors": [],
    }
    discovered = DiscoveredReport(version=1, report_path=tmp_path / "r.json", data=data)

    payload = json.loads(format_report(discovered, json_output=True))

    assert payload == data


def test_format_report_human_lists_qc_and_errors():
    data = {
        "render_version": "v001", "render_type": "final", "effective_status": "NOT_READY",
        "studio_execution_result": "ERROR", "output_file": None,
        "started_at": "t0", "completed_at": "t1", "duration_ms": 500,
        "qc_results": [],
        "errors": [{"code": "ffmpeg_failed", "stage": "trim_clip", "message": "FFmpeg exited with non-zero status"}],
    }
    discovered = DiscoveredReport(version=1, report_path=Path("logs/render_v001.render_report.json"), data=data)

    text = format_report(discovered, json_output=False)

    assert "ffmpeg_failed" in text
    assert "trim_clip" in text


# --- usage error -------------------------------------------------------------


def test_format_usage_error_json_carries_exit_code_four():
    payload = json.loads(format_usage_error("project directory not found: /nope", json_output=True))
    assert payload["exit_code"] == 4
    assert "project directory not found" in payload["error"]
