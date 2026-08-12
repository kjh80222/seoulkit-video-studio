import hashlib
import shutil
import subprocess

import pytest

from seoulkit_studio.render import (
    burn_subtitles,
    generate_ass,
    generate_srt,
    probe_video_resolution,
)
from seoulkit_studio.render.fonts import FONT_DIR, BundledFontError

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed on this system",
)


def one_subtitle(**overrides) -> dict:
    sub = {
        "beat": 1,
        "start_ms": 500,
        "end_ms": 1500,
        "lines": ["첫 번째 줄"],
        "position_preset": "bottom-center",
    }
    sub.update(overrides)
    return sub


# --- generate_ass(): pure text, no ffmpeg needed ----------------------------


def test_generate_ass_header_uses_given_play_res():
    ass = generate_ass([], play_res_x=1080, play_res_y=1920)

    assert "PlayResX: 1080" in ass
    assert "PlayResY: 1920" in ass


def test_generate_ass_style_uses_ch18_values():
    ass = generate_ass([], play_res_x=640, play_res_y=360)

    style_line = next(line for line in ass.splitlines() if line.startswith("Style:"))
    fields = style_line.split(",")
    assert fields[1] == "Noto Sans KR"
    assert fields[2] == "44"
    outline, shadow = fields[16], fields[17]
    assert outline == "3"
    assert shadow == "1"


def test_generate_ass_maps_bottom_center_to_an2_and_margin_120():
    ass = generate_ass([one_subtitle(position_preset="bottom-center")], 640, 360)

    dialogue = next(line for line in ass.splitlines() if line.startswith("Dialogue:"))
    fields = dialogue.split(",", 9)
    margin_v = fields[7]
    text = fields[9]
    assert margin_v == "120"
    assert text.startswith(r"{\an2}")


def test_generate_ass_maps_top_center_to_an8_and_margin_100():
    ass = generate_ass([one_subtitle(position_preset="top-center")], 640, 360)

    dialogue = next(line for line in ass.splitlines() if line.startswith("Dialogue:"))
    fields = dialogue.split(",", 9)
    margin_v = fields[7]
    text = fields[9]
    assert margin_v == "100"
    assert text.startswith(r"{\an8}")


def test_generate_ass_joins_multiple_lines_with_ass_linebreak():
    ass = generate_ass([one_subtitle(lines=["첫 줄", "둘째 줄"])], 640, 360)

    dialogue = next(line for line in ass.splitlines() if line.startswith("Dialogue:"))
    assert r"첫 줄\N둘째 줄" in dialogue


def test_generate_ass_uses_ass_timestamp_format_not_ffmpeg_format():
    ass = generate_ass([one_subtitle(start_ms=1234, end_ms=5678)], 640, 360)

    dialogue = next(line for line in ass.splitlines() if line.startswith("Dialogue:"))
    # ms_to_ass_timestamp(1234) == "0:00:01.23", ms_to_ass_timestamp(5678) == "0:00:05.68"
    assert "0:00:01.23" in dialogue
    assert "0:00:05.68" in dialogue


def test_generate_ass_empty_subtitles_still_produces_valid_header():
    ass = generate_ass([], 640, 360)

    assert "[Script Info]" in ass
    assert "[V4+ Styles]" in ass
    assert "[Events]" in ass
    assert "Dialogue:" not in ass


# --- generate_srt(): pure text, no ffmpeg needed ----------------------------


def test_generate_srt_produces_numbered_blocks():
    srt = generate_srt(
        [
            one_subtitle(start_ms=500, end_ms=1500, lines=["첫 번째"]),
            one_subtitle(start_ms=2000, end_ms=3000, lines=["두 번째"]),
        ]
    )

    assert srt.startswith("1\n00:00:00,500 --> 00:00:01,500\n첫 번째\n")
    assert "2\n00:00:02,000 --> 00:00:03,000\n두 번째\n" in srt


def test_generate_srt_preserves_full_millisecond_precision():
    # Unlike ASS, SRT uses the same HH:MM:SS,mmm precision as
    # ms_to_ffmpeg_timestamp()'s HH:MM:SS.mmm. No new rounding behavior
    # needed; reusing that zero-rounding-error function and swapping the
    # separator is simpler and safer than writing a parallel formatter.
    srt = generate_srt([one_subtitle(start_ms=1234, end_ms=5678, lines=["x"])])

    assert "00:00:01,234 --> 00:00:05,678" in srt


def test_generate_srt_joins_multiple_lines_with_real_newline():
    srt = generate_srt([one_subtitle(lines=["첫 줄", "둘째 줄"])])

    assert "첫 줄\n둘째 줄" in srt


# --- burn_subtitles(): pure logic, no real ffmpeg execution needed ---------


def test_colon_in_ass_path_is_rejected_before_ffmpeg_runs(tmp_path):
    bad_ass_path = tmp_path / "sub:title.ass"  # colon in filename

    with pytest.raises(ValueError):
        burn_subtitles(tmp_path / "in.mp4", bad_ass_path, tmp_path / "out.mp4")


