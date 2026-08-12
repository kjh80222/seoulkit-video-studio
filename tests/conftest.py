import re
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def measure_mean_volume_db():
    """Real decode + FFmpeg's own `volumedetect` filter - not a guess from
    file size or duration. `start_ms`/`end_ms` optionally restrict the
    measurement window, so a test can distinguish "quiet here" from "loud
    there" within a single mixed file (e.g. BGM before vs. during a ducking
    window)."""

    def _measure(path: Path, *, start_ms: int | None = None, end_ms: int | None = None) -> float:
        command = ["ffmpeg", "-y"]
        if start_ms is not None:
            command += ["-ss", f"{start_ms / 1000:.3f}"]
        command += ["-i", str(path)]
        if end_ms is not None:
            duration_s = (end_ms - (start_ms or 0)) / 1000
            command += ["-t", f"{duration_s:.3f}"]
        command += ["-af", "volumedetect", "-f", "null", "-"]

        proc = subprocess.run(command, capture_output=True, text=True, check=True)
        match = re.search(r"mean_volume:\s*(-?[\d.]+)\s*dB", proc.stderr)
        assert match, f"no mean_volume found in ffmpeg output:\n{proc.stderr}"
        return float(match.group(1))

    return _measure


@pytest.fixture
def measure_lowpass_mean_volume_db():
    """Same real measurement as `measure_mean_volume_db`, but through a
    `lowpass` filter first - lets a test isolate a low-frequency track
    (e.g. a BGM bed) from a higher-frequency one mixed on top of it (e.g.
    dialogue) in the same file, without needing separate stems. Not a
    perfect brick-wall separation (a lowpass filter has rolloff, not an
    instant cutoff), so tests using this should pick source frequencies
    well apart from `cutoff_hz`, not just barely on either side of it."""

    def _measure(
        path: Path, *, cutoff_hz: int = 250, start_ms: int | None = None, end_ms: int | None = None
    ) -> float:
        command = ["ffmpeg", "-y"]
        if start_ms is not None:
            command += ["-ss", f"{start_ms / 1000:.3f}"]
        command += ["-i", str(path)]
        if end_ms is not None:
            duration_s = (end_ms - (start_ms or 0)) / 1000
            command += ["-t", f"{duration_s:.3f}"]
        command += ["-af", f"lowpass=f={cutoff_hz},volumedetect", "-f", "null", "-"]

        proc = subprocess.run(command, capture_output=True, text=True, check=True)
        match = re.search(r"mean_volume:\s*(-?[\d.]+)\s*dB", proc.stderr)
        assert match, f"no mean_volume found in ffmpeg output:\n{proc.stderr}"
        return float(match.group(1))

    return _measure


@pytest.fixture
def measure_integrated_lufs():
    """Real EBU R128 measurement (`ebur128` filter) of a whole file's
    integrated loudness - the same metric `loudnorm`'s `I=` target uses, so
    this is a direct check of whether a normalize pass actually landed near
    its target, not a proxy."""

    def _measure(path: Path) -> float:
        command = ["ffmpeg", "-y", "-i", str(path), "-af", "ebur128", "-f", "null", "-"]
        proc = subprocess.run(command, capture_output=True, text=True, check=True)
        summary = proc.stderr.rsplit("Summary:", 1)[-1]
        match = re.search(r"I:\s*(-?[\d.]+)\s*LUFS", summary)
        assert match, f"no integrated loudness found in ffmpeg output:\n{proc.stderr}"
        return float(match.group(1))

    return _measure


@pytest.fixture
def make_synthetic_video():
    """Generate a short lavfi test video on demand - no binary fixture is
    committed to the repo, so the file this produces is deterministic but
    never stored in git."""

    def _make(path: Path, duration_ms: int, *, with_audio: bool = False, fps: int = 30) -> Path:
        duration_s = f"{duration_ms / 1000:.3f}"
        command = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=duration={duration_s}:size=320x240:rate={fps}"]
        if with_audio:
            command += ["-f", "lavfi", "-i", f"sine=frequency=1000:duration={duration_s}"]
        command += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
        if with_audio:
            command += ["-c:a", "aac"]
        command += [str(path)]
        subprocess.run(command, capture_output=True, text=True, check=True)
        return path

    return _make


@pytest.fixture
def probe_duration_ms():
    def _probe(path: Path) -> int:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True, text=True, check=True,
        )
        return round(float(proc.stdout.strip()) * 1000)

    return _probe


@pytest.fixture
def probe_has_audio_stream():
    def _probe(path: Path) -> bool:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "a",
                "-show_entries", "stream=index",
                "-of", "csv=p=0",
                str(path),
            ],
            capture_output=True, text=True, check=True,
        )
        return bool(proc.stdout.strip())

    return _probe


@pytest.fixture
def make_solid_color_video():
    """A synthetic clip whose entire visual content is one flat color - used
    to verify concat *order*, not just duration: two clips of different
    colors let a test check which one actually plays first in the output."""

    def _make(path: Path, duration_ms: int, color: str, *, fps: int = 10, size: str = "64x64") -> Path:
        duration_s = f"{duration_ms / 1000:.3f}"
        command = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c={color}:size={size}:rate={fps}:duration={duration_s}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(path),
        ]
        subprocess.run(command, capture_output=True, text=True, check=True)
        return path

    return _make


@pytest.fixture
def sample_frame_rgb():
    """Grab one real decoded frame at `at_ms` and return its average (R, G, B)
    - real ffmpeg decode + pixel read, not a duration proxy."""

    def _sample(path: Path, at_ms: int) -> tuple[float, float, float]:
        ts = f"{at_ms / 1000:.3f}"
        proc = subprocess.run(
            [
                "ffmpeg", "-y", "-ss", ts, "-i", str(path),
                "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24",
                "-",
            ],
            capture_output=True, check=True,
        )
        raw = proc.stdout
        r, g, b = raw[0::3], raw[1::3], raw[2::3]
        return (sum(r) / len(r), sum(g) / len(g), sum(b) / len(b))

    return _sample


@pytest.fixture
def frame_region_stddev():
    """Grab one real decoded frame at `at_ms`, crop it, and return the
    standard deviation of luminance within that crop. On a flat-color
    background (see make_solid_color_video), stddev stays near 0 unless
    something with real edges - like burned-in subtitle text - was drawn
    there. This is how the burn-in tests verify "something was actually
    drawn in roughly the right place at roughly the right time" without
    doing OCR."""

    def _stddev(path: Path, at_ms: int, crop: str = "iw:ih/2:0:ih/2") -> float:
        ts = f"{at_ms / 1000:.3f}"
        proc = subprocess.run(
            [
                "ffmpeg", "-y", "-ss", ts, "-i", str(path),
                "-frames:v", "1", "-vf", f"crop={crop}",
                "-f", "rawvideo", "-pix_fmt", "gray",
                "-",
            ],
            capture_output=True, check=True,
        )
        raw = proc.stdout
        if not raw:
            return 0.0
        mean = sum(raw) / len(raw)
        variance = sum((b - mean) ** 2 for b in raw) / len(raw)
        return variance ** 0.5

    return _stddev
