"""Overlay rendering via FFmpeg `drawtext` (Stage 5 spec, ch. 09, ch. 18).

ch. 09 pipeline order: Overlay (step 4) comes BEFORE Subtitle burn-in
(step 5) - this module operates on Phase 4's `concat_clips()` output
(still silent, still un-subtitled), not Phase 6's. Whichever assembly
layer eventually chains these phases together should call them in that
order: trim -> hold -> concat -> overlay -> subtitle -> audio mix ->
encode.

ch. 18 gives seven position presets (`x_pct`/`y_pct`, optional `align`)
and three font-size presets (giant_number/headline/label) but no font
family or colors - both are reused from `subtitle.py`'s own defaults
(Noto Sans KR; white text, black outline) for project-wide consistency,
since nothing in the spec suggests overlays should look different.

Position math: `x_pct`/`y_pct` are percentages of the actual video's real
resolution (via `probe_video_resolution()`, reused from `subtitle.py` -
not a hardcoded canonical size, same reasoning as Phase 6's PlayResX/Y
decision). `align` determines which edge of the text box sits at that
anchor point - none/absent -> left edge (`x = anchor`), `"right"` ->
right edge (`x = anchor - text_w`), `"center"` -> centered on the anchor
(`x = anchor - text_w/2`). `text_w` is `drawtext`'s own built-in
variable, computed at render time, so this is exact regardless of the
actual rendered string width. `position_override` (when present) replaces
`x_pct`/`y_pct` but not `align` - the schema's override object carries no
alignment field of its own, so alignment always still comes from the
segment's own `position_preset`.

Font resolution: `drawtext`'s `font` option does NOT accept fontconfig
pattern syntax like `"Noto Sans KR:style=Bold"` - that was tried during
Phase 7 development and fails with `"Error applying option 'style' to
filter 'drawtext': Option not found"` (drawtext's own option parser
treats the colon as an option separator, same class of problem as
`subtitle.py`'s `ass_path` colon guard, just in a different filter).
`fontfile=<path>` is what actually works.

That path used to come from asking `fc-match` for a concrete file, which
Phase 7 development found to be a real, silent failure mode: `fc-match`
never fails, even for a font name that plainly doesn't exist, and in this
project's dev sandbox "Noto Sans KR" itself isn't installed - every
resolution silently substituted a fallback (DejaVu Sans) that has no
Hangul glyphs, so Korean overlay text rendered as empty tofu boxes with no
error anywhere in the pipeline. `resolve_font_file()` now instead goes
through `render.fonts.resolve_bundled_font()`, which points at the actual
bundled "Noto Sans KR" files committed in this repo - fontconfig, and
whatever it happens to have installed in a given environment, is no longer
consulted at all. See `render/fonts.py` for the full reasoning and the
bundling mechanics.

Because there is now exactly one font family in play (not "whatever
fontconfig resolves"), there is no longer a meaningful distinction between
"requested" and "resolved" family, and no fallback worth accepting: a
missing or corrupt bundled font is a packaging defect, not an environment
difference, so `resolve_font_file()` lets `BundledFontError` surface
per-overlay as a `FontSubstitutionIssue` (same `code`/`detail`/
`segment_ref` shape as `seoulkit_studio.preflight.PreflightIssue`, with
`severity: Severity | None` still left `None` - no phase has decided yet
whether this should be blocking/warning/info) rather than silently
rendering with a substitute the way the old fontconfig-based version did.
`build_overlay_filter()` skips generating a `drawtext` clause for any
overlay whose font can't be resolved (there is no filter string that could
work without a real `fontfile=` path), and `burn_overlays()` refuses to
even invoke FFmpeg if any font issues were recorded, returning them on the
`OverlayResult` instead of producing a video silently missing overlays.

Windows path escaping: `resolution.file_path` used to go straight into the
`fontfile=` value unescaped, with no guard at all. Empirically this turned
out to be a *silent* failure mode, not a crash: unlike the `ass` filter
(where a colon derails the filename token and FFmpeg refuses to run at
all, `subtitle.py`'s original failure mode), `drawtext`'s `fontfile=`
value simply truncates at the first unescaped colon and FFmpeg still
exits 0 - it falls back to some other font entirely rather than erroring,
confirmed by rendering visibly non-bold text from a request for the bold
weight. That is the same class of silent-substitution bug the bundled-font
work elsewhere in this module exists to prevent, just reached through a
different mechanism (filtergraph misparsing instead of fontconfig
fallback) - arguably worse than a loud failure, since nothing here would
have told a Windows user their overlays were rendering in the wrong font.
Now routed through `render.filter_escape.escape_filter_path()`, the same
empirically-verified helper `subtitle.py` uses for `ass=`/`fontsdir=`.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from seoulkit_studio.preflight import Severity
from seoulkit_studio.render.filter_escape import escape_filter_path
from seoulkit_studio.render.fonts import BundledFontError, resolve_bundled_font
from seoulkit_studio.render.subtitle import probe_video_resolution
from seoulkit_studio.render.time_format import ms_to_seconds_str

# ch. 18 `overlay.position_presets`.
OVERLAY_POSITION_PRESETS: dict[str, dict[str, Any]] = {
    "top-left": {"x_pct": 8, "y_pct": 8},
    "top-right": {"x_pct": 92, "y_pct": 8, "align": "right"},
    "top-center": {"x_pct": 50, "y_pct": 8, "align": "center"},
    "bottom-left": {"x_pct": 8, "y_pct": 88},
    "bottom-right": {"x_pct": 92, "y_pct": 88, "align": "right"},
    "center-lower-third": {"x_pct": 50, "y_pct": 75, "align": "center"},
    "center-upper-third": {"x_pct": 50, "y_pct": 25, "align": "center"},
}

# ch. 18 `overlay.font_presets`.
OVERLAY_FONT_PRESETS: dict[str, dict[str, Any]] = {
    "giant_number": {"size": 96, "weight": "bold"},
    "headline": {"size": 56, "weight": "bold"},
    "label": {"size": 36, "weight": "regular"},
}


@dataclass
class FontResolution:
    weight: Literal["regular", "bold"]
    file_path: str


@dataclass
class FontSubstitutionIssue:
    """Same shape as `seoulkit_studio.preflight.PreflightIssue`
    (code/detail/segment_ref), except `severity` is left undecided.

    Despite the name (kept for continuity with Phase 6/7's fc-match-based
    design), this no longer means "a different system font got resolved" -
    fontconfig isn't consulted at all anymore. It now means the bundled
    font asset itself is missing, corrupt, or lacks Hangul glyph coverage -
    see `render/fonts.py::BundledFontError`.
    """

    code: str
    severity: Severity | None
    detail: str
    segment_ref: str | None = None


def resolve_font_file(weight: Literal["regular", "bold"]) -> FontResolution:
    path = resolve_bundled_font(weight)
    return FontResolution(weight=weight, file_path=str(path))


def _drawtext_x_expr(anchor_px: int, align: str | None) -> str:
    if align == "right":
        return f"{anchor_px}-text_w"
    if align == "center":
        return f"{anchor_px}-text_w/2"
    return str(anchor_px)


def _escape_drawtext_text(text: str) -> str:
    # drawtext's own escaping rules: backslash, colon, and single-quote are
    # all filter-syntax-significant inside a quoted option value.
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def build_overlay_filter(
    overlays: list[dict[str, Any]], video_width: int, video_height: int
) -> tuple[str, list[FontResolution], list[FontSubstitutionIssue]]:
    filters: list[str] = []
    resolutions: list[FontResolution] = []
    issues: list[FontSubstitutionIssue] = []

    for overlay in overlays:
        preset = OVERLAY_POSITION_PRESETS[overlay["position_preset"]]
        override = overlay.get("position_override")
        x_pct = override["x_pct"] if override else preset["x_pct"]
        y_pct = override["y_pct"] if override else preset["y_pct"]
        align = preset.get("align")

        anchor_x = round(x_pct / 100 * video_width)
        anchor_y = round(y_pct / 100 * video_height)

        font_preset = OVERLAY_FONT_PRESETS[overlay["type"]]
        ref = f"beat={overlay.get('beat')} shot={overlay.get('shot')}"
        try:
            resolution = resolve_font_file(font_preset["weight"])
        except BundledFontError as exc:
            issues.append(FontSubstitutionIssue("FONT_BUNDLE_INVALID", None, str(exc), ref))
            continue
        resolutions.append(resolution)

        start_s = ms_to_seconds_str(overlay["start_ms"])
        end_s = ms_to_seconds_str(overlay["end_ms"])
        x_expr = _drawtext_x_expr(anchor_x, align)
        text = _escape_drawtext_text(overlay["text"])

        filters.append(
            f"drawtext=fontfile={escape_filter_path(resolution.file_path)}:text='{text}':"
            f"x={x_expr}:y={anchor_y}:fontsize={font_preset['size']}:"
            f"fontcolor=white:bordercolor=black:borderw=3:"
            f"enable='between(t,{start_s},{end_s})'"
        )

    return ",".join(filters), resolutions, issues


OverlayErrorKind = Literal["ffmpeg_not_found", "ffmpeg_failed", "bundled_font_error"]


@dataclass
class OverlayResult:
    output_path: Path
    command: list[str]
    returncode: int | None
    stdout: str
    stderr: str
    font_issues: list[FontSubstitutionIssue] = field(default_factory=list)
    error: OverlayErrorKind | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def burn_overlays(video_path: Path, overlays: list[dict[str, Any]], output_path: Path) -> OverlayResult:
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        return OverlayResult(
            output_path=output_path,
            command=[],
            returncode=None,
            stdout="",
            stderr="",
            error="ffmpeg_not_found",
        )

    command = [ffmpeg_bin, "-y", "-i", str(video_path)]

    font_issues: list[FontSubstitutionIssue] = []
    if overlays:
        width, height = probe_video_resolution(video_path)
        filter_str, _resolutions, font_issues = build_overlay_filter(overlays, width, height)
        if font_issues:
            # A missing/corrupt bundled font is a packaging defect with no
            # sensible fallback (see module docstring) - refuse to render a
            # video that would silently be missing overlays rather than
            # invoking FFmpeg with a partial filter graph.
            return OverlayResult(
                output_path=output_path,
                command=[],
                returncode=None,
                stdout="",
                stderr="",
                font_issues=font_issues,
                error="bundled_font_error",
            )
        command += ["-vf", filter_str]

    command += ["-an", "-c:v", "libx264", str(output_path)]

    proc = subprocess.run(command, capture_output=True, text=True)

    return OverlayResult(
        output_path=output_path,
        command=command,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        font_issues=font_issues,
        error=None if proc.returncode == 0 else "ffmpeg_failed",
    )
