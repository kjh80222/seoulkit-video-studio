import json
import shutil
import subprocess
from pathlib import Path

import pytest

from seoulkit_studio.cli import exit_codes
from seoulkit_studio.cli.main import build_parser, main
from seoulkit_studio.render import pipeline as render_pipeline

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed on this system",
)


def _colored_clip(path: Path, color: str, duration_s: float) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:size=320x240:rate=10:duration={duration_s}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
        ],
        capture_output=True, text=True, check=True,
    )


def _tone(path: Path, duration_s: float) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=800:duration={duration_s}",
         "-af", "volume=-15dB", "-ar", "44100", "-ac", "2", str(path)],
        capture_output=True, text=True, check=True,
    )


def base_plan(*, status: str, warnings: list[dict]) -> dict:
    return {
        "schema_version": "1.0",
        "project": "cli-test",
        "format": "MINI",
        "status": status,
        "timing_grade_default": "EXACT",
        "voice": {
            "audio_file": "clips/audio/voice_final.wav",
            "aligned": True,
            "alignment_file": None,
            "total_duration_ms": 4000,
        },
        "segments": [
            {
                "beat": 1, "shot": "1A", "start_ms": 0, "end_ms": 2000,
                "timing_grade": "EXACT", "source_clip": "clips/shot_1a.mp4",
                "source_duration_ms": 3000, "usable_start_ms": 0, "usable_end_ms": 3000,
                "key_event_end_ms": 2000, "settle_start_ms": 2000,
                "clip_in_ms": 0, "clip_out_ms": 2000,
                "camera_behavior": "locked-off", "hold_strategy": "none", "hold_ms": 0,
                "trim_reason": None, "voice_text": "one", "status": "OK", "status_note": None,
            },
            {
                "beat": 1, "shot": "1B", "start_ms": 2000, "end_ms": 4000,
                "timing_grade": "EXACT", "source_clip": "clips/shot_1b.mp4",
                "source_duration_ms": 3000, "usable_start_ms": 0, "usable_end_ms": 3000,
                "key_event_end_ms": 2000, "settle_start_ms": 2000,
                "clip_in_ms": 0, "clip_out_ms": 2000,
                "camera_behavior": "locked-off", "hold_strategy": "none", "hold_ms": 0,
                "trim_reason": None, "voice_text": "two", "status": "OK", "status_note": None,
            },
        ],
        "overlays": [],
        "subtitles": [],
        "audio_layers": {
            "voice": "authoritative",
            "sfx_source": {"mode": "none", "clips": []},
            "bgm": {"mode": "none", "file": None, "reference_db": None},
        },
        "warnings": warnings,
    }


@pytest.fixture
def demo_project(tmp_path):
    project_dir = tmp_path / "project"
    (project_dir / "clips" / "audio").mkdir(parents=True)

    _colored_clip(project_dir / "clips" / "shot_1a.mp4", "navy", 3)
    _colored_clip(project_dir / "clips" / "shot_1b.mp4", "darkgreen", 3)
    _tone(project_dir / "clips" / "audio" / "voice_final.wav", 4)

    return project_dir


def write_plan(project_dir: Path, plan: dict) -> Path:
    path = project_dir / "edit_plan.json"
    path.write_text(json.dumps(plan))
    return path


def write_clip_manifest(project_dir: Path) -> Path:
    manifest = {
        "note": "test fixture",
        "clips": [
            {"shot": "1A", "file": "clips/shot_1a.mp4", "camera_behavior": "locked-off",
             "source_duration_ms": 3000, "usable_start_ms": 0, "usable_end_ms": 3000,
             "key_event_end_ms": 2000, "settle_start_ms": 2000},
            {"shot": "1B", "file": "clips/shot_1b.mp4", "camera_behavior": "locked-off",
             "source_duration_ms": 3000, "usable_start_ms": 0, "usable_end_ms": 3000,
             "key_event_end_ms": 2000, "settle_start_ms": 2000},
        ],
    }
    path = project_dir / "clips" / "clip_manifest.json"
    path.write_text(json.dumps(manifest))
    return path


