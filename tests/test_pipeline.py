import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from seoulkit_studio.render.pipeline import render_final, render_preview

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


def base_plan(*, status: str, warnings: list[dict], hold_ms: int = 0) -> dict:
    hold_strategy = "settle_frame_hold" if hold_ms else "none"
    return {
        "schema_version": "1.0",
        "project": "pipeline-test",
        "format": "MINI",
        "status": status,
        "timing_grade_default": "EXACT",
        "voice": {
            "audio_file": "clips/audio/voice_final.wav",
            "aligned": True,
            "alignment_file": None,
            "total_duration_ms": 4000 + hold_ms,
        },
        "segments": [
            {
                "beat": 1, "shot": "1A", "start_ms": 0, "end_ms": 2000 + hold_ms,
                "timing_grade": "EXACT", "source_clip": "clips/shot_1a.mp4",
                "source_duration_ms": 3000, "usable_start_ms": 0, "usable_end_ms": 3000,
                "key_event_end_ms": 2000, "settle_start_ms": 2000,
                "clip_in_ms": 0, "clip_out_ms": 2000,
                "camera_behavior": "locked-off", "hold_strategy": hold_strategy, "hold_ms": hold_ms,
                "trim_reason": None, "voice_text": "one", "status": "OK", "status_note": None,
            },
            {
                "beat": 1, "shot": "1B", "start_ms": 2000 + hold_ms, "end_ms": 4000 + hold_ms,
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
    """A minimal but real two-shot project: distinguishable colored clips
    (so concat order/boundaries are provable, same pattern as Phase 4's own
    tests), a real voice track, no overlays/subtitles/SFX/BGM - this test
    file is about pipeline *plumbing* (ordering, gating), not re-proving
    what each individual primitive already proves about its own output."""
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


# --- hard gate: proven by spying on subprocess.run, not by inspecting output ---


def test_final_never_invokes_ffmpeg_when_review_required(demo_project, monkeypatch):
    plan_path = write_plan(demo_project, base_plan(status="READY", warnings=[
        {"code": "SOME_WARNING", "message": "...", "severity": "warning", "segment_ref": None}
    ]))

    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a))
    result = render_final(
        plan_path, demo_project, demo_project / "out.mp4", demo_project / "out.ass", demo_project / "out.srt"
    )

    assert result.gated
    assert result.gate_error == "review_required"
    assert calls == []


def test_final_never_invokes_ffmpeg_when_not_ready(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a))

    plan_path = write_plan(tmp_path, base_plan(status="READY", warnings=[
        {"code": "SOME_BLOCKING_THING", "message": "...", "severity": "blocking", "segment_ref": None}
    ]))
    result = render_final(plan_path, tmp_path, tmp_path / "out.mp4", tmp_path / "out.ass", tmp_path / "out.srt")

    assert result.gated
    assert result.gate_error == "not_ready"
    assert calls == []


def test_preview_never_invokes_ffmpeg_when_not_ready(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a))

    plan_path = write_plan(tmp_path, base_plan(status="READY", warnings=[
        {"code": "SOME_BLOCKING_THING", "message": "...", "severity": "blocking", "segment_ref": None}
    ]))
    result = render_preview(plan_path, tmp_path, tmp_path / "out.mp4")

    assert result.gated
    assert result.gate_error == "not_ready"
    assert calls == []


def test_no_output_file_is_created_when_final_is_gated(demo_project):
    plan_path = write_plan(demo_project, base_plan(status="READY", warnings=[
        {"code": "SOME_WARNING", "message": "...", "severity": "warning", "segment_ref": None}
    ]))
    output = demo_project / "final.mp4"

    result = render_final(plan_path, demo_project, output, demo_project / "f.ass", demo_project / "f.srt")

    assert result.gated
    assert not output.exists()
    assert not (demo_project / "f.ass").exists()


# --- evaluation fields exposed on RenderResult (added for Phase 10 ch. 17) --


def test_gated_result_still_exposes_evaluation_fields(demo_project):
    # studio_execution_result=PASS (only a warning, nothing blocking) yet
    # Final is still refused because effective_status != READY - both facts
    # need to survive onto RenderResult for the ch.17 Render Report to
    # record the divergence, even though nothing was ever rendered.
    plan_path = write_plan(demo_project, base_plan(status="READY", warnings=[
        {"code": "SOME_WARNING", "message": "...", "severity": "warning", "segment_ref": None}
    ]))

    result = render_final(
        plan_path, demo_project, demo_project / "out.mp4", demo_project / "out.ass", demo_project / "out.srt"
    )

    assert result.gated
    assert result.studio_execution_result == "PASS"
    assert result.effective_status == "REVIEW_REQUIRED"
    assert result.plan_status_as_declared == "READY"
    assert any(issue.code == "SOME_WARNING" for issue in result.preflight_issues)


@requires_ffmpeg
def test_successful_result_exposes_evaluation_fields(demo_project):
    plan_path = write_plan(demo_project, base_plan(status="READY", warnings=[]))

    result = render_final(
        plan_path, demo_project, demo_project / "final.mp4", demo_project / "f.ass", demo_project / "f.srt"
    )

    assert result.ok, [getattr(sr, "stderr", "") for sr in result.stage_results if not sr.ok]
    assert result.studio_execution_result == "PASS"
    assert result.effective_status == "READY"
    assert result.plan_status_as_declared == "READY"
    assert result.preflight_issues == []


# --- real FFmpeg execution: no mocking ---------------------------------------


