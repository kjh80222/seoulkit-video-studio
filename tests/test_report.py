import hashlib
import json
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path

import pytest

from seoulkit_studio.render.report import next_render_version, render_final_and_report, render_preview_and_report

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
        "project": "report-test",
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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- next_render_version() ---------------------------------------------------


def test_next_render_version_starts_at_one_for_an_empty_project(tmp_path):
    assert next_render_version(tmp_path) == 1


def test_next_render_version_scans_all_three_directories(tmp_path):
    # The max is deliberately placed in preview/ (not output/) so a bug that
    # only scans one directory (e.g. output/ alone) is actually caught here,
    # not coincidentally masked by which directory happens to hold the max.
    (tmp_path / "preview").mkdir()
    (tmp_path / "preview" / "preview_v009.mp4").write_bytes(b"")
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "final_v002.mp4").write_bytes(b"")
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "render_v003.log").write_bytes(b"")

    assert next_render_version(tmp_path) == 10


def test_next_render_version_ignores_unrelated_files(tmp_path):
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "README.md").write_text("not a version file")

    assert next_render_version(tmp_path) == 1


# --- successful Preview report -----------------------------------------------


@requires_ffmpeg
def test_preview_success_report_lands_in_preview_dir(demo_project, probe_duration_ms):
    plan_path = write_plan(demo_project, base_plan(status="READY", warnings=[]))

    result, report = render_preview_and_report(plan_path, demo_project)

    assert result.ok, [getattr(sr, "stderr", "") for sr in result.stage_results if not sr.ok]
    output = demo_project / "preview" / "preview_v001.mp4"
    report_path = demo_project / "preview" / "preview_v001.render_report.json"
    assert output.exists()
    assert report_path.exists()

    assert report.render_type == "preview"
    assert report.render_version == "v001"
    assert report.output_file == "preview/preview_v001.mp4"
    assert report.studio_execution_result == "PASS"
    assert report.effective_status == "READY"
    assert report.plan_status_as_declared == "READY"
    assert report.errors == []
    assert len(report.qc_results) == 2
    assert {qc.check for qc in report.qc_results} == {"duration_match", "duration_invariant_post_render"}
    assert all(qc.result == "PASS" for qc in report.qc_results)
    assert len(report.ffmpeg_commands) > 0

    on_disk = json.loads(report_path.read_text())
    assert on_disk["render_version"] == "v001"
    assert on_disk["output_file"] == "preview/preview_v001.mp4"


@requires_ffmpeg
def test_result_report_path_and_log_path_match_the_real_files_on_success(demo_project, probe_duration_ms):
    plan_path = write_plan(demo_project, base_plan(status="READY", warnings=[]))

    result, report = render_preview_and_report(plan_path, demo_project)

    assert result.report_path == demo_project / "preview" / "preview_v001.render_report.json"
    assert result.log_path == demo_project / "logs" / "render_v001.log"
    assert result.report_path.exists()
    assert result.log_path.exists()


@requires_ffmpeg
def test_persisted_render_report_json_never_gains_report_path_or_log_path(demo_project):
    # Phase 11 exposes report_path/log_path additively on RenderResult only
    # (see render/pipeline.py's module docstring) - the persisted ch. 17
    # Render Report JSON, built from RenderReport via asdict(), must stay
    # exactly the schema it was before Phase 11. This is the automated
    # version of a check that was previously only done by hand.
    plan_path = write_plan(demo_project, base_plan(status="READY", warnings=[]))

    result, report = render_preview_and_report(plan_path, demo_project)

    assert "report_path" not in asdict(report)
    assert "log_path" not in asdict(report)
    on_disk = json.loads(result.report_path.read_text())
    assert "report_path" not in on_disk
    assert "log_path" not in on_disk


# --- successful Final report --------------------------------------------------


@requires_ffmpeg
def test_final_success_report_lands_in_output_dir(demo_project):
    plan_path = write_plan(demo_project, base_plan(status="READY", warnings=[]))

    result, report = render_final_and_report(plan_path, demo_project)

    assert result.ok, [getattr(sr, "stderr", "") for sr in result.stage_results if not sr.ok]
    assert (demo_project / "output" / "final_v001.mp4").exists()
    assert (demo_project / "output" / "final_v001.ass").exists()
    assert (demo_project / "output" / "final_v001.srt").exists()
    assert (demo_project / "output" / "final_v001.render_report.json").exists()

    assert report.render_type == "final"
    assert report.output_file == "output/final_v001.mp4"
    assert report.studio_execution_result == "PASS"
    assert report.effective_status == "READY"
    assert all(qc.result == "PASS" for qc in report.qc_results)


# --- gate rejection: PASS but REVIEW_REQUIRED, no output produced -----------


@requires_ffmpeg
def test_final_gate_rejection_report_lands_in_logs(demo_project):
    plan_path = write_plan(demo_project, base_plan(status="READY", warnings=[
        {"code": "SOME_WARNING", "message": "...", "severity": "warning", "segment_ref": None}
    ]))

    result, report = render_final_and_report(plan_path, demo_project)

    assert result.gated
    assert not (demo_project / "output").exists() or list((demo_project / "output").glob("final_v001.*")) == []
    report_path = demo_project / "logs" / "render_v001.render_report.json"
    assert report_path.exists()

    assert report.output_file is None
    assert report.studio_execution_result == "PASS"  # only a warning, nothing blocking
    assert report.effective_status == "REVIEW_REQUIRED"
    assert report.errors == []  # gate rejection is not an execution failure
    assert any(issue.code == "SOME_WARNING" for issue in report.preflight_issues)
    assert report.qc_results == []

    assert result.report_path == report_path
    assert result.log_path == demo_project / "logs" / "render_v001.log"


