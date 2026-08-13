"""ASS subtitle generation + burn-in (Stage 5 spec, ch. 11, ch. 18).

ch. 11: iterate `edit_plan.json`'s `subtitles[]`, generate one ASS Dialogue
event per entry, map `position_preset` to an `\an` alignment tag + margin
(ch. 18: `bottom-center -> {an: 2, margin_v: 120}`,
`top-center -> {an: 8, margin_v: 100}`), use one project-wide style (ch. 18:
Noto Sans KR, 44pt, outline 3, shadow 1). "Preview/Final 모두 burn-in을
기본으로 한다" - the `.ass` file itself is also kept (`final_v{NNN}.ass`)
for re-rendering/reuse, and an `.srt` is always exported alongside it
(ch. 18 `export_srt: true`).

The schema's `subtitles[].position_override_reason` is a free-text note
field with no companion coordinate override (unlike `overlays[]`, which has
a real `position_override` object) - position is always exactly one of the
two ch. 18 presets, so there is no override-coordinate logic to implement
here at all.

Two responsibilities kept deliberately separate, same reasoning as
`trim.py`/`concat.py`/`hold.py` being separate modules: `generate_ass()`/
`generate_srt()` are pure text builders (fully testable via string
equality, no FFmpeg involved), `burn_subtitles()` is the one piece that
actually shells out.

Resolution: `PlayResX`/`PlayResY` in the generated ASS script are set to
the caller-supplied `play_res_x`/`play_res_y`, not a hardcoded target
resolution - `probe_video_resolution()` is provided so a caller can measure
the actual input video first. ch. 18's `margin_v` pixel values were almost
certainly picked with some canonical resolution in mind, and nothing in
the pipeline yet guarantees a clip is at that resolution by the time
subtitles are burned in (ch. 09's resolution-dependent step looks like
final encoding, step 7, which comes *after* subtitle burn-in, step 5) -
that mismatch is a known gap (`docs/phase-plan.md`), not something this
module tries to solve.

Font resolution: `_FONT_NAME` names the style's `Fontname`, but libass (the
engine behind FFmpeg's `ass` filter) still has to find an actual "Noto Sans
KR" file to satisfy that name. Relying on system fontconfig for that was
never actually verified as safe - it happened to produce legible Korean
subtitles in this project's dev sandbox only because a different, unasked-
for CJK font (WenQuanYi Zen Hei) was coincidentally installed and libass's
own per-glyph fallback picked it up; a "Noto Sans KR" file was never
actually present. `burn_subtitles()` now passes `fontsdir=` (an option the
`ass` filter supports) pointing at `render.fonts.FONT_DIR`, where the real
bundled "Noto Sans KR" files are committed as ordinary binary assets - see
`render/fonts.py`. libass still does its own
family-name matching within that directory, but the directory now actually
contains the requested family instead of leaving the outcome up to whatever
else is installed.

Windows path escaping: `burn_subtitles()` used to `raise ValueError` if
`ass_path`/`FONT_DIR` contained a colon - refusing to run rather than
misparsing, since a Windows drive-letter colon (`C:\...`) is the normal
shape of an absolute path there, not a rare edge case. That guard is gone;
both values now go through `render.filter_escape.escape_filter_path()`
instead, which produces a filtergraph value FFmpeg parses correctly
(verified empirically against a real FFmpeg binary, not assumed from
documentation - see that module's own docstring for the exact rule and
how it was determined). On an ordinary POSIX path this is a no-op, so
existing behavior on Linux/macOS is unchanged.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from seoulkit_studio.render.filter_escape import escape_filter_path
from seoulkit_studio.render.fonts import BundledFontError, FONT_DIR, resolve_bundled_font
from seoulkit_studio.render.time_format import ms_to_ass_timestamp, ms_to_ffmpeg_timestamp

# ch. 18 `subtitle.position_presets`.
SUBTITLE_POSITION_PRESETS: dict[str, dict[str, int]] = {
    "bottom-center": {"an": 2, "margin_v": 120},
    "top-center": {"an": 8, "margin_v": 100},
}

# ch. 18 `subtitle` config. Colors are not specified there - white text with
# a black outline is this module's own reasonable default, not a spec value
# (see docs/phase-plan.md Known gaps).
_STYLE_NAME = "Default"
_FONT_NAME = "Noto Sans KR"
_FONT_SIZE = 44
_OUTLINE = 3
_SHADOW = 1


def generate_ass(subtitles: list[dict[str, Any]], play_res_x: int, play_res_y: int) -> str:
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {play_res_x}\n"
        f"PlayResY: {play_res_y}\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: {_STYLE_NAME},{_FONT_NAME},{_FONT_SIZE},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
        f"0,0,0,0,100,100,0,0,1,{_OUTLINE},{_SHADOW},2,10,10,10,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    events = []
    for sub in subtitles:
        preset = SUBTITLE_POSITION_PRESETS[sub["position_preset"]]
        start = ms_to_ass_timestamp(sub["start_ms"])
        end = ms_to_ass_timestamp(sub["end_ms"])
        text = r"\N".join(sub["lines"])
        text = f"{{\\an{preset['an']}}}{text}"
        events.append(
            f"Dialogue: 0,{start},{end},{_STYLE_NAME},,0,0,{preset['margin_v']},,{text}\n"
        )

    return header + "".join(events)


def generate_srt(subtitles: list[dict[str, Any]]) -> str:
    # SRT timestamps are HH:MM:SS,mmm - millisecond precision, same as
    # ms_to_ffmpeg_timestamp()'s HH:MM:SS.mmm. No new rounding behavior
    # needed; reusing that zero-rounding-error function and swapping the
    # separator is simpler and safer than writing a parallel formatter.
    blocks = []
    for i, sub in enumerate(subtitles, start=1):
        start = ms_to_ffmpeg_timestamp(sub["start_ms"]).replace(".", ",")
        end = ms_to_ffmpeg_timestamp(sub["end_ms"]).replace(".", ",")
        text = "\n".join(sub["lines"])
        blocks.append(f"{i}\n{start} --> {end}\n{text}\n")
    return "\n".join(blocks)


def probe_video_resolution(video_path: Path) -> tuple[int, int]:
    proc = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0",
            str(video_path),
        ],
        capture_output=True, text=True, check=True,
    )
    width_str, height_str = proc.stdout.strip().split("x")
    return int(width_str), int(height_str)


BurnInErrorKind = Literal["ffmpeg_not_found", "ffmpeg_failed", "bundled_font_error"]


@dataclass
class BurnInResult:
    output_path: Path
    command: list[str]
    returncode: int | None
    stdout: str
    stderr: str
    error: BurnInErrorKind | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def burn_subtitles(video_path: Path, ass_path: Path, output_path: Path) -> BurnInResult:
    try:
        resolve_bundled_font("regular")
    except BundledFontError as exc:
        return BurnInResult(
            output_path=output_path,
            command=[],
            returncode=None,
            stdout="",
            stderr=str(exc),
            error="bundled_font_error",
        )

    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        return BurnInResult(
            output_path=output_path,
            command=[],
            returncode=None,
            stdout="",
            stderr="",
            error="ffmpeg_not_found",
        )

    command = [
        ffmpeg_bin,
        "-y",
        "-i", str(video_path),
        "-vf", f"ass={escape_filter_path(ass_path)}:fontsdir={escape_filter_path(FONT_DIR)}",
        "-an",
        "-c:v", "libx264",
        str(output_path),
    ]

    proc = subprocess.run(command, capture_output=True, text=True)

    return BurnInResult(
        output_path=output_path,
        command=command,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        error=None if proc.returncode == 0 else "ffmpeg_failed",
    )