def test_ffmpeg_not_found_is_reported_without_raising(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)

    result = burn_subtitles(tmp_path / "in.mp4", tmp_path / "sub.ass", tmp_path / "out.mp4")

    assert not result.ok
    assert result.error == "ffmpeg_not_found"
    assert result.command == []


def test_command_uses_ass_filter_with_the_given_path(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/ffmpeg")

    captured = {}

    def fake_run(command, capture_output, text):
        captured["command"] = command
        return subprocess.CompletedProcess(command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    video = tmp_path / "in.mp4"
    ass = tmp_path / "sub.ass"
    output = tmp_path / "out.mp4"
    result = burn_subtitles(video, ass, output)

    command = captured["command"]
    assert command[0] == "/usr/bin/ffmpeg"
    assert command[command.index("-i") + 1] == str(video)
    assert command[command.index("-vf") + 1] == f"ass={ass}:fontsdir={FONT_DIR}"
    assert "-an" in command
    assert command[-1] == str(output)
    assert result.ok


def test_missing_bundled_font_is_reported_not_raised(tmp_path, monkeypatch):
    # A missing/corrupt bundled font asset is a packaging defect (see
    # render/fonts.py) - burn_subtitles() must report it on the result the
    # same way it reports ffmpeg_not_found, not let the exception propagate
    # and crash whatever assembly layer called it.
    def fake_resolve(weight):
        raise BundledFontError("simulated missing bundled font")

    monkeypatch.setattr("seoulkit_studio.render.subtitle.resolve_bundled_font", fake_resolve)

    result = burn_subtitles(tmp_path / "in.mp4", tmp_path / "sub.ass", tmp_path / "out.mp4")

    assert not result.ok
    assert result.error == "bundled_font_error"
    assert result.command == []


# --- real FFmpeg execution: no mocking --------------------------------------


@requires_ffmpeg
def test_probe_video_resolution_matches_the_generated_video(tmp_path, make_synthetic_video):
    video = make_synthetic_video(tmp_path / "in.mp4", duration_ms=500)  # default 320x240

    assert probe_video_resolution(video) == (320, 240)


@requires_ffmpeg
def test_burn_in_preserves_duration(tmp_path, make_solid_color_video, probe_duration_ms):
    video = make_solid_color_video(tmp_path / "bg.mp4", duration_ms=2000, color="navy", size="640x360")
    ass_path = tmp_path / "sub.ass"
    ass_path.write_text(generate_ass([one_subtitle(start_ms=500, end_ms=1500)], 640, 360))

    result = burn_subtitles(video, ass_path, tmp_path / "out.mp4")

    assert result.ok, result.stderr
    before_ms = probe_duration_ms(video)
    after_ms = probe_duration_ms(result.output_path)
    assert abs(after_ms - before_ms) <= 50  # duration_tolerance_ms default


@requires_ffmpeg
def test_burn_in_draws_something_in_the_subtitle_region(
    tmp_path, make_solid_color_video, frame_region_stddev
):
    # Flat-color background: any nonzero stddev in the bottom half after
    # burn-in can only have come from something actually drawn there - the
    # burned-in subtitle text, not compression noise or source detail.
    video = make_solid_color_video(tmp_path / "bg.mp4", duration_ms=2000, color="navy", size="640x360")
    ass_path = tmp_path / "sub.ass"
    ass_path.write_text(
        generate_ass([one_subtitle(start_ms=500, end_ms=1500, position_preset="bottom-center")], 640, 360)
    )

    result = burn_subtitles(video, ass_path, tmp_path / "out.mp4")
    assert result.ok, result.stderr

    off_stddev = frame_region_stddev(result.output_path, at_ms=100)  # before the subtitle appears
    on_stddev = frame_region_stddev(result.output_path, at_ms=1000)  # subtitle is on-screen

    assert off_stddev < 1.0  # flat background, nothing drawn yet
    assert on_stddev > 10.0  # text edges present


@requires_ffmpeg
def test_burn_never_modifies_the_source_video(tmp_path, make_solid_color_video):
    video = make_solid_color_video(tmp_path / "bg.mp4", duration_ms=1000, color="navy", size="320x240")
    hash_before = hashlib.sha256(video.read_bytes()).hexdigest()
    ass_path = tmp_path / "sub.ass"
    ass_path.write_text(generate_ass([one_subtitle(start_ms=0, end_ms=1000)], 320, 240))

    result = burn_subtitles(video, ass_path, tmp_path / "out.mp4")

    assert result.ok, result.stderr
    assert hashlib.sha256(video.read_bytes()).hexdigest() == hash_before


@requires_ffmpeg
def test_ffmpeg_failure_on_missing_video_is_captured_not_raised(tmp_path):
    missing_video = tmp_path / "does_not_exist.mp4"
    ass_path = tmp_path / "sub.ass"
    ass_path.write_text(generate_ass([], 640, 360))

    result = burn_subtitles(missing_video, ass_path, tmp_path / "out.mp4")

    assert not result.ok
    assert result.error == "ffmpeg_failed"
    assert result.returncode != 0
    assert result.stderr != ""
