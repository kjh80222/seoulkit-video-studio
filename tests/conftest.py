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
