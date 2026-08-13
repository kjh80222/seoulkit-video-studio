"""Render Report generation + version-numbered output path assignment
(Stage 5 spec ch. 17, folder/naming conventions confirmed via
`examples/sample-project/{preview,output,logs}/README.md`).

Phase 9's `render_preview()`/`render_final()` take whatever `output_path`
the caller hands them and don't decide what that path *should be* -
that was deliberately deferred (`docs/phase-plan.md` Known gaps: "Assigning
real version-numbered filenames ... is Render Report (Phase 10) or CLI
(Phase 11) territory"). This module is that missing owner: it picks the
next `v{NNN}`, builds the exact paths ch.02's folder layout expects, calls
Phase 9's functions with them, and writes the ch.17 JSON report (plus the
`render_v{NNN}.log` FFmpeg-command log ch.09/ch.17 both call for, open
since Phase 3).

Folder/naming rules (confirmed from the example project scaffold, not
invented here):
    preview/preview_v{NNN}.mp4 + preview/preview_v{NNN}.render_report.json
    output/final_v{NNN}.{mp4,ass,srt} + output/final_v{NNN}.render_report.json
    logs/render_v{NNN}.log  (always written, success or failure)
    logs/render_v{NNN}.render_report.json  (only when no output was produced)

Report location is decided by `RenderResult.ok`, not by
`studio_execution_result` - `studio_execution_result` can be `"PASS"` while
`effective_status` is still `REVIEW_REQUIRED` (only a warning-level issue,
nothing blocking) and Final is still correctly gate-refused (ch.05). What
actually determines whether a report belongs in `preview/`/`output/` vs
`logs/` is whether a file was actually produced, not which of PASS/BLOCKED/
ERROR the Pre-flight run happened to land on.

`render_version` is never tracked in a state file - `next_render_version()`
scans `preview/`, `output/`, and `logs/` for existing `_v{NNN}.` filenames
and returns max+1, so a failed attempt (report only, in `logs/`) still
consumes a number and that number is never reused (ch.23) - the counter is
purely a function of what's already on disk.

Gate rejections do not populate `errors[]` - ch.05 refusing to run is not
an execution failure, it's already fully represented by `effective_status`/
`preflight_issues[]`/`gate_error`. `errors[]` is reserved for an actual
failed pipeline stage (a real FFmpeg/primitive failure after the gate
allowed the attempt to proceed).

`duration_invariant_post_render` is a real, ffprobe-measured check against
the actual rendered file - not a second call to
`schema.validate_duration_invariant()` against the input JSON (that would
prove nothing new; `evaluate_plan()` already ran that pre-render). It sums
`schema.segment_expected_duration_ms()` across all segments and compares
that to the real muxed output's measured duration. This is deliberately
aggregate-level: it can't attribute a mismatch to one specific segment,
since Phase 9's `_render()` discards every intermediate trim/hold file
inside its own `tempfile.TemporaryDirectory()` before returning, and this
module calls `render_preview()`/`render_final()` from the outside - by the
time either returns, there is nothing left to probe per segment. A true
per-segment post-render check would need Phase 9's own
`_run_clip_stage()` to measure each trim/hold output with `ffprobe` before
its temp directory is cleaned up and expose that on `RenderResult` - not
implemented here (see `docs/phase-plan.md` Known gaps).

This module does not clean up a partial/corrupt file that Phase 9's final
`mux_and_encode()` may leave at the real output path on a mid-encode
failure (`render/pipeline.py`'s docstring and `docs/phase-plan.md` Known
gaps both flag this as an open Phase 9 defect). Fixing that here would
quietly patch over a Phase 9 bug from inside a Phase 10 module and hide
that it needs its own fix - `output_file` is correctly recorded as `null`
on failure regardless of whether a stray file happens to sit on disk.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from seoulkit_studio.execution import load_plan_file
from seoulkit_studio.preflight import PreflightIssue
from seoulkit_studio.render.pipeline import RenderResult, render_final, render_preview
from seoulkit_studio.schema import DEFAULT_DURATION_TOLERANCE_MS, segment_expected_duration_ms

RenderType = Literal["preview", "final"]

_VERSION_RE = re.compile(r"_v(\d+)\.")

_STAGE_NAMES = {
    "TrimResult": "trim_clip",
    "HoldResult": "hold_clip",
    "ConcatResult": "concat_clips",
    "OverlayResult": "burn_overlays",
    "BurnInResult": "burn_subtitles",
    "AudioMixResult": "mix_audio",
    "EncodeResult": "mux_and_encode",
    "PublishResult": "publish_outputs",
}

_ERROR_MESSAGES = {
    "ffmpeg_failed": "FFmpeg exited with non-zero status",
    "ffmpeg_not_found": "ffmpeg executable not found on PATH",
    "bundled_font_error": "bundled font asset missing or invalid",
    "publish_copy_failed": "failed to stage a rendered output file for publish",
    "publish_commit_failed": "failed to commit a staged output file to its real destination",
    "publish_rollback_failed": "commit failed and rollback of already-committed files also failed",
}


@dataclass
class QCResult:
    check: str
    result: Literal["PASS", "FAIL"]
    detail: str


@dataclass
class ReportError:
    code: str
    stage: str | None
    message: str
    stderr: str | None


@dataclass
class RenderReport:
    render_type: RenderType
    render_version: str
    studio_execution_result: str
    effective_status: str
    plan_status_as_declared: str | None
    started_at: str
    completed_at: str
    duration_ms: int
    input_edit_plan_hash: str
    output_file: str | None
    qc_results: list[QCResult] = field(default_factory=list)
    ffmpeg_commands: list[str] = field(default_factory=list)
    preflight_issues: list[PreflightIssue] = field(default_factory=list)
    errors: list[ReportError] = field(default_factory=list)


def next_render_version(project_dir: Path) -> int:
    max_seen = 0
    for subdir in ("preview", "output", "logs"):
        d = project_dir / subdir
        if not d.is_dir():
            continue
        for entry in d.iterdir():
            if not entry.is_file():
                continue
            match = _VERSION_RE.search(entry.name)
            if match:
                max_seen = max(max_seen, int(match.group(1)))
    return max_seen + 1


def _preview_paths(project_dir: Path, version: int) -> dict[str, Path]:
    base = project_dir / "preview" / f"preview_v{version:03d}"
    return {"output": Path(f"{base}.mp4"), "report": Path(f"{base}.render_report.json")}


def _final_paths(project_dir: Path, version: int) -> dict[str, Path]:
    base = project_dir / "output" / f"final_v{version:03d}"
    return {
        "output": Path(f"{base}.mp4"),
        "ass": Path(f"{base}.ass"),
        "srt": Path(f"{base}.srt"),
        "report": Path(f"{base}.render_report.json"),
    }


def _log_paths(project_dir: Path, version: int) -> dict[str, Path]:
    base = project_dir / "logs" / f"render_v{version:03d}"
    return {"log": Path(f"{base}.log"), "report": Path(f"{base}.render_report.json")}


def _probe_duration_ms(video_path: Path) -> int:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video_path)],
        capture_output=True, text=True, check=True,
    )
    return round(float(proc.stdout.strip()) * 1000)


def _duration_match(output_path: Path, expected_ms: int, tolerance_ms: int) -> QCResult:
    actual_ms = _probe_duration_ms(output_path)
    diff_ms = abs(actual_ms - expected_ms)
    return QCResult(
        check="duration_match",
        result="PASS" if diff_ms <= tolerance_ms else "FAIL",
        detail=(
            f"actual={actual_ms}ms vs voice.total_duration_ms={expected_ms}ms, "
            f"diff={diff_ms}ms, tolerance={tolerance_ms}ms"
        ),
    )


def _duration_invariant_post_render(data: dict, output_path: Path, tolerance_ms: int) -> QCResult:
    actual_ms = _probe_duration_ms(output_path)
    expected_total_ms = 0
    unresolved_shots = []
    for segment in data.get("segments", []):
        expected = segment_expected_duration_ms(segment)
        if expected is None:
            unresolved_shots.append(segment.get("shot"))
            continue
        expected_total_ms += expected

    diff_ms = abs(actual_ms - expected_total_ms)
    passed = diff_ms <= tolerance_ms and not unresolved_shots
    detail = (
        f"actual={actual_ms}ms vs sum(segment_expected_duration_ms)={expected_total_ms}ms, "
        f"diff={diff_ms}ms, tolerance={tolerance_ms}ms - "
        "aggregate-level, not per-segment-attributable"
    )
    if unresolved_shots:
        detail += f"; could not compute expected duration for shots {unresolved_shots}"
    return QCResult(check="duration_invariant_post_render", result="PASS" if passed else "FAIL", detail=detail)


def _stage_name(stage_result: object) -> str:
    return _STAGE_NAMES.get(type(stage_result).__name__, type(stage_result).__name__)


def _errors_from_stage_results(result: RenderResult) -> list[ReportError]:
    errors = []
    for stage_result in result.stage_results:
        if not stage_result.ok:
            error_code = str(stage_result.error)
            errors.append(
                ReportError(
                    code=error_code,
                    stage=_stage_name(stage_result),
                    message=_ERROR_MESSAGES.get(error_code, error_code),
                    stderr=stage_result.stderr or None,
                )
            )
    return errors


def _run_and_report(
    render_type: RenderType,
    edit_plan_path: Path,
    project_dir: Path,
    clip_manifest_path: Path | None,
) -> tuple[RenderResult, RenderReport]:
    started_at = datetime.now(timezone.utc)
    version = next_render_version(project_dir)
    version_str = f"v{version:03d}"

    if render_type == "preview":
        paths = _preview_paths(project_dir, version)
        paths["output"].parent.mkdir(parents=True, exist_ok=True)
        result = render_preview(edit_plan_path, project_dir, paths["output"], clip_manifest_path)
    else:
        paths = _final_paths(project_dir, version)
        paths["output"].parent.mkdir(parents=True, exist_ok=True)
        result = render_final(
            edit_plan_path, project_dir, paths["output"], paths["ass"], paths["srt"], clip_manifest_path
        )

    completed_at = datetime.now(timezone.utc)
    duration_ms = round((completed_at - started_at).total_seconds() * 1000)

    input_hash = "sha256:" + hashlib.sha256(edit_plan_path.read_bytes()).hexdigest()
    ffmpeg_commands = [shlex.join(sr.command) for sr in result.stage_results if getattr(sr, "command", None)]

    qc_results: list[QCResult] = []
    errors: list[ReportError] = []
    output_file: str | None = None

    if result.ok:
        report_path = paths["report"]
        output_file = str(paths["output"].relative_to(project_dir))

        load_result = load_plan_file(edit_plan_path)
        data = load_result.data
        expected_voice_ms = data.get("voice", {}).get("total_duration_ms") if isinstance(data, dict) else None
        if expected_voice_ms is not None:
            qc_results.append(_duration_match(paths["output"], expected_voice_ms, DEFAULT_DURATION_TOLERANCE_MS))
        if isinstance(data, dict):
            qc_results.append(
                _duration_invariant_post_render(data, paths["output"], DEFAULT_DURATION_TOLERANCE_MS)
            )
    else:
        report_path = _log_paths(project_dir, version)["report"]
        errors = _errors_from_stage_results(result)

    report = RenderReport(
        render_type=render_type,
        render_version=version_str,
        studio_execution_result=result.studio_execution_result or "ERROR",
        effective_status=result.effective_status or "NOT_READY",
        plan_status_as_declared=result.plan_status_as_declared,
        started_at=started_at.isoformat(),
        completed_at=completed_at.isoformat(),
        duration_ms=duration_ms,
        input_edit_plan_hash=input_hash,
        output_file=output_file,
        qc_results=qc_results,
        ffmpeg_commands=ffmpeg_commands,
        preflight_issues=result.preflight_issues,
        errors=errors,
    )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(asdict(report), indent=2, default=str))

    log_path = _log_paths(project_dir, version)["log"]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_text = "\n".join(ffmpeg_commands)
    log_path.write_text(log_text + "\n" if log_text else "")

    return result, report


def render_preview_and_report(
    edit_plan_path: Path,
    project_dir: Path,
    clip_manifest_path: Path | None = None,
) -> tuple[RenderResult, RenderReport]:
    return _run_and_report("preview", edit_plan_path, project_dir, clip_manifest_path)


def render_final_and_report(
    edit_plan_path: Path,
    project_dir: Path,
    clip_manifest_path: Path | None = None,
) -> tuple[RenderResult, RenderReport]:
    return _run_and_report("final", edit_plan_path, project_dir, clip_manifest_path)