# --- real stage failure: gate lets it through, FFmpeg then fails -----------


@requires_ffmpeg
def test_real_stage_failure_report_lands_in_logs_with_an_error(demo_project):
    plan_path = write_plan(demo_project, base_plan(status="READY", warnings=[]))
    # File exists (passes the MISSING_CLIP pre-flight check) but is not a
    # real video - trim_clip()'s actual FFmpeg call fails.
    (demo_project / "clips" / "shot_1a.mp4").write_bytes(b"not actually a video file")

    result, report = render_final_and_report(plan_path, demo_project)

    assert not result.ok
    assert not result.gated  # the gate allowed the attempt; the pipeline itself failed
    report_path = demo_project / "logs" / "render_v001.render_report.json"
    assert report_path.exists()

    assert report.output_file is None
    assert len(report.errors) == 1
    assert report.errors[0].code == "ffmpeg_failed"
    assert report.errors[0].stage == "trim_clip"
    assert report.errors[0].stderr


# --- shared version sequence across type and outcome ------------------------


@requires_ffmpeg
def test_version_sequence_is_shared_across_type_and_outcome(demo_project):
    ready_plan = write_plan(demo_project, base_plan(status="READY", warnings=[]))
    _, report1 = render_preview_and_report(ready_plan, demo_project)
    assert report1.render_version == "v001"

    review_plan = write_plan(demo_project, base_plan(status="READY", warnings=[
        {"code": "SOME_WARNING", "message": "...", "severity": "warning", "segment_ref": None}
    ]))
    _, report2 = render_final_and_report(review_plan, demo_project)  # gate-rejected
    assert report2.render_version == "v002"

    _, report3 = render_preview_and_report(ready_plan, demo_project)
    assert report3.render_version == "v003"

    _, report4 = render_final_and_report(ready_plan, demo_project)
    assert report4.render_version == "v004"


# --- input_edit_plan_hash ----------------------------------------------------


@requires_ffmpeg
def test_input_edit_plan_hash_matches_real_file_hash(demo_project):
    plan_path = write_plan(demo_project, base_plan(status="READY", warnings=[]))

    _, report = render_preview_and_report(plan_path, demo_project)

    assert report.input_edit_plan_hash == f"sha256:{_sha256(plan_path)}"


# --- render_v{NNN}.log --------------------------------------------------------


@requires_ffmpeg
def test_log_file_is_written_on_success_and_matches_ffmpeg_commands(demo_project):
    plan_path = write_plan(demo_project, base_plan(status="READY", warnings=[]))

    _, report = render_preview_and_report(plan_path, demo_project)

    log_path = demo_project / "logs" / "render_v001.log"
    assert log_path.exists()
    log_lines = [line for line in log_path.read_text().splitlines() if line]
    assert len(log_lines) == len(report.ffmpeg_commands)


@requires_ffmpeg
def test_log_file_is_written_even_on_gate_rejection(demo_project):
    plan_path = write_plan(demo_project, base_plan(status="READY", warnings=[
        {"code": "SOME_WARNING", "message": "...", "severity": "warning", "segment_ref": None}
    ]))

    render_final_and_report(plan_path, demo_project)

    log_path = demo_project / "logs" / "render_v001.log"
    assert log_path.exists()
    assert log_path.read_text() == ""  # gated before any FFmpeg call


# --- QC checks genuinely measure the real file (can FAIL, not always PASS) --


@requires_ffmpeg
def test_duration_match_detects_a_real_mismatch(tmp_path):
    from seoulkit_studio.render.report import _duration_match

    video = tmp_path / "v.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=navy:size=64x64:rate=10:duration=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)],
        capture_output=True, text=True, check=True,
    )

    qc = _duration_match(video, expected_ms=5000, tolerance_ms=50)

    assert qc.result == "FAIL"
    assert "diff=" in qc.detail


@requires_ffmpeg
def test_duration_invariant_post_render_detects_a_real_mismatch(tmp_path):
    from seoulkit_studio.render.report import _duration_invariant_post_render

    video = tmp_path / "v.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=navy:size=64x64:rate=10:duration=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)],
        capture_output=True, text=True, check=True,
    )
    data = {"segments": [{"clip_in_ms": 0, "clip_out_ms": 5000, "hold_strategy": "none", "hold_ms": 0}]}

    qc = _duration_invariant_post_render(data, video, tolerance_ms=50)

    assert qc.result == "FAIL"
    assert "aggregate-level" in qc.detail


# --- immutability -------------------------------------------------------------


@requires_ffmpeg
def test_report_generation_never_modifies_edit_plan(demo_project):
    plan_path = write_plan(demo_project, base_plan(status="READY", warnings=[]))
    before = _sha256(plan_path)

    render_preview_and_report(plan_path, demo_project)

    assert _sha256(plan_path) == before
