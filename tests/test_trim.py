import shutil
import subprocess

import pytest

from seoulkit_studio.render import trim_clip
from seoulkit_studio.schema import DEFAULT_DURATION_TOLERANCE_MS

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed on this system",
)


# --- pure logic: no real ffmpeg execution needed ----------------------------


def test_ffmpeg_not_found_is_reported_without_raising(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)

    result = trim_clip(tmp_path / "source.mp4", tmp_path / "out.mp4", 0, 1000)

    assert not result.ok
    assert result.error == "ffmpeg_not_found"
    assert result.command == []
    assert result.returncode is None


def test_command_is_built_from_ms_to_ffmpeg_timestamp_not_raw_ms(tmp_path, monkeypatch):
    # Verifies our own command-assembly logic (start/duration/argv shape) in
    # isolation from FFmpeg's actual behavior - subprocess itself is faked
    # here. The real-execution behavior is covered separately below, without
    # any mocking.
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/ffmpeg")

    captured = {}

    def fake_run(command, capture_output, text):
        captured["command"] = command
        return subprocess.CompletedProcess(command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    source = tmp_path / "source.mp4"
    output = tmp_path / "out.mp4"
    result = trim_clip(source, output, clip_in_ms=1500, clip_out_ms=4200)

    command = captured["command"]
    assert command[0] == "/usr/bin/ffmpeg"
    assert command[command.index("-ss") + 1] == "00:00:01.500"
    assert command[command.index("-i") + 1] == str(source)
    assert command[command.index("-t") + 1] == "00:00:02.700"  # 4200 - 1500
    assert "-an" in command
    assert command[-1] == str(output)
    assert result.ok
    assert result.returncode == 0


# --- real FFmpeg execution: no mocking --------------------------------------


@requires_ffmpeg
def test_trim_produces_output_with_expected_duration(tmp_path, make_synthetic_video, probe_duration_ms):
    source = make_synthetic_video(tmp_path / "source.mp4", duration_ms=6000)
    output = tmp_path / "trimmed.mp4"

    result = trim_clip(source, output, clip_in_ms=1000, clip_out_ms=4000)

    assert result.ok, result.stderr
    assert output.is_file()
    actual_ms = probe_duration_ms(output)
    assert abs(actual_ms - 3000) <= DEFAULT_DURATION_TOLERANCE_MS


@requires_ffmpeg
def test_trim_drops_audio_even_when_source_has_audio(tmp_path, make_synthetic_video, probe_has_audio_stream):
    # The Clip Engine (this module) and the Audio Mixer are separate boxes
    # in the ch. 01 architecture diagram, meeting only at pipeline step 6.
    # A source clip's own embedded audio - if any - is never part of the
    # final mix, so it must not survive this trim step either.
    source = make_synthetic_video(tmp_path / "source.mp4", duration_ms=3000, with_audio=True)
    assert probe_has_audio_stream(source)  # sanity: the source really has audio

    output = tmp_path / "trimmed.mp4"
    result = trim_clip(source, output, clip_in_ms=0, clip_out_ms=2000)

    assert result.ok, result.stderr
    assert not probe_has_audio_stream(output)


@requires_ffmpeg
def test_clip_out_ms_beyond_source_duration_truncates_without_error(tmp_path, make_synthetic_video, probe_duration_ms):
    # Phase 1's OUT_OF_USABLE_RANGE is supposed to catch this upstream in the
    # normal pipeline; this test isn't a defensive check added here, it's a
    # documented observation of FFmpeg's own behavior when that never
    # happens - so a future reader isn't left guessing whether an over-long
    # clip_out_ms would crash the trim step or just quietly truncate.
    source = make_synthetic_video(tmp_path / "source.mp4", duration_ms=2000)
    output = tmp_path / "trimmed.mp4"

    result = trim_clip(source, output, clip_in_ms=0, clip_out_ms=10_000)  # source is only 2000ms

    assert result.ok, result.stderr
    actual_ms = probe_duration_ms(output)
    assert actual_ms < 10_000
    assert abs(actual_ms - 2000) <= DEFAULT_DURATION_TOLERANCE_MS


@requires_ffmpeg
def test_ffmpeg_failure_on_missing_source_is_captured_not_raised(tmp_path):
    missing_source = tmp_path / "does_not_exist.mp4"
    output = tmp_path / "out.mp4"

    result = trim_clip(missing_source, output, clip_in_ms=0, clip_out_ms=1000)

    assert not result.ok
    assert result.error == "ffmpeg_failed"
    assert result.returncode != 0
    assert result.stderr != ""
    assert not output.exists()