# --- argparse wiring: primary names + aliases share one implementation -----


def test_all_primary_commands_and_aliases_are_registered():
    parser = build_parser()
    args = parser.parse_args(["preflight", "."])
    assert args.func.__name__ == "cmd_preflight"
    args = parser.parse_args(["validate", "."])
    assert args.func.__name__ == "cmd_preflight"
    args = parser.parse_args(["render", "."])
    assert args.func.__name__ == "cmd_render"
    args = parser.parse_args(["final", "."])
    assert args.func.__name__ == "cmd_render"
    args = parser.parse_args(["preview", "."])
    assert args.func.__name__ == "cmd_preview"
    args = parser.parse_args(["status", "."])
    assert args.func.__name__ == "cmd_status"
    args = parser.parse_args(["report", "."])
    assert args.func.__name__ == "cmd_report"


# --- preflight exit codes ---------------------------------------------------


def test_preflight_exits_zero_on_ready(demo_project, capsys):
    write_plan(demo_project, base_plan(status="READY", warnings=[]))
    write_clip_manifest(demo_project)

    code = main(["preflight", str(demo_project)])

    assert code == exit_codes.OK
    assert "READY" in capsys.readouterr().out


def test_preflight_exits_one_on_review_required(demo_project, capsys):
    # No clip_manifest.json written - the always-pass-through policy must
    # surface CLIP_MANIFEST_MISSING (a warning) on its own, with zero new
    # CLI-side validation logic.
    write_plan(demo_project, base_plan(status="READY", warnings=[]))

    code = main(["preflight", str(demo_project)])

    out = capsys.readouterr().out
    assert code == exit_codes.REVIEW_REQUIRED
    assert "CLIP_MANIFEST_MISSING" in out


def test_preflight_exits_two_on_not_ready(demo_project, capsys):
    write_plan(demo_project, base_plan(status="READY", warnings=[
        {"code": "SOME_BLOCKING_THING", "message": "...", "severity": "blocking", "segment_ref": None}
    ]))
    write_clip_manifest(demo_project)

    code = main(["preflight", str(demo_project)])

    assert code == exit_codes.NOT_READY
    assert "NOT_READY" in capsys.readouterr().out


