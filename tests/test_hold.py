import hashlib
import shutil
import subprocess

import pytest

from seoulkit_studio.render import hold_clip
from seoulkit_studio.schema import DEFAULT_DURATION_TOLERANCE_MS

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed on this system",
)


# --- pure logic: no real ffmpeg execution needed ----------------------------


def test_hold_ms_zero_raises_value_error(tmp_path):
    # hold_clip() should only ever be called for a settle_frame_hold segment,
    # which the schema guarantees hold_ms >= 1 for. hold_ms=0 arriving here
    # is itself evidence of a caller mistake (e.g. calling this for a
    # source_hold segment, where hold_ms is schema-guaranteed to be 0) -
    # that must fail loudly, not silently no-op.
    with pytest.raises(ValueError):
        hold_clip(tmp_path / "in.mp4", tmp_path / "out.mp4", 0)


def test_negative_hold_ms_raises_value_error(tmp_path):
    with pytest.raises(ValueError):
        hold_clip(tmp_path / "in.mp4", tmp_path / "out.mp4", -100)


def test_ffmpeg_not_found_is_reported_without_raising(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)

    result = hold_clip(tmp_path / "in.mp4", tmp_path / "out.mp4", 500)

    assert not result.ok
    assert result.error == "ffmpeg_not_found"
    assert result.command == []


def test_command_uses_ms_to_seconds_str_for_stop_duration(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/ffmpeg")

    captured = {}

    def fake_run(command, capture_output, text):
        captured["command"] = command
        return subprocess.CompletedProcess(command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    clip = tmp_path / "in.mp4"
    output = tmp_path / "out.mp4"
    result = hold_clip(clip, output, hold_ms=1234)

    command = captured["command"]
    assert command[0] == "/usr/bin/ffmpeg"
    assert command[command.index("-i") + 1] == str(clip)
    vf = command[command.index("-vf") + 1]
    assert vf == "tpad=stop_mode=clone:stop_duration=1.234"
    assert command[-1] == str(output)
    assert result.ok


# --- real FFmpeg execution: no mocking --------------------------------------


@requires_ffmpeg
def test_hold_extends_duration_by_hold_ms(tmp_path, make_synthetic_video, probe_duration_ms):
    clip = make_synthetic_video(tmp_path / "in.mp4", duration_ms=2000)
    output = tmp_path / "held.mp4"

    result = hold_clip(clip, output, hold_ms=500)

    assert result.ok, result.stderr
    actual_ms = probe_duration_ms(output)
    assert abs(actual_ms - 2500) <= DEFAULT_DURATION_TOLERANCE_MS


@requires_ffmpeg
def test_calling_hold_clip_twice_visibly_doubles_the_extension(tmp_path, make_synthetic_video, probe_duration_ms):
    # hold_clip() cannot prevent being called twice - it's a stateless
    # function with no memory of prior calls. This test doesn't prove
    # double-application is prevented (it isn't, and can't be, at this
    # layer); it proves double-application is *measurable*, so whichever
    # future assembly layer decides when to call hold_clip() has a concrete
    # pattern to build its own regression test against.
    clip = make_synthetic_video(tmp_path / "in.mp4", duration_ms=2000)
    once = tmp_path / "held_once.mp4"
    twice = tmp_path / "held_twice.mp4"

    assert hold_clip(clip, once, hold_ms=500).ok
    assert hold_clip(once, twice, hold_ms=500).ok

    once_ms = probe_duration_ms(once)
    twice_ms = probe_duration_ms(twice)
    assert abs(once_ms - 2500) <= DEFAULT_DURATION_TOLERANCE_MS
    assert abs(twice_ms - 3000) <= DEFAULT_DURATION_TOLERANCE_MS  # 2000 + 500 + 500, not 2000 + 500


@requires_ffmpeg
def test_hold_never_modifies_the_source_clip(tmp_path, make_synthetic_video):
    clip = make_synthetic_video(tmp_path / "in.mp4", duration_ms=1500)
    hash_before = hashlib.sha256(clip.read_bytes()).hexdigest()

    result = hold_clip(clip, tmp_path / "held.mp4", hold_ms=300)

    assert result.ok, result.stderr
    assert hashlib.sha256(clip.read_bytes()).hexdigest() == hash_before


@requires_ffmpeg
def test_ffmpeg_failure_on_missing_clip_is_captured_not_raised(tmp_path):
    missing = tmp_path / "does_not_exist.mp4"

    result = hold_clip(missing, tmp_path / "out.mp4", hold_ms=500)

    assert not result.ok
    assert result.error == "ffmpeg_failed"
    assert result.returncode != 0
    assert result.stderr != ""
