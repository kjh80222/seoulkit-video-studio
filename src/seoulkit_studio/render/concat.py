"""Segment concat via FFmpeg (Stage 5 spec, ch. 09-10).

Executes step 3 of the ch. 09 render pipeline: join already-trimmed clip
files (Phase 3's `trim_clip()` output) into a single video stream.

ch. 10: "세그먼트 순서(beat, shot 오름차순)대로 연결. 트랜지션은 기본
하드컷." `edit_plan.schema.json`'s `segments[]` has no field describing a
transition type - only `trim_reason`, a free-text note unrelated to
editing effects - so there is no data path by which this module could ever
be told to do anything other than a hard cut. No crossfade/`xfade` filter
is implemented; there is nothing in the schema for it to be driven by.

Uses FFmpeg's concat *filter* (`concat=n=N:v=1:a=0` in a filtergraph), not
the concat *demuxer*. The demuxer's fast `-c copy` path requires every
input to share identical codec parameters; Phase 3's `trim_clip()`
re-encodes each segment independently; nothing enforces that source clips
all share one resolution. The concat filter decodes every input and
re-encodes the joined result, so a real mismatch surfaces as a clear
FFmpeg error instead of a silently malformed concat - the same "fail loud,
not quiet" stance Phase 2.5's duplicate-shot detection took.

Audio is never mapped (`a=0`) - Phase 3 already dropped it per-segment
with `-an`, so every input here is video-only. This matches the ch. 01
architecture diagram: Clip Engine, Subtitle Engine, and Overlay Renderer
converge first, and only the later Audio Mixer stage ever adds an audio
track.

Ordering contract: `concat_clips()` does not sort `clip_paths` - it joins
them in exactly the order given. Determining beat/shot ascending order
requires reading `edit_plan.json`, which is not this module's job (same
"trim_clip() doesn't recompute clip_in_ms" separation as Phase 3). See
`docs/phase-plan.md`'s Known gaps for the assembly-layer responsibility
this creates.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ConcatErrorKind = Literal["no_clips", "ffmpeg_not_found", "ffmpeg_failed"]


@dataclass
class ConcatResult:
    output_path: Path
    command: list[str]
    returncode: int | None
    stdout: str
    stderr: str
    error: ConcatErrorKind | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def concat_clips(clip_paths: list[Path], output_path: Path) -> ConcatResult:
    if not clip_paths:
        return ConcatResult(
            output_path=output_path,
            command=[],
            returncode=None,
            stdout="",
            stderr="",
            error="no_clips",
        )

    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        return ConcatResult(
            output_path=output_path,
            command=[],
            returncode=None,
            stdout="",
            stderr="",
            error="ffmpeg_not_found",
        )

    command = [ffmpeg_bin, "-y"]
    for clip_path in clip_paths:
        command += ["-i", str(clip_path)]

    filter_inputs = "".join(f"[{i}:v]" for i in range(len(clip_paths)))
    filter_complex = f"{filter_inputs}concat=n={len(clip_paths)}:v=1:a=0[outv]"
    command += [
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-c:v", "libx264",
        str(output_path),
    ]

    proc = subprocess.run(command, capture_output=True, text=True)

    return ConcatResult(
        output_path=output_path,
        command=command,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        error=None if proc.returncode == 0 else "ffmpeg_failed",
    )