def test_preflight_json_is_a_single_parseable_object(demo_project, capsys):
    write_plan(demo_project, base_plan(status="READY", warnings=[]))
    write_clip_manifest(demo_project)

    code = main(["preflight", str(demo_project), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == exit_codes.OK
    assert payload["effective_status"] == "READY"


def test_clip_manifest_override_clears_the_missing_manifest_warning(demo_project, tmp_path, capsys):
    write_plan(demo_project, base_plan(status="READY", warnings=[]))
    real_manifest = write_clip_manifest(demo_project)
    override = tmp_path / "elsewhere.json"
    override.write_text(real_manifest.read_text())

    code = main(["preflight", str(demo_project), "--clip-manifest", str(override)])

    assert code == exit_codes.OK
    assert "CLIP_MANIFEST_MISSING" not in capsys.readouterr().out


# --- preview exit codes ------------------------------------------------------


@requires_ffmpeg
def test_preview_exits_zero_and_surfaces_review_required_when_gate_allows_it(demo_project, capsys):
    write_plan(demo_project, base_plan(status="READY", warnings=[]))
    # No clip_manifest.json -> REVIEW_REQUIRED, which Preview is allowed to run under.

    code = main(["preview", str(demo_project)])

    out = capsys.readouterr().out
    assert code == exit_codes.OK
    assert "REVIEW_REQUIRED" in out
    assert "OK: True" in out
    assert (demo_project / "preview" / "preview_v001.mp4").exists()


@requires_ffmpeg
def test_preview_exits_two_when_not_ready(demo_project, capsys):
    write_plan(demo_project, base_plan(status="READY", warnings=[
        {"code": "SOME_BLOCKING_THING", "message": "...", "severity": "blocking", "segment_ref": None}
    ]))
    write_clip_manifest(demo_project)

    code = main(["preview", str(demo_project)])

    assert code == exit_codes.NOT_READY


@requires_ffmpeg
def test_preview_exits_three_on_a_real_execution_failure(demo_project, capsys):
    write_plan(demo_project, base_plan(status="READY", warnings=[]))
    write_clip_manifest(demo_project)
    # File exists (passes preflight) but isn't a real video - the gate
    # passes and trim_clip()'s real FFmpeg call then fails.
    (demo_project / "clips" / "shot_1a.mp4").write_bytes(b"not actually a video file")

    code = main(["preview", str(demo_project)])

    assert code == exit_codes.EXECUTION_FAILED
    assert "OK: False" in capsys.readouterr().out


# --- render (Final) exit codes -----------------------------------------------


@requires_ffmpeg
def test_render_exits_zero_on_ready(demo_project, capsys):
    write_plan(demo_project, base_plan(status="READY", warnings=[]))
    write_clip_manifest(demo_project)

    code = main(["render", str(demo_project)])

    out = capsys.readouterr().out
    assert code == exit_codes.OK
    assert (demo_project / "output" / "final_v001.mp4").exists()
    assert "Report:" in out
    assert "Log:" in out


@requires_ffmpeg
def test_render_exits_one_on_review_required(demo_project, capsys):
    write_plan(demo_project, base_plan(status="READY", warnings=[
        {"code": "SOME_WARNING", "message": "...", "severity": "warning", "segment_ref": None}
    ]))
    write_clip_manifest(demo_project)

    code = main(["render", str(demo_project)])

    out = capsys.readouterr().out
    assert code == exit_codes.REVIEW_REQUIRED
    assert "Gate rejected: review_required" in out


@requires_ffmpeg
def test_final_alias_behaves_identically_to_render(demo_project, capsys):
    write_plan(demo_project, base_plan(status="READY", warnings=[
        {"code": "SOME_WARNING", "message": "...", "severity": "warning", "segment_ref": None}
    ]))
    write_clip_manifest(demo_project)

    code = main(["final", str(demo_project), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == exit_codes.REVIEW_REQUIRED
    assert payload["gate_error"] == "review_required"


@requires_ffmpeg
def test_render_exits_two_on_not_ready(demo_project, capsys):
    write_plan(demo_project, base_plan(status="READY", warnings=[
        {"code": "SOME_BLOCKING_THING", "message": "...", "severity": "blocking", "segment_ref": None}
    ]))
    write_clip_manifest(demo_project)

    code = main(["render", str(demo_project)])

    assert code == exit_codes.NOT_READY


@requires_ffmpeg
def test_render_exits_three_on_a_real_execution_failure(demo_project, capsys):
    write_plan(demo_project, base_plan(status="READY", warnings=[]))
    write_clip_manifest(demo_project)
    (demo_project / "clips" / "shot_1a.mp4").write_bytes(b"not actually a video file")

    code = main(["render", str(demo_project)])

    assert code == exit_codes.EXECUTION_FAILED


# --- status / report E2E after a real render ---------------------------------


@requires_ffmpeg
def test_status_and_report_reflect_a_real_completed_render(demo_project, capsys):
    write_plan(demo_project, base_plan(status="READY", warnings=[]))
    write_clip_manifest(demo_project)
    main(["render", str(demo_project)])
    capsys.readouterr()

    status_code = main(["status", str(demo_project), "--json"])
    status_payload = json.loads(capsys.readouterr().out)

    report_code = main(["report", str(demo_project), "--version", "1", "--json"])
    report_payload = json.loads(capsys.readouterr().out)

    assert status_code == exit_codes.OK
    assert status_payload["render_version"] == "v001"
    assert status_payload["output_file"] == "output/final_v001.mp4"

    assert report_code == exit_codes.OK
    assert report_payload["render_version"] == "v001"
    assert report_payload["render_type"] == "final"


@requires_ffmpeg
def test_report_unknown_version_exits_four(demo_project, capsys):
    write_plan(demo_project, base_plan(status="READY", warnings=[]))
    write_clip_manifest(demo_project)
    main(["render", str(demo_project)])
    capsys.readouterr()

    code = main(["report", str(demo_project), "--version", "999"])

    assert code == exit_codes.USAGE_ERROR


def test_status_on_a_project_that_was_never_rendered(demo_project, capsys):
    write_plan(demo_project, base_plan(status="READY", warnings=[]))

    code = main(["status", str(demo_project)])

    assert code == exit_codes.OK
    assert "No renders found" in capsys.readouterr().out


# --- status/report never re-evaluate or render (read-only contract) --------


def _forbid_evaluate_plan(*a, **k):
    raise AssertionError("status/report must never call evaluate_plan()")


def test_status_never_calls_evaluate_plan(demo_project, monkeypatch):
    write_plan(demo_project, base_plan(status="READY", warnings=[]))
    monkeypatch.setattr(render_pipeline, "evaluate_plan", _forbid_evaluate_plan)

    code = main(["status", str(demo_project)])

    assert code == exit_codes.OK  # would have raised above if evaluate_plan() were called


def test_report_never_calls_evaluate_plan(demo_project, monkeypatch):
    write_plan(demo_project, base_plan(status="READY", warnings=[]))
    monkeypatch.setattr(render_pipeline, "evaluate_plan", _forbid_evaluate_plan)

    code = main(["report", str(demo_project)])  # no renders yet - must not evaluate anything to say so

    assert code == exit_codes.OK


# --- no double evaluation inside preview/render ------------------------------


@requires_ffmpeg
def test_preview_calls_evaluate_plan_exactly_once(demo_project, monkeypatch):
    write_plan(demo_project, base_plan(status="READY", warnings=[]))
    write_clip_manifest(demo_project)

    real = render_pipeline.evaluate_plan
    calls = []

    def _spy(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(render_pipeline, "evaluate_plan", _spy)

    main(["preview", str(demo_project)])

    assert len(calls) == 1


@requires_ffmpeg
def test_render_calls_evaluate_plan_exactly_once(demo_project, monkeypatch):
    write_plan(demo_project, base_plan(status="READY", warnings=[]))
    write_clip_manifest(demo_project)

    real = render_pipeline.evaluate_plan
    calls = []

    def _spy(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(render_pipeline, "evaluate_plan", _spy)

    main(["render", str(demo_project)])

    assert len(calls) == 1


# --- usage errors --------------------------------------------------------------


def test_nonexistent_project_dir_exits_four(tmp_path, capsys):
    missing = tmp_path / "does-not-exist"

    code = main(["preflight", str(missing)])

    out = capsys.readouterr().out
    assert code == exit_codes.USAGE_ERROR
    assert "not found" in out


def test_nonexistent_project_dir_json_is_still_parseable(tmp_path, capsys):
    missing = tmp_path / "does-not-exist"

    code = main(["render", str(missing), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == exit_codes.USAGE_ERROR
    assert payload["exit_code"] == 4


@requires_ffmpeg
def test_missing_ffprobe_is_caught_by_the_top_level_handler_not_a_traceback(demo_project, monkeypatch, capsys):
    # ffmpeg call sites all guard via shutil.which() and degrade to a
    # graceful stage failure (exit 3) - ffprobe call sites don't have that
    # guard (a known, documented gap). This proves main()'s catch-all is
    # what actually stands between that gap and a raw traceback reaching
    # the user, mapping it to exit 4 with a JSON-safe message instead.
    write_plan(demo_project, base_plan(status="READY", warnings=[]))
    write_clip_manifest(demo_project)

    def _raise_ffprobe_not_found(*a, **k):
        raise FileNotFoundError("[Errno 2] No such file or directory: 'ffprobe'")

    monkeypatch.setattr("seoulkit_studio.render.report.subprocess.run", _raise_ffprobe_not_found)

    code = main(["preview", str(demo_project), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == exit_codes.USAGE_ERROR
    assert "ffprobe" in payload["error"] or "unexpected error" in payload["error"]
