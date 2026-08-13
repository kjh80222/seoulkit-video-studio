import shutil
import subprocess
from pathlib import Path

import pytest

from seoulkit_studio.render.encode import mux_and_encode

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed on this system",
)


@pytest.fixture
def silent_video(tmp_path) -> Path:
    path = tmp_path / "video.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=10",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    return path


@pytest.fixture
def some_audio(tmp_path) -> Path:
    path = tmp_path / "audio.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-ar", "44100", "-ac", "2", str(path)],
        capture_output=True, text=True, check=True,
    )
    return path


def test_ffmpeg_not_found_is_reported_without_raising(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)

    result = mux_and_encode(tmp_path / "v.mp4", tmp_path / "a.wav", tmp_path / "out.mp4", resolution="720x1280", crf=28)

    assert not result.ok
    assert result.error == "ffmpeg_not_found"
    assert result.command == []


@requires_ffmpeg
def test_output_has_the_target_resolution(tmp_path, silent_video, some_audio):
    output = tmp_path / "out.mp4"

    result = mux_and_encode(silent_video, some_audio, output, resolution="720x1280", crf=28)

    assert result.ok, result.stderr
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "csv=s=x:p=0", str(output)],
        capture_output=True, text=True, check=True,
    )
    assert proc.stdout.strip() == "720x1280"


@requires_ffmpeg
def test_output_carries_both_video_and_audio_streams(tmp_path, silent_video, some_audio, probe_has_audio_stream):
    output = tmp_path / "out.mp4"

    result = mux_and_encode(silent_video, some_audio, output, resolution="720x1280", crf=28)

    assert result.ok, result.stderr
    assert probe_has_audio_stream(output)


@requires_ffmpeg
def test_output_duration_matches_the_inputs(tmp_path, silent_video, some_audio, probe_duration_ms):
    output = tmp_path / "out.mp4"

    result = mux_and_encode(silent_video, some_audio, output, resolution="720x1280", crf=28)

    assert result.ok, result.stderr
    assert abs(probe_duration_ms(output) - 2000) <= 100


@requires_ffmpeg
def test_watermark_none_draws_nothing(tmp_path, silent_video, some_audio, frame_region_stddev):
    # testsrc has real content everywhere, so this can't prove "watermark
    # absent" by stddev alone - it proves the command itself has no
    # drawtext filter, which is the actual thing watermark_text=None means.
    output = tmp_path / "out.mp4"

    result = mux_and_encode(silent_video, some_audio, output, resolution="720x1280", crf=28, watermark_text=None)

    assert result.ok, result.stderr
    assert "drawtext" not in " ".join(result.command)


@requires_ffmpeg
def test_watermark_text_draws_something_near_center(tmp_path, silent_video, some_audio, frame_region_stddev):
    # A flat color source (unlike testsrc) makes the watermark's edges the
    # only thing that can raise stddev in the center crop.
    flat_video = tmp_path / "flat.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=navy:size=640x360:rate=10:duration=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(flat_video)],
        capture_output=True, text=True, check=True,
    )
    output = tmp_path / "out.mp4"

    result = mux_and_encode(flat_video, some_audio, output, resolution="640x360", crf=28, watermark_text="PREVIEW")

    assert result.ok, result.stderr
    center_stddev = frame_region_stddev(output, at_ms=1000, crop="iw/2:ih/4:iw/4:ih*3/8")
    assert center_stddev > 5.0


@requires_ffmpeg
def test_ffmpeg_failure_on_missing_video_is_captured_not_raised(tmp_path, some_audio):
    result = mux_and_encode(tmp_path / "does_not_exist.mp4", some_audio, tmp_path / "out.mp4", resolution="720x1280", crf=28)

    assert not result.ok
    assert result.error == "ffmpeg_failed"
    assert result.returncode != 0
    assert result.stderr != ""


@requires_ffmpeg
def test_colon_in_watermark_font_path_is_escaped_and_watermark_still_draws(
    tmp_path, some_audio, frame_region_stddev, monkeypatch
):
    # Regression test for the Windows path-escaping hotfix, real FFmpeg
    # execution. The watermark's fontfile= value went straight into the
    # filtergraph unescaped, same unguarded gap as overlay.py - and the
    # same silent-fallback failure mode, not a crash (see overlay.py's
    # module docstring), so the command-string assertion below is what
    # actually proves the escaping happened, not the stddev check alone.
    # A colon is a legal POSIX filename character, so the real bundled font
    # is copied into a colon-named directory to reproduce the same class
    # of path FONT_DIR always has on Windows.
    from seoulkit_studio.render.fonts import FONT_DIR

    colon_dir = tmp_path / "fake:windows-style-dir"
    colon_dir.mkdir()
    colon_font_path = colon_dir / "NotoSansKR-Bold.ttf"
    shutil.copy2(FONT_DIR / "NotoSansKR-Bold.ttf", colon_font_path)
    monkeypatch.setattr("seoulkit_studio.render.encode.resolve_bundled_font", lambda weight: colon_font_path)

    flat_video = tmp_path / "flat.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=navy:size=640x360:rate=10:duration=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(flat_video)],
        capture_output=True, text=True, check=True,
    )
    output = tmp_path / "out.mp4"

    result = mux_and_encode(flat_video, some_audio, output, resolution="640x360", crf=28, watermark_text="PREVIEW")

    assert result.ok, result.stderr
    assert "fontfile=" + str(colon_font_path).replace(":", "\\" * 2 + ":") in " ".join(result.command)
    center_stddev = frame_region_stddev(output, at_ms=1000, crop="iw/2:ih/4:iw/4:ih*3/8")
    assert center_stddev > 5.0  # watermark text actually drawn, not silently skipped
