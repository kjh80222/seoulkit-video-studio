"""Single-clip trim via FFmpeg (Stage 5 spec, ch. 09-10).

Executes exactly step 1 of the ch. 09 render pipeline: trim `source_path`
to `[clip_in_ms, clip_out_ms)` and write the result to `output_path`.
Where that output eventually lives (`preview/`, `output/`, or neither) and
how it gets logged (ch. 09: "모든 FFmpeg 호출은 로그에 원본 커맨드를
기록한다") are decisions for later phases - this function only trims and
reports what it did via `TrimResult`, the same "judge now, act later" shape
`execution.plan_loader.LoadResult` already uses.

Audio is dropped (`-an`), deliberately. The ch. 01 architecture diagram
draws "Clip Engine (trim/hold/concat)" and "Audio Mixer (adopted/selected
assets only)" as separate boxes that only meet at pipeline step 6; ch. 12
lists exactly three audio sources for the final mix - Voice (always
authoritative), adopted SFX, and selected BGM - with no path by which a
source clip's own embedded audio ever reaches the output. Carrying it
through this trim step would produce a stream nothing downstream ever
uses, so it is discarded here instead of silently ignored later.

Re-encodes video (never `-c copy`): stream copy can only cut on keyframe
boundaries, which would make the Duration Invariant check meaningless.

hold_strategy handling (source_hold/settle_frame_hold) is out of scope -
source_hold needs no extra filter (already baked into clip_in_ms/
clip_out_ms per ch. 10) and settle_frame_hold's `tpad` filter is Phase 5's
job.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from seoulkit_studio.render.time_format import ms_to_ffmpeg_timestamp

TrimErrorKind = Literal["ffmpeg_not_found", "ffmpeg_failed"]


@dataclass
class TrimResult:
    output_path: Path
    command: list[str]
    returncode: int | None
    stdout: str
    stderr: str
    error: TrimErrorKind | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def trim_clip(source_path: Path, output_path: Path, clip_in_ms: int, clip_out_ms: int) -> TrimResult:
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        return TrimResult(
            output_path=output_path,
            command=[],
            returncode=None,
            stdout="",
            stderr="",
            error="ffmpeg_not_found",
        )

    start = ms_to_ffmpeg_timestamp(clip_in_ms)
    duration = ms_to_ffmpeg_timestamp(clip_out_ms - clip_in_ms)

    command = [
        ffmpeg_bin,
        "-y",
        "-ss", start,
        "-i", str(source_path),
        "-t", duration,
        "-an",
        "-c:v", "libx264",
        str(output_path),
    ]

    proc = subprocess.run(command, capture_output=True, text=True)

    return TrimResult(
        output_path=output_path,
        command=command,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        error=None if proc.returncode == 0 else "ffmpeg_failed",
    )
