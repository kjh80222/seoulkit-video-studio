import hashlib
import shutil

import pytest

from seoulkit_studio.render import (
    OVERLAY_FONT_PRESETS,
    OVERLAY_POSITION_PRESETS,
    build_overlay_filter,
    burn_overlays,
    resolve_font_file,
)
from seoulkit_studio.render.fonts import BundledFontError

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed on this system",
)


def one_overlay(**overrides) -> dict:
    overlay = {
        "beat": 1,
        "shot": None,
        "text": "1953",
        "type": "giant_number",
        "start_ms": 0,
        "end_ms": 2400,
        "position_preset": "top-right",
        "position_override": None,
    }
    overlay.update(overrides)
    return overlay


# --- ch. 18 config tables ----------------------------------------------------


def test_position_presets_match_ch18():
    assert OVERLAY_POSITION_PRESETS["top-left"] == {"x_pct": 8, "y_pct": 8}
    assert OVERLAY_POSITION_PRESETS["top-right"] == {"x_pct": 92, "y_pct": 8, "align": "right"}
    assert OVERLAY_POSITION_PRESETS["top-center"] == {"x_pct": 50, "y_pct": 8, "align": "center"}
    assert OVERLAY_POSITION_PRESETS["bottom-left"] == {"x_pct": 8, "y_pct": 88}
    assert OVERLAY_POSITION_PRESETS["bottom-right"] == {"x_pct": 92, "y_pct": 88, "align": "right"}
    assert OVERLAY_POSITION_PRESETS["center-lower-third"] == {"x_pct": 50, "y_pct": 75, "align": "center"}
    assert OVERLAY_POSITION_PRESETS["center-upper-third"] == {"x_pct": 50, "y_pct": 25, "align": "center"}


def test_font_presets_match_ch18():
    assert OVERLAY_FONT_PRESETS["giant_number"] == {"size": 96, "weight": "bold"}
    assert OVERLAY_FONT_PRESETS["headline"] == {"size": 56, "weight": "bold"}
    assert OVERLAY_FONT_PRESETS["label"] == {"size": 36, "weight": "regular"}


# --- resolve_font_file(): bundled asset lookup, no fontconfig involved ------


def test_resolve_font_file_returns_the_real_bundled_regular_file():
    resolution = resolve_font_file("regular")

    assert resolution.weight == "regular"
    assert "NotoSansKR-Regular.ttf" in resolution.file_path


def test_resolve_font_file_returns_the_real_bundled_bold_file():
    resolution = resolve_font_file("bold")

    assert resolution.weight == "bold"
    assert "NotoSansKR-Bold.ttf" in resolution.file_path


# --- build_overlay_filter(): pure string construction -----------------------


def test_build_overlay_filter_left_aligned_uses_literal_anchor_x():
    filter_str, _, _ = build_overlay_filter([one_overlay(position_preset="top-left")], 1000, 1000)

    assert "x=80:" in filter_str  # 8% of 1000


def test_build_overlay_filter_right_aligned_subtracts_text_width():
    filter_str, _, _ = build_overlay_filter([one_overlay(position_preset="top-right")], 1000, 1000)

    assert "x=920-text_w:" in filter_str  # 92% of 1000


def test_build_overlay_filter_center_aligned_halves_text_width():
    filter_str, _, _ = build_overlay_filter([one_overlay(position_preset="top-center")], 1000, 1000)

    assert "x=500-text_w/2:" in filter_str  # 50% of 1000


def test_position_override_replaces_coordinates_but_keeps_preset_alignment():
    # top-right's alignment is "right" - an override must still right-align
    # (subtract text_w), just anchored at the override's own coordinates,
    # not the preset's 92%.
    overlay = one_overlay(position_preset="top-right", position_override={"x_pct": 10, "y_pct": 10, "reason": "x"})
    filter_str, _, _ = build_overlay_filter([overlay], 1000, 1000)

    assert "x=100-text_w:y=100:" in filter_str  # 10% of 1000, still right-aligned


def test_build_overlay_filter_uses_seconds_for_enable_between():
    overlay = one_overlay(start_ms=1234, end_ms=5678)
    filter_str, _, _ = build_overlay_filter([overlay], 1000, 1000)

    assert "between(t,1.234,5.678)" in filter_str


def test_build_overlay_filter_escapes_special_characters_in_text():
    overlay = one_overlay(text="a:b'c\\d")
    filter_str, _, _ = build_overlay_filter([overlay], 1000, 1000)

    assert r"a\:b\'c\\d" in filter_str


def test_build_overlay_filter_chains_multiple_overlays_with_commas():
    filter_str, _, _ = build_overlay_filter(
        [one_overlay(position_preset="top-left"), one_overlay(position_preset="bottom-right")], 1000, 1000
    )

    assert filter_str.count("drawtext=") == 2
    assert "," in filter_str


def test_build_overlay_filter_uses_fontfile_not_a_fontconfig_pattern():
    # drawtext doesn't support "Name:style=Bold" pattern syntax at all
    # (Phase 7 finding) - the only thing that can appear here is a real
    # fontfile= path to one of the bundled .ttf files.
    filter_str, _, _ = build_overlay_filter([one_overlay(type="giant_number")], 1000, 1000)

    assert "fontfile=" in filter_str
    assert "NotoSansKR-Bold.ttf" in filter_str


def test_build_overlay_filter_reports_no_font_issues_when_bundle_is_intact():
    _, resolutions, issues = build_overlay_filter([one_overlay()], 1000, 1000)

    assert len(resolutions) == 1
    assert issues == []


