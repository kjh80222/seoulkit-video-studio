import json
from pathlib import Path

import pytest

from content_engine.video_studio.adapter import AdapterFailureCategory, run_preflight, run_preview, run_render
from content_engine.video_studio.pipeline import preflight_project, render_project

# An empty project directory (no edit_plan.json at all) is enough for most
# tests here: `preflight` still returns ok=True with effective_status
# NOT_READY (no valid payload -> no domain outcome to distinguish), and
# `preview`/`render` still dispatch to the right CLI command - no real
# ffmpeg-encoded media is needed to prove CE-6's own wiring/pass-through
# behavior.
#
# A genuine GATE_REJECTED result (rather than USAGE_ERROR) requires
# `edit_plan.json` to actually exist on disk - `render/report.py`'s
# `_run_and_report()` unconditionally reads `edit_plan_path.read_bytes()`
# for its input hash even on the gate-rejected path, so a *missing* file
# raises a raw `FileNotFoundError` the CLI's top-level catch-all turns into
# `USAGE_ERROR` instead (confirmed empirically, not assumed) - a real,
# frozen Video Studio behavior, not something CE-6 works around. A minimal
# schema-valid plan whose referenced clip doesn't exist gets a clean
# `MISSING_CLIP` -> `NOT_READY` -> `GATE_REJECTED` instead.

_MINIMAL_PLAN = {
    "schema_version": "1.0", "project": "ce6-test", "format": "MINI",
    "status": "READY", "timing_grade_default": "EXACT",
    "voice": {"audio_file": None, "aligned": False, "total_duration_ms": 2000},
    "segments": [
        {
            "beat": 1, "shot": "1A", "start_ms": 0, "end_ms": 2000, "timing_grade": "EXACT",
            "source_clip": "clips/shot_1a.mp4", "source_duration_ms": 2000,
            "usable_start_ms": 0, "usable_end_ms": 2000, "key_event_end_ms": 1000, "settle_start_ms": 1500,
            "clip_in_ms": 0, "clip_out_ms": 2000, "camera_behavior": "locked-off",
            "hold_strategy": "none", "hold_ms": 0, "trim_reason": None,
            "voice_text": "text", "status": "OK", "status_note": None,
        },
    ],
    "overlays": [], "subtitles": [],
    "audio_layers": {"voice": "authoritative", "sfx_source": {"mode": "none", "clips": []}, "bgm": {"mode": "none", "file": None, "reference_db": None}},
    "warnings": [],
}


@pytest.fixture
def gate_rejected_project(tmp_path):
    (tmp_path / "edit_plan.json").write_text(json.dumps(_MINIMAL_PLAN))
    return tmp_path


def test_preflight_project_returns_adapter_result_for_preflight_command(tmp_path):
    result = preflight_project(tmp_path)

    assert result.command == "preflight"
    assert result.ok is True


def test_render_project_preview_uses_preview_command(tmp_path):
    result = render_project(tmp_path, "preview")

    assert result.command == "preview"


def test_render_project_final_uses_render_command(tmp_path):
    result = render_project(tmp_path, "final")

    assert result.command == "render"


def test_render_project_raises_valueerror_on_invalid_mode(tmp_path):
    with pytest.raises(ValueError):
        render_project(tmp_path, "oops")


def test_render_project_invalid_mode_never_invokes_subprocess(tmp_path, monkeypatch):
    def _fail(*args, **kwargs):
        raise AssertionError("should not be called for an invalid mode")

    monkeypatch.setattr("content_engine.video_studio.pipeline.run_preview", _fail)
    monkeypatch.setattr("content_engine.video_studio.pipeline.run_render", _fail)

    with pytest.raises(ValueError):
        render_project(tmp_path, "oops")


def test_preflight_project_is_unmodified_pass_through(tmp_path):
    direct = run_preflight(tmp_path)
    via_pipeline = preflight_project(tmp_path)

    assert via_pipeline == direct


def test_render_project_is_unmodified_pass_through(tmp_path):
    direct = run_preview(tmp_path)
    via_pipeline = render_project(tmp_path, "preview")

    assert via_pipeline == direct


def test_render_project_preserves_category_and_payload_on_gate_rejection(gate_rejected_project):
    result = render_project(gate_rejected_project, "final")

    assert result.ok is False
    assert result.category == AdapterFailureCategory.GATE_REJECTED
    assert result.payload is not None
