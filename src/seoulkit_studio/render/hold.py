"""Settle-frame hold via FFmpeg `tpad` (Stage 5 spec, ch. 09-10).

ch. 10's hold table gives exactly one FFmpeg mapping for
`hold_strategy=settle_frame_hold`: freeze the last frame at `clip_out_ms`
for `hold_ms` by generating real extra frames -
`tpad=stop_mode=clone:stop_duration={hold_ms/1000}`. Per the spec's own
words, this is "the only place Stage 5 ever manufactures new time."

`hold_strategy` in {none, source_hold} needs no code here at all - ch. 10:
"처리 없음" for both. The extra usable-range time source_hold captures is
already baked into `clip_in_ms`/`clip_out_ms` by the time `trim_clip()`
(Phase 3) runs, so those two strategies are exercised entirely by Phase 3's
own test suite (`tests/test_trim.py`) - there is nothing for `hold_clip()`
to do, and no separate regression test lives in this module for that case,
because there is no code path here that could regress.

`hold_ms < 1` is rejected immediately (`ValueError`), not silently
no-opped. `hold_clip()` should only ever be called for a
`settle_frame_hold` segment, which the Phase 0 schema always gives
`hold_ms >= 1`. Being called with `hold_ms < 1` is itself evidence of a
caller mistake (e.g. invoking this for a `source_hold` segment, where
`hold_ms` is schema-guaranteed to be `0`) - failing loudly here is cheaper
than producing a silently-wrong render.

What this module cannot do: prevent being *called twice* on the same clip.
It is a stateless function with no way to know its own call history, so
"hold_ms is never double-applied" cannot be enforced here - only by
whatever future assembly layer decides *whether* to call `hold_clip()` at
all for a given segment (the same layer `docs/phase-plan.md`'s Known gaps
already flags as missing for Concat's ordering contract). See
`tests/test_hold.py::test_calling_hold_clip_twice_visibly_doubles_the_extension`
for what a double-application actually looks like when measured - proof
that this is a *detectable* danger, not proof that it is *prevented*.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from seoulkit_studio.render.time_format import ms_to_seconds_str

HoldErrorKind = Literal["ffmpeg_not_found", "ffmpeg_failed"]


@dataclass
class HoldResult:
    output_path: Path
    command: list[str]
    returncode: int | None
    stdout: str
    stderr: str
    error: HoldErrorKind | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def hold_clip(clip_path: Path, output_path: Path, hold_ms: int) -> HoldResult:
    if hold_ms < 1:
        raise ValueError(
            f"hold_ms must be >= 1, got {hold_ms} - hold_clip() should only be called for a "
            "settle_frame_hold segment, which the schema guarantees hold_ms >= 1 for"
        )

    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        return HoldResult(
            output_path=output_path,
            command=[],
            returncode=None,
            stdout="",
            stderr="",
            error="ffmpeg_not_found",
        )

    stop_duration = ms_to_seconds_str(hold_ms)
    command = [
        ffmpeg_bin,
        "-y",
        "-i", str(clip_path),
        "-vf", f"tpad=stop_mode=clone:stop_duration={stop_duration}",
        "-c:v", "libx264",
        str(output_path),
    ]

    proc = subprocess.run(command, capture_output=True, text=True)

    return HoldResult(
        output_path=output_path,
        command=command,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        error=None if proc.returncode == 0 else "ffmpeg_failed",
    )