def test_build_overlay_filter_records_a_font_substitution_issue_when_bundle_is_broken(monkeypatch):
    def fake_resolve(weight):
        raise BundledFontError(f"simulated missing bundled font ({weight})")

    monkeypatch.setattr("seoulkit_studio.render.overlay.resolve_font_file", fake_resolve)

    filter_str, resolutions, issues = build_overlay_filter([one_overlay()], 1000, 1000)

    assert filter_str == ""  # no drawtext clause can be built without a real font file
    assert resolutions == []
    assert len(issues) == 1
    assert issues[0].code == "FONT_BUNDLE_INVALID"
    assert issues[0].severity is None  # deliberately undecided, not yet wired to a severity
    assert "simulated missing bundled font" in issues[0].detail


# --- burn_overlays(): pure logic, no real ffmpeg execution needed -----------


def test_ffmpeg_not_found_is_reported_without_raising(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)

    result = burn_overlays(tmp_path / "in.mp4", [one_overlay()], tmp_path / "out.mp4")

    assert not result.ok
    assert result.error == "ffmpeg_not_found"
    assert result.command == []


def test_bundled_font_error_is_reported_without_invoking_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        "seoulkit_studio.render.overlay.probe_video_resolution", lambda path: (1000, 1000)
    )

    def fake_resolve(weight):
        raise BundledFontError("simulated missing bundled font")

    monkeypatch.setattr("seoulkit_studio.render.overlay.resolve_font_file", fake_resolve)

    result = burn_overlays(tmp_path / "in.mp4", [one_overlay()], tmp_path / "out.mp4")

    assert not result.ok
    assert result.error == "bundled_font_error"
    assert result.command == []
    assert len(result.font_issues) == 1


# --- real FFmpeg execution: no mocking --------------------------------------


@requires_ffmpeg
def test_burn_overlays_with_empty_list_is_a_passthrough(tmp_path, make_solid_color_video, probe_duration_ms):
    video = make_solid_color_video(tmp_path / "bg.mp4", duration_ms=1000, color="navy", size="320x240")

    result = burn_overlays(video, [], tmp_path / "out.mp4")

    assert result.ok, result.stderr
    assert "-vf" not in result.command  # no overlays -> no filter, runs the same real ffmpeg path regardless
    before_ms = probe_duration_ms(video)
    after_ms = probe_duration_ms(result.output_path)
    assert abs(after_ms - before_ms) <= 50


@requires_ffmpeg
def test_burn_overlays_preserves_duration(tmp_path, make_solid_color_video, probe_duration_ms):
    video = make_solid_color_video(tmp_path / "bg.mp4", duration_ms=2000, color="navy", size="640x360")

    result = burn_overlays(video, [one_overlay(end_ms=1500)], tmp_path / "out.mp4")

    assert result.ok, result.stderr
    before_ms = probe_duration_ms(video)
    after_ms = probe_duration_ms(result.output_path)
    assert abs(after_ms - before_ms) <= 50


@requires_ffmpeg
def test_burn_overlays_draws_in_the_correct_screen_quadrant(
    tmp_path, make_solid_color_video, frame_region_stddev
):
    video = make_solid_color_video(tmp_path / "bg.mp4", duration_ms=2000, color="navy", size="640x360")
    overlays = [
        one_overlay(text="1953", type="giant_number", position_preset="top-right", start_ms=0, end_ms=2000),
    ]

    result = burn_overlays(video, overlays, tmp_path / "out.mp4")
    assert result.ok, result.stderr

    top_right_stddev = frame_region_stddev(result.output_path, at_ms=1000, crop="iw/2:ih/2:iw/2:0")
    top_left_stddev = frame_region_stddev(result.output_path, at_ms=1000, crop="iw/2:ih/2:0:0")
    bottom_half_stddev = frame_region_stddev(result.output_path, at_ms=1000, crop="iw:ih/2:0:ih/2")

    assert top_right_stddev > 10.0  # the giant_number is here
    assert top_left_stddev < 1.0  # nothing here
    assert bottom_half_stddev < 1.0  # nothing here


@requires_ffmpeg
def test_burn_overlays_respects_enable_between_timing(tmp_path, make_solid_color_video, frame_region_stddev):
    video = make_solid_color_video(tmp_path / "bg.mp4", duration_ms=2000, color="navy", size="640x360")
    overlays = [one_overlay(position_preset="top-right", start_ms=1000, end_ms=1500)]

    result = burn_overlays(video, overlays, tmp_path / "out.mp4")
    assert result.ok, result.stderr

    before_stddev = frame_region_stddev(result.output_path, at_ms=200, crop="iw/2:ih/2:iw/2:0")
    during_stddev = frame_region_stddev(result.output_path, at_ms=1250, crop="iw/2:ih/2:iw/2:0")
    after_stddev = frame_region_stddev(result.output_path, at_ms=1800, crop="iw/2:ih/2:iw/2:0")

    assert before_stddev < 1.0
    assert during_stddev > 10.0
    assert after_stddev < 1.0


@requires_ffmpeg
def test_burn_overlays_never_modifies_the_source_video(tmp_path, make_solid_color_video):
    video = make_solid_color_video(tmp_path / "bg.mp4", duration_ms=1000, color="navy", size="320x240")
    hash_before = hashlib.sha256(video.read_bytes()).hexdigest()

    result = burn_overlays(video, [one_overlay(end_ms=1000)], tmp_path / "out.mp4")

    assert result.ok, result.stderr
    assert hashlib.sha256(video.read_bytes()).hexdigest() == hash_before


@requires_ffmpeg
def test_ffmpeg_failure_on_missing_video_is_captured_not_raised(tmp_path):
    missing_video = tmp_path / "does_not_exist.mp4"

    result = burn_overlays(missing_video, [], tmp_path / "out.mp4")

    assert not result.ok
    assert result.error == "ffmpeg_failed"
    assert result.returncode != 0
    assert result.stderr != ""
