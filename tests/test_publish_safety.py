"""Tests for the Phase 9 partial-output hotfix (`render/pipeline.py`'s
`_Publishable`/`_stage_copies`/`_commit_all`/`_rollback`/`PublishResult`).

Two layers, deliberately kept separate:
- Low-level tests call the private publish helpers directly against plain
  files (no FFmpeg involved) - this is what actually exercises the
  two-phase-commit/rollback logic itself, including failure modes that
  would be slow or awkward to trigger through a real render.
- End-to-end tests go through `render_final()`/`render_preview()`/
  `render_final_and_report()` with real FFmpeg, to prove the fix at the
  level the original bug was reported at: a real failed render must not
  leave a new/partial file at the real output path, and an existing valid
  Final set must survive a subsequent failed re-render byte-identical.
"""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from seoulkit_studio.render.encode import EncodeResult
from seoulkit_studio.render.pipeline import (
    _Publishable,
    _commit_all,
    _rollback,
    _stage_copies,
)
from seoulkit_studio.render.pipeline import render_final, render_preview
from seoulkit_studio.render.report import render_final_and_report

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
        "project": "publish-safety-test",
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


def _leftover_names(directory: Path, exclude: set[str]) -> list[str]:
    return sorted(p.name for p in directory.iterdir() if p.name not in exclude)


# --- low-level: _stage_copies() / _commit_all() / _rollback() ----------------


def test_stage_copies_failure_touches_no_real_destination_and_leaves_no_tmp(tmp_path, monkeypatch):
    staged_a = tmp_path / "staged_a.bin"
    staged_a.write_bytes(b"A")
    staged_b = tmp_path / "staged_b.bin"
    staged_b.write_bytes(b"B")
    dest_a = tmp_path / "dest_a.bin"
    dest_b = tmp_path / "dest_b.bin"

    publishables = [
        _Publishable(staged_path=staged_a, dest_path=dest_a),
        _Publishable(staged_path=staged_b, dest_path=dest_b),
    ]

    real_copy2 = shutil.copy2

    def flaky_copy2(src, dst):
        if str(src) == str(staged_b):
            raise OSError("simulated stage failure")
        real_copy2(src, dst)

    monkeypatch.setattr("seoulkit_studio.render.pipeline.shutil.copy2", flaky_copy2)

    error = _stage_copies(publishables)

    assert error is not None
    assert not dest_a.exists()
    assert not dest_b.exists()
    assert _leftover_names(tmp_path, {"staged_a.bin", "staged_b.bin"}) == []


def test_commit_failure_restores_existing_destination_byte_identical_no_bak_leftover(tmp_path, monkeypatch):
    # Regression test for a real bug found while implementing this hotfix:
    # a failure on the *second* os.replace() of a single publishable's
    # commit (tmp -> dest, right after dest -> backup already succeeded)
    # was not being rolled back at all, because that publishable was never
    # appended to `committed` - the backup was then deleted by `_cleanup()`,
    # permanently losing the original content. Confirmed failing against
    # the first version of `_commit_all()` before the fix below existed.
    dest = tmp_path / "out.mp4"
    dest.write_bytes(b"OLD-CONTENT")
    staged = tmp_path / "staged.mp4"
    staged.write_bytes(b"NEW-CONTENT")

    publishables = [_Publishable(staged_path=staged, dest_path=dest)]
    assert _stage_copies(publishables) is None

    import os as os_module
    real_replace = os_module.replace
    calls = {"n": 0}

    def flaky_replace(src, dst):
        if str(dst) == str(dest):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("simulated commit failure")
        real_replace(src, dst)

    monkeypatch.setattr("seoulkit_studio.render.pipeline.os.replace", flaky_replace)

    error_kind, message = _commit_all(publishables)

    assert error_kind == "publish_commit_failed"
    assert dest.read_bytes() == b"OLD-CONTENT"
    assert _leftover_names(tmp_path, {"out.mp4", "staged.mp4"}) == []


