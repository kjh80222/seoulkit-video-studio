"""Final video/audio mux + resolution/CRF encode (Stage 5 spec, ch. 07-09).

ch. 09's pipeline lists two separate steps after Subtitle burn-in: "6.
Audio mix 결과와 video 스트림 결합" and "7. 최종 인코딩." Everything before
this module is either video-only (`trim.py`/`hold.py`/`concat.py`/
`overlay.py`/`subtitle.py` all pass `-an`) or audio-only (`audio_mix.py`) -
nothing yet actually combines the two streams or applies the target
resolution/CRF from ch.07 (Preview: 720x1280, CRF 28) / ch.08 (Final:
1080x1920, CRF 18). `mux_and_encode()` is that missing step, done in one
FFmpeg invocation (video input, audio input, scale + optional watermark
filter, single encode) rather than as two - consistent with every other
`render/*` module here being a single FFmpeg call.

Resolution: `resolution` is passed as `"WxH"` (matching how it already
appears in ch.07/08/18 - e.g. `"720x1280"`) and translated to FFmpeg's own
`scale=W:H` syntax internally. This is the first point in the whole
pipeline where the video stream's actual resolution gets pinned to a
specific target - see `docs/phase-plan.md` Known gaps for why nothing
upstream (subtitle margins, overlay percentages) could safely assume a
resolution before this step existed.

Watermark: ch.18 only has a boolean `render.preview.watermark: true` flag -
no spec text anywhere gives the actual text, position, color, opacity, or
size. The values here (`"PREVIEW"`, centered, white at 40% opacity,
font size proportional to frame height so it scales with resolution
instead of a fixed pixel size) are this module's own default, not derived
from spec - see `docs/phase-plan.md` Known gaps. `watermark_text=None`
(the Final path's case) skips the `drawtext` filter entirely - Final never
gets a watermark, per ch.08.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from seoulkit_studio.render.fonts import BundledFontError, resolve_bundled_font

EncodeErrorKind = Literal["ffmpeg_not_found", "ffmpeg_failed", "bundled_font_error"]


@dataclass
class EncodeResult:
    output_path: Path
    command: list[str]
    returncode: int | None
    stdout: str
    stderr: str
    error: EncodeErrorKind | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _escape_drawtext_text(text: str) -> str:
    # Same escaping rules as overlay.py's drawtext usage - backslash, colon,
    # and single-quote are all filter-syntax-significant inside a quoted
    # option value.
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def mux_and_encode(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
    *,
    resolution: str,
    crf: int,
    watermark_text: str | None = None,
) -> EncodeResult:
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        return EncodeResult(
            output_path=output_path, command=[], returncode=None, stdout="", stderr="", error="ffmpeg_not_found"
        )

    width, height = resolution.split("x")
    vf_filters = [f"scale={width}:{height}"]

    if watermark_text is not None:
        try:
            font_path = resolve_bundled_font("bold")
        except BundledFontError as exc:
            return EncodeResult(
                output_path=output_path,
                command=[],
                returncode=None,
                stdout="",
                stderr=str(exc),
                error="bundled_font_error",
            )
        text = _escape_drawtext_text(watermark_text)
        vf_filters.append(
            f"drawtext=fontfile={font_path}:text='{text}':"
            f"fontcolor=white@0.4:fontsize=h*0.05:"
            f"x=(w-text_w)/2:y=(h-text_h)/2"
        )

    command = [
        ffmpeg_bin,
        "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-vf", ",".join(vf_filters),
        "-map", "0:v",
        "-map", "1:a",
        "-c:v", "libx264",
        "-crf", str(crf),
        "-c:a", "aac",
        "-shortest",  # defensive: video/audio should already match duration by construction
        str(output_path),
    ]

    proc = subprocess.run(command, capture_output=True, text=True)

    return EncodeResult(
        output_path=output_path,
        command=command,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        error=None if proc.returncode == 0 else "ffmpeg_failed",
    )