@requires_ffmpeg
def test_preview_succeeds_on_review_required_plan(demo_project, probe_duration_ms):
    plan_path = write_plan(demo_project, base_plan(status="REVIEW_REQUIRED", warnings=[
        {"code": "TIMING_GRADE_NOT_EXACT", "message": "...", "severity": "warning", "segment_ref": None}
    ]))
    output = demo_project / "preview.mp4"

    result = render_preview(plan_path, demo_project, output)

    assert result.ok, [getattr(sr, "stderr", "") for sr in result.stage_results if not sr.ok]
    assert not result.gated
    assert output.exists()
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "csv=s=x:p=0", str(output)],
        capture_output=True, text=True, check=True,
    )
    assert proc.stdout.strip() == "720x1280"
    assert abs(probe_duration_ms(output) - 4000) <= 100


@requires_ffmpeg
def test_final_succeeds_on_ready_plan_and_exports_ass_and_srt(demo_project, probe_duration_ms):
    plan_path = write_plan(demo_project, base_plan(status="READY", warnings=[]))
    output = demo_project / "final.mp4"
    ass_path = demo_project / "final.ass"
    srt_path = demo_project / "final.srt"

    result = render_final(plan_path, demo_project, output, ass_path, srt_path)

    assert result.ok, [getattr(sr, "stderr", "") for sr in result.stage_results if not sr.ok]
    assert not result.gated
    assert output.exists()
    assert ass_path.exists()
    assert srt_path.exists()
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "csv=s=x:p=0", str(output)],
        capture_output=True, text=True, check=True,
    )
    assert proc.stdout.strip() == "1080x1920"
    assert abs(probe_duration_ms(output) - 4000) <= 100


@requires_ffmpeg
def test_concat_order_is_preserved_through_the_whole_pipeline(demo_project, sample_frame_rgb):
    # shot_1a (navy) plays 0-2s, shot_1b (darkgreen) plays 2-4s - proves the
    # pipeline threads segments through in edit_plan.json order end to end,
    # the same real-pixel-value pattern Phase 4 used for concat_clips()
    # alone, now checked through every stage between it and the final file.
    plan_path = write_plan(demo_project, base_plan(status="READY", warnings=[]))
    output = demo_project / "final.mp4"

    result = render_final(plan_path, demo_project, output, demo_project / "f.ass", demo_project / "f.srt")
    assert result.ok, [getattr(sr, "stderr", "") for sr in result.stage_results if not sr.ok]

    early_rgb = sample_frame_rgb(output, at_ms=500)
    late_rgb = sample_frame_rgb(output, at_ms=3500)

    # navy ~ (0,0,128), darkgreen ~ (0,100,0): blue channel distinguishes them.
    assert early_rgb[2] > late_rgb[2]


@requires_ffmpeg
def test_settle_frame_hold_extends_duration_through_the_whole_pipeline(demo_project, probe_duration_ms):
    # hold_clip() must run per-segment before concat (see render/pipeline.py
    # module docstring) - this proves it actually happens that way when
    # wired into the real orchestrator, not just in hold.py's own isolated
    # tests. 1000ms of hold on shot 1A should show up as 1000ms of extra
    # total duration, matching the Duration Invariant.
    # demo_project's voice track is a fixed 4s by default - regenerate it to
    # match the 1000ms hold this test adds, or mix_audio()'s -t (driven by
    # voice.total_duration_ms=5000) would ask for 5s of audio it doesn't
    # have, and mux_and_encode()'s -shortest would then cap the whole
    # output back down to whichever stream is actually shorter.
    _tone(demo_project / "clips" / "audio" / "voice_final.wav", 5)

    plan_path = write_plan(demo_project, base_plan(status="READY", warnings=[], hold_ms=1000))
    output = demo_project / "final.mp4"

    result = render_final(plan_path, demo_project, output, demo_project / "f.ass", demo_project / "f.srt")

    assert result.ok, [getattr(sr, "stderr", "") for sr in result.stage_results if not sr.ok]
    assert abs(probe_duration_ms(output) - 5000) <= 100  # 4000 base + 1000 hold


@requires_ffmpeg
def test_preview_has_a_watermark_and_final_does_not(demo_project, frame_region_stddev):
    plan_path = write_plan(demo_project, base_plan(status="READY", warnings=[]))

    preview_output = demo_project / "preview.mp4"
    preview_result = render_preview(plan_path, demo_project, preview_output)
    assert preview_result.ok, [getattr(sr, "stderr", "") for sr in preview_result.stage_results if not sr.ok]

    final_output = demo_project / "final.mp4"
    final_result = render_final(plan_path, demo_project, final_output, demo_project / "f.ass", demo_project / "f.srt")
    assert final_result.ok, [getattr(sr, "stderr", "") for sr in final_result.stage_results if not sr.ok]

    # The last stage in both pipelines is always the encode step - check its
    # command directly rather than searching every stage.
    assert "drawtext" in " ".join(preview_result.stage_results[-1].command)
    assert "drawtext" not in " ".join(final_result.stage_results[-1].command)


@requires_ffmpeg
def test_never_modifies_source_clips_or_voice(demo_project):
    clip_a = demo_project / "clips" / "shot_1a.mp4"
    voice = demo_project / "clips" / "audio" / "voice_final.wav"
    hash_clip_before = hashlib.sha256(clip_a.read_bytes()).hexdigest()
    hash_voice_before = hashlib.sha256(voice.read_bytes()).hexdigest()

    plan_path = write_plan(demo_project, base_plan(status="READY", warnings=[]))
    result = render_final(
        plan_path, demo_project, demo_project / "final.mp4", demo_project / "f.ass", demo_project / "f.srt"
    )

    assert result.ok, [getattr(sr, "stderr", "") for sr in result.stage_results if not sr.ok]
    assert hashlib.sha256(clip_a.read_bytes()).hexdigest() == hash_clip_before
    assert hashlib.sha256(voice.read_bytes()).hexdigest() == hash_voice_before