def test_commit_failure_partway_through_multiple_files_rolls_back_all_committed(tmp_path, monkeypatch):
    dests = {}
    publishables = []
    for name, old, new in [("a.bin", b"OLD-A", b"NEW-A"), ("b.bin", b"OLD-B", b"NEW-B"), ("c.bin", b"OLD-C", b"NEW-C")]:
        dest = tmp_path / name
        dest.write_bytes(old)
        staged = tmp_path / f"staged_{name}"
        staged.write_bytes(new)
        dests[name] = (dest, old)
        publishables.append(_Publishable(staged_path=staged, dest_path=dest))

    assert _stage_copies(publishables) is None

    import os as os_module
    real_replace = os_module.replace
    c_dest = dests["c.bin"][0]
    calls = {"n": 0}

    def flaky_replace(src, dst):
        if str(dst) == str(c_dest):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("simulated commit failure on third file")
        real_replace(src, dst)

    monkeypatch.setattr("seoulkit_studio.render.pipeline.os.replace", flaky_replace)

    error_kind, message = _commit_all(publishables)

    assert error_kind == "publish_commit_failed"
    for name, (dest, old) in dests.items():
        assert dest.read_bytes() == old, f"{name} was not restored to its original content"

    exclude = {"a.bin", "b.bin", "c.bin", "staged_a.bin", "staged_b.bin", "staged_c.bin"}
    assert _leftover_names(tmp_path, exclude) == []


def test_commit_full_success_replaces_existing_set_and_leaves_no_temp_files(tmp_path):
    dest_a = tmp_path / "a.bin"
    dest_a.write_bytes(b"OLD-A")
    dest_b = tmp_path / "b.bin"
    dest_b.write_bytes(b"OLD-B")
    staged_a = tmp_path / "staged_a.bin"
    staged_a.write_bytes(b"NEW-A")
    staged_b = tmp_path / "staged_b.bin"
    staged_b.write_bytes(b"NEW-B")

    publishables = [
        _Publishable(staged_path=staged_a, dest_path=dest_a),
        _Publishable(staged_path=staged_b, dest_path=dest_b),
    ]
    assert _stage_copies(publishables) is None

    error_kind, message = _commit_all(publishables)

    assert error_kind is None
    assert dest_a.read_bytes() == b"NEW-A"
    assert dest_b.read_bytes() == b"NEW-B"
    assert _leftover_names(tmp_path, {"a.bin", "b.bin", "staged_a.bin", "staged_b.bin"}) == []


def test_commit_success_when_destination_did_not_exist_before(tmp_path):
    dest = tmp_path / "new_output.bin"
    staged = tmp_path / "staged.bin"
    staged.write_bytes(b"BRAND-NEW")

    publishables = [_Publishable(staged_path=staged, dest_path=dest)]
    assert _stage_copies(publishables) is None

    error_kind, message = _commit_all(publishables)

    assert error_kind is None
    assert dest.read_bytes() == b"BRAND-NEW"
    assert _leftover_names(tmp_path, {"new_output.bin", "staged.bin"}) == []


def test_rollback_failure_is_reported_as_publish_rollback_failed_not_hidden(tmp_path, monkeypatch):
    dest_a = tmp_path / "a.bin"
    dest_a.write_bytes(b"OLD-A")
    dest_b = tmp_path / "b.bin"
    dest_b.write_bytes(b"OLD-B")
    staged_a = tmp_path / "staged_a.bin"
    staged_a.write_bytes(b"NEW-A")
    staged_b = tmp_path / "staged_b.bin"
    staged_b.write_bytes(b"NEW-B")

    publishables = [
        _Publishable(staged_path=staged_a, dest_path=dest_a),
        _Publishable(staged_path=staged_b, dest_path=dest_b),
    ]
    assert _stage_copies(publishables) is None

    import os as os_module
    real_replace = os_module.replace
    calls = {"b_commit": 0}

    def flaky_replace(src, dst):
        # a.bin commits normally (backup + commit both succeed). b.bin's
        # own commit (tmp -> dest_b) fails once, triggering rollback; b's
        # own restore (backup -> dest_b) is then allowed to succeed
        # trivially, but a.bin's restore (backup -> dest_a, the file that
        # was actually already published) is made to fail every time - so
        # the rollback itself, not just the original commit, fails.
        if str(dst) == str(dest_b) and calls["b_commit"] == 0:
            calls["b_commit"] += 1
            raise OSError("simulated commit failure on b.bin")
        if str(dst) == str(dest_a) and str(src).endswith(".bak"):
            raise OSError("simulated rollback failure on a.bin")
        real_replace(src, dst)

    monkeypatch.setattr("seoulkit_studio.render.pipeline.os.replace", flaky_replace)

    error_kind, message = _commit_all(publishables)

    assert error_kind == "publish_rollback_failed"
    assert "manual check needed" in message
    assert "a.bin" in message
    # b.bin's own restore succeeded even though its commit failed - only
    # a.bin's rollback (the one deliberately broken above) is unresolved.
    assert dest_b.read_bytes() == b"OLD-B"


