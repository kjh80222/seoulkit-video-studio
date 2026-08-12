import hashlib
import shutil
import subprocess

import pytest

from seoulkit_studio.render import concat_clips

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed on this system",
)


# --- pure logic: no real ffmpeg execution needed ----------------------------


def test_no_clips_is_an_error_without_invoking_ffmpeg(tmp_path):
    result = concat_clips([], tmp_path / "out.mp4")

    assert not result.ok
    assert result.error == "no_clips"
    assert result.command == []


def test_ffmpeg_not_found_is_reported_without_raising(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)

    result = concat_clips([tmp_path / "a.mp4"], tmp_path / "out.mp4")

    assert not result.ok
    assert result.error == "ffmpeg_not_found"
    assert result.command == []


def test_command_maps_every_input_through_the_concat_filter(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/ffmpeg")

    captured = {}

    def fake_run(command, capture_output, text):
        captured["command"] = command
        return subprocess.CompletedProcess(command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    clips = [tmp_path / "a.mp4", tmp_path / "b.mp4", tmp_path / "c.mp4"]
    output = tmp_path / "out.mp4"
    result = concat_clips(clips, output)

    command = captured["command"]
    assert command[0] == "/usr/bin/ffmpeg"
    # each clip appears as its own -i input, in the given order
    input_indices = [i for i, arg in enumerate(command) if arg == "-i"]
    assert [command[i + 1] for i in input_indices] == [str(c) for c in clips]
    filter_complex = command[command.index("-filter_complex") + 1]
    assert filter_complex == "[0:v][1:v][2:v]concat=n=3:v=1:a=0[outv]"
    assert command[command.index("-map") + 1] == "[outv]"
    assert command[-1] == str(output)
    assert result.ok


# --- real FFmpeg execution: no mocking --------------------------------------


@requires_ffmpeg
def test_concat_output_duration_is_the_sum_of_inputs(tmp_path, make_synthetic_video, probe_duration_ms):
    clip_a = make_synthetic_video(tmp_path / "a.mp4", duration_ms=1000)
    clip_b = make_synthetic_video(tmp_path / "b.mp4", duration_ms=1500)
    clip_c = make_synthetic_video(tmp_path / "c.mp4", duration_ms=2000)
    output = tmp_path / "out.mp4"

    result = concat_clips([clip_a, clip_b, clip_c], output)

    assert result.ok, result.stderr
    actual_ms = probe_duration_ms(output)
    assert abs(actual_ms - 4500) <= 50  # 1000 + 1500 + 2000, Duration Invariant's default tolerance


@requires_ffmpeg
def test_single_clip_concat_is_a_noop(tmp_path, make_synthetic_video, probe_duration_ms):
    # No special-cased shortcut for len(clip_paths) == 1 - this runs through
    # exactly the same concat-filter code path as the multi-clip case, so a
    # measured duration match here proves "1 clip in -> equivalent clip out"
    # empirically instead of by inspection.
    clip = make_synthetic_video(tmp_path / "only.mp4", duration_ms=1234)
    output = tmp_path / "out.mp4"

    result = concat_clips([clip], output)

    assert result.ok, result.stderr
    actual_ms = probe_duration_ms(output)
    assert abs(actual_ms - 1234) <= 50


@requires_ffmpeg
def test_concat_preserves_the_given_order_not_sorted(tmp_path, make_solid_color_video, sample_frame_rgb):
    # concat_clips() has no idea what "beat"/"shot" mean and does not sort -
    # it joins clip_paths in exactly the order given. Two visually distinct
    # (color) clips prove this empirically: whichever comes first in the
    # input list must be what actually plays first in the output.
    red = make_solid_color_video(tmp_path / "red.mp4", duration_ms=1000, color="red")
    blue = make_solid_color_video(tmp_path / "blue.mp4", duration_ms=1000, color="blue")

    forward = tmp_path / "forward.mp4"
    assert concat_clips([red, blue], forward).ok
    r, _, b = sample_frame_rgb(forward, at_ms=200)
    assert r > b  # red plays first
    r, _, b = sample_frame_rgb(forward, at_ms=1800)
    assert b > r  # blue plays second

    reversed_output = tmp_path / "reversed.mp4"
    assert concat_clips([blue, red], reversed_output).ok
    r, _, b = sample_frame_rgb(reversed_output, at_ms=200)
    assert b > r  # blue plays first this time
    r, _, b = sample_frame_rgb(reversed_output, at_ms=1800)
    assert r > b  # red plays second this time


@requires_ffmpeg
def test_concat_never_modifies_input_clips(tmp_path, make_synthetic_video):
    clip_a = make_synthetic_video(tmp_path / "a.mp4", duration_ms=800)
    clip_b = make_synthetic_video(tmp_path / "b.mp4", duration_ms=800)
    hashes_before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (clip_a, clip_b)}

    result = concat_clips([clip_a, clip_b], tmp_path / "out.mp4")

    assert result.ok, result.stderr
    for p, hash_before in hashes_before.items():
        assert hashlib.sha256(p.read_bytes()).hexdigest() == hash_before


@requires_ffmpeg
def test_ffmpeg_failure_on_missing_clip_is_captured_not_raised(tmp_path, make_synthetic_video):
    clip_a = make_synthetic_video(tmp_path / "a.mp4", duration_ms=500)
    missing = tmp_path / "does_not_exist.mp4"

    result = concat_clips([clip_a, missing], tmp_path / "out.mp4")

    assert not result.ok
    assert result.error == "ffmpeg_failed"
    assert result.returncode != 0
    assert result.stderr != ""
