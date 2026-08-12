import subprocess
from pathlib import Path

import pytest


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
