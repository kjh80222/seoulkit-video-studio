"""ms -> FFmpeg timestamp conversion (Stage 5 spec, ch. 10).

ch. 10: "clip_in_ms / clip_out_ms를 그대로 사용한다. Stage 5는 재검증만 하고
재계산하지 않는다." This module's only job is to render an already-decided
millisecond value into the `HH:MM:SS.mmm` string FFmpeg's `-ss`/`-t` options
expect - never to adjust, round, or "correct" the value itself.

Conversion uses integer `divmod` only, never float division. `ms / 1000`
can lose precision in binary floating point (e.g. 12345 -> 12.344999999999999
instead of 12.345); integer divmod on whole milliseconds has no such failure
mode, so this step contributes exactly zero rounding error. Any deviation
between a requested and an actually-rendered clip duration comes solely from
FFmpeg's own frame-boundary quantization during encoding - a separate,
unavoidable concern belonging to `trim.py`, not to this conversion.
"""

from __future__ import annotations


def ms_to_ffmpeg_timestamp(ms: int) -> str:
    if ms < 0:
        raise ValueError(f"ms must be non-negative, got {ms}")

    hours, remainder = divmod(ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def ms_to_seconds_str(ms: int) -> str:
    """For FFmpeg options that take plain decimal seconds (e.g. `tpad`'s
    `stop_duration`), not the `HH:MM:SS.mmm` timestamp format above. Same
    integer-`divmod`-only construction, same reason: zero rounding error."""
    if ms < 0:
        raise ValueError(f"ms must be non-negative, got {ms}")

    seconds, milliseconds = divmod(ms, 1_000)
    return f"{seconds}.{milliseconds:03d}"


def ms_to_ass_timestamp(ms: int) -> str:
    """ASS subtitle format's `H:MM:SS.CC` (centiseconds, ch. 11) - unlike its
    two siblings above, this one does NOT have zero rounding error, because
    ASS itself cannot represent anything finer than 10ms. Rounding to the
    nearest centisecond is unavoidable here, not a shortcut.

    The rounding happens once, on the *total* millisecond value, before any
    field is split out - never per-field (hours/minutes/seconds rounded
    independently). Rounding a field at a time risks a carry bug (e.g.
    59.996s naively rounding its own centisecond field without bumping the
    minute); rounding the total first and then decomposing the rounded total
    cannot produce that class of bug, by construction. Worst-case error is
    +-5ms, well inside `duration_tolerance_ms` (default 50ms, ch. 18).
    """
    if ms < 0:
        raise ValueError(f"ms must be non-negative, got {ms}")

    total_centiseconds = (ms + 5) // 10
    hours, remainder = divmod(total_centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    seconds, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"