# --- end-to-end: real render_final()/render_preview(), real FFmpeg -----------


@requires_ffmpeg
def test_audio_mix_failure_leaves_no_new_final_outputs(demo_project):
    plan_path = write_plan(demo_project, base_plan(status="READY", warnings=[]))
    voice_path = demo_project / "clips" / "audio" / "voice_final.wav"
    voice_path.write_bytes(b"not a real wav file")

    output = demo_project / "final.mp4"
    ass_output = demo_project / "f.ass"
    srt_output = demo_project / "f.srt"

    result = render_final(plan_path, demo_project, output, ass_output, srt_output)

    assert not result.ok
    assert not output.exists()
    assert not ass_output.exists()
    assert not srt_output.exists()


@requires_ffmpeg
def test_preview_encode_failure_leaves_no_new_preview_output(demo_project, monkeypatch):
    plan_path = write_plan(demo_project, base_plan(status="READY", warnings=[]))
    output = demo_project / "preview.mp4"

    def failing_mux_and_encode(*args, **kwargs):
        return EncodeResult(
            output_path=args[2] if len(args) > 2 else kwargs.get("output_path"),
            command=["ffmpeg", "-simulated-failure"],
            returncode=1,
            stdout="",
            stderr="simulated encode failure",
            error="ffmpeg_failed",
        )

    monkeypatch.setattr("seoulkit_studio.render.pipeline.mux_and_encode", failing_mux_and_encode)

    result = render_preview(plan_path, demo_project, output)

    assert not result.ok
    assert not output.exists()


@requires_ffmpeg
def test_existing_valid_final_set_survives_a_subsequent_failed_render_byte_identical(demo_project):
    plan_path = write_plan(demo_project, base_plan(status="READY", warnings=[]))
    output = demo_project / "final.mp4"
    ass_output = demo_project / "f.ass"
    srt_output = demo_project / "f.srt"

    good_result = render_final(plan_path, demo_project, output, ass_output, srt_output)
    assert good_result.ok, [getattr(sr, "stderr", "") for sr in good_result.stage_results if not sr.ok]

    hash_before = {p.name: _sha256(p) for p in (output, ass_output, srt_output)}

    voice_path = demo_project / "clips" / "audio" / "voice_final.wav"
    voice_path.write_bytes(b"not a real wav file")

    failed_result = render_final(plan_path, demo_project, output, ass_output, srt_output)

    assert not failed_result.ok
    for p in (output, ass_output, srt_output):
        assert _sha256(p) == hash_before[p.name], f"{p.name} changed after a failed re-render"


@requires_ffmpeg
def test_publish_copy_failure_appears_in_render_report_errors_with_publish_stage(demo_project, monkeypatch):
    plan_path = write_plan(demo_project, base_plan(status="READY", warnings=[]))

    def flaky_copy2(src, dst):
        raise OSError("simulated publish stage failure")

    monkeypatch.setattr("seoulkit_studio.render.pipeline.shutil.copy2", flaky_copy2)

    result, report = render_final_and_report(plan_path, demo_project)

    assert not result.ok
    assert report.output_file is None
    assert any(err.stage == "publish_outputs" and err.code == "publish_copy_failed" for err in report.errors)
